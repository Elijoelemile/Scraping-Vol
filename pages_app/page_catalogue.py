"""Page « Catalogue » : prix le plus bas de chaque jour sur 1 an, par trajet (données collectées par catalogue.py)."""
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import catalogue
import db
import recap
from pays import pays_aeroport
from google_flights_scraper import Trajet, vers_dataframe
from titres import titre_page

COULEUR = "#2a78d6"
COMPAGNIES = ["VY", "UX"]  # compagnies par défaut du tableau de toutes les dates (Vueling, Air Europa)
LISTE_COMPAGNIES = Path(__file__).parent.parent / "listes" / "compagnies.txt"  # codes proposés (ceux d'Airfox)
MAX_LIGNES = 600_000  # taille maximale du tableau de toutes les dates
def temps_texte(secondes: float) -> str:
    """Durée lisible : « 45 s », « 12 min », « 1 h 05 min », « 2 j 1 h »."""
    if secondes < 60:
        return f"{max(0, round(secondes))} s"
    minutes = round(secondes / 60)
    if minutes < 60:
        return f"{minutes} min"
    heures, minutes = divmod(minutes, 60)
    return f"{heures // 24} j {heures % 24} h" if heures >= 24 else f"{heures} h {minutes:02d} min"


def duree_texte(n: int) -> str:
    """Durée estimée de n recherches (≈ 3,2 s chacune, 4 en parallèle), ex. « ~1 h 15 », « ~2 j 1 h »."""
    return "~" + temps_texte(max(60, n * catalogue.SECONDES_PAR_RECHERCHE))


@st.fragment(run_every=3)
def suivi_actualisation():
    """Avancement de l'actualisation en arrière-plan (relu toutes les 3 s), bouton Arrêter ; à la fin, le tableau
    est rechargé une fois pour montrer les nouvelles données."""
    e = catalogue.etat_actualisation()
    if not e:
        return
    faites, total = e.get("faites", 0), max(1, e.get("total", 1))
    debut = datetime.fromisoformat(e["debut"]) if e.get("debut") else None
    if e["statut"] in ("en cours", "pause"):
        st.session_state["suivi_actif"] = True
        ecoule = (datetime.now() - debut).total_seconds() if debut else 0
        # temps restant : au rythme réellement observé dès 20 recherches faites, sinon l'estimation de départ
        reste = (ecoule / faites * (total - faites) if faites >= 20
                 else (total - faites) * catalogue.SECONDES_PAR_RECHERCHE)
        fin_prevue = datetime.now() + timedelta(seconds=reste)
        jour_fin = "" if fin_prevue.date() == date.today() else f" le {fin_prevue:%d/%m}"
        with st.container(border=True):
            st.progress(min(1.0, faites / total),
                        text=f"**{e.get('libelle', '')}** : {faites}/{total} recherches ({100 * faites / total:.0f} %)"
                             f" · {e.get('message', '')}")
            st.caption(f"⏱️ Commencée à {debut:%H:%M}" + (f" le {debut:%d/%m}" if debut.date() != date.today() else "")
                       + f" · en cours depuis **{temps_texte(ecoule)}** · reste environ **{temps_texte(reste)}**"
                       f" · fin prévue vers **{fin_prevue:%H:%M}{jour_fin}**" if debut else "")
            c1, c2 = st.columns(2)
            if c1.button("🔃 Voir les résultats déjà obtenus", width="stretch"):
                st.rerun(scope="app")
            if c2.button("⏹ Arrêter", width="stretch", help="Les recherches déjà faites sont gardées ; "
                                                             "« Compléter » reprendra là où elles se sont arrêtées."):
                catalogue.arreter_actualisation()
                st.toast("Arrêt demandé : il prend effet après les recherches en cours (≈ 2 min).")
        return
    if st.session_state.pop("suivi_actif", False):
        st.rerun(scope="app")  # actualisation finie : nouveau tableau
    messages = {"terminée": st.success, "arrêtée": st.info, "bloquée": st.warning, "interrompue": st.warning,
                "erreur": st.error}
    suite = {"terminée": "", "arrêtée": " Cliquez sur « Compléter » pour reprendre.",
             "bloquée": " Relancez « Compléter » plus tard.",
             "interrompue": " (ordinateur en veille ou application arrêtée ?) Cliquez sur « Compléter » pour reprendre.",
             "erreur": " Cliquez sur « Compléter » pour reprendre."}
    fin = datetime.fromisoformat(e.get("fin") or e["maj"])
    duree_reelle = (f" en **{temps_texte((fin - debut).total_seconds())}** (du {debut:%d/%m %H:%M} au "
                    f"{fin:%d/%m %H:%M})" if debut else "")
    messages.get(e["statut"], st.info)(
        f"Dernière actualisation ({e.get('libelle', '')}) : {e['statut']}{duree_reelle}, {faites}/{total} recherches, "
        f"{e.get('trouvees', 0)} avec vols. {e.get('message', '') if e['statut'] in ('erreur', 'terminée') else ''}"
        + suite.get(e["statut"], ""))



def tableau_historique(bilans: list[dict]) -> pd.DataFrame:
    """Une ligne par actualisation : période recherchée, date et heure de lancement, durée."""
    def periode(b):
        jours = b.get("periode")
        return f"{pd.Timestamp(jours[0]):%d/%m/%Y} --- {pd.Timestamp(jours[1]):%d/%m/%Y}" if jours else "—"
    return pd.DataFrame({
        "Période": [periode(b) for b in bilans],
        "Date": [f"{datetime.fromisoformat(b['debut']):%d/%m/%Y}" for b in bilans],
        "Heure": [f"{datetime.fromisoformat(b['debut']):%H:%M}" for b in bilans],
        "Durée": [temps_texte((datetime.fromisoformat(b["fin"]) - datetime.fromisoformat(b["debut"])).total_seconds())
                  for b in bilans],
    })


@st.cache_data(show_spinner="Préparation du PDF…")
def pdf_actualisations(bilans: list[dict]) -> bytes:
    """PDF : historique des actualisations jusqu'à la date choisie (période, date, heure, durée)."""
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    polices = Path("C:/Windows/Fonts")
    if (polices / "arial.ttf").exists():  # police Unicode : tous les accents
        pdf.add_font("Arial", "", str(polices / "arial.ttf"))
        pdf.add_font("Arial", "B", str(polices / "arialbd.ttf"))
        police = "Arial"
    else:
        police = "Helvetica"
    pdf.add_page()
    pdf.set_font(police, "B", 16)
    pdf.cell(0, 10, "Historique des actualisations", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font(police, "", 11)
    with pdf.table(col_widths=(70, 40, 30, 40), text_align="LEFT", line_height=7,
                   headings_style=FontFace(emphasis="NONE")) as tab:
        tableau = tableau_historique(bilans)
        tab.row(list(tableau.columns))
        for ligne in tableau.itertuples(index=False):
            tab.row([str(v) for v in ligne])
    return bytes(pdf.output())


def historique_des_actualisations():
    """Choix d'une date d'actualisation : historique de celle-ci et des précédentes, à l'écran et en PDF."""
    bilans = catalogue.historique_actualisations()
    if not bilans:
        return
    with st.expander(f"📜 Historique des actualisations ({len(bilans)})"):
        choix = st.selectbox("Date d'actualisation", range(len(bilans)), index=None,
                             format_func=lambda i: f"{datetime.fromisoformat(bilans[i]['debut']):%d/%m/%Y %H:%M} — "
                                                   f"{bilans[i].get('libelle', '')}",
                             placeholder="Choisir une date d'actualisation", key="historique_actualisation")
        if choix is None:
            st.caption("Choisissez une date : l'historique affiche cette actualisation et toutes les précédentes.")
            return
        precedentes = bilans[choix:]  # la liste va de la plus récente à la plus ancienne
        st.dataframe(tableau_historique(precedentes), hide_index=True, width="stretch")
        st.download_button("Télécharger (PDF)", pdf_actualisations(precedentes),
                           file_name=f"Historique des actualisations au {bilans[choix]['id']}.pdf",
                           mime="application/pdf", key="telecharger_actualisation",
                           help="Ce tableau en PDF : période recherchée, date, heure et durée de chaque actualisation "
                                "jusqu'à cette date.")


LISTE_AIRFOX = Path(__file__).parent.parent / "listes" / "airfox.txt"  # destinations actualisées par défaut
A_COMPLETER = "à compléter"
PAS_DE_VOL = "pas de vol"
AUCUN_VOL = "aucun vol trouvé"  # date recherchée en direct, aucun vol depuis cet aéroport
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

titre_page("Catalogue des prix sur 1 an", "catalogue")
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
# Tous les champs sont vides à l'ouverture : vide = pas de filtre
pays_dep = p1.selectbox("Pays de départ", liste_pays_dep, index=None, placeholder=TOUS_PAYS)
if pays_dep is not None:
    tous = tous[tous["pays_depart"] == pays_dep]
pays_arr = p2.selectbox("Pays d'arrivée", sorted(tous["pays_arrivee"].unique()), index=None, placeholder=TOUS_PAYS)
if pays_arr is not None:
    tous = tous[tous["pays_arrivee"] == pays_arr]
if tous.empty:
    st.warning("Aucun trajet du catalogue pour ces pays.")
    st.stop()

origines = (tous.groupby("origine")["ville_depart"].first().fillna("").sort_values())
f1, f2 = st.columns(2)
# Départ et arrivée sont facultatifs : « Tous les départs » = tous les aéroports des pays choisis.
# Vide à l'ouverture ; on peut en choisir plusieurs (bouton : tous les aéroports de Paris).
# Plusieurs aéroports possibles (ex. tous ceux de Paris) ; aucun = « Tous les départs ».
codes_origines = sorted(origines.index, key=lambda o: (nom_aeroport(o, origines[o]), o))
if "departs" not in st.session_state:
    st.session_state["departs"] = []
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
        # Compagnies au choix (toutes celles d'Airfox) ; aucune à l'ouverture (une ligne par date)
        collectees = catalogue.compagnies_collectees() or COMPAGNIES
        noms_cies = dict(catalogue.lire_liste(str(LISTE_COMPAGNIES))) if LISTE_COMPAGNIES.exists() else {}
        options_cies = collectees + [c for c in noms_cies if c not in collectees]
        g1, g2, g3 = st.columns([3, 2, 1])
        cies = g2.multiselect("Compagnies", options_cies, default=[], placeholder="Choisir des compagnies",
                              format_func=lambda c: f"{c} — {noms_cies.get(c, c)}"
                                                    + (" ✓" if c in collectees else ""),
                              help="✓ = calendrier collecté. Les autres compagnies s'affichent « à compléter » "
                                   "tant que leur collecte n'a pas été faite.")
        # Aucune compagnie choisie : une ligne par date, sans les colonnes propres à une compagnie
        sans_compagnie = not cies
        cies = cies or collectees  # les compagnies collectées servent quand même à nommer la moins chère
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
        # Dates de départ libres, comme sur Google Flights : un jour (un clic) ou une période (deux clics : début, fin)
        h1, h3, h4 = st.columns([3, 2, 2])  # « Nuits » assez large pour afficher les boutons − et +
        fin_calendrier = pd.Timestamp(dates["date"].max()).date() if not dates.empty else None
        jours_depart = h1.date_input("Dates de départ", value=(), min_value=date.today(), max_value=fin_calendrier,
                                     format="DD/MM/YYYY", key="dates_toutes_dates",
                                     help="Un clic sur une date = ce jour seulement ; un deuxième clic = fin de la "
                                          "période (ex. du 25/10 au 15/11). Vide = toutes les dates jusqu'au "
                                          + (f"{fin_calendrier:%d/%m/%Y}" if fin_calendrier else "bout du calendrier")
                                          + ". En aller-retour, la date de retour est déduite du nombre de nuits.")
        # le choix est gardé d'une page à l'autre (Streamlit oublie les widgets d'une page quittée)
        choix_precedent = st.session_state.get("nuits_catalogue")
        # vide à l'ouverture ; type choisi « Aller simple » = 0 nuit gardé pour le Comparatif
        type_trajet = h3.radio("Type de trajet", ["Aller simple", "Aller-retour"], horizontal=True,
                               index=None if choix_precedent is None else 1 if choix_precedent else 0,
                               key="type_toutes_dates")
        aller_retour_tab = type_trajet == "Aller-retour"
        nuits_tab = h4.number_input("Nombre de nuits", 1, 60, choix_precedent or None, disabled=not aller_retour_tab,
                                    key="nuits_toutes_dates", placeholder="—",
                                    help="Durée du séjour : date de retour = date de départ + nuits")
        nuits_ar = int(nuits_tab) if aller_retour_tab and nuits_tab else None
        if type_trajet is not None and not (aller_retour_tab and not nuits_tab):
            st.session_state["nuits_catalogue"] = nuits_ar or 0  # repris par la page Comparatif Airfox
        # Dates retenues : un jour, une période (début → fin) ou, sans choix, toutes
        if jours_depart:
            premier, dernier = jours_depart[0], jours_depart[-1]
            dates = dates[(dates["date"] >= premier.isoformat()) & (dates["date"] <= dernier.isoformat())]
        if not dates.empty:
            st.caption(f"Dates de départ retenues : du {pd.Timestamp(dates['date'].min()):%d/%m/%Y} au "
                       f"{pd.Timestamp(dates['date'].max()):%d/%m/%Y} ({dates['date'].nunique()} jour(s)).")
        if aller_retour_tab and not nuits_tab:
            st.info("Indiquez le nombre de nuits : la date de retour en sera déduite.")
            st.stop()
        # Actualisation : une vraie recherche Google par destination et par date des lignes affichées.
        # Sans ville choisie : seulement les destinations du fichier Airfox (≈ 196 par date, ~10 min)
        airfox = {c for c, _ in catalogue.lire_liste(str(LISTE_AIRFOX))} if LISTE_AIRFOX.exists() else set()
        a_actualiser = sorted({(d, j) for d, j in zip(dates["destination"], dates["date"])
                               if j >= date.today().isoformat() and (villes or not airfox or d in airfox)},
                              key=lambda x: (x[1], x[0]))  # date par date : les premiers jours sont prêts d'abord
        # Déjà recherchées pour ce type de trajet (avec ou sans vol) : « Compléter » ne fait que les manquantes
        # Une recherche « Paris » couvre les 4 aéroports parisiens : elle compte pour chacun, même si ses vols
        # partent d'un autre aéroport que celui affiché. Heure de la recherche par destination et date.
        heures_recherche = (catalogue.vols_jour_tableau(sorted(set(departs) | set(catalogue.PARIS_AEROPORTS)), nuits_ar)
                            .groupby(["destination", "date"])["collecte_le"].max())
        deja = set(heures_recherche.index)
        manquantes = [p for p in a_actualiser if p not in deja]

        def libelle_periode() -> str:
            jours = sorted({j for _, j in a_actualiser})
            trajet = f"aller-retour {nuits_ar} nuits" if nuits_ar else "aller simple"
            if not jours:
                return trajet
            return ((f"Du {pd.Timestamp(jours[0]):%d/%m/%Y} au {pd.Timestamp(jours[-1]):%d/%m/%Y}" if len(jours) > 1
                     else f"Le {pd.Timestamp(jours[0]):%d/%m/%Y}") + f", {trajet}")

        def lancer(paires):
            catalogue.lancer_actualisation(paires, departs, nuits_ar, libelle_periode())
            st.session_state["suivi_actif"] = True
            st.rerun()

        aide = ("Interroge Google Flights (4 recherches à la fois) pour chaque destination "
                + ("choisie" if villes else "du fichier Airfox") + " et chaque date choisie : prix, compagnie "
                "la moins chère, heures de départ et d'arrivée, durée et bagage. Les recherches tournent en "
                "arrière-plan : vous pouvez quitter la page ou fermer le navigateur, mais l'ordinateur doit rester "
                "allumé. Si Google bloque, elles reprennent seules au bout de "
                f"{catalogue.PAUSE_BLOCAGE_MIN} min.")
        actif = catalogue.actualisation_active()
        b1, b2, b3 = st.columns(3)
        # « Compléter » n'apparaît que s'il reste des destinations jamais recherchées pour ces dates
        if manquantes and b1.button(f"🔄 Compléter ({len(manquantes)} recherches, {duree_texte(len(manquantes))})",
                                    disabled=actif, type="primary", width="stretch",
                                    help="Une actualisation est déjà en cours." if actif else
                                    "Seulement les destinations et dates pas encore recherchées. " + aide):
            lancer(manquantes)
        if b2.button(f"Tout réactualiser ({len(a_actualiser)} recherches, {duree_texte(len(a_actualiser))})",
                     disabled=actif or not a_actualiser, width="stretch",
                     help="Une actualisation est déjà en cours." if actif else
                     "Refait toutes les recherches des dates choisies, même celles déjà faites, pour avoir les prix du "
                     "moment. " + aide):
            lancer(a_actualiser)
        # Toute l'année d'un coup (sans date choisie) : calendrier « Paris » de chaque destination, puis celui de ses
        # compagnies les moins chères → prix, compagnie et bagage de chaque date (sans les heures, en ~40 min)
        dests_annee = sorted({d for d, _ in a_actualiser})
        if (not jours_depart and dests_annee
                and set(departs) <= set(catalogue.PARIS_AEROPORTS)
                and b3.button(f"📅 Prix de l'année seulement ({len(dests_annee)} destinations, "
                              f"~{catalogue.duree_annee(len(dests_annee))} min)", disabled=actif, width="stretch",
                              help="Plus rapide, sans les heures : pour chaque destination, le prix le plus bas de "
                                   "chaque date jusqu'à la fin du calendrier Google, la compagnie qui le propose et "
                                   "son bagage cabine. Ne touchez pas à la page pendant la recherche.")):
            with st.status("Calendriers de l'année sur Google Flights…", expanded=True) as statut:
                barre = st.progress(0.0)
                try:
                    n = asyncio.run(catalogue.actualiser_annee(
                        dests_annee, nuits_ar,
                        on_progress=lambda i, total, msg: barre.progress(i / total, text=f"{i}/{total} : {msg}")))
                    statut.update(label=f"Année actualisée : {n}/{len(dests_annee)} destinations avec prix",
                                  state="complete")
                except catalogue.Bloque:
                    statut.update(label="Google bloque temporairement les recherches (captcha). Les destinations "
                                        "déjà faites sont gardées ; réessayez plus tard.", state="error")
                    st.stop()
            st.rerun()
        suivi_actualisation()
        historique_des_actualisations()
        if nuits_ar:
            # Aller-retour : les prix aller simple ne valent plus. Prix le plus bas = calendrier aller-retour s'il a
            # été collecté ; prix et bagage de chaque compagnie = recherches en direct de cet aller-retour
            dates["date_retour"] = catalogue._retours(dates["date"], nuits_ar)
            dates = dates.drop(columns=["prix_compagnie", "bagage", "collecte_compagnie"]).merge(
                catalogue.calendrier_ar_origines(departs, nuits_ar), how="left", on=["origine", "destination", "date"])
            dates["prix"], dates["collecte_le"] = dates["prix_ar"], dates["collecte_ar"]
            vj = catalogue.vols_jour_tableau(departs, nuits_ar, annee=True)  # recherches d'une date + toute l'année
            cherchees = deja  # recherches « Paris » faites pour ces dates (quel que soit l'aéroport des vols trouvés)
            dates = dates.merge(vj[vj["origine"] != "-"][["origine", "destination", "date", "compagnie", "prix", "bagage"]]
                                .rename(columns={"compagnie": "code_compagnie", "prix": "prix_compagnie"}),
                                how="left", on=["origine", "destination", "date", "code_compagnie"])
            # aller-retour recherché ce jour-là : une compagnie absente des résultats n'a pas de vol
            dates["collecte_compagnie"] = [(d, j) in cherchees for d, j in zip(dates["destination"], dates["date"])]
        else:  # aller simple : prix et bagage manquants complétés par « toute l'année »
            va = catalogue.vols_jour_tableau(departs, None, annee=True)
            va = (va[va["origine"] != "-"].sort_values("prix")
                  .drop_duplicates(["origine", "destination", "date", "compagnie"])
                  [["origine", "destination", "date", "compagnie", "prix", "bagage"]]
                  .rename(columns={"compagnie": "code_compagnie", "prix": "prix_annee", "bagage": "bagage_annee"}))
            dates = dates.merge(va, how="left", on=["origine", "destination", "date", "code_compagnie"])
            dates["prix_compagnie"] = pd.to_numeric(dates["prix_compagnie"], errors="coerce").fillna(dates["prix_annee"])
            dates["bagage"] = pd.to_numeric(dates["bagage"], errors="coerce").fillna(dates["bagage_annee"])
        if avec_vol and not sans_compagnie:
            dates = dates[pd.to_numeric(dates["prix_compagnie"], errors="coerce").notna() | ~dates["collecte_compagnie"]]
        dates = dates.sort_values(["date", "ville_arrivee", "origine", "code_compagnie"], ignore_index=True)
        st.caption(f"{len(dates):,} lignes".replace(",", " ")
                   + f" ({len(departs)} aéroport(s) de départ × {dates['destination'].nunique()} destinations × dates"
                   + ("). " if sans_compagnie else f" × {len(cies)} compagnie(s)). ")
                   + "« Prix le plus bas » = toutes compagnies confondues (catalogue). « Prix de la compagnie » = prix de "
                   "cette compagnie ce jour-là ; « Bagage » = bagage cabine inclus sur son vol le moins cher du "
                   f"trajet. « {PAS_DE_VOL} » : la compagnie ne propose pas de vol ce jour-là ; « {A_COMPLETER} » : "
                   "trajet pas encore collecté pour cette compagnie.")
        # Dates recherchées une à une (catalogue.py jour), plus récentes que le calendrier : leur prix le plus bas
        # et leur date de collecte remplacent ceux du catalogue, et elles nomment la compagnie la moins chère
        du_jour = catalogue.moins_cher_du_jour(departs, nuits_ar)
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
        # Bagage cabine de la compagnie la moins chère : recherche d'une date, sinon la compagnie collectée la moins chère
        bagage_egal = (egal.dropna(subset=["c"]).assign(b=pd.to_numeric(egal["bagage"], errors="coerce"))
                       .dropna(subset=["b"]).groupby(["origine", "destination", "date"])["b"].first())
        bagage_mc = pd.Series([b if pd.notna(b) else bagage_egal.get(k) for k, b in zip(
            zip(dates["origine"], dates["destination"], dates["date"]),
            pd.to_numeric(dates["bagage_moins_cher"], errors="coerce"))], index=dates.index)
        # Date recherchée (bouton « Actualiser ») mais aucun vol trouvé depuis cet aéroport : on le dit,
        # au lieu de « à compléter » qui laisserait croire que rien n'a été recherché
        cherchees_jour = set(heures_recherche.index)
        sans_resultat = pd.Series([(d, j) in cherchees_jour and not isinstance(n, str) for d, j, n in zip(
            dates["destination"], dates["date"], dates["nom_moins_cher"])], index=dates.index)
        moins_cher = moins_cher.mask(sans_resultat & (moins_cher == "autre compagnie"), AUCUN_VOL)
        tableau = pd.DataFrame({
            "Départ": [f"{nom_aeroport(o, origines[o])} ({o})" for o in dates["origine"]],
            "Ville d'arrivée": dates["ville_arrivee"], "Code": dates["destination"],
            "Pays": dates["destination"].map(pays_aeroport),
            "Date": pd.to_datetime(dates["date"]).dt.strftime("%d/%m/%Y"),
            **({"Date de retour": pd.to_datetime(dates["date_retour"]).dt.strftime("%d/%m/%Y")} if nuits_ar else {}),
            "Prix le plus bas (toutes compagnies)": dates["prix"],
            "Compagnie la moins chère": moins_cher,
            # horaires du vol le moins cher (dates recherchées en direct ou par « catalogue.py jour »)
            # « à compléter » : pas encore de recherche de cette date (le calendrier de l'année n'a pas les horaires)
            "Heure de départ": dates["heure_depart_mc"].fillna(A_COMPLETER).mask(sans_resultat, "—"),
            "Heure d'arrivée": dates["heure_arrivee_mc"].fillna(A_COMPLETER).mask(sans_resultat, "—"),
            "Durée": dates["duree_mc"].fillna(A_COMPLETER).mask(sans_resultat, "—"),
            "Bagage (moins chère)": bagage_mc.map({1: "True", 0: "False"}).fillna(A_COMPLETER)
                                    .mask(sans_resultat & bagage_mc.isna(), AUCUN_VOL),
            "Code compagnie": dates["code_compagnie"],
            # une seule compagnie choisie : la colonne porte son nom (ex. « Prix Air Europa »)
            (f"Prix {noms_cies.get(cies[0], cies[0])}" if len(cies) == 1 else "Prix de la compagnie"): prix_comp.map(lambda v: A_COMPLETER if pd.isna(v) else f"{v:.0f} €").mask(sans_vol, PAS_DE_VOL),
            "Bagage": bagage.map({1: "True", 0: "False"}).fillna(A_COMPLETER).mask(sans_vol, PAS_DE_VOL),
            # « aucun vol trouvé » : l'heure de la recherche qui n'a rien trouvé
            "Collecté le": pd.to_datetime(dates["collecte_le"].where(~sans_resultat, [
                heures_recherche.get((d, j)) for d, j in zip(dates["destination"], dates["date"])]))
                .dt.strftime("%d/%m/%Y %H:%M").fillna("—"),
            "_aucun_vol": sans_resultat,  # colonne de travail, retirée avant l'affichage
        })
        if sans_compagnie:  # une ligne par date : sans les colonnes d'une compagnie, « Bagage » = celui de la moins chère
            tableau = (tableau.drop(columns=[c for c in tableau.columns
                                             if c in ("Code compagnie", "Bagage") or c.startswith("Prix ")
                                             and c != "Prix le plus bas (toutes compagnies)"])
                       .rename(columns={"Bagage (moins chère)": "Bagage"})
                       .drop_duplicates(["Départ", "Code", "Date"], ignore_index=True))
        else:
            tableau = tableau.drop(columns="Bagage (moins chère)")
        # lazy=False : au-delà de 150 000 lignes Streamlit charge le tableau par morceaux, liés à la session du
        # navigateur ; après une reconnexion, ces morceaux échouent (« Failed to load data »). On envoie tout
        # d'un coup, ce qui garde aussi la recherche et le téléchargement CSV du tableau.
        PLUS_BAS = "Prix le plus bas (toutes compagnies)"
        aide = ("Prix le plus bas ce jour-là, toutes compagnies confondues : il peut venir d'une autre compagnie que "
                f"celle de la ligne. « {A_COMPLETER} » : pas encore recherché (ex. aller-retour non actualisé).")
        if tableau[PLUS_BAS].isna().any():  # prix inconnus : « à compléter » plutôt que « None »
            tableau[PLUS_BAS] = [(AUCUN_VOL if vide else A_COMPLETER) if pd.isna(v) else f"{v:.0f} €"
                                 for v, vide in zip(tableau[PLUS_BAS], tableau["_aucun_vol"])]
            colonne_prix = st.column_config.TextColumn(help=aide)
        else:
            colonne_prix = st.column_config.NumberColumn(format="%d €", help=aide)
        st.dataframe(tableau.drop(columns="_aucun_vol"), hide_index=True, width="stretch", lazy=False,
                     column_config={PLUS_BAS: colonne_prix})
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
