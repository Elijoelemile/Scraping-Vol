@echo off
rem Collecte du catalogue des prix (1 an) - a lancer par double-clic.
rem Commence par les trajets jamais collectes (France -> Thailande en priorite), reprend la ou il s'est arrete.
rem Rythme prudent ; en cas de blocage Google : pause d'1 h puis reprise (10 blocages max).
rem Le journal s'ecrit dans collecte.log. Fermez cette fenetre pour arreter.
cd /d "%~dp0"
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
echo Collecte en cours... (journal : collecte.log) - fermez cette fenetre pour arreter.
".venv\Scripts\python.exe" -u catalogue.py collecter --delai 6 --pause-blocage 60 --max-blocages 10 >> collecte.log 2>&1
echo Collecte terminee ou arretee. Voir collecte.log
pause
