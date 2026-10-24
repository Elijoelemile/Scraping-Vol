"""Page « Comparatif Airfox vs Google Flights » : donnée Airfox (page « Charger la donnée »), fichier Google
Flights (recherches actualisées du Catalogue) et leurs jointures."""
import pandas as pd
import streamlit as st

import catalogue
import donnees
from titres import titre_page

titre_page("Comparatif Airfox vs Google Flights", "comparatif")
# La donnée Airfox vient de la page « Charger la donnée » ; sans elle, la page reste vide
gardee = donnees.donnee_gardee()
if gardee is None:
    st.info("Aucune donnée chargée : chargez le fichier Airfox dans la page « Charger la donnée ».")
    st.stop()


def filtrer(tab: pd.DataFrame, cle: str) -> pd.DataFrame:
    """Filtres d'une section (ville d'arrivée, dates de départ, compagnie, bagage) ; renvoie les lignes retenues."""
    f1, f2, f3, f4 = st.columns([3, 3, 3, 2])
    if "VilleArrivee" in tab:
        villes = f1.multiselect("Ville d'arrivée", sorted(tab["VilleArrivee"].dropna().astype(str).unique()),
                                placeholder="Toutes", key=f"{cle}_villes")
        if villes:
            tab = tab[tab["VilleArrivee"].astype(str).isin(villes)]
    if "DateAller" in tab:
        jours = pd.to_datetime(tab["DateAller"], format="%d/%m/%Y", errors="coerce")
        periode = f2.date_input("Date de départ", value=(), format="DD/MM/YYYY", key=f"{cle}_dates",
                                help="Une date, ou une date de début puis de fin")
        if len(periode) == 2:
            tab = tab[(jours >= pd.Timestamp(periode[0])) & (jours <= pd.Timestamp(periode[1]))]
        elif len(periode) == 1:
            tab = tab[jours == pd.Timestamp(periode[0])]
    if "CodeTransporteur" in tab:
        cies = f3.multiselect("Compagnie", sorted(tab["CodeTransporteur"].dropna().astype(str).unique()),
                              placeholder="Toutes", key=f"{cle}_cies")
        if cies:
            tab = tab[tab["CodeTransporteur"].astype(str).isin(cies)]
    bagages = [c for c in tab.columns if c.startswith("FlagBagageInclus")]
    for col in bagages:
        choix = f4.selectbox(col.replace("FlagBagageInclus", "Bagage"), ["Tous", "TRUE", "FALSE"],
                             key=f"{cle}_{col}")
        if choix != "Tous":
            tab = tab[tab[col].astype(str) == choix]
    return tab




df, nom_fichier, _ = gardee
df = df.copy()

# Tables complètes gardées pour la page « Rapport Excel »
st.session_state["rapport_airfox"] = df.copy()
st.session_state["rapport_fichier"] = nom_fichier
st.session_state["rapport_google"] = None
st.session_state["rapport_jointure"] = None
st.session_state["rapport_jointure_moins_chere"] = None

# Affichage comme dans Excel : dates « 22/10/2026 », vrai/faux en « TRUE » / « FALSE » (et non en cases à cocher)
for col in df.columns:
    if pd.api.types.is_datetime64_any_dtype(df[col]):
        df[col] = df[col].dt.strftime("%d/%m/%Y")
    elif pd.api.types.is_bool_dtype(df[col]):
        df[col] = df[col].map({True: "TRUE", False: "FALSE"})

c1, c2, c3 = st.columns(3)
c1.metric("Lignes", f"{len(df):,}".replace(",", " "))
c2.metric("Colonnes", len(df.columns))
c3.metric("Fichier", nom_fichier)
vue_airfox = filtrer(df, "airfox")
st.caption(f"{len(vue_airfox):,} ligne(s) affichée(s)".replace(",", " "))
TARIF_EUROS = {"Tarif": st.column_config.NumberColumn(format="%.2f €")}  # symbole de l'euro après les tarifs
st.dataframe(vue_airfox, hide_index=True, width="stretch", lazy=False, column_config=TARIF_EUROS)
avertissement = st.empty()  # rempli plus bas, selon le type de trajet choisi pour Google Flights

# --- Fichier Google Flights (données du catalogue), au même format pour la jointure ----------------------
st.header("Fichier Google Flights")
# Durée du séjour des tarifs Airfox (colonne « Duree », en nuits) : les tarifs Google doivent être comparables
nuits_airfox = (int(pd.to_numeric(df["Duree"], errors="coerce").mode().iloc[0])
                if "Duree" in df.columns and pd.to_numeric(df["Duree"], errors="coerce").notna().any() else None)
# Type de trajet : celui choisi dans la page Catalogue (tableau « Toutes les dates ») ; None = aller simple.
# Catalogue pas encore ouvert dans cette session : celui de la donnée Airfox (ex. aller-retour de 7 nuits)
nuits = st.session_state.get("nuits_catalogue", nuits_airfox) or None  # 0 = aller simple choisi au Catalogue
st.session_state["nuits_comparatif"] = nuits  # repris par Data Visualisation (type de trajet des jointures)
aller_retour = nuits is not None
if nuits_airfox and nuits != nuits_airfox:
    avertissement.warning(f"⚠️ Les tarifs Airfox sont des allers-retours de **{nuits_airfox} nuits** (colonne Duree). "
                          f"Pour comparer, choisissez « Aller-retour » et **{nuits_airfox} nuits** dans la page "
                          "Catalogue (tableau « Toutes les dates ») : le fichier Google Flights ci-dessous reprend ce choix.")
if "VilleArrivee" not in df.columns:
    st.warning("Colonne « VilleArrivee » introuvable dans le fichier Airfox : impossible de choisir les destinations.")
    st.stop()
# Fichier Google Flights : les recherches actualisées dans le Catalogue (boutons « Compléter » / « Actualiser »)
google = catalogue.tableau_google_airfox(sorted(df["VilleArrivee"].dropna().astype(str).unique()),
                                        int(nuits) if aller_retour else None, seulement_actualise=True)
st.session_state["rapport_google"] = google.copy()
if google.empty:
    st.info(f"Aucun tarif Google Flights aller-retour de {nuits} nuits pour les destinations de ce fichier. "
            "Pour en obtenir : page Catalogue, tableau « Toutes les dates », choisissez « Aller-retour » et "
            f"{nuits} nuits, puis « Actualiser en direct »." if aller_retour
            else "Aucune donnée Google Flights pour les destinations de ce fichier.")
    st.stop()
d1, d2, d3 = st.columns(3)
d1.metric("Lignes", f"{len(google):,}".replace(",", " "))
d2.metric("Compagnies", google["CodeTransporteur"].nunique())
d3.metric("Destinations", google["VilleArrivee"].nunique())
vue = google.assign(DateAller=pd.to_datetime(google["DateAller"]).dt.strftime("%d/%m/%Y"),
                    **({"DateRetour": pd.to_datetime(google["DateRetour"]).dt.strftime("%d/%m/%Y")}
                       if "DateRetour" in google else {}),
                    FlagBagageInclus=google["FlagBagageInclus"].map({True: "TRUE", False: "FALSE"}).fillna(""),
                    CollecteLe=pd.to_datetime(google["CollecteLe"]).dt.strftime("%d/%m/%Y %H:%M"))
vue_google = filtrer(vue, "google")
st.caption(f"{len(vue_google):,} ligne(s) affichée(s)".replace(",", " "))
st.dataframe(vue_google, hide_index=True, width="stretch", lazy=False, column_config=TARIF_EUROS)

# --- Jointures Airfox × Google Flights (sur les deux fichiers tels qu'affichés ci-dessus, avec leurs filtres) ----
ECART = "Écart (Airfox − Google)"


def couleur_ecart(v):
    """Cellule rouge si l'écart est positif (Airfox plus cher), verte s'il est négatif (Airfox moins cher) ;
    écriture noire."""
    if pd.isna(v) or v == 0:
        return ""
    return f"background-color: {'#ffcdd2' if v > 0 else '#c8e6c9'}; color: #000000; font-weight: 600"


MEME_BAGAGE = "Même bagage"


def meme_bagage(jointure: pd.DataFrame) -> pd.Series:
    """TRUE si Airfox et Google ont la même valeur de bagage ; vide si l'un des deux est inconnu."""
    a = jointure["FlagBagageInclus Airfox"].astype(str).str.upper()
    g = jointure["FlagBagageInclus Google"].astype(str).str.upper()
    connus = a.isin(["TRUE", "FALSE"]) & g.isin(["TRUE", "FALSE"])
    return (a == g).map({True: "TRUE", False: "FALSE"}).where(connus, "")


def couleur_bagage(v):
    """Même bagage : cellule verte si TRUE, rouge si FALSE."""
    return {"TRUE": "background-color: #c8e6c9; color: #000000; font-weight: 600",
            "FALSE": "background-color: #ffcdd2; color: #000000; font-weight: 600"}.get(v, "")


def style_meme_compagnie(v):
    """Toutes les cases en gris ; la coche (même compagnie) en noir."""
    return "background-color: #e0e0e0; color: #000000; font-weight: 700; text-align: center"


def afficher_jointure(jointure: pd.DataFrame, nb_airfox: int):
    jointure[ECART] = (pd.to_numeric(jointure["Tarif Airfox"], errors="coerce")
                       - pd.to_numeric(jointure["Tarif Google"], errors="coerce")).round(2)
    jointure.insert(jointure.columns.get_loc("FlagBagageInclus Google") + 1, MEME_BAGAGE, meme_bagage(jointure))
    j1, j2, j3 = st.columns(3)
    j1.metric("Lignes jointes", f"{len(jointure):,}".replace(",", " "))
    j2.metric("Lignes Airfox sans équivalent Google", f"{nb_airfox - len(jointure):,}".replace(",", " "))
    j3.metric("Écart médian", f"{jointure[ECART].median():.0f} €" if len(jointure) else "—")
    tarifs = [c for c in jointure.columns if c.startswith("Tarif")] + [ECART]
    pd.set_option("styler.render.max_elements", max(262144, jointure.size + 1))
    style = jointure.style.map(couleur_ecart, subset=[ECART]).map(couleur_bagage, subset=[MEME_BAGAGE])
    if "MêmeCompagnie" in jointure:
        style = style.map(style_meme_compagnie, subset=["MêmeCompagnie"])
    st.dataframe(style.format("{:.2f} €", subset=tarifs, na_rep="—"), hide_index=True, width="stretch", lazy=False)


cles_trajet = ["VilleDepart", "VilleArrivee", "DateAller"] + (["Duree"] if aller_retour else [])
manquantes = [c for c in cles_trajet + ["CodeTransporteur"] if c not in df.columns]
if manquantes:
    st.warning(f"Colonnes absentes du fichier Airfox : {', '.join(manquantes)} — jointure impossible.")
    st.stop()
airfox = vue_airfox.assign(**{c: vue_airfox[c].astype(str) for c in ("VilleDepart", "VilleArrivee", "CodeTransporteur")})
precisions = ("Les colonnes présentes dans les deux fichiers sont suffixées « Airfox » ou « Google » (Tarif, "
              "FlagBagageInclus…) ; « Écart » = tarif Airfox − tarif Google (rouge : Airfox plus cher, vert : Airfox "
              "moins cher). Les filtres des deux fichiers ci-dessus s'appliquent aussi à la jointure.")

st.subheader("Jointure sur la compagnie")
cles = cles_trajet + ["CodeTransporteur"]
st.caption(f"Une ligne quand Airfox et Google Flights ont la même valeur dans les colonnes **{', '.join(cles)}** "
           "(même départ, même ville d'arrivée, même date de départ, même compagnie"
           + (", même durée de séjour" if aller_retour else "") + "). " + precisions)
afficher_jointure(airfox.merge(vue_google, on=cles, how="inner", suffixes=(" Airfox", " Google")), len(airfox))
# Rapport Excel : la même jointure sur les fichiers complets (sans les filtres de la page)
complet = df.assign(**{c: df[c].astype(str) for c in ("VilleDepart", "VilleArrivee", "CodeTransporteur")}).merge(
    vue, on=cles, how="inner", suffixes=(" Airfox", " Google"))
complet[ECART] = (pd.to_numeric(complet["Tarif Airfox"], errors="coerce")
                  - pd.to_numeric(complet["Tarif Google"], errors="coerce")).round(2)
complet.insert(complet.columns.get_loc("FlagBagageInclus Google") + 1, MEME_BAGAGE, meme_bagage(complet))
st.session_state["rapport_jointure"] = complet

st.subheader("Jointure sur la compagnie la moins chère")
def jointure_moins_chere(fichier_airfox: pd.DataFrame, fichier_google: pd.DataFrame) -> pd.DataFrame:
    """Chaque ligne Airfox face à la compagnie la moins chère chez Google le même jour, quelle qu'elle soit."""
    tarifs = fichier_google.assign(_t=pd.to_numeric(fichier_google["Tarif"], errors="coerce")).dropna(subset=["_t"])
    moins_cher = (tarifs.sort_values("_t").drop_duplicates(cles_trajet).drop(columns="_t")
                  .rename(columns={"CodeTransporteur": "CompagnieMoinsChere"}))
    j = fichier_airfox.merge(moins_cher, on=cles_trajet, how="inner", suffixes=(" Airfox", " Google"))
    # Case grise pour toutes les lignes, coche noire quand c'est la même compagnie (et non une case à cocher Streamlit)
    j.insert(j.columns.get_loc("CompagnieMoinsChere") + 1, "MêmeCompagnie",
             (j["CodeTransporteur"] == j["CompagnieMoinsChere"]).map({True: "✔", False: ""}))
    return j


jointure_mc = jointure_moins_chere(airfox, vue_google)
st.caption(f"Une ligne quand Airfox et Google Flights ont la même valeur dans les colonnes **{', '.join(cles_trajet)}** "
           "(même départ, même ville d'arrivée, même date de départ" + (", même durée de séjour" if aller_retour else "")
           + ") : le tarif Airfox (sa compagnie, CodeTransporteur) est comparé au tarif de la **compagnie la moins chère "
           "chez Google** ce jour-là (CompagnieMoinsChere), quelle qu'elle soit. MêmeCompagnie indique si c'est la même. "
           "Côté Google, seules les compagnies présentes dans le fichier Google Flights ci-dessus sont prises en compte. "
           + precisions)
afficher_jointure(jointure_mc, len(airfox))
# Rapport Excel (feuille 3) : la même jointure sur les fichiers complets, sans les filtres de la page
complet_mc = jointure_moins_chere(df.assign(**{c: df[c].astype(str) for c in ("VilleDepart", "VilleArrivee",
                                                                             "CodeTransporteur")}), vue)
complet_mc[ECART] = (pd.to_numeric(complet_mc["Tarif Airfox"], errors="coerce")
                     - pd.to_numeric(complet_mc["Tarif Google"], errors="coerce")).round(2)
complet_mc.insert(complet_mc.columns.get_loc("FlagBagageInclus Google") + 1, MEME_BAGAGE, meme_bagage(complet_mc))
st.session_state["rapport_jointure_moins_chere"] = complet_mc
