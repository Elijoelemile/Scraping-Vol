"""Page « Historique tables rafraîchies » : deux sortes de tables, la plus récente d'abord.
- Catalogue : les tableaux du Catalogue enregistrés au moment de « Rafraîchir la table » (consultables,
  rechargeables dans le Catalogue, téléchargeables en Excel, supprimables) ;
- Profils de Marge : le fichier final (Date du jour, Profil, Libelle…) remplacé par un fichier d'une autre date dans
  « Charger la donnée Profils de Marge » (consultables, téléchargeables en Excel et csv, supprimables)."""
import io

import pandas as pd
import streamlit as st

import catalogue
import profils_marge
from titres import titre_page

titre_page("Historique tables rafraîchies", "tables")

if msg := st.session_state.pop("table_supprimee", None):
    st.success(msg)
toutes = catalogue.tables_rafraichies()
SORTES = {"catalogue": "🧳 Tables du Catalogue", "pm": "💰 Tables Profils de Marge"}
sorte = st.segmented_control("Sorte de tables", list(SORTES), default="catalogue", key="sorte_tables",
                             format_func=lambda k: f"{SORTES[k]} ({sum(t['type'] == k for t in toutes)})",
                             label_visibility="collapsed") or "catalogue"
tables = [t for t in toutes if t["type"] == sorte]

if sorte == "catalogue":
    st.caption("Chaque fois que vous cliquez sur « 🧹 Rafraîchir la table » dans le Catalogue aller-retour, le tableau "
               "affiché est enregistré ici tel qu'il était, avant d'être vidé.")
    vide = ("Aucune table rafraîchie du Catalogue pour l'instant : utilisez « 🧹 Rafraîchir la table » sous le tableau "
            "du Catalogue aller-retour.")
else:
    st.caption("Quand vous chargez dans « Charger la donnée Profils de Marge » un fichier d'une autre date que celui "
               "présent, le fichier final présent (avec sa Date du jour et ses libellés) est enregistré ici avant "
               "d'être remplacé. Un fichier chargé le même jour remplace simplement le précédent.")
    vide = ("Aucune table Profils de Marge pour l'instant : elles arrivent ici quand un fichier Profils de Marge est "
            "remplacé par un fichier d'une autre date.")
if not tables:
    st.info(vide)
    st.stop()

libelle = catalogue.libelle_table
choix = st.selectbox("Table rafraîchie", tables, format_func=libelle, key=f"table_rafraichie_choisie_{sorte}")


@st.cache_data(show_spinner=False)
def excel(nom: str) -> bytes:
    """La table en Excel stylé (en-tête bleu, lignes alternées, filtres), comme les autres exports."""
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo
    tab = catalogue.lire_table_rafraichie(nom)
    sortie = io.BytesIO()
    with pd.ExcelWriter(sortie, engine="openpyxl") as w:
        tab.to_excel(w, sheet_name="Table rafraîchie", index=False)
        ws = w.sheets["Table rafraîchie"]
        if len(tab):
            ref = f"A1:{get_column_letter(len(tab.columns))}{len(tab) + 1}"
            t = Table(displayName="TableRafraichie", ref=ref)
            t.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            ws.add_table(t)
        for i, col in enumerate(tab.columns, start=1):
            ws.column_dimensions[get_column_letter(i)].width = max(12, len(str(col)) + 4)
    return sortie.getvalue()


def bouton_supprimer(colonne):
    with colonne.popover("🗑️ Supprimer cette table", width="stretch"):
        st.write(f"Supprimer définitivement la table du {libelle(choix)} ?")
        if st.button("Oui, supprimer", type="primary", key=f"supprimer_{choix['nom']}"):
            catalogue.supprimer_table_rafraichie(choix["nom"])
            st.session_state["table_supprimee"] = "Table supprimée de l'historique."
            st.session_state.pop(f"table_rafraichie_choisie_{sorte}", None)
            st.rerun()


if sorte == "pm":
    table = profils_marge.lire_archive(choix["nom"])
    st.dataframe(profils_marge.pour_affichage(table), hide_index=True, width="stretch")
    nom = f"{choix['fichier'].rsplit('.', 1)[0]} - Tableau de PM du {choix['date_du_jour'][8:10]}-" \
          f"{choix['date_du_jour'][5:7]}-{choix['date_du_jour'][:4]}"
    c0, c1, c2 = st.columns(3)
    c0.download_button("⬇️ Télécharger (Excel)", profils_marge.en_excel(table), file_name=f"{nom}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
                       type="primary")
    c1.download_button("⬇️ Télécharger (csv)", profils_marge.en_csv(table), file_name=f"{nom}.csv", mime="text/csv",
                       width="stretch")
    bouton_supprimer(c2)
    st.stop()

table = catalogue.lire_table_rafraichie(choix["nom"])
st.dataframe(table, hide_index=True, width="stretch")
c0, c1, c2 = st.columns(3)
if c0.button("📂 Recharger dans le Catalogue", type="primary", width="stretch",
             disabled=catalogue.actualisation_active(),
             help="Remet cette table dans le Catalogue aller-retour pour la retravailler, avec ses champs, comme avant "
                  "« Rafraîchir la table » ; le Comparatif, la Data Visualisation et le Rapport l'utilisent de "
                  "nouveau. Si certaines de ses dates ont été recherchées depuis, leurs prix les plus récents "
                  "s'affichent."):
    catalogue.recharger_table_rafraichie(choix["nom"])
    st.session_state["table_rechargee"] = libelle(choix)
    st.switch_page("pages_app/page_catalogue.py")
c1.download_button("⬇️ Télécharger en Excel", excel(choix["nom"]), file_name=f"table_rafraichie_{choix['nom']}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
bouton_supprimer(c2)
