"""Comparateur de prix de vols (données Google Flights, stockage SQLite).

Lancer :  .venv\\Scripts\\streamlit run app.py
"""
import asyncio

import streamlit as st

import session_google

st.set_page_config(page_title="Comparateur de vols", page_icon="✈️", layout="wide")

page = st.navigation([
    st.Page("pages_app/page_catalogue.py", title="Catalogue (1 an)", icon="📅", default=True),
    st.Page("pages_app/page_recherche.py", title="Recherche détaillée", icon="🔎"),
    st.Page("pages_app/page_pays.py", title="Pays → pays", icon="🌍"),
])

# --- Compte Google (commun aux deux pages) : prix réservés aux comptes connectés ---
with st.sidebar:
    with st.expander("👤 Compte Google", expanded=not session_google.est_connecte()):
        if session_google.est_connecte():
            st.caption(f"Connecté : {session_google.compte() or 'compte Google'}")
            st.session_state["utiliser_compte"] = st.toggle(
                "Utiliser ce compte pour les recherches", value=True,
                help="Obtient aussi les prix que Google ne montre qu'aux comptes connectés.")
            if st.button("Se déconnecter", width="stretch"):
                session_google.deconnecter()
                st.rerun()
        else:
            st.session_state["utiliser_compte"] = False
            st.caption("Non connecté : certains vols restent « Non communiqué ». "
                       "Utilisez de préférence un compte secondaire.")
            if st.button("Se connecter à Google", width="stretch"):
                with st.spinner("Une fenêtre Chrome s'ouvre : connectez-vous, puis FERMEZ-la (10 min max)…"):
                    ok = asyncio.run(session_google.connecter(log=lambda m: None))
                if not ok:
                    st.warning("Aucune connexion trouvée. Connectez-vous dans la fenêtre Chrome, puis fermez-la.")
                else:
                    st.rerun()

page.run()
