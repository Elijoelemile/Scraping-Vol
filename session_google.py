"""Session Google connectée (compte de l'utilisateur) pour obtenir les prix réservés aux comptes connectés.

L'utilisateur se connecte LUI-MÊME dans une fenêtre Chrome dédiée (profil séparé dans .profil_google/) :
le mot de passe n'est jamais vu ni stocké par l'application. Seuls les cookies de session sont gardés,
localement, dans .profil_google/cookies.json.

    python session_google.py connecter    # ouvre la fenêtre de connexion
    python session_google.py etat
    python session_google.py deconnecter  # supprime la session enregistrée
"""
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

PROFIL = Path(__file__).with_name(".profil_google")
COOKIES = PROFIL / "cookies.json"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
CONNEXION_URL = ("https://accounts.google.com/ServiceLogin?hl=fr"
                 "&continue=https://www.google.com/travel/flights?hl%3Dfr%26gl%3DFR")


def cookies() -> dict[str, str]:
    """Cookies de session enregistrés (vide si pas connecté)."""
    try:
        return {c["name"]: c["value"] for c in json.loads(COOKIES.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {}


def est_connecte() -> bool:
    return "SID" in cookies()


def compte() -> str | None:
    try:
        return json.loads((PROFIL / "compte.json").read_text(encoding="utf-8")).get("email")
    except (OSError, ValueError):
        return None


async def connecter(delai_max: int = 600, log=print) -> bool:
    """Ouvre le vrai Chrome (fenêtre normale, non pilotée) sur la page de connexion Google, avec un profil séparé.

    Google refuse les connexions dans un navigateur piloté par un outil d'automatisation : la fenêtre est donc
    un Chrome ordinaire. L'utilisateur s'y connecte puis la ferme ; on lit ensuite la session dans le profil.
    """
    import subprocess
    if not CHROME.exists():
        raise FileNotFoundError(f"Google Chrome introuvable : {CHROME}")
    PROFIL.mkdir(exist_ok=True)
    log("Connectez-vous à votre compte Google dans la fenêtre Chrome, puis FERMEZ cette fenêtre.")
    fenetre = subprocess.Popen([str(CHROME), f"--user-data-dir={PROFIL / 'chrome'}", "--no-first-run",
                                "--no-default-browser-check", CONNEXION_URL])
    try:
        await asyncio.to_thread(fenetre.wait, delai_max)
    except subprocess.TimeoutExpired:
        fenetre.terminate()
    ok = await lire_session()
    log("Connexion enregistrée." if ok else "Aucune connexion trouvée (la fenêtre a-t-elle été fermée après connexion ?).")
    return ok


async def lire_session() -> bool:
    """Lit les cookies Google du profil Chrome (fenêtre fermée) et les enregistre dans cookies.json."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(str(PROFIL / "chrome"), headless=True,
                                                         executable_path=str(CHROME))
        liste = await ctx.cookies("https://www.google.com")
        email = None
        if any(c["name"] == "SID" for c in liste):
            try:  # adresse e-mail du compte, pour l'afficher dans l'application
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto("https://myaccount.google.com/?hl=fr")
                await page.wait_for_timeout(2500)
                email = await page.evaluate(
                    "() => (document.body.innerText.match(/[\\w.+-]+@[\\w-]+\\.[\\w.]+/) || [null])[0]")
            except Exception:
                pass
        await ctx.close()
    if not any(c["name"] == "SID" for c in liste):
        return False
    COOKIES.write_text(json.dumps(liste, ensure_ascii=False), encoding="utf-8")
    (PROFIL / "compte.json").write_text(json.dumps({"email": email}), encoding="utf-8")
    return True


def deconnecter():
    shutil.rmtree(PROFIL, ignore_errors=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "etat"
    if cmd == "connecter":
        asyncio.run(connecter())
    elif cmd == "deconnecter":
        deconnecter()
        print("Session supprimée.")
    print("Connecté :", est_connecte(), "|", compte() or "")
