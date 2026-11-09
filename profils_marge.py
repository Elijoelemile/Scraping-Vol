"""Donnée Profils de Marge : un fichier (csv ou Excel, nommé n'importe comment) gardé sur le disque dans
donnee_profils_marge (personnel), chargé par la page « Charger la donnée Profils de Marge » et lu par « Evolution
Profils de Marge ».

Transformation (toujours la même) : l'export brut des profils de marge
    Profil ; (colonne sans nom : False) ; Marque ; Categorie ; Zone ; Arr ; Debut (jj/mm/aaaa hh:mm) ; Dur ;
    6 × (% marge ; Montant)
devient le « Tableau de PM »
    Profil ; Marque ; Categorie ; Debut (jj/mm/aaaa) ; % marge ; Montant engagé ; % marge ; Montant non engagés ;
    % marge ; Montant airfox ; % marge ; Montant terrestre carte ; % marge ; Montant transport carte ; % marge ;
    Montant bedfox
(colonne sans nom, Zone, Arr et Dur retirées, heure de Debut retirée, les 6 « Montant » renommés dans l'ordre ;
les valeurs restent celles du fichier). Un fichier déjà au format Tableau de PM est gardé tel quel.

Deuxième transformation : une colonne « Libelle » juste après Profil, le libellé du profil trouvé par son code
dans le fichier des libellés (colonnes Code ; Libelle, gardé dans libelles_profils_marge) ; vide si le code n'y est
pas. Et une colonne « Date du jour » avant Profil (même format que Debut : jj/mm/aaaa) : la date du jour où
la date écrite dans le nom du fichier (notée dans .date_du_jour.json), la même pour toutes les lignes ; un
fichier sans date dans son nom, ou daté d'avant aujourd'hui, est refusé. Charger un fichier d'une autre date envoie le précédent dans l'historique des tables
rafraîchies (marqué « Profils de Marge ») ; à la même date, il est simplement remplacé.
"""
import json
import re
import unicodedata
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import Path

import pandas as pd
import streamlit as st

DOSSIER = Path(__file__).with_name("donnee_profils_marge")
DOSSIER_LIBELLES = Path(__file__).with_name("libelles_profils_marge")
MONTANTS = ["Montant engagé", "Montant non engagés", "Montant airfox", "Montant terrestre carte",
            "Montant transport carte", "Montant bedfox"]
COLONNES = ["Profil", "Marque", "Categorie", "Debut"] + [c for m in MONTANTS for c in ("% marge", m)]
RETIREES = ["(colonne sans nom)", "Zone", "Arr", "Dur"]


def _cle(nom) -> str:
    """Nom de colonne comparable : sans accents, espaces ni majuscules."""
    t = unicodedata.normalize("NFKD", str(nom)).encode("ascii", "ignore").decode()
    return "".join(t.lower().split())


def _brut(contenu: bytes, nom: str) -> pd.DataFrame:
    """Toutes les cellules en texte, en-tête compris (première ligne) : les noms répétés restent intacts."""
    if nom.lower().endswith(".csv"):
        texte = contenu.decode("utf-8-sig", errors="strict") if _utf8(contenu) else contenu.decode("cp1252")
        premiere = texte.split("\n", 1)[0]
        sep = ";" if premiere.count(";") >= premiere.count(",") else ","
        return pd.read_csv(StringIO(texte), sep=sep, header=None, dtype=str, keep_default_na=False)
    return pd.read_excel(BytesIO(contenu), header=None, dtype=str).fillna("")


def _utf8(contenu: bytes) -> bool:
    try:
        contenu.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _date_sans_heure(s: pd.Series) -> pd.Series:
    """« 15/01/2024 00:00 » ou « 2024-01-15 00:00:00 » (Excel) → « 15/01/2024 » ; une valeur illisible reste telle quelle."""
    s = s.astype(str).str.strip()
    jma = s.str.extract(r"^(\d{2}/\d{2}/\d{4})")[0]
    amj = pd.to_datetime(s.str.extract(r"^(\d{4}-\d{2}-\d{2})")[0], format="%Y-%m-%d", errors="coerce")
    return jma.fillna(amj.dt.strftime("%d/%m/%Y")).fillna(s)


def transformer(brut: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Tableau de PM à partir du fichier (cellules texte, en-tête en première ligne). Renvoie (tableau, transformé ?) ;
    ValueError si le fichier n'est ni l'export brut ni déjà le Tableau de PM."""
    entete = [str(c).strip() for c in brut.iloc[0]]
    lignes = brut.iloc[1:].reset_index(drop=True)
    lignes = lignes[(lignes != "").any(axis=1)]  # lignes vides en fin de fichier
    cles = [_cle(c) for c in entete]
    if cles[:len(COLONNES)] == [_cle(c) for c in COLONNES]:  # déjà au format Tableau de PM
        t = lignes.iloc[:, :len(COLONNES)].copy()
        t.columns = COLONNES
        t["Debut"] = _date_sans_heure(t["Debut"])
        return t.reset_index(drop=True), False
    # Export brut : les 4 colonnes gardées + les 6 paires « % marge ; Montant », dans l'ordre du fichier
    manquent = [c for c in ("Profil", "Marque", "Categorie", "Debut") if _cle(c) not in cles]
    paires = [i for i in range(len(cles) - 1) if cles[i] == "%marge" and cles[i + 1].startswith("montant")]
    if manquent or len(paires) != len(MONTANTS):
        raise ValueError(
            "Ce fichier n'a pas les colonnes attendues : Profil, Marque, Categorie, Debut et 6 paires « % marge ; "
            "Montant »" + (f" (il manque : {', '.join(manquent)})" if manquent else
                           f" ({len(paires)} paire(s) « % marge ; Montant » trouvée(s) au lieu de 6)") + ".")
    t = pd.DataFrame({c: lignes.iloc[:, cles.index(_cle(c))].values for c in ("Profil", "Marque", "Categorie")})
    t["Debut"] = _date_sans_heure(lignes.iloc[:, cles.index("debut")]).values
    t = pd.concat([t] + [lignes.iloc[:, [i, i + 1]].reset_index(drop=True).set_axis(["% marge", m], axis=1)
                         for i, m in zip(paires, MONTANTS)], axis=1)
    return t, True


FICHIER_DATE = ".date_du_jour.json"  # dans DOSSIER : la date du jour du fichier chargé
DOSSIER_TABLES = Path(__file__).with_name("historique_tables")  # même historique que les tables du Catalogue


def fichier_garde() -> Path | None:
    """Le fichier des profils de marge chargé (un seul à la fois), ou None."""
    fichiers = sorted(f for f in DOSSIER.iterdir() if f.name != FICHIER_DATE) if DOSSIER.exists() else []
    return fichiers[0] if fichiers else None


def date_du_jour() -> date | None:
    """Date du jour du fichier chargé : celle de son nom, notée à son chargement ;
    à défaut, la date du fichier sur le disque."""
    f = fichier_garde()
    if not f:
        return None
    try:
        return date.fromisoformat(json.loads((DOSSIER / FICHIER_DATE).read_text(encoding="utf-8"))["date"])
    except (FileNotFoundError, KeyError, ValueError):
        return datetime.fromtimestamp(f.stat().st_mtime).date()


def noter_date_du_jour(jour: date):
    DOSSIER.mkdir(parents=True, exist_ok=True)
    (DOSSIER / FICHIER_DATE).write_text(json.dumps({"date": jour.isoformat()}), encoding="utf-8")


MOIS = {"janvier": 1, "janv": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3, "avril": 4, "avr": 4, "mai": 5,
        "juin": 6, "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9, "sep": 9, "octobre": 10, "oct": 10,
        "novembre": 11, "nov": 11, "decembre": 12, "dec": 12}


def date_du_nom(nom: str) -> date | None:
    """Date écrite dans le nom du fichier, quel que soit son format : 06-11-2026, 06_11_2026, 06.11.2026, 06 11 2026,
    2026-11-06, 06112026, 20261106, 06-11-26, 6 novembre 2026, 06 nov 2026… ; None s'il n'y en a pas."""
    t = unicodedata.normalize("NFKD", Path(nom).stem).encode("ascii", "ignore").decode().lower()
    sep = r"[\s._\-/]+"
    essais = []  # (jour, mois, année) dans l'ordre de priorité
    for m in re.finditer(rf"(?<!\d)(\d{{4}}){sep}(\d{{1,2}}){sep}(\d{{1,2}})(?!\d)", t):
        essais.append((m[3], m[2], m[1]))
    for m in re.finditer(rf"(?<!\d)(\d{{1,2}}){sep}(\d{{1,2}}){sep}(\d{{4}}|\d{{2}})(?!\d)", t):
        essais.append((m[1], m[2], m[3]))
    for m in re.finditer(rf"(?<!\d)(\d{{1,2}}){sep}?([a-z]+){sep}?(\d{{4}}|\d{{2}})(?!\d)", t):
        if m[2] in MOIS:
            essais.append((m[1], MOIS[m[2]], m[3]))
    for m in re.finditer(r"(?<!\d)(\d{8})(?!\d)", t):
        g = m[1]
        essais += [(g[6:], g[4:6], g[:4]), (g[:2], g[2:4], g[4:])] if g[:2] in ("19", "20") else                   [(g[:2], g[2:4], g[4:]), (g[6:], g[4:6], g[:4])]
    for m in re.finditer(r"(?<!\d)(\d{6})(?!\d)", t):
        essais.append((m[1][:2], m[1][2:4], m[1][4:]))
    for j, mo, a in essais:
        a = int(a) + (2000 if len(str(a)) == 2 else 0)
        try:
            return date(a, int(mo), int(j))
        except ValueError:
            continue
    return None


def garder(contenu: bytes, nom: str) -> str | None:
    """Charge ce fichier à la place du fichier présent. Sa date (Date du jour) est celle écrite dans son nom : refusé
    (ValueError, rien de changé) si le nom n'a pas de date, si cette date est antérieure à aujourd'hui ou si le fichier
    ne convient pas. Si le fichier présent a une autre date, son tableau final part d'abord dans l'historique des
    tables rafraîchies (renvoie le nom de l'enregistrement) ; à la même date, il est simplement remplacé (None)."""
    jour = date_du_nom(nom)
    if jour is None:
        raise ValueError(f"le nom du fichier « {nom} » ne contient pas de date. Renommez-le avec sa date "
                         "(par exemple « Fichier PM 09-11-2026.csv ») puis chargez-le de nouveau.")
    if jour < date.today():
        raise ValueError(f"la date du fichier ({jour:%d/%m/%Y}) est antérieure à aujourd'hui "
                         f"({date.today():%d/%m/%Y}) : seul un fichier daté d'aujourd'hui ou d'une date à venir est "
                         "accepté.")
    transformer(_brut(contenu, nom))
    archive = archiver() if fichier_garde() and date_du_jour() != jour else None
    retirer()
    DOSSIER.mkdir(parents=True, exist_ok=True)
    (DOSSIER / Path(nom).name).write_bytes(contenu)
    noter_date_du_jour(jour)
    return archive


def archiver() -> str:
    """Enregistre le tableau final du fichier présent (Date du jour, Profil, Libelle…) dans historique_tables, marqué
    « Profils de Marge » pour le distinguer des tables du Catalogue."""
    t = lire()
    maintenant = datetime.now()
    nom = f"{maintenant:%Y-%m-%d_%H%M%S}_pm"
    DOSSIER_TABLES.mkdir(parents=True, exist_ok=True)
    (DOSSIER_TABLES / f"{nom}.csv").write_bytes(en_csv(t))
    (DOSSIER_TABLES / f"{nom}.json").write_text(json.dumps(
        {"type": "pm", "rafraichie_le": maintenant.isoformat(timespec="seconds"),
         "description": f"Profils de Marge — {fichier_garde().name} · date du jour {date_du_jour():%d/%m/%Y}",
         "date_du_jour": date_du_jour().isoformat(), "fichier": fichier_garde().name, "lignes": len(t)},
        ensure_ascii=False), encoding="utf-8")
    return nom


def lire_archive(nom: str) -> pd.DataFrame:
    """Tableau Profils de Marge de l'historique (noms de colonnes exacts, « % marge » répété)."""
    brut = pd.read_csv(DOSSIER_TABLES / f"{nom}.csv", sep=";", header=None, dtype=str, keep_default_na=False,
                       encoding="utf-8-sig")
    return brut.iloc[1:].reset_index(drop=True).set_axis(list(brut.iloc[0]), axis=1)


def retirer():
    if DOSSIER.exists():
        for f in DOSSIER.iterdir():
            f.unlink()
    _lire.clear()


@st.cache_data(show_spinner="Lecture et transformation du fichier Profils de Marge…")
def _lire(chemin: str, modifie_le: float) -> tuple[pd.DataFrame, bool]:
    return transformer(_brut(Path(chemin).read_bytes(), chemin))


def lire() -> pd.DataFrame | None:
    """Tableau final du fichier gardé : Date du jour, puis le Tableau de PM avec la colonne Libelle si le fichier des
    libellés est chargé (relu seulement quand un fichier change) ; None si aucun fichier n'est chargé."""
    f = fichier_garde()
    if not f:
        return None
    t = _lire(str(f), f.stat().st_mtime)[0]
    lib = lire_libelles()
    t = ajouter_libelles(t, lib) if lib is not None else t
    return ajouter_date_du_jour(t, date_du_jour())


def ajouter_date_du_jour(t: pd.DataFrame, jour: date) -> pd.DataFrame:
    """Colonne « Date du jour » avant Profil, au format de Debut (jj/mm/aaaa)."""
    t = t.copy()
    t.insert(0, "Date du jour", jour.strftime("%d/%m/%Y"), allow_duplicates=True)
    return t


# --- Deuxième transformation : libellé de chaque profil --------------------------------------------------------
def libelles_lisibles(brut: pd.DataFrame) -> pd.Series:
    """Code → libellé (codes et libellés sans espaces autour ; un code répété garde son premier libellé).
    ValueError si le fichier n'a pas les colonnes Code et Libelle."""
    cles = [_cle(c) for c in brut.iloc[0]]
    i_code = cles.index("code") if "code" in cles else None
    i_lib = next((cles.index(k) for k in ("libelle", "label", "libelles") if k in cles), None)
    if i_code is None or i_lib is None:
        raise ValueError("ce fichier de libellés doit avoir une colonne « Code » et une colonne « Libelle ».")
    lignes = brut.iloc[1:]
    s = pd.Series(lignes.iloc[:, i_lib].str.strip().values, index=lignes.iloc[:, i_code].str.strip().values)
    s = s[s.index != ""]
    return s[~s.index.duplicated()]


def libelles_garde() -> Path | None:
    fichiers = sorted(DOSSIER_LIBELLES.iterdir()) if DOSSIER_LIBELLES.exists() else []
    return fichiers[0] if fichiers else None


def garder_libelles(contenu: bytes, nom: str) -> pd.Series:
    """Remplace le fichier des libellés gardé (erreur, et rien de gardé, s'il ne convient pas)."""
    s = libelles_lisibles(_brut(contenu, nom))
    retirer_libelles()
    DOSSIER_LIBELLES.mkdir(parents=True, exist_ok=True)
    (DOSSIER_LIBELLES / Path(nom).name).write_bytes(contenu)
    return s


def retirer_libelles():
    if DOSSIER_LIBELLES.exists():
        for f in DOSSIER_LIBELLES.iterdir():
            f.unlink()
    _lire_libelles.clear()


@st.cache_data(show_spinner=False)
def _lire_libelles(chemin: str, modifie_le: float) -> pd.Series:
    return libelles_lisibles(_brut(Path(chemin).read_bytes(), chemin))


def lire_libelles() -> pd.Series | None:
    f = libelles_garde()
    return _lire_libelles(str(f), f.stat().st_mtime) if f else None


def ajouter_libelles(t: pd.DataFrame, libelles: pd.Series) -> pd.DataFrame:
    """Colonne « Libelle » juste après Profil : le libellé du code du profil, vide s'il n'est pas dans le fichier."""
    t = t.copy()
    t.insert(1, "Libelle", t["Profil"].astype(str).str.strip().map(libelles).fillna("").values,
             allow_duplicates=True)
    return t


def en_excel(t: pd.DataFrame) -> bytes:
    """Le tableau final en Excel (noms de colonnes exacts, « % marge » répété), tout en texte comme le fichier."""
    sortie = BytesIO()
    with pd.ExcelWriter(sortie, engine="openpyxl") as x:
        t.to_excel(x, index=False, sheet_name="Tableau de PM")
    return sortie.getvalue()


def a_ete_transforme() -> bool:
    f = fichier_garde()
    return bool(f) and _lire(str(f), f.stat().st_mtime)[1]


def en_csv(t: pd.DataFrame) -> bytes:
    """Le Tableau de PM en csv « ; » (noms de colonnes exacts, « % marge » répété), lisible par Excel."""
    return t.to_csv(sep=";", index=False).encode("utf-8-sig")


def pour_affichage(t: pd.DataFrame) -> pd.DataFrame:
    """Streamlit refuse deux colonnes de même nom : chaque « % marge » reçoit un caractère invisible différent."""
    vus = {}
    noms = []
    for c in t.columns:
        vus[c] = vus.get(c, -1) + 1
        noms.append(c + "​" * vus[c])
    return t.set_axis(noms, axis=1)
