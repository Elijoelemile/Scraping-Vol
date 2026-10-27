"""Page « Historique tables rafraîchies » : les tableaux du Catalogue enregistrés au moment de « Rafraîchir la table »,
la plus récente d'abord, consultables et téléchargeables en Excel."""
import io
from datetime import datetime

import pandas as pd
import streamlit as st

import catalogue
from titres import titre_page

titre_page("Historique tables rafraîchies", "tables")
st.caption("Chaque fois que vous cliquez sur « 🧹 Rafraîchir la table » dans le Catalogue aller-retour, le tableau "
           "affiché est enregistré ici tel qu'il était, avant d'être vidé.")

tables = catalogue.tables_rafraichies()
if not tables:
    st.info("Aucune table rafraîchie pour l'instant : utilisez « 🧹 Rafraîchir la table » sous le tableau du "
            "Catalogue aller-retour.")
    st.stop()


def libelle(t: dict) -> str:
    quand = datetime.fromisoformat(t["rafraichie_le"])
    return f"{quand:%d/%m/%Y} à {quand:%H:%M} — {t['description']} ({t['lignes']:,} lignes)".replace(",", " ")


choix = st.selectbox("Table rafraîchie", tables, format_func=libelle, key="table_rafraichie_choisie")
table = catalogue.lire_table_rafraichie(choix["nom"])


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


st.dataframe(table, hide_index=True, width="stretch")
st.download_button("⬇️ Télécharger en Excel", excel(choix["nom"]), file_name=f"table_rafraichie_{choix['nom']}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
