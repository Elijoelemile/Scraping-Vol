"""Scraper Google Flights (Playwright) — n'importe quel aéroport de départ / d'arrivée.

Un trajet :
    python google_flights_scraper.py CDG JFK 2026-11-15
    python google_flights_scraper.py LHR DXB 2026-11-15 --retour 2026-11-22 --adultes 2 --classe affaires

Plusieurs trajets (fichier CSV : origine;destination;date;retour;adultes;classe) :
    python google_flights_scraper.py --fichier trajets.csv --tous --sortie resultats.csv

Depuis Python :
    from google_flights_scraper import scrape_many, Trajet
    vols = asyncio.run(scrape_many([Trajet("JFK", "LAX", "2026-11-20")]))
"""
import argparse
import asyncio
import base64
import json
import random
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from playwright.async_api import Page, async_playwright

CLASSES = {"eco": 1, "premium": 2, "affaires": 3, "premiere": 4}
MOIS = {m: i for i, m in enumerate(
    ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
     "août", "septembre", "octobre", "novembre", "décembre"], 1)}

# Nom de compagnie affiché par Google -> code IATA, quand la page ne donne pas de logo (session Google connectée)
CODES_COMPAGNIES = {
    "Aegean": "A3", "Aer Lingus": "EI", "Aeromexico": "AM", "Air Algérie": "AH", "Air Arabia": "G9",
    "Air Austral": "UU", "Air Baltic": "BT", "Air Canada": "AC", "Air Caraibes": "TX", "Air China": "CA",
    "Air Corsica": "XK", "Air Cote D'Ivoire": "HF", "Air Dolomiti": "EN", "Air Europa": "UX", "Air France": "AF",
    "Air India": "AI", "Air Mauritius": "MK", "Air Peace": "P4", "Air Senegal": "HC", "Air Serbia": "JU",
    "Air Tahiti Nui": "TN", "Air Transat": "TS", "Air Tunisie": "TU", "Tunisair": "TU", "airBaltic": "BT",
    "All Nippon Airways": "NH", "ANA": "NH", "American": "AA", "ASKY": "KP", "Asiana Airlines": "OZ",
    "Austrian": "OS", "Avianca": "AV", "Azul": "AD", "Bangkok Airways": "PG", "Binter": "NT",
    "British Airways": "BA", "Brussels Airlines": "SN", "Cathay Pacific": "CX", "China Eastern": "MU",
    "China Southern": "CZ", "Condor": "DE", "COPA": "CM", "Corsair": "SS", "Croatia Airlines": "OU",
    "Delta": "DL", "Discover Airlines": "4Y", "easyJet": "U2", "Edelweiss Air": "WK", "EgyptAir": "MS",
    "El Al": "LY", "Emirates": "EK", "Ethiopian": "ET", "Etihad": "EY", "Eurowings": "EW", "Finnair": "AY",
    "flydubai": "FZ", "French bee": "BF", "Garuda Indonesia": "GA", "Iberia": "IB", "Iberia Express": "I2",
    "Iberojet": "E9", "Icelandair": "FI", "ITA": "AZ", "Japan Airlines": "JL", "Jet2": "LS", "JetBlue": "B6",
    "Kenya Airways": "KQ", "KLM": "KL", "Korean Air": "KE", "LATAM": "LA", "LEVEL": "IB", "LOT": "LO",
    "Lufthansa": "LH", "Lufthansa City Airlines": "VL", "Luxair": "LG", "Malaysia Airlines": "MH",
    "Norwegian": "DY", "Oman Air": "WY", "Pegasus": "PC", "Philippine Airlines": "PR", "Qantas": "QF",
    "Qatar Airways": "QR", "Royal Air Maroc": "AT", "Royal Jordanian": "RJ", "RwandAir": "WB", "Ryanair": "FR",
    "Saudia": "SV", "Scandinavian Airlines": "SK", "SAS": "SK", "Singapore Airlines": "SQ", "SunExpress": "XQ",
    "SWISS": "LX", "Tap Air Portugal": "TP", "TAROM": "RO", "THAI": "TG", "Transavia": "TO", "TUI fly": "TB",
    "Turkish Airlines": "TK", "United": "UA", "Vietnam Airlines": "VN", "Virgin Atlantic": "VS",
    "Volotea": "V7", "Vueling": "VY", "WestJet": "WS", "Wizz Air": "W6",
}


class Bloque(Exception):
    """Google renvoie sa page « sorry » (captcha) : l'adresse IP est temporairement bloquée."""


@dataclass
class Trajet:
    origine: str
    destination: str
    date: str                 # AAAA-MM-JJ
    retour: str | None = None
    adultes: int = 1
    classe: str = "eco"

    def __post_init__(self):
        self.origine, self.destination = self.origine.upper().strip(), self.destination.upper().strip()
        if self.classe not in CLASSES:
            raise ValueError(f"Classe inconnue : {self.classe} (choix : {', '.join(CLASSES)})")


# --- Encodage du paramètre "tfs" (protobuf utilisé par Google Flights) -------
def _varint(n: int) -> bytes:
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _field(num: int, wire_type: int, payload) -> bytes:
    key = _varint((num << 3) | wire_type)
    if wire_type == 0:
        return key + _varint(payload)
    return key + _varint(len(payload)) + payload


def _str(num: int, text: str) -> bytes:
    return _field(num, 2, text.encode())


def _lieu(num: int, code: str) -> bytes:
    """Code IATA d'aéroport, ou identifiant Google de ville (« /m/05qtj » = Paris, tous aéroports)."""
    if code.startswith("/"):
        return _field(num, 2, _field(1, 0, 3) + _str(2, code))
    return _field(num, 2, _str(2, code))


def build_tfs(legs, adults=1, seat=1) -> str:
    """legs = [(date 'AAAA-MM-JJ', origine, destination), ...] ; origine/destination : IATA ou /m/… (ville)"""
    msg = b""
    for d, origin, dest in legs:
        leg = _str(2, d) + _lieu(13, origin) + _lieu(14, dest)
        msg += _field(3, 2, leg)
    for _ in range(adults):
        msg += _field(8, 0, 1)  # passager adulte
    trip = 1 if len(legs) == 2 else 2  # 1 = aller-retour, 2 = aller simple
    msg += _field(9, 0, seat) + _field(19, 0, trip)
    return base64.urlsafe_b64encode(msg).decode().rstrip("=")


def search_url(t: Trajet, mids: dict | None = None) -> str:
    """mids : {code IATA: identifiant de ville} pour chercher sur tous les aéroports des villes."""
    o, d = (mids or {}).get(t.origine, t.origine), (mids or {}).get(t.destination, t.destination)
    legs = [(t.date, o, d)]
    if t.retour:
        legs.append((t.retour, d, o))
    return ("https://www.google.com/travel/flights/search"
            f"?tfs={build_tfs(legs, t.adultes, CLASSES[t.classe])}&hl=fr&gl=FR&curr=EUR")


# --- Analyse des résultats ---------------------------------------------------
LABEL_RE = re.compile(
    r"(?:À partir de (?P<prix>[\d\s \xa0]+) euros(?: aller-retour \(prix total\))?|Le prix total n'est pas disponible)"
    r"\.(?P<remarque>[^.]*\.)?\s*"
    r"Vol (?:direct )?avec (?P<compagnies>.+?)(?:, ?(?P<escales>\d+)\s*escales?)?(?:\. Vol opéré par (?P<operateur>.+?))?\. "
    r"Départ de (?P<aeroport_depart>.+?) à (?P<heure_depart>\d{1,2}:\d{2}) le (?P<date_depart>.+?), "
    r"arrivée à (?P<aeroport_arrivee>.+?) à (?P<heure_arrivee>\d{1,2}:\d{2}) le (?P<date_arrivee>.+?)\. "
    r"Durée totale\s*:\s*(?P<duree>.+?)\."
)
LAYOVER_RE = re.compile(r"escale de (?P<duree>.+?) à (?P<lieu>.+?)(?:\.|$)")
CO2_RE = re.compile(r"émissions de CO₂\s*:\s*(\d+)\s*kg")
DUREE_RE = re.compile(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*min)?")
DATE_FR_RE = re.compile(r"(\w+)\s+(\d{1,2})$")  # "dimanche, novembre 15"


def _clean(s: str) -> str:
    return re.sub(r"[\s \xa0]+", " ", s).strip()


def _minutes(duree: str) -> int | None:
    m = DUREE_RE.fullmatch(duree.strip())
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0) if m and any(m.groups()) else None


def _iso_date(texte: str, ref: date) -> str | None:
    """'dimanche, novembre 15' -> 'AAAA-MM-JJ' (année déduite de la date de recherche)."""
    m = DATE_FR_RE.search(texte)
    if not m or m.group(1) not in MOIS:
        return None
    d = date(ref.year, MOIS[m.group(1)], int(m.group(2)))
    if (d - ref).days < -180:  # passage d'année (ex. départ 31 déc., arrivée 1er janv.)
        d = d.replace(year=ref.year + 1)
    return d.isoformat()


def parse_label(label: str, co2_label: str | None, ref: date) -> dict | None:
    label = _clean(label)
    m = LABEL_RE.search(label)
    if not m:
        return None
    d = m.groupdict()
    prix = d.pop("prix")
    d["prix_eur"] = int(re.sub(r"\D", "", prix)) if prix else None  # None = « Prix non disponible »
    d["escales"] = int(d["escales"] or 0)
    d["remarque"] = (d["remarque"] or "").strip()
    # Bagage cabine : False si Google précise que le prix n'inclut pas les bagages (compartiment supérieur), sinon True
    d["bagage"] = not re.search(r"bagage|compartiment", d["remarque"], re.I)
    d["operateur"] = d["operateur"] or ""
    d["duree_min"] = _minutes(d["duree"])
    d["date_depart"] = _iso_date(d["date_depart"], ref) or d["date_depart"]
    d["date_arrivee"] = _iso_date(d["date_arrivee"], ref) or d["date_arrivee"]
    d["detail_escales"] = "; ".join(
        f"{x['lieu']} ({x['duree']})" for x in (mm.groupdict() for mm in LAYOVER_RE.finditer(label))
    )
    co2 = CO2_RE.search(_clean(co2_label or ""))
    d["co2_kg"] = int(co2.group(1)) if co2 else None
    return d


# --- Navigation --------------------------------------------------------------
async def _accept_consent(page: Page):
    if "consent." in page.url:
        await page.get_by_role("button", name="Tout refuser").first.click()
        await page.wait_for_url(re.compile(r"^https://www\.google\.com/travel/"), timeout=30000)


VILLES_RE = re.compile(r"Suivre les prix des vols entre (.+) et (.+?) avec départ")


def _ville_iata(code: str) -> str:
    """Secours hors ligne (noms en anglais) si Google n'affiche pas la ville."""
    global _AIRPORTS
    if _AIRPORTS is None:
        import airportsdata
        _AIRPORTS = airportsdata.load("IATA")
    return _AIRPORTS.get(code, {}).get("city") or code


_AIRPORTS = None


async def _villes(page: Page, t: Trajet) -> tuple[str, str]:
    """Noms de villes en français tels qu'affichés par Google (ex. LHR -> Londres)."""
    el = page.locator("[aria-label^='Suivre les prix des vols entre']")
    if await el.count():
        m = VILLES_RE.search(_clean(await el.first.get_attribute("aria-label") or ""))
        if m:
            return m.group(1), m.group(2)
    return _ville_iata(t.origine), _ville_iata(t.destination)


async def scrape_route(page: Page, t: Trajet, tous=False, mids: dict | None = None) -> list[dict]:
    url = search_url(t, mids)
    items = page.locator("li.pIav2d")
    for essai in range(6):  # Google affiche souvent « Petit problème… » : on recharge la page
        await page.goto(url)
        if "/sorry/" in page.url:  # captcha : inutile de recharger, Google bloque l'adresse IP pour un moment
            raise Bloque()
        await _accept_consent(page)
        try:
            await items.first.wait_for(timeout=15000)
            break
        except Exception:
            if essai == 5:
                raise
            await page.wait_for_timeout(1500)

    if tous:  # déplier la liste complète (« Afficher plus de vols »)
        more = page.get_by_role("button", name=re.compile("plus de vols", re.I))
        if await more.count():
            avant = await items.count()
            await more.first.click()
            for _ in range(20):
                await page.wait_for_timeout(500)
                if await items.count() > avant:
                    break
            await page.wait_for_timeout(1500)

    ville_dep, ville_arr = await _villes(page, t)
    meta = {
        "ville_depart": ville_dep, "ville_arrivee": ville_arr,
        "origine": t.origine, "destination": t.destination,
        "date_aller": t.date, "date_retour": t.retour,
        "type_trajet": "aller-retour" if t.retour else "aller simple",
        "adultes": t.adultes, "classe": t.classe,
        "scrape_le": datetime.now().isoformat(timespec="seconds"), "url": url,
    }
    ref = date.fromisoformat(t.date)
    vols, vus = [], set()
    for li in await items.all():
        label_el = li.locator("[aria-label^='À partir de'], [aria-label^='Le prix total']")
        if not await label_el.count():
            continue
        label = await label_el.first.get_attribute("aria-label")
        if not label or label in vus:  # la liste est dupliquée dans le DOM
            continue
        vus.add(label)
        co2_el = li.locator("[aria-label*='émissions de CO']")
        co2 = await co2_el.first.get_attribute("aria-label") if await co2_el.count() else None
        vol = parse_label(label, co2, ref)
        if vol:
            # Logo de la compagnie : image (ou fond) pointant vers airline_logos dans la ligne du vol
            vol["logo"] = await li.evaluate(r"""li => {
                for (const e of li.querySelectorAll('img, [style*="airline_logos"]')) {
                    const src = e.src || (e.style.backgroundImage.match(/url\(["']?([^"')]+)/) || [])[1];
                    if (src && src.includes('airline_logos')) return src;
                }
                return '';
            }""")
            # Code compagnie : nom du fichier du logo (…/70px/VY.png -> VY ; « multi » = plusieurs compagnies)
            code = re.search(r"/([A-Za-z0-9]+)\.png", vol["logo"] or "")
            vol["code_compagnie"] = code.group(1) if code else ""
            vols.append(meta | vol)
        else:
            print(f"[non analysé] {label[:120]}…", file=sys.stderr)
    return vols


async def scrape_many(trajets: list[Trajet], tous=False, visible=False,
                      pause=(3.0, 7.0), on_progress=None, profil=None, chrome=None, mids=None) -> list[dict]:
    """Scrape plusieurs trajets avec un seul navigateur. Un trajet en échec n'arrête pas les autres.

    on_progress(i, total, message) est appelé après chaque trajet (ex. barre de progression).
    profil/chrome : dossier de profil et exécutable Chrome d'une session Google connectée — la page
    affiche alors exactement les prix que Google montre à ce compte.
    mids : {IATA: identifiant de ville} pour chercher sur tous les aéroports des villes.
    """
    resultats = []
    async with async_playwright() as p:
        if profil:
            browser = await p.chromium.launch_persistent_context(str(profil), headless=not visible,
                                                                 executable_path=str(chrome), locale="fr-FR")
            page = browser.pages[0] if browser.pages else await browser.new_page()
        else:
            try:
                browser = await p.chromium.launch(headless=not visible)
            except Exception:  # Chromium de Playwright absent (version portable) : Microsoft Edge, présent sur Windows
                browser = await p.chromium.launch(headless=not visible, channel="msedge")
            page = await browser.new_page(locale="fr-FR")
        for i, t in enumerate(trajets, 1):
            label = f"[{i}/{len(trajets)}] {t.origine}→{t.destination} {t.date}" + (f"↔{t.retour}" if t.retour else "")
            try:
                vols = await scrape_route(page, t, tous, mids)
                msg = f"{label} : {len(vols)} vols"
                resultats += vols
            except Bloque:  # les trajets suivants seraient bloqués aussi : on arrête tout de suite
                await browser.close()
                raise
            except Exception as e:
                msg = f"{label} : ÉCHEC ({type(e).__name__}: {str(e).splitlines()[0]})"
            print(msg, file=sys.stderr)
            if on_progress:
                on_progress(i, len(trajets), msg)
            if i < len(trajets):  # pause aléatoire pour limiter le risque de blocage
                await asyncio.sleep(random.uniform(*pause))
        await browser.close()
    return resultats


# Colonnes exportées par défaut pour le comparateur : nom interne -> nom affiché
COLONNES_COMPARATEUR = {  # ordre d'affichage, comme sur Google : compagnie, horaires, durée, prix d'abord
    "logo": "Logo",
    "compagnies": "Compagnie aérienne",
    "code_compagnie": "Code compagnie",
    "bagage": "Bagage",  # bagage cabine inclus : True / False (selon Google)
    "heure_depart": "Heure de départ",
    "arrivee_affichee": "Heure d'arrivée",
    "duree": "Durée",
    "detail_escales": "Escales",  # distingue les itinéraires partant à la même heure
    "prix_eur": "Prix (EUR)",
    "ville_depart": "Ville de départ",
    "ville_arrivee": "Ville d'arrivée",
    "aeroport_depart": "Aéroport de départ",
    "aeroport_arrivee": "Aéroport d'arrivée",
    "date_depart": "Date de départ (aller)",
    "date_retour": "Date de retour",
}


def tableau_comparateur(df: pd.DataFrame) -> pd.DataFrame:
    """Colonnes affichées du comparateur ; l'heure d'arrivée porte « +1 » si l'on arrive le lendemain (comme Google)."""
    df = df.copy()
    for col in ("logo", "heure_depart", "heure_arrivee", "date_depart", "date_arrivee", "detail_escales",
                "code_compagnie"):
        if col not in df:
            df[col] = ""
    # Code compagnie manquant (page sans logo, anciennes recherches) : déduit du nom, « AF/DL » si plusieurs
    df["code_compagnie"] = [c if c else "/".join(dict.fromkeys(
                                CODES_COMPAGNIES.get(n.strip(), "?") for n in re.split(r", | et ", noms or "") if n.strip()))
                            for c, noms in zip(df["code_compagnie"].fillna(""), df.get("compagnies", ""))]
    # True / False (stocké 1 / 0 dans SQLite) ; vide pour les recherches enregistrées avant l'ajout de l'information
    bagage = pd.to_numeric(df["bagage"], errors="coerce") if "bagage" in df else pd.Series(pd.NA, index=df.index)
    df["bagage"] = bagage.map({1: "True", 0: "False"}).fillna("")
    df["logo"] = df["logo"].fillna("")
    df["detail_escales"] = df["detail_escales"].fillna("").replace("", "Direct").where(
        df.get("escales", pd.Series(1, index=df.index)).fillna(1) == 0, df["detail_escales"].fillna(""))
    # Prix en nombres : les vols sans prix deviennent NaN, affichés « Prix non disponible » (et non « None »)
    df["prix_eur"] = pd.to_numeric(df["prix_eur"], errors="coerce")
    ecart = (pd.to_datetime(df["date_arrivee"], errors="coerce")
             - pd.to_datetime(df["date_depart"], errors="coerce")).dt.days
    df["arrivee_affichee"] = [f"{h} (+{int(n)})" if pd.notna(n) and n > 0 else (h or "")
                              for h, n in zip(df["heure_arrivee"].fillna(""), ecart)]
    df["date_retour"] = df["date_retour"].fillna("")
    return df.rename(columns=COLONNES_COMPARATEUR)[list(COLONNES_COMPARATEUR.values())]


def vers_dataframe(vols: list[dict], complet=False) -> pd.DataFrame:
    """Résultats bruts -> DataFrame trié ; colonnes du comparateur sauf si complet=True."""
    df = pd.DataFrame(vols).sort_values(["origine", "destination", "date_aller", "prix_eur"])
    df["date_retour"] = df["date_retour"].fillna("")
    if not complet:
        df = tableau_comparateur(df)
    return df.reset_index(drop=True)


def lire_trajets(path: str) -> list[Trajet]:
    df = pd.read_csv(path, sep=None, engine="python", dtype=str).fillna("")
    return [Trajet(r["origine"], r["destination"], r["date"],
                   r.get("retour") or None, int(r.get("adultes") or 1), r.get("classe") or "eco")
            for r in df.to_dict("records")]


def main():
    ap = argparse.ArgumentParser(description="Scraper Google Flights")
    ap.add_argument("origine", nargs="?", help="Code IATA d'aéroport (ex. CDG, LHR, JFK)")
    ap.add_argument("destination", nargs="?", help="Code IATA d'aéroport")
    ap.add_argument("date", nargs="?", help="Date de départ AAAA-MM-JJ")
    ap.add_argument("--retour", help="Date de retour AAAA-MM-JJ (aller-retour)")
    ap.add_argument("--adultes", type=int, default=1)
    ap.add_argument("--classe", choices=CLASSES, default="eco")
    ap.add_argument("--fichier", help="CSV de trajets : origine;destination;date;retour;adultes;classe")
    ap.add_argument("--tous", action="store_true", help="Cliquer sur « Afficher plus de vols »")
    ap.add_argument("--sortie", default="vols.csv", help="Fichier .csv ou .json")
    ap.add_argument("--visible", action="store_true", help="Afficher le navigateur")
    ap.add_argument("--complet", action="store_true",
                    help="Garder toutes les colonnes (aéroports, escales, CO2, horaires…)")
    a = ap.parse_args()

    if a.fichier:
        trajets = lire_trajets(a.fichier)
    elif a.origine and a.destination and a.date:
        trajets = [Trajet(a.origine, a.destination, a.date, a.retour, a.adultes, a.classe)]
    else:
        ap.error("indiquer origine destination date, ou --fichier")

    vols = asyncio.run(scrape_many(trajets, a.tous, a.visible))
    if not vols:
        sys.exit("Aucun vol récupéré.")

    df = vers_dataframe(vols, a.complet)
    out = Path(a.sortie)
    if out.suffix == ".json":
        out.write_text(json.dumps(df.to_dict("records"), ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        df.to_csv(out, index=False, encoding="utf-8-sig", sep=";")
    print(f"{len(df)} vols enregistrés dans {out}")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
