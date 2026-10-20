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
"""


class Bloque(Exception):
    """Google renvoie sa page « sorry » (captcha) : l'adresse IP est temporairement bloquée."""


class PlageInvalide(Exception):
    """Plage de dates refusée (au-delà de l'horizon de réservation ou trop longue)."""


def connexion(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


# --- Client des appels internes ------------------------------------------------
class GoogleFlights:
    def __init__(self, delai: float = 4.0, connecte: bool = False):
        """connecte=True : utilise la session Google de l'utilisateur (session_google.py), si elle existe,
        pour obtenir les prix réservés aux comptes connectés."""
        import session_google
        self.delai = delai          # pause moyenne entre deux appels (secondes)
        self.appels = 0
        self._dernier = 0.0
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
                ligne = next(l for l in r.text.split("\n") if l.startswith('[["wrb.fr"'))
                return json.loads(json.loads(ligne)[0][2])
            m = re.search(r'\[(\d+),null,\[\["type', r.text)
            if m and m.group(1) == "3":
                raise PlageInvalide()
        return None

    @staticmethod
    def _filtre(o: str, d: str, jour: str, type_o=0) -> list:
        segment = [[[[o, type_o]]], [[[d, 0]]], None, 0, None, None, jour,
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
                    })
            return liste

        # Google ne renvoie pas toujours le prix de chaque vol (« prix total non disponible »), et cela varie d'un
        # appel à l'autre : tant qu'il manque des prix, on relance (2 fois max) et on complète vol par vol.
        resultats = []
        for passe in range(3):
            data = await self.rpc("GetShoppingResults", requete, essais=12)
            if not data:
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

    async def calendrier(self, o: str, d: str, debut: date, fin: date) -> dict[str, int]:
        """{date: prix} pour les jours entre debut et fin (une seule plage ≤ TRANCHE_JOURS)."""
        data = await self.rpc("GetCalendarPicker",
                              [None, self._filtre(o, d, debut.isoformat()), [debut.isoformat(), fin.isoformat()]])
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
            except Bloque:
                blocages += 1
                if blocages > max_blocages:
                    log(f"Bloqué par Google {blocages} fois : arrêt. Relancez plus tard, la collecte reprendra ici.")
                    break
                log(f"⚠️ Bloqué par Google (captcha). Pause de {pause_blocage_min:.0f} min puis reprise…")
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
    origines, destinations = lire_liste(fichier_origines), lire_liste(fichier_destinations)
    paires = sorted(((i + j, i, o, vo, d, vd) for i, (o, vo) in enumerate(origines)
                     for j, (d, vd) in enumerate(destinations) if o != d))
    maintenant = datetime.now().isoformat(timespec="seconds")
    with closing(connexion()) as con, con:
        avant = con.total_changes
        con.executemany("INSERT OR IGNORE INTO trajets_catalogue (origine, destination, ville_depart, ville_arrivee,"
                        " pays_arrivee, decouvert_le) VALUES (?, ?, ?, ?, ?, ?)",
                        [(o, d, vo, vd, pays_arrivee, maintenant) for _, _, o, vo, d, vd in paires])
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
    """Tous les vols d'un pays vers un autre à une date (une seule recherche Google, tous aéroports confondus)."""
    from google_flights_scraper import Trajet
    t = Trajet(pays_depart["mid"], pays_arrivee["mid"], date_aller, date_retour, adultes, classe)
    lieux = {t.origine: (pays_depart["mid"], 5), t.destination: (pays_arrivee["mid"], 5)}
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
    p = sub.add_parser("croiser", help="Ajouter tous les trajets entre deux listes d'aéroports (sans collecter)")
    p.add_argument("origines", help="Fichier CODE;Ville des départs (ex. listes/france.txt)")
    p.add_argument("destinations", help="Fichier CODE;Ville des arrivées (ex. listes/thailande.txt)")
    p.add_argument("--pays", default="", help="Pays d'arrivée affiché (ex. Thaïlande)")
    p = sub.add_parser("ajouter", help="Ajouter un trajet précis et collecter son calendrier")
    p.add_argument("origine")
    p.add_argument("destination")
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
        elif a.cmd == "collecter":
            def codes(v):
                if not v:
                    return None
                return [c for c, _ in lire_liste(v)] if Path(v).exists() else [c.strip().upper() for c in v.split(",")]
            asyncio.run(collecter(a.max_trajets, a.delai, a.pause_blocage, a.max_blocages,
                                  origines=codes(a.origines), destinations=codes(a.destinations),
                                  seulement_nouveaux=a.nouveaux))
        print(json.dumps(etat(), ensure_ascii=False, indent=2))
    except Bloque:
        sys.exit("Bloqué par Google (captcha) : réessayez plus tard.")


if __name__ == "__main__":
    main()
