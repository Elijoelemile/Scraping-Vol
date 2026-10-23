"""Catalogue des prix : prix le plus bas de chaque jour, sur 1 an à partir d'aujourd'hui, pour chaque trajet.

Utilise les appels internes de Google Flights (sans navigateur) :
    GetExploreDestinations : destinations proposées depuis un aéroport (découverte des trajets)
    GetCalendarPicker      : prix le plus bas par jour sur une plage de dates (≤ ~180 jours par appel)

Prix : aller simple, 1 adulte, classe économique, en EUR.

Commandes :
    python catalogue.py decouvrir                 # trajets depuis les aéroports de origines.txt
    python catalogue.py decouvrir CDG DSS LBV     # ... ou depuis ces aéroports
    python catalogue.py collecter                 # met à jour les calendriers (les plus anciens d'abord)
    python catalogue.py collecter --max-trajets 50 --delai 5
    python catalogue.py ajouter CDG PUJ           # ajouter et collecter un trajet précis
    python catalogue.py etat
"""
import argparse
import asyncio
import json
import random
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from db import DB_PATH

BASE = "https://www.google.com/_/FlightsFrontendUi/data/travel.frontend.flights.FlightsFrontendService/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0 Safari/537.36")
HEADERS = {
    "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    "x-same-domain": "1",
    "x-goog-ext-259736195-jspb": '["fr","FR","EUR",1,null,[-120],null,null,1,[]]',
    "origin": "https://www.google.com",
    "referer": "https://www.google.com/travel/flights?hl=fr&gl=FR",
}
# Cookie de consentement RGPD (« Tout refuser ») : évite la redirection vers consent.google.com
SOCS = "CAESNQgjEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjYxMDA2LjA0X3AwGgJmciACGgYIgLqb1gY"
FENETRE_JOURS = 365   # fenêtre demandée : 1 an à partir d'aujourd'hui
TRANCHE_JOURS = 170   # Google refuse les plages de plus de ~181 jours
ORIGINES_FICHIER = Path(__file__).with_name("origines.txt")
LOGOS = "https://www.gstatic.com/flights/airline_logos/70px/"  # logos des compagnies (images de Google)
# Régions de destination interrogées une à une (identifiants Google) : sans elles, « Explorer »
# ne renvoie que la zone autour du départ (ex. depuis Paris : l'Europe uniquement)
REGIONS = {
    "Europe": "/m/02j9z", "Afrique": "/m/0dg3n1", "Moyen-Orient": "/m/04wsz", "Asie": "/m/0j0k",
    "Amérique du Nord": "/m/059g4", "Amérique centrale": "/m/01tzh", "Caraïbes": "/m/0261m",
    "Amérique du Sud": "/m/06n3y", "Australie": "/m/0chghy", "Nouvelle-Zélande": "/m/0ctw_b",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS trajets_catalogue (
    origine       TEXT NOT NULL,
    destination   TEXT NOT NULL,
    ville_depart  TEXT,
    ville_arrivee TEXT,
    pays_arrivee  TEXT,
    decouvert_le  TEXT NOT NULL,
    collecte_le   TEXT,             -- dernière collecte du calendrier (NULL = jamais)
    nb_jours_prix INTEGER,
    prix_min      INTEGER,
    date_prix_min TEXT,
    PRIMARY KEY (origine, destination)
);
CREATE TABLE IF NOT EXISTS villes_google (    -- aéroport -> identifiant Google de sa ville (pour chercher « Paris » = CDG + ORY…)
    iata  TEXT PRIMARY KEY,
    mid   TEXT NOT NULL,
    ville TEXT
);
CREATE TABLE IF NOT EXISTS calendrier_ar (   -- aller-retour, calculé à la demande pour une durée de séjour
    origine     TEXT NOT NULL,
    destination TEXT NOT NULL,
    duree       INTEGER NOT NULL,   -- jours entre l'aller et le retour
    date        TEXT NOT NULL,      -- date de l'aller
    date_retour TEXT NOT NULL,
    prix        INTEGER NOT NULL,   -- prix total aller-retour (EUR, 1 adulte, éco)
    collecte_le TEXT NOT NULL,
    PRIMARY KEY (origine, destination, duree, date)
);
CREATE TABLE IF NOT EXISTS calendrier (
    origine     TEXT NOT NULL,
    destination TEXT NOT NULL,
    date        TEXT NOT NULL,      -- date de départ
    prix        INTEGER NOT NULL,   -- prix le plus bas ce jour-là (EUR, aller simple, 1 adulte, éco)
    collecte_le TEXT NOT NULL,
    PRIMARY KEY (origine, destination, date)
);
CREATE TABLE IF NOT EXISTS calendrier_compagnie (   -- prix le plus bas de chaque jour pour une seule compagnie
    origine     TEXT NOT NULL,
    destination TEXT NOT NULL,
    compagnie   TEXT NOT NULL,      -- code IATA (VY, UX…)
    date        TEXT NOT NULL,
    prix        INTEGER NOT NULL,   -- EUR, aller simple, 1 adulte, éco
    collecte_le TEXT NOT NULL,
    PRIMARY KEY (origine, destination, compagnie, date)
);
CREATE TABLE IF NOT EXISTS vols_jour (   -- recherche d'une date : vol le moins cher de chaque compagnie ce jour-là
    origine     TEXT NOT NULL,      -- aéroport de départ réel (CDG, ORY…)
    destination TEXT NOT NULL,
    date        TEXT NOT NULL,
    compagnie   TEXT NOT NULL,      -- code IATA ; plusieurs compagnies : AF/KL
    nom         TEXT,               -- nom affiché par Google (« Lufthansa City Airlines, Lufthansa »)
    prix        INTEGER NOT NULL,
    bagage      INTEGER,            -- bagage cabine inclus (1/0)
    collecte_le TEXT NOT NULL,
    heure_depart  TEXT,             -- horaires du vol le moins cher de la compagnie
    heure_arrivee TEXT,             -- « 21:30 », « 06:40 (+1) » si l'on arrive le lendemain
    duree         TEXT,
    date_retour   TEXT NOT NULL DEFAULT '',   -- '' = aller simple ; sinon tarif aller-retour
    PRIMARY KEY (origine, destination, date, date_retour, compagnie)
);
CREATE TABLE IF NOT EXISTS compagnies_trajets (     -- bilan de la collecte par compagnie (0 jour = ne dessert pas)
    origine     TEXT NOT NULL,
    destination TEXT NOT NULL,
    compagnie   TEXT NOT NULL,
    collecte_le TEXT NOT NULL,
    nb_jours    INTEGER NOT NULL,
    prix_min    INTEGER,
    date_prix_min TEXT,
    bagage      INTEGER,            -- bagage cabine inclus (1/0) sur le vol le moins cher ; NULL = inconnu
    PRIMARY KEY (origine, destination, compagnie)
);
"""


from google_flights_scraper import Bloque  # noqa: E402  (page « sorry » de Google : adresse IP bloquée)


class ErreurGoogle(Exception):
    """Google n'a renvoyé que des erreurs pour une recherche : on ne sait rien (surtout pas « aucun vol »)."""


class PlageInvalide(Exception):
    """Plage de dates refusée (au-delà de l'horizon de réservation ou trop longue)."""


def connexion(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    # Mise à niveau des bases créées avant l'ajout des horaires
    colonnes = {r[1] for r in con.execute("PRAGMA table_info(vols_jour)")}
    for nom in ("heure_depart", "heure_arrivee", "duree"):
        if nom not in colonnes:
            con.execute(f"ALTER TABLE vols_jour ADD COLUMN {nom} TEXT")
    if "date_retour" not in colonnes:  # la date de retour entre dans la clé : la table est recréée
        with con:
            con.execute("ALTER TABLE vols_jour RENAME TO vols_jour_ancien")
            con.executescript(SCHEMA)
            con.execute("INSERT INTO vols_jour SELECT *, '' FROM vols_jour_ancien")
            con.execute("DROP TABLE vols_jour_ancien")
    return con


# --- Client des appels internes ------------------------------------------------
class GoogleFlights:
    def __init__(self, delai: float = 4.0, connecte: bool = False):
        """connecte=True : utilise la session Google de l'utilisateur (session_google.py), si elle existe,
        pour obtenir les prix réservés aux comptes connectés."""
        import session_google
        self.delai = delai          # pause moyenne entre deux appels (secondes)
        self.appels = 0
        self.echecs_suite = 0       # appels consécutifs restés en erreur après toutes les tentatives
        self._dernier = 0.0
        self._verrou = None         # espace les appels même quand plusieurs recherches tournent en parallèle
        self.at = None              # jeton anti-falsification exigé par Google pour un compte connecté
        biscuits = {"SOCS": SOCS} | (session_google.cookies() if connecte else {})
        self.connecte = "SID" in biscuits
        # délai de 60 s : les trajets rares prennent ~25 s côté Google
        self.client = httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"user-agent": UA},
                                        cookies=biscuits)

    async def ouvrir(self):
        r = await self.client.get("https://www.google.com/travel/flights?hl=fr&gl=FR&curr=EUR")
        if "/sorry/" in str(r.url) or r.status_code == 429:
            raise Bloque()
        if self.connecte:
            m = re.search(r'"SNlM0e":"([^"]+)"', r.text)
            self.at = m.group(1) if m else None
            if not self.at:  # session expirée : on continue sans compte
                self.connecte = False

    async def fermer(self):
        await self.client.aclose()

    async def _attendre(self):
        self._verrou = self._verrou or asyncio.Lock()
        async with self._verrou:  # un seul appel part à la fois : pas de rafale en parallèle
            pause = self.delai * random.uniform(0.7, 1.3) - (time.monotonic() - self._dernier)
            if pause > 0:
                await asyncio.sleep(pause)
            self._dernier = time.monotonic()

    async def rpc(self, nom: str, inner: list, essais: int = 8):
        """Appel avec nouvelles tentatives : ~1 réponse sur 2 est une erreur interne (code 13) passagère."""
        for k in range(essais):
            await (self._attendre() if k == 0 else asyncio.sleep(0.5 + k * 0.3))
            self.appels += 1
            try:
                # garde-fou global : une requête ne doit jamais rester suspendue (ex. après une mise en veille)
                corps = {"f.req": json.dumps([None, json.dumps(inner)])} | ({"at": self.at} if self.at else {})
                r = await asyncio.wait_for(
                    self.client.post(BASE + nom, params={"hl": "fr", "gl": "FR", "rt": "c"},
                                     data=corps, headers=HEADERS),
                    timeout=90)
            except (httpx.TransportError, asyncio.TimeoutError):  # connexion coupée, délai dépassé… : on réessaie
                await asyncio.sleep(5 * (k + 1))
                continue
            if r.status_code == 429 or "/sorry/" in str(r.url):
                raise Bloque()
            if '"wrb.fr",null,"' in r.text:
                self.echecs_suite = 0
                ligne = next(l for l in r.text.split("\n") if l.startswith('[["wrb.fr"'))
                return json.loads(json.loads(ligne)[0][2])
            m = re.search(r'\[(\d+),null,\[\["type', r.text)
            if m and m.group(1) == "3":
                raise PlageInvalide()
        self.echecs_suite += 1
        return None

    @staticmethod
    def _filtre(o: str, d: str, jour: str, type_o=0, compagnies: list[str] | None = None) -> list:
        # compagnies : ne garder que les vols de ces compagnies (codes IATA), comme le filtre « Compagnies » de Google
        segment = [[[[o, type_o]]], [[[d, 0]]], None, 0, compagnies, None, jour,
                   None, None, None, None, None, None, None, 3]
        return [None, None, 2, None, [], 1, [1, 0, 0, 0], None, None, None, None, None, None,
                [segment], None, None, None, 1]

    async def destinations(self, origine: str, region: str | None = None) -> list[dict]:
        """Destinations proposées par « Explorer » depuis un aéroport, dans une région (sinon la zone par défaut,
        souvent limitée au continent de départ)."""
        dest = [[[region, 6]]] if region else []
        inner = [[], None, None,
                 [None, None, 1, None, [], 1, [1, 0, 0, 0], None, None, None, None, None, None,
                  [[[[[origine, 0]]], dest, None, 0], [dest, [[[origine, 0]]], None, 0]], None, None, None, 0],
                 None, 1, None, 0, None, 1, [964, 820]]
        data = await self.rpc("GetExploreDestinations", inner, essais=12)
        if not data or not data[3]:
            return []
        ville_dep = data[6][0][0] if data[6] else None
        return [{"origine": origine, "destination": x[15], "ville_depart": ville_dep,
                 "ville_arrivee": x[2], "pays_arrivee": x[4]}
                for x in data[3][0] if len(x) > 15 and x[15] and x[15] != origine]

    async def toutes_destinations(self, origine: str) -> list[dict]:
        """Zone par défaut + chaque région du monde, sans doublons."""
        vues = {}
        for region in [None, *REGIONS.values()]:
            for x in await self.destinations(origine, region):
                vues.setdefault(x["destination"], x)
        return list(vues.values())

    async def calendrier_ar(self, o: str, d: str, debut: date, fin: date, duree: int) -> dict[str, tuple[str, int]]:
        """{date aller: (date retour, prix aller-retour)} pour un séjour de `duree` jours."""
        def segment(a, b, jour):
            return [[[[a, 0]]], [[[b, 0]]], None, 0, None, None, jour, None, None, None, None, None, None, None, 3]
        filtre = [None, None, 1, None, [], 1, [1, 0, 0, 0], None, None, None, None, None, None,
                  [segment(o, d, debut.isoformat()), segment(d, o, (debut + timedelta(days=duree)).isoformat())],
                  None, None, None, 1]
        data = await self.rpc("GetCalendarPicker",
                              [None, filtre, [debut.isoformat(), fin.isoformat()], None, [duree, duree]])
        if not data or not data[1]:
            return {}
        return {j[0]: (j[1], j[2][0][1]) for j in data[1] if j[1] and j[2] and j[2][0] and j[2][0][1]}

    async def vols(self, t, ville: bool = False, lieux: dict | None = None) -> list[dict]:
        """Liste des vols d'un trajet à une date (GetShoppingResults), au format de google_flights_scraper.

        ville=True : cherche depuis/vers tous les aéroports des deux villes (ex. Paris = CDG + Orly), comme
        quand on tape « Paris » sur Google Flights.
        lieux : {code: (identifiant Google, type)} pour imposer un lieu, ex. un pays (type 5) : t.origine et
        t.destination sont alors des identifiants de pays, et chaque vol porte sa ville et son aéroport réels.
        """
        from google_flights_scraper import CLASSES, search_url

        mids = mids_villes([t.origine, t.destination]) if ville and not lieux else {}
        if ville and not lieux and len(mids) < 2:
            # Identifiant de ville inconnu : une recherche par aéroport l'apprend, puis on recommence par ville
            await self.vols(t, ville=False)
            mids = mids_villes([t.origine, t.destination])

        def lieu(code):
            if lieux and code in lieux:
                return [[[lieux[code][0], lieux[code][1]]]]
            return [[[mids[code], 4]]] if code in mids else [[[code, 0]]]

        def segment(a, b, jour):
            return [lieu(a), lieu(b), None, 0, None, None, jour, None, None, None, None, None, None, None, 3]
        segments = [segment(t.origine, t.destination, t.date)]
        if t.retour:
            segments.append(segment(t.destination, t.origine, t.retour))
        filtre = [None, None, 1 if t.retour else 2, None, [], CLASSES[t.classe], [t.adultes, 0, 0, 0],
                  None, None, None, None, None, None, segments, None, None, None, 1]
        # 4e paramètre à 1 = « Afficher plus de vols » : tous les vols (≈ 100+), pas seulement les ~10 premiers
        requete = [[], filtre, 0, 1, 0, 1]

        def heure(h):
            # Google omet les zéros : [14] = 14:00, [None, 25] = 00:25 (minuit)
            if not h:
                return ""
            return f"{h[0] or 0:02d}:{(h[1] if len(h) > 1 and h[1] else 0):02d}"

        def jour(j):
            return f"{j[0]:04d}-{j[1]:02d}-{j[2]:02d}" if j else ""

        def duree_txt(m):
            return f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h"

        def analyser(data) -> list[dict]:
            # Villes (en français) et identifiants de ville : départ/arrivée en [1], aéroports d'escale en [17]
            villes, a_memoriser = {}, []
            for a in (data[1][0] if data[1] else []) + (data[17] or []):
                try:
                    villes[a[0][0]] = a[2][1]
                    a_memoriser.append((a[0][0], a[2][0], a[2][1]))
                except (TypeError, IndexError):
                    pass
            memoriser_mids(a_memoriser)
            # En recherche par ville, le départ réel (CDG, ORY…) est indiqué par vol dans « aeroport_depart »
            meta = {"origine": t.origine, "destination": t.destination, "date_aller": t.date,
                    "date_retour": t.retour, "type_trajet": "aller-retour" if t.retour else "aller simple",
                    "adultes": t.adultes, "classe": t.classe, "scrape_le": datetime.now().isoformat(timespec="seconds"),
                    "url": search_url(t), "ville_depart": villes.get(t.origine, t.origine),
                    "ville_arrivee": villes.get(t.destination, t.destination)}
            liste = []
            for bloc in (data[2], data[3]):  # meilleurs vols, puis autres vols
                for e in (bloc[0] if bloc and bloc[0] else []):
                    info = e[0]
                    prix = e[1][0][1] if e[1] and e[1][0] and len(e[1][0]) > 1 else None
                    etapes = info[2] or []
                    escales = info[13] or []
                    operateurs = sorted({x[2] for x in etapes if x[2]})
                    co2 = info[22][7] if len(info) > 22 and info[22] and len(info[22]) > 7 and info[22][7] else None
                    # Identité d'un vol : numéros de vol de chaque étape + date et heure de départ
                    cle = (jour(info[4]), heure(info[5]),
                           tuple(f"{x[22][0]}{x[22][1]}" for x in etapes if len(x) > 22 and x[22]))
                    liste.append(meta | {
                        "_cle": cle,
                        "compagnies": " et ".join(info[1]), "operateur": ", ".join(operateurs),
                        "escales": len(escales),
                        "detail_escales": "; ".join(f"{x[5] or x[1]} ({duree_txt(x[0])})" for x in escales),
                        "aeroport_depart": etapes[0][4] if etapes else "",
                        "aeroport_arrivee": etapes[-1][5] if etapes else "",
                        "code_depart": info[3], "code_arrivee": info[6],
                        **({"ville_depart": villes.get(info[3]) or ville_fr(info[3]),
                            "ville_arrivee": villes.get(info[6]) or ville_fr(info[6])} if lieux else {}),
                        "date_depart": jour(info[4]), "heure_depart": heure(info[5]),
                        "date_arrivee": jour(info[7]), "heure_arrivee": heure(info[8]),
                        "duree": duree_txt(info[9]), "duree_min": info[9], "prix_eur": prix,
                        "co2_kg": round(co2 / 1000) if co2 else None,
                        # info[0] = code IATA de la compagnie, ou « multi » pour plusieurs compagnies
                        "logo": f"{LOGOS}{info[0]}.png" if info[0] else "",
                        # Code IATA de la compagnie ; plusieurs compagnies (« multi ») : codes de chaque étape
                        "code_compagnie": (info[0] if info[0] and info[0] != "multi" else
                                           "/".join(dict.fromkeys(x[22][0] for x in etapes
                                                                  if len(x) > 22 and x[22] and x[22][0]))),
                        # Bagage cabine : False si Google indique qu'il n'est pas inclus (tarifs « light »), sinon True
                        "bagage": not (len(e) > 5 and e[5] and e[5][0]),
                    })
            return liste

        # Google ne renvoie pas toujours le prix de chaque vol (« prix total non disponible »), et cela varie d'un
        # appel à l'autre : tant qu'il manque des prix, on relance (2 fois max) et on complète vol par vol.
        resultats = []
        for passe in range(3):
            data = await self.rpc("GetShoppingResults", requete, essais=12)
            if not data:
                if passe == 0:  # aucune réponse exploitable : ce n'est pas « aucun vol »
                    raise ErreurGoogle()
                break
            nouveaux = analyser(data)
            if not resultats:
                resultats = nouveaux
            else:
                connus = {v["_cle"]: v for v in resultats}
                for v in nouveaux:
                    ancien = connus.get(v["_cle"])
                    if ancien is None:
                        resultats.append(v)
                        connus[v["_cle"]] = v
                    elif ancien["prix_eur"] is None and v["prix_eur"] is not None:
                        ancien["prix_eur"] = v["prix_eur"]
            if all(v["prix_eur"] is not None for v in resultats):
                break
        # Comme Google Flights : les vols restés sans prix sont gardés et affichés « Prix non disponible »
        for v in resultats:
            del v["_cle"]
            v["remarque"] = "" if v["prix_eur"] is not None else "Prix non disponible"
        return resultats

    async def calendrier(self, o: str, d: str, debut: date, fin: date,
                         compagnies: list[str] | None = None) -> dict[str, int]:
        """{date: prix} pour les jours entre debut et fin (une seule plage ≤ TRANCHE_JOURS) ;
        compagnies : prix de ces compagnies seulement."""
        data = await self.rpc("GetCalendarPicker",
                              [None, self._filtre(o, d, debut.isoformat(), compagnies=compagnies),
                               [debut.isoformat(), fin.isoformat()]])
        if not data or not data[1]:
            return {}
        return {j[0]: j[2][0][1] for j in data[1] if j[2] and j[2][0] and j[2][0][1]}


# --- Horizon de réservation -------------------------------------------------------
async def horizon(gf: GoogleFlights, o: str, d: str) -> date:
    """Dernière date acceptée par Google (≈ J+328), plafonnée à J+365. Recherche dichotomique."""
    aujourd_hui = date.today()
    lo, hi = 300, FENETRE_JOURS  # lo : accepté (hypothèse prudente), hi : à tester
    async def accepte(n):
        fin = aujourd_hui + timedelta(days=n)
        try:
            await gf.calendrier(o, d, max(aujourd_hui, fin - timedelta(days=30)), fin)
            return True
        except PlageInvalide:
            return False
    if await accepte(hi):
        return aujourd_hui + timedelta(days=hi)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if await accepte(mid) else (lo, mid)
    return aujourd_hui + timedelta(days=lo)


def tranches(debut: date, fin: date):
    while debut <= fin:
        b = min(fin, debut + timedelta(days=TRANCHE_JOURS - 1))
        yield debut, b
        debut = b + timedelta(days=1)


# --- Commandes --------------------------------------------------------------------
def lire_origines() -> list[str]:
    lignes = ORIGINES_FICHIER.read_text(encoding="utf-8").splitlines() if ORIGINES_FICHIER.exists() else []
    return [l.split("#")[0].strip().upper() for l in lignes if l.split("#")[0].strip()]


async def decouvrir(origines: list[str], delai: float, log=print) -> int:
    gf = GoogleFlights(delai)
    await gf.ouvrir()
    nouveaux = 0
    try:
        for i, o in enumerate(origines, 1):
            dests = await gf.toutes_destinations(o)
            maintenant = datetime.now().isoformat(timespec="seconds")
            with closing(connexion()) as con, con:
                avant = con.total_changes
                con.executemany(
                    "INSERT OR IGNORE INTO trajets_catalogue (origine, destination, ville_depart, ville_arrivee,"
                    " pays_arrivee, decouvert_le) VALUES (:origine, :destination, :ville_depart, :ville_arrivee,"
                    " :pays_arrivee, :decouvert_le)", [x | {"decouvert_le": maintenant} for x in dests])
                n = con.total_changes - avant
            nouveaux += n
            log(f"[{i}/{len(origines)}] {o} : {len(dests)} destinations ({n} nouvelles)")
    finally:
        await gf.fermer()
    return nouveaux


async def collecter(max_trajets: int | None, delai: float, pause_blocage_min: float = 30,
                    max_blocages: int = 3, log=print, origines: list[str] | None = None,
                    destinations: list[str] | None = None, seulement_nouveaux: bool = False) -> int:
    """Met à jour les calendriers, en commençant par les trajets jamais ou le plus anciennement collectés.

    origines / destinations : limiter à ces codes IATA ; seulement_nouveaux : ignorer les trajets déjà collectés.
    """
    conditions, params = [], []
    if origines:
        conditions.append(f"origine IN ({','.join('?' * len(origines))})"); params += origines
    if destinations:
        conditions.append(f"destination IN ({','.join('?' * len(destinations))})"); params += destinations
    if seulement_nouveaux:
        conditions.append("collecte_le IS NULL")
    with closing(connexion()) as con:
        trajets = con.execute(
            "SELECT origine, destination FROM trajets_catalogue"
            + (" WHERE " + " AND ".join(conditions) if conditions else "")
            + " ORDER BY collecte_le IS NOT NULL, collecte_le, rowid"
            + (" LIMIT ?" if max_trajets else ""), params + ([max_trajets] if max_trajets else [])).fetchall()
    if not trajets:
        log("Aucun trajet : lancez d'abord « decouvrir ».")
        return 0

    gf = GoogleFlights(delai)
    blocages, faits, t0 = 0, 0, time.monotonic()
    fin = None
    i = 0
    try:
        while i < len(trajets):
            o, d = trajets[i]
            try:
                if fin is None:  # premier appel de la session
                    await gf.ouvrir()
                    fin = await horizon(gf, o, d)
                    log(f"Horizon de réservation : {fin.isoformat()} (fenêtre demandée : J+{FENETRE_JOURS})")
                prix = {}
                for a, b in tranches(date.today(), fin):
                    prix |= await gf.calendrier(o, d, a, b)
                if not prix and gf.echecs_suite >= 2:
                    # Blocage silencieux : Google répond « erreur 13 » à tout, sans page captcha.
                    # Le trajet n'est pas enregistré comme « sans prix » : il sera repris après la pause.
                    raise Bloque()
            except Bloque:
                blocages += 1
                if blocages > max_blocages:
                    log(f"Bloqué par Google {blocages} fois : arrêt. Relancez plus tard, la collecte reprendra ici.")
                    break
                log(f"⚠️ Bloqué par Google (captcha ou erreurs répétées). Pause de {pause_blocage_min:.0f} min puis reprise…")
                await asyncio.sleep(pause_blocage_min * 60)
                gf.delai *= 1.5  # ralentir après un blocage
                continue
            except PlageInvalide:
                fin = None  # l'horizon a changé (passage de minuit) : on le recalcule
                continue
            except Exception as e:  # erreur imprévue sur ce trajet : on le saute, il sera repris au prochain passage
                log(f"⚠️ {o}→{d} ignoré ({type(e).__name__}: {e})")
                i += 1
                continue
            enregistrer_calendrier(o, d, prix)
            faits += 1
            i += 1
            if faits % 5 == 0 or i == len(trajets):
                ecoule = time.monotonic() - t0
                log(f"{faits}/{len(trajets)} trajets · {gf.appels} appels · {ecoule / 60:.1f} min "
                    f"· {faits / ecoule * 3600:.0f} trajets/h")
    finally:
        await gf.fermer()
    return faits


async def refaire(paires: list[tuple[str, str]], delai: float, pause_blocage_min: float = 60,
                  max_blocages: int = 10, log=print) -> list[tuple[str, str]]:
    """Recollecte des trajets restés sans prix. Renvoie ceux qui sont encore vides (sans doute aucun vol)."""
    gf = GoogleFlights(delai)
    vides, fin, blocages, i = [], None, 0, 0
    try:
        while i < len(paires):
            o, d = paires[i]
            try:
                if fin is None:
                    await gf.ouvrir()
                    fin = await horizon(gf, o, d)
                prix = {}
                for a, b in tranches(date.today(), fin):
                    prix |= await gf.calendrier(o, d, a, b)
                if not prix and gf.echecs_suite >= 2:
                    raise Bloque()
            except Bloque:
                blocages += 1
                if blocages > max_blocages:
                    raise
                log(f"⚠️ Bloqué par Google (captcha ou erreurs répétées). Pause de {pause_blocage_min:.0f} min puis reprise…")
                await asyncio.sleep(pause_blocage_min * 60)
                continue
            except PlageInvalide:
                fin = None
                continue
            if prix:
                enregistrer_calendrier(o, d, prix)
            else:
                vides.append((o, d))
            i += 1
    finally:
        await gf.fermer()
    return vides


def _trajets_depuis(depuis: str, origines, destinations) -> list[tuple[str, str, int]]:
    """(origine, destination, nb de jours avec prix) des trajets collectés depuis cet horodatage."""
    with closing(connexion()) as con:
        lignes = con.execute("SELECT origine, destination, COALESCE(nb_jours_prix, 0) FROM trajets_catalogue"
                             " WHERE collecte_le >= ? ORDER BY collecte_le", (depuis,)).fetchall()
    return [l for l in lignes if (not origines or l[0] in origines) and (not destinations or l[1] in destinations)]


async def collecter_par_lots(taille: int, delai: float, pause_blocage_min: float, max_blocages: int,
                             origines=None, destinations=None, vides_depuis: str | None = None, log=print) -> int:
    """Collecte les trajets jamais collectés par lots ; après chaque lot, les trajets sans prix sont refaits une
    fois puis on vérifie le lot avant de passer au suivant. vides_depuis : refaire d'abord les trajets vides
    collectés depuis cet horodatage (ex. reprise d'une collecte précédente)."""
    def verifier(nom, lignes, encore_vides):
        pleins = sum(1 for l in lignes if l[2] > 0) + (len([l for l in lignes if l[2] == 0]) - len(encore_vides))
        log(f"✅ Vérification {nom} : {len(lignes)} trajets, {pleins} avec prix, {len(encore_vides)} sans prix après"
            f" 2 tentatives (aucun vol)" + (f" : {', '.join(f'{o}→{d}' for o, d in encore_vides)}" if encore_vides else ""))

    if vides_depuis:
        lignes = _trajets_depuis(vides_depuis, origines, destinations)
        vides = [(o, d) for o, d, n in lignes if n == 0]
        log(f"Trajets vides déjà collectés depuis {vides_depuis} : {len(vides)} à refaire…")
        verifier("des trajets déjà collectés", lignes, await refaire(vides, delai, pause_blocage_min, max_blocages, log))
    lot, total = 0, 0
    while True:
        debut = datetime.now().isoformat(timespec="seconds")
        faits = await collecter(taille, delai, pause_blocage_min, max_blocages, log, origines, destinations,
                                seulement_nouveaux=True)
        if not faits:
            break
        lot += 1
        total += faits
        lignes = _trajets_depuis(debut, origines, destinations)
        vides = [(o, d) for o, d, n in lignes if n == 0]
        if vides:
            log(f"Lot {lot} : {len(vides)} trajet(s) sans prix, on les refait…")
        verifier(f"lot {lot} ({total} trajets au total)", lignes,
                 await refaire(vides, delai, pause_blocage_min, max_blocages, log))
    log(f"Collecte par lots terminée : {total} trajets en {lot} lot(s).")
    return total


PARIS_MID = "/m/05qtj"  # identifiant Google de la ville de Paris (CDG + Orly + Beauvais…)
PARIS_AEROPORTS = ["CDG", "ORY", "BVA", "XCR"]


async def rechercher_jour(gf: GoogleFlights, d: str, jour: str, origines: list[str], retour: str | None = None) -> bool:
    """Recherche en direct des vols vers d à une date depuis ces aéroports (Paris : une seule recherche « Paris »,
    comme sur Google Flights) ; enregistre le vol le moins cher de chaque compagnie et de chaque départ dans
    vols_jour. retour : date de retour (tarif aller-retour). Renvoie True si des vols ont été trouvés.
    Lève Bloque si Google bloque."""
    from google_flights_scraper import Trajet
    recherches = [("PAR", PARIS_AEROPORTS)] if set(origines) & set(PARIS_AEROPORTS) else []
    recherches += [(o, [o]) for o in origines if o not in PARIS_AEROPORTS]
    meilleurs = {}  # (départ, compagnie) -> vol le moins cher
    for o, codes in recherches:
        try:
            vols = await gf.vols(Trajet(o, d, jour, retour), lieux={"PAR": (PARIS_MID, 4)} if o == "PAR" else None)
        except ErreurGoogle:
            if gf.echecs_suite >= 3:
                raise Bloque()  # erreurs à la suite : blocage silencieux de Google
            raise
        for v in vols:
            # aéroports parfois donnés par leur nom (« Aéroport de Paris-Orly ») : on retrouve le code
            depart = next((c for c in codes if _meme_aeroport(v.get("aeroport_depart"), c)), None)
            if v.get("prix_eur") is None or not depart:
                continue
            cle = (depart, v.get("code_compagnie") or "?")
            if cle not in meilleurs or v["prix_eur"] < meilleurs[cle]["prix_eur"]:
                meilleurs[cle] = v
    cherches = [c for _, codes in recherches for c in codes]
    maintenant = datetime.now().isoformat(timespec="seconds")
    with closing(connexion()) as con, con:
        con.execute(f"DELETE FROM vols_jour WHERE destination = ? AND date = ? AND date_retour = ?"
                    f" AND origine IN ({','.join('?' * (len(cherches) + 1))})", (d, jour, retour or "", *cherches, "-"))
        con.executemany("INSERT INTO vols_jour VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(o, d, jour, c, v.get("compagnies"), int(v["prix_eur"]),
                          None if v.get("bagage") is None else int(v["bagage"]), maintenant,
                          v.get("heure_depart"), _arrivee(v), v.get("duree"), retour or "")
                         for (o, c), v in meilleurs.items()]
                        or [("-", d, jour, "-", None, 0, None, maintenant, None, None, None,
                             retour or "")])  # aucun vol : marqué fait
    return bool(meilleurs)


def _arrivee(v: dict) -> str:
    """Heure d'arrivée, avec « (+1) » si l'on arrive le lendemain (comme sur Google Flights)."""
    try:
        n = (date.fromisoformat(v["date_arrivee"]) - date.fromisoformat(v["date_depart"])).days
    except (KeyError, TypeError, ValueError):
        n = 0
    return f"{v.get('heure_arrivee') or ''} (+{n})" if n > 0 else (v.get("heure_arrivee") or "")


async def actualiser_direct(paires: list[tuple[str, str]], origines: list[str], delai: float = 1.2,
                            on_progress=None, nuits: int | None = None, parallele: int = 4) -> int:
    """Recherches en direct pour des (destination, date) choisies dans l'application, `parallele` à la fois ;
    nuits : aller-retour (retour nuits jours après l'aller). Renvoie le nb avec vols. Lève Bloque si Google bloque."""
    gf = GoogleFlights(delai)
    places = asyncio.Semaphore(parallele)
    bloque = asyncio.Event()
    faites, trouvees = 0, 0

    async def une(d, jour):
        nonlocal faites, trouvees
        async with places:
            if bloque.is_set():
                return
            retour = (date.fromisoformat(jour) + timedelta(days=nuits)).isoformat() if nuits else None
            try:
                avec_vols = await rechercher_jour(gf, d, jour, origines, retour)
            except Bloque:
                bloque.set()  # inutile de continuer : les recherches suivantes seraient bloquées aussi
                return
            except ErreurGoogle:
                avec_vols = False  # rien n'est enregistré pour cette date : elle garde ses anciennes valeurs
            trouvees += avec_vols
            faites += 1
            if on_progress:
                on_progress(faites, len(paires), f"{d} le {jour}")

    try:
        await gf.ouvrir()
        await asyncio.gather(*(une(d, j) for d, j in paires))
    finally:
        await gf.fermer()
    if bloque.is_set():
        raise Bloque()
    return trouvees


async def collecter_jour(jour: str, destinations: list[str], delai: float, pause_blocage_min: float = 60,
                         max_blocages: int = 10, log=print) -> int:
    """Pour une date : une recherche « Paris → destination » par destination (comme sur Google Flights) ;
    garde le vol le moins cher de chaque compagnie et de chaque aéroport de départ. Reprend là où elle s'est arrêtée."""
    with closing(connexion()) as con:
        faites = {r[0] for r in con.execute("SELECT DISTINCT destination FROM vols_jour WHERE date = ?"
                                             " AND date_retour = ''", (jour,))}
    a_faire = [d for d in destinations if d not in faites]
    log(f"{jour} : {len(a_faire)} destinations à rechercher depuis Paris ({len(faites)} déjà faites).")
    gf = GoogleFlights(delai)
    blocages, i, trouvees, t0 = 0, 0, 0, time.monotonic()
    try:
        await gf.ouvrir()
        while i < len(a_faire):
            d = a_faire[i]
            try:
                trouvees += await rechercher_jour(gf, d, jour, PARIS_AEROPORTS)
            except Bloque:
                blocages += 1
                if blocages > max_blocages:
                    log("Bloqué par Google trop de fois : arrêt. Relancez plus tard, la collecte reprendra ici.")
                    break
                log(f"⚠️ Bloqué par Google. Pause de {pause_blocage_min:.0f} min puis reprise…")
                await asyncio.sleep(pause_blocage_min * 60)
                continue
            except Exception as e:
                log(f"⚠️ PAR→{d} ignoré ({type(e).__name__}: {e})")
                i += 1
                continue
            i += 1
            if i % 25 == 0 or i == len(a_faire):
                log(f"✅ {i}/{len(a_faire)} destinations · {trouvees} avec vols · {(time.monotonic() - t0) / 60:.1f} min")
    finally:
        await gf.fermer()
    return i


async def bagage_compagnie(gf: GoogleFlights, o: str, d: str, c: str, jour: str) -> int | None:
    """Bagage cabine inclus (1/0) sur le vol le moins cher de la compagnie ce jour-là ; None si introuvable.
    Les itinéraires avec correspondance comptent aussi (ex. « AF/UX » via Madrid pour Air Europa depuis CDG)."""
    from google_flights_scraper import Trajet
    vols = [v for v in await gf.vols(Trajet(o, d, jour))
            if c in (v.get("code_compagnie") or "").split("/") and v.get("prix_eur") is not None]
    if not vols:
        return None
    seuls = [v for v in vols if v["code_compagnie"] == c]  # de préférence un vol de la compagnie seule
    return int(min(seuls or vols, key=lambda v: v["prix_eur"])["bagage"])


async def completer_bagages(compagnies: list[str], delai: float, log=print) -> int:
    """Recherche le bagage des trajets déjà collectés par compagnie dont le bagage est resté inconnu."""
    with closing(connexion()) as con:
        paires = con.execute("SELECT origine, destination, compagnie, date_prix_min FROM compagnies_trajets"
                             f" WHERE nb_jours > 0 AND bagage IS NULL AND compagnie IN ({','.join('?' * len(compagnies))})",
                             compagnies).fetchall()
    log(f"{len(paires)} bagages inconnus à rechercher.")
    gf = GoogleFlights(delai)
    trouves = 0
    try:
        await gf.ouvrir()
        for i, (o, d, c, jour) in enumerate(paires, 1):
            try:
                bagage = await bagage_compagnie(gf, o, d, c, jour)
            except ErreurGoogle:
                bagage = None
            if bagage is not None:
                trouves += 1
                with closing(connexion()) as con, con:
                    con.execute("UPDATE compagnies_trajets SET bagage = ? WHERE origine = ? AND destination = ?"
                                " AND compagnie = ?", (bagage, o, d, c))
            if i % 25 == 0 or i == len(paires):
                log(f"✅ {i}/{len(paires)} recherchés · {trouves} bagages trouvés")
    finally:
        await gf.fermer()
    return trouves


async def collecter_compagnies(compagnies: list[str], delai: float, pause_blocage_min: float = 60,
                               max_blocages: int = 10, origines=None, destinations=None, log=print) -> int:
    """Calendrier sur 1 an de chaque compagnie, pour les trajets du catalogue qui ont des prix, plus le bagage
    cabine (vol le moins cher de la compagnie, à sa date la moins chère). Reprend là où elle s'est arrêtée."""
    with closing(connexion()) as con:
        trajets = con.execute("SELECT origine, destination FROM trajets_catalogue WHERE nb_jours_prix > 0"
                              " ORDER BY prix_min").fetchall()
        faits = {tuple(r) for r in con.execute("SELECT origine, destination, compagnie FROM compagnies_trajets")}
    paires = [(o, d, c) for o, d in trajets for c in compagnies
              if (not origines or o in origines) and (not destinations or d in destinations) and (o, d, c) not in faits]
    log(f"{len(paires)} calendriers par compagnie à collecter ({', '.join(compagnies)}).")
    gf = GoogleFlights(delai)
    fin, blocages, i, servis, t0 = None, 0, 0, 0, time.monotonic()
    try:
        while i < len(paires):
            o, d, c = paires[i]
            try:
                if fin is None:
                    await gf.ouvrir()
                    fin = await horizon(gf, o, d)
                prix = {}
                for a, b in tranches(date.today(), fin):
                    prix |= await gf.calendrier(o, d, a, b, [c])
                if not prix and gf.echecs_suite >= 2:
                    raise Bloque()  # blocage silencieux (erreur 13 répétée)
                bagage = await bagage_compagnie(gf, o, d, c, min(prix, key=prix.get)) if prix else None
            except Bloque:
                blocages += 1
                if blocages > max_blocages:
                    log(f"Bloqué par Google {blocages} fois : arrêt. Relancez plus tard, la collecte reprendra ici.")
                    break
                log(f"⚠️ Bloqué par Google (captcha ou erreurs répétées). Pause de {pause_blocage_min:.0f} min puis reprise…")
                await asyncio.sleep(pause_blocage_min * 60)
                gf.delai *= 1.5
                continue
            except PlageInvalide:
                fin = None
                continue
            except Exception as e:
                log(f"⚠️ {o}→{d} {c} ignoré ({type(e).__name__}: {e})")
                i += 1
                continue
            maintenant = datetime.now().isoformat(timespec="seconds")
            meilleur = min(prix.items(), key=lambda kv: kv[1]) if prix else (None, None)
            with closing(connexion()) as con, con:
                con.execute("DELETE FROM calendrier_compagnie WHERE origine = ? AND destination = ? AND compagnie = ?",
                            (o, d, c))
                con.executemany("INSERT INTO calendrier_compagnie VALUES (?, ?, ?, ?, ?, ?)",
                                [(o, d, c, j, p, maintenant) for j, p in prix.items()])
                con.execute("INSERT OR REPLACE INTO compagnies_trajets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (o, d, c, maintenant, len(prix), meilleur[1], meilleur[0], bagage))
            servis += bool(prix)
            i += 1
            if i % 50 == 0 or i == len(paires):
                ecoule = time.monotonic() - t0
                log(f"✅ Vérification {i}/{len(paires)} calendriers · {servis} avec prix (la compagnie dessert le "
                    f"trajet) · {i - servis} sans · {gf.appels} appels · {ecoule / 60:.1f} min")
    finally:
        await gf.fermer()
    return i


def lire_liste(path: str) -> list[tuple[str, str]]:
    """Fichier « CODE;Ville » (une ligne par aéroport, # = commentaire), du plus au moins prioritaire."""
    lignes = Path(path).read_text(encoding="utf-8").splitlines()
    res = []
    for l in lignes:
        l = l.split("#")[0].strip()
        if l:
            code, _, ville = l.partition(";")
            res.append((code.strip().upper(), ville.strip() or code.strip().upper()))
    return res


def croiser(fichier_origines: str, fichier_destinations: str, pays_arrivee: str = "") -> int:
    """Ajoute au catalogue tous les trajets origines × destinations (sans collecter), les plus prioritaires
    d'abord (rang de l'origine + rang de la destination). Renvoie le nombre de nouveaux trajets."""
    from pays import pays_aeroport  # pays de chaque arrivée quand la liste en couvre plusieurs
    origines, destinations = lire_liste(fichier_origines), lire_liste(fichier_destinations)
    paires = sorted(((i + j, i, o, vo, d, vd) for i, (o, vo) in enumerate(origines)
                     for j, (d, vd) in enumerate(destinations) if o != d))
    maintenant = datetime.now().isoformat(timespec="seconds")
    with closing(connexion()) as con, con:
        avant = con.total_changes
        con.executemany("INSERT OR IGNORE INTO trajets_catalogue (origine, destination, ville_depart, ville_arrivee,"
                        " pays_arrivee, decouvert_le) VALUES (?, ?, ?, ?, ?, ?)",
                        [(o, d, vo, vd, pays_arrivee or pays_aeroport(d), maintenant) for _, _, o, vo, d, vd in paires])
        return con.total_changes - avant


async def ajouter(o: str, d: str, collecter_maintenant=True) -> int:
    """Ajoute un trajet précis au catalogue (s'il n'y est pas) et collecte son calendrier. Renvoie le nb de jours."""
    import airportsdata
    villes = airportsdata.load("IATA")
    with closing(connexion()) as con, con:
        con.execute("INSERT OR IGNORE INTO trajets_catalogue (origine, destination, ville_depart, ville_arrivee,"
                    " pays_arrivee, decouvert_le) VALUES (?, ?, ?, ?, ?, ?)",
                    (o, d, villes.get(o, {}).get("city"), villes.get(d, {}).get("city"),
                     villes.get(d, {}).get("country"), datetime.now().isoformat(timespec="seconds")))
    if not collecter_maintenant:
        return 0
    gf = GoogleFlights(delai=1.0)
    try:
        await gf.ouvrir()
        fin = await horizon(gf, o, d)
        prix = {}
        for a, b in tranches(date.today(), fin):
            prix |= await gf.calendrier(o, d, a, b)
    finally:
        await gf.fermer()
    enregistrer_calendrier(o, d, prix)
    return len(prix)


async def collecter_ar(o: str, d: str, duree: int) -> int:
    """Calendrier aller-retour d'un trajet pour une durée de séjour, sur 1 an. Renvoie le nb de jours avec prix."""
    gf = GoogleFlights(delai=1.0)
    try:
        await gf.ouvrir()
        fin = await horizon(gf, o, d)
        prix = {}
        for a, b in tranches(date.today(), fin):
            prix |= await gf.calendrier_ar(o, d, a, b, duree)
    finally:
        await gf.fermer()
    maintenant = datetime.now().isoformat(timespec="seconds")
    with closing(connexion()) as con, con:
        con.execute("DELETE FROM calendrier_ar WHERE origine = ? AND destination = ? AND duree = ?", (o, d, duree))
        con.executemany("INSERT INTO calendrier_ar VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(o, d, duree, j, r, p, maintenant) for j, (r, p) in prix.items()])
    return len(prix)


_VILLES_FR = None


def ville_fr(code: str) -> str:
    """Nom de ville d'un aéroport : listes/*.txt (en français), sinon base airportsdata (en anglais)."""
    global _VILLES_FR
    if _VILLES_FR is None:
        _VILLES_FR = {}
        for f in sorted(Path(__file__).with_name("listes").glob("*.txt")):
            _VILLES_FR.update(dict(lire_liste(str(f))))
        import airportsdata
        for c, a in airportsdata.load("IATA").items():
            _VILLES_FR.setdefault(c, a["city"] or c)
    return _VILLES_FR.get(code, code)


# Types de lieux : autocomplétion Google (1 aéroport, 3 ville, 4 pays/région) -> recherche de vols (0, 4, 5)
TYPE_RECHERCHE = {1: 0, 3: 4, 4: 5}


async def suggestions(texte: str) -> list[dict]:
    """Autocomplétion de Google Flights : pays, villes et aéroports correspondant au texte tapé."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"user-agent": UA},
                                 cookies={"SOCS": SOCS}) as client:
        r = await client.post("https://www.google.com/_/FlightsFrontendUi/data/batchexecute",
                              params={"rpcids": "H028ib", "hl": "fr", "gl": "FR", "rt": "c"},
                              data={"f.req": json.dumps([[["H028ib", json.dumps([texte, [1, 2, 3, 5, 4], None, [1, 1, 1], 1]),
                                                           None, "generic"]]])},
                              headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8", "x-same-domain": "1"})
    if r.status_code == 429 or "/sorry/" in str(r.url):
        raise Bloque()
    ligne = next((l for l in r.text.split("\n") if l.startswith('[["wrb.fr","H028ib"')), None)
    if not ligne or not json.loads(ligne)[0][2]:
        return []
    res = []
    for groupe in json.loads(json.loads(ligne)[0][2])[0]:
        lieu = groupe[0]  # [type, libellé, nom, description, identifiant, …]
        res.append({"type": lieu[0], "libelle": lieu[1], "nom": lieu[2], "description": lieu[3] or "",
                    "mid": lieu[4]})
    return res


async def resoudre_pays(texte: str) -> dict | None:
    """Pays correspondant au texte (ex. « Thaïlande » -> {nom, mid}), d'après l'autocomplétion de Google."""
    import unicodedata

    def norm(x):
        return unicodedata.normalize("NFKD", x or "").encode("ascii", "ignore").decode().lower().strip()
    pays = [s for s in await suggestions(texte) if s["type"] == 4 and s["mid"]]
    if not pays:
        return None
    exact = [p for p in pays if norm(p["nom"]) == norm(texte)]
    return (exact or pays)[0]


async def vols_pays(pays_depart: dict, pays_arrivee: dict, date_aller: str, date_retour: str | None = None,
                    adultes: int = 1, classe: str = "eco") -> list[dict]:
    """Tous les vols d'un pays vers un autre à une date (une seule recherche Google, tous aéroports confondus).

    pays_depart / pays_arrivee : {"mid", "nom"} d'un pays (type 5 par défaut), ou d'un aéroport avec
    {"mid": code IATA, "type": 0, "nom": ville} pour chercher d'un pays vers un aéroport précis.
    """
    from google_flights_scraper import Trajet
    t = Trajet(pays_depart["mid"], pays_arrivee["mid"], date_aller, date_retour, adultes, classe)
    lieux = {t.origine: (pays_depart["mid"], pays_depart.get("type", 5)),
             t.destination: (pays_arrivee["mid"], pays_arrivee.get("type", 5))}
    gf = GoogleFlights(delai=1.0)
    try:
        await gf.ouvrir()
        vols = await gf.vols(t, lieux=lieux)
    finally:
        await gf.fermer()
    for v in vols:
        v["pays_depart"], v["pays_arrivee"] = pays_depart["nom"], pays_arrivee["nom"]
        v["source"] = "appel direct (recherche pays → pays)"
    return vols


def mids_villes(codes: list[str]) -> dict[str, str]:
    with closing(connexion()) as con:
        return dict(con.execute(f"SELECT iata, mid FROM villes_google WHERE iata IN ({','.join('?' * len(codes))})",
                                codes).fetchall())


def memoriser_mids(lignes: list[tuple]):
    lignes = [l for l in lignes if l[0] and l[1] and str(l[1]).startswith("/")]
    if lignes:
        with closing(connexion()) as con, con:
            con.executemany("INSERT OR REPLACE INTO villes_google (iata, mid, ville) VALUES (?, ?, ?)", lignes)


async def rechercher_vols(trajets: list, on_progress=None, ville: bool = False, compte: bool = False) -> list[dict]:
    """Recherche rapide (sans navigateur) des vols de plusieurs trajets ; compte=True : avec la session Google."""
    gf = GoogleFlights(delai=1.0, connecte=compte)
    resultats = []
    try:
        try:
            await gf.ouvrir()
        except Bloque:  # Google bloque l'adresse IP (captcha) : aucune recherche possible pour l'instant
            if on_progress:
                on_progress(len(trajets), len(trajets), "ÉCHEC : Google bloque temporairement les recherches (captcha)")
            raise
        for i, t in enumerate(trajets, 1):
            label = f"[{i}/{len(trajets)}] {t.origine}→{t.destination} {t.date}" + (f"↔{t.retour}" if t.retour else "")
            try:
                vols = await gf.vols(t, ville=ville)
                msg = f"{label} : {len(vols)} vols"
                resultats += vols
            except Exception as e:  # Bloque compris : on le signale et on continue
                msg = f"{label} : ÉCHEC ({type(e).__name__})"
            if on_progress:
                on_progress(i, len(trajets), msg)
    finally:
        await gf.fermer()
    return resultats


async def rechercher(trajets: list, on_progress=None, ville: bool = False, compte: bool = False) -> list[dict]:
    """Recherche des vols.

    compte=True et session Google enregistrée : lit la page Google Flights dans le profil Chrome connecté
    (exactement les prix que Google montre à ce compte, plus lent). Sinon : appel direct rapide, et les
    trajets restés sans résultat passent par le navigateur en secours.
    """
    import session_google
    from google_flights_scraper import scrape_many
    if compte and session_google.est_connecte():
        mids = {}
        if ville:
            codes = {c for t in trajets for c in (t.origine, t.destination)}
            if codes - set(mids_villes(list(codes))):  # identifiants de ville inconnus : un appel direct les apprend
                gf = GoogleFlights(delai=1.0)
                try:
                    await gf.ouvrir()
                    for t in trajets:
                        if {t.origine, t.destination} - set(mids_villes([t.origine, t.destination])):
                            await gf.vols(t)
                finally:
                    await gf.fermer()
            mids = mids_villes(list(codes))
        try:
            vols = await scrape_many(trajets, tous=True, pause=(1.0, 2.0), on_progress=on_progress,
                                     profil=session_google.PROFIL / "chrome", chrome=session_google.CHROME, mids=mids)
        except Bloque:  # captcha : l'appel direct serait bloqué lui aussi
            if on_progress:
                on_progress(len(trajets), len(trajets), "ÉCHEC : Google bloque temporairement les recherches (captcha)")
            raise
        except Exception:  # profil occupé (fenêtre Chrome ouverte), etc.
            vols = []
        for v in vols:
            v["source"] = "page Google Flights (compte connecté)"
        trouves = {(v["origine"], v["destination"], v["date_aller"], v["date_retour"]) for v in vols}
        restants = [t for t in trajets if (t.origine, t.destination, t.date, t.retour) not in trouves]
        if restants:  # page vide ou en erreur : appel direct en secours
            secours = await rechercher_vols(restants, on_progress, ville)
            for v in secours:
                v["source"] = "appel direct (secours : la page Google n'a pas répondu)"
            vols += secours
        return vols
    vols = await rechercher_vols(trajets, on_progress, ville, compte)
    for v in vols:
        v["source"] = "appel direct (sans compte)"
    trouves = {(v["origine"], v["destination"], v["date_aller"], v["date_retour"]) for v in vols}
    manquants = [t for t in trajets if (t.origine, t.destination, t.date, t.retour) not in trouves]
    if manquants:
        if on_progress:
            on_progress(len(trajets), len(trajets), f"{len(manquants)} trajet(s) sans résultat : nouvel essai par le navigateur…")
        vols += await scrape_many(manquants, pause=(2.0, 4.0))
    return vols


def calendrier_ar_trajet(o: str, d: str, duree: int, max_age: timedelta | None = None) -> pd.DataFrame:
    """Calendrier aller-retour enregistré (vide s'il n'existe pas ou est plus vieux que max_age)."""
    q = ("SELECT date, date_retour, prix, collecte_le FROM calendrier_ar WHERE origine = ? AND destination = ?"
         " AND duree = ? AND date >= ?")
    params = [o, d, duree, date.today().isoformat()]
    if max_age:
        q += " AND collecte_le >= ?"
        params.append((datetime.now() - max_age).isoformat(timespec="seconds"))
    with closing(connexion()) as con:
        return pd.read_sql_query(q + " ORDER BY date", con, params=params)


def enregistrer_calendrier(o: str, d: str, prix: dict[str, int]):
    maintenant = datetime.now().isoformat(timespec="seconds")
    aujourd_hui = date.today().isoformat()
    with closing(connexion()) as con, con:
        # on remplace le calendrier du trajet (les dates passées et les jours sans vol disparaissent)
        con.execute("DELETE FROM calendrier WHERE origine = ? AND destination = ?", (o, d))
        con.executemany("INSERT INTO calendrier VALUES (?, ?, ?, ?, ?)",
                        [(o, d, j, p, maintenant) for j, p in prix.items() if j >= aujourd_hui])
        meilleur = min(prix.items(), key=lambda kv: kv[1]) if prix else (None, None)
        con.execute("UPDATE trajets_catalogue SET collecte_le = ?, nb_jours_prix = ?, prix_min = ?, date_prix_min = ?"
                    " WHERE origine = ? AND destination = ?",
                    (maintenant, len(prix), meilleur[1], meilleur[0], o, d))


# --- Lecture (pour l'application) ----------------------------------------------------
def etat() -> dict:
    with closing(connexion()) as con:
        r = con.execute("SELECT COUNT(*), COUNT(collecte_le), COUNT(DISTINCT origine), MIN(collecte_le),"
                        " MAX(collecte_le) FROM trajets_catalogue").fetchone()
        jours = con.execute("SELECT COUNT(*) FROM calendrier").fetchone()[0]
    return {"trajets": r[0], "trajets_collectes": r[1], "origines": r[2], "plus_ancienne": r[3],
            "plus_recente": r[4], "prix_journaliers": jours}


def trajets(origine: str | None = None) -> pd.DataFrame:
    with closing(connexion()) as con:
        return pd.read_sql_query(
            "SELECT * FROM trajets_catalogue" + (" WHERE origine = ?" if origine else "")
            + " ORDER BY prix_min IS NULL, prix_min", con, params=[origine] if origine else [])


def calendrier_combine(origines: list[str], destinations: str | list[str]) -> pd.DataFrame:
    """Calendrier « tous aéroports confondus » : chaque jour, le prix le plus bas parmi les trajets
    origines × destinations, avec le trajet (origine, destination) correspondant."""
    destinations = [destinations] if isinstance(destinations, str) else list(destinations)
    if not origines or not destinations:
        return pd.DataFrame(columns=["date", "prix", "origine", "destination", "collecte_le"])
    with closing(connexion()) as con:
        cal = pd.read_sql_query(
            f"SELECT date, prix, origine, destination, collecte_le FROM calendrier WHERE date >= ?"
            f" AND destination IN ({','.join('?' * len(destinations))})"
            f" AND origine IN ({','.join('?' * len(origines))})",
            con, params=[date.today().isoformat(), *destinations, *origines])
    if cal.empty:
        return cal
    return (cal.sort_values(["date", "prix"]).drop_duplicates("date").reset_index(drop=True))


# Codes ville utilisés par Airfox -> aéroports collectés (Airfox part de « PAR » = tous les aéroports de Paris)
VILLES_AIRFOX = {"PAR": PARIS_AEROPORTS, "LON": ["LHR", "LGW", "STN", "LTN", "LCY", "SEN"],
                 "MIL": ["MXP", "LIN", "BGY"], "NYC": ["JFK", "EWR", "LGA"], "RIO": ["GIG", "SDU"],
                 "ROM": ["FCO", "CIA"], "TYO": ["HND", "NRT"], "YMQ": ["YUL"], "YTO": ["YYZ", "YTZ"],
                 "IZM": ["ADB"], "DKR": ["DSS"]}


def tableau_google_airfox(villes_arrivee: list[str], nuits: int | None = None) -> pd.DataFrame:
    """Données Google Flights du catalogue au format du fichier Airfox, pour la jointure :
    VilleDepart (PAR), VilleArrivee (code Airfox), DateAller, CodeTransporteur, Tarif, FlagBagageInclus.
    Une ligne par date et compagnie : le tarif le plus bas parmi les aéroports de Paris (aller simple, 1 adulte).
    Sources : calendriers par compagnie (collecter_compagnies) et recherches d'une date (vols_jour, plus récentes).
    nuits : tarifs aller-retour (retour nuits jours après l'aller), issus des recherches d'une date seulement."""
    vers_airfox = {a: v for v, aeroports in VILLES_AIRFOX.items() for a in aeroports if v != "PAR"}
    aeroports = sorted({a for v in villes_arrivee for a in VILLES_AIRFOX.get(v, [v])})
    if not aeroports:
        return pd.DataFrame()
    marques = ",".join("?" * len(aeroports))
    paris = ",".join("?" * len(PARIS_AEROPORTS))
    with closing(connexion()) as con:
        cal = pd.read_sql_query(
            f"SELECT c.origine, c.destination, c.date, c.compagnie, c.prix, t.bagage, c.collecte_le"
            f" FROM calendrier_compagnie c JOIN compagnies_trajets t ON t.origine = c.origine"
            f" AND t.destination = c.destination AND t.compagnie = c.compagnie"
            f" WHERE c.origine IN ({paris}) AND c.destination IN ({marques}) AND c.date >= ?",
            con, params=[*PARIS_AEROPORTS, *aeroports, date.today().isoformat()])
        jour = pd.read_sql_query(
            f"SELECT origine, destination, date, date_retour, compagnie, prix, bagage, collecte_le FROM vols_jour"
            f" WHERE origine IN ({paris}) AND destination IN ({marques}) AND date >= ?",
            con, params=[*PARIS_AEROPORTS, *aeroports, date.today().isoformat()])
    jour = jour[jour["date_retour"] == _retours(jour["date"], nuits)].drop(columns="date_retour")
    if nuits:  # pas de calendrier aller-retour par compagnie : seulement les recherches d'une date
        cal = cal.iloc[0:0]
    cal["Source"] = "calendrier de la compagnie"
    jour["Source"] = "recherche du jour"
    df = pd.concat([cal, jour], ignore_index=True)
    df = df[df["compagnie"] != "-"]  # « - » : recherche faite, aucun vol
    if df.empty:
        return df
    df["VilleArrivee"] = df["destination"].map(lambda a: vers_airfox.get(a, a))
    # Pour chaque date, compagnie et ville : la source la plus récente, puis le tarif le plus bas de Paris
    df = df.sort_values(["collecte_le", "prix"], ascending=[False, True])
    df = df[df["collecte_le"].str[:10] == df.groupby(["VilleArrivee", "date", "compagnie"])["collecte_le"]
            .transform("max").str[:10]]
    df = df.sort_values("prix").drop_duplicates(["VilleArrivee", "date", "compagnie"])
    return pd.DataFrame({
        "VilleDepart": "PAR", "VilleArrivee": df["VilleArrivee"], "DateAller": df["date"],
        **({"DateRetour": _retours(df["date"], nuits), "Duree": nuits} if nuits else {}),
        "CodeTransporteur": df["compagnie"], "Tarif": df["prix"].astype(float),
        "FlagBagageInclus": df["bagage"].map({1: True, 0: False}),
        "AeroportDepart": df["origine"], "AeroportArrivee": df["destination"],
        "Source": df["Source"], "CollecteLe": df["collecte_le"],
    }).sort_values(["VilleArrivee", "DateAller", "CodeTransporteur"], ignore_index=True)


def _retours(dates: pd.Series, nuits: int | None) -> pd.Series:
    """Date de retour attendue pour chaque date d'aller ('' en aller simple)."""
    if not nuits:
        return pd.Series("", index=dates.index)
    return (pd.to_datetime(dates) + pd.Timedelta(days=nuits)).dt.strftime("%Y-%m-%d")


def calendrier_ar_origines(origines: list[str], nuits: int) -> pd.DataFrame:
    """Calendriers aller-retour (toutes compagnies) déjà collectés pour ces départs et cette durée de séjour."""
    with closing(connexion()) as con:
        return pd.read_sql_query(
            f"SELECT origine, destination, date, prix AS prix_ar, collecte_le AS collecte_ar FROM calendrier_ar"
            f" WHERE duree = ? AND origine IN ({','.join('?' * len(origines))})", con, params=[nuits, *origines])


def vols_jour_tableau(origines: list[str], nuits: int | None = None) -> pd.DataFrame:
    """Recherches d'une date (vols_jour) en aller simple, ou en aller-retour de `nuits` nuits."""
    with closing(connexion()) as con:
        vj = pd.read_sql_query(
            f"SELECT * FROM vols_jour WHERE origine IN ({','.join('?' * len(origines))}) OR origine = '-'",
            con, params=origines)  # « - » : recherche faite sans aucun vol
    return vj[vj["date_retour"] == _retours(vj["date"], nuits)]


def moins_cher_du_jour(origines: list[str], nuits: int | None = None) -> pd.DataFrame:
    """Recherches d'une date (vols_jour) : compagnie du vol le moins cher par départ, destination et date."""
    vj = vols_jour_tableau(origines, nuits)
    vj = vj[vj["compagnie"] != "-"][["origine", "destination", "date", "compagnie", "nom", "prix", "collecte_le",
                                     "heure_depart", "heure_arrivee", "duree", "bagage"]]
    return (vj.sort_values("prix").drop_duplicates(["origine", "destination", "date"])
              .rename(columns={"compagnie": "code_moins_cher", "nom": "nom_moins_cher", "prix": "prix_moins_cher",
                               "collecte_le": "collecte_moins_cher", "bagage": "bagage_moins_cher"}))


def compagnies_collectees() -> list[str]:
    """Codes des compagnies dont les calendriers ont été collectés (collecter_compagnies)."""
    with closing(connexion()) as con:
        return [r[0] for r in con.execute("SELECT DISTINCT compagnie FROM compagnies_trajets ORDER BY compagnie")]


def _meme_aeroport(nom: str | None, code: str) -> bool:
    """Le nom affiché par Google (« Aéroport de Paris-Charles de Gaulle ») désigne-t-il l'aéroport de ce code ?"""
    if not nom or len(nom) == 3:
        return nom == code
    import airportsdata
    global _AEROPORTS
    _AEROPORTS = _AEROPORTS or airportsdata.load("IATA")
    mots = re.findall(r"[a-z]{4,}", (_AEROPORTS.get(code, {}).get("name") or "").lower())
    mots = [m for m in mots if m not in {"airport", "international", "paris", "regional", "city"}]
    return bool(mots) and any(m in nom.lower() for m in mots)


_AEROPORTS = None


def calendrier_compagnies(origine: str, codes: list[str]) -> pd.DataFrame:
    """Toutes les dates du catalogue au départ d'un aéroport, une ligne par date et par compagnie (codes IATA).

    prix_compagnie / bagage : vol le moins cher de cette compagnie ce jour-là dans les recherches détaillées
    enregistrées (aller simple) ; vides quand aucune recherche ne l'a encore trouvé.
    """
    from google_flights_scraper import tableau_comparateur
    with closing(connexion()) as con:
        cal = pd.read_sql_query(
            "SELECT c.destination, t.ville_arrivee, c.date, c.prix, c.collecte_le FROM calendrier c"
            " JOIN trajets_catalogue t ON t.origine = c.origine AND t.destination = c.destination"
            " WHERE c.origine = ? AND c.date >= ?", con, params=[origine, date.today().isoformat()])
        vols = pd.read_sql_query(
            "SELECT v.*, r.destination AS dest_recherche FROM vols v JOIN recherches r ON r.id = v.recherche_id"
            " WHERE (v.aeroport_depart = ? OR length(v.aeroport_depart) > 3) AND r.date_retour = ''",
            con, params=[origine])
    # Page Google (compte connecté) : aéroports enregistrés par leur nom (« Aéroport de Paris-Orly ») ;
    # on garde le code recherché seulement si le nom lui correspond (une recherche « Paris » couvre CDG et Orly)
    vols = vols[[_meme_aeroport(a, origine) for a in vols["aeroport_depart"]]]
    vols["code_arrivee"] = [a if len(a or "") == 3 else d if _meme_aeroport(a, d) else None
                            for a, d in zip(vols["aeroport_arrivee"], vols["dest_recherche"])]
    vols = vols[vols["code_arrivee"].notna()]
    lignes = cal.merge(pd.DataFrame({"code_compagnie": codes}), how="cross")
    if not vols.empty:
        vols["date_retour"] = ""
        vols["code_compagnie"] = tableau_comparateur(vols)["Code compagnie"]  # code déduit du logo ou du nom
        vols["prix_eur"] = pd.to_numeric(vols["prix_eur"], errors="coerce")
        vols = (vols[vols["code_compagnie"].isin(codes) & vols["prix_eur"].notna()]
                .sort_values("prix_eur")
                .drop_duplicates(["code_arrivee", "date_depart", "code_compagnie"])
                .rename(columns={"code_arrivee": "destination", "date_depart": "date", "prix_eur": "prix_compagnie"}))
        lignes = lignes.merge(vols[["destination", "date", "code_compagnie", "prix_compagnie", "bagage"]],
                              how="left", on=["destination", "date", "code_compagnie"])
    else:
        lignes["prix_compagnie"] = lignes["bagage"] = None
    # Collecte par compagnie (prioritaire) : prix du jour de la compagnie, bagage de son vol le moins cher ;
    # collecte_compagnie = le trajet a été collecté pour cette compagnie (pas de prix ce jour-là = pas de vol)
    with closing(connexion()) as con:
        cc = pd.read_sql_query("SELECT destination, compagnie AS code_compagnie, date, prix AS prix_cc"
                               " FROM calendrier_compagnie WHERE origine = ?", con, params=[origine])
        ct = pd.read_sql_query("SELECT destination, compagnie AS code_compagnie, bagage AS bagage_cc,"
                               " 1 AS collecte_compagnie FROM compagnies_trajets WHERE origine = ?", con, params=[origine])
    lignes = (lignes.merge(cc, how="left", on=["destination", "code_compagnie", "date"])
                    .merge(ct, how="left", on=["destination", "code_compagnie"]))
    lignes["prix_compagnie"] = lignes["prix_cc"].combine_first(pd.to_numeric(lignes["prix_compagnie"], errors="coerce"))
    lignes["bagage"] = lignes["bagage_cc"].where(lignes["prix_cc"].notna() & lignes["bagage_cc"].notna(),
                                                 pd.to_numeric(lignes["bagage"], errors="coerce"))
    lignes["collecte_compagnie"] = lignes["collecte_compagnie"].fillna(0).astype(bool)
    lignes = lignes.drop(columns=["prix_cc", "bagage_cc"])
    return lignes.sort_values(["ville_arrivee", "date", "code_compagnie"]).reset_index(drop=True)


def calendrier_trajet(o: str, d: str) -> pd.DataFrame:
    with closing(connexion()) as con:
        return pd.read_sql_query("SELECT date, prix, collecte_le FROM calendrier WHERE origine = ? AND destination = ?"
                                 " ORDER BY date", con, params=[o, d])


def main():
    ap = argparse.ArgumentParser(description="Catalogue des prix Google Flights sur 1 an")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("decouvrir", help="Découvrir les trajets depuis des aéroports de départ")
    p.add_argument("origines", nargs="*", help="Codes IATA (par défaut : origines.txt)")
    p.add_argument("--delai", type=float, default=4.0)
    p = sub.add_parser("collecter", help="Collecter les calendriers de prix")
    p.add_argument("--max-trajets", type=int)
    p.add_argument("--delai", type=float, default=4.0, help="Pause moyenne entre deux appels (s)")
    p.add_argument("--pause-blocage", type=float, default=30, help="Pause après un blocage (min)")
    p.add_argument("--max-blocages", type=int, default=3, help="Nombre de blocages tolérés avant l'arrêt")
    p.add_argument("--origines", help="Limiter aux départs (codes IATA séparés par des virgules, ou fichier liste)")
    p.add_argument("--destinations", help="Limiter aux arrivées (codes IATA séparés par des virgules, ou fichier liste)")
    p.add_argument("--nouveaux", action="store_true", help="Seulement les trajets jamais collectés")
    p.add_argument("--par-lots", type=int, help="Trajets jamais collectés, par lots de N : les trajets sans prix "
                                                "de chaque lot sont refaits et vérifiés avant le lot suivant")
    p.add_argument("--refaire-vides-depuis", help="Avec --par-lots : refaire d'abord les trajets vides collectés "
                                                  "depuis cet horodatage (ex. 2026-10-21T23:43)")
    p = sub.add_parser("croiser", help="Ajouter tous les trajets entre deux listes d'aéroports (sans collecter)")
    p.add_argument("origines", help="Fichier CODE;Ville des départs (ex. listes/france.txt)")
    p.add_argument("destinations", help="Fichier CODE;Ville des arrivées (ex. listes/thailande.txt)")
    p.add_argument("--pays", default="", help="Pays d'arrivée affiché (ex. Thaïlande) ; vide : pays de chaque aéroport")
    p = sub.add_parser("ajouter", help="Ajouter un trajet précis et collecter son calendrier")
    p.add_argument("origine")
    p.add_argument("destination")
    p = sub.add_parser("compagnies", help="Collecter le calendrier de chaque compagnie (et son bagage) sur 1 an")
    p.add_argument("codes", help="Codes IATA des compagnies, séparés par des virgules (ex. VY,UX)")
    p.add_argument("--origines", help="Limiter aux départs (codes IATA séparés par des virgules, ou fichier liste)")
    p.add_argument("--destinations", help="Limiter aux arrivées (codes IATA séparés par des virgules, ou fichier liste)")
    p.add_argument("--delai", type=float, default=6.0)
    p.add_argument("--pause-blocage", type=float, default=60, help="Pause après un blocage (min)")
    p.add_argument("--max-blocages", type=int, default=10)
    p.add_argument("--bagages", action="store_true", help="Seulement rechercher les bagages restés inconnus")
    p = sub.add_parser("jour", help="Pour une date : vol le moins cher de chaque compagnie, de Paris vers chaque destination")
    p.add_argument("date", help="Date de départ (AAAA-MM-JJ)")
    p.add_argument("destinations", help="Codes IATA séparés par des virgules, ou fichier liste (ex. listes/airfox.txt)")
    p.add_argument("--delai", type=float, default=6.0)
    sub.add_parser("etat", help="Afficher l'état du catalogue")
    a = ap.parse_args()

    try:
        if a.cmd == "decouvrir":
            origines = [o.upper() for o in a.origines] or lire_origines()
            n = asyncio.run(decouvrir(origines, a.delai))
            print(f"{n} nouveaux trajets.")
        elif a.cmd == "croiser":
            print(f"{croiser(a.origines, a.destinations, a.pays)} nouveaux trajets ajoutés au catalogue.")
        elif a.cmd == "ajouter":
            n = asyncio.run(ajouter(a.origine.upper(), a.destination.upper()))
            print(f"{a.origine.upper()}→{a.destination.upper()} : {n} jours avec prix.")
        elif a.cmd == "jour":
            dest = ([c for c, _ in lire_liste(a.destinations)] if Path(a.destinations).exists()
                    else [c.strip().upper() for c in a.destinations.split(",")])
            asyncio.run(collecter_jour(a.date, dest, a.delai))
        elif a.cmd in ("collecter", "compagnies"):
            def codes(v):
                if not v:
                    return None
                return [c for c, _ in lire_liste(v)] if Path(v).exists() else [c.strip().upper() for c in v.split(",")]
            if a.cmd == "compagnies" and a.bagages:
                asyncio.run(completer_bagages(codes(a.codes), a.delai))
            elif a.cmd == "compagnies":
                asyncio.run(collecter_compagnies(codes(a.codes), a.delai, a.pause_blocage, a.max_blocages,
                                                 codes(a.origines), codes(a.destinations)))
            elif a.par_lots:
                asyncio.run(collecter_par_lots(a.par_lots, a.delai, a.pause_blocage, a.max_blocages,
                                               codes(a.origines), codes(a.destinations), a.refaire_vides_depuis))
            else:
                asyncio.run(collecter(a.max_trajets, a.delai, a.pause_blocage, a.max_blocages,
                                      origines=codes(a.origines), destinations=codes(a.destinations),
                                      seulement_nouveaux=a.nouveaux))
        print(json.dumps(etat(), ensure_ascii=False, indent=2))
    except Bloque:
        sys.exit("Bloqué par Google (captcha) : réessayez plus tard.")


if __name__ == "__main__":
    main()
