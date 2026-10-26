"""Stockage SQLite des recherches et des vols scrapés.

Schéma :
    recherches : un scraping d'un trajet (origine, destination, dates, passagers, classe) à un instant donné
    vols       : les vols trouvés pour une recherche (clé étrangère recherche_id)
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from google_flights_scraper import Trajet

DB_PATH = Path(os.environ.get("VOLS_DB") or Path(__file__).with_name("vols.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS recherches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    origine      TEXT NOT NULL,
    destination  TEXT NOT NULL,
    ville_depart TEXT,
    ville_arrivee TEXT,
    date_aller   TEXT NOT NULL,
    date_retour  TEXT NOT NULL DEFAULT '',   -- '' = aller simple
    adultes      INTEGER NOT NULL,
    classe       TEXT NOT NULL,
    scrape_le    TEXT NOT NULL,              -- ISO 8601
    nb_vols      INTEGER NOT NULL,
    prix_min     INTEGER,
    url          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_recherches_trajet
    ON recherches (origine, destination, date_aller, date_retour, adultes, classe, scrape_le);

CREATE TABLE IF NOT EXISTS vols (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    recherche_id     INTEGER NOT NULL REFERENCES recherches(id) ON DELETE CASCADE,
    ville_depart     TEXT,
    ville_arrivee    TEXT,
    compagnies       TEXT,
    operateur        TEXT,
    escales          INTEGER,
    detail_escales   TEXT,
    aeroport_depart  TEXT,
    aeroport_arrivee TEXT,
    date_depart      TEXT,
    heure_depart     TEXT,
    date_arrivee     TEXT,
    heure_arrivee    TEXT,
    duree            TEXT,
    duree_min        INTEGER,
    prix_eur         INTEGER,
    co2_kg           INTEGER,
    remarque         TEXT,
    logo             TEXT,             -- URL du logo de la compagnie (images Google)
    code_compagnie   TEXT,             -- code IATA (VY, AF…) ; plusieurs compagnies : AF/DL
    bagage           INTEGER           -- bagage cabine inclus : 1 = True, 0 = False
);
CREATE INDEX IF NOT EXISTS idx_vols_recherche ON vols (recherche_id);
"""

COLONNES_VOLS = ["ville_depart", "ville_arrivee", "compagnies", "operateur", "escales", "detail_escales",
                 "aeroport_depart", "aeroport_arrivee", "date_depart", "heure_depart", "date_arrivee",
                 "heure_arrivee", "duree", "duree_min", "prix_eur", "co2_kg", "remarque", "logo",
                 "code_compagnie", "bagage"]
CLE_TRAJET = ["origine", "destination", "date_aller", "date_retour", "adultes", "classe"]


def connexion(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30)  # base occupée : attendre jusqu'à 30 s (même base que catalogue.py, en WAL)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    # Mise à niveau des bases créées avant l'ajout des logos
    colonnes = {r[1] for r in con.execute("PRAGMA table_info(vols)")}
    for nom, type_ in (("logo", "TEXT"), ("code_compagnie", "TEXT"), ("bagage", "INTEGER")):
        if nom not in colonnes:  # mise à niveau des bases créées avant l'ajout de ces colonnes
            con.execute(f"ALTER TABLE vols ADD COLUMN {nom} {type_}")
    return con


def _cle(t: Trajet) -> tuple:
    return (t.origine, t.destination, t.date, t.retour or "", t.adultes, t.classe)


def enregistrer(vols: list[dict], path: Path = DB_PATH) -> list[int]:
    """Enregistre les résultats de scrape_many (une recherche par trajet). Renvoie les ids créés."""
    if not vols:
        return []
    df = pd.DataFrame(vols)
    df["date_retour"] = df["date_retour"].fillna("")
    ids = []
    with closing(connexion(path)) as con, con:
        for cle, groupe in df.groupby(CLE_TRAJET + ["scrape_le"], sort=False):
            g0 = groupe.iloc[0]
            cur = con.execute(
                "INSERT OR IGNORE INTO recherches (origine, destination, date_aller, date_retour, adultes, classe, scrape_le,"
                " ville_depart, ville_arrivee, nb_vols, prix_min, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (*[_py(v) for v in cle], g0["ville_depart"], g0["ville_arrivee"],
                 len(groupe), None if pd.isna(groupe["prix_eur"].min()) else int(groupe["prix_eur"].min()),
                 g0["url"]),
            )
            if cur.rowcount == 0:  # déjà enregistrée (même trajet, même horodatage)
                continue
            rid = cur.lastrowid
            ids.append(rid)
            lignes = groupe.reindex(columns=COLONNES_VOLS).astype(object)
            lignes = lignes.where(lignes.notna(), None)  # NaN -> NULL
            con.executemany(
                f"INSERT INTO vols (recherche_id, {', '.join(COLONNES_VOLS)}) "
                f"VALUES (?, {', '.join('?' * len(COLONNES_VOLS))})",
                [(rid, *map(_py, row)) for row in lignes.itertuples(index=False)],
            )
    return ids


def _py(v):
    """Types numpy/pandas -> types Python acceptés par sqlite3."""
    return v.item() if hasattr(v, "item") else v


def _vols_de(con, recherche_ids: list[int]) -> pd.DataFrame:
    if not recherche_ids:
        return pd.DataFrame()
    q = f"""
        SELECT r.origine, r.destination, r.date_aller, r.date_retour, r.adultes, r.classe, r.scrape_le, r.url,
               r.id AS recherche_id, {', '.join('v.' + c for c in COLONNES_VOLS)}
        FROM vols v JOIN recherches r ON r.id = v.recherche_id
        WHERE r.id IN ({', '.join('?' * len(recherche_ids))})
        ORDER BY r.origine, r.destination, v.prix_eur
    """
    return pd.read_sql_query(q, con, params=recherche_ids)


def derniere_recherche(t: Trajet, max_age: timedelta | None = None, path: Path = DB_PATH) -> int | None:
    """Id de la recherche la plus récente pour ce trajet (plus jeune que max_age si fourni)."""
    q = (f"SELECT id FROM recherches WHERE {' AND '.join(c + ' = ?' for c in CLE_TRAJET)}"
         + (" AND scrape_le >= ?" if max_age else "") + " ORDER BY scrape_le DESC LIMIT 1")
    params = list(_cle(t)) + ([(datetime.now() - max_age).isoformat(timespec="seconds")] if max_age else [])
    with closing(connexion(path)) as con:
        row = con.execute(q, params).fetchone()
    return row[0] if row else None


def charger_vols(recherche_ids: list[int], path: Path = DB_PATH) -> pd.DataFrame:
    with closing(connexion(path)) as con:
        return _vols_de(con, recherche_ids)


def historique(trajets: list[Trajet], path: Path = DB_PATH) -> pd.DataFrame:
    """Prix le plus bas et médian à chaque recherche enregistrée pour ces trajets."""
    if not trajets:
        return pd.DataFrame()
    cond = " OR ".join(f"({' AND '.join(c + ' = ?' for c in CLE_TRAJET)})" for _ in trajets)
    q = f"""
        SELECT r.id, r.origine, r.destination, r.ville_depart, r.ville_arrivee, r.date_aller, r.date_retour,
               r.scrape_le, r.nb_vols, r.prix_min,
               (SELECT v.compagnies FROM vols v WHERE v.recherche_id = r.id ORDER BY v.prix_eur LIMIT 1)
                   AS compagnie_moins_chere
        FROM recherches r WHERE {cond} ORDER BY r.scrape_le
    """
    params = [x for t in trajets for x in _cle(t)]
    with closing(connexion(path)) as con:
        return pd.read_sql_query(q, con, params=params)


def liste_recherches(limit: int = 200, path: Path = DB_PATH) -> pd.DataFrame:
    with closing(connexion(path)) as con:
        return pd.read_sql_query(
            "SELECT id, scrape_le, origine, destination, ville_depart, ville_arrivee, date_aller, date_retour,"
            " adultes, classe, nb_vols, prix_min FROM recherches ORDER BY scrape_le DESC, id DESC LIMIT ?",
            con, params=[limit])


def supprimer_recherches(ids: list[int], path: Path = DB_PATH):
    with closing(connexion(path)) as con, con:
        con.executemany("DELETE FROM recherches WHERE id = ?", [(i,) for i in ids])
