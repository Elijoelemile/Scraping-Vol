"""Alerte routines : programme fixe (sans IA).

Selon la fréquence choisie dans la page « Assistant Décisionnel », vérifie le pourcentage des routines effectuées
aujourd'hui ; s'il n'est pas à 100 %, envoie un e-mail à l'adresse enregistrée avec la liste des routines non faites.

À lancer régulièrement (toutes les 15 minutes) par le Planificateur de tâches Windows : planifier_alerte.bat.
    python alerte.py            vérifie et envoie si c'est l'heure
    python alerte.py --test     envoie tout de suite, sans tenir compte de la fréquence

Envoi des e-mails : variables d'environnement ALERTE_SMTP_UTILISATEUR et ALERTE_SMTP_MOT_DE_PASSE (pour Gmail : un
« mot de passe d'application »), et au besoin ALERTE_SMTP_SERVEUR / ALERTE_SMTP_PORT (par défaut smtp.gmail.com:587).
"""
import json
import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # certificats de Windows (antivirus, réseau d'entreprise)

RACINE = Path(__file__).parent
FICHIER_ROUTINES = RACINE / "routines.json"
FICHIER_ALERTE = RACINE / "alerte_routine.json"
JOURNAL = RACINE / "alerte.log"

# Fréquence -> intervalle minimal entre deux alertes, ou moment fixe (heure, jour de semaine, jour du mois)
INTERVALLES = {"Toutes les 15 minutes": 15, "Toutes les 30 minutes": 30, "Toutes les heures": 60,
               "Toutes les 2 heures": 120, "Toutes les 3 heures": 180, "Toutes les 6 heures": 360,
               "Toutes les 12 heures": 720}
QUOTIDIENNES = {"Chaque jour à 7 h": 7, "Chaque jour à 12 h": 12, "Chaque jour à 18 h": 18}


def lire(fichier: Path, defaut):
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defaut


def avancement(routines: list[dict], jour: date | None = None) -> tuple[int, list[dict]]:
    """Pourcentage des routines effectuées ce jour-là, et routines non faites."""
    jour = (jour or date.today()).isoformat()
    non_faites = [r for r in routines if (r.get("effectuee_le") or "")[:10] != jour]
    pourcentage = round(100 * (len(routines) - len(non_faites)) / len(routines)) if routines else 100
    return pourcentage, non_faites


def c_est_l_heure(frequence: str, derniere: datetime | None, maintenant: datetime) -> bool:
    """L'alerte doit-elle partir maintenant, d'après la fréquence et la dernière alerte envoyée ?"""
    if frequence == "Désactivée":
        return False
    if frequence in INTERVALLES:
        return derniere is None or maintenant - derniere >= timedelta(minutes=INTERVALLES[frequence])
    if frequence in QUOTIDIENNES:
        moment = maintenant.replace(hour=QUOTIDIENNES[frequence], minute=0, second=0, microsecond=0)
    elif frequence == "Chaque lundi à 7 h":
        moment = (maintenant - timedelta(days=maintenant.weekday())).replace(hour=7, minute=0, second=0, microsecond=0)
    elif frequence == "Chaque semaine (même jour)":
        return derniere is None or maintenant - derniere >= timedelta(days=7)
    elif frequence == "Le 1er de chaque mois":
        moment = maintenant.replace(day=1, hour=7, minute=0, second=0, microsecond=0)
    else:
        return False
    # moment fixe : une seule alerte par période, une fois le moment passé
    return maintenant >= moment and (derniere is None or derniere < moment)


def envoyer(destinataire: str, sujet: str, texte: str):
    utilisateur, mot_de_passe = os.environ.get("ALERTE_SMTP_UTILISATEUR"), os.environ.get("ALERTE_SMTP_MOT_DE_PASSE")
    if not (utilisateur and mot_de_passe):
        raise RuntimeError("compte d'envoi non configuré (ALERTE_SMTP_UTILISATEUR / ALERTE_SMTP_MOT_DE_PASSE)")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = utilisateur, destinataire, sujet
    message.set_content(texte)
    serveur = os.environ.get("ALERTE_SMTP_SERVEUR", "smtp.gmail.com")
    with smtplib.SMTP(serveur, int(os.environ.get("ALERTE_SMTP_PORT", "587")), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(utilisateur, mot_de_passe)
        smtp.send_message(message)


def noter(texte: str):
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%d/%m/%Y %H:%M:%S} {texte}\n")


def verifier(forcer: bool = False) -> str:
    """Vérifie les routines et envoie l'alerte si besoin. Renvoie ce qui a été fait (aussi écrit dans alerte.log)."""
    reglages, routines = lire(FICHIER_ALERTE, {}), lire(FICHIER_ROUTINES, [])
    maintenant = datetime.now()
    derniere = datetime.fromisoformat(reglages["derniere_alerte"]) if reglages.get("derniere_alerte") else None
    if not forcer and not c_est_l_heure(reglages.get("frequence", "Chaque jour à 7 h"), derniere, maintenant):
        return "pas encore l'heure"
    pourcentage, non_faites = avancement(routines)
    if not routines or pourcentage == 100:
        resultat = "rien à signaler (100 % des routines effectuées)" if routines else "aucune routine enregistrée"
    elif not reglages.get("email"):
        resultat = f"{pourcentage} % : pas d'adresse e-mail enregistrée, alerte non envoyée"
    else:
        sujet = f"Alerte routines : {pourcentage} % effectuées aujourd'hui"
        texte = (f"Le {maintenant:%d/%m/%Y à %H:%M}, {pourcentage} % des routines ont été effectuées "
                 f"({len(routines) - len(non_faites)}/{len(routines)}).\n\nRoutines non faites :\n"
                 + "".join(f"- {r['routine']}\n" for r in non_faites)
                 + "\nMessage envoyé automatiquement par l'application Scraping Vol.")
        try:
            envoyer(reglages["email"], sujet, texte)
            resultat = f"{pourcentage} % : alerte envoyée à {reglages['email']}"
        except Exception as e:
            noter(f"ÉCHEC de l'envoi : {e}")
            return f"échec de l'envoi : {e}"
    reglages["derniere_alerte"] = maintenant.isoformat(timespec="seconds")
    FICHIER_ALERTE.write_text(json.dumps(reglages, ensure_ascii=False), encoding="utf-8")
    noter(resultat)
    return resultat


if __name__ == "__main__":
    print(verifier(forcer="--test" in sys.argv))
