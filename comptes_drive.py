"""Comptes de la page « Envoyer sur Drive » : chaque personne est autonome.

- Le détenteur ne partage qu'un code d'accès (variable d'environnement CODE_ENVOI_DRIVE), à redonner quand il met à
  jour l'application. Avec ce code, chacun crée lui-même son compte : identifiant, mot de passe et adresse e-mail de
  récupération (obligatoire).
- Chacun modifie ensuite son mot de passe (et son adresse) lui-même ; en cas d'oubli, un e-mail est envoyé à son
  adresse de récupération, avec un bouton « Mot de passe de récupération ». Le lien du bouton est toujours le même pour
  une personne (calculé à partir de son identifiant et de son adresse) : en cas de nouvel oubli, il suffit de rouvrir
  le premier e-mail. Il ne change (et les anciens e-mails ne marchent plus) que si l'adresse de récupération change.

Rien n'est gardé en clair : les mots de passe ne sont écrits que sous forme d'empreinte (PBKDF2-SHA256 salé), dans
comptes_drive.json (propre à ce PC : ni dans git, ni dans le zip), avec la clé secrète qui signe les liens.
Envoi des e-mails : mêmes variables que les alertes (ALERTE_SMTP_UTILISATEUR, ALERTE_SMTP_MOT_DE_PASSE, et au besoin
ALERTE_SMTP_SERVEUR / ALERTE_SMTP_PORT).
"""
import hashlib
import html as html_lib
import hmac
import json
import os
import re
import secrets
import smtplib
from email.message import EmailMessage
from pathlib import Path

FICHIER = Path(__file__).with_name("comptes_drive.json")
VARIABLE_CODE = "CODE_ENVOI_DRIVE"
LONGUEUR_MIN = 8
ITERATIONS = 200_000
PARAMETRE_LIEN = "recuperation"  # ?recuperation=… dans l'adresse de la page : lien du bouton de l'e-mail


def variable(nom: str) -> str:
    """Variable d'environnement ; à défaut, celle enregistrée par « setx » dans le compte Windows (lue même si
    l'application a été lancée avant la commande)."""
    valeur = os.environ.get(nom, "")
    if not valeur and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as cle:
                valeur = str(winreg.QueryValueEx(cle, nom)[0])
        except OSError:
            valeur = ""
    return valeur


def _lire() -> dict:
    try:
        donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        donnees = {}
    return {"comptes": donnees.get("comptes", {}), "cle": donnees.get("cle") or secrets.token_hex(32)}


def _ecrire(donnees: dict):
    tmp = FICHIER.with_suffix(".tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FICHIER)


def _empreinte(secret: str, sel: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(sel), ITERATIONS).hex()


def _proteger(secret: str) -> dict:
    sel = secrets.token_hex(16)
    return {"sel": sel, "empreinte": _empreinte(secret, sel)}


def _correspond(secret: str, entree: dict) -> bool:
    return hmac.compare_digest(_empreinte(secret, entree["sel"]), entree["empreinte"])


def ouvert() -> bool:
    """La page peut-elle être utilisée ? (des comptes existent, ou un code d'accès permet d'en créer)"""
    return bool(_lire()["comptes"]) or bool(variable(VARIABLE_CODE))


def email_valide(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email))


def probleme_mot_de_passe(nouveau: str, confirmation: str) -> str | None:
    if len(nouveau) < LONGUEUR_MIN:
        return f"Le mot de passe doit faire au moins {LONGUEUR_MIN} caractères."
    if nouveau != confirmation:
        return "Les deux saisies du nouveau mot de passe sont différentes."
    return None


def creer(identifiant: str, email: str, mot_de_passe: str, confirmation: str, code_acces: str) -> str | None:
    """Crée son propre compte avec le code d'accès partagé. Renvoie le problème, ou None si c'est fait."""
    code = variable(VARIABLE_CODE)
    if not code or not hmac.compare_digest(code_acces.encode(), code.encode()):
        return "Code d'accès faux."
    if not identifiant:
        return "Choisissez un identifiant."
    if not email_valide(email):
        return "Adresse e-mail de récupération invalide."
    if probleme := probleme_mot_de_passe(mot_de_passe, confirmation):
        return probleme
    donnees = _lire()
    if identifiant in donnees["comptes"]:
        return "Cet identifiant est déjà pris."
    donnees["comptes"][identifiant] = {**_proteger(mot_de_passe), "email": email}
    _ecrire(donnees)
    return None


def verifier(identifiant: str, mot_de_passe: str) -> bool:
    compte = _lire()["comptes"].get(identifiant)
    return bool(compte) and _correspond(mot_de_passe, compte)


def email_de(identifiant: str) -> str:
    return _lire()["comptes"].get(identifiant, {}).get("email", "")


def changer(identifiant: str, nouveau: str):
    donnees = _lire()
    donnees["comptes"][identifiant].update(_proteger(nouveau))
    _ecrire(donnees)


def changer_email(identifiant: str, email: str):
    donnees = _lire()
    donnees["comptes"][identifiant]["email"] = email
    _ecrire(donnees)


def masquer(email: str) -> str:
    """ma***@gmail.com : l'adresse n'est pas révélée à qui ne connaît que l'identifiant."""
    nom, _, domaine = email.partition("@")
    return f"{nom[:2]}***@{domaine}"


def _envoyer_email(destinataire: str, sujet: str, texte: str, html: str | None = None):
    utilisateur, mot_de_passe = variable("ALERTE_SMTP_UTILISATEUR"), variable("ALERTE_SMTP_MOT_DE_PASSE")
    if not utilisateur or not mot_de_passe:
        raise RuntimeError("l'envoi d'e-mails n'est pas configuré sur cet ordinateur")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = utilisateur, destinataire, sujet
    message.set_content(texte)
    if html:
        message.add_alternative(html, subtype="html")
    with smtplib.SMTP(variable("ALERTE_SMTP_SERVEUR") or "smtp.gmail.com",
                      int(variable("ALERTE_SMTP_PORT") or "587"), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(utilisateur, mot_de_passe)
        smtp.send_message(message)


def jeton(identifiant: str) -> str:
    """Jeton du lien de récupération : toujours le même tant que l'adresse de récupération ne change pas."""
    donnees = _lire()
    if "cle" not in json.loads(FICHIER.read_text(encoding="utf-8")):
        _ecrire(donnees)  # clé secrète créée une fois pour toutes
    email = donnees["comptes"][identifiant]["email"]
    return hmac.new(bytes.fromhex(donnees["cle"]), f"{identifiant}\n{email}".encode(), hashlib.sha256).hexdigest()


def identifiant_du_jeton(valeur: str) -> str | None:
    """Personne à qui appartient le lien de récupération, ou None s'il n'est pas (ou plus) valable."""
    for identifiant in _lire()["comptes"]:
        if hmac.compare_digest(jeton(identifiant), valeur):
            return identifiant
    return None


def envoyer_lien(identifiant: str, adresse_page: str) -> str | None:
    """Mot de passe oublié : envoie l'e-mail au bouton « Mot de passe de récupération ». Renvoie l'adresse masquée, ou
    None si l'identifiant n'existe pas. Lève RuntimeError si l'e-mail ne peut pas partir."""
    email = email_de(identifiant)
    if not email:
        return None
    lien = f"{adresse_page}?{PARAMETRE_LIEN}={jeton(identifiant)}"
    nom = html_lib.escape(identifiant)
    _envoyer_email(
        email, "Scraping Vol : récupération de votre mot de passe",
        f"Bonjour {identifiant},\n\nPour choisir un nouveau mot de passe (page « Envoyer sur Drive »), ouvrez ce "
        f"lien :\n{lien}\n\nGardez cet e-mail : si vous oubliez de nouveau votre mot de passe, ce même lien "
        "fonctionnera encore (tant que votre adresse de récupération ne change pas). Si vous n'avez rien demandé, "
        "ignorez ce message : votre mot de passe actuel reste valable.",
        f"""<div style="font-family:Arial,sans-serif;font-size:15px;color:#222;max-width:520px">
<p>Bonjour {nom},</p>
<p>Pour choisir un nouveau mot de passe (page « Envoyer sur Drive »), cliquez sur le bouton :</p>
<p style="margin:28px 0"><a href="{html_lib.escape(lien)}" style="background:#2c3e55;color:#fff;padding:12px 22px;
border-radius:999px;text-decoration:none;font-weight:bold;display:inline-block">Mot de passe de récupération</a></p>
<p><b>Gardez cet e-mail</b> : si vous oubliez de nouveau votre mot de passe, ce même bouton fonctionnera encore
(tant que votre adresse de récupération ne change pas).</p>
<p style="color:#666;font-size:13px">Si vous n'avez rien demandé, ignorez ce message : votre mot de passe actuel
reste valable. L'application doit être lancée sur l'ordinateur pour que le bouton s'ouvre.</p></div>""")
    return masquer(email)


def reinitialiser_par_lien(valeur: str, nouveau: str, confirmation: str) -> str | None:
    """Nouveau mot de passe depuis le bouton de l'e-mail. Renvoie le problème, ou None si c'est fait."""
    identifiant = identifiant_du_jeton(valeur)
    if not identifiant:
        return "Ce lien de récupération n'est plus valable : demandez un nouvel e-mail."
    if probleme := probleme_mot_de_passe(nouveau, confirmation):
        return probleme
    changer(identifiant, nouveau)
    return None
