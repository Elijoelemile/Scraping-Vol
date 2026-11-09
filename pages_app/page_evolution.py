"""Page « Evolution tarifs des vols » : tarifs aller-retour Google Flights d'une destination à l'autre, pour chaque
trajet (aéroport de départ → destination) et chaque compagnie, au fil des dates de départ.

Source : les recherches d'une date du Catalogue (table vols_jour) ; chaque date garde le dernier tarif trouvé (une
nouvelle recherche remplace l'ancien).
"""
from contextlib import closing
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

import catalogue
from titres import intitule, titre_page

ENCRE, ENCRE_DOUCE, GRILLE = "#0b0b0b", "#52514e", "#ececea"
# Une couleur par compagnie, dans l'ordre où elles sont choisies (jamais réattribuée quand on en retire une)
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
# Tarif médian de la carte de chaleur : une seule teinte, du plus clair (moins cher) au plus foncé (plus cher)
DEGRADE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
MAX_DESTINATIONS, MAX_COMPAGNIES = 10, 8


@st.cache_data(show_spinner=False)
def nuits_disponibles(etat) -> pd.Series:
    """Nombre de tarifs aller-retour par durée de séjour (nuits)."""
    with closing(catalogue.connexion()) as con:
        n = pd.read_sql_query("SELECT CAST(julianday(date_retour) - julianday(date) AS INTEGER) AS nuits, "
                              "COUNT(*) AS n FROM vols_jour WHERE date_retour <> '' GROUP BY nuits", con)
    return n.set_index("nuits")["n"]


@st.cache_data(show_spinner="Lecture des tarifs…")
def tarifs(nuits: int, etat) -> pd.DataFrame:
    """Tarifs aller-retour de `nuits` nuits : une ligne par aéroport de départ, destination, date et compagnie."""
    with closing(catalogue.connexion()) as con:
        t = pd.read_sql_query(
            "SELECT origine, destination, date, compagnie, nom, prix, bagage, collecte_le FROM vols_jour "
            "WHERE date_retour <> '' AND CAST(julianday(date_retour) - julianday(date) AS INTEGER) = ?",
            con, params=[nuits])
    t["date"] = pd.to_datetime(t["date"])
    return t


titre_page("Evolution tarifs des vols", "evolution")
st.caption("Tarifs aller-retour Google Flights (1 adulte, classe économique, en euros) d'une destination à l'autre, "
           "pour chaque trajet et chaque compagnie, au fil des dates de départ. Chaque date garde le dernier tarif "
           "trouvé par le Catalogue.")

etat = catalogue._etat_base()  # les tableaux sont relus quand la base change (nouvelles recherches)
dispo = nuits_disponibles(etat)
if dispo.empty:
    st.info("Aucun tarif aller-retour pour l'instant : lancez des recherches dans la page « Catalogue aller-retour ».")
    st.stop()

# --- Choix : durée du séjour, départs, destinations, compagnies, période ----------------------------------------
f1, f2, f3 = st.columns([1.2, 2, 2.4])
nuits_defaut = st.session_state.get("nuits_catalogue")
options_nuits = list(dispo.sort_values(ascending=False).index)
nuits = f1.selectbox("Nombre de nuits", options_nuits,
                     index=options_nuits.index(nuits_defaut) if nuits_defaut in options_nuits else 0,
                     format_func=lambda n: f"{n} nuits ({dispo[n]:,} tarifs)".replace(",", " "))
t = tarifs(int(nuits), etat)
departs = f2.multiselect("Aéroports de départ", sorted(t["origine"].unique()), key="evolution_departs",
                         format_func=lambda c: f"{catalogue.nom_ville_airfox(c)} ({c})",
                         placeholder="Tous les aéroports de départ")
if departs:
    t = t[t["origine"].isin(departs)]
# destinations proposées des plus fournies aux moins fournies ; par défaut celles de la dernière table du Catalogue
nb_dest = t.groupby("destination")["date"].nunique().sort_values(ascending=False)
derniere = [d for d in dict.fromkeys(d for d, _ in sorted(catalogue.derniere_table(int(nuits)))) if d in nb_dest]
destinations = f3.multiselect(
    "Destinations", list(nb_dest.index), default=(derniere or list(nb_dest.index))[:3], key="evolution_destinations",
    max_selections=MAX_DESTINATIONS, placeholder="Choisir des destinations",
    format_func=lambda d: f"{catalogue.nom_ville_airfox(d)} ({d}) — {nb_dest[d]} date(s)")
if not destinations:
    st.info("Choisissez au moins une destination.")
    st.stop()
t = t[t["destination"].isin(destinations)]

noms_cies = t.groupby("compagnie")["nom"].agg(lambda s: s.mode().iloc[0] if not s.mode().empty else "")
nb_cies = t.groupby("compagnie")[["destination", "date"]].apply(lambda g: len(g.drop_duplicates())) \
    .sort_values(ascending=False)
g1, g2 = st.columns([4.4, 2.2])
compagnies = g1.multiselect(
    f"Compagnies (jusqu'à {MAX_COMPAGNIES}, une couleur chacune)", list(nb_cies.index),
    default=list(nb_cies.index[:5]), max_selections=MAX_COMPAGNIES, key="evolution_compagnies",
    format_func=lambda c: f"{c} — {noms_cies.get(c, '')} ({nb_cies[c]} dates)",
    help="Par défaut, les 5 compagnies présentes sur le plus de dates. Plusieurs codes (AF/KL) : vol partagé entre "
         "ces compagnies.")
premier, dernier = t["date"].min().date(), t["date"].max().date()
periode = g2.date_input("Dates de départ", (max(premier, date.today()), dernier), min_value=premier,
                        max_value=dernier, format="DD/MM/YYYY", key="evolution_periode")
if not compagnies:
    st.info("Choisissez au moins une compagnie.")
    st.stop()
t = t[t["compagnie"].isin(compagnies)]
if len(periode) == 2:
    t = t[(t["date"].dt.date >= periode[0]) & (t["date"].dt.date <= periode[1])]
if t.empty:
    st.info("Aucun tarif pour ces choix : élargissez la période, les destinations ou les compagnies.")
    st.stop()

t = t.assign(Destination=t["destination"].map(lambda d: f"{catalogue.nom_ville_airfox(d)} ({d})"),
             Compagnie=t["compagnie"].map(lambda c: f"{c} — {noms_cies.get(c, '')}"[:40]),
             Trajet=t["origine"] + " → " + t["destination"])
ordre_cies = [f"{c} — {noms_cies.get(c, '')}"[:40] for c in compagnies]
couleurs_cies = alt.Scale(domain=ordre_cies, range=PALETTE[:len(ordre_cies)])

m1, m2, m3, m4 = st.columns(4)
m1.metric("Destinations", t["destination"].nunique())
m2.metric("Compagnies", t["compagnie"].nunique())
m3.metric("Dates de départ", t["date"].nunique())
m4.metric("Tarifs", f"{len(t):,}".replace(",", " "))

# --- 1. D'une destination à l'autre : tarif médian de chaque compagnie sur chaque destination -------------------
intitule("D'une destination à l'autre : tarif médian par compagnie")
synthese = (t.groupby(["Destination", "Compagnie"])["prix"]
            .agg(Médian="median", Minimum="min", Maximum="max", Dates="count").reset_index())
base = alt.Chart(synthese).encode(
    x=alt.X("Compagnie:N", sort=ordre_cies, title=None,
            axis=alt.Axis(orient="top", labelColor=ENCRE, labelAngle=0, labelLimit=115, labelOverlap=False,
                          ticks=False, domain=False)),
    y=alt.Y("Destination:N", title=None, sort=[f"{catalogue.nom_ville_airfox(d)} ({d})" for d in destinations],
            axis=alt.Axis(labelColor=ENCRE, labelFontWeight="bold", ticks=False, domain=False)))
cases = base.mark_rect(cornerRadius=4, stroke="#fcfcfb", strokeWidth=2).encode(
    color=alt.Color("Médian:Q", scale=alt.Scale(range=DEGRADE),
                    legend=alt.Legend(title="Tarif médian (€)", orient="right", titleColor=ENCRE_DOUCE,
                                      labelColor=ENCRE_DOUCE, gradientLength=80)),
    tooltip=[alt.Tooltip("Destination:N"), alt.Tooltip("Compagnie:N"),
             alt.Tooltip("Médian:Q", title="Tarif médian (€)", format=".0f"),
             alt.Tooltip("Minimum:Q", title="Tarif le plus bas (€)", format=".0f"),
             alt.Tooltip("Maximum:Q", title="Tarif le plus haut (€)", format=".0f"),
             alt.Tooltip("Dates:Q", title="Tarifs")])
milieu = (synthese["Médian"].min() + synthese["Médian"].max()) / 2
textes = base.mark_text(fontSize=12).encode(
    text=alt.Text("Médian:Q", format=".0f"),
    color=alt.condition(alt.datum["Médian"] > milieu, alt.value("#ffffff"), alt.value(ENCRE)))
st.altair_chart((cases + textes).properties(height=alt.Step(44)), width="stretch")
st.caption("Chaque case : le tarif médian de la compagnie vers cette destination sur la période (plus la case est "
           "foncée, plus c'est cher). Case vide : la compagnie ne dessert pas cette destination ou n'a pas encore "
           "été trouvée. Survolez une case pour le tarif le plus bas et le plus haut.")

# --- 2. Au fil des dates de départ : une courbe par compagnie, un graphique par destination ---------------------
intitule("Au fil des dates de départ")
# plusieurs aéroports de départ pour une compagnie : son tarif le plus bas ce jour-là (aéroport dans l'infobulle)
courbes = t.loc[t.groupby(["Destination", "Compagnie", "date"])["prix"].idxmin()]
# mêmes échelles pour toutes les destinations : tarifs et dates se comparent d'un graphique à l'autre
echelle = alt.Scale(domain=[0, float(courbes["prix"].max()) * 1.05])
echelle_dates = alt.Scale(domain=[courbes["date"].min().isoformat(), courbes["date"].max().isoformat()])


def evolution(d: pd.DataFrame, avec_legende: bool) -> alt.LayerChart:
    """Courbes d'une destination : une par compagnie, au fil des dates de départ."""
    survol = alt.selection_point(fields=["Compagnie"], bind="legend", on="pointerover", clear="pointerout")
    couleur = alt.Color("Compagnie:N", scale=couleurs_cies, legend=alt.Legend(
        title=None, orient="top", columns=4, labelColor=ENCRE, labelLimit=220) if avec_legende else None)
    lignes = alt.Chart(d).mark_line(strokeWidth=2, interpolate="monotone").encode(
        x=alt.X("date:T", title="Date de départ", scale=echelle_dates,
                axis=alt.Axis(format="%d/%m", labelColor=ENCRE_DOUCE, titleColor=ENCRE_DOUCE, grid=False)),
        y=alt.Y("prix:Q", title="Tarif aller-retour (€)", scale=echelle,
                axis=alt.Axis(labelColor=ENCRE_DOUCE, titleColor=ENCRE_DOUCE, gridColor=GRILLE)),
        color=couleur, opacity=alt.condition(survol, alt.value(1), alt.value(0.25)))
    points = alt.Chart(d).mark_point(filled=True, size=50, stroke="#fcfcfb", strokeWidth=1.5).encode(
        x=alt.X("date:T", scale=echelle_dates), y=alt.Y("prix:Q", scale=echelle), color=alt.Color("Compagnie:N", scale=couleurs_cies, legend=None),
        opacity=alt.condition(survol, alt.value(1), alt.value(0.25)),
        tooltip=[alt.Tooltip("Destination:N"), alt.Tooltip("Compagnie:N"), alt.Tooltip("Trajet:N"),
                 alt.Tooltip("date:T", title="Départ", format="%d/%m/%Y"),
                 alt.Tooltip("prix:Q", title="Tarif (€)", format=".0f"),
                 alt.Tooltip("collecte_le:N", title="Recherché le")])
    return alt.layer(lignes, points).add_params(survol).properties(height=260)


premiere = True
for dest in destinations:
    d = courbes[courbes["destination"] == dest]
    if d.empty:
        continue
    st.markdown(f"**{catalogue.nom_ville_airfox(dest)} ({dest})**")
    st.altair_chart(evolution(d, premiere), width="stretch")
    premiere = False
st.caption("Une courbe par compagnie (même couleur sur toutes les destinations) ; l'échelle des tarifs est la même "
           "d'une destination à l'autre pour pouvoir les comparer. Survolez une compagnie de la légende pour la "
           "mettre en avant, et un point pour le détail.")

# --- 3. Tableau : chaque trajet sur chaque compagnie ------------------------------------------------------------
intitule("Chaque trajet sur chaque compagnie")
idx_min = t.groupby(["Trajet", "Compagnie"])["prix"].idxmin()
tableau = (t.groupby(["Destination", "Trajet", "Compagnie"])
           .agg(**{"Tarif le plus bas": ("prix", "min"), "Tarif médian": ("prix", "median"),
                   "Tarif le plus haut": ("prix", "max"), "Dates": ("date", "nunique"),
                   "Bagage inclus": ("bagage", lambda s: {1: "TRUE", 0: "FALSE"}.get(s.mode().iloc[0], "")
                                     if not s.dropna().empty else "")})
           .reset_index())
tableau["Date du tarif le plus bas"] = (t.loc[idx_min.values].set_index(["Trajet", "Compagnie"])["date"]
                                        .reindex(pd.MultiIndex.from_frame(tableau[["Trajet", "Compagnie"]]))
                                        .dt.strftime("%d/%m/%Y").values)
moins_cher = tableau.groupby("Destination")["Tarif médian"].transform("min")
tableau["Écart avec la moins chère (médian)"] = tableau["Tarif médian"] - moins_cher
tableau = tableau.sort_values(["Destination", "Tarif médian"])
st.dataframe(tableau, hide_index=True, width="stretch", column_config={
    c: st.column_config.NumberColumn(format="%.0f €") for c in
    ("Tarif le plus bas", "Tarif médian", "Tarif le plus haut", "Écart avec la moins chère (médian)")})
st.caption("Une ligne par trajet (aéroport de départ → destination) et par compagnie, de la moins chère à la plus "
           "chère pour chaque destination. Écart : tarif médian de la compagnie moins celui de la compagnie la moins "
           "chère sur cette destination (0 € = la moins chère).")
