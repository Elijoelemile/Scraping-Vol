"""Page « Charger la donnée Profils de Marge » : fichier des profils de marge, transformé au format Tableau de PM,
avec le libellé de chaque profil (fichier des libellés) ; les deux fichiers sont gardés jusqu'à ce qu'on les retire."""
from datetime import datetime
from pathlib import Path

import streamlit as st

import profils_marge
from titres import intitule, titre_page

titre_page("Charger la donnée Profils de Marge", "marge")
st.caption("Chargez l'export des profils de marge (csv ou Excel, quel que soit son nom) et le fichier des libellés "
           "(Code ; Libelle) : les deux sont gardés, même en changeant de page ou en relançant l'application, jusqu'à "
           "ce que vous les retiriez. La page « Evolution Profils de Marge » utilise le résultat.")


def fichier_charge(chemin: Path, texte: str, cle: str, retirer) -> None:
    """Ligne « fichier chargé » avec son bouton pour le retirer."""
    le = datetime.fromtimestamp(chemin.stat().st_mtime)
    c1, c2 = st.columns([5, 1.3], vertical_alignment="center")
    c1.success(f"{texte} : **{chemin.name}** (chargé le {le:%d/%m/%Y} à {le:%H:%M})")
    if c2.button("🗑️ Retirer le fichier", key=cle, width="stretch",
                 help="Retire ce fichier pour en charger un autre (le fichier d'origine sur votre ordinateur n'est "
                      "pas touché)."):
        retirer()
        st.rerun()


# --- 1. Fichier Profils de Marge → Tableau de PM -----------------------------------------------------------------
intitule("1. Fichier Profils de Marge")
if msg := st.session_state.pop("pm_message", None):
    st.success(msg)
garde = profils_marge.fichier_garde()
if garde is not None:
    fichier_charge(garde, f"Fichier Profils de Marge du **{profils_marge.date_du_jour():%d/%m/%Y}**", "retirer_pm",
                   profils_marge.retirer)
    if profils_marge.a_ete_transforme():
        st.caption("✅ Transformé au format **Tableau de PM** : colonne sans nom, Zone, Arr et Dur retirées ; heure "
                   "retirée de Debut ; les 6 colonnes « Montant » renommées Montant engagé, Montant non engagés, "
                   "Montant airfox, Montant terrestre carte, Montant transport carte, Montant bedfox.")
    else:
        st.caption("✅ Ce fichier était déjà au format **Tableau de PM** : gardé tel quel.")
cle_depot = f"depot_pm_{st.session_state.get('depot_pm_n', 0)}"  # nouvelle zone vide après chaque chargement
nouveau = st.file_uploader(
    "Chargement et transformation de la donnée", type=["csv", "xlsx", "xls"], key=cle_depot,
    help="Le nom du fichier doit contenir sa date (par exemple « Fichier PM 09-11-2026.csv ») : elle remplit la "
         f"colonne Date du jour. Un fichier sans date, ou daté d'avant aujourd'hui ({datetime.now():%d/%m/%Y}), est "
         "refusé. S'il remplace un fichier d'une autre date, celui-ci part dans « Historique tables rafraîchies » "
         "(Tables Profils de Marge) ; à la même date, il est simplement remplacé.")
st.caption("📅 Le nom du fichier doit contenir sa date, d'aujourd'hui ou à venir (ex. « Fichier PM "
           f"{datetime.now():%d-%m-%Y}.csv ») : sinon le fichier est refusé.")
if nouveau is not None:
    try:
        archive = profils_marge.garder(nouveau.getvalue(), nouveau.name)
        st.session_state["depot_pm_n"] = st.session_state.get("depot_pm_n", 0) + 1
        st.session_state["pm_message"] = (
            f"**{nouveau.name}** chargé (date du jour {profils_marge.date_du_jour():%d/%m/%Y})."
            + (" Le fichier précédent, d'une autre date, est enregistré dans « Historique tables rafraîchies » "
               "(Tables Profils de Marge)." if archive else
               " Il remplace le fichier précédent, de la même date." if garde is not None else ""))
        st.rerun()
    except Exception as e:
        st.error(f"Impossible de transformer ce fichier : {e}" if isinstance(e, ValueError)
                 else f"Impossible de lire le fichier ({type(e).__name__} : {e}).")

# --- 2. Fichier des libellés → colonne Libelle ------------------------------------------------------------------
intitule("2. Fichier des libellés (Code ; Libelle)")
if msg := st.session_state.pop("lib_message", None):
    st.success(msg)
garde_lib = profils_marge.libelles_garde()
if garde_lib is not None:
    fichier_charge(garde_lib, f"Fichier des libellés ({len(profils_marge.lire_libelles())} codes)", "retirer_lib",
                   profils_marge.retirer_libelles)
    st.caption("Ce fichier reste en place : il sert à tous les fichiers Profils de Marge. Pour le changer, déposez "
               "simplement le nouveau ci-dessous, il remplace celui-ci.")
cle_lib = f"libelles_pm_{st.session_state.get('depot_lib_n', 0)}"  # nouvelle zone vide après chaque chargement
lib = st.file_uploader("Fichier des libellés des profils" + (" (remplace le fichier présent)" if garde_lib else ""),
                       type=["csv", "xlsx", "xls"], key=cle_lib,
                       help="Une colonne « Code » (le code du profil) et une colonne « Libelle ». Il remplace le "
                            "fichier des libellés présent, sans historique.")
if lib is not None:
    try:
        n = len(profils_marge.garder_libelles(lib.getvalue(), lib.name))
        st.session_state["depot_lib_n"] = st.session_state.get("depot_lib_n", 0) + 1
        st.session_state["lib_message"] = (f"**{lib.name}** chargé ({n} codes)"
                                           + (f" : il remplace **{garde_lib.name}**." if garde_lib else "."))
        st.rerun()
    except Exception as e:
        st.error(f"Impossible d'utiliser ce fichier (le fichier présent est gardé) : {e}" if isinstance(e, ValueError)
                 else f"Impossible de lire le fichier ({type(e).__name__} : {e}) ; le fichier présent est gardé.")

# --- Résultat : fichier final à télécharger ---------------------------------------------------------------------
if garde is not None:
    t = profils_marge.lire()
    intitule("Fichier final")
    if garde_lib is None:
        st.warning("Chargez le fichier des libellés (étape 2) pour ajouter la colonne **Libelle**.")
    else:
        sans = t.loc[t["Libelle"] == "", "Profil"].astype(str).str.strip()
        st.caption(f"✅ Colonne **Libelle** ajoutée après Profil : {len(t) - len(sans):,} ligne(s) sur {len(t):,} "
                   f"ont un libellé.".replace(",", " "))
        if len(sans):
            with st.expander(f"⚠️ {sans.nunique()} profil(s) sans libellé ({len(sans)} ligne(s)) : leur code n'est "
                             "pas dans le fichier des libellés"):
                st.write(", ".join(sorted(sans.unique())))
    nom = f"{Path(garde.name).stem} - Tableau de PM" + (" avec libellés" if garde_lib else "")
    d1, d2, _ = st.columns([1.4, 1.4, 3])
    d1.download_button("⬇️ Télécharger (Excel)", profils_marge.en_excel(t), file_name=f"{nom}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
                       type="primary")
    d2.download_button("⬇️ Télécharger (csv)", profils_marge.en_csv(t), file_name=f"{nom}.csv", mime="text/csv",
                       width="stretch")
    st.caption(f"{len(t):,} lignes · {len(t.columns)} colonnes.".replace(",", " "))
    st.dataframe(profils_marge.pour_affichage(t), hide_index=True, width="stretch")
