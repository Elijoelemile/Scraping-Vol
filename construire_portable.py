"""Construit la version portable de l'application : dist/Scraping Vol.zip.

Le dossier contient son propre Python (version « embeddable » officielle), les bibliothèques de .venv et
l'application, sans vos données : la base est créée vide au premier lancement.
Sur l'autre ordinateur (Windows 10/11, 64 bits) : décompresser, puis double-cliquer sur « Lancer Scraping Vol.bat ». Rien à installer.

    .venv\\Scripts\\python.exe construire_portable.py                 (à partager : base vide)
    .venv\\Scripts\\python.exe construire_portable.py --avec-ma-base  (pour soi : base, donnée Airfox, historiques)
"""
import shutil
import sqlite3
import sys
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path

ICI = Path(__file__).parent
DIST = ICI / "dist"
PORTABLE = DIST / "Scraping Vol"
VERSION_PYTHON = "3.12.10"  # même version mineure que .venv (3.12) : les bibliothèques compilées restent compatibles
URL_PYTHON = f"https://www.python.org/ftp/python/{VERSION_PYTHON}/python-{VERSION_PYTHON}-embed-amd64.zip"

# Application : code et listes. Pas de journaux, ni de réglages ou session personnels (session Google, adresse
# e-mail des alertes), ni d'actualisation en cours. Par défaut, pas vos données non plus : la personne qui reçoit
# le zip part d'une base vide (créée au premier lancement) et charge sa propre donnée Airfox.
FICHIERS = ["app.py", "catalogue.py", "db.py", "donnees.py", "google_flights_scraper.py", "pays.py", "recap.py",
            "session_google.py", "titres.py", "alerte.py", "actions.py", "comptes_drive.py", "origines.txt", "requirements.txt", "README.md"]
DOSSIERS = ["pages_app", "listes", ".streamlit"]  # .streamlit : réglages (police Montserrat)
# --avec-ma-base : ajoute la base, la donnée Airfox chargée et les historiques (pour votre propre autre ordinateur)
MES_DONNEES = (["vols.db", "derniere_table.json"], ["donnee_chargee", "historique_catalogue", "historique_actualisations",
                                "historique_tables"])

LANCEUR = r"""@echo off
chcp 65001 >nul
title Scraping Vol
cd /d "%~dp0app"
rem uniquement les bibliotheques du dossier (pas celles d'un Python deja installe sur ce PC)
set PYTHONNOUSERSITE=1
echo.
echo   Scraping Vol demarre... L'application va s'ouvrir dans une fenetre.
echo   Laissez cette fenetre noire ouverte : la fermer arrete l'application
echo   (une actualisation en cours continue quand meme en arriere-plan).
echo.
start "" /b "%~dp0python\python.exe" -m streamlit run app.py --server.port 8520 --server.headless true --browser.gatherUsageStats false
powershell -NoProfile -Command "for($i=0;$i -lt 90;$i++){try{Invoke-WebRequest -UseBasicParsing http://localhost:8520/_stcore/health -TimeoutSec 2 | Out-Null; exit 0}catch{Start-Sleep 1}}; exit 1"
if errorlevel 1 (
  echo   L'application ne repond pas. Relancez ce fichier ; si cela persiste, envoyez une capture de cette fenetre.
  pause
  exit /b 1
)
rem Google Chrome s'il est installe (fenetre sans barre d'adresse), sinon Microsoft Edge
set "CHROME="
for %%c in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe") do if exist %%c set "CHROME=%%~c"
if not defined CHROME goto edge
start "" "%CHROME%" --app=http://localhost:8520
exit /b 0
:edge
start "" msedge --app=http://localhost:8520
"""

LISEZ_MOI = """SCRAPING VOL - version portable
================================

Ordinateur : Windows 10 ou 11, 64 bits, connecté à Internet. Rien à installer.

1. Décompresser « Scraping Vol.zip » (clic droit > Extraire tout), par exemple sur le Bureau.
   Si Windows a marqué le fichier comme venant d'Internet : clic droit sur le .zip > Propriétés >
   cocher « Débloquer » > OK, avant d'extraire.
2. Ouvrir le dossier « Scraping Vol » et double-cliquer sur « Lancer Scraping Vol.bat ».
3. L'application s'ouvre dans une fenêtre Google Chrome (ou Microsoft Edge si Chrome n'est pas
   installé). Si elle ne s'ouvre pas, aller à l'adresse
   http://localhost:8520 dans un navigateur.

Pour quitter : fermer la fenêtre de l'application, puis la fenêtre noire.

Au premier lancement, la base est vide : charger la donnée Airfox dans « Charger la donnée », puis lancer
les recherches dans « Catalogue aller-retour ».
Les données (catalogue, recherches, donnée Airfox, historiques) sont dans le dossier « app ».
Les recherches faites sur cet ordinateur restent sur cet ordinateur.
"""


def telecharger_python() -> Path:
    cache = DIST / "cache" / Path(URL_PYTHON).name
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        print(f"Téléchargement de Python {VERSION_PYTHON}…")
        urllib.request.urlretrieve(URL_PYTHON, cache)
    return cache


def ignorer(dossier, noms):
    return [n for n in noms if n == "__pycache__" or n.endswith((".pyc", ".log"))]


def main():
    fichiers, dossiers = FICHIERS, DOSSIERS
    if "--avec-ma-base" in sys.argv:
        fichiers, dossiers = FICHIERS + MES_DONNEES[0], DOSSIERS + MES_DONNEES[1]
    if PORTABLE.exists():
        shutil.rmtree(PORTABLE)
    python = PORTABLE / "python"
    python.mkdir(parents=True)
    with zipfile.ZipFile(telecharger_python()) as z:
        z.extractall(python)
    # Python « embeddable » : chemins de recherche fixés par python312._pth ; on y ajoute les bibliothèques
    # et le dossier de l'application (sinon le programme d'actualisation en arrière-plan ne trouve pas ses modules)
    pth = next(python.glob("python3*._pth"))
    pth.write_text(pth.read_text().replace("#import site", "import site") + "Lib\\site-packages\n..\\app\n")
    print("Copie des bibliothèques…")
    shutil.copytree(ICI / ".venv" / "Lib" / "site-packages", python / "Lib" / "site-packages", ignore=ignorer)

    print("Copie de l'application…")
    app = PORTABLE / "app"
    app.mkdir()
    for f in fichiers:
        if f == "vols.db" and (ICI / f).exists():
            # copie cohérente même si une actualisation écrit dans la base en ce moment
            with closing(sqlite3.connect(ICI / f)) as source, closing(sqlite3.connect(app / f)) as copie:
                source.backup(copie)
        elif (ICI / f).exists():
            shutil.copy2(ICI / f, app / f)
    for d in dossiers:
        if (ICI / d).exists():
            shutil.copytree(ICI / d, app / d, ignore=ignorer)
    (PORTABLE / "Lancer Scraping Vol.bat").write_text(LANCEUR.replace("\n", "\r\n"), encoding="utf-8")
    (PORTABLE / "LISEZ-MOI.txt").write_text(LISEZ_MOI.replace("\n", "\r\n"), encoding="utf-8-sig")

    print("Compression…")
    archive = DIST / "Scraping Vol.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(PORTABLE.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(DIST))
    print(f"Terminé : {archive} ({archive.stat().st_size / 1e6:.0f} Mo)")


if __name__ == "__main__":
    sys.exit(main())
