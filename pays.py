"""Pays des aéroports : code IATA d'aéroport -> code pays ISO -> nom du pays en français."""
from functools import lru_cache

NOMS_PAYS = {
    "AE": "Émirats arabes unis", "AF": "Afghanistan", "AG": "Antigua-et-Barbuda", "AL": "Albanie", "AM": "Arménie",
    "AO": "Angola", "AR": "Argentine", "AT": "Autriche", "AU": "Australie", "AW": "Aruba", "AZ": "Azerbaïdjan",
    "BA": "Bosnie-Herzégovine", "BB": "Barbade", "BD": "Bangladesh", "BE": "Belgique", "BF": "Burkina Faso",
    "BG": "Bulgarie", "BH": "Bahreïn", "BI": "Burundi", "BJ": "Bénin", "BM": "Bermudes", "BO": "Bolivie",
    "BR": "Brésil", "BS": "Bahamas", "BW": "Botswana", "BY": "Biélorussie", "BZ": "Belize", "CA": "Canada",
    "CD": "RD Congo", "CF": "Centrafrique", "CG": "Congo", "CH": "Suisse", "CI": "Côte d'Ivoire", "CL": "Chili",
    "CM": "Cameroun", "CN": "Chine", "CO": "Colombie", "CR": "Costa Rica", "CU": "Cuba", "CV": "Cap-Vert",
    "CW": "Curaçao", "CY": "Chypre", "CZ": "Tchéquie", "DE": "Allemagne", "DJ": "Djibouti", "DK": "Danemark",
    "DM": "Dominique", "DO": "République dominicaine", "DZ": "Algérie", "EC": "Équateur", "EE": "Estonie",
    "EG": "Égypte", "EH": "Sahara occidental", "ER": "Érythrée", "ES": "Espagne", "ET": "Éthiopie",
    "FI": "Finlande", "FJ": "Fidji", "FR": "France", "GA": "Gabon", "GB": "Royaume-Uni", "GD": "Grenade",
    "GE": "Géorgie", "GF": "Guyane", "GH": "Ghana", "GL": "Groenland", "GM": "Gambie", "GN": "Guinée",
    "GP": "Guadeloupe", "GQ": "Guinée équatoriale", "GR": "Grèce", "GT": "Guatemala", "GW": "Guinée-Bissau",
    "HK": "Hong Kong", "HN": "Honduras", "HR": "Croatie", "HT": "Haïti", "HU": "Hongrie", "ID": "Indonésie",
    "IE": "Irlande", "IL": "Israël", "IN": "Inde", "IQ": "Irak", "IR": "Iran", "IS": "Islande", "IT": "Italie",
    "JM": "Jamaïque", "JO": "Jordanie", "JP": "Japon", "KE": "Kenya", "KH": "Cambodge", "KM": "Comores",
    "KR": "Corée du Sud", "KW": "Koweït", "KY": "Îles Caïmans", "KZ": "Kazakhstan", "LA": "Laos", "LB": "Liban",
    "LC": "Sainte-Lucie", "LK": "Sri Lanka", "LR": "Liberia", "LT": "Lituanie", "LU": "Luxembourg",
    "LV": "Lettonie", "LY": "Libye", "MA": "Maroc", "MC": "Monaco", "MD": "Moldavie", "ME": "Monténégro",
    "MG": "Madagascar", "MK": "Macédoine du Nord", "ML": "Mali", "MM": "Birmanie", "MN": "Mongolie",
    "MQ": "Martinique", "MR": "Mauritanie", "MT": "Malte", "MU": "Maurice", "MV": "Maldives", "MW": "Malawi",
    "MX": "Mexique", "MY": "Malaisie", "MZ": "Mozambique", "NA": "Namibie", "NC": "Nouvelle-Calédonie",
    "NE": "Niger", "NG": "Nigeria", "NI": "Nicaragua", "NL": "Pays-Bas", "NO": "Norvège", "NP": "Népal",
    "NZ": "Nouvelle-Zélande", "OM": "Oman", "PA": "Panama", "PE": "Pérou", "PF": "Polynésie française",
    "PH": "Philippines", "PK": "Pakistan", "PL": "Pologne", "PR": "Porto Rico", "PT": "Portugal",
    "PY": "Paraguay", "QA": "Qatar", "RE": "La Réunion", "RO": "Roumanie", "RS": "Serbie", "RU": "Russie",
    "RW": "Rwanda", "SA": "Arabie saoudite", "SC": "Seychelles", "SD": "Soudan", "SE": "Suède", "SG": "Singapour",
    "SI": "Slovénie", "SK": "Slovaquie", "SL": "Sierra Leone", "SN": "Sénégal", "SO": "Somalie",
    "SV": "Salvador", "SX": "Saint-Martin (NL)", "SY": "Syrie", "TC": "Îles Turques-et-Caïques", "TD": "Tchad",
    "TG": "Togo", "TH": "Thaïlande", "TN": "Tunisie", "TR": "Turquie", "TT": "Trinité-et-Tobago",
    "TW": "Taïwan", "TZ": "Tanzanie", "UA": "Ukraine", "UG": "Ouganda", "US": "États-Unis", "UY": "Uruguay",
    "UZ": "Ouzbékistan", "VE": "Venezuela", "VN": "Viêt Nam", "YE": "Yémen", "YT": "Mayotte",
    "ZA": "Afrique du Sud", "ZM": "Zambie", "ZW": "Zimbabwe",
}


@lru_cache(maxsize=1)
def _pays_par_aeroport() -> dict[str, str]:
    import airportsdata
    return {code: a["country"] for code, a in airportsdata.load("IATA").items()}


def pays_aeroport(code: str) -> str:
    """Nom du pays (en français) d'un aéroport, à partir de son code IATA."""
    iso = _pays_par_aeroport().get(code, "")
    return NOMS_PAYS.get(iso, iso or "Inconnu")
