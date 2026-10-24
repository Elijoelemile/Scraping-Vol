@echo off
rem Programme l'alerte des routines : le Planificateur de taches Windows lance alerte.py toutes les 15 minutes.
rem alerte.py decide lui-meme s'il doit envoyer (selon la frequence choisie dans l'application).
rem Pour arreter : schtasks /Delete /TN "Scraping Vol - Alerte routines" /F
cd /d "%~dp0"
schtasks /Create /F /SC MINUTE /MO 15 /TN "Scraping Vol - Alerte routines" /TR "\"%~dp0.venv\Scripts\pythonw.exe\" \"%~dp0alerte.py\""
echo.
echo Alerte programmee (verification toutes les 15 minutes). Journal : alerte.log
pause
