# Scraping Vol — comparateur de prix de vols

Application Streamlit qui collecte les prix de Google Flights et les compare.

- **Catalogue (1 an)** : prix le plus bas de chaque jour sur un an, par trajet (aller simple ou aller-retour).
- **Recherche détaillée** : tous les vols d'un ou plusieurs trajets à une date (compagnies, horaires, escales, prix),
  avec récapitulatif et historique des chargements.

Les données sont stockées localement dans `vols.db` (SQLite).

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## Lancement

```powershell
.\.venv\Scripts\streamlit.exe run app.py
```

Puis ouvrir http://localhost:8501.

## Collecte du catalogue

```powershell
# Ajouter tous les trajets entre deux listes d'aéroports (fichiers « CODE;Ville » dans listes/)
.\.venv\Scripts\python.exe catalogue.py croiser listes/paris.txt listes/maroc.txt --pays Maroc

# Collecter les calendriers de prix (rythme prudent, reprise automatique après un blocage)
.\.venv\Scripts\python.exe catalogue.py collecter --origines listes/paris.txt --destinations listes/maroc.txt --nouveaux

# État du catalogue
.\.venv\Scripts\python.exe catalogue.py etat
```

Ou double-cliquer sur `collecter_catalogue.bat` pour collecter tous les trajets non encore collectés.

## À savoir

- Google limite les requêtes automatiques : en cas de blocage (captcha), attendre quelques heures.
- Les prix du catalogue sont ceux que Google garde en mémoire ; le prix exact d'un vol s'obtient avec la recherche
  détaillée. Vérifier toujours le prix final avant de réserver.
