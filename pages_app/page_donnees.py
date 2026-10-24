"""Page « Charger la donnée » : fichier Airfox de référence ; le catalogue est remplacé par ses trajets."""
from datetime import datetime

import pandas as pd
import streamlit as st

import donnees
from titres import titre_page

titre_page("Charger la donnée", "donnee")
st.caption("Chargez le fichier Airfox : ses destinations deviennent celles du catalogue.")

CLES_RAPPORT = ("rapport_airfox", "rapport_google", "rapport_jointure", "rapport_jointure_moins_chere", "rapport_1a")

# La donnée chargée est gardée (sur le disque) : elle reste là, même en changeant de page ou en relançant
# l'application, jusqu'à ce qu'on clique sur la croix du fichier.
gardee = donnees.donnee_gardee()
st.markdown("Fichier Airfox")
if gardee is None:
    nouveau = st.file_uploader("Fichier Airfox", type=["csv", "xlsx", "xls"], label_visibility="collapsed")
    if nouveau is not None:
        try:
            donnees.lire_fichier(nouveau.getvalue(), nouveau.name)
        except Exception as e:
            st.error(f"Impossible de lire le fichier ({type(e).__name__} : {e}).")
            st.stop()
        donnees.garder(nouveau.getvalue(), nouveau.name)
        st.rerun()
else:
    # le fichier gardé, comme le champ de chargement l'afficherait, avec sa croix pour le retirer
    with st.container(border=True):
        c_nom, c_croix = st.columns([12, 1], vertical_alignment="center")
        c_nom.markdown(f"📄 **{gardee[1]}** · {gardee[2] / 1e6:.1f} MB")
        if c_croix.button("✖", key="retirer_donnee", help="Retirer la donnée (les pages Comparatif, Data "
                                                         "Visualisation et Rapport Excel se vident)"):
            donnees.retirer()
            for cle in CLES_RAPPORT + ("donnee_airfox", "donnee_fichier"):
                st.session_state.pop(cle, None)
            st.rerun()
if gardee is not None:
    df, nom_fichier, _ = gardee
    # gardée aussi en session pour les pages Comparatif et Rapport Excel
    st.session_state["donnee_airfox"], st.session_state["donnee_fichier"] = df, nom_fichier

    vue = df.copy()
    for col in vue.columns:  # affichage comme dans Excel
        if pd.api.types.is_datetime64_any_dtype(vue[col]):
            vue[col] = vue[col].dt.strftime("%d/%m/%Y")
        elif pd.api.types.is_bool_dtype(vue[col]):
            vue[col] = vue[col].map({True: "TRUE", False: "FALSE"})
    c1, c2, c3 = st.columns(3)
    c1.metric("Lignes", f"{len(df):,}".replace(",", " "))
    c2.metric("Colonnes", len(df.columns))
    c3.metric("Fichier", nom_fichier)
    st.dataframe(vue, hide_index=True, width="stretch", lazy=False)

    if not {"VilleDepart", "VilleArrivee"} <= set(df.columns):
        st.warning("Colonnes VilleDepart et VilleArrivee introuvables : impossible de mettre à jour le catalogue.")
    else:
        departs, arrivees = donnees.trajets_de(df)
        bilan = donnees.comparer(departs, arrivees)
        st.subheader("Mise à jour du catalogue")
        st.markdown(
            f"- Départs : **{', '.join(departs)}** · destinations de la donnée : **{len(arrivees)}**\n"
            f"- Trajets du catalogue gardés : **{bilan['trajets_gardes']}** · supprimés (hors donnée, avec leurs "
            f"prix) : **{bilan['trajets_supprimes']}**\n"
            f"- Destinations retirées : **{len(bilan['destinations_retirees'])}** · nouvelles destinations "
            f"(à collecter) : **{len(bilan['destinations_ajoutees'])}**"
            + (f" ({', '.join(bilan['destinations_ajoutees'])})" if bilan["destinations_ajoutees"] else ""))
        st.caption("Avant le remplacement, la liste des destinations du catalogue actuel est archivée en PDF "
                   "(voir « Historique catalogue » ci-dessous). Les trajets supprimés et leurs prix ne sont pas "
                   "récupérables.")
        if st.button("Remplacer le catalogue par les destinations de cette donnée", type="primary",
                     disabled=not (bilan["trajets_supprimes"] or bilan["destinations_ajoutees"])):
            with st.spinner("Archivage et mise à jour du catalogue…"):
                fait = donnees.remplacer_catalogue(departs, arrivees)
            st.success(f"Catalogue mis à jour : {fait['trajets_supprimes']} trajets supprimés, "
                       f"{len(fait['destinations_ajoutees'])} nouvelles destinations à collecter. "
                       f"Ancien catalogue archivé : {fait['archive']}.")

# --- Historique du catalogue (PDF archivés avant chaque remplacement) ------------------------------------------
st.subheader("Historique catalogue")
archives = donnees.historiques()
if not archives:
    st.info("Aucun historique pour l'instant : il est créé au premier remplacement du catalogue.")
else:
    choix = st.selectbox("Date", archives, format_func=lambda p: datetime.strptime(
        p.stem.removeprefix("catalogue_"), "%Y-%m-%d_%H-%M-%S").strftime("%d/%m/%Y à %H:%M:%S"))
    st.download_button("Historique catalogue", choix.read_bytes(), file_name=choix.name, mime="application/pdf")
