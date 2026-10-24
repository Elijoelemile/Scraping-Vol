"""Page « Data Visualisation » : graphiques des deux jointures de la page Comparatif.

1A — jointure sur la compagnie : haltères tarif Airfox / tarif Google de la même compagnie à chaque date
     (trait rouge : Airfox plus cher, vert : moins cher ; symbole plein : bagage inclus), une destination à la fois.
2A — jointure sur la compagnie la moins chère : tarif Airfox et tarif le plus bas chez Google, date par date.
Les jointures (fichiers complets, sans filtres) sont calculées par la page Comparatif et gardées en session.
"""
import altair as alt
import pandas as pd
import streamlit as st

import actions
import catalogue

from titres import couleurs_graphiques, enregistrer_couleurs, intitule, titre_page

ECART = "Écart (Airfox − Google)"
ENCRE, ENCRE_DOUCE = "#0b0b0b", "#52514e"

titre_page("Visualisation Graphiques", "dataviz")

jointure = st.session_state.get("rapport_jointure")
jointure_mc = st.session_state.get("rapport_jointure_moins_chere")
if jointure is None or jointure_mc is None:
    st.info("Ouvrez d'abord la page « Comparatif Airfox vs Google Flights » : les graphiques utilisent ses jointures.")
    st.stop()
# type de trajet réellement utilisé par les jointures du Comparatif (aller-retour de N nuits, ou aller simple)
nuits = st.session_state.get("nuits_comparatif", st.session_state.get("nuits_catalogue"))
st.caption(f"Données de la page Comparatif ({'aller-retour de ' + str(nuits) + ' nuits' if nuits else 'aller simple'}"
           ", fichiers complets). Plus il y a de dates actualisées dans le Catalogue, plus les graphiques sont fournis.")


def preparer(tab: pd.DataFrame, meme_bagage_seul: bool) -> pd.DataFrame:
    tab = tab.assign(Date=pd.to_datetime(tab["DateAller"], dayfirst=True, errors="coerce"),
                     **{"Tarif Airfox": pd.to_numeric(tab["Tarif Airfox"], errors="coerce"),
                        "Tarif Google": pd.to_numeric(tab["Tarif Google"], errors="coerce"),
                        ECART: pd.to_numeric(tab[ECART], errors="coerce")})
    if meme_bagage_seul:
        tab = tab[tab["Même bagage"] == "TRUE"]
    return tab.dropna(subset=[ECART])


def nom_ville(code: str) -> str:
    """Nom de ville d'un code Airfox (PAR -> Paris, LON -> Londres, ACE -> Lanzarote…)."""
    return {"PAR": "Paris", "LON": "Londres", "MIL": "Milan", "NYC": "New York", "RIO": "Rio de Janeiro",
            "ROM": "Rome", "TYO": "Tokyo", "YMQ": "Montréal", "YTO": "Toronto", "IZM": "Izmir",
            "DKR": "Dakar"}.get(code) or catalogue.ville_fr(code)


def chiffres_cles(tab: pd.DataFrame):
    c1, c2, c3 = st.columns(3)
    c1.metric("Comparaisons", f"{len(tab):,}".replace(",", " "))
    c2.metric("Écart médian", f"{tab[ECART].median():+.0f} €" if len(tab) else "—")
    c3.metric("Google moins cher", f"{(tab[ECART] > 0).mean():.0%}" if len(tab) else "—")


# Couleurs des graphiques : la légende au-dessus de chaque graphique est cliquable (une pastille = un sélecteur de
# couleur) ; le choix est gardé dans couleurs_graphiques.json et repris par le rapport Excel
COUL = couleurs_graphiques()
# pastille de chaque couleur, à la forme de son symbole dans le graphique
FORMES = {"Airfox": ("airfox", "rond"), "Google": ("google", "losange"), "Airfox plus cher": ("pluscher", "trait"),
          "Airfox moins cher": ("moinscher", "trait"), "Même prix": ("memeprix", "trait")}
st.html("""<style>
[class*="st-key-legende_"]:not([class*="__"]) { margin-bottom: 0.9rem; padding-right: 7rem; }
[class*="st-key-legende_"] [data-testid="stElementContainer"] { width: fit-content !important;
    min-width: 0 !important; max-width: fit-content !important; flex: 0 0 auto !important; }
[class*="st-key-legende_"] [data-testid="stColorPicker"] { width: fit-content !important; min-width: 0; gap: 0; }
[class*="st-key-legende_"] [data-testid="stColorPicker"] > label { display: none; }
[class*="st-key-legende_"] [data-testid="stColorPicker"] { position: relative; top: 0.45rem; }
[class*="st-key-legende_"] [data-testid="stColorPicker"] button { padding: 0; min-height: 0; border: 0;
    background: none; display: flex; align-items: center; justify-content: center; width: 1.1rem; height: 1.1rem; }
[class*="st-key-legende_"] [data-testid="stColorPickerBlock"] { width: 0.85rem; height: 0.85rem; border-radius: 3px;
    cursor: pointer; box-shadow: 0 0 0 1px rgba(0,0,0,.08); }
[class*="-rond"] [data-testid="stColorPickerBlock"] { border-radius: 50%; }
[class*="-losange"] [data-testid="stColorPickerBlock"] { transform: rotate(45deg) scale(.82); border-radius: 1px; }
[class*="-trait"] [data-testid="stColorPickerBlock"] { width: 1rem; height: 0.3rem; border-radius: 1px; }
[class*="st-key-legende_"] [data-testid="stColorPicker"] button:hover [data-testid="stColorPickerBlock"] {
    outline: 2px solid #9a9993; outline-offset: 2px; }
[class*="st-key-legende_"] [data-testid="stMarkdownContainer"] p { font-size: 0.85rem; color: #52514e; margin: 0;
    white-space: nowrap; }
[class*="st-key-legende_"] [data-testid="stElementContainer"]:not([class*="__"]) {
    margin-right: 1.1rem; }
</style>""")


def changer_couleur(nom: str, cle: str):
    """Une pastille modifiée : enregistre la couleur et met à jour la même pastille des autres légendes."""
    COUL[nom] = st.session_state[cle]
    enregistrer_couleurs(COUL)
    fin_cle = f"__{FORMES[nom][0]}-{FORMES[nom][1]}"
    for autre in [k for k in st.session_state if str(k).startswith("legende_") and str(k).endswith(fin_cle)]:
        st.session_state[autre] = COUL[nom]


def legende(cle: str, elements: list[tuple[str, str]]):
    """Légende cliquable, à la place de celle du graphique : pour chaque élément, une pastille à la forme et à la
    couleur de son symbole (cliquer dessus ouvre le choix de la couleur), suivie de son libellé."""
    with st.container(key=f"legende_{cle}", horizontal=True, gap="small", vertical_alignment="center"):
        for nom, libelle in elements:
            cle_pastille = f"legende_{cle}__{FORMES[nom][0]}-{FORMES[nom][1]}"
            st.color_picker(libelle, COUL[nom], key=cle_pastille, label_visibility="collapsed",
                            on_change=changer_couleur, args=(nom, cle_pastille))
            st.markdown(libelle)


BLEU, NOIR = COUL["Airfox"], COUL["Google"]  # séries Airfox / Google

bagage_seul = st.toggle("Même bagage seulement", help="Ne garder que les comparaisons où Airfox et Google ont le même "
                                                      "bagage (sinon on compare des offres différentes)")

# --- 1A : haltères — tarif Airfox et Google de la même compagnie à chaque date, et leur bagage -------------------
st.subheader("1A — Airfox face à Google, même compagnie, date par date (jointure sur la compagnie)")
j1 = preparer(jointure, bagage_seul)
if j1.empty:
    chiffres_cles(j1)
    st.info("Aucune comparaison pour l'instant.")
else:
    # bagage de chaque côté : TRUE / FALSE (vide ou autre : inconnu)
    for cote in ("Airfox", "Google"):
        j1[f"Bagage {cote}"] = j1[f"FlagBagageInclus {cote}"].astype(str).str.upper().where(
            lambda v: v.isin(["TRUE", "FALSE"]), "inconnu")
    # une destination à la fois : une date regroupe sinon des destinations aux prix sans rapport
    # Ville (codes Airfox : PAR, LON…) et aéroport (celui du tarif Google : CDG, ORY…) de départ et d'arrivée
    f0, f1, f3, f4 = st.columns(4)
    departs_1a = j1["VilleDepart"].astype(str).value_counts()
    depart_1a = f0.selectbox("Ville de départ", departs_1a.index, key="depart_1a",
                             format_func=lambda v: f"{nom_ville(v)} ({v})")
    j1 = j1[j1["VilleDepart"].astype(str) == depart_1a]
    aeroport_dep_1a = f1.selectbox("Aéroport de départ", ["Tous"] + sorted(j1["AeroportDepart"].dropna().unique()),
                                   key="aeroport_depart_1a")
    if aeroport_dep_1a != "Tous":
        j1 = j1[j1["AeroportDepart"] == aeroport_dep_1a]
    villes_1a = j1["VilleArrivee"].value_counts()
    if villes_1a.empty:
        st.info("Aucune comparaison au départ de cet aéroport.")
        st.stop()
    ville_1a = f3.selectbox("Destination", villes_1a.index, key="destination_1a",
                            format_func=lambda v: f"{nom_ville(v)} ({v}) — {villes_1a[v]} comparaison(s)")
    j1 = j1[j1["VilleArrivee"] == ville_1a]
    aeroport_arr_1a = f4.selectbox("Aéroport d'arrivée", ["Tous"] + sorted(j1["AeroportArrivee"].dropna().unique()),
                                   key="aeroport_arrivee_1a")
    if aeroport_arr_1a != "Tous":
        j1 = j1[j1["AeroportArrivee"] == aeroport_arr_1a]
    # Compagnie (par défaut : celle qui a le plus de comparaisons), combinaison de bagages et période
    g1, g2, g3 = st.columns([2, 2, 3])
    cies_1a = j1["CodeTransporteur"].astype(str).value_counts()
    cie_1a = g1.selectbox("Compagnie", ["Toutes"] + list(cies_1a.index), index=1 if len(cies_1a) else 0,
                          key="compagnie_1a",
                          format_func=lambda c: c if c == "Toutes" else f"{c} — {cies_1a[c]} comparaison(s)",
                          help="« Toutes » : une haltère par compagnie à chaque date, côte à côte")
    if cie_1a != "Toutes":
        j1 = j1[j1["CodeTransporteur"].astype(str) == cie_1a]
    SITUATIONS = {("TRUE", "TRUE"): "Bagage des deux côtés", ("FALSE", "FALSE"): "Sans bagage des deux côtés",
                  ("TRUE", "FALSE"): "Bagage chez Airfox seulement", ("FALSE", "TRUE"): "Bagage chez Google seulement"}
    COMBINAISONS = {"Toutes": lambda a, g: a == a,
                    "Même bagage": lambda a, g: (a == g) & (a != "inconnu"),
                    "Bagage des deux côtés": lambda a, g: (a == "TRUE") & (g == "TRUE"),
                    "Bagage chez Airfox seulement": lambda a, g: (a == "TRUE") & (g == "FALSE"),
                    "Bagage chez Google seulement": lambda a, g: (a == "FALSE") & (g == "TRUE"),
                    "Sans bagage des deux côtés": lambda a, g: (a == "FALSE") & (g == "FALSE")}
    combinaison = g2.selectbox("Bagages", list(COMBINAISONS), key="bagages_1a")
    j1 = j1[COMBINAISONS[combinaison](j1["Bagage Airfox"], j1["Bagage Google"])]
    d1 = j1.sort_values("Date")
    if d1.empty:
        st.info("Aucune comparaison pour ces filtres.")
        st.stop()
    dates_1a = sorted(d1["Date"].dt.normalize().unique())
    debut_min, fin_max = pd.Timestamp(dates_1a[0]).date(), pd.Timestamp(dates_1a[-1]).date()
    defaut_fin = pd.Timestamp(dates_1a[min(len(dates_1a), 30) - 1]).date()  # par défaut : les 30 premières dates
    periode = g3.date_input("Période", value=(debut_min, defaut_fin), min_value=debut_min, max_value=fin_max,
                            format="DD/MM/YYYY", key="periode_1a")
    debut_1a, fin_1a = (periode[0], periode[1]) if len(periode) == 2 else (debut_min, defaut_fin)
    d1 = d1[(d1["Date"].dt.date >= debut_1a) & (d1["Date"].dt.date <= fin_1a)]
    # repris par le rapport Excel (même destination, même période)
    st.session_state["rapport_1a"] = {"depart": depart_1a, "ville": ville_1a, "debut": debut_1a, "fin": fin_1a,
                                      "aeroport_depart": aeroport_dep_1a, "aeroport_arrivee": aeroport_arr_1a}

    # Pour fixer les tarifs : par situation de bagage, combien de dates où Airfox est au-dessus de Google et de
    # combien il faut baisser (baisse nécessaire = Airfox − Google quand Airfox est plus cher, sinon 0)
    d1 = d1.assign(**{"Baisse nécessaire": d1[ECART].clip(lower=0),
                      "Augmentation nécessaire": (-d1[ECART]).clip(lower=0), "Tarif cible": d1["Tarif Google"],
                      "Situation bagage": [SITUATIONS.get((a, g), "Bagage inconnu")
                                           for a, g in zip(d1["Bagage Airfox"], d1["Bagage Google"])]})
    a_baisser = d1[d1[ECART] > 0]
    intitule("Que faut-il baisser ? (selon le bagage)")
    bilan = []
    for situation in list(SITUATIONS.values()) + ["Bagage inconnu"]:
        s_tout = d1[d1["Situation bagage"] == situation]
        s_baisse = a_baisser[a_baisser["Situation bagage"] == situation]
        if s_tout.empty:
            continue
        bilan.append({"Situation bagage": situation, "Dates comparées": len(s_tout),
                      "Airfox au-dessus de Google": f"{len(s_baisse)} ({len(s_baisse) / len(s_tout):.0%})",
                      "Baisse la plus faible": s_baisse["Baisse nécessaire"].min() if len(s_baisse) else None,
                      "Baisse la plus forte": s_baisse["Baisse nécessaire"].max() if len(s_baisse) else None})
    st.dataframe(pd.DataFrame(bilan), hide_index=True, width="stretch",
                 column_config={"Baisse la plus faible": st.column_config.NumberColumn(format="%.2f €"),
                                "Baisse la plus forte": st.column_config.NumberColumn(format="%.2f €")})
    st.caption("« Baisse » = de combien Airfox doit baisser pour revenir au tarif Google de la même compagnie, le "
               "même jour. Quand Airfox inclut le bagage et pas Google, être plus cher peut se justifier : comparez "
               "la baisse au prix du bagage chez la compagnie.")

    jours = pd.date_range(debut_1a, fin_1a, freq="D")  # tous les jours entre les deux dates, même sans comparaison
    ordre_jours = [j.strftime("%d/%m") for j in jours]
    d1 = d1.assign(Jour=d1["Date"].dt.strftime("%d/%m"),
                   Sens=d1[ECART].map(lambda e: "Airfox plus cher" if e > 0 else
                                      "Airfox moins cher" if e < 0 else "Même prix"))
    ROUGE, VERT, GRIS = COUL["Airfox plus cher"], COUL["Airfox moins cher"], COUL["Même prix"]
    axe_x = alt.X("Jour:N", sort=ordre_jours, scale=alt.Scale(domain=ordre_jours), title="Date de départ",
                  axis=alt.Axis(labelColor=ENCRE, titleColor=ENCRE_DOUCE, ticks=False,
                                labelAngle=0 if len(jours) <= 15 else -45))
    axe_y = alt.Y("Tarif:Q", title="Tarif (€)", scale=alt.Scale(zero=False),
                  axis=alt.Axis(labelColor=ENCRE_DOUCE, titleColor=ENCRE_DOUCE, gridColor="#ececea",
                                labelExpr="format(datum.value, ',.0f') + ' €'"))
    decalage = alt.XOffset("CodeTransporteur:N")  # plusieurs compagnies le même jour : côte à côte
    infos = [alt.Tooltip("Date:T", title="Départ", format="%d/%m/%Y"),
             alt.Tooltip("CodeTransporteur:N", title="Compagnie"),
             alt.Tooltip("Tarif Airfox:Q", title="Tarif Airfox (€)", format=".2f"),
             alt.Tooltip("Tarif Google:Q", title="Tarif Google (€)", format=".2f"),
             alt.Tooltip(f"{ECART}:Q", title="Écart Airfox − Google (€)", format="+.2f"),
             alt.Tooltip("Bagage Airfox:N"), alt.Tooltip("Bagage Google:N"),
             alt.Tooltip("Baisse nécessaire:Q", title="Baisse nécessaire (€)", format=".2f"),
             alt.Tooltip("Augmentation nécessaire:Q", title="Augmentation nécessaire (€)", format=".2f"),
             alt.Tooltip("Tarif cible:Q", title="Tarif cible (€)", format=".2f")]
    # le trait de l'haltère : de Google à Airfox, rouge si Airfox est plus cher, vert s'il est moins cher
    traits = alt.Chart(d1.assign(Tarif=d1["Tarif Airfox"])).mark_rule(strokeWidth=3).encode(
        x=axe_x, xOffset=decalage, y=axe_y, y2="Tarif Google:Q",
        color=alt.Color("Sens:N", scale=alt.Scale(domain=["Airfox plus cher", "Airfox moins cher", "Même prix"],
                                                  range=[ROUGE, VERT, GRIS]),
                        legend=None),
        tooltip=infos)
    # les deux bouts : ● Airfox, ◆ Google ; plein = bagage inclus, creux = sans bagage
    bouts = pd.concat([
        d1.assign(Source="Airfox", Tarif=d1["Tarif Airfox"], Bagage=d1["Bagage Airfox"]),
        d1.assign(Source="Google", Tarif=d1["Tarif Google"], Bagage=d1["Bagage Google"]),
    ])
    sources = alt.Scale(domain=["Airfox", "Google"], range=[BLEU, NOIR])
    points = alt.Chart(bouts).mark_point(size=120, strokeWidth=2.5, opacity=1).encode(
        x=axe_x, xOffset=decalage, y=axe_y,
        shape=alt.Shape("Source:N", scale=alt.Scale(domain=["Airfox", "Google"], range=["circle", "diamond"]),
                        legend=None),
        stroke=alt.Stroke("Source:N", scale=sources, legend=None),
        fill=alt.condition("datum.Bagage == 'TRUE'", alt.Fill("Source:N", scale=sources, legend=None),
                           alt.value("#ffffff")),
        tooltip=infos)
    graphique = traits + points
    legende("1a", [("Airfox", "Airfox"), ("Google", "Google"), ("Airfox plus cher", "Airfox plus cher"),
                   ("Airfox moins cher", "Airfox moins cher"), ("Même prix", "Même prix")])
    st.altair_chart(graphique.properties(height=400).resolve_scale(color="independent"), width="stretch")
    trajet = (f"{nom_ville(depart_1a)}{'' if aeroport_dep_1a == 'Tous' else ' (' + aeroport_dep_1a + ')'} → "
              f"{nom_ville(ville_1a)}{'' if aeroport_arr_1a == 'Tous' else ' (' + aeroport_arr_1a + ')'}")
    st.caption(f"{trajet}{'' if cie_1a == 'Toutes' else ' · ' + cie_1a} : pour chaque date, ● tarif Airfox et "
               "◆ tarif Google de la même compagnie, reliés par un trait **rouge** si Airfox est plus cher, **vert** "
               "s'il est moins cher. Symbole **plein** = bagage inclus, **creux** = sans bagage. Survolez pour le "
               f"détail. {d1['Date'].dt.normalize().nunique()} date(s) comparée(s) sur {len(jours)} jour(s) : une date "
               "vide n'a pas encore été comparée (actualisez-la dans le Catalogue).")
    # La liste d'actions, modifiable : toutes les dates comparées, avec la baisse (Airfox plus cher) ou l'augmentation
    # (Airfox moins cher) pour atteindre le tarif cible ; tarifs corrigeables (recalcul), lignes supprimables ; elle
    # repart des données quand un filtre change. Téléchargeable en CSV.
    signature = (depart_1a, aeroport_dep_1a, ville_1a, aeroport_arr_1a, cie_1a, combinaison, debut_1a, fin_1a, nuits)
    if (st.session_state.get("baisse_signature") != signature or "baisse_table" not in st.session_state
            or "Augmentation nécessaire" not in st.session_state["baisse_table"]):
        st.session_state["baisse_table"] = d1.sort_values("Date").assign(
            date_vol=lambda t: t["Date"].dt.strftime("%Y-%m-%d"),
            AeroportDepart=d1.get("AeroportDepart", pd.Series("", index=d1.index)).fillna(""),
            AeroportArrivee=d1.get("AeroportArrivee", pd.Series("", index=d1.index)).fillna(""),
        )[["DateAller", "CodeTransporteur", "Situation bagage", "Tarif Airfox", "Tarif Google", "Baisse nécessaire",
           "Augmentation nécessaire", "Tarif cible", "date_vol", "VilleDepart", "AeroportDepart", "VilleArrivee", "AeroportArrivee",
           "Bagage Airfox", "Bagage Google"]].rename(columns={"DateAller": "Date", "CodeTransporteur": "Compagnie"}
                                                     ).reset_index(drop=True)
        st.session_state["baisse_signature"] = signature
        st.session_state["baisse_version"] = st.session_state.get("baisse_version", 0) + 1
    table = st.session_state["baisse_table"]

    def avec_modifications(t: pd.DataFrame, etat: dict) -> pd.DataFrame:
        """Le tableau avec les saisies du tableau modifiable (cellules et lignes supprimées), baisse et augmentation
        recalculées."""
        t = t.copy()
        for ligne, changements in etat.get("edited_rows", {}).items():
            for colonne, valeur in changements.items():
                t.loc[t.index[int(ligne)], colonne] = valeur
        t = t.drop(index=[t.index[int(i)] for i in etat.get("deleted_rows", []) if int(i) < len(t)])
        t["Tarif cible"] = pd.to_numeric(t["Tarif cible"], errors="coerce").fillna(
            pd.to_numeric(t["Tarif Google"], errors="coerce"))
        ecart_cible = pd.to_numeric(t["Tarif Airfox"], errors="coerce") - t["Tarif cible"]
        t["Baisse nécessaire"] = ecart_cible.clip(lower=0).round(2)
        t["Augmentation nécessaire"] = (-ecart_cible).clip(lower=0).round(2)
        return t

    # Le tableau ne reçoit de nouvelles données qu'au clic sur « Recalculer » : tant qu'on saisit, il ne bouge pas
    # (Streamlit le ramènerait sinon en haut à gauche à chaque chiffre). L'enregistrement prend les saisies en compte.
    cle_tableau = f"tableau_baisse_{st.session_state['baisse_version']}"
    etat_tableau = st.session_state.get(cle_tableau, {})
    gardees = avec_modifications(table, etat_tableau)
    modifie = bool(etat_tableau.get("edited_rows") or etat_tableau.get("deleted_rows"))
    intitule(f"Augmentation et baisse de tarifs ({len(gardees)})")
    if table.empty:
        st.info("Aucune comparaison pour ces filtres.")
    else:
        st.data_editor(
            table, hide_index=True, width="stretch", key=cle_tableau, num_rows="delete",
            disabled=["Date", "Compagnie", "Situation bagage", "Baisse nécessaire", "Augmentation nécessaire"],
            column_config={
                **{c: st.column_config.NumberColumn(format="%.2f €", min_value=0, required=True)
                   for c in ("Tarif Airfox", "Tarif Google")},
                "Baisse nécessaire": st.column_config.NumberColumn(
                    format="%.2f €", help="Airfox plus cher : de combien baisser pour atteindre le tarif cible"),
                "Augmentation nécessaire": st.column_config.NumberColumn(
                    format="%.2f €", help="Airfox moins cher : de combien augmenter pour atteindre le tarif cible"),
                "Tarif cible": st.column_config.NumberColumn(format="%.2f €", min_value=0, required=True,
                                                             help="Le tarif à viser : par défaut le tarif Google"),
                **{c: None for c in ("date_vol", "VilleDepart", "AeroportDepart", "VilleArrivee", "AeroportArrivee",
                                     "Bagage Airfox", "Bagage Google")},
            })
        b1, b2, _ = st.columns([1.3, 1.3, 3])
        if b1.button("↻ Recalculer", type="primary" if modifie else "secondary", width="stretch", disabled=not modifie,
                     help="Met à jour « Baisse nécessaire » et « Augmentation nécessaire » avec vos saisies"):
            st.session_state["baisse_table"] = avec_modifications(table, etat_tableau).reset_index(drop=True)
            st.session_state["baisse_version"] += 1
            st.rerun()
        if b2.button("↺ Valeurs d'origine", width="stretch", help="Annule vos modifications du tableau"):
            st.session_state.pop("baisse_signature", None)
            st.rerun()
        st.caption("Baisse nécessaire : Airfox est plus cher que le tarif cible ; augmentation nécessaire : il est moins "
                   "cher, il peut monter jusqu'au tarif cible. Double-cliquez sur un tarif pour le modifier : le "
                   "tableau reste en place pendant vos saisies, puis « Recalculer » met à jour baisse et augmentation "
                   "(l'enregistrement en tient compte de toute façon). Pour retirer une ligne, cochez-la à gauche puis "
                   "appuyez sur Suppr (ou l'icône corbeille). Tarif cible par défaut = tarif Google de la même "
                   "compagnie ce jour-là. Le tableau se télécharge en CSV (icône en haut à droite).")

        # Contexte de la décision : saison, vacances, événements… pour que l'agent IA comprenne ces choix plus tard
        st.html("""<style>
.st-key-contexte_actions [data-testid="stWidgetLabel"] p { font-weight: 700 !important; color: #000 !important; }
.st-key-contexte_actions [data-testid="stTextAreaRootElement"] { border: 2px solid #000 !important; border-radius: .5rem; }
</style>""")
        contexte = st.text_area(
            "Description du contexte", key="contexte_actions", height=90,
            placeholder="Ex. vacances de la Toussaint, haute saison au Portugal ; Ryanair a lancé des promotions ; "
                        "nous voulons rester sous Google sans descendre sous 100 €.",
            help="La saison et tout ce qui explique ces décisions (vacances scolaires, événements, concurrence, "
                 "objectifs). Il est enregistré avec les actions : l'agent IA s'en servira pour comprendre dans quel "
                 "contexte elles ont été prises.")
        # Historique pour l'agent IA : la liste du jour est gardée dans la base (table actions_tarifs)
        if st.button(f"💾 Enregistrer ces {len(gardees)} actions", key="enregistrer_actions",
                     help="Garde ces actions (avec vos modifications) et le contexte dans la base, avec la date "
                          "d'aujourd'hui, pour y noter ensuite votre décision (ci-dessous). Les réenregistrer le même "
                          "jour les met à jour sans effacer les décisions. L'agent IA de la page Assistant "
                          "Décisionnel s'appuiera sur cet historique."):
            if not contexte.strip():
                st.warning("Décrivez d'abord le contexte (saison, événements…) : sans lui, l'IA ne saura pas "
                           "pourquoi ces décisions ont été prises.")
            else:
                n = actions.enregistrer(pd.DataFrame({
                    "ville_depart": gardees["VilleDepart"].astype(str), "aeroport_depart": gardees["AeroportDepart"],
                    "ville_arrivee": gardees["VilleArrivee"].astype(str), "aeroport_arrivee": gardees["AeroportArrivee"],
                    "date_vol": gardees["date_vol"], "nuits": int(nuits or 0), "compagnie": gardees["Compagnie"].astype(str),
                    "bagage_airfox": gardees["Bagage Airfox"], "bagage_google": gardees["Bagage Google"],
                    "situation": gardees["Situation bagage"], "tarif_airfox": gardees["Tarif Airfox"],
                    "tarif_google": gardees["Tarif Google"], "baisse": gardees["Baisse nécessaire"],
                    "augmentation": gardees["Augmentation nécessaire"],
                    "tarif_cible": gardees["Tarif cible"]}), contexte)
                st.toast(f"{n} actions enregistrées avec leur contexte")

    # Décisions sur les actions enregistrées pour cette destination : baissé, gardé, autre + tarif retenu
    enregistrees = actions.lire(ville_1a)
    with st.expander(f"📝 Mes décisions — actions enregistrées pour {nom_ville(ville_1a)} ({len(enregistrees)})"):
        if enregistrees.empty:
            st.caption("Aucune action enregistrée pour cette destination : cliquez sur « Enregistrer ces actions ».")
        else:
            seulement = st.toggle("Seulement celles « à décider »", value=True, key="seulement_a_decider")
            vue = enregistrees[enregistrees["decision"] == "à décider"] if seulement else enregistrees
            vue = vue.assign(analyse_le=pd.to_datetime(vue["analyse_le"]).dt.date,
                             date_vol=pd.to_datetime(vue["date_vol"]).dt.date)
            colonnes = ["id", "analyse_le", "date_vol", "nuits", "compagnie", "situation", "tarif_airfox",
                        "tarif_google", "baisse", "augmentation", "tarif_cible", "decision", "tarif_retenu",
                        "commentaire", "contexte"]
            modifiees = st.data_editor(
                vue[colonnes], hide_index=True, width="stretch", key="editeur_decisions",
                disabled=[c for c in colonnes if c not in ("decision", "tarif_retenu", "commentaire")],
                column_config={
                    "id": None,
                    "analyse_le": st.column_config.DateColumn("Analysé le", format="DD/MM/YYYY"),
                    "date_vol": st.column_config.DateColumn("Date du vol", format="DD/MM/YYYY"),
                    "nuits": st.column_config.NumberColumn("Nuits"),
                    "compagnie": "Compagnie", "situation": "Situation bagage",
                    "tarif_airfox": st.column_config.NumberColumn("Tarif Airfox", format="%.2f €"),
                    "tarif_google": st.column_config.NumberColumn("Tarif Google", format="%.2f €"),
                    "baisse": st.column_config.NumberColumn("Baisse proposée", format="%.2f €"),
                    "augmentation": st.column_config.NumberColumn("Augmentation proposée", format="%.2f €"),
                    "tarif_cible": st.column_config.NumberColumn("Tarif cible", format="%.2f €"),
                    "decision": st.column_config.SelectboxColumn("Ma décision", options=actions.DECISIONS,
                                                                 required=True),
                    "tarif_retenu": st.column_config.NumberColumn("Tarif retenu", format="%.2f €", min_value=0),
                    "commentaire": st.column_config.TextColumn("Commentaire",
                                                               help="Pourquoi ce choix (ex. bagage inclus)"),
                    "contexte": st.column_config.TextColumn("Contexte", help="Décrit à l'enregistrement des actions"),
                })
            changees = modifiees[(modifiees[["decision", "tarif_retenu", "commentaire"]].fillna("")
                                  != vue[["decision", "tarif_retenu", "commentaire"]].fillna("")).any(axis=1)]
            if st.button(f"Enregistrer mes décisions ({len(changees)})", disabled=changees.empty,
                         key="enregistrer_decisions", type="primary"):
                actions.enregistrer_decisions(changees[["id", "decision", "tarif_retenu", "commentaire"]])
                st.toast(f"{len(changees)} décision(s) enregistrée(s)")
                st.rerun()

# --- 2A : tarif Airfox contre tarif le plus bas chez Google, date par date -----------------------------------------
st.subheader("2A — Airfox face au prix le plus bas de Google, date par date (jointure sur la moins chère)")
j2 = preparer(jointure_mc, bagage_seul)
chiffres_cles(j2)
if j2.empty:
    st.info("Aucune comparaison pour l'instant.")
else:
    villes = j2["VilleArrivee"].value_counts()
    intitule("Destination")
    ville = st.selectbox("Destination", villes.index, label_visibility="collapsed",
                         format_func=lambda v: f"{v} ({villes[v]} date(s))")
    d = j2[j2["VilleArrivee"] == ville].sort_values("Date")
    longue = pd.concat([
        d.assign(Série="Airfox", Tarif=d["Tarif Airfox"], Compagnie=d["CodeTransporteur"]),
        d.assign(Série="Google (moins chère)", Tarif=d["Tarif Google"], Compagnie=d["CompagnieMoinsChere"]),
    ])[["Date", "Série", "Tarif", "Compagnie", "MêmeCompagnie", ECART]]
    couleurs = alt.Scale(domain=["Airfox", "Google (moins chère)"], range=[BLEU, NOIR])
    survol = alt.selection_point(fields=["Date"], nearest=True, on="pointerover", empty=False)
    lignes = alt.Chart(longue).mark_line(strokeWidth=2).encode(
        x=alt.X("Date:T", title="Date de départ", axis=alt.Axis(format="%d/%m", labelColor=ENCRE_DOUCE,
                                                                  titleColor=ENCRE_DOUCE, grid=False)),
        y=alt.Y("Tarif:Q", title="Tarif (€)", axis=alt.Axis(labelColor=ENCRE_DOUCE, titleColor=ENCRE_DOUCE,
                                                             gridColor="#ececea")),
        color=alt.Color("Série:N", scale=couleurs, legend=None))
    points = alt.Chart(longue).mark_point(filled=True, size=70, stroke="#fcfcfb", strokeWidth=2).encode(
        x="Date:T", y="Tarif:Q", color=alt.Color("Série:N", scale=couleurs, legend=None),
        # même compagnie chez Airfox et moins chère chez Google : losange plus grand
        shape=alt.Shape("MêmeCompagnie:N", scale=alt.Scale(domain=["✔", ""], range=["diamond", "circle"]),
                        legend=alt.Legend(title=None, orient="top", labelExpr="datum.value == '✔' ? "
                                          "'Même compagnie' : 'Compagnie différente'")),
        tooltip=[alt.Tooltip("Date:T", title="Départ", format="%d/%m/%Y"), alt.Tooltip("Série:N"),
                 alt.Tooltip("Tarif:Q", title="Tarif (€)", format=".2f"), alt.Tooltip("Compagnie:N"),
                 alt.Tooltip(f"{ECART}:Q", title="Écart Airfox − Google (€)", format="+.2f")])
    repere = alt.Chart(longue).mark_rule(color=ENCRE_DOUCE, strokeDash=[3, 3]).encode(x="Date:T").transform_filter(
        survol)
    cible = alt.Chart(longue).mark_point(size=400, opacity=0).encode(x="Date:T", y="Tarif:Q").add_params(survol)
    legende("2a", [("Airfox", "Airfox"), ("Google", "Google (moins chère)")])
    st.altair_chart((lignes + points + repere + cible).properties(height=360), width="stretch")
    st.caption("Tarif Airfox et tarif de la compagnie la moins chère chez Google le même jour (couleurs de la "
               "légende). "
               "Losange : c'est la même compagnie des deux côtés. Survolez un point pour le détail.")
    with st.expander("Voir les données du graphique"):
        st.dataframe(d[["DateAller", "CodeTransporteur", "Tarif Airfox", "CompagnieMoinsChere", "Tarif Google",
                        "MêmeCompagnie", ECART]], hide_index=True, width="stretch")
