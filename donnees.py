"""Donnée de référence (fichier Airfox) : le catalogue ne garde que ses trajets.

Avant chaque remplacement, la liste des destinations du catalogue est archivée en PDF dans historique_catalogue/.
"""
import io
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd

import catalogue
from pays import pays_aeroport

DOSSIER_HISTORIQUE = Path(__file__).with_name("historique_catalogue")
LISTE_AIRFOX = Path(__file__).with_name("listes") / "airfox.txt"


def lire_fichier(contenu: bytes, nom: str) -> pd.DataFrame:
    """Fichier Airfox (Excel ou CSV « ; » / « , »)."""
    if nom.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(contenu))
    for encodage in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(contenu), sep=None, engine="python", encoding=encodage)
        except UnicodeDecodeError:
            continue
    raise ValueError("encodage du fichier non reconnu")


def aeroports(codes) -> list[str]:
    """Codes du fichier (villes Airfox : PAR, LON…) -> aéroports, dans l'ordre, sans doublon."""
    vus = []
    for c in codes:
        for a in catalogue.VILLES_AIRFOX.get(str(c).strip().upper(), [str(c).strip().upper()]):
            if a not in vus:
                vus.append(a)
    return vus


def trajets_de(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Aéroports de départ et d'arrivée de la donnée (destinations les plus fréquentes d'abord)."""
    departs = aeroports(df["VilleDepart"].dropna().unique())
    arrivees = [a for a in aeroports(df["VilleArrivee"].value_counts().index) if a not in departs]
    return departs, arrivees


def destinations_catalogue() -> pd.DataFrame:
    with closing(catalogue.connexion()) as con:
        return pd.read_sql_query("SELECT destination, MIN(ville_arrivee) AS ville_arrivee FROM trajets_catalogue"
                                 " GROUP BY destination ORDER BY 2, 1", con)  # un aéroport = une ligne


def comparer(departs: list[str], arrivees: list[str]) -> dict:
    """Ce que le remplacement changerait : trajets supprimés, destinations ajoutées ou retirées."""
    with closing(catalogue.connexion()) as con:
        trajets = pd.read_sql_query("SELECT origine, destination FROM trajets_catalogue", con)
    garde = trajets["origine"].isin(departs) & trajets["destination"].isin(arrivees)
    actuelles = set(trajets["destination"])
    return {"trajets_supprimes": int((~garde).sum()), "trajets_gardes": int(garde.sum()),
            "destinations_retirees": sorted(actuelles - set(arrivees)),
            "destinations_ajoutees": [a for a in arrivees if a not in actuelles]}


def archiver_pdf() -> Path:
    """Liste des destinations du catalogue actuel, en PDF daté dans historique_catalogue/."""
    from fpdf import FPDF

    dest = destinations_catalogue()
    maintenant = datetime.now()
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()

    def texte(s):  # polices intégrées du PDF : alphabet latin (accents français compris)
        return str(s).encode("latin-1", "replace").decode("latin-1")

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, texte("Historique du catalogue"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, texte(f"{maintenant:%d/%m/%Y} à {maintenant:%H:%M} - {len(dest)} destinations"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    for largeur, titre in ((25, "Code"), (85, "Ville"), (70, "Pays")):
        pdf.cell(largeur, 7, texte(titre), border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 10)
    for r in dest.itertuples():
        for largeur, valeur in ((25, r.destination), (85, r.ville_arrivee or ""), (70, pays_aeroport(r.destination))):
            pdf.cell(largeur, 6, texte(valeur)[:45], border=1)
        pdf.ln()
    DOSSIER_HISTORIQUE.mkdir(exist_ok=True)
    fichier = DOSSIER_HISTORIQUE / f"catalogue_{maintenant:%Y-%m-%d_%H-%M-%S}.pdf"
    pdf.output(str(fichier))
    return fichier


def remplacer_catalogue(departs: list[str], arrivees: list[str]) -> dict:
    """Archive le catalogue en PDF, supprime les trajets hors donnée (et leurs prix), ajoute les nouveaux trajets
    (à collecter) et met à jour listes/airfox.txt."""
    archive = archiver_pdf()
    bilan = comparer(departs, arrivees)
    d, a = ",".join("?" * len(departs)), ",".join("?" * len(arrivees))
    hors = f"NOT (origine IN ({d}) AND destination IN ({a}))"
    with closing(catalogue.connexion()) as con, con:
        for table in ("trajets_catalogue", "calendrier", "calendrier_ar", "calendrier_compagnie",
                      "compagnies_trajets", "vols_jour"):
            con.execute(f"DELETE FROM {table} WHERE {hors} AND origine != '-'", [*departs, *arrivees])
        con.execute(f"DELETE FROM vols_jour WHERE origine = '-' AND destination NOT IN ({a})", arrivees)
        maintenant = datetime.now().isoformat(timespec="seconds")
        con.executemany("INSERT OR IGNORE INTO trajets_catalogue (origine, destination, ville_depart, ville_arrivee,"
                        " pays_arrivee, decouvert_le) VALUES (?, ?, ?, ?, ?, ?)",
                        [(o, x, catalogue.ville_fr(o), catalogue.ville_fr(x), pays_aeroport(x), maintenant)
                         for o in departs for x in arrivees])
    LISTE_AIRFOX.write_text("# Destinations de la donnée chargée (fichier Airfox), les plus fréquentes d'abord\n"
                            "# Format : CODE;Ville\n" + "".join(f"{x};{catalogue.ville_fr(x)}\n" for x in arrivees),
                            encoding="utf-8")
    return bilan | {"archive": archive.name}


def historiques() -> list[Path]:
    """PDF archivés, du plus récent au plus ancien."""
    return sorted(DOSSIER_HISTORIQUE.glob("catalogue_*.pdf"), reverse=True) if DOSSIER_HISTORIQUE.exists() else []


# --- Donnée chargée, gardée sur le disque : elle reste affichée tant qu'on ne la retire pas (croix du fichier) ------
DOSSIER_DONNEE = Path(__file__).with_name("donnee_chargee")


def garder(contenu: bytes, nom: str):
    DOSSIER_DONNEE.mkdir(exist_ok=True)
    for ancien in DOSSIER_DONNEE.iterdir():
        ancien.unlink()
    (DOSSIER_DONNEE / nom).write_bytes(contenu)


def donnee_presente() -> bool:
    """Une donnée est chargée (sans la lire : pour les chiffres du Catalogue, relus toutes les 15 s)."""
    return DOSSIER_DONNEE.exists() and any(DOSSIER_DONNEE.iterdir())


_lue: dict = {}  # dernière donnée lue : {(nom, date de modification, taille): table}


def donnee_gardee() -> tuple[pd.DataFrame, str, int] | None:
    """(table, nom du fichier, taille en octets) de la donnée chargée, ou None si aucune.
    Le fichier (plusieurs secondes de lecture pour un Excel) n'est relu que s'il a changé."""
    fichiers = sorted(DOSSIER_DONNEE.iterdir()) if DOSSIER_DONNEE.exists() else []
    if not fichiers:
        return None
    infos = fichiers[0].stat()
    cle = (fichiers[0].name, infos.st_mtime_ns, infos.st_size)
    if cle not in _lue:
        _lue.clear()
        _lue[cle] = lire_fichier(fichiers[0].read_bytes(), fichiers[0].name)
    return _lue[cle].copy(), fichiers[0].name, infos.st_size


def retirer():
    if DOSSIER_DONNEE.exists():
        for f in DOSSIER_DONNEE.iterdir():
            f.unlink()
