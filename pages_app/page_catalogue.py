"""Page « Catalogue » : tarifs aller-retour Google Flights des trajets de la donnée chargée, date par date
(recherches en arrière-plan, historique des actualisations)."""
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import catalogue
from pays import pays_aeroport
from titres import titre_page

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
    """Durée estimée de n recherches au rythme actuel (adaptatif), ex. « ~1 h 15 », « ~2 j 1 h »."""
    return "~" + temps_texte(max(60, n * catalogue.secondes_par_recherche()))


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
                 else (total - faites) * catalogue.secondes_par_recherche())
        fin_prevue = datetime.now() + timedelta(seconds=reste)
        jour_fin = "" if fin_prevue.date() == date.today() else f" le {fin_prevue:%d/%m}"
        with st.container(border=True, key="cadre_suivi"):  # cadre aux couleurs de la page (titres.py)
            st.progress(min(1.0, faites / total),
                        text=f"**{e.get('libelle', '')}** : {faites}/{total} recherches ({100 * faites / total:.0f} %)"
                             f" · {e.get('message', '')}")
            st.caption(f"⏱️ Commencée à {debut:%H:%M}" + (f" le {debut:%d/%m}" if debut.date() != date.today() else "")
                       + f" · en cours depuis **{temps_texte(ecoule)}** · reste environ **{temps_texte(reste)}**"
                       f" · fin prévue vers **{fin_prevue:%H:%M}{jour_fin}**"
                       + (f" · rythme : {e['rythme']}" if e.get("rythme") else "") if debut else "")
            en_pause = e["statut"] == "pause"
            c1, c2, *c3 = st.columns(3 if en_pause else 2)
            # pendant une pause : choisir de ne pas attendre (au risque d'un nouveau blocage si Google bloque encore)
            if en_pause and c3[0].button("▶️ Reprendre maintenant", type="primary", width="stretch",
                                         help="Finit la pause tout de suite au lieu d'attendre l'heure de reprise. "
                                              "Si Google bloque encore, une nouvelle pause commencera."):
                catalogue.reprendre_maintenant()
                st.toast("Reprise demandée : elle a lieu dans quelques secondes.")
            if c1.button("🔃 Voir les résultats déjà obtenus", width="stretch"):
                st.rerun(scope="app")
            if c2.button("⏹ Arrêter", width="stretch", help="Les recherches déjà faites sont gardées ; "
                                                             "le bouton « Reprendre » permettra de continuer là où elles se sont arrêtées."):
                catalogue.arreter_actualisation()
                st.toast("Arrêt demandé : il prend effet après les recherches en cours (≈ 2 min).")
        return
    if st.session_state.pop("suivi_actif", False):
        st.rerun(scope="app")  # actualisation finie : nouveau tableau
    messages = {"terminée": st.success, "arrêtée": st.info, "bloquée": st.warning, "interrompue": st.warning,
                "erreur": st.error}
    suite = {"terminée": "", "arrêtée": "", "bloquée": " Reprenez-la un peu plus tard.",
             "interrompue": " (ordinateur en veille ou application arrêtée ?)", "erreur": ""}
    fin = datetime.fromisoformat(e.get("fin") or e["maj"])
    duree_reelle = (f" en **{temps_texte((fin - debut).total_seconds())}** (du {debut:%d/%m %H:%M} au "
                    f"{fin:%d/%m %H:%M})" if debut else "")
    messages.get(e["statut"], st.info)(
        f"Dernière actualisation ({e.get('libelle', '')}) : {e['statut']}{duree_reelle}, {faites}/{total} recherches, "
        f"{e.get('trouvees', 0)} avec vols. {e.get('message', '') if e['statut'] in ('erreur', 'terminée') else ''}"
        + suite.get(e["statut"], ""))
    # Reprise en un clic, juste sous le message : les recherches restantes, mêmes départs et même nombre de nuits
    restantes = recherches_restantes_affichees(e.get("debut"), e.get("maj"))
    if restantes and (e["statut"] != "terminée" or e.get("incompletes")):
        if st.button(f"▶️ Reprendre ({restantes} recherches restantes, {duree_texte(restantes)})", type="primary",
                     key="reprendre_actualisation",
                     help="Relance cette actualisation là où elle s'est arrêtée : seulement les recherches pas "
                          "encore faites, avec les mêmes départs et le même nombre de nuits."):
            catalogue.reprendre_actualisation()
            st.session_state["suivi_actif"] = True
            st.rerun(scope="app")


@st.cache_data(ttl=60, show_spinner=False)
def recherches_restantes_affichees(debut, maj) -> int:
    """Nombre de recherches restantes de la dernière actualisation (gardé 60 s : le suivi est relu toutes les 3 s)."""
    return len(catalogue.recherches_restantes())



def tableau_historique(bilans: list[dict]) -> pd.DataFrame:
    """Une ligne par actualisation : période recherchée, date et heure de lancement, durée, blocages de Google."""
    def periode(b):
        jours = b.get("periode")
        return f"{pd.Timestamp(jours[0]):%d/%m/%Y} --- {pd.Timestamp(jours[1]):%d/%m/%Y}" if jours else "—"

    def blocages(b):
        """« aucun », ou « 3 : 05:28, 06:47, 07:37 » (la date est ajoutée si ce n'est plus le jour du lancement)."""
        jour = datetime.fromisoformat(b["debut"]).date()
        heures = catalogue.heures_blocages(b)
        if not heures:
            return "aucun"
        return f"{len(heures)} : " + ", ".join(f"{h:%H:%M}" if h.date() == jour else f"{h:%d/%m %H:%M}"
                                               for h in heures)
    return pd.DataFrame({
        "Période": [periode(b) for b in bilans],
        "Date": [f"{datetime.fromisoformat(b['debut']):%d/%m/%Y}" for b in bilans],
        "Heure": [f"{datetime.fromisoformat(b['debut']):%H:%M}" for b in bilans],
        "Durée": [temps_texte((datetime.fromisoformat(b["fin"]) - datetime.fromisoformat(b["debut"])).total_seconds())
                  for b in bilans],
        "Blocages": [blocages(b) for b in bilans],
    })


@st.cache_data(show_spinner="Préparation du PDF…")
def pdf_actualisations(bilans: list[dict]) -> bytes:
    """PDF : historique des actualisations jusqu'à la date choisie (période, date, heure, durée, blocages)."""
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
    with pdf.table(col_widths=(50, 24, 16, 26, 74), text_align="LEFT", line_height=7,
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
                           help="Ce tableau en PDF : période recherchée, date, heure, durée et blocages de Google de "
                                "chaque actualisation jusqu'à cette date.")


LISTE_AIRFOX = Path(__file__).parent.parent / "listes" / "airfox.txt"  # destinations de la donnée chargée
A_COMPLETER = "à compléter"
PAS_DE_VOL = "pas de vol"
AUCUN_VOL = "aucun vol trouvé"  # date recherchée, aucun vol depuis cet aéroport
HORIZON_JOURS = 327  # dernière date de départ acceptée par Google (≈ J+327)
# Aéroports de Paris (vols commerciaux) ; Beauvais et Vatry sont vendus comme « Paris » par les low-cost
PARIS = ["CDG", "ORY", "BVA", "XCR"]
NOMS_AEROPORTS = {"CDG": "Paris-Charles de Gaulle", "ORY": "Paris-Orly", "BVA": "Paris-Beauvais",
                  "XCR": "Paris-Vatry"}


def nom_aeroport(code, ville):
    return NOMS_AEROPORTS.get(code) or ville or code


titre_page("Catalogue des prix aller-retour", "catalogue")
st.caption("Tarifs aller-retour Google Flights des trajets de la donnée chargée (1 adulte, classe économique, en euros) : "
           "une recherche par destination et par date de départ, retour au bout du nombre de nuits choisi.")



@st.fragment(run_every=15)
def chiffres_cles():
    """Chiffres du haut de la page, relus toutes les 15 s : ils suivent l'actualisation en cours sans recharger la page."""
    e = catalogue.etat()
    c1, c2, c3 = st.columns(3)
    c1.metric("Trajets de la donnée", f"{e['trajets']:,}".replace(",", " "),
              help=f"{e['destinations']} destinations × aéroports de départ (« Charger la donnée »)")
    c2.metric("Dates recherchées (aller-retour)", f"{e['recherches']:,}".replace(",", " "),
              help="Couples destination × date de départ déjà recherchés, tous nombres de nuits confondus")
    c3.metric("Dernière actualisation", f"{pd.Timestamp(e['derniere_collecte']):%d/%m %H:%M}"
              if e["derniere_collecte"] else "—", help="Heure de la recherche la plus récente enregistrée (recherches "
                                                       "d'une date ou prix de l'année) ; mise à jour toutes les 15 s")


chiffres_cles()
suivi_actualisation()
historique_des_actualisations()

tous = catalogue.trajets()
if tous.empty:
    st.info("Aucun trajet : chargez d'abord la donnée Airfox dans la page « Charger la donnée ».")
    st.stop()
origines = tous.groupby("origine")["ville_depart"].first().fillna("")

# --- Choix : départs, villes d'arrivée, compagnies, dates de départ, nombre de nuits -------------------------------
# Départ choisi par ville : « Paris » = ses 4 aéroports (CDG, ORY, BVA, XCR), recherchés ensemble comme sur Google
# Flights ; un aéroport hors de ces villes reste proposé seul.
villes_depart = {}
for o in origines.index:
    villes_depart.setdefault("PAR" if o in PARIS else o, []).append(o)
noms_villes_depart = {v: "Paris" if v == "PAR" else f"{nom_aeroport(v, origines[v])} ({v})" for v in villes_depart}
codes_villes = sorted(villes_depart, key=noms_villes_depart.get)
if "villes_depart" not in st.session_state:
    st.session_state["villes_depart"] = []
st.session_state["villes_depart"] = [v for v in st.session_state["villes_depart"] if v in codes_villes]
f1, f2 = st.columns(2)
choix_depart = f1.multiselect("Départ", codes_villes, key="villes_depart", placeholder="Choisir une ville de départ",
                              format_func=noms_villes_depart.get,
                              help="Paris : les 4 aéroports (Charles de Gaulle, Orly, Beauvais, Vatry) en même temps")
departs = sorted(a for v in choix_depart for a in villes_depart[v])  # aéroports utilisés pour la recherche
trajets_dep = tous[tous["origine"].isin(departs)]
villes = f2.multiselect("Villes d'arrivée", sorted(trajets_dep["ville_arrivee"].dropna().unique()),
                        placeholder=f"Toutes ({trajets_dep['destination'].nunique()} destinations)")
if not departs:
    st.info("Choisissez une ville de départ (par exemple Paris).")
    st.stop()
if villes:
    trajets_dep = trajets_dep[trajets_dep["ville_arrivee"].isin(villes)]

noms_cies = dict(catalogue.lire_liste(str(LISTE_COMPAGNIES))) if LISTE_COMPAGNIES.exists() else {}
g1, g2, g3, g4 = st.columns([3, 3, 2, 2])
jours_depart = g1.date_input("Dates de départ", value=(), min_value=date.today(),
                             max_value=date.today() + timedelta(days=HORIZON_JOURS), format="DD/MM/YYYY",
                             key="dates_toutes_dates",
                             help="Un clic sur une date = ce jour seulement ; un deuxième clic = fin de la période "
                                  "(ex. du 28/10 au 30/11). Vide = toutes les dates jusqu'à l'horizon de Google "
                                  f"(J+{HORIZON_JOURS}).")
cies = g2.multiselect("Compagnies", list(noms_cies), default=[], placeholder="Toutes (une ligne par date)",
                      format_func=lambda c: f"{c} — {noms_cies.get(c, c)}",
                      help="Une ligne par date et par compagnie choisie, avec son prix et son bagage ce jour-là.")
# le nombre de nuits est gardé d'une page à l'autre et repris par le Comparatif
choix_precedent = st.session_state.get("nuits_catalogue") or None
nuits_tab = g3.number_input("Nombre de nuits", 1, 60, choix_precedent, key="nuits_toutes_dates", placeholder="—",
                            help="Durée du séjour : date de retour = date de départ + nuits")
avec_vol = g4.checkbox("Masquer « pas de vol »", disabled=not cies,
                       help="Ne garder que les jours où la compagnie a un prix (ou n'a pas encore été recherchée)")
if not nuits_tab:
    st.info("Indiquez le nombre de nuits : la date de retour en sera déduite.")
    st.stop()
nuits_ar = int(nuits_tab)
st.session_state["nuits_catalogue"] = nuits_ar

# --- Squelette : chaque trajet de la donnée × chaque date de départ (× chaque compagnie choisie) --------------------
premier, dernier = ((jours_depart[0], jours_depart[-1]) if jours_depart
                    else (date.today(), date.today() + timedelta(days=HORIZON_JOURS)))
jours = [d.strftime("%Y-%m-%d") for d in pd.date_range(premier, dernier)]
estimation = len(trajets_dep) * len(jours) * max(1, len(cies))
if estimation > MAX_LIGNES:
    st.warning(f"Environ {estimation:,} lignes : c'est trop pour un seul tableau (maximum {MAX_LIGNES:,})."
               .replace(",", " ") + " Choisissez des villes d'arrivée, une période plus courte, moins de compagnies "
                                    "ou moins d'aéroports de départ.")
    st.stop()
dates = (trajets_dep[["origine", "destination", "ville_arrivee"]]
         .merge(pd.DataFrame({"date": jours}), how="cross"))
if cies:
    dates = dates.merge(pd.DataFrame({"code_compagnie": cies}), how="cross")
dates["date_retour"] = catalogue._retours(dates["date"], nuits_ar)
st.caption(f"Dates de départ retenues : du {premier:%d/%m/%Y} au {dernier:%d/%m/%Y} ({len(jours)} jour(s)), "
           f"retour {nuits_ar} nuit(s) plus tard.")

# --- Actualisation : une recherche Google par destination et par date ---------------------------------------------
airfox = {c for c, _ in catalogue.lire_liste(str(LISTE_AIRFOX))} if LISTE_AIRFOX.exists() else set()
a_actualiser = sorted({(d, j) for d, j in zip(dates["destination"], dates["date"])
                       if villes or not airfox or d in airfox},
                      key=lambda x: (x[1], x[0]))  # date par date : les premiers jours sont prêts d'abord
# Déjà recherchées pour ce nombre de nuits (avec ou sans vol) : « Compléter » ne fait que les manquantes. Une recherche
# « Paris » couvre les 4 aéroports parisiens : elle compte pour chacun. Heure de la recherche par destination et date.
heures_recherche = (catalogue.vols_jour_tableau(sorted(set(departs) | set(catalogue.PARIS_AEROPORTS)), nuits_ar)
                    .groupby(["destination", "date"])["collecte_le"].max())
deja = set(heures_recherche.index)
manquantes = [p for p in a_actualiser if p not in deja]


def libelle_periode() -> str:
    jours_paires = sorted({j for _, j in a_actualiser})
    if not jours_paires:
        return f"aller-retour {nuits_ar} nuits"
    return ((f"Du {pd.Timestamp(jours_paires[0]):%d/%m/%Y} au {pd.Timestamp(jours_paires[-1]):%d/%m/%Y}"
             if len(jours_paires) > 1 else f"Le {pd.Timestamp(jours_paires[0]):%d/%m/%Y}")
            + f", aller-retour {nuits_ar} nuits")


def lancer(paires):
    catalogue.lancer_actualisation(paires, departs, nuits_ar, libelle_periode())
    st.session_state["suivi_actif"] = True
    st.rerun()


aide = (f"Interroge Google Flights ({catalogue.texte_rythme()}) pour chaque destination "
        + ("choisie" if villes else "de la donnée") + " et chaque date choisie : prix, compagnie la moins chère, "
        "heures de départ et d'arrivée, durée et bagage. Les recherches tournent en arrière-plan : vous pouvez quitter "
        "la page ou fermer le navigateur, mais l'ordinateur doit rester allumé. Si Google bloque, elles reprennent "
        f"seules au bout de {catalogue.PAUSE_BLOCAGE_MIN} min, à un rythme plus lent (le rythme accélère ensuite "
        "doucement tant que Google ne bloque pas).")
actif = catalogue.actualisation_active()
b1, b2, b3 = st.columns(3)
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
# Toute l'année d'un coup (sans date choisie) : calendrier aller-retour « Paris » de chaque destination, puis celui
# de ses compagnies les moins chères → prix, compagnie et bagage de chaque date (sans les heures, en ~40 min)
dests_annee = sorted({d for d, _ in a_actualiser})
if (not jours_depart and dests_annee and set(departs) <= set(catalogue.PARIS_AEROPORTS)
        and b3.button(f"📅 Prix de l'année seulement ({len(dests_annee)} destinations, "
                      f"~{catalogue.duree_annee(len(dests_annee))} min)", disabled=actif, width="stretch",
                      help="Plus rapide, sans les heures : pour chaque destination, le tarif aller-retour le plus bas "
                           "de chaque date jusqu'à la fin du calendrier Google, la compagnie qui le propose et son "
                           "bagage en soute. Ne touchez pas à la page pendant la recherche.")):
    with st.status("Calendriers aller-retour de l'année sur Google Flights…", expanded=True) as statut:
        barre = st.progress(0.0)
        try:
            n = asyncio.run(catalogue.actualiser_annee(
                dests_annee, nuits_ar,
                on_progress=lambda i, total, msg: barre.progress(i / total, text=f"{i}/{total} : {msg}")))
            statut.update(label=f"Année actualisée : {n}/{len(dests_annee)} destinations avec prix", state="complete")
        except catalogue.Bloque:
            statut.update(label="Google bloque temporairement les recherches (captcha). Les destinations déjà faites "
                                "sont gardées ; réessayez plus tard.", state="error")
            st.stop()
    st.rerun()
if actif:
    st.caption("⏳ Une actualisation tourne en arrière-plan : son avancement s'affiche en haut de la page.")

# --- Résultats : recherches d'une date (avec heures) et calendriers de l'année ------------------------------------
if cies:
    vj = catalogue.vols_jour_tableau(departs, nuits_ar, annee=True)  # recherches d'une date + toute l'année
    dates = dates.merge(vj[vj["origine"] != "-"].sort_values("prix")
                        .drop_duplicates(["origine", "destination", "date", "compagnie"])
                        [["origine", "destination", "date", "compagnie", "prix", "bagage"]]
                        .rename(columns={"compagnie": "code_compagnie", "prix": "prix_compagnie"}),
                        how="left", on=["origine", "destination", "date", "code_compagnie"])
    # date recherchée : une compagnie absente des résultats n'a pas de vol ce jour-là
    dates["collecte_compagnie"] = [(d, j) in deja for d, j in zip(dates["destination"], dates["date"])]
    if avec_vol:
        dates = dates[dates["prix_compagnie"].notna() | ~dates["collecte_compagnie"]]
du_jour = catalogue.moins_cher_du_jour(departs, nuits_ar).rename(
    columns={"heure_depart": "heure_depart_mc", "heure_arrivee": "heure_arrivee_mc", "duree": "duree_mc"})
dates = (dates.merge(du_jour, how="left", on=["origine", "destination", "date"])
         .sort_values(["date", "ville_arrivee", "origine"] + (["code_compagnie"] if cies else []), ignore_index=True))
recherche = dates["prix_moins_cher"].notna()
# Date recherchée mais aucun vol depuis cet aéroport : « aucun vol trouvé » (et non « à compléter »)
sans_resultat = pd.Series([(d, j) in deja and not ok for d, j, ok in zip(dates["destination"], dates["date"],
                                                                        recherche)], index=dates.index)
bagage_mc = pd.to_numeric(dates["bagage_moins_cher"], errors="coerce")
tableau = pd.DataFrame({
    "Départ": [f"{nom_aeroport(o, origines[o])} ({o})" for o in dates["origine"]],
    "Ville d'arrivée": dates["ville_arrivee"], "Code": dates["destination"],
    "Pays": dates["destination"].map(pays_aeroport),
    "Date": pd.to_datetime(dates["date"]).dt.strftime("%d/%m/%Y"),
    "Date de retour": pd.to_datetime(dates["date_retour"]).dt.strftime("%d/%m/%Y"),
    "Prix le plus bas (toutes compagnies)": [f"{v:.0f} €" if pd.notna(v) else (AUCUN_VOL if s else A_COMPLETER)
                                             for v, s in zip(dates["prix_moins_cher"], sans_resultat)],
    "Compagnie la moins chère": [f"{n} ({c})" if isinstance(n, str) else (AUCUN_VOL if s else A_COMPLETER)
                                 for n, c, s in zip(dates["nom_moins_cher"], dates["code_moins_cher"],
                                                    sans_resultat)],
    # horaires du vol le moins cher : seulement les recherches d'une date (pas le calendrier de l'année)
    "Heure de départ": dates["heure_depart_mc"].fillna(A_COMPLETER).mask(sans_resultat, "—"),
    "Heure d'arrivée": dates["heure_arrivee_mc"].fillna(A_COMPLETER).mask(sans_resultat, "—"),
    "Durée": dates["duree_mc"].fillna(A_COMPLETER).mask(sans_resultat, "—"),
    "Bagage": bagage_mc.map({1: "True", 0: "False"}).fillna(A_COMPLETER).mask(sans_resultat, AUCUN_VOL),
    "Collecté le": pd.to_datetime(dates["collecte_moins_cher"].where(~sans_resultat, [
        heures_recherche.get((d, j)) for d, j in zip(dates["destination"], dates["date"])]))
        .dt.strftime("%d/%m/%Y %H:%M").fillna("—"),
    # d'où vient le prix de la ligne : recherche d'une date (avec horaires) ou calendrier de l'année
    "Origine du prix": pd.Series(np.select(
        [sans_resultat, recherche & dates["heure_depart_mc"].notna(), recherche],
        ["🟢 Recherche du jour (aucun vol)", "🟢 Recherche du jour", "🔵 Calendrier de l'année"], "—"),
        index=dates.index),
})
if cies:  # colonnes de la compagnie de la ligne : son prix et son bagage ce jour-là
    prix_comp = pd.to_numeric(dates["prix_compagnie"], errors="coerce")
    sans_vol = prix_comp.isna() & dates["collecte_compagnie"]
    tableau.insert(tableau.columns.get_loc("Bagage"), "Bagage (moins chère)", tableau.pop("Bagage"))
    tableau.insert(tableau.columns.get_loc("Collecté le"), "Code compagnie", dates["code_compagnie"])
    tableau.insert(tableau.columns.get_loc("Collecté le"),
                   f"Prix {noms_cies.get(cies[0], cies[0])}" if len(cies) == 1 else "Prix de la compagnie",
                   prix_comp.map(lambda v: A_COMPLETER if pd.isna(v) else f"{v:.0f} €").mask(sans_vol, PAS_DE_VOL))
    tableau.insert(tableau.columns.get_loc("Collecté le"), "Bagage",
                   pd.to_numeric(dates["bagage"], errors="coerce").map({1: "True", 0: "False"})
                   .fillna(A_COMPLETER).mask(sans_vol, PAS_DE_VOL))
st.caption(f"{len(tableau):,} lignes".replace(",", " ")
           + f" ({len(departs)} aéroport(s) de départ × {dates['destination'].nunique()} destinations × "
           f"{len(jours)} date(s)" + (f" × {len(cies)} compagnie(s)" if cies else "") + "). "
           f"« {A_COMPLETER} » : date pas encore recherchée ; « {AUCUN_VOL} » : recherchée, sans vol depuis cet "
           "aéroport ; « Origine du prix » : 🟢 recherche d'une date (avec heures), 🔵 calendrier de l'année.")
# lazy=False : au-delà de 150 000 lignes Streamlit charge le tableau par morceaux, liés à la session du navigateur ;
# après une reconnexion, ces morceaux échouent (« Failed to load data »). On envoie tout d'un coup.
st.dataframe(tableau, hide_index=True, width="stretch", lazy=False)
