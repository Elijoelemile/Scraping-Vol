"""Page « Recherche détaillée » : vols d'un ou plusieurs trajets à une date (scraping Google Flights)."""
import asyncio
import itertools
from datetime import date, timedelta

import airportsdata
import altair as alt
import pandas as pd
import streamlit as st

import db
import catalogue
import recap
from google_flights_scraper import Trajet, tableau_comparateur
from titres import titre_page

MAX_TRAJETS = 12  # au-delà, risque de blocage par Google
LIBELLES_CLASSES = {"eco": "Économique", "premium": "Premium éco", "affaires": "Affaires", "premiere": "Première"}
COULEUR_BARRES = "#2a78d6"
# Palette catégorielle (ordre fixe, validée pour daltonisme) : une couleur par trajet
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
FRAICHEUR = {"Toujours relancer": None, "1 h": 1, "6 h": 6, "24 h": 24, "7 jours": 24 * 7}


def avec_prix(df, colonne, vide):
    """Affiche les prix en « 123 € » et les prix absents avec un texte, en gardant le tri numérique."""
    return df.style.format(lambda v: f"{v:.0f} €", subset=[colonne], na_rep=vide)


@st.cache_data
def aeroports() -> dict[str, str]:
    """Code IATA -> libellé lisible, trié par ville."""
    data = airportsdata.load("IATA")
    items = sorted(data.values(), key=lambda a: (a["city"], a["name"]))
    return {a["iata"]: f"{a['city']} — {a['name']} ({a['iata']}, {a['country']})" for a in items}


AEROPORTS = aeroports()


def choix_aeroports(label: str, defaut: list[str], key: str) -> list[str]:
    return st.multiselect(label, options=list(AEROPORTS), default=defaut, key=key,
                          format_func=AEROPORTS.get, placeholder="Tapez une ville ou un code (ex. Paris, JFK)")


def _fr(d: str) -> str:
    return f"{d[8:10]}/{d[5:7]}" if d else ""


def libelle_trajet(r) -> str:
    dates = _fr(r["date_aller"]) + (f"–{_fr(r['date_retour'])}" if r["date_retour"] else "")
    return f"{r['ville_depart']} → {r['ville_arrivee']} ({dates})"


# --- Formulaire de recherche -------------------------------------------------
with st.sidebar:
    st.header("✈️ Recherche")
    aller_retour = st.radio("Type de trajet", ["Aller simple", "Aller-retour"], horizontal=True) == "Aller-retour"
    origines = choix_aeroports("Aéroport(s) de départ", ["CDG"], "orig")
    destinations = choix_aeroports("Aéroport(s) d'arrivée", ["JFK"], "dest")

    demain = date.today() + timedelta(days=1)
    d_aller = st.date_input("Date de départ (aller)", demain + timedelta(days=30), min_value=demain,
                            format="DD/MM/YYYY")
    d_retour = None
    if aller_retour:
        d_retour = st.date_input("Date de retour", d_aller + timedelta(days=7), min_value=d_aller,
                                 format="DD/MM/YYYY")

    c1, c2 = st.columns([1, 2])
    adultes = c1.number_input("Adultes", 1, 9, 1)
    classe = {v: k for k, v in LIBELLES_CLASSES.items()}[c2.selectbox("Classe", list(LIBELLES_CLASSES.values()))]
    fraicheur = FRAICHEUR[st.selectbox(
        "Réutiliser les résultats enregistrés de moins de", list(FRAICHEUR), index=2,
        help="Évite de relancer Google Flights si le trajet a déjà été recherché récemment")]
    ville = st.checkbox("Tous les aéroports des villes (ex. Paris : CDG + Orly)", value=True,
                        help="Comme lorsqu'on tape « Paris » sur Google Flights")

    trajets = [Trajet(o, d, d_aller.isoformat(), d_retour.isoformat() if d_retour else None, adultes, classe)
               for o, d in itertools.product(origines, destinations) if o != d]
    st.caption(f"{len(trajets)} trajet(s) à rechercher")
    lancer = st.button("Rechercher", type="primary", width="stretch",
                       disabled=not trajets or len(trajets) > MAX_TRAJETS)
    if len(trajets) > MAX_TRAJETS:
        st.warning(f"Maximum {MAX_TRAJETS} trajets par recherche (départs × arrivées).")

    # --- Recherches enregistrées ---
    st.divider()
    st.header("🗂️ Recherches enregistrées")
    passees = db.liste_recherches()
    if passees.empty:
        st.caption("Aucune recherche enregistrée pour l'instant.")
    else:
        libelles = {r.id: f"{r.scrape_le[8:10]}/{r.scrape_le[5:7]} {r.scrape_le[11:16]} · {libelle_trajet(r._asdict())}"
                             f" · {r.prix_min} €"
                    for r in passees.itertuples()}
        choix = st.multiselect("Choisir", list(libelles), format_func=libelles.get, label_visibility="collapsed",
                               placeholder=f"{len(passees)} recherche(s) — choisir…")
        b1, b2 = st.columns(2)
        if b1.button("Afficher", width="stretch", disabled=not choix):
            st.session_state["recherche_ids"] = choix
            st.rerun()
        if b2.button("Supprimer", width="stretch", disabled=not choix):
            db.supprimer_recherches(choix)
            st.session_state["recherche_ids"] = [i for i in st.session_state.get("recherche_ids", [])
                                                 if i not in choix]
            st.rerun()


titre_page("Recherche détaillée", "recherche")

# --- Recherche : base d'abord, Google Flights ensuite ------------------------
if lancer:
    max_age = timedelta(hours=fraicheur) if fraicheur else None
    en_cache = [db.derniere_recherche(t, max_age) if max_age else None for t in trajets]
    ids = [i for i in en_cache if i]
    a_scraper = [t for t, i in zip(trajets, en_cache) if not i]

    with st.status(f"Recherche de {len(trajets)} trajet(s)…", expanded=True) as status:
        if ids:
            st.write(f"💾 {len(ids)} trajet(s) repris de la base (moins de {fraicheur} h)")
        if a_scraper:
            barre = st.progress(0.0, text="Google Flights…")

            def progression(i, total, msg):
                barre.progress(i / total, text="Google Flights…")
                st.write(("⚠️ " if "ÉCHEC" in msg else "✅ ") + msg)

            try:
                vols = asyncio.run(catalogue.rechercher(a_scraper, on_progress=progression, ville=ville,
                                                        compte=st.session_state.get("utiliser_compte", False)))
            except catalogue.Bloque:
                vols = []
                st.error("Google bloque temporairement les recherches depuis votre connexion (captcha). Attendez quelques heures sans lancer de recherche, puis réessayez.")
            except Exception as e:  # erreur réseau ou autre : message lisible plutôt qu'un plantage
                vols = []
                st.error(f"La recherche a échoué ({type(e).__name__}). Réessayez dans quelques minutes.")
            ids += db.enregistrer(vols)
            st.session_state["source_recherche"] = sorted({v.get("source", "") for v in vols} - {""})
        if not a_scraper:
            st.session_state["source_recherche"] = ["résultats enregistrés (pas de nouvelle recherche Google)"]
        if ids:
            st.session_state["recherche_ids"] = ids
            st.session_state["recap_nouveau"] = True
            status.update(label=f"{len(ids)} trajet(s) chargé(s)", state="complete", expanded=False)
        else:
            status.update(label="Aucun vol trouvé", state="error")


# --- Résultats -----------------------------------------------------------------
ids = st.session_state.get("recherche_ids")
df = db.charger_vols(ids) if ids else pd.DataFrame()
if df.empty:
    st.info("Choisissez vos aéroports et vos dates dans le panneau de gauche, puis lancez la recherche. "
            "Chaque recherche est enregistrée et peut être rouverte depuis « Recherches enregistrées ».")
    st.stop()

collectes = df.groupby(["origine", "destination"])["scrape_le"].first()
st.caption("Données collectées le " + " · ".join(
    f"{o}→{d} : {s[8:10]}/{s[5:7]} à {s[11:16]}" for (o, d), s in collectes.items()))

# Filtres
f1, f2, f3, f4 = st.columns([3, 2, 2, 1.4])
compagnies = sorted(df["compagnies"].unique())
choix_cies = f1.multiselect("Compagnies", compagnies, placeholder="Toutes")
prix_lo, prix_hi = int(df["prix_eur"].min()), int(df["prix_eur"].max())
prix_max = f2.slider("Prix max (€)", prix_lo, max(prix_hi, prix_lo + 10), prix_hi, step=10)
duree_h_max = int(df["duree_min"].max() // 60 + 1)
duree_max = f3.slider("Durée max (h)", 1, max(duree_h_max, 2), duree_h_max)
ESCALES = {"Toutes": None, "Direct": 0, "≤ 1 escale": 1, "≤ 2 escales": 2}
escales_max = ESCALES[f4.selectbox("Escales", list(ESCALES))]

# Les vols sans prix (non communiqué par Google) restent affichés quel que soit le filtre de prix
vue = df[((df["prix_eur"] <= prix_max) | df["prix_eur"].isna()) & (df["duree_min"] <= duree_max * 60)]
if choix_cies:
    vue = vue[vue["compagnies"].isin(choix_cies)]
if escales_max is not None:
    vue = vue[vue["escales"] <= escales_max]

if vue.empty:
    st.warning("Aucun vol ne correspond aux filtres.")
    st.stop()

# Indicateurs
moins_cher = vue.loc[vue["prix_eur"].idxmin()]
plus_rapide = vue.loc[vue["duree_min"].idxmin()]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Vols", len(vue))
m2.metric("Prix le plus bas", f"{int(moins_cher['prix_eur'])} €")
m2.caption(f"{moins_cher['compagnies']} · {moins_cher['ville_depart']} → {moins_cher['ville_arrivee']}")
m3.metric("Prix médian", f"{int(vue['prix_eur'].median())} €")
m4.metric("Vol le plus rapide", plus_rapide["duree"])
m4.caption(f"{plus_rapide['compagnies']} · "
           + ("prix non communiqué" if pd.isna(plus_rapide["prix_eur"]) else f"{int(plus_rapide['prix_eur'])} €"))

tab_table, tab_graph, tab_histo = st.tabs(["📋 Tableau", "📊 Prix par compagnie", "📈 Historique des prix"])

with tab_table:
    tableau = tableau_comparateur(vue).sort_values("Prix (EUR)")
    st.dataframe(avec_prix(tableau, "Prix (EUR)", "Prix non disponible"), hide_index=True, width="stretch",
                 column_config={"Logo": st.column_config.ImageColumn("Logo", width="small")})
    if st.session_state.get("source_recherche"):
        st.caption("Source des prix (dernière recherche) : " + " ; ".join(st.session_state["source_recherche"]) + ".")
    if tableau["Prix (EUR)"].isna().any():
        st.caption(f"{int(tableau['Prix (EUR)'].isna().sum())} vol(s) en fin de liste « Prix non disponible » : Google n'a pas de prix pour ces vols au moment de la recherche.")
    st.download_button("Télécharger en CSV", tableau.to_csv(index=False, sep=";").encode("utf-8-sig"),
                       "comparateur_vols.csv", "text/csv")

with tab_graph:
    par_cie = (vue.groupby("compagnies")
               .agg(prix_min=("prix_eur", "min"), vols=("prix_eur", "size"), duree_min=("duree_min", "min"))
               .reset_index().dropna(subset=["prix_min"]).sort_values("prix_min"))
    barres = alt.Chart(par_cie).mark_bar(color=COULEUR_BARRES, cornerRadiusEnd=4, size=18).encode(
        x=alt.X("prix_min:Q", title="Prix le plus bas (€)"),
        y=alt.Y("compagnies:N", sort="x", title=None),
        tooltip=[alt.Tooltip("compagnies:N", title="Compagnie"),
                 alt.Tooltip("prix_min:Q", title="Prix le plus bas (€)"),
                 alt.Tooltip("vols:Q", title="Nombre de vols")],
    )
    # Étiquettes de valeur dans la couleur de texte du thème (pas celle des barres)
    etiquettes = barres.mark_text(align="left", dx=4).encode(text=alt.Text("prix_min:Q", format="d"))
    # Une ligne de 32 px par compagnie : toutes les étiquettes restent lisibles
    st.altair_chart((barres + etiquettes).properties(height=alt.Step(32)), width="stretch")

with tab_histo:
    cles = df[db.CLE_TRAJET].drop_duplicates()
    trajets_affiches = [Trajet(r.origine, r.destination, r.date_aller, r.date_retour or None, int(r.adultes), r.classe)
                        for r in cles.itertuples()]
    histo = db.historique(trajets_affiches)
    histo["trajet"] = histo.apply(libelle_trajet, axis=1)
    histo["collecte"] = pd.to_datetime(histo["scrape_le"])

    # Une couleur fixe par trajet (ordre alphabétique), 8 trajets maximum sur le graphique
    noms = sorted(histo["trajet"].unique())[:len(PALETTE)]
    if len(histo["trajet"].unique()) > len(PALETTE):
        st.caption(f"Graphique limité aux {len(PALETTE)} premiers trajets ; le tableau ci-dessous les contient tous.")
    graph = histo[histo["trajet"].isin(noms)]
    couleur = alt.Color("trajet:N", title="Trajet", scale=alt.Scale(domain=noms, range=PALETTE[:len(noms)]),
                        legend=alt.Legend(orient="top", direction="horizontal", columns=2))
    # Graduations sur les vraies dates de collecte quand elles sont peu nombreuses (évite les doublons d'étiquettes)
    instants = sorted(graph["collecte"].unique())
    axe_x = alt.Axis(format="%d/%m %H:%M", labelOverlap=True,
                     values=[pd.Timestamp(t).isoformat() for t in instants] if len(instants) <= 10 else alt.Undefined)
    base = alt.Chart(graph).encode(
        x=alt.X("collecte:T", title="Date de collecte", axis=axe_x),
        y=alt.Y("prix_min:Q", title="Prix le plus bas (€)", scale=alt.Scale(zero=False),
                axis=alt.Axis(format="d", tickMinStep=1)),
        color=couleur,
        tooltip=[alt.Tooltip("trajet:N", title="Trajet"),
                 alt.Tooltip("collecte:T", title="Collecté le", format="%d/%m/%Y %H:%M"),
                 alt.Tooltip("prix_min:Q", title="Prix le plus bas (€)"),
                 alt.Tooltip("compagnie_moins_chere:N", title="Compagnie"),
                 alt.Tooltip("nb_vols:Q", title="Vols trouvés")],
    )
    lignes = base.mark_line(strokeWidth=2)
    points = base.mark_point(filled=True, size=70, opacity=1)
    st.altair_chart((lignes + points).properties(height=320), width="stretch")
    if histo.groupby("trajet").size().max() < 2:
        st.info("Une seule collecte par trajet pour l'instant : relancez la même recherche plus tard "
                "(avec « Toujours relancer ») pour voir les prix évoluer.")

    st.dataframe(
        histo[["trajet", "collecte", "prix_min", "compagnie_moins_chere", "nb_vols"]]
        .sort_values(["trajet", "collecte"], ascending=[True, False])
        .rename(columns={"trajet": "Trajet", "collecte": "Collecté le", "prix_min": "Prix le plus bas",
                         "compagnie_moins_chere": "Compagnie", "nb_vols": "Vols"}),
        hide_index=True, width="stretch",
        column_config={"Prix le plus bas": st.column_config.NumberColumn(format="%d €"),
                       "Collecté le": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")},
    )


# --- Récapitulatif et historique des chargements ---------------------------------
st.divider()
trajets_affiches = sorted({f"{r.origine}→{r.destination} {r.date_aller[8:10]}/{r.date_aller[5:7]}"
                           + (f"↔{r.date_retour[8:10]}/{r.date_retour[5:7]}" if r.date_retour else "")
                           for r in df[["origine", "destination", "date_aller", "date_retour"]].itertuples()})
recap.afficher(df, "historique_recherche", nouvelle=st.session_state.pop("recap_nouveau", False),
               trajet=" + ".join(trajets_affiches), source=" ; ".join(st.session_state.get("source_recherche", [])))
