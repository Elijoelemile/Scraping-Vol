"""Page « Assistant Décisionnel » : demande à l'IA, routines enregistrées et alerte du jour (étape 1 : interface).

Les routines sont gardées dans routines.json ; le pourcentage = routines effectuées aujourd'hui / routines enregistrées.
"""
import json
import re
from pathlib import Path

import streamlit as st

import alerte as programme_alerte  # programme fixe des alertes (alerte.py)
from titres import bouton_theme, etiquettes_moyennes, intitule, liste_theme, titre_page

FICHIER_ROUTINES = Path(__file__).parent.parent / "routines.json"
FICHIER_ALERTE = Path(__file__).parent.parent / "alerte_routine.json"  # fréquence choisie pour l'alerte
FREQUENCES = ["Toutes les 15 minutes", "Toutes les 30 minutes", "Toutes les heures", "Toutes les 2 heures",
              "Toutes les 3 heures", "Toutes les 6 heures", "Toutes les 12 heures", "Chaque jour à 7 h",
              "Chaque jour à 12 h", "Chaque jour à 18 h", "Chaque lundi à 7 h", "Chaque semaine (même jour)",
              "Le 1er de chaque mois", "Désactivée"]


def lire_routines() -> list[dict]:
    try:
        return json.loads(FICHIER_ROUTINES.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


titre_page("IA décisionnel", "assistant")

etiquettes_moyennes()
st.text_area("Que veux-tu que je fasse ?", key="demande",
             placeholder="Ex. compare les tarifs Airfox et Google Flights pour Agadir la semaine prochaine")

# Bouton aux couleurs du titre : affiche ou masque les routines enregistrées
bouton_theme("routines", "assistant")
if st.button("Routine enregistrée", key="routines"):
    st.session_state["voir_routines"] = not st.session_state.get("voir_routines", False)
if st.session_state.get("voir_routines"):
    enregistrees = lire_routines()
    if enregistrees:
        st.dataframe([{"Routine": r["routine"], "Créée le": r["creee_le"][:16].replace("T", " "),
                       "Effectuée le": (r.get("effectuee_le") or "—")[:16].replace("T", " ")} for r in enregistrees],
                     hide_index=True, width="stretch")
        # Choix d'une routine, puis petit bouton « Télécharger » (fiche de la routine en CSV)
        choix = st.selectbox("Routine à télécharger", range(len(enregistrees)), placeholder="Choisir une routine",
                             index=None, format_func=lambda i: enregistrees[i]["routine"])
        fiche = "" if choix is None else (
            "routine;creee_le;effectuee_le\n"
            + ";".join(str(enregistrees[choix].get(c) or "") for c in ("routine", "creee_le", "effectuee_le")) + "\n")
        bouton_theme("telecharger_routine", "assistant")
        st.download_button("Télécharger", fiche.encode("utf-8-sig"), key="telecharger_routine",
                           file_name=f"routine_{choix + 1 if choix is not None else ''}.csv", mime="text/csv",
                           disabled=choix is None, help="Sélectionnez d'abord une routine")
    else:
        st.info("Aucune routine enregistrée pour l'instant.")

intitule("Alerte routine")
routines = lire_routines()
pourcentage, non_faites = programme_alerte.avancement(routines)  # même calcul que le programme d'alerte
st.progress(pourcentage / 100 if routines else 0.0,
            text=f"{pourcentage if routines else 0} % des routines effectuées aujourd'hui "
                 f"({len(routines) - len(non_faites)}/{len(routines)})")


# Réglages de l'alerte (adresse e-mail, fréquence), gardés dans alerte_routine.json
def lire_alerte() -> dict:
    try:
        return json.loads(FICHIER_ALERTE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def ecrire_alerte(**valeurs):
    FICHIER_ALERTE.write_text(json.dumps(lire_alerte() | valeurs, ensure_ascii=False), encoding="utf-8")


def enregistrer_email():
    email = st.session_state["email_alerte"].strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        st.toast("Adresse e-mail invalide")
        return
    ecrire_alerte(email=email)
    st.toast(f"Alertes envoyées à {email}" if email else "Adresse e-mail retirée")


def enregistrer_frequence():
    ecrire_alerte(frequence=st.session_state["frequence_alerte"])
    st.toast(f"Alerte programmée : {st.session_state['frequence_alerte'].lower()}")


alerte = lire_alerte()
st.text_input("Email", value=alerte.get("email", ""), key="email_alerte", on_change=enregistrer_email,
              placeholder="adresse@exemple.com — Entrée pour enregistrer")

# Programmation de l'alerte : liste déroulante aux couleurs du titre
frequence = alerte.get("frequence", "Chaque jour à 7 h")
liste_theme("frequence_alerte", "assistant")
st.selectbox("Programmer l'alerte", FREQUENCES, index=FREQUENCES.index(frequence) if frequence in FREQUENCES else 0,
             key="frequence_alerte", on_change=enregistrer_frequence,
             help="Si la barre n'est pas à 100 %, un e-mail est envoyé à cette fréquence (programme alerte.py, "
                  "lancé par le Planificateur de tâches Windows : planifier_alerte.bat).")
journal = (programme_alerte.JOURNAL.read_text(encoding="utf-8").strip().splitlines()
           if programme_alerte.JOURNAL.exists() else [])
st.caption(f"Dernière vérification : {journal[-1]}" if journal else "Aucune vérification pour l'instant.")
