"""Отдельный Chrome с постоянным профилем для Google-аккаунта (музыка Google и т.п.).

Пользователь один раз входит сам — сессия (cookies) остаётся в профиле data/chrome_google,
и при следующих запусках он уже залогинен. Пароль софт не хранит и не вводит.
Chrome открывается с портом отладки 9333, чтобы Claude мог подключиться к этой же сессии.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

import requests

from . import store

PROFILE = store.DATA / "chrome_google"
PORT = 9333
DEFAULT_URL = "https://labs.google/fx/tools/music-fx"
_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def chrome_path():
    for c in _CANDIDATES:
        if Path(c).exists():
            return c
    return shutil.which("google-chrome") or shutil.which("chrome")


def running():
    try:
        return requests.get(f"http://127.0.0.1:{PORT}/json/version", timeout=1).ok
    except requests.RequestException:
        return False


def _meta_file():
    return PROFILE / "yt_studio.json"


def status():
    meta = {}
    if _meta_file().exists():
        meta = json.loads(_meta_file().read_text(encoding="utf-8"))
    return {"chrome": bool(chrome_path()), "profile": PROFILE.exists() and (PROFILE / "Default").exists(),
            "running": running(), "port": PORT, "url": meta.get("url") or DEFAULT_URL,
            "account": meta.get("account", ""), "last_open": meta.get("last_open")}


def open_chrome(url=None, account=None):
    exe = chrome_path()
    if not exe:
        raise RuntimeError("Не найден Google Chrome — установи его")
    PROFILE.mkdir(parents=True, exist_ok=True)
    meta = status()
    url = (url or meta["url"]).strip()
    m = {"url": url, "account": account if account is not None else meta["account"], "last_open": time.time()}
    _meta_file().write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    if running():  # уже открыт — просто новая вкладка в той же сессии
        try:
            requests.put(f"http://127.0.0.1:{PORT}/json/new?{url}", timeout=3)
        except requests.RequestException:
            pass
        return status()
    subprocess.Popen([exe, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
                      "--no-first-run", "--no-default-browser-check", url])
    for _ in range(20):
        if running():
            break
        time.sleep(0.5)
    return status()


def reset():
    """Выйти: удалить профиль (сессию)."""
    if running():
        raise RuntimeError("Сначала закрой окно Chrome с аккаунтом")
    shutil.rmtree(PROFILE, ignore_errors=True)
    return status()
