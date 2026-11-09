"""Recherches planifiées du Catalogue : programme fixe (sans IA).

Une planification garde une table du Catalogue (ses (destination, date), aéroports de départ, nuits et champs de la
page) et le moment où la rechercher : une seule fois, tous les jours ou certains jours de la semaine, à une heure
donnée ; « Compléter » (seulement les dates pas encore recherchées) ou « Tout réactualiser ». Elles sont gardées dans
planifications.json, autant qu'on veut.

verifier() lance la planification due si aucune actualisation ne tourne ; sinon elle attend son tour : plusieurs
planifications dues partent l'une après l'autre. L'application l'appelle chaque minute tant qu'elle est ouverte
(surveiller()) ; une heure passée application fermée est rattrapée dès sa réouverture (une seule fois).
"""
import json
import secrets
import threading
import time
from datetime import date, datetime, time as heure, timedelta
from pathlib import Path

import catalogue

FICHIER = Path(__file__).with_name("planifications.json")
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
REPETITIONS = ["une seule fois", "tous les jours", "certains jours"]
MODES = {"completer": "Compléter", "tout": "Tout réactualiser"}
INTERVALLE_S = 60  # vérification des planifications chaque minute


def lire() -> list[dict]:
    try:
        return json.loads(FICHIER.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return []


def _ecrire(plans: list[dict]):
    tmp = FICHIER.with_suffix(".tmp")
    tmp.write_text(json.dumps(plans, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FICHIER)


def _modifier(ident: str, **champs):
    """Met à jour une planification (relue juste avant : la page et la vérification écrivent dans le même fichier)."""
    plans = lire()
    for p in plans:
        if p["id"] == ident:
            p.update(champs)
    _ecrire(plans)


def prochaine(plan: dict, apres: datetime) -> datetime | None:
    """Prochain lancement après `apres` (None : une seule fois, et déjà passée)."""
    h = heure.fromisoformat(plan["heure"])
    if plan["repetition"] == "une seule fois":
        moment = datetime.combine(date.fromisoformat(plan["date"]), h)
        return moment if moment > apres else None
    debut = max(date.fromisoformat(plan["date"]), apres.date())
    for k in range(8):
        jour = debut + timedelta(days=k)
        moment = datetime.combine(jour, h)
        if moment > apres and (plan["repetition"] == "tous les jours" or jour.weekday() in plan["jours"]):
            return moment
    return None


def quand(plan: dict) -> str:
    """« le 03/11/2026 à 06:00 », « tous les jours à 06:00 », « lundi, jeudi à 06:00 »."""
    if plan["repetition"] == "une seule fois":
        return f"le {date.fromisoformat(plan['date']):%d/%m/%Y} à {plan['heure']}"
    jours = "tous les jours" if plan["repetition"] == "tous les jours" else ", ".join(JOURS[j] for j in plan["jours"])
    return f"{jours} à {plan['heure']}"


def ajouter(nom: str, repetition: str, jour: date, h: heure, jours: list[int], mode: str,
            paires: list[tuple[str, str]], origines: list[str], nuits: int, choix: dict, libelle: str) -> str | None:
    """Enregistre une planification. Renvoie le problème, ou None si c'est fait."""
    if not nom:
        return "Donnez un nom à la planification."
    if not paires:
        return "La table choisie n'a aucune date à rechercher."
    if repetition == "certains jours" and not jours:
        return "Choisissez au moins un jour de la semaine."
    plan = {"id": secrets.token_hex(4), "nom": nom, "repetition": repetition, "date": jour.isoformat(),
            "heure": h.strftime("%H:%M"), "jours": sorted(jours) if repetition == "certains jours" else [],
            "mode": mode, "paires": [list(p) for p in paires], "origines": origines, "nuits": nuits,
            "choix": choix, "libelle": libelle, "active": True, "derniere": None, "statut": "en attente de l'heure",
            "creee_le": datetime.now().isoformat(timespec="seconds")}
    suivante = prochaine(plan, datetime.now())
    if not suivante:
        return "Cette date et cette heure sont déjà passées : choisissez un moment à venir."
    plan["prochaine"] = suivante.isoformat(timespec="minutes")
    _ecrire(lire() + [plan])
    return None


def activer(ident: str, active: bool) -> str | None:
    """Active (prochain lancement recalculé à partir de maintenant) ou met en pause une planification."""
    plan = next((p for p in lire() if p["id"] == ident), None)
    if not plan:
        return None
    if not active:
        _modifier(ident, active=False, statut="en pause")
        return None
    suivante = prochaine(plan, datetime.now())
    if not suivante:
        return "Sa date est passée : créez une nouvelle planification."
    _modifier(ident, active=True, prochaine=suivante.isoformat(timespec="minutes"), statut="en attente de l'heure")
    return None


def supprimer(ident: str):
    _ecrire([p for p in lire() if p["id"] != ident])


def paires_a_lancer(plan: dict) -> list[tuple[str, str]]:
    """(destination, date) à rechercher maintenant : les dates à venir de la table ; avec « Compléter », seulement
    celles pas encore recherchées (comme le bouton du Catalogue)."""
    aujourd_hui = date.today().isoformat()
    paires = [tuple(p) for p in plan["paires"] if p[1] >= aujourd_hui]
    if plan["mode"] == "completer" and paires:
        faites = catalogue.vols_jour_tableau(sorted(set(plan["origines"]) | set(catalogue.PARIS_AEROPORTS)),
                                             plan["nuits"], catalogue=True)
        deja = set(zip(faites["destination"], faites["date"]))
        paires = [p for p in paires if p not in deja]
    return paires


def verifier() -> str | None:
    """Lance la planification due la plus ancienne, si aucune actualisation ne tourne. Renvoie ce qui a été fait."""
    maintenant = datetime.now()
    dues = sorted((p for p in lire() if p.get("active") and p.get("prochaine")
                   and datetime.fromisoformat(p["prochaine"]) <= maintenant), key=lambda p: p["prochaine"])
    if not dues:
        return None
    if catalogue.actualisation_active():
        for p in dues:
            _modifier(p["id"], statut="en attente : une actualisation tourne déjà, elle partira juste après")
        return None
    plan = dues[0]
    paires = paires_a_lancer(plan)
    if paires:
        catalogue.memoriser_derniere_table([tuple(p) for p in plan["paires"]], plan["nuits"], plan["choix"])
        catalogue.lancer_actualisation(paires, plan["origines"], plan["nuits"],
                                       f"{plan['libelle']} — planification « {plan['nom']} »")
        statut = f"lancée le {maintenant:%d/%m} à {maintenant:%H:%M} ({len(paires)} recherches)"
    else:
        statut = (f"rien à rechercher le {maintenant:%d/%m} à {maintenant:%H:%M} (tout est déjà fait, ou dates "
                  "passées)")
    suivante = prochaine(plan, maintenant)
    _modifier(plan["id"], derniere=maintenant.isoformat(timespec="minutes"), statut=statut,
              prochaine=suivante.isoformat(timespec="minutes") if suivante else None, active=bool(suivante))
    return f"{plan['nom']} : {statut}"


def surveiller():
    """Vérifie les planifications chaque minute, dans l'application ouverte (un seul fil de vérification, même
    quand Streamlit recharge ce module)."""
    if any(f.name == "planifications" for f in threading.enumerate()):
        return

    def boucle():
        while True:
            try:
                if fait := verifier():
                    print(f"{datetime.now():%d/%m %H:%M:%S} planification {fait}", flush=True)
            except Exception as e:  # une erreur n'arrête pas les vérifications suivantes
                print(f"{datetime.now():%d/%m %H:%M:%S} planification : erreur {type(e).__name__} : {e}", flush=True)
            time.sleep(INTERVALLE_S)

    threading.Thread(target=boucle, daemon=True, name="planifications").start()
