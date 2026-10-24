"""Page « Pays → pays » : tous les vols d'un pays vers un autre à une date (une seule recherche Google)."""
import asyncio
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import catalogue
import recap
from google_flights_scraper import tableau_comparateur, vers_dataframe
from titres import titre_page

LIBELLES_CLASSES = {"eco": "Économique", "premium": "Premium éco", "affaires": "Affaires", "premiere": "Première"}
ESCALES = {"Toutes": None, "Direct": 0, "≤ 1 escale": 1, "≤ 2 escales": 2}


def avec_prix(df, colonne, vide):
    """Affiche les prix en « 123 € » et les prix absents avec un texte, en gardant le tri numérique."""
    return df.style.format(lambda v: f"{v:.0f} €", subset=[colonne], na_rep=vide)


titre_page("Vols de pays à pays", "pays")
st.caption("Tapez un pays de départ et un pays d'arrivée : tous les vols entre les deux, tous aéroports confondus "
           "(jusqu'à environ 300 vols, les plus pertinents selon Google), en une seule recherche.")

demain = date.today() + timedelta(days=1)
with st.form("recherche_pays"):
    c1, c2 = st.columns(2)
    texte_depart = c1.text_input("Pays de départ", "France", placeholder="ex. France, Belgique, Sénégal…")
    texte_arrivee = c2.text_input("Pays d'arrivée", "Thaïlande", placeholder="ex. Thaïlande, Maroc, Cap-Vert…")
    c3, c4, c5, c6, c7 = st.columns([2, 2, 2, 1, 2])
    aller_retour = c3.radio("Type de trajet", ["Aller simple", "Aller-retour"], horizontal=True) == "Aller-retour"
    d_aller = c4.date_input("Date de départ", demain + timedelta(days=30), min_value=demain, format="DD/MM/YYYY")
    d_retour = c5.date_input("Date de retour (aller-retour)", demain + timedelta(days=37), min_value=demain,
                             format="DD/MM/YYYY")
    adultes = c6.number_input("Adultes", 1, 9, 1)
    classe = {v: k for k, v in LIBELLES_CLASSES.items()}[c7.selectbox("Classe", list(LIBELLES_CLASSES.values()))]
    lancer = st.form_submit_button("Rechercher", type="primary")

if lancer:
    if aller_retour and d_retour <= d_aller:
        st.error("La date de retour doit être après la date de départ.")
        st.stop()
    try:
        with st.spinner("Recherche des pays…"):
            async def resoudre():
                return await asyncio.gather(catalogue.resoudre_pays(texte_depart), catalogue.resoudre_pays(texte_arrivee))
            pays_dep, pays_arr = asyncio.run(resoudre())
        introuvables = [t for t, p in ((texte_depart, pays_dep), (texte_arrivee, pays_arr)) if not p]
        if introuvables:
            st.error("Pays introuvable : " + ", ".join(f"« {t} »" for t in introuvables)
                     + ". Vérifiez l'orthographe (ex. « Cap-Vert », « République dominicaine »).")
            st.stop()
        with st.spinner(f"Recherche des vols {pays_dep['nom']} → {pays_arr['nom']} sur Google Flights…"):
            vols = asyncio.run(catalogue.vols_pays(pays_dep, pays_arr, d_aller.isoformat(),
                                                   d_retour.isoformat() if aller_retour else None, adultes, classe))
    except catalogue.Bloque:
        st.error("Google bloque temporairement les recherches depuis votre connexion (captcha). "
                 "Attendez quelques heures sans lancer de recherche, puis réessayez.")
        st.stop()
    except Exception as e:  # erreur réseau ou autre : message lisible plutôt qu'un plantage
        st.error(f"La recherche a échoué ({type(e).__name__}). Réessayez dans quelques minutes.")
        st.stop()
    if not vols:
        st.warning(f"Aucun vol trouvé de {pays_dep['nom']} vers {pays_arr['nom']} à cette date.")
        st.stop()
    st.session_state["pays_df"] = vers_dataframe(vols, complet=True)
    st.session_state["pays_libelle"] = (f"{pays_dep['nom']} → {pays_arr['nom']} {d_aller:%d/%m}"
                                        + (f"↔{d_retour:%d/%m}" if aller_retour else ""))
    st.session_state["pays_ar"] = aller_retour
    st.session_state["recap_nouveau_pays"] = True

df = st.session_state.get("pays_df")
if df is None:
    st.stop()

st.caption(f"{st.session_state['pays_libelle']} : {len(df)} vols trouvés, de {df['ville_depart'].nunique()} ville(s) "
           f"de départ vers {df['ville_arrivee'].nunique()} ville(s) d'arrivée.")

# Filtres
f1, f2, f3 = st.columns([3, 3, 1.4])
choix_dep = f1.multiselect("Villes de départ", sorted(df["ville_depart"].dropna().unique()), placeholder="Toutes")
choix_arr = f2.multiselect("Villes d'arrivée", sorted(df["ville_arrivee"].dropna().unique()), placeholder="Toutes")
escales_max = ESCALES[f3.selectbox("Escales", list(ESCALES))]
vue = df
if choix_dep:
    vue = vue[vue["ville_depart"].isin(choix_dep)]
if choix_arr:
    vue = vue[vue["ville_arrivee"].isin(choix_arr)]
if escales_max is not None:
    vue = vue[vue["escales"] <= escales_max]
if vue.empty:
    st.warning("Aucun vol ne correspond aux filtres.")
    st.stop()

if st.session_state.get("pays_ar"):
    st.caption("Aller-retour : horaires, durée et compagnie du vol aller ; prix total aller-retour.")
tableau = tableau_comparateur(vue).sort_values("Prix (EUR)")
st.dataframe(avec_prix(tableau, "Prix (EUR)", "Prix non disponible"), hide_index=True, width="stretch",
             column_config={"Logo": st.column_config.ImageColumn("Logo", width="small")})
st.download_button("Télécharger en CSV", tableau.to_csv(index=False, sep=";").encode("utf-8-sig"),
                   "vols_pays_a_pays.csv", "text/csv")

with st.expander("Meilleur prix par trajet (ville de départ → ville d'arrivée)", expanded=False):
    d = vue.assign(prix=pd.to_numeric(vue["prix_eur"], errors="coerce"))
    par_trajet = (d.groupby(["ville_depart", "ville_arrivee"])
                  .agg(vols=("prix", "size"), prix_min=("prix", "min"))
                  .reset_index().sort_values(["prix_min", "vols"], ascending=[True, False], na_position="last")
                  .rename(columns={"ville_depart": "Départ", "ville_arrivee": "Arrivée", "vols": "Vols",
                                   "prix_min": "Prix le plus bas"}))
    st.dataframe(par_trajet, hide_index=True, width="stretch",
                 column_config={"Prix le plus bas": st.column_config.NumberColumn(format="%d €")})

st.divider()
recap.afficher(vue, "historique_pays", nouvelle=st.session_state.pop("recap_nouveau_pays", False),
               trajet=st.session_state["pays_libelle"], source="appel direct (recherche pays → pays)")
