"""Page « Rapport Excel » : téléchargement du rapport Airfox vs Google Flights (fichier .xlsx).

Les données viennent de la page « Comparatif Airfox vs Google Flights » (fichier Airfox chargé, fichier
Google Flights correspondant et leur jointure), gardées en mémoire de session.
"""
import io
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st
from titres import couleurs_graphiques, titre_page

titre_page("Rapport Excel", "rapport")

airfox = st.session_state.get("rapport_airfox")
google = st.session_state.get("rapport_google")
jointure = st.session_state.get("rapport_jointure")
jointure_mc = st.session_state.get("rapport_jointure_moins_chere")
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
            if str(tab.columns[j]).startswith(("Tarif", "Écart")) and isinstance(c.value, (int, float)):
                c.number_format = '#,##0.00 "€"'  # symbole de l'euro après les tarifs
            if isinstance(c.value, datetime):  # avec l'heure seulement si elle est renseignée (ex. CollecteLe)
                c.number_format = "dd/mm/yyyy hh:mm" if c.value.hour or c.value.minute else "dd/mm/yyyy"
    if not len(tab):
        return
    fin = entete + len(tab)
    table = Table(displayName=nom_table,
                  ref=f"{get_column_letter(colonne)}{entete}:{get_column_letter(colonne + len(tab.columns) - 1)}{fin}")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)
    if "MêmeCompagnie" in tab:  # comme dans l'application : cases grises, coche noire centrée
        from openpyxl.styles import Alignment, PatternFill
        col = colonne + list(tab.columns).index("MêmeCompagnie")
        for ligne_excel in range(entete + 1, fin + 1):
            c = ws.cell(row=ligne_excel, column=col)
            c.fill = PatternFill("solid", fgColor="E0E0E0")
            c.font = Font(color="000000", bold=True)
            c.alignment = Alignment(horizontal="center")
    if "Même bagage" in tab:  # cellule verte si TRUE, rouge si FALSE
        from openpyxl.formatting.rule import FormulaRule
        from openpyxl.styles import PatternFill
        lettre = get_column_letter(colonne + list(tab.columns).index("Même bagage"))
        zone, haut = f"{lettre}{entete + 1}:{lettre}{fin}", f"{lettre}{entete + 1}"
        ws.conditional_formatting.add(zone, FormulaRule(formula=[f'{haut}="TRUE"'], font=Font(color="000000", bold=True),
                                                        fill=PatternFill("solid", bgColor="C8E6C9")))
        ws.conditional_formatting.add(zone, FormulaRule(formula=[f'{haut}="FALSE"'], font=Font(color="000000", bold=True),
                                                        fill=PatternFill("solid", bgColor="FFCDD2")))
    if ECART in tab:
        lettre = get_column_letter(colonne + list(tab.columns).index(ECART))
        zone = f"{lettre}{entete + 1}:{lettre}{fin}"
        # rouge si positif (Airfox plus cher), vert si négatif (Airfox moins cher), écriture noire
        from openpyxl.styles import PatternFill
        ws.conditional_formatting.add(zone, CellIsRule(operator="greaterThan", formula=["0"],
                                                       font=Font(color="000000", bold=True),
                                                       fill=PatternFill("solid", bgColor="FFCDD2")))
        ws.conditional_formatting.add(zone, CellIsRule(operator="lessThan", formula=["0"],
                                                       font=Font(color="000000", bold=True),
                                                       fill=PatternFill("solid", bgColor="C8E6C9")))


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
    # Bagage écrit « TRUE » / « FALSE » comme dans l'application (un booléen Excel s'afficherait VRAI / FAUX)
    for c in tab.columns:
        if str(c).startswith("FlagBagageInclus") or c == "Même bagage":
            tab[c] = tab[c].map(lambda v: ("TRUE" if v else "FALSE") if isinstance(v, bool) else v)
    return tab


def quadrillage_clair(graphique):
    """Quadrillage gris clair et titre au-dessus du tracé (et non par-dessus)."""
    from openpyxl.chart.shapes import GraphicalProperties
    from openpyxl.drawing.line import LineProperties
    graphique.y_axis.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill="E3E3E0"))
    if graphique.title is not None:
        graphique.title.overlay = False


def tableau_donnees(ws, col_debut: int, col_fin: int, entete: int, fin: int, nom: str):
    """Données d'un graphique en tableau Excel stylé, comme les tables des autres feuilles : en-tête bleu, lignes
    alternées, filtres ; colonnes assez larges pour leurs en-têtes."""
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo
    table = Table(displayName=nom, ref=f"{get_column_letter(col_debut)}{entete}:{get_column_letter(col_fin)}{fin}")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)
    for col in range(col_debut, col_fin + 1):
        lettre = get_column_letter(col)
        ws.column_dimensions[lettre].width = max(ws.column_dimensions[lettre].width or 0,
                                                 len(str(ws.cell(row=entete, column=col).value or "")) + 4, 12)


def airfox_plus_cher(ws, col_airfox: int, col_google: int, debut: int, fin: int):
    """Case Airfox en rouge (même rouge que les écarts positifs) quand Airfox est plus cher que Google ce jour-là."""
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    a, g = get_column_letter(col_airfox), get_column_letter(col_google)
    ws.conditional_formatting.add(f"{a}{debut}:{a}{fin}", FormulaRule(
        formula=[f"AND(ISNUMBER({a}{debut}),ISNUMBER({g}{debut}),{a}{debut}>{g}{debut})"],
        font=Font(color="000000", bold=True), fill=PatternFill("solid", bgColor="FFCDD2")))


def feuille_graphiques(ws, jointure: pd.DataFrame, jointure_mc: pd.DataFrame, choix_graphique: dict | None = None,
                       couleurs: tuple = ("2A78D6", "111111")):
    """Feuille 4 : les deux graphiques de la page Data Visualisation, en graphiques Excel (modifiables), avec
    leurs données en dessous. graphique « même compagnie » : tarifs date par date ; graphique « moins chère » : Airfox face au prix le plus bas de Google,
    pour la destination la plus comparée."""
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.chart.label import DataLabelList
    from openpyxl.chart.marker import DataPoint
    from openpyxl.chart.shapes import GraphicalProperties
    from openpyxl.styles import Font

    ws.sheet_view.showGridLines = False
    for col in range(1, 18):
        ws.column_dimensions[chr(64 + col)].width = 13
    # --- Graphique « même compagnie » : tarif Airfox et Google à chaque date, pour la destination la plus comparée (30 premières dates)
    if len(jointure):
        j = jointure.assign(Date=pd.to_datetime(jointure["DateAller"], dayfirst=True, errors="coerce"),
                            **{c: pd.to_numeric(jointure[c], errors="coerce") for c in ("Tarif Airfox", "Tarif Google")})
        # destination et période choisies dans la page Data Visualisation ; sinon la plus comparée, 30 premières dates
        depart_cie = (choix_graphique or {}).get("depart") or str(j["VilleDepart"].iloc[0])
        j = j[j["VilleDepart"].astype(str) == depart_cie]  # ville de départ choisie dans Data Visualisation
        for cle, colonne in (("aeroport_depart", "AeroportDepart"), ("aeroport_arrivee", "AeroportArrivee")):
            if (choix_graphique or {}).get(cle, "Tous") != "Tous":  # aéroport choisi : seulement ses tarifs
                j = j[j[colonne] == choix_graphique[cle]]
        if j.empty:  # aucune comparaison au départ de cette ville
            ville_cie, d1 = "", pd.DataFrame()
        elif choix_graphique and choix_graphique["ville"] in set(j["VilleArrivee"]):
            ville_cie, debut_cie, fin_cie = choix_graphique["ville"], pd.Timestamp(choix_graphique["debut"]), pd.Timestamp(choix_graphique["fin"])
        else:
            ville_cie = j["VilleArrivee"].value_counts().index[0]
            dates_ville = j.loc[j["VilleArrivee"] == ville_cie, "Date"].sort_values()
            debut_cie, fin_cie = dates_ville.iloc[0], dates_ville.iloc[min(len(dates_ville), 30) - 1]
        # tous les jours entre les deux dates, même sans comparaison (barres vides)
        d1 = pd.DataFrame() if j.empty else (j[(j["VilleArrivee"] == ville_cie) & (j["Date"] >= debut_cie) & (j["Date"] <= fin_cie)]
              .drop_duplicates("Date").set_index("Date")
              .reindex(pd.date_range(debut_cie, fin_cie, freq="D")).rename_axis("Date").reset_index())
    else:
        ville_cie, d1 = "", pd.DataFrame()
    bandeau(ws, "Tarifs Airfox et Google, date par date (jointure sur la compagnie)", 3, 1, 8, BLEU, 13, 26)
    bandeau(ws, "Airfox face au prix le plus bas de Google, date par date", 3, 10, 17, BLEU, 13, 26)
    ligne_donnees = 35  # données des graphiques, sous les graphiques
    ws.cell(row=33, column=1, value="Tarif Airfox et tarif Google de la même compagnie, à chaque date de "
                                    "départ, tous les jours de la période choisie dans Data Visualisation (sinon la destination la plus "
                                    "comparée).").font = Font(
                                        italic=True, color="52514E")
    ws.cell(row=33, column=10, value="Tarif Airfox et tarif de la compagnie la moins chère chez Google "
                                     "le même jour. Destination la plus comparée.").font = Font(italic=True,
                                                                                                color="52514E")
    ws.cell(row=ligne_donnees, column=1, value=f"Données du graphique — {ville_cie}").font = Font(bold=True)
    for k, titre in enumerate(("Date", "Airfox", "Google", "Compagnie")):
        ws.cell(row=ligne_donnees + 1, column=1 + k, value=titre).font = Font(bold=True)
    for i, r in enumerate(d1.itertuples(), start=ligne_donnees + 2):
        ws.cell(row=i, column=1, value=d1.loc[r.Index, "Date"].strftime("%d/%m"))  # texte : une colonne par date
        for k, c in enumerate(("Tarif Airfox", "Tarif Google"), start=2):  # format repris par les étiquettes
            if pd.notna(d1.loc[r.Index, c]):  # jour sans comparaison : case vide, barre absente
                ws.cell(row=i, column=k, value=round(float(d1.loc[r.Index, c]), 2)).number_format = '#,##0 "€"'
        if pd.notna(d1.loc[r.Index, "CodeTransporteur"]):
            ws.cell(row=i, column=4, value=d1.loc[r.Index, "CodeTransporteur"])
    if len(d1):  # tableau Excel stylé comme dans les autres feuilles (en-tête bleu, lignes alternées, filtres)
        tableau_donnees(ws, 1, 4, ligne_donnees + 1, ligne_donnees + 1 + len(d1), "DonneesGraphiqueCompagnie")
        airfox_plus_cher(ws, 2, 3, ligne_donnees + 2, ligne_donnees + 1 + len(d1))
    if len(d1):
        barres = BarChart()
        barres.type, barres.style, barres.grouping = "col", 10, "clustered"
        aeroports = [a for a in ((choix_graphique or {}).get("aeroport_depart", "Tous"),
                                 (choix_graphique or {}).get("aeroport_arrivee", "Tous")) if a != "Tous"]
        barres.title = (f"{depart_cie} → {ville_cie}" + (f" ({' → '.join(aeroports)})" if aeroports else "")
                        + " : tarif Airfox et Google à chaque date")
        barres.x_axis.title = barres.y_axis.title = None  # dates et tarifs se lisent sur les graduations
        fin_donnees = ligne_donnees + 1 + len(d1)
        barres.add_data(Reference(ws, min_col=2, max_col=3, min_row=ligne_donnees + 1, max_row=fin_donnees),
                        titles_from_data=True)
        barres.set_categories(Reference(ws, min_col=1, min_row=ligne_donnees + 2, max_row=fin_donnees))
        for serie, couleur in zip(barres.series, couleurs):
            serie.graphicalProperties = GraphicalProperties(solidFill=couleur)
            if len(d1) <= 15:  # tarif au-dessus des barres s'il y a la place
                serie.dLbls = DataLabelList()
                serie.dLbls.showVal = True
                serie.dLbls.showSerName = serie.dLbls.showCatName = serie.dLbls.showLegendKey = False
                serie.dLbls.position = "outEnd"
        barres.gapWidth, barres.overlap = 50, 0
        barres.y_axis.number_format = '#,##0 "€"'
        barres.y_axis.scaling.min = 0
        barres.legend.position, barres.legend.overlay = "t", False
        barres.y_axis.delete = barres.x_axis.delete = False
        quadrillage_clair(barres)
        barres.height, barres.width = 14.5, 17
        ws.add_chart(barres, "A5")
    # --- Graphique « moins chère » : destination la plus comparée
    jm = jointure_mc.assign(Date=pd.to_datetime(jointure_mc.get("DateAller"), dayfirst=True, errors="coerce"))         if len(jointure_mc) else pd.DataFrame(columns=["VilleArrivee", "Date", "Tarif Airfox", "Tarif Google"])
    if len(jm):
        ville = jm["VilleArrivee"].value_counts().index[0]
        d = jm[jm["VilleArrivee"] == ville].sort_values("Date")
        ws.cell(row=ligne_donnees, column=10, value=f"Données du graphique — {ville}").font = Font(bold=True)
        for k, titre in enumerate(("Date", "Airfox", "Google (moins chère)", "Compagnie Airfox", "Compagnie Google")):
            ws.cell(row=ligne_donnees + 1, column=10 + k, value=titre).font = Font(bold=True)
        for i, r in enumerate(d.itertuples(), start=ligne_donnees + 2):
            ws.cell(row=i, column=10, value=r.Date.to_pydatetime()).number_format = "dd/mm"
            for k, c in ((11, "Tarif Airfox"), (12, "Tarif Google")):
                ws.cell(row=i, column=k, value=float(pd.to_numeric(d.loc[r.Index, c], errors="coerce"))
                        ).number_format = '#,##0 "€"'
            ws.cell(row=i, column=13, value=d.loc[r.Index, "CodeTransporteur"])
            ws.cell(row=i, column=14, value=d.loc[r.Index, "CompagnieMoinsChere"])
        fin = ligne_donnees + 1 + len(d)
        tableau_donnees(ws, 10, 14, ligne_donnees + 1, fin, "DonneesGraphiqueMoinsChere")
        airfox_plus_cher(ws, 11, 12, ligne_donnees + 2, fin)
        courbes = LineChart()
        courbes.title = f"{ville} : Airfox et prix le plus bas chez Google"
        courbes.y_axis.title = courbes.x_axis.title = None  # dates et tarifs se lisent sur les graduations
        courbes.y_axis.number_format = '#,##0 "€"'
        courbes.add_data(Reference(ws, min_col=11, max_col=12, min_row=ligne_donnees + 1, max_row=fin),
                         titles_from_data=True)
        courbes.set_categories(Reference(ws, min_col=10, min_row=ligne_donnees + 2, max_row=fin))
        courbes.x_axis.number_format = "dd/mm"
        for serie, couleur in zip(courbes.series, couleurs):
            serie.graphicalProperties.line.solidFill = couleur
            serie.graphicalProperties.line.width = 22000  # ≈ 1,75 pt
            serie.smooth = False
            serie.marker.symbol, serie.marker.size = "circle", 6  # un point par date réellement comparée
            serie.marker.graphicalProperties = GraphicalProperties(solidFill=couleur)
            serie.marker.graphicalProperties.line.solidFill = couleur
        courbes.legend.position, courbes.legend.overlay = "t", False  # légende au-dessus, hors du tracé
        quadrillage_clair(courbes)
        courbes.y_axis.delete = courbes.x_axis.delete = False
        courbes.height, courbes.width = 14.5, 17
        ws.add_chart(courbes, "J5")
    grand_titre(ws, "Visualisation graphique")


@st.cache_data(show_spinner="Préparation du rapport…")
def construire_rapport(airfox: pd.DataFrame, google: pd.DataFrame, jointure: pd.DataFrame,
                       jointure_mc: pd.DataFrame, version: float = 0, choix_graphique: dict | None = None,
                       couleurs: tuple = ("2A78D6", "111111")) -> bytes:
    """version : date de modification de ce fichier — le cache ne garde que le seul code de cette fonction,
    il resservirait sinon un ancien rapport après un changement des feuilles ou des graphiques."""
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
    # Feuille 3 : jointure sur la compagnie la moins chère, au milieu de la feuille
    moins = classeur.create_sheet("Jointure moins chère")
    ecrire_table(moins, en_dates(jointure_mc, ["DateAller", "DateRetour", "CollecteLe"]),
                 "Airfox face à la compagnie la moins chère chez Google : même départ, même ville d'arrivée, même date",
                 3, 3, "JointureMoinsChere")
    grand_titre(moins, "Jointure sur la compagnie la moins chère en comparaison avec Google Flights", debut=3)
    moins.column_dimensions["A"].width = moins.column_dimensions["B"].width = 6
    moins.freeze_panes = "A6"
    feuille_graphiques(classeur.create_sheet("Visualisation graphique"), jointure, jointure_mc, choix_graphique, couleurs)
    sortie = io.BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()


def nombre(n: int) -> str:
    return f"{n:,}".replace(",", " ")


vide = pd.DataFrame()
st.caption(f"Fichier Airfox : {st.session_state.get('rapport_fichier', '—')} · {nombre(len(airfox))} lignes Airfox, "
           f"{nombre(0 if google is None else len(google))} lignes Google Flights, "
           f"{nombre(0 if jointure is None else len(jointure))} lignes jointes sur la même compagnie, "
           f"{nombre(0 if jointure_mc is None else len(jointure_mc))} sur la compagnie la moins chère.")
contenu = construire_rapport(airfox, vide if google is None else google, vide if jointure is None else jointure,
                             vide if jointure_mc is None else jointure_mc, version=Path(__file__).stat().st_mtime,
                             choix_graphique=st.session_state.get("rapport_graphique"),
                             couleurs=tuple(couleurs_graphiques()[k].lstrip("#").upper() for k in ("Airfox", "Google")))
st.download_button("Télécharger", contenu, type="primary",
                   file_name=f"Rapport Airfox vs Google Flights {datetime.now():%Y-%m-%d %Hh%M}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
