"""Page « Catalogue » : prix le plus bas de chaque jour sur 1 an, par trajet (données collectées par catalogue.py)."""
import asyncio
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import catalogue
import db
import recap
from pays import pays_aeroport
from google_flights_scraper import Trajet, vers_dataframe

COULEUR = "#2a78d6"
COMPAGNIES = ["VY", "UX"]  # compagnies par défaut du tableau de toutes les dates (Vueling, Air Europa)
LISTE_COMPAGNIES = Path(__file__).parent.parent / "listes" / "compagnies.txt"  # codes proposés (ceux d'Airfox)
MAX_LIGNES = 600_000  # taille maximale du tableau de toutes les dates
MAX_DIRECT = 50  # recherches en direct par clic (≈ 10 s chacune) : au-delà, risque de blocage par Google
A_COMPLETER = "à compléter"
PAS_DE_VOL = "pas de vol"
# Aéroports de Paris (vols commerciaux) ; Beauvais et Vatry sont vendus comme « Paris » par les low-cost
PARIS = ["CDG", "ORY", "BVA", "XCR"]
NOMS_AEROPORTS = {"CDG": "Paris-Charles de Gaulle", "ORY": "Paris-Orly", "BVA": "Paris-Beauvais",
                  "XCR": "Paris-Vatry"}


def nom_aeroport(code, ville):
    return NOMS_AEROPORTS.get(code) or ville or code
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
# À l'ouverture, Paris-CDG est proposé ; on peut en choisir plusieurs (bouton : tous les aéroports de Paris).
# Plusieurs aéroports possibles (ex. tous ceux de Paris) ; aucun = « Tous les départs ».
codes_origines = sorted(origines.index, key=lambda o: (nom_aeroport(o, origines[o]), o))
defaut = ["CDG"] if "CDG" in codes_origines else []
if "departs" not in st.session_state:
    st.session_state["departs"] = defaut
# aéroports qui ne sont plus proposés (autre pays choisi) : retirés de la sélection
st.session_state["departs"] = [o for o in st.session_state["departs"] if o in codes_origines]
departs = f1.multiselect("Départ", codes_origines, key="departs", placeholder="Tous les départs",
                         format_func=lambda o: f"{nom_aeroport(o, origines[o])} ({o})")
paris = [o for o in PARIS if o in codes_origines]
f1.button("Tous les aéroports de Paris", disabled=not paris,
          on_click=lambda: st.session_state.update({"departs": paris}))
origine = departs[0] if len(departs) == 1 else None  # un seul aéroport : calendrier aller-retour possible
dests = tous[tous["origine"].isin(departs)] if departs else tous
libelles = {r.destination: f"{r.ville_arrivee} ({r.destination}, {r.pays_arrivee})"
            for r in dests.drop_duplicates("destination").itertuples()}
destination = f2.selectbox("Arrivée", [None] + sorted(libelles, key=libelles.get),
                           format_func=lambda d: "Toutes les destinations" if d is None else libelles[d])

# --- Pas d'arrivée choisie : liste des destinations / des trajets -------------------
mode_pays = not departs and destination is None  # pays → pays : tous départs vers toutes arrivées
if destination is None:
    if origine is not None:  # un départ précis : ses destinations
        vue = dests[["ville_arrivee", "destination", "pays_arrivee", "prix_min", "date_prix_min", "nb_jours_prix",
                     "collecte_le"]].copy()
        noms = {"ville_arrivee": "Ville d'arrivée", "destination": "Code", "pays_arrivee": "Pays"}
    else:  # tous les départs : tous les trajets des pays choisis, tous aéroports confondus
        vue = dests.assign(depart=[f"{nom_aeroport(o, v)} ({o})" for o, v in zip(dests["origine"], dests["ville_depart"])],
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
    if departs:
        st.dataframe(avec_prix(vue, "Prix le plus bas", "—"), hide_index=True, width="stretch")
        st.caption("Choisissez une arrivée pour voir le calendrier des prix sur l'année.")
        # Toutes les dates, une ligne par compagnie ; ce que l'on ne sait pas encore : « à compléter »
        st.subheader("Toutes les dates — par compagnie")
        # Compagnies au choix (toutes celles d'Airfox) ; par défaut celles déjà collectées
        collectees = catalogue.compagnies_collectees() or COMPAGNIES
        noms_cies = dict(catalogue.lire_liste(str(LISTE_COMPAGNIES))) if LISTE_COMPAGNIES.exists() else {}
        options_cies = collectees + [c for c in noms_cies if c not in collectees]
        g1, g2, g3 = st.columns([3, 2, 1])
        cies = g2.multiselect("Compagnies", options_cies, default=collectees, placeholder="Choisir des compagnies",
                              format_func=lambda c: f"{c} — {noms_cies.get(c, c)}"
                                                    + (" ✓" if c in collectees else ""),
                              help="✓ = calendrier collecté. Les autres compagnies s'affichent « à compléter » "
                                   "tant que leur collecte n'a pas été faite.") or collectees
        # Filtres du tableau ; tri par date d'abord : chaque jour, toutes les villes d'arrivée se suivent
        avec_prix_cat = dests[dests["nb_jours_prix"].fillna(0) > 0]
        villes = g1.multiselect("Villes d'arrivée", sorted(avec_prix_cat["ville_arrivee"].dropna().unique()),
                                placeholder=f"Toutes ({avec_prix_cat['destination'].nunique()} destinations)")
        avec_vol = g3.checkbox("Masquer « pas de vol »", help="Ne garder que les jours où la compagnie a un prix "
                                                             "(ou n'a pas encore été collectée)")
        if villes:
            avec_prix_cat = avec_prix_cat[avec_prix_cat["ville_arrivee"].isin(villes)]
        # Garde-fou : une ligne par jour × trajet × compagnie ; au-delà, le navigateur ne suit plus
        estimation = int(avec_prix_cat["nb_jours_prix"].sum()) * len(cies)
        if estimation > MAX_LIGNES:
            st.warning(f"Environ {estimation:,} lignes : c'est trop pour un seul tableau (maximum {MAX_LIGNES:,})."
                       .replace(",", " ")
                       + " Choisissez des villes d'arrivée, moins de compagnies ou moins d'aéroports de départ.")
            st.stop()
        dates = pd.concat([catalogue.calendrier_compagnies(o, cies).assign(origine=o) for o in departs],
                          ignore_index=True)
        dates = dates.merge(avec_prix_cat[["origine", "destination"]], on=["origine", "destination"])
        if avec_vol:
            dates = dates[pd.to_numeric(dates["prix_compagnie"], errors="coerce").notna() | ~dates["collecte_compagnie"]]
        # Période affichée (par défaut toute l'année)
        h1, h2 = st.columns([3, 3])
        periode = h1.date_input("Dates de départ", value=(), min_value=date.today(), format="DD/MM/YYYY",
                                help="Choisissez une date de début puis de fin ; vide = toutes les dates")
        if len(periode) == 2:
            dates = dates[(dates["date"] >= periode[0].isoformat()) & (dates["date"] <= periode[1].isoformat())]
        elif len(periode) == 1:
            dates = dates[dates["date"] == periode[0].isoformat()]
        # Actualisation en direct : une vraie recherche Google par destination et par date des lignes affichées
        a_actualiser = sorted({(d, j) for d, j in zip(dates["destination"], dates["date"])
                               if j >= date.today().isoformat()})
        trop = len(a_actualiser) > MAX_DIRECT
        h2.write("")
        if h2.button(f"🔄 Actualiser en direct ({len(a_actualiser)} recherche(s), ~{len(a_actualiser) * 10 // 60 + 1} min)",
                     disabled=trop or not a_actualiser,
                     help=(f"Plus de {MAX_DIRECT} recherches : filtrez par villes d'arrivée ou par dates." if trop else
                           "Interroge Google Flights maintenant pour chaque destination et date affichée : prix, "
                           "compagnie la moins chère et bagage du moment.")):
            with st.status("Recherche en direct sur Google Flights…", expanded=True) as statut:
                barre = st.progress(0.0)
                try:
                    n = asyncio.run(catalogue.actualiser_direct(
                        a_actualiser, departs,
                        on_progress=lambda i, total, msg: barre.progress(i / total, text=f"{i}/{total} : {msg}")))
                    statut.update(label=f"Actualisé : {n}/{len(a_actualiser)} avec vols", state="complete")
                except catalogue.Bloque:
                    statut.update(label="Google bloque temporairement les recherches (captcha). Réessayez plus tard.",
                                  state="error")
                    st.stop()
            st.rerun()
        dates = dates.sort_values(["date", "ville_arrivee", "origine", "code_compagnie"], ignore_index=True)
        st.caption(f"{len(dates):,} lignes".replace(",", " ")
                   + f" ({len(departs)} aéroport(s) de départ × {dates['destination'].nunique()} destinations × dates"
                   f" × {len(cies)} compagnie(s)). "
                   "« Prix le plus bas » = toutes compagnies confondues (catalogue). « Prix de la compagnie » = prix de "
                   "cette compagnie ce jour-là ; « Bagage » = bagage cabine inclus sur son vol le moins cher du "
                   f"trajet. « {PAS_DE_VOL} » : la compagnie ne propose pas de vol ce jour-là ; « {A_COMPLETER} » : "
                   "trajet pas encore collecté pour cette compagnie.")
        # Dates recherchées une à une (catalogue.py jour), plus récentes que le calendrier : leur prix le plus bas
        # et leur date de collecte remplacent ceux du catalogue, et elles nomment la compagnie la moins chère
        du_jour = catalogue.moins_cher_du_jour(departs)
        du_jour = du_jour.rename(columns={"heure_depart": "heure_depart_mc", "heure_arrivee": "heure_arrivee_mc",
                                          "duree": "duree_mc"})
        dates = dates.merge(du_jour, how="left", on=["origine", "destination", "date"])
        recherche = dates["prix_moins_cher"].notna()
        dates.loc[recherche, "prix"] = dates.loc[recherche, "prix_moins_cher"]
        dates.loc[recherche, "collecte_le"] = dates.loc[recherche, "collecte_moins_cher"]
        bagage = pd.to_numeric(dates["bagage"], errors="coerce")
        prix_comp = pd.to_numeric(dates["prix_compagnie"], errors="coerce")
        # Trajet collecté pour la compagnie mais sans prix ce jour-là : elle ne vole pas ce jour (ou pas sur ce trajet)
        sans_vol = prix_comp.isna() & dates["collecte_compagnie"]
        # Compagnie qui fait le prix le plus bas du jour : parmi les compagnies collectées, celle(s) dont le prix
        # égale le prix du catalogue ; sinon le prix vient d'une compagnie non collectée (le calendrier ne la nomme pas)
        egal = dates.assign(c=dates["code_compagnie"].where(prix_comp <= dates["prix"]))
        moins_cher = (egal.dropna(subset=["c"]).groupby(["origine", "destination", "date"])["c"]
                      .agg(lambda s: "/".join(sorted(set(s)))))
        moins_cher = pd.Series([f"{nom} ({code})" if isinstance(nom, str) else moins_cher.get(k, "autre compagnie")
                                for k, nom, code in zip(zip(dates["origine"], dates["destination"], dates["date"]),
                                                        dates["nom_moins_cher"], dates["code_moins_cher"])],
                               index=dates.index)
        tableau = pd.DataFrame({
            "Départ": [f"{nom_aeroport(o, origines[o])} ({o})" for o in dates["origine"]],
            "Ville d'arrivée": dates["ville_arrivee"], "Code": dates["destination"],
            "Pays": dates["destination"].map(pays_aeroport),
            "Date": pd.to_datetime(dates["date"]).dt.strftime("%d/%m/%Y"),
            "Prix le plus bas (toutes compagnies)": dates["prix"],
            "Compagnie la moins chère": moins_cher,
            # horaires du vol le moins cher (dates recherchées en direct ou par « catalogue.py jour »)
            "Heure de départ": dates["heure_depart_mc"].fillna("—"),
            "Heure d'arrivée": dates["heure_arrivee_mc"].fillna("—"),
            "Durée": dates["duree_mc"].fillna("—"),
            "Code compagnie": dates["code_compagnie"],
            # une seule compagnie choisie : la colonne porte son nom (ex. « Prix Air Europa »)
            (f"Prix {noms_cies.get(cies[0], cies[0])}" if len(cies) == 1 else "Prix de la compagnie"): prix_comp.map(lambda v: A_COMPLETER if pd.isna(v) else f"{v:.0f} €").mask(sans_vol, PAS_DE_VOL),
            "Bagage": bagage.map({1: "True", 0: "False"}).fillna(A_COMPLETER).mask(sans_vol, PAS_DE_VOL),
            "Collecté le": pd.to_datetime(dates["collecte_le"]).dt.strftime("%d/%m/%Y %H:%M"),
        })
        # lazy=False : au-delà de 150 000 lignes Streamlit charge le tableau par morceaux, liés à la session du
        # navigateur ; après une reconnexion, ces morceaux échouent (« Failed to load data »). On envoie tout
        # d'un coup, ce qui garde aussi la recherche et le téléchargement CSV du tableau.
        st.dataframe(tableau, hide_index=True, width="stretch", lazy=False,
                     column_config={"Prix le plus bas (toutes compagnies)": st.column_config.NumberColumn(
                         format="%d €", help="Prix le plus bas ce jour-là, toutes compagnies confondues : il peut venir "
                                             "d'une autre compagnie que celle de la ligne")})
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
if not combine:
    nom_depart = f"{nom_aeroport(origine, origines[origine])} ({origine})"
elif departs:  # plusieurs aéroports choisis, ex. Paris (CDG, ORY, BVA)
    nom_depart = ", ".join(f"{nom_aeroport(o, origines[o])} ({o})" for o in departs)
else:
    nom_depart = pays_dep if pays_dep != TOUS_PAYS else "tous les départs"
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
        on_select="rerun", selection_mode="multi-row", key=f"dates_{'-'.join(departs)}_{destination}_{duree}",
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
