"""Actions de tarification (« Dates à baisser » de Data Visualisation) et décisions prises.

Chaque action enregistrée garde la comparaison du jour (tarifs, bagages, baisse proposée, tarif cible) ; la décision
(baissé, gardé, autre), le tarif retenu et un commentaire s'y ajoutent ensuite. Cet historique servira à l'agent IA
de la page Assistant Décisionnel : il y lira les actions et les habitudes de décision.
"""
from contextlib import closing
from datetime import datetime

import pandas as pd

import catalogue

DECISIONS = ["à décider", "baissé", "augmenté", "gardé", "autre"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions_tarifs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    analyse_le      TEXT NOT NULL,      -- jour de l'analyse (une même action réenregistrée le même jour est mise à jour)
    enregistre_le   TEXT NOT NULL,
    ville_depart    TEXT NOT NULL,      -- codes Airfox (PAR, LON…)
    aeroport_depart TEXT NOT NULL DEFAULT '',
    ville_arrivee   TEXT NOT NULL,
    aeroport_arrivee TEXT NOT NULL DEFAULT '',
    date_vol        TEXT NOT NULL,      -- date de départ (AAAA-MM-JJ)
    nuits           INTEGER NOT NULL DEFAULT 0,   -- 0 = aller simple
    compagnie       TEXT NOT NULL,
    bagage_airfox   TEXT,               -- TRUE / FALSE / inconnu
    bagage_google   TEXT,
    situation       TEXT,               -- ex. « Sans bagage des deux côtés »
    tarif_airfox    REAL,
    tarif_google    REAL,
    baisse          REAL,               -- baisse nécessaire pour revenir au tarif cible (Airfox plus cher)
    augmentation    REAL,               -- augmentation possible jusqu'au tarif cible (Airfox moins cher)
    tarif_cible     REAL,
    decision        TEXT NOT NULL DEFAULT 'à décider',
    tarif_retenu    REAL,
    commentaire     TEXT NOT NULL DEFAULT '',
    decide_le       TEXT,
    contexte        TEXT NOT NULL DEFAULT '',   -- saison, vacances, événements… au moment de l'analyse
    UNIQUE (analyse_le, ville_depart, aeroport_depart, ville_arrivee, aeroport_arrivee, date_vol, nuits, compagnie)
);
"""


def connexion():
    con = catalogue.connexion()
    con.executescript(SCHEMA)
    colonnes = {r[1] for r in con.execute("PRAGMA table_info(actions_tarifs)")}  # bases plus anciennes
    if "contexte" not in colonnes:
        con.execute("ALTER TABLE actions_tarifs ADD COLUMN contexte TEXT NOT NULL DEFAULT ''")
    if "augmentation" not in colonnes:
        con.execute("ALTER TABLE actions_tarifs ADD COLUMN augmentation REAL")
    return con


def enregistrer(actions: pd.DataFrame, contexte: str = "") -> int:
    """Enregistre les actions (colonnes : ville_depart, aeroport_depart, ville_arrivee, aeroport_arrivee, date_vol,
    nuits, compagnie, bagage_airfox, bagage_google, situation, tarif_airfox, tarif_google, baisse, augmentation,
    tarif_cible) et
    le contexte de l'analyse (saison, vacances, événements…), pour que l'agent IA comprenne les décisions.
    Une action déjà enregistrée aujourd'hui est mise à jour, sans toucher à la décision prise. Renvoie le nombre."""
    maintenant = datetime.now()
    lignes = [(maintenant.date().isoformat(), maintenant.isoformat(timespec="seconds"), r.ville_depart,
               r.aeroport_depart or "", r.ville_arrivee, r.aeroport_arrivee or "", r.date_vol, int(r.nuits or 0),
               r.compagnie, r.bagage_airfox, r.bagage_google, r.situation, float(r.tarif_airfox),
               float(r.tarif_google), float(r.baisse), float(r.augmentation), float(r.tarif_cible),
               contexte.strip())
              for r in actions.itertuples(index=False)]
    with closing(connexion()) as con, con:
        con.executemany(
            """INSERT INTO actions_tarifs (analyse_le, enregistre_le, ville_depart, aeroport_depart, ville_arrivee,
                   aeroport_arrivee, date_vol, nuits, compagnie, bagage_airfox, bagage_google, situation,
                   tarif_airfox, tarif_google, baisse, augmentation, tarif_cible, contexte)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (analyse_le, ville_depart, aeroport_depart, ville_arrivee, aeroport_arrivee, date_vol,
                            nuits, compagnie)
               DO UPDATE SET enregistre_le = excluded.enregistre_le, bagage_airfox = excluded.bagage_airfox,
                   bagage_google = excluded.bagage_google, situation = excluded.situation,
                   tarif_airfox = excluded.tarif_airfox, tarif_google = excluded.tarif_google,
                   baisse = excluded.baisse, augmentation = excluded.augmentation,
                   tarif_cible = excluded.tarif_cible,
                   contexte = excluded.contexte""", lignes)
    return len(lignes)


def lire(ville_arrivee: str | None = None) -> pd.DataFrame:
    """Actions enregistrées (toutes, ou celles d'une destination), les plus récentes d'abord."""
    with closing(connexion()) as con:
        return pd.read_sql_query(
            "SELECT * FROM actions_tarifs" + (" WHERE ville_arrivee = ?" if ville_arrivee else "")
            + " ORDER BY analyse_le DESC, date_vol, compagnie", con, params=[ville_arrivee] if ville_arrivee else [])


def enregistrer_decisions(decisions: pd.DataFrame) -> int:
    """Met à jour décision, tarif retenu et commentaire (colonnes : id, decision, tarif_retenu, commentaire)."""
    maintenant = datetime.now().isoformat(timespec="seconds")
    with closing(connexion()) as con, con:
        con.executemany(
            "UPDATE actions_tarifs SET decision = ?, tarif_retenu = ?, commentaire = ?, decide_le = ? WHERE id = ?",
            [(r.decision or "à décider", None if pd.isna(r.tarif_retenu) else float(r.tarif_retenu),
              r.commentaire or "", maintenant if (r.decision or "à décider") != "à décider" else None, int(r.id))
             for r in decisions.itertuples(index=False)])
    return len(decisions)
