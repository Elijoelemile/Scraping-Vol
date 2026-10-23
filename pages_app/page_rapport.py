"""Page « Rapport Excel » : téléchargement du rapport Airfox vs Google Flights (fichier .xlsx).

Les données viennent de la page « Comparatif Airfox vs Google Flights » (fichier Airfox chargé, fichier
Google Flights correspondant et leur jointure), gardées en mémoire de session.
"""
import io
from datetime import datetime

import pandas as pd
import streamlit as st

st.title("Rapport Excel")

airfox = st.session_state.get("rapport_airfox")
google = st.session_state.get("rapport_google")
jointure = st.session_state.get("rapport_jointure")
if airfox is None:
    st.info("Chargez d'abord le fichier Airfox dans la page « Comparatif Airfox vs Google Flights ».")
    st.stop()

ECART = "Écart (Airfox − Google)"
BLEU_NUIT, BLEU, BLEU_CLAIR = "0B2E5C", "1F5FA8", "DCE8F5"  # couleurs des bandeaux (style tableau de bord)


def ecrire_table(ws, tab: pd.DataFrame, titre: str, ligne: int, colonne: int, nom_table: str):
    """Écrit `tab` dans la feuille : titre sur `ligne`, en-têtes deux lignes plus bas, données dessous, en tableau
    Excel (filtres et lignes alternées). Écart : rouge si négatif (Airfox moins cher), vert si positif."""
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    entete = ligne + 2
    bandeau(ws, titre, ligne, colonne, colonne + max(len(tab.columns), 1) - 1, BLEU, 13, 26)
    for j, nom in enumerate(tab.columns):
        ws.cell(row=entete, column=colonne + j, value=str(nom)).font = Font(bold=True)
        ws.column_dimensions[get_column_letter(colonne + j)].width = max(12, len(str(nom)) + 2)
    for i, valeurs in enumerate(tab.itertuples(index=False), start=entete + 1):
        for j, v in enumerate(valeurs):
            if pd.isna(v):
                continue
            c = ws.cell(row=i, column=colonne + j, value=v.item() if hasattr(v, "item") else v)
            if isinstance(c.value, datetime):  # avec l'heure seulement si elle est renseignée (ex. CollecteLe)
                c.number_format = "dd/mm/yyyy hh:mm" if c.value.hour or c.value.minute else "dd/mm/yyyy"
    if not len(tab):
        return
    fin = entete + len(tab)
    table = Table(displayName=nom_table,
                  ref=f"{get_column_letter(colonne)}{entete}:{get_column_letter(colonne + len(tab.columns) - 1)}{fin}")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)
    if ECART in tab:
        lettre = get_column_letter(colonne + list(tab.columns).index(ECART))
        zone = f"{lettre}{entete + 1}:{lettre}{fin}"
        ws.conditional_formatting.add(zone, CellIsRule(operator="lessThan", formula=["0"],
                                                       font=Font(color="D62728", bold=True)))
        ws.conditional_formatting.add(zone, CellIsRule(operator="greaterThan", formula=["0"],
                                                       font=Font(color="2CA02C", bold=True)))


def bandeau(ws, texte: str, ligne: int, debut: int, fin: int, fond: str, taille: int, hauteur: int):
    """Titre en bandeau : cellules fusionnées, fond coloré, texte blanc en gras centré, liseré bleu clair."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    if fin > debut:
        ws.merge_cells(start_row=ligne, start_column=debut, end_row=ligne, end_column=fin)
    liseré = Side(style="medium", color=BLEU_CLAIR)
    for col in range(debut, fin + 1):
        c = ws.cell(row=ligne, column=col)
        c.fill = PatternFill("solid", fgColor=fond)
        c.border = Border(top=liseré, bottom=liseré)
    c = ws.cell(row=ligne, column=debut, value=texte)
    c.font = Font(bold=True, size=taille, color="FFFFFF")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[ligne].height = hauteur


def grand_titre(ws, texte: str, debut: int = 1):
    """Grand titre de la feuille, sur toute la largeur de son contenu (à appeler après les tables)."""
    bandeau(ws, texte.upper(), 1, debut, max(ws.max_column, debut + 5), BLEU_NUIT, 22, 44)


def en_dates(tab: pd.DataFrame, colonnes) -> pd.DataFrame:
    """Colonnes de dates (texte « 22/10/2026 » ou ISO) en vraies dates Excel."""
    tab = tab.copy()
    for c in colonnes:
        if c in tab:
            jj_mm = tab[c].astype(str).str.match(r"\d{2}/\d{2}/\d{4}")
            tab[c] = pd.to_datetime(tab[c], errors="coerce", dayfirst=bool(jj_mm.any()))
    # Bagage affiché « TRUE » / « FALSE » dans l'application : vrais booléens Excel
    for c in tab.columns:
        if str(c).startswith("FlagBagageInclus"):
            tab[c] = tab[c].map(lambda v: {"TRUE": True, "FALSE": False}.get(v, v) if isinstance(v, str) else v)
    return tab


@st.cache_data(show_spinner="Préparation du rapport…")
def construire_rapport(airfox: pd.DataFrame, google: pd.DataFrame, jointure: pd.DataFrame) -> bytes:
    from openpyxl import Workbook

    classeur = Workbook()
    # Feuille 1 : les deux fichiers complets, côte à côte
    brutes = classeur.active
    brutes.title = "Données brutes"
    airfox = en_dates(airfox, ["DateAller"])
    ecrire_table(brutes, airfox, "Airfox", 3, 1, "Airfox")
    ecrire_table(brutes, en_dates(google, ["DateAller", "DateRetour", "CollecteLe"]), "Google Flights", 3,
                 len(airfox.columns) + 2, "GoogleFlights")  # à droite d'Airfox, après une colonne vide
    grand_titre(brutes, "Données brutes")
    brutes.freeze_panes = "A6"
    # Feuille 2 : jointure sur la même compagnie, au milieu de la feuille
    meme = classeur.create_sheet("Jointure même compagnie")
    ecrire_table(meme, en_dates(jointure, ["DateAller", "DateRetour", "CollecteLe"]),
                 "Airfox et Google Flights : même départ, même ville d'arrivée, même date, même compagnie", 3, 3,
                 "JointureMemeCompagnie")
    grand_titre(meme, "Jointure sur la même compagnie en comparaison avec Google Flights", debut=3)
    meme.column_dimensions["A"].width = meme.column_dimensions["B"].width = 6
    meme.freeze_panes = "A6"
    classeur.create_sheet("Feuille 3")
    sortie = io.BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()


def nombre(n: int) -> str:
    return f"{n:,}".replace(",", " ")


vide = pd.DataFrame()
st.caption(f"Fichier Airfox : {st.session_state.get('rapport_fichier', '—')} · {nombre(len(airfox))} lignes Airfox, "
           f"{nombre(0 if google is None else len(google))} lignes Google Flights, "
           f"{nombre(0 if jointure is None else len(jointure))} lignes jointes sur la même compagnie.")
contenu = construire_rapport(airfox, vide if google is None else google, vide if jointure is None else jointure)
st.download_button("Télécharger", contenu, type="primary",
                   file_name=f"Rapport Airfox vs Google Flights {datetime.now():%Y-%m-%d}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
