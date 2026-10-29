"""Comparateur de prix de vols (données Google Flights, stockage SQLite).

Lancer :  .venv\\Scripts\\streamlit run app.py
"""
import asyncio

import streamlit as st

import session_google
import titres

st.set_page_config(page_title="Comparateur de vols", page_icon="✈️", layout="wide")

# Pages de la barre latérale, dans l'ordre, avec le thème (couleur) de leur titre
PAGES = [
    ("pages_app/page_donnees.py", "Charger la donnée", "📂", "donnee"),
    ("pages_app/page_catalogue.py", "Catalogue aller-retour", "📅", "catalogue"),
    ("pages_app/page_tables.py", "Historique tables rafraîchies", "🗂️", "tables"),
    ("pages_app/page_recherche.py", "Recherche détaillée", "🔎", "recherche"),
    ("pages_app/page_pays.py", "Pays → pays", "🌍", "pays"),
    ("pages_app/page_airfox.py", "Comparatif Airfox vs Google Flights", "⚖️", "comparatif"),
    ("pages_app/page_dataviz.py", "Data Visualisation", "📈", "dataviz"),
    ("pages_app/page_rapport.py", "Rapport Excel", "📊", "rapport"),
    ("pages_app/page_assistant.py", "Assistant Décisionnel", "🤖", "assistant"),
    ("pages_app/page_drive.py", "Envoyer sur Drive", "📤", "drive"),
]
page = st.navigation([st.Page(f, title=t, icon=i, default=f.endswith("page_catalogue.py")) for f, t, i, _ in PAGES])
# Boutons de la barre latérale en forme de pilule, aux couleurs du titre de leur page : dégradé vertical (reflet
# clair en haut, plus foncé en bas), cadre foncé, écriture noire normale
st.markdown("<style>[data-testid='stSidebar'] {background: #faf6ec;}"  # fond « Floral White »
            "[data-testid='stSidebarNav'] a span {color: #000 !important;}"
            "[data-testid='stSidebarNav'] a p {font-weight: 400;}" + "".join(
    f"[data-testid='stSidebarNav'] li:nth-child({n}) a {{"
    f"background: linear-gradient(180deg, color-mix(in srgb, {clair} 45%, white) 0%, {clair} 50%,"
    f" color-mix(in srgb, {clair} 80%, {fonce}) 100%);"
    f" border: 2px solid {fonce}; border-radius: 999px; margin-bottom: .35rem;"
    f" box-shadow: inset 0 2px 0 rgba(255,255,255,.55);}}"
    f"[data-testid='stSidebarNav'] li:nth-child({n}) a:hover {{filter: brightness(1.07);}}"
    for n, (_, _, _, theme) in enumerate(PAGES, start=1) for clair, fonce in [titres.COULEURS[theme]])
    # Page en cours, « en gras » comme un bouton mis en avant : plus haute, épais cadre noir et ombre noire décalée
    # (effet relief), écriture en gras légèrement espacée, icône agrandie ; elle garde la largeur de la barre
    # latérale (elle suit son élargissement)
    + "[data-testid='stSidebarNav'] a {transition: padding .15s ease, box-shadow .15s ease;}"
    "[data-testid='stSidebarNav'] a[aria-current='page'] {padding-top: .6rem; padding-bottom: .6rem;"
    " position: relative; z-index: 2; border: 3px solid #111 !important;"
    " box-shadow: inset 0 3px 0 rgba(255,255,255,.6), -3px 5px 0 #111 !important; margin: .5rem 0 .9rem 3px;}"
    "[data-testid='stSidebarNav'] a[aria-current='page'] p {font-weight: 800; font-size: .95rem; line-height: 1.2;"
    " white-space: normal;}"
    # nom trop long pour une ligne (Montserrat est large) : sur deux lignes plutôt que coupé par « … »
    "[data-testid='stSidebarNav'] a[aria-current='page'] span {overflow: visible; text-overflow: clip;"
    " white-space: normal;}"
    "[data-testid='stSidebarNav'] a[aria-current='page'] > span:first-child {width: auto; margin-right: .6rem;}"
    "[data-testid='stSidebarNav'] a[aria-current='page'] [data-testid='stIconEmoji'] {font-size: 1.4rem;"
    " line-height: 1; filter: drop-shadow(0 2px 2px rgba(0,0,0,.35));}"
    "</style>", unsafe_allow_html=True)


def navigateur_chrome() -> bool:
    """Le visiteur utilise-t-il Google Chrome ? (Edge et Opera s'annoncent aussi « Chrome » : on les exclut.)"""
    ua = st.context.headers.get("User-Agent", "")
    return "Chrome/" in ua and not any(x in ua for x in ("Edg/", "OPR/", "SamsungBrowser/"))


# La connexion ouvre Chrome sur la machine qui fait tourner l'application : possible seulement en local
connexion_possible = session_google.CHROME.exists()

# --- Compte Google (commun aux pages), affiché uniquement aux visiteurs sous Google Chrome ---
st.session_state["utiliser_compte"] = False
if navigateur_chrome():
    with st.sidebar:
        with st.expander("👤 Compte Google", expanded=connexion_possible and not session_google.est_connecte()):
            if not connexion_possible:
                st.caption("La connexion à un compte Google n'est disponible que dans la version de l'application "
                           "installée sur votre ordinateur (elle ouvre Google Chrome sur la machine qui l'exécute).")
                st.button("Se connecter à Google", width="stretch", disabled=True)
            elif session_google.est_connecte():
                st.caption(f"Connecté : {session_google.compte() or 'compte Google'}")
                st.session_state["utiliser_compte"] = st.toggle(
                    "Utiliser ce compte pour les recherches", value=True,
                    help="Obtient aussi les prix que Google ne montre qu'aux comptes connectés.")
                if st.button("Se déconnecter", width="stretch"):
                    session_google.deconnecter()
                    st.rerun()
            else:
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
