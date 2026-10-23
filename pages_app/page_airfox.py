"""Page « Comparatif Airfox vs Google Flights » : chargement et affichage du fichier Airfox (étape 1)."""
import pandas as pd
import streamlit as st

import catalogue

st.title("Comparatif Airfox vs Google Flights")
st.caption("Chargez le fichier Airfox (CSV ou Excel) pour l'afficher.")

fichier = st.file_uploader("Fichier Airfox", type=["csv", "xlsx", "xls"],
                           help="Ex. AirfoxCache.csv ou AirfoxCache.csv.xlsx")
if fichier is None and "donnee_airfox" not in st.session_state:
    st.info("Aucun fichier chargé (ici ou dans la page « Charger la donnée »).")
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


@st.cache_data(show_spinner="Lecture du fichier…")
def lire(contenu: bytes, nom: str) -> pd.DataFrame:
    import io
    if nom.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(contenu))
    # CSV : séparateur (« ; » ou « , ») et encodage détectés
    for encodage in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(contenu), sep=None, engine="python", encoding=encodage)
        except UnicodeDecodeError:
            continue
    raise ValueError("encodage du fichier non reconnu")


try:
    if fichier is None:  # donnée chargée dans la page « Charger la donnée »
        df = st.session_state["donnee_airfox"].copy()
        fichier = type("Fichier", (), {"name": st.session_state.get("donnee_fichier", "donnée chargée")})()
    else:
        df = lire(fichier.getvalue(), fichier.name)
except Exception as e:
    st.error(f"Impossible de lire le fichier ({type(e).__name__} : {e}).")
    st.stop()

# Tables complètes gardées pour la page « Rapport Excel »
st.session_state["rapport_airfox"] = df.copy()
st.session_state["rapport_fichier"] = fichier.name
st.session_state["rapport_google"] = None
st.session_state["rapport_jointure"] = None

# Affichage comme dans Excel : dates « 22/10/2026 », vrai/faux en « TRUE » / « FALSE » (et non en cases à cocher)
for col in df.columns:
    if pd.api.types.is_datetime64_any_dtype(df[col]):
        df[col] = df[col].dt.strftime("%d/%m/%Y")
    elif pd.api.types.is_bool_dtype(df[col]):
        df[col] = df[col].map({True: "TRUE", False: "FALSE"})

c1, c2, c3 = st.columns(3)
c1.metric("Lignes", f"{len(df):,}".replace(",", " "))
c2.metric("Colonnes", len(df.columns))
c3.metric("Fichier", fichier.name)
vue_airfox = filtrer(df, "airfox")
st.caption(f"{len(vue_airfox):,} ligne(s) affichée(s)".replace(",", " "))
st.dataframe(vue_airfox, hide_index=True, width="stretch", lazy=False)
avertissement = st.empty()  # rempli plus bas, selon le type de trajet choisi pour Google Flights

# --- Fichier Google Flights (données du catalogue), au même format pour la jointure ----------------------
st.header("Fichier Google Flights")
# Durée du séjour des tarifs Airfox (colonne « Duree », en nuits) : les tarifs Google doivent être comparables
nuits_airfox = (int(pd.to_numeric(df["Duree"], errors="coerce").mode().iloc[0])
                if "Duree" in df.columns and pd.to_numeric(df["Duree"], errors="coerce").notna().any() else None)
# Type de trajet : celui choisi dans la page Catalogue (tableau « Toutes les dates ») ; None = aller simple
nuits = st.session_state.get("nuits_catalogue")
aller_retour = nuits is not None
if nuits_airfox and nuits != nuits_airfox:
    avertissement.warning(f"⚠️ Les tarifs Airfox sont des allers-retours de **{nuits_airfox} nuits** (colonne Duree). "
                          f"Pour comparer, choisissez « Aller-retour » et **{nuits_airfox} nuits** dans la page "
                          "Catalogue (tableau « Toutes les dates ») : le fichier Google Flights ci-dessous reprend ce choix.")
if "VilleArrivee" not in df.columns:
    st.warning("Colonne « VilleArrivee » introuvable dans le fichier Airfox : impossible de choisir les destinations.")
    st.stop()
google = catalogue.tableau_google_airfox(sorted(df["VilleArrivee"].dropna().astype(str).unique()),
                                        int(nuits) if aller_retour else None)
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
st.dataframe(vue_google, hide_index=True, width="stretch", lazy=False)

# --- Jointures Airfox × Google Flights (sur les deux fichiers tels qu'affichés ci-dessus, avec leurs filtres) ----
ECART = "Écart (Airfox − Google)"


def couleur_ecart(v):
    """Écart négatif (Airfox moins cher) en rouge, positif (Google moins cher) en vert."""
    if pd.isna(v) or v == 0:
        return ""
    return f"color: {'#d62728' if v < 0 else '#2ca02c'}; font-weight: 600"


def afficher_jointure(jointure: pd.DataFrame, nb_airfox: int):
    jointure[ECART] = (pd.to_numeric(jointure["Tarif Airfox"], errors="coerce")
                       - pd.to_numeric(jointure["Tarif Google"], errors="coerce")).round(2)
    j1, j2, j3 = st.columns(3)
    j1.metric("Lignes jointes", f"{len(jointure):,}".replace(",", " "))
    j2.metric("Lignes Airfox sans équivalent Google", f"{nb_airfox - len(jointure):,}".replace(",", " "))
    j3.metric("Écart médian", f"{jointure[ECART].median():.0f} €" if len(jointure) else "—")
    tarifs = [c for c in jointure.columns if c.startswith("Tarif")] + [ECART]
    pd.set_option("styler.render.max_elements", max(262144, jointure.size + 1))
    st.dataframe(jointure.style.map(couleur_ecart, subset=[ECART]).format("{:.2f}", subset=tarifs, na_rep="—"),
                 hide_index=True, width="stretch", lazy=False)


cles_trajet = ["VilleDepart", "VilleArrivee", "DateAller"] + (["Duree"] if aller_retour else [])
manquantes = [c for c in cles_trajet + ["CodeTransporteur"] if c not in df.columns]
if manquantes:
    st.warning(f"Colonnes absentes du fichier Airfox : {', '.join(manquantes)} — jointure impossible.")
    st.stop()
airfox = vue_airfox.assign(**{c: vue_airfox[c].astype(str) for c in ("VilleDepart", "VilleArrivee", "CodeTransporteur")})
precisions = ("Les colonnes présentes dans les deux fichiers sont suffixées « Airfox » ou « Google » (Tarif, "
              "FlagBagageInclus…) ; « Écart » = tarif Airfox − tarif Google (vert : Google moins cher, rouge : Airfox "
              "moins cher). Les filtres des deux fichiers ci-dessus s'appliquent aussi à la jointure.")

st.subheader("Jointure sur la même compagnie")
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
st.session_state["rapport_jointure"] = complet

st.subheader("Jointure sur la compagnie la moins chère")
# Côté Google : pour chaque trajet et date, la compagnie au tarif le plus bas, quelle qu'elle soit
google_tarif = vue_google.assign(_t=pd.to_numeric(vue_google["Tarif"], errors="coerce")).dropna(subset=["_t"])
moins_cher = (google_tarif.sort_values("_t").drop_duplicates(cles_trajet).drop(columns="_t")
              .rename(columns={"CodeTransporteur": "CompagnieMoinsChere"}))
jointure_mc = airfox.merge(moins_cher, on=cles_trajet, how="inner", suffixes=(" Airfox", " Google"))
jointure_mc.insert(jointure_mc.columns.get_loc("CompagnieMoinsChere") + 1, "MêmeCompagnie",
                   jointure_mc["CodeTransporteur"] == jointure_mc["CompagnieMoinsChere"])
st.caption(f"Une ligne quand Airfox et Google Flights ont la même valeur dans les colonnes **{', '.join(cles_trajet)}** "
           "(même départ, même ville d'arrivée, même date de départ" + (", même durée de séjour" if aller_retour else "")
           + ") : le tarif Airfox (sa compagnie, CodeTransporteur) est comparé au tarif de la **compagnie la moins chère "
           "chez Google** ce jour-là (CompagnieMoinsChere), quelle qu'elle soit. MêmeCompagnie indique si c'est la même. "
           "Côté Google, seules les compagnies présentes dans le fichier Google Flights ci-dessus sont prises en compte. "
           + precisions)
afficher_jointure(jointure_mc, len(airfox))
