"""Хранилище: настройки, проекты, референсы. Всё лежит в папке data/ рядом с софтом."""
import json
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PROJECTS = DATA / "projects"
REFS = DATA / "references"
SFX = DATA / "sfx"
SETTINGS_FILE = DATA / "settings.json"

for d in (DATA, PROJECTS, REFS, SFX):
    d.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()

DEFAULT_SETTINGS = {
    "pexels_key": "",
    "pixabay_key": "",
    "google_key": "",          # Gemini / Nano Banana — генерация картинок
    "google_image_model": "gemini-2.5-flash-image",
    "anthropic_key": "",       # Claude — умный разбор сценария (необязательно)
    "anthropic_model": "claude-sonnet-5-5",
    "google_text_model": "gemini-2.5-flash",  # разбор сценария, если нет ключа Claude
    "elevenlabs_key": "",
    "elevenlabs_voice": "21m00Tcm4TlvDq8ikWAM",
    "lmnt_key": "",
    "fish_dir": "F:/AI/fish-speech",  # локальный Fish Speech (клон голоса)
    "fish_port": "8180",
    "glabs_key": "",          # G-Labs (Nano Banana Pro)
    "glabs_base": "http://127.0.0.1:8765",
    "suno_key": "",
    "suno_base": "https://api.sunoapi.org",
    "suno_model": "V4_5",
    "sources": ["pexels", "pixabay", "wikimedia"],
}

DEFAULT_STYLE = {
    "format": "16:9",           # 16:9 или 9:16
    "fps": 30,
    "shot_len": 4.0,            # средняя длина кадра, сек (из референса)
    "media_pref": "video",      # video | image | mix
    "subtitles": True,
    "sub_words": 5,
    "sub_position": "bottom",   # bottom | center
    "sub_size": 64,
    "sub_color": "#FFFFFF",
    "transitions": True,
    "transition": "fade",       # любой xfade: fade, slideleft, wipeleft, circleopen, dissolve, smoothleft...
    "transition_dur": 0.4,
    "zoom": True,               # Ken Burns на картинках
    "photo_card": "auto",
    "ai_share": 0.6,  # >0 — ИИ дорисовывает, где не хватило стоков; 1.0 — все кадры ИИ; 0 — без ИИ
    "yt_share": 0.3,           # доля кусков с YouTube в сцене (ищутся сами, вместе со стоками)
    "yt_len": 4,               # длина куска с YouTube, с
    "ai_kind": "image",        # ИИ-кадры: image (картинки) или video (клипы G-Labs)
    "font": "Georgia",          # шрифт субтитров и плашек (с засечками, как в референсе)            # доля ИИ-кадров в сцене (как в референсе)       # фото карточкой на тёмном фоне: auto (если не влезает в кадр) / all / off
    "plates": True,             # плашки-заголовки сцен
    "plate_style": "ass",       # "ass" — текстовая плашка; "label" — светлая плашка печатается поверх кадра (gfx)
    "plate_color": "#141414",
    "sfx": True,                # свуш на смене сцены
    "sfx_volume": 0.3,
    "sfx_name": "",
    "voice": "edge",            # none | edge | local | elevenlabs | lmnt
    "edge_voice": "ru-RU-DmitryNeural",
    "voice_id": "",             # голос выбранного сервиса (или свой ID из ElevenLabs/LMNT)
    "lang": "ru",
    "music_volume": 0.18,       # громкость фона под голосом
}


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_id():
    return uuid.uuid4().hex[:10]


def read_json(path: Path, default=None):
    for _ in range(10):  # файл может быть занят/недописан соседней записью — пробуем ещё
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (PermissionError, ValueError):
            time.sleep(0.02)
        except Exception:
            return default
    return default


def write_json(path: Path, obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    with _lock:
        tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        # Windows не даёт заменить файл, пока его кто-то читает (другой поток, антивирус) — ждём и повторяем
        for _ in range(20):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                time.sleep(0.05)
        try:  # не вышло за ~1 с — пишем прямо в файл, лишь бы не терять данные
            path.write_text(text, encoding="utf-8")
        finally:
            tmp.unlink(missing_ok=True)


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(read_json(SETTINGS_FILE, {}) or {})
    return s


def save_settings(patch: dict):
    s = get_settings()
    s.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    write_json(SETTINGS_FILE, s)
    return s


# ---------- проекты ----------

def project_dir(pid: str) -> Path:
    return PROJECTS / pid


def load_project(pid: str):
    p = read_json(project_dir(pid) / "project.json")
    if p is None:
        raise KeyError(pid)
    style = dict(DEFAULT_STYLE)
    style.update(p.get("style") or {})
    p["style"] = style
    return p


def save_project(p: dict):
    p["updated"] = now()
    write_json(project_dir(p["id"]) / "project.json", p)
    return p


def create_project(name: str, fmt: str = "16:9", channel_id=None):
    pid = new_id()
    d = project_dir(pid)
    for sub in ("media", "voice", "music", "render"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    style = dict(DEFAULT_STYLE)
    style["format"] = fmt
    p = {
        "id": pid, "name": name or "Новый проект", "created": now(), "updated": now(),
        "reference_id": None, "channel_id": channel_id, "style": style, "script": "",
        "meta": {"title": "", "description": "", "tags": "", "thumb_prompts": []},
        "scenes": [], "music": None, "output": None,
    }
    return save_project(p)


def list_projects(channel_id=None):
    out = []
    for d in sorted(PROJECTS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        p = read_json(d / "project.json")
        if p and (channel_id is None or p.get("channel_id") == (channel_id or None)):
            out.append({"id": p["id"], "name": p["name"], "updated": p.get("updated"),
                        "channel_id": p.get("channel_id"),
                        "format": (p.get("style") or {}).get("format", "16:9"),
                        "scenes": len(p.get("scenes", [])), "output": p.get("output")})
    return out


# ---------- референсы ----------

def list_refs():
    out = []
    for d in sorted(REFS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        r = read_json(d / "ref.json")
        if r:
            out.append(r)
    return out


def load_ref(rid: str):
    r = read_json(REFS / rid / "ref.json")
    if r is None:
        raise KeyError(rid)
    return r


def save_ref(r: dict):
    (REFS / r["id"]).mkdir(parents=True, exist_ok=True)
    write_json(REFS / r["id"] / "ref.json", r)
    return r


# ---------- каналы ----------

def _chfile():
    return DATA / "channels.json"


def list_channels():
    return read_json(_chfile(), []) or []


def save_channels(chs):
    write_json(_chfile(), chs)
    return chs


def get_channel(cid):
    return next((c for c in list_channels() if c["id"] == cid), None)


def find_channel(name_or_id):
    k = (name_or_id or "").strip().lower()
    return next((c for c in list_channels() if c["id"] == k or c["name"].strip().lower() == k), None)


def export_to_channel(cid, path, extra=None, log=print):
    """Копия готового файла в папку канала (если у канала задана папка). extra: {имя_файла: текст}."""
    import shutil
    c = get_channel(cid) if cid else None
    out = (c or {}).get("output_dir", "").strip()
    if not out:
        return None
    try:
        dst = Path(out)
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dst / Path(path).name)
        for name, text in (extra or {}).items():
            if text:
                (dst / name).write_text(text, encoding="utf-8")
        log(f"Сохранено в папку канала: {dst / Path(path).name}")
        return str(dst / Path(path).name)
    except Exception as e:
        log(f"Не смог сохранить в папку канала {out}: {e}")
        return None


def upsert_channel(c):
    chs = [x for x in list_channels() if x["id"] != c["id"]]
    chs.append(c)
    save_channels(chs)
    return c
