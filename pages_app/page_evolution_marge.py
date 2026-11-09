"""Page « Evolution Profils de Marge » : le fichier transformé (Tableau de PM + colonne Libelle) chargé dans
« Charger la donnée Profils de Marge », affiché et téléchargeable."""
from pathlib import Path

import streamlit as st

import profils_marge
from titres import intitule, titre_page

titre_page("Evolution Profils de Marge", "evolution_marge")

t = profils_marge.lire()
if t is None:
    st.info("Aucun fichier Profils de Marge : chargez-le dans la page « Charger la donnée Profils de Marge ».")
    st.stop()

garde, garde_lib = profils_marge.fichier_garde(), profils_marge.libelles_garde()
intitule("Fichier transformé")
st.caption(f"Fichier : **{garde.name}**" + (f" · libellés : **{garde_lib.name}**" if garde_lib else "")
           + f" · {len(t):,} lignes · {len(t.columns)} colonnes".replace(",", " "))
if garde_lib is None:
    st.warning("Pas de fichier des libellés : la colonne **Libelle** sera ajoutée quand il sera chargé dans la page "
               "« Charger la donnée Profils de Marge ».")
else:
    sans = t.loc[t["Libelle"] == "", "Profil"].astype(str).str.strip()
    if len(sans):
        st.caption(f"⚠️ {sans.nunique()} profil(s) sans libellé ({len(sans)} ligne(s)) : "
                   + ", ".join(sorted(sans.unique())[:20]) + (" …" if sans.nunique() > 20 else ""))

nom = f"{Path(garde.name).stem} - Tableau de PM" + (" avec libellés" if garde_lib else "")
d1, d2, _ = st.columns([1.4, 1.4, 3])
d1.download_button("⬇️ Télécharger (Excel)", profils_marge.en_excel(t), file_name=f"{nom}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
                   type="primary")
d2.download_button("⬇️ Télécharger (csv)", profils_marge.en_csv(t), file_name=f"{nom}.csv", mime="text/csv",
                   width="stretch")
st.dataframe(profils_marge.pour_affichage(t), hide_index=True, width="stretch", height=600)
