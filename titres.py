"""Titres de page : centrés, dans un cadre aux coins arrondis (comme les boutons de la barre latérale), une
couleur par thème."""
import html
import json
from pathlib import Path

import streamlit as st

# Couleur de chaque thème : (claire au centre, foncée sur les bords)
COULEURS = {
    "donnee": ("#5fd3b4", "#0f6b55"),      # Charger la donnée : vert d'eau
    "catalogue": ("#7fb6f0", "#174a8b"),   # Catalogue : bleu ciel → bleu
    "tables": ("#d6b98a", "#6b4a16"),      # Historique tables rafraîchies : sépia (archives)
    "recherche": ("#b79af0", "#4b2a8a"),   # Recherche détaillée : violet
    "pays": ("#7fdcf0", "#0b5f7a"),        # Pays → pays : bleu turquoise (le monde)
    "comparatif": ("#ffc163", "#b85c00"),  # Comparatif Airfox vs Google Flights : orangé
    "rapport": ("#7ccf9f", "#185c37"),     # Rapport Excel : vert Excel
    "dataviz": ("#f5a3d9", "#8e1f6b"),     # Data Visualisation : magenta
    "assistant": ("#f19aa8", "#8c1d32"),   # Assistant décisionnel : rouge bordeaux
    "drive": ("#9fb3c8", "#2c3e55"),       # Envoyer sur Drive : gris ardoise
}


# Fond de la page principale, une teinte très pâle de la couleur du thème (pages sans fond : blanc)
FONDS = {"donnee": "#ecfaf6", "catalogue": "#f0f6fd", "tables": "#faf6ef", "recherche": "#f6f3fd", "pays": "#f0fbfd",
         "comparatif": "#fff8ec", "dataviz": "#fef4fa", "rapport": "#eff9f3", "assistant": "#fdf3f5",
         "drive": "#f3f5f8"}


def aplat(couleur: str, part: float = 0.28) -> str:
    """Teinte pâle d'une couleur (mélange avec du blanc) : fond des sections, plus soutenu que celui de la page."""
    r, g, b = (int(couleur[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(255 - (255 - c) * part) for c in (r, g, b))


def titre_page(texte: str, theme: str):
    """Grand titre centré, dans un cadre de la même forme que les boutons de la barre latérale : rectangle aux coins
    arrondis bordé de la couleur foncée du thème, fond dégradé du thème."""
    clair, fonce = COULEURS[theme]
    # Titre remonté vers le haut de la page (Streamlit laisse ~6 rem de marge au-dessus du contenu)
    # Contour des champs (listes, saisies, dates, nombres, zones de texte) et des tableaux dans la couleur foncée du
    # thème, comme le cadre du titre : ils se distinguent du fond de la page
    champs = ", ".join(f"[data-testid='stMain'] {c}" for c in (
        "[data-testid='stSelectbox'] [role='group']", "[data-testid='stMultiSelect'] [role='group']",
        "[data-testid='stDateInputField']", "[data-testid='stNumberInputContainer']",
        "[data-testid='stTextInputRootElement']", "[data-testid='stTextAreaRootElement']",
        "[data-testid='stDataFrameResizable']",  # tableaux (consultation et modifiables)
        "[data-testid='stFileUploaderDropzone']"))  # zone de dépôt de fichier (« Charger la donnée »)
    st.markdown("<style>.block-container, [data-testid='stMainBlockContainer'] {padding-top: 3.2rem;}"
                + (f"[data-testid='stMain'], [data-testid='stHeader'] {{background: {FONDS[theme]};}}"
                   if theme in FONDS else "")
                + f"{champs} {{border: 2px solid {fonce} !important; background-color: #fff !important;}}"
                # zones de saisie à l'intérieur des champs : transparentes, pour laisser voir le fond blanc
                + ", ".join(f"[data-testid='stMain'] {c} {e}" for c in (
                    "[data-testid='stDateInputField']", "[data-testid='stNumberInputContainer']",
                    "[data-testid='stTextInputRootElement']", "[data-testid='stTextAreaRootElement']")
                            for e in ("input", "textarea", "div"))
                + " {background-color: transparent !important;}"
                # sections encadrées (suivi, fichier chargé…), sections repliables et formulaires : sans contour, un
                # aplat de la couleur du thème un peu plus soutenu que le fond de la page
                + f"[data-testid='stMain'] [class*='st-key-cadre_'], [data-testid='stMain'] [data-testid='stExpander'] details,"
                  f" [data-testid='stMain'] [data-testid='stForm'] {{border: none !important;"
                  f" background: {aplat(clair)} !important; border-radius: .6rem;}}"
                # barre de progression : partie remplie dans la couleur foncée du thème
                + f"[data-testid='stProgressBarTrack'] > div {{background-color: {fonce} !important;}}"
                # graphiques : même contour, tracé autour (sans réduire la place du graphique)
                + f"[data-testid='stMain'] [data-testid='stVegaLiteChart'] {{outline: 2px solid {fonce};"
                  " outline-offset: 6px; border-radius: .5rem; margin: 8px 0; background: #fff;"
                  " box-shadow: 0 0 0 6px #fff;}"
                # barre latérale : barre de défilement toujours visible, couleur du cadre du titre ; bouton « << »
                # (replier la barre) toujours affiché, un peu plus grand, de la même couleur
                + f"[data-testid='stSidebarContent'] {{overflow-y: scroll !important; scrollbar-width: auto !important;"
                  f" scrollbar-color: {fonce} #efe8d6 !important;}}"
                  "[data-testid='stSidebarCollapseButton'] {visibility: visible !important; opacity: 1 !important;}"
                  "[data-testid='stSidebarCollapseButton'] button {transform: scale(1.35); transform-origin: center;}"
                + f"[data-testid='stSidebarCollapseButton'] button, [data-testid='stSidebarCollapseButton'] span"
                  f" {{color: {fonce} !important;}}"
                # boutons en aplats, sans contour, tous dans la couleur foncée du thème (texte blanc, au lieu du rouge
                # et du blanc de Streamlit) ; étiquettes des choix multiples assorties.
                # :where() : priorité minimale, les boutons habillés à part (bouton_theme) gardent leur style
                + f":where([data-testid='stMain'] button[data-testid^='stBaseButton-secondary']),"
                  f" :where([data-testid='stMain'] button[data-testid^='stBaseButton-primary'])"
                  f" {{background: {fonce} !important; border: none !important; color: #fff !important;}}"
                  ":where([data-testid='stMain'] button[data-testid^='stBaseButton-secondary']):hover:not(:disabled),"
                  " :where([data-testid='stMain'] button[data-testid^='stBaseButton-primary']):hover:not(:disabled)"
                  " {filter: brightness(1.15);}"
                  ":where([data-testid='stMain'] button[data-testid^='stBaseButton-']):disabled {opacity: .5;}"
                  f"[data-testid='stMain'] [data-tag] {{background-color: {fonce} !important;}}"
                + "</style>", unsafe_allow_html=True)
    st.markdown(f"""
<div style="display:flex; justify-content:center; margin:0 0 2.4rem 0;">
  <div style="width:min(100%, 680px); padding:1.15rem 2rem; box-sizing:border-box; text-align:center;
       border:3px solid {fonce}; border-radius:1rem; background:radial-gradient(ellipse at center, {clair} 0%,
       {fonce} 135%); box-shadow:0 3px 6px rgba(0,0,0,.22); color:#fff; font-weight:700; line-height:1.15;
       font-size:clamp(1.1rem, 2.6vw, 1.9rem); text-shadow:0 2px 3px rgba(0,0,0,.45);">
    {html.escape(texte)}
  </div>
</div>""", unsafe_allow_html=True)


def intitule(texte: str):
    """Petit intitulé en gras (même style que les étiquettes des champs, voir etiquettes_moyennes)."""
    st.markdown(f"<p style='font-size:1.02rem; font-weight:600; margin:0;'>{html.escape(texte)}</p>",
                unsafe_allow_html=True)


def etiquettes_moyennes():
    """Étiquettes des champs de la page en écriture moyenne, juste au-dessus de leur champ."""
    st.markdown("<style>[data-testid='stWidgetLabel'] p {font-size:1.02rem !important; font-weight:600;}</style>",
                unsafe_allow_html=True)


def bouton_theme(cle: str, theme: str):
    """Habille le bouton de clé `cle` aux couleurs du titre de la page (dégradé du thème, bordure foncée comme le titre)."""
    clair, fonce = COULEURS[theme]
    st.markdown(f"""<style>
.st-key-{cle} button {{background: radial-gradient(circle, {clair} 0%, {fonce} 100%) !important;
    color: #fff !important; border: 2px solid {fonce} !important; font-weight: 700;
    text-shadow: 0 1px 2px rgba(0,0,0,.4);}}
.st-key-{cle} button:hover {{color: #fff !important; border-color: {fonce} !important; filter: brightness(1.08);}}
.st-key-{cle} button p {{font-size: 1.02rem;}}
</style>""", unsafe_allow_html=True)


def liste_theme(cle: str, theme: str):
    """Habille la liste déroulante de clé `cle` aux couleurs du titre de la page."""
    clair, fonce = COULEURS[theme]
    st.markdown(f"""<style>
.st-key-{cle} [role="group"] {{background: radial-gradient(circle, {clair} 0%, {fonce} 100%) !important;
    border: 2px solid {fonce}; border-radius: .5rem;}}
.st-key-{cle} [role="group"] input {{color: #fff; -webkit-text-fill-color: #fff; font-weight: 700;
    background: transparent;}}
.st-key-{cle} [role="group"] svg {{color: #fff; fill: #fff;}}
</style>""", unsafe_allow_html=True)


# --- Couleurs des graphiques (Data Visualisation et rapport Excel), réglables dans Data Visualisation -------------
FICHIER_COULEURS = Path(__file__).with_name("couleurs_graphiques.json")
COULEURS_GRAPHIQUES = {"Airfox": "#2a78d6", "Google": "#111111", "Airfox plus cher": "#d03b3b",
                       "Airfox moins cher": "#2e9d5b", "Même prix": "#8a8984"}


def couleurs_graphiques() -> dict:
    """Couleurs choisies (couleurs_graphiques.json), complétées par les couleurs par défaut."""
    try:
        choisies = json.loads(FICHIER_COULEURS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        choisies = {}
    return COULEURS_GRAPHIQUES | {k: v for k, v in choisies.items() if k in COULEURS_GRAPHIQUES}


def enregistrer_couleurs(couleurs: dict):
    FICHIER_COULEURS.write_text(json.dumps(couleurs, ensure_ascii=False, indent=1), encoding="utf-8")
