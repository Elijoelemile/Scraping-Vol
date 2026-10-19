"""Récapitulatif d'une recherche de vols et comparaison entre chargements successifs (pages Streamlit).

L'historique des chargements est enregistré dans vols.db (table chargements) : il se cumule d'une session à
l'autre jusqu'à ce que l'utilisateur l'efface.
"""
import json
from collections import Counter
from contextlib import closing
from datetime import datetime

import pandas as pd
import streamlit as st

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS chargements (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    quand     TEXT NOT NULL,      -- date et heure du chargement (ISO)
    page      TEXT NOT NULL,      -- page de l'application (catalogue / recherche)
    trajet    TEXT NOT NULL,
    source    TEXT,
    resume    TEXT NOT NULL,      -- chiffres du récapitulatif (JSON)
    empreinte TEXT NOT NULL       -- vols et prix (JSON) pour comparer deux chargements
);
"""


def _connexion():
    con = db.connexion()
    con.executescript(SCHEMA)
    return con


def _enregistrer(page, trajet, source, res, emp: Counter):
    with closing(_connexion()) as con, con:
        con.execute("INSERT INTO chargements (quand, page, trajet, source, resume, empreinte) VALUES (?,?,?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), page, trajet, source,
                     json.dumps(res, ensure_ascii=False),
                     json.dumps([[list(k), p, n] for (k, p), n in emp.items()], ensure_ascii=False)))


def _historique(page) -> list[dict]:
    with closing(_connexion()) as con:
        lignes = con.execute("SELECT quand, trajet, source, resume, empreinte FROM chargements WHERE page = ?"
                             " ORDER BY quand, id", (page,)).fetchall()
    return [{"quand": q, "trajet": t, "source": src, "resume": json.loads(r),
             "empreinte": Counter({(tuple(k), p): n for k, p, n in json.loads(e)})}
            for q, t, src, r, e in lignes]


def _effacer(page):
    with closing(_connexion()) as con, con:
        con.execute("DELETE FROM chargements WHERE page = ?", (page,))


def _cle(v) -> tuple:
    """Identité d'un vol : compagnies, horaires, escales et aéroport de départ."""
    return (v.get("compagnies"), v.get("heure_depart"), v.get("heure_arrivee"),
            v.get("detail_escales") or "", v.get("aeroport_depart") or "")


def _prix(p):
    return None if p is None or pd.isna(p) else int(p)


def empreinte(df: pd.DataFrame) -> Counter:
    """Ensemble (vol, prix) d'une recherche, pour comparer deux chargements."""
    return Counter((_cle(v), _prix(v.get("prix_eur"))) for v in df.to_dict("records"))


def resume(df: pd.DataFrame) -> dict:
    prix = pd.to_numeric(df["prix_eur"], errors="coerce")
    escales = pd.to_numeric(df.get("escales", pd.Series(dtype=float)), errors="coerce")
    duree = pd.to_numeric(df.get("duree_min", pd.Series(dtype=float)), errors="coerce")
    plus_court = df.loc[duree.idxmin()] if duree.notna().any() else None
    aeroports = df.get("aeroport_depart", pd.Series(dtype=str)).fillna("").value_counts()
    return {
        "Vols trouvés": len(df),
        "Avec prix": int(prix.notna().sum()),
        "Prix non disponible": int(prix.isna().sum()),
        "Compagnies": df["compagnies"].nunique(),
        "Vols directs": int((escales == 0).sum()),
        "Prix le plus bas": f"{int(prix.min())} €" if prix.notna().any() else "—",
        "Prix médian": f"{int(prix.median())} €" if prix.notna().any() else "—",
        "Prix le plus haut": f"{int(prix.max())} €" if prix.notna().any() else "—",
        "Durée la plus courte": f"{plus_court['duree']} ({plus_court['compagnies']})" if plus_court is not None else "—",
        "Aéroports de départ": ", ".join(f"{a.replace('Aéroport de ', '')} ({n})" for a, n in aeroports.items() if a),
    }


def par_compagnie(df: pd.DataFrame) -> pd.DataFrame:
    d = df.assign(prix=pd.to_numeric(df["prix_eur"], errors="coerce"))
    t = d.groupby("compagnies").agg(vols=("prix", "size"), avec_prix=("prix", "count"), prix_min=("prix", "min"))
    t["sans_prix"] = t["vols"] - t["avec_prix"]
    t = t.reset_index().sort_values(["prix_min", "compagnies"], na_position="last")
    return t.rename(columns={"compagnies": "Compagnie", "vols": "Vols", "avec_prix": "Avec prix",
                             "sans_prix": "Prix non disponible", "prix_min": "Prix le plus bas"})[
        ["Compagnie", "Vols", "Avec prix", "Prix non disponible", "Prix le plus bas"]]


def comparer(avant: Counter, apres: Counter) -> str:
    if avant == apres:
        return "✅ Identique"
    vols_avant = {k for k, _ in avant}
    vols_apres = {k for k, _ in apres}
    nouveaux, disparus = vols_apres - vols_avant, vols_avant - vols_apres
    prix_avant = {k: p for k, p in avant}
    changes = sum(1 for k, p in apres if k in prix_avant and prix_avant[k] != p)
    morceaux = [f"+{len(nouveaux)} vol(s)" if nouveaux else "", f"−{len(disparus)} vol(s)" if disparus else "",
                f"{changes} prix changé(s)" if changes else ""]
    return "⚠️ Différent : " + ", ".join(m for m in morceaux if m) if any(morceaux) else "⚠️ Différent"


def afficher(df: pd.DataFrame, page: str, nouvelle: bool, trajet: str, source: str = ""):
    """Affiche le récapitulatif ; si `nouvelle`, enregistre ce chargement dans l'historique cumulé (vols.db)."""
    if df is None or df.empty:
        return
    r = resume(df)
    if nouvelle:
        _enregistrer(page, trajet, source, r, empreinte(df))

    st.subheader("Récapitulatif")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Vols trouvés", r["Vols trouvés"])
    c2.metric("Avec prix", r["Avec prix"])
    c3.metric("Prix non disponible", r["Prix non disponible"])
    c4.metric("Compagnies", r["Compagnies"])
    c5.metric("Vols directs", r["Vols directs"])
    st.caption(f"Prix : {r['Prix le plus bas']} (bas) · {r['Prix médian']} (médian) · {r['Prix le plus haut']} (haut)"
               f" — Durée la plus courte : {r['Durée la plus courte']}"
               + (f" — Départs : {r['Aéroports de départ']}" if r["Aéroports de départ"] else "")
               + (f" — Source : {source}" if source else ""))

    with st.expander("Détail par compagnie", expanded=False):
        st.dataframe(par_compagnie(df), hide_index=True, width="stretch",
                     column_config={"Prix le plus bas": st.column_config.NumberColumn(format="%d €")})

    historique = _historique(page)
    if historique:
        st.markdown(f"**Historique des chargements** — {len(historique)} chargement(s) enregistré(s), conservés "
                    "jusqu'à ce que vous les effaciez. Relancez la recherche à quelques minutes d'intervalle pour "
                    "vérifier que les résultats restent les mêmes.")
        lignes, precedents = [], {}
        for h in historique:  # ordre chronologique : chaque chargement est comparé au précédent du même trajet
            avant = precedents.get(h["trajet"])
            lignes.append({
                "Date et heure": datetime.fromisoformat(h["quand"]).strftime("%d/%m/%Y %H:%M:%S"),
                "Trajet": h["trajet"],
                **{k: h["resume"].get(k) for k in ("Vols trouvés", "Avec prix", "Prix non disponible", "Compagnies",
                                                   "Prix le plus bas")},
                "Par rapport au chargement précédent": comparer(avant, h["empreinte"]) if avant is not None
                else "— (premier chargement)",
                "Source": h["source"]})
            precedents[h["trajet"]] = h["empreinte"]
        st.dataframe(pd.DataFrame(lignes[::-1]), hide_index=True, width="stretch")  # le plus récent en haut
        if st.button("Effacer l'historique", key=f"effacer_{page}"):
            _effacer(page)
            st.rerun()
