"""Page « Catalogue » : prix le plus bas de chaque jour sur 1 an, par trajet (données collectées par catalogue.py)."""
import asyncio
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import catalogue
import db
import recap
from pays import pays_aeroport
from google_flights_scraper import Trajet, vers_dataframe

COULEUR = "#2a78d6"
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
# Vega-Lite affiche les mois en anglais : étiquettes d'axe traduites
MOIS_AXE = ("['janv.','févr.','mars','avr.','mai','juin','juil.','août','sept.','oct.','nov.','déc.']"
            "[month(datum.value)] + ' ' + year(datum.value)")


def avec_prix(df, colonne, vide):
    """Affiche les prix en « 123 € » et les prix absents avec un texte, en gardant le tri numérique."""
    return df.style.format(lambda v: f"{v:.0f} €", subset=[colonne], na_rep=vide)

st.title("Catalogue des prix sur 1 an")
st.caption("Prix le plus bas de chaque jour de départ — aller simple, 1 adulte, classe économique, en euros.")

e = catalogue.etat()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Trajets connus", f"{e['trajets']:,}".replace(",", " "))
c2.metric("Trajets collectés", f"{e['trajets_collectes']:,}".replace(",", " "))
c3.metric("Prix journaliers", f"{e['prix_journaliers']:,}".replace(",", " "))
c4.metric("Dernière collecte", f"{pd.Timestamp(e['plus_recente']):%d/%m %H:%M}" if e["plus_recente"] else "—")

if not e["trajets"]:
    st.info("Le catalogue est vide. Dans un terminal :\n\n"
            "```\n.venv\\Scripts\\python catalogue.py decouvrir\n.venv\\Scripts\\python catalogue.py collecter\n```")
    st.stop()

tous = catalogue.trajets()
# Pays (en français) de chaque aéroport, d'après son code IATA
tous["pays_depart"] = tous["origine"].map(pays_aeroport)
tous["pays_arrivee"] = tous["destination"].map(pays_aeroport)

TOUS_PAYS = "Tous les pays"
p1, p2 = st.columns(2)
liste_pays_dep = sorted(tous["pays_depart"].unique())
pays_dep = p1.selectbox("Pays de départ", [TOUS_PAYS] + liste_pays_dep)
if pays_dep != TOUS_PAYS:
    tous = tous[tous["pays_depart"] == pays_dep]
pays_arr = p2.selectbox("Pays d'arrivée", [TOUS_PAYS] + sorted(tous["pays_arrivee"].unique()))
if pays_arr != TOUS_PAYS:
    tous = tous[tous["pays_arrivee"] == pays_arr]
if tous.empty:
    st.warning("Aucun trajet du catalogue pour ces pays.")
    st.stop()

origines = (tous.groupby("origine")["ville_depart"].first().fillna("").sort_values())
f1, f2 = st.columns(2)
# Départ et arrivée sont facultatifs : « Tous les départs » = tous les aéroports des pays choisis.
# À l'ouverture (aucun pays choisi), Paris-CDG est proposé ; dès qu'un pays de départ est choisi : tous ses aéroports.
codes_origines = list(origines.index)
defaut = codes_origines.index("CDG") + 1 if pays_dep == TOUS_PAYS and "CDG" in codes_origines else 0
origine = f1.selectbox("Départ", [None] + codes_origines, index=defaut,
                       format_func=lambda o: "Tous les départs" if o is None else f"{origines[o]} ({o})".strip())
dests = tous if origine is None else tous[tous["origine"] == origine]
libelles = {r.destination: f"{r.ville_arrivee} ({r.destination}, {r.pays_arrivee})"
            for r in dests.drop_duplicates("destination").itertuples()}
destination = f2.selectbox("Arrivée", [None] + sorted(libelles, key=libelles.get),
                           format_func=lambda d: "Toutes les destinations" if d is None else libelles[d])

# --- Pas d'arrivée choisie : liste des destinations / des trajets -------------------
mode_pays = origine is None and destination is None  # pays → pays : tous départs vers toutes arrivées
if destination is None:
    if origine is not None:  # un départ précis : ses destinations
        vue = dests[["ville_arrivee", "destination", "pays_arrivee", "prix_min", "date_prix_min", "nb_jours_prix",
                     "collecte_le"]].copy()
        noms = {"ville_arrivee": "Ville d'arrivée", "destination": "Code", "pays_arrivee": "Pays"}
    else:  # tous les départs : tous les trajets des pays choisis, tous aéroports confondus
        vue = dests.assign(depart=dests["ville_depart"].fillna("") + " (" + dests["origine"] + ")",
                           arrivee=dests["ville_arrivee"].fillna("") + " (" + dests["destination"] + ")")
        vue = vue[["depart", "arrivee", "pays_arrivee", "prix_min", "date_prix_min", "nb_jours_prix", "collecte_le"]]
        noms = {"depart": "Départ", "arrivee": "Arrivée", "pays_arrivee": "Pays d'arrivée"}
    vue = vue.sort_values("prix_min", na_position="last")
    # Dates et prix en texte lisible ; « — » quand le trajet n'a pas de prix au catalogue (au lieu de « None »)
    vue["date_prix_min"] = pd.to_datetime(vue["date_prix_min"]).dt.strftime("%d/%m/%Y").fillna("—")
    vue["collecte_le"] = pd.to_datetime(vue["collecte_le"]).dt.strftime("%d/%m/%Y %H:%M").fillna("—")
    vue["nb_jours_prix"] = vue["nb_jours_prix"].fillna(0).astype(int)
    vue = vue.rename(columns=noms | {"prix_min": "Prix le plus bas", "date_prix_min": "Meilleure date",
                                     "nb_jours_prix": "Jours avec prix", "collecte_le": "Collecté le"})
    if origine is not None:
        st.dataframe(avec_prix(vue, "Prix le plus bas", "—"), hide_index=True, width="stretch")
        st.caption("Choisissez une arrivée pour voir le calendrier des prix sur l'année.")
        st.stop()
    with st.expander(f"Tous les trajets du catalogue ({len(vue)} : {dests['origine'].nunique()} aéroport(s) de départ "
                     f"vers {dests['destination'].nunique()} aéroport(s) d'arrivée), du moins cher au plus cher"):
        st.dataframe(avec_prix(vue, "Prix le plus bas", "—"), hide_index=True, width="stretch")


# --- Un trajet : calendrier sur l'année ------------------------------------------

# « Tous les départs » : calendrier combiné de tous les aéroports de départ qui desservent cette arrivée
combine = origine is None
dest_sel = [destination] if destination else sorted(dests["destination"].unique())
paires = dests.loc[dests["destination"].isin(dest_sel), ["origine", "destination"]].drop_duplicates()
origines_sel = sorted(paires["origine"].unique()) if combine else [origine]
nom_depart = (pays_dep if pays_dep != TOUS_PAYS else "tous les départs") if combine else f"{origines[origine]} ({origine})"
nom_arrivee = (pays_arr if pays_arr != TOUS_PAYS else "toutes les arrivées") if mode_pays else destination
if mode_pays:
    st.caption(f"Pays → pays : prix le plus bas de chaque jour parmi {len(paires)} trajet(s) "
               f"({len(origines_sel)} aéroport(s) de départ, {len(dest_sel)} d'arrivée), tous aéroports confondus.")

t1, t2, t3 = st.columns([2, 1, 2])
aller_retour = t1.radio("Type de trajet", ["Aller simple", "Aller-retour"], horizontal=True, disabled=combine,
                        help="Aller-retour : choisissez un aéroport de départ précis" if combine else None
                        ) == "Aller-retour" and not combine
duree = t2.number_input("Durée du séjour (jours)", 1, 60, 7) if aller_retour else None
trop = combine and len(paires) > 20
maj = t3.button("🔄 Mettre à jour maintenant", disabled=trop,
                help=("Trop de trajets à mettre à jour d'un coup : utilisez le collecteur (collecter_catalogue.bat)"
                      if trop else "Interroge Google Flights (quelques secondes)"))


def lancer(coro, message):
    try:
        with st.spinner(message):
            asyncio.run(coro)
        return True
    except catalogue.Bloque:
        st.error("Google bloque temporairement les requêtes (captcha). Réessayez plus tard.")
        return False


if aller_retour:
    # Calculé à la demande puis gardé 24 h (trop de combinaisons départ × durée pour tout précollecter)
    cal = pd.DataFrame() if maj else catalogue.calendrier_ar_trajet(origine, destination, duree, timedelta(hours=24))
    if cal.empty:
        if lancer(catalogue.collecter_ar(origine, destination, duree),
                  f"Calcul des prix aller-retour ({duree} jours de séjour) sur l'année…"):
            cal = catalogue.calendrier_ar_trajet(origine, destination, duree)
else:
    if maj:
        async def maj_aller():
            gf = catalogue.GoogleFlights(delai=1.0)
            try:
                await gf.ouvrir()
                liste = list(paires.itertuples(index=False)) if combine else [(origine, destination)]
                fin = await catalogue.horizon(gf, liste[0][0], liste[0][1])
                for o, d in liste:
                    prix = {}
                    for a, b in catalogue.tranches(date.today(), fin):
                        prix |= await gf.calendrier(o, d, a, b)
                    catalogue.enregistrer_calendrier(o, d, prix)
            finally:
                await gf.fermer()
        lancer(maj_aller(), f"Collecte du calendrier ({len(paires) if combine else 1} trajet(s))…")
    cal = (catalogue.calendrier_combine(origines_sel, dest_sel) if combine
           else catalogue.calendrier_trajet(origine, destination))

if cal.empty:
    st.info("Aucun prix disponible pour ce trajet (pas encore collecté, ou Google n'a pas de prix en mémoire).")
    st.stop()

libelle_prix = "Prix aller-retour" if aller_retour else "Prix aller simple"
cal["date"] = pd.to_datetime(cal["date"])
villes_arr = dests.drop_duplicates("destination").set_index("destination")["ville_arrivee"].to_dict()
COL_TRAJET = "Trajet" if mode_pays else "Aéroport de départ"
if combine:
    cal["depart_de"] = [f"{origines.get(o, o)} ({o})" + (f" → {villes_arr.get(d, d)} ({d})" if mode_pays else "")
                        for o, d in zip(cal["origine"], cal["destination"])]
cal["jour_fr"] = [f"{JOURS[d.weekday()]} {d:%d/%m/%Y}" for d in cal["date"]]
if aller_retour:
    cal["retour_fr"] = [f"{JOURS[d.weekday()]} {d:%d/%m/%Y}" for d in pd.to_datetime(cal["date_retour"])]
moins_cher = cal.loc[cal["prix"].idxmin()]
st.caption(f"{libelle_prix} le plus bas pour chaque date de départ"
           + (", tous aéroports confondus" if mode_pays else
              f", tous aéroports de départ confondus ({len(origines_sel)} : {', '.join(origines_sel)})" if combine
              else "")
           + (f", retour {duree} jours plus tard" if aller_retour else "")
           + f" — données du {pd.Timestamp(cal['collecte_le'].max()):%d/%m/%Y à %H:%M}.")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Prix le plus bas", f"{moins_cher['prix']} €")
m1.caption((f"depuis {moins_cher['depart_de']}, " if combine else "") + f"départ le {moins_cher['date']:%d/%m/%Y}"
           + (f", retour le {pd.Timestamp(moins_cher['date_retour']):%d/%m/%Y}" if aller_retour else ""))
m2.metric("Prix médian", f"{int(cal['prix'].median())} €")
m3.metric("Prix le plus haut", f"{cal['prix'].max()} €")
m4.metric("Jours avec prix", len(cal))
m4.caption(f"du {cal['date'].min():%d/%m/%Y} au {cal['date'].max():%d/%m/%Y}")

survol = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
base = alt.Chart(cal).encode(
    x=alt.X("date:T", title="Date de départ", axis=alt.Axis(labelExpr=MOIS_AXE, tickCount="month")),
    y=alt.Y("prix:Q", title=f"{libelle_prix} (€)", scale=alt.Scale(zero=False), axis=alt.Axis(format="d")),
)
infobulle = [alt.Tooltip("jour_fr:N", title="Départ")]
if combine:
    infobulle.append(alt.Tooltip("depart_de:N", title=COL_TRAJET))
if aller_retour:
    infobulle.append(alt.Tooltip("retour_fr:N", title="Retour"))
infobulle.append(alt.Tooltip("prix:Q", title=f"{libelle_prix} (€)"))
ligne = base.mark_line(color=COULEUR, strokeWidth=2, interpolate="step-after")
points = base.mark_point(size=70, filled=True, color=COULEUR).encode(
    opacity=alt.condition(survol, alt.value(1), alt.value(0)), tooltip=infobulle,
).add_params(survol)
repere = base.mark_rule(color="#888", strokeDash=[3, 3]).encode(opacity=alt.condition(survol, alt.value(1), alt.value(0)))
minimum = alt.Chart(cal[cal["prix"] == cal["prix"].min()]).mark_point(
    shape="triangle-down", size=90, filled=True, color=COULEUR).encode(x="date:T", y="prix:Q")
st.altair_chart((ligne + repere + points + minimum).properties(height=320), width="stretch")

# Toutes les dates, d'aujourd'hui à la dernière date disponible (jours sans prix compris)
premier = max(cal["date"].min().date(), date.today())
dernier = cal["date"].max().date()
toutes = pd.DataFrame({"date": pd.date_range(premier, dernier)}).merge(
    cal[["date", "prix"] + (["depart_de"] if combine else [])], on="date", how="left")
toutes["Départ"] = [f"{JOURS[d.weekday()]} {d:%d/%m/%Y}" for d in toutes["date"]]
colonnes = ["Départ"]
if combine:
    toutes[COL_TRAJET] = toutes["depart_de"].fillna("")
    colonnes.append(COL_TRAJET)
if aller_retour:
    toutes["Retour"] = [f"{JOURS[r.weekday()]} {r:%d/%m/%Y}" for r in toutes["date"] + pd.Timedelta(days=duree)]
    colonnes.append("Retour")
toutes = toutes.rename(columns={"prix": "Prix"})
colonnes.append("Prix")

MAX_DATES = 10  # dates recherchées en une fois (au-delà, risque de blocage par Google)

g1, g2 = st.columns([3, 2])
with g1:
    st.markdown(f"**Toutes les dates** — {len(toutes)} jours, dont {int(toutes['Prix'].notna().sum())} avec un prix "
                "au catalogue")
    st.caption("Cochez une ou plusieurs dates pour rechercher leurs vols ; cliquez sur « Prix » pour trier. "
               "« pas de prix » = pas de prix au catalogue (une recherche peut quand même trouver des vols).")
    selection = st.dataframe(
        avec_prix(toutes[colonnes], "Prix", "pas de prix"), hide_index=True, width="stretch", height=420,
        on_select="rerun", selection_mode="multi-row", key=f"dates_{origine}_{destination}_{duree}",
    )
    coches = sorted(toutes.loc[i, "date"].date() for i in selection.selection.rows)
with g2:
    st.markdown("**Voir les vols des dates choisies**")
    if coches:
        jours = coches[:MAX_DATES]
        st.write(f"{len(coches)} date(s) cochée(s) : " + ", ".join(f"{JOURS[d.weekday()]} {d:%d/%m}" for d in jours)
                 + ("…" if len(coches) > MAX_DATES else ""))
        if len(coches) > MAX_DATES:
            st.warning(f"Seules les {MAX_DATES} premières dates seront recherchées, pour éviter un blocage par Google.")
    else:  # aucune case cochée : une date au choix
        jours = [st.date_input("Date de départ", premier, min_value=premier, max_value=dernier, format="DD/MM/YYYY")]
    if len(jours) == 1:
        retour1 = jours[0] + timedelta(days=duree) if aller_retour else None
        ligne_jour = cal[cal["date"].dt.date == jours[0]]
        st.caption((f"Retour le {retour1:%d/%m/%Y}. " if retour1 else "")
                   + (f"Prix le plus bas au catalogue : {ligne_jour['prix'].iloc[0]} €" if len(ligne_jour)
                      else "Pas de prix au catalogue pour cette date.")
                   + f" — Date la moins chère : {JOURS[moins_cher['date'].weekday()]} {moins_cher['date']:%d/%m/%Y}"
                   f" ({moins_cher['prix']} €).")
    elif aller_retour:
        st.caption(f"Retour {duree} jours après chaque date de départ.")
    ville = True if mode_pays else st.checkbox("Tous les aéroports des villes (ex. Paris : CDG + Orly)", value=True,
                                               help="Comme lorsqu'on tape « Paris » sur Google Flights")
    if mode_pays and TOUS_PAYS in (pays_dep, pays_arr):
        st.caption("Pour rechercher les vols en pays → pays, choisissez un pays de départ "
                   "et un pays d'arrivée en haut de la page.")
    if st.button("Rechercher les vols", type="primary", disabled=mode_pays and TOUS_PAYS in (pays_dep, pays_arr)):
        try:
            with st.spinner(f"Recherche sur Google Flights ({len(jours)} date(s), 5 à 30 s par date)…"):
                if mode_pays:
                    # Pays → pays : pour chaque date, tous les vols entre les deux pays en une seule recherche Google
                    async def entre_pays():
                        p_dep, p_arr = await asyncio.gather(catalogue.resoudre_pays(pays_dep),
                                                            catalogue.resoudre_pays(pays_arr))
                        if not p_dep or not p_arr:
                            raise ValueError("pays introuvable chez Google")
                        tous_vols = []
                        for i, j in enumerate(jours):
                            if i:
                                await asyncio.sleep(2)  # pause entre deux dates (limite le risque de blocage)
                            tous_vols += await catalogue.vols_pays(p_dep, p_arr, j.isoformat())
                        return tous_vols
                    vols = asyncio.run(entre_pays())
                else:
                    # « Tous les départs » : chaque aéroport de départ du catalogue qui dessert cette arrivée (8 max).
                    # (La recherche Google « pays → aéroport » se limite à la capitale : on interroge chaque départ.)
                    departs = origines_sel[:8]
                    if combine and ville:  # une seule recherche par ville (ex. Paris = CDG + Orly + Beauvais)
                        mids = catalogue.mids_villes(departs)
                        vus, uniques = set(), []
                        for o in departs:
                            cle_ville = mids.get(o, o)
                            if cle_ville not in vus:
                                vus.add(cle_ville)
                                uniques.append(o)
                        departs = uniques
                    vols = asyncio.run(catalogue.rechercher(
                        [Trajet(o, destination, j.isoformat(),
                                (j + timedelta(days=duree)).isoformat() if aller_retour else None)
                         for j in jours for o in departs], ville=ville,
                        compte=st.session_state.get("utiliser_compte", False)))
        except catalogue.Bloque:
            vols = None
            st.error("Google bloque temporairement les recherches depuis votre connexion (captcha). Attendez quelques heures sans lancer de recherche, puis réessayez.")
        except Exception as e:  # erreur réseau ou autre : message lisible plutôt qu'un plantage
            vols = None
            st.error(f"La recherche a échoué ({type(e).__name__}). Réessayez dans quelques minutes.")
        if vols is None:
            pass
        elif vols:
            db.enregistrer(vols)
            st.session_state["vols_jour"] = vers_dataframe(vols)
            st.session_state["vols_jour_brut"] = vers_dataframe(vols, complet=True)
            st.session_state["source_jour"] = sorted({v.get("source", "") for v in vols} - {""})
            st.session_state["trajet_jour"] = (f"{origine or nom_depart}→{destination or nom_arrivee} "
                                               + ", ".join(f"{j:%d/%m}" for j in jours)
                                               + (f" (retour +{duree} j)" if aller_retour else ""))
            st.session_state["recap_nouveau_jour"] = True
        else:
            st.error("Aucun vol récupéré (pas de vol à ces dates, ou blocage temporaire de Google).")

if "vols_jour" in st.session_state:
    if aller_retour:
        st.caption("Aller-retour : horaires, durée et compagnie du vol aller ; prix total aller-retour.")
    vj = st.session_state["vols_jour"]
    st.dataframe(avec_prix(vj, "Prix (EUR)", "Prix non disponible"), hide_index=True, width="stretch",
                 column_config={"Logo": st.column_config.ImageColumn("Logo", width="small")})
    if st.session_state.get("source_jour"):
        st.caption("Source des prix : " + " ; ".join(st.session_state["source_jour"]) + ".")
    if vj["Prix (EUR)"].isna().any():
        st.caption(f"{int(vj['Prix (EUR)'].isna().sum())} vol(s) en fin de liste « Prix non disponible » : Google n'a pas de prix pour ces vols au moment de la recherche.")
    recap.afficher(st.session_state.get("vols_jour_brut"), "historique_catalogue",
                   nouvelle=st.session_state.pop("recap_nouveau_jour", False),
                   trajet=st.session_state.get("trajet_jour", ""),
                   source=" ; ".join(st.session_state.get("source_jour", [])))
