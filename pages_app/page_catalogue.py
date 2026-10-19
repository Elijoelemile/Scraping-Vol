"""Page « Catalogue » : prix le plus bas de chaque jour sur 1 an, par trajet (données collectées par catalogue.py)."""
import asyncio
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import catalogue
import db
import recap
from google_flights_scraper import Trajet, vers_dataframe

COULEUR = "#2a78d6"
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
# Vega-Lite affiche les mois en anglais : étiquettes d'axe traduites
MOIS_AXE = ("['janv.','févr.','mars','avr.','mai','juin','juil.','août','sept.','oct.','nov.','déc.']"
            "[month(datum.value)] + ' ' + year(datum.value)")


def avec_prix(df, colonne, vide):
    """Affiche les prix en « 123 € » et les prix absents avec un texte, en gardant le tri numérique."""
    return df.style.format(lambda v: f"{v:.0f} €", subset=[colonne], na_rep=vide)

st.title("Catalogue des prix sur 1 an")
st.caption("Prix le plus bas de chaque jour de départ — aller simple, 1 adulte, classe économique, en euros.")

e = catalogue.etat()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Trajets connus", f"{e['trajets']:,}".replace(",", " "))
c2.metric("Trajets collectés", f"{e['trajets_collectes']:,}".replace(",", " "))
c3.metric("Prix journaliers", f"{e['prix_journaliers']:,}".replace(",", " "))
c4.metric("Dernière collecte", f"{pd.Timestamp(e['plus_recente']):%d/%m %H:%M}" if e["plus_recente"] else "—")

if not e["trajets"]:
    st.info("Le catalogue est vide. Dans un terminal :\n\n"
            "```\n.venv\\Scripts\\python catalogue.py decouvrir\n.venv\\Scripts\\python catalogue.py collecter\n```")
    st.stop()

tous = catalogue.trajets()
origines = (tous.groupby("origine")["ville_depart"].first().fillna("").sort_values())
f1, f2 = st.columns(2)
# Départ proposé à l'ouverture : Paris-CDG (sinon le premier de la liste), plutôt que le premier par ordre alphabétique
codes_origines = list(origines.index)
origine = f1.selectbox("Départ", codes_origines, index=codes_origines.index("CDG") if "CDG" in codes_origines else 0,
                       format_func=lambda o: f"{origines[o]} ({o})".strip())
dests = tous[tous["origine"] == origine]
libelles = {r.destination: f"{r.ville_arrivee} ({r.destination}, {r.pays_arrivee})" for r in dests.itertuples()}
destination = f2.selectbox("Arrivée", [None] + sorted(libelles, key=libelles.get),
                           format_func=lambda d: "Toutes les destinations" if d is None else libelles[d])

# --- Toutes les destinations depuis un départ ---------------------------------
if destination is None:
    vue = dests[["ville_arrivee", "destination", "pays_arrivee", "prix_min", "date_prix_min", "nb_jours_prix",
                 "collecte_le"]].copy()
    vue["date_prix_min"] = pd.to_datetime(vue["date_prix_min"])
    vue["collecte_le"] = pd.to_datetime(vue["collecte_le"])
    st.dataframe(
        vue.rename(columns={"ville_arrivee": "Ville d'arrivée", "destination": "Code", "pays_arrivee": "Pays",
                            "prix_min": "Prix le plus bas", "date_prix_min": "Meilleure date",
                            "nb_jours_prix": "Jours avec prix", "collecte_le": "Collecté le"}),
        hide_index=True, width="stretch",
        column_config={"Prix le plus bas": st.column_config.NumberColumn(format="%d €"),
                       "Meilleure date": st.column_config.DateColumn(format="DD/MM/YYYY"),
                       "Collecté le": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")},
    )
    st.caption("Choisissez une arrivée pour voir le calendrier des prix sur l'année.")
    st.stop()

# --- Un trajet : calendrier sur l'année ------------------------------------------

t1, t2, t3 = st.columns([2, 1, 2])
aller_retour = t1.radio("Type de trajet", ["Aller simple", "Aller-retour"], horizontal=True) == "Aller-retour"
duree = t2.number_input("Durée du séjour (jours)", 1, 60, 7) if aller_retour else None
maj = t3.button("🔄 Mettre à jour maintenant", help="Interroge Google Flights (quelques secondes)")


def lancer(coro, message):
    try:
        with st.spinner(message):
            asyncio.run(coro)
        return True
    except catalogue.Bloque:
        st.error("Google bloque temporairement les requêtes (captcha). Réessayez plus tard.")
        return False


if aller_retour:
    # Calculé à la demande puis gardé 24 h (trop de combinaisons départ × durée pour tout précollecter)
    cal = pd.DataFrame() if maj else catalogue.calendrier_ar_trajet(origine, destination, duree, timedelta(hours=24))
    if cal.empty:
        if lancer(catalogue.collecter_ar(origine, destination, duree),
                  f"Calcul des prix aller-retour ({duree} jours de séjour) sur l'année…"):
            cal = catalogue.calendrier_ar_trajet(origine, destination, duree)
else:
    if maj:
        async def maj_aller():
            gf = catalogue.GoogleFlights(delai=1.0)
            try:
                await gf.ouvrir()
                fin = await catalogue.horizon(gf, origine, destination)
                prix = {}
                for a, b in catalogue.tranches(date.today(), fin):
                    prix |= await gf.calendrier(origine, destination, a, b)
                catalogue.enregistrer_calendrier(origine, destination, prix)
            finally:
                await gf.fermer()
        lancer(maj_aller(), "Collecte du calendrier…")
    cal = catalogue.calendrier_trajet(origine, destination)

if cal.empty:
    st.info("Aucun prix disponible pour ce trajet (pas encore collecté, ou Google n'a pas de prix en mémoire).")
    st.stop()

libelle_prix = "Prix aller-retour" if aller_retour else "Prix aller simple"
cal["date"] = pd.to_datetime(cal["date"])
cal["jour_fr"] = [f"{JOURS[d.weekday()]} {d:%d/%m/%Y}" for d in cal["date"]]
if aller_retour:
    cal["retour_fr"] = [f"{JOURS[d.weekday()]} {d:%d/%m/%Y}" for d in pd.to_datetime(cal["date_retour"])]
moins_cher = cal.loc[cal["prix"].idxmin()]
st.caption(f"{libelle_prix} le plus bas pour chaque date de départ"
           + (f", retour {duree} jours plus tard" if aller_retour else "")
           + f" — données du {pd.Timestamp(cal['collecte_le'].max()):%d/%m/%Y à %H:%M}.")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Prix le plus bas", f"{moins_cher['prix']} €")
m1.caption(f"départ le {moins_cher['date']:%d/%m/%Y}"
           + (f", retour le {pd.Timestamp(moins_cher['date_retour']):%d/%m/%Y}" if aller_retour else ""))
m2.metric("Prix médian", f"{int(cal['prix'].median())} €")
m3.metric("Prix le plus haut", f"{cal['prix'].max()} €")
m4.metric("Jours avec prix", len(cal))
m4.caption(f"du {cal['date'].min():%d/%m/%Y} au {cal['date'].max():%d/%m/%Y}")

survol = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
base = alt.Chart(cal).encode(
    x=alt.X("date:T", title="Date de départ", axis=alt.Axis(labelExpr=MOIS_AXE, tickCount="month")),
    y=alt.Y("prix:Q", title=f"{libelle_prix} (€)", scale=alt.Scale(zero=False), axis=alt.Axis(format="d")),
)
infobulle = [alt.Tooltip("jour_fr:N", title="Départ")]
if aller_retour:
    infobulle.append(alt.Tooltip("retour_fr:N", title="Retour"))
infobulle.append(alt.Tooltip("prix:Q", title=f"{libelle_prix} (€)"))
ligne = base.mark_line(color=COULEUR, strokeWidth=2, interpolate="step-after")
points = base.mark_point(size=70, filled=True, color=COULEUR).encode(
    opacity=alt.condition(survol, alt.value(1), alt.value(0)), tooltip=infobulle,
).add_params(survol)
repere = base.mark_rule(color="#888", strokeDash=[3, 3]).encode(opacity=alt.condition(survol, alt.value(1), alt.value(0)))
minimum = alt.Chart(cal[cal["prix"] == cal["prix"].min()]).mark_point(
    shape="triangle-down", size=90, filled=True, color=COULEUR).encode(x="date:T", y="prix:Q")
st.altair_chart((ligne + repere + points + minimum).properties(height=320), width="stretch")

# Toutes les dates, d'aujourd'hui à la dernière date disponible (jours sans prix compris)
premier = max(cal["date"].min().date(), date.today())
dernier = cal["date"].max().date()
toutes = pd.DataFrame({"date": pd.date_range(premier, dernier)}).merge(cal[["date", "prix"]], on="date", how="left")
toutes["Départ"] = [f"{JOURS[d.weekday()]} {d:%d/%m/%Y}" for d in toutes["date"]]
colonnes = ["Départ"]
if aller_retour:
    toutes["Retour"] = [f"{JOURS[r.weekday()]} {r:%d/%m/%Y}" for r in toutes["date"] + pd.Timedelta(days=duree)]
    colonnes.append("Retour")
toutes = toutes.rename(columns={"prix": "Prix"})
colonnes.append("Prix")

g1, g2 = st.columns([3, 2])
with g1:
    st.markdown(f"**Toutes les dates** — {len(toutes)} jours, dont {int(toutes['Prix'].notna().sum())} avec un prix "
                "au catalogue")
    st.caption("Cliquez sur une ligne pour choisir cette date ; cliquez sur « Prix » pour trier. "
               "« pas de prix » = pas de prix au catalogue (une recherche peut quand même trouver des vols).")
    selection = st.dataframe(
        avec_prix(toutes[colonnes], "Prix", "pas de prix"), hide_index=True, width="stretch", height=420,
        on_select="rerun", selection_mode="single-row", key=f"dates_{origine}_{destination}_{duree}",
    )
    lignes = selection.selection.rows
    choisi = toutes.loc[lignes[0], "date"].date() if lignes else None
with g2:
    st.markdown("**Voir les vols d'une date**")
    jour = st.date_input("Date de départ", choisi or premier, min_value=premier, max_value=dernier,
                         format="DD/MM/YYYY", key=f"jour_{choisi}")
    retour = jour + timedelta(days=duree) if aller_retour else None
    ligne_jour = cal[cal["date"].dt.date == jour]
    st.caption((f"Retour le {retour:%d/%m/%Y}. " if retour else "")
               + (f"Prix le plus bas au catalogue : {ligne_jour['prix'].iloc[0]} €" if len(ligne_jour)
                  else "Pas de prix au catalogue pour cette date.")
               + f" — Date la moins chère : {JOURS[moins_cher['date'].weekday()]} {moins_cher['date']:%d/%m/%Y}"
               f" ({moins_cher['prix']} €).")
    ville = st.checkbox("Tous les aéroports des villes (ex. Paris : CDG + Orly)", value=True,
                        help="Comme lorsqu'on tape « Paris » sur Google Flights")
    if st.button("Rechercher les vols", type="primary"):
        try:
            with st.spinner("Recherche sur Google Flights (5 à 30 s)…"):
                vols = asyncio.run(catalogue.rechercher([Trajet(origine, destination, jour.isoformat(),
                                                       retour.isoformat() if retour else None)], ville=ville,
                                                       compte=st.session_state.get("utiliser_compte", False)))
        except catalogue.Bloque:
            vols = None
            st.error("Google bloque temporairement les recherches depuis votre connexion (captcha). Attendez quelques heures sans lancer de recherche, puis réessayez.")
        except Exception as e:  # erreur réseau ou autre : message lisible plutôt qu'un plantage
            vols = None
            st.error(f"La recherche a échoué ({type(e).__name__}). Réessayez dans quelques minutes.")
        if vols is None:
            pass
        elif vols:
            db.enregistrer(vols)
            st.session_state["vols_jour"] = vers_dataframe(vols)
            st.session_state["vols_jour_brut"] = vers_dataframe(vols, complet=True)
            st.session_state["source_jour"] = sorted({v.get("source", "") for v in vols} - {""})
            st.session_state["trajet_jour"] = (f"{origine}→{destination} {jour:%d/%m}"
                                               + (f"↔{retour:%d/%m}" if retour else ""))
            st.session_state["recap_nouveau_jour"] = True
        else:
            st.error("Aucun vol récupéré (pas de vol ce jour-là, ou blocage temporaire de Google).")

if "vols_jour" in st.session_state:
    if aller_retour:
        st.caption("Aller-retour : horaires, durée et compagnie du vol aller ; prix total aller-retour.")
    vj = st.session_state["vols_jour"]
    st.dataframe(avec_prix(vj, "Prix (EUR)", "Prix non disponible"), hide_index=True, width="stretch",
                 column_config={"Logo": st.column_config.ImageColumn("Logo", width="small")})
    if st.session_state.get("source_jour"):
        st.caption("Source des prix : " + " ; ".join(st.session_state["source_jour"]) + ".")
    if vj["Prix (EUR)"].isna().any():
        st.caption(f"{int(vj['Prix (EUR)'].isna().sum())} vol(s) en fin de liste « Prix non disponible » : Google n'a pas de prix pour ces vols au moment de la recherche.")
    recap.afficher(st.session_state.get("vols_jour_brut"), "historique_catalogue",
                   nouvelle=st.session_state.pop("recap_nouveau_jour", False),
                   trajet=st.session_state.get("trajet_jour", ""),
                   source=" ; ".join(st.session_state.get("source_jour", [])))
