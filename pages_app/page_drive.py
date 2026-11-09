"""Page « Envoyer sur Drive » : envoi des résultats vers le drive en ligne (SharePoint / OneDrive pro).

Accès par identifiant et mot de passe (voir comptes_drive.py) : chacun crée son compte avec le code d'accès partagé
par le détenteur, puis gère seul son mot de passe (modification, récupération par e-mail).
"""
import time

import streamlit as st

import comptes_drive as comptes
import donnees
from titres import titre_page

ESSAIS_MAX = 5        # mots de passe faux d'affilée avant une attente
ATTENTE_S = 60        # attente (secondes) après ESSAIS_MAX mots de passe faux

titre_page("Envoyer sur Drive", "drive")
if message := st.session_state.pop("drive_message", None):
    st.success(message)
# Premier usage sur ce PC : le détenteur crée le code d'accès, qu'il partagera pour que chacun crée son compte
if not comptes.code_defini():
    st.info("Aucun code d'accès n'existe encore sur cet ordinateur : créez-le. C'est le code que vous partagerez "
            "pour que chaque personne crée son propre compte.")
    with st.form("creer_code_acces"):
        code = st.text_input(f"Code d'accès ({comptes.LONGUEUR_MIN} caractères minimum)", type="password")
        confirmation = st.text_input("Confirmer le code d'accès", type="password")
        if st.form_submit_button("Créer le code d'accès", type="primary"):
            if probleme := comptes.creer_code(code, confirmation):
                st.error(probleme)
            else:
                st.session_state["drive_message"] = ("Code d'accès créé. Créez maintenant votre compte (onglet « Créer "
                                                     "mon compte ») avec ce code, puis partagez-le.")
                st.rerun()
    st.caption("Notez-le bien : il n'est gardé que sous forme d'empreinte, personne ne pourra vous le redonner.")
    st.stop()


def trop_d_essais() -> bool:
    """Après ESSAIS_MAX erreurs d'affilée (connexion, création, code de récupération) : attente de ATTENTE_S s."""
    reste = st.session_state.get("drive_bloque_jusqu", 0) - time.time()
    if reste > 0:
        st.error(f"Trop d'erreurs : réessayez dans {int(reste) + 1} s.")
    return reste > 0


def erreur(texte: str):
    essais = st.session_state.get("drive_essais", 0) + 1
    st.session_state["drive_essais"] = essais
    if essais >= ESSAIS_MAX:
        st.session_state["drive_essais"] = 0
        st.session_state["drive_bloque_jusqu"] = time.time() + ATTENTE_S
        texte += f" ({ESSAIS_MAX} erreurs de suite : attendez {ATTENTE_S} s.)"
    st.error(texte)


def connecter(identifiant: str, texte: str):
    st.session_state["drive_utilisateur"] = identifiant
    st.session_state["drive_essais"] = 0
    st.session_state["drive_message"] = texte
    st.rerun()


def adresse_page() -> str:
    """Adresse de cette page (sans paramètres), pour le bouton de l'e-mail de récupération."""
    return (st.context.url or "http://localhost:8520/page_drive").split("?")[0]


# --- Bouton « Mot de passe de récupération » de l'e-mail : nouveau mot de passe, sans être connecté ---
if lien := st.query_params.get(comptes.PARAMETRE_LIEN):
    identifiant = comptes.identifiant_du_jeton(lien)
    if not identifiant:
        st.error("Ce lien de récupération n'est plus valable (l'adresse de récupération a changé) : demandez un "
                 "nouvel e-mail dans l'onglet « Mot de passe oublié ».")
        if st.button("Continuer"):
            del st.query_params[comptes.PARAMETRE_LIEN]
            st.rerun()
        st.stop()
    st.subheader(f"Nouveau mot de passe pour « {identifiant} »")
    with st.form("reinitialiser"):
        nouveau = st.text_input(f"Nouveau mot de passe ({comptes.LONGUEUR_MIN} caractères minimum)", type="password")
        confirmer = st.text_input("Confirmer le nouveau mot de passe", type="password")
        valider_lien = st.form_submit_button("Changer le mot de passe", type="primary")
    if valider_lien:
        if probleme := comptes.reinitialiser_par_lien(lien, nouveau, confirmer):
            st.error(probleme)
        else:
            del st.query_params[comptes.PARAMETRE_LIEN]
            connecter(identifiant, "Mot de passe changé : vous êtes connecté.")
    st.stop()

# --- Pas encore connecté : connexion, création de son compte, mot de passe oublié ---
utilisateur = st.session_state.get("drive_utilisateur")
if not utilisateur:
    connexion, creation, oubli = st.tabs(["Se connecter", "Créer mon compte", "Mot de passe oublié"])
    with connexion:
        with st.form("acces_drive"):
            identifiant = st.text_input("Identifiant")
            saisi = st.text_input("Mot de passe", type="password")
            valider = st.form_submit_button("Ouvrir", type="primary")
        if valider and not trop_d_essais():
            if comptes.verifier(identifiant.strip(), saisi):
                connecter(identifiant.strip(), f"Bienvenue, {identifiant.strip()}.")
            erreur("Identifiant ou mot de passe faux.")
    with creation:
        st.caption("Avec le code d'accès que vous a donné le détenteur de l'application. L'adresse e-mail sert à "
                   "récupérer votre compte si vous oubliez votre mot de passe.")
        with st.form("creer_compte", clear_on_submit=False):
            code_acces = st.text_input("Code d'accès", type="password")
            nouvel_id = st.text_input("Identifiant (votre choix)")
            email = st.text_input("Adresse e-mail de récupération")
            mdp = st.text_input(f"Mot de passe ({comptes.LONGUEUR_MIN} caractères minimum)", type="password")
            confirmation = st.text_input("Confirmer le mot de passe", type="password")
            creer = st.form_submit_button("Créer mon compte", type="primary")
        if creer and not trop_d_essais():
            if probleme := comptes.creer(nouvel_id.strip(), email.strip(), mdp, confirmation, code_acces):
                erreur(probleme)
            else:
                connecter(nouvel_id.strip(), "Compte créé : vous êtes connecté.")
    with oubli:
        st.caption("Vous recevrez un e-mail avec un bouton « Mot de passe de récupération ». Gardez-le : en cas de "
                   "nouvel oubli, il suffit de rouvrir ce même e-mail et de recliquer sur le bouton.")
        with st.form("demander_lien"):
            id_oubli = st.text_input("Identifiant", key="id_oubli")
            demander = st.form_submit_button("Recevoir l'e-mail de récupération")
        if demander and not trop_d_essais():
            try:
                adresse = comptes.envoyer_lien(id_oubli.strip(), adresse_page())
            except Exception as e:
                st.error(f"L'e-mail n'a pas pu être envoyé ({e}).")
            else:
                if adresse:
                    st.success(f"E-mail envoyé à {adresse} : cliquez sur son bouton « Mot de passe de récupération ».")
                else:
                    erreur("Identifiant inconnu.")
    st.stop()

# --- Connecté ---
st.caption(f"Connecté : **{utilisateur}** · récupération : {comptes.masquer(comptes.email_de(utilisateur))}")
c2, c3, c4, _ = st.columns([2.6, 2.6, 1.6, 1])
with c2.popover("🔑 Modifier le mot de passe", width="stretch"):
    with st.form("changer_mot_de_passe", clear_on_submit=True):
        ancien = st.text_input("Mot de passe actuel", type="password")
        nouveau = st.text_input(f"Nouveau mot de passe ({comptes.LONGUEUR_MIN} caractères minimum)", type="password")
        confirmation = st.text_input("Confirmer le nouveau mot de passe", type="password")
        if st.form_submit_button("Enregistrer", type="primary"):
            if not comptes.verifier(utilisateur, ancien):
                st.error("Mot de passe actuel faux.")
            elif probleme := comptes.probleme_mot_de_passe(nouveau, confirmation):
                st.error(probleme)
            else:
                comptes.changer(utilisateur, nouveau)
                st.session_state["drive_message"] = "Mot de passe modifié."
                st.rerun()
with c3.popover("✉️ Adresse de récupération", width="stretch"):
    with st.form("changer_email", clear_on_submit=True):
        email = st.text_input("Nouvelle adresse e-mail de récupération")
        ancien = st.text_input("Mot de passe actuel", type="password")
        if st.form_submit_button("Enregistrer", type="primary"):
            if not comptes.verifier(utilisateur, ancien):
                st.error("Mot de passe actuel faux.")
            elif not comptes.email_valide(email.strip()):
                st.error("Adresse e-mail invalide.")
            else:
                comptes.changer_email(utilisateur, email.strip())
                st.session_state["drive_message"] = "Adresse de récupération modifiée."
                st.rerun()
if c4.button("🔒 Verrouiller", width="stretch", help="Referme la page : le mot de passe sera redemandé"):
    st.session_state.pop("drive_utilisateur", None)
    st.rerun()

# --- Le fichier à envoyer : le Fichier Google Flights de la page Comparatif (même calcul, même type de trajet) ---
st.header("Fichier Google Flights à envoyer")
gardee = donnees.donnee_gardee()
if gardee is None:
    st.info("Aucune donnée chargée : chargez le fichier Airfox dans la page « Charger la donnée Airfox ».")
    st.stop()
df = gardee[0]
# type de trajet : celui choisi au Catalogue, sinon celui de la donnée Airfox (comme la page Comparatif)
nuits = st.session_state.get("nuits_catalogue", donnees.nuits_airfox(df)) or None
google = donnees.fichier_google_flights(df, nuits)
if google.empty:
    st.info("Rien à envoyer : le Fichier Google Flights est vide (table du Catalogue pas encore actualisée, ou "
            "rafraîchie).")
    st.stop()
m1, m2, m3 = st.columns(3)
m1.metric("Lignes", f"{len(google):,}".replace(",", " "))
m2.metric("Compagnies", google["CodeTransporteur"].nunique())
m3.metric("Destinations", google["VilleArrivee"].nunique())
st.dataframe(donnees.vue_google_flights(google), hide_index=True, width="stretch",
             column_config={"Tarif": st.column_config.NumberColumn(format="%.2f €")})
st.caption("La même table que le « Fichier Google Flights » de la page Comparatif (toutes ses lignes, sans les "
           "filtres de cette page).")

if st.button("📤 Envoyer", type="primary"):
    # Destination à définir : rien n'est encore envoyé
    st.info("Le drive de destination n'est pas encore défini : rien n'a été envoyé.")
