"""Поиск и скачивание медиа: Pexels, Pixabay, Wikimedia Commons, генерация картинок Google (Nano Banana)."""
import base64
import time
import hashlib
from pathlib import Path

import requests
import re
import threading

UA = {"User-Agent": "YTStudio/1.0 (local video editing tool; https://github.com/ytstudio-local) python-requests"}
_BLOCKED = {}  # хост -> до какого времени он нас режет (429)
TIMEOUT = 30


def _orient(fmt):
    return "portrait" if fmt == "9:16" else "landscape"


class QuotaError(RuntimeError):
    """Кончилась квота / лимит запросов или ключ не принят - источник дальше не дёргаем."""


def _chk(r, name):
    if r.status_code == 429:
        raise QuotaError(f"{name}: закончилась квота (лимит запросов, 429) - попробуй позже или завтра")
    if r.status_code in (401, 403):
        raise QuotaError(f"{name}: ключ не принят или доступ закрыт ({r.status_code})")
    if r.status_code == 402:
        raise QuotaError(f"{name}: закончились кредиты (402)")
    if r.status_code >= 400:
        raise RuntimeError(f"{name}: ошибка {r.status_code} {r.text[:150]}")
    return r


_DEAD = {}  # источник -> причина, до перезапуска софта (дневная квота сама не вернётся)


def pexels(query, kind, fmt, key, n=10):
    if not key:
        return []
    h = {"Authorization": key, **UA}
    out = []
    if kind == "video":
        r = requests.get("https://api.pexels.com/videos/search", headers=h, timeout=TIMEOUT,
                         params={"query": query, "orientation": _orient(fmt), "per_page": n})
        for v in _chk(r, "pexels").json().get("videos", []):
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
            if not files:
                continue
            files.sort(key=lambda f: abs(max(f["width"], f["height"]) - 1920))
            f = files[0]
            out.append({"type": "video", "url": f["link"], "thumb": v.get("image"), "source": "pexels",
                        "w": f["width"], "h": f["height"], "dur": v.get("duration"), "credit": v.get("url")})
    else:
        r = requests.get("https://api.pexels.com/v1/search", headers=h, timeout=TIMEOUT,
                         params={"query": query, "orientation": _orient(fmt), "per_page": n})
        for p in _chk(r, "pexels").json().get("photos", []):
            out.append({"type": "image", "url": p["src"]["large2x"], "thumb": p["src"]["medium"],
                        "source": "pexels", "w": p["width"], "h": p["height"], "credit": p.get("url")})
    return out


def pixabay(query, kind, fmt, key, n=10):
    if not key:
        return []
    out = []
    if kind == "video":
        r = requests.get("https://pixabay.com/api/videos/", timeout=TIMEOUT,
                         params={"key": key, "q": query[:100], "per_page": max(n, 3)})
        for v in _chk(r, "pixabay").json().get("hits", []):
            vids = v.get("videos", {})
            f = vids.get("large") if vids.get("large", {}).get("url") else vids.get("medium")
            if not f or not f.get("url"):
                continue
            out.append({"type": "video", "url": f["url"], "thumb": f.get("thumbnail"), "source": "pixabay",
                        "w": f.get("width"), "h": f.get("height"), "dur": v.get("duration"),
                        "credit": v.get("pageURL")})
    else:
        r = requests.get("https://pixabay.com/api/", timeout=TIMEOUT,
                         params={"key": key, "q": query[:100], "image_type": "photo",
                                 "orientation": "vertical" if fmt == "9:16" else "horizontal",
                                 "per_page": max(n, 3)})
        for p in _chk(r, "pixabay").json().get("hits", []):
            out.append({"type": "image", "url": p.get("largeImageURL"), "thumb": p.get("webformatURL"),
                        "source": "pixabay", "w": p.get("imageWidth"), "h": p.get("imageHeight"),
                        "credit": p.get("pageURL")})
    return out


BAD_WORDS = ("diagram", "schema", "schéma", "scheme", "comparison", "chart", "graph", "map ", "_map", "carte", "karte",
             "plan ", "plan.", "section", "icon", "logo", "cover", "drawing", "rampe", "sketch", "infographic", "svg",
             "coat of arms", "flag of", "sheet ", "table ")


def _free_license(meta):
    """Файл без обязательной атрибуции: общественное достояние / CC0 / PD-* (не надо указывать автора)."""
    lic = ((meta.get("License") or {}).get("value") or "").lower()
    short = ((meta.get("LicenseShortName") or {}).get("value") or "").lower()
    attr = ((meta.get("AttributionRequired") or {}).get("value") or "").lower()
    pd = lic.startswith("pd") or lic == "cc0" or "public domain" in short or short.startswith("pd") or "cc0" in short
    return pd and attr != "true"


def wikimedia(query, kind, fmt, key=None, n=40):
    """Wikimedia Commons — без ключа, бесплатно. Только картинки (видео там редкие и в webm)."""
    if kind == "video":
        return []
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=TIMEOUT, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"filetype:bitmap {query}", "gsrlimit": n, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiextmetadatafilter": "License|LicenseShortName|AttributionRequired",
        "iiurlwidth": 1920})
    pages = (_chk(r, "wikimedia").json().get("query") or {}).get("pages", {})
    out = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        if (ii.get("width") or 0) < 640:
            continue
        title = (p.get("title") or "").lower()
        if any(w in title for w in BAD_WORDS):
            continue  # схемы, чертежи, карты, обложки — не для кадра
        if not _free_license(ii.get("extmetadata") or {}):
            continue  # только общественное достояние / CC0 — без указания авторов в описании
        out.append({"type": "image", "url": ii.get("thumburl") or ii.get("url"), "thumb": ii.get("thumburl"),
                    "source": "wikimedia", "w": ii.get("width"), "h": ii.get("height"),
                    "credit": ii.get("descriptionurl")})
    return out


PROVIDERS = {"pexels": pexels, "pixabay": pixabay, "wikimedia": wikimedia}


def search(query, kind, fmt, settings, only=None):
    """Ищет по всем включённым источникам в заданном порядке. kind: video|image."""
    results, errors = [], []
    for name in (only or settings.get("sources") or []):
        fn = PROVIDERS.get(name)
        if not fn:
            continue
        if name in _DEAD and _DEAD[name][1] > time.time():
            continue
        key = settings.get(f"{name}_key")
        try:
            results += fn(query, kind, fmt, key)
        except QuotaError as e:  # квота кончилась - пишем один раз и выключаем источник на час
            _DEAD[name] = (str(e), time.time() + 3600)
            errors.append(f"⛔ {e}. {name} отключён на час, ищу в остальных")
        except Exception as e:  # источник упал — идём дальше
            errors.append(f"{name}: {e}")
    return results, errors


def download(url, folder: Path, ext_hint=""):
    folder.mkdir(parents=True, exist_ok=True)
    ext = ext_hint or Path(url.split("?")[0]).suffix.lower() or ".bin"
    if ext not in (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp"):
        ext = ".mp4" if "video" in url else ".jpg"
    name = hashlib.md5(url.encode()).hexdigest()[:16] + ext
    path = folder / name
    if path.exists() and path.stat().st_size > 0:
        return path
    host = url.split("/")[2] if "://" in url else ""
    if _BLOCKED.get(host, 0) > time.time():
        raise RuntimeError(f"{host} временно ограничил скачивание — беру другие кадры")
    for attempt in range(3):
        r = requests.get(url, headers=UA, stream=True, timeout=120)
        if r.status_code not in (429, 503):
            break
        r.close()
        if attempt == 2:
            # хост режет частые запросы — на пару минут больше к нему не ходим, берём кадры из других источников
            _BLOCKED[host] = time.time() + 120
            raise RuntimeError(f"{host} временно ограничил скачивание (429)")
        time.sleep(2 * (attempt + 1))
    with r:
        r.raise_for_status()
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        tmp.replace(path)
    return path


def _why(e):
    """Человеческая причина: квота/кредиты/ключ - отдельно от прочих ошибок."""
    t = str(e)
    low = t.lower()
    if "429" in t or "quota" in low or "quota_exceeded" in low or "resource_exhausted" in low or "rate limit" in low:
        return "⛔ закончилась квота (" + t[:120] + ")"
    if "402" in t or "credit" in low or "insufficient" in low:
        return "⛔ закончились кредиты (" + t[:120] + ")"
    if "401" in t or "403" in t:
        return "⛔ ключ не принят (" + t[:120] + ")"
    return t[:200]


def glabs_url(settings):
    """Адрес G-Labs из настроек: «9000», «192.168.1.5», «192.168.1.5:9000» или полный http://…"""
    b = (settings.get("glabs_base") or "").strip().rstrip("/")
    if not b:
        return "http://127.0.0.1:8765"
    if b.isdigit():
        b = "127.0.0.1:" + b
    if "://" in b:
        return b  # полный адрес — как есть
    return "http://" + b + ("" if ":" in b else ":8765")


def _glabs_task(r):
    """G-Labs отвечает 200/201/202 и task_id; всё остальное — ошибка."""
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"G-Labs {r.status_code}: {r.text[:200]}")
    try:
        tid = r.json().get("task_id")
    except ValueError:
        tid = None
    if not tid:
        raise RuntimeError(f"G-Labs {r.status_code}: нет task_id в ответе: {r.text[:200]}")
    return tid


CANCEL = threading.local()  # .event — «Стоп» текущей сборки (ставит pipeline в рабочем потоке)


def _wait(sec):
    """Пауза опроса, которую прерывает «Стоп»."""
    ev = getattr(CANCEL, "event", None)
    if ev is None:
        time.sleep(sec)
    elif ev.wait(sec):
        raise RuntimeError("остановлено")


def is_name(q):
    """Запрос похож на имя человека/бренда/места: Scottie Pippen, Air Jordan, NASA."""
    words = re.findall(r"[^\W\d_][\w'’.-]*", str(q or ""))
    if any(w.isupper() and len(w) >= 2 for w in words):
        return True
    return sum(w[0].isupper() for w in words) >= 2 or any(w[0].isupper() for w in words[1:])


def _fix_image(path: Path):
    """G-Labs иногда отдаёт PNG с битым exif-блоком — PIL его не открывает. Перекодируем ffmpeg без метаданных."""
    from PIL import Image
    try:
        Image.open(path).load()
        return
    except Exception:
        pass
    import subprocess
    tmp = path.with_name(path.stem + "_fix.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map_metadata", "-1", str(tmp)], check=True)
    tmp.replace(path)


def glabs_image(prompt, fmt, settings, folder: Path, refs=()):
    """G-Labs (Nano Banana Pro): задача -> опрос статуса -> скачать картинку."""
    base = glabs_url(settings)
    h = {"X-API-Key": settings["glabs_key"]}
    path = folder / (hashlib.md5(f"glabs|{prompt}|{fmt}|{refs}".encode()).hexdigest()[:16] + ".png")
    if path.exists() and path.stat().st_size > 10000:
        return path
    r = requests.post(f"{base}/api/image/generate", headers=h, timeout=60,
                      json={"prompt": prompt, "model": settings.get("glabs_model") or "nano_banana_pro",
                            "aspect_ratio": fmt or "16:9", "reference_images": [{"path": str(p)} for p in refs]})
    tid = _glabs_task(r)
    for _ in range(150):
        _wait(4)
        st = requests.get(f"{base}/api/status/{tid}", headers=h, timeout=30).json()
        if st.get("status") == "completed":
            img = requests.get(st["results"][0], timeout=120)
            folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(img.content)
            _fix_image(path)
            return path
        if st.get("status") == "failed":
            raise RuntimeError(f"G-Labs: {st.get('error_detail') or st.get('error')}")
    raise RuntimeError("G-Labs: не дождался картинки")


def glabs_video(prompt, fmt, settings, folder: Path, refs=(), length=4):
    """G-Labs видео (Omni Flash, 720p, 4-8 с): без рефов text_to_video, с рефами - «Ингредиенты»."""
    base = glabs_url(settings)
    h = {"X-API-Key": settings["glabs_key"]}
    path = folder / (hashlib.md5(f"glabsv|{prompt}|{fmt}|{refs}|{length}".encode()).hexdigest()[:16] + ".mp4")
    if path.exists() and path.stat().st_size > 50000:
        return path
    r = requests.post(f"{base}/api/video/generate", headers=h, timeout=60,
                      json={"prompt": prompt, "model": settings.get("glabs_video_model") or "omni_flash",
                            "mode": "components" if refs else "text_to_video", "aspect_ratio": fmt or "16:9",
                            "resolution": ["720p"], "video_length": int(length),
                            "reference_images": [{"path": str(p).replace("\\", "/")} for p in refs]})
    tid = _glabs_task(r)
    for _ in range(240):
        _wait(5)
        st = requests.get(f"{base}/api/status/{tid}", headers=h, timeout=30).json()
        if st.get("status") == "completed":
            v = requests.get(st["results"][0], timeout=300)
            folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(v.content)
            return path
        if st.get("status") == "failed":
            raise RuntimeError(f"G-Labs: {st.get('error_detail') or st.get('error')}")
    raise RuntimeError("G-Labs: не дождался видео")


def glabs_check(settings):
    base = glabs_url(settings)
    try:
        r = requests.get(f"{base}/api/status/ping", headers={"X-API-Key": settings.get("glabs_key", "")}, timeout=10)
    except Exception as e:
        return {"ok": False, "msg": f"G-Labs не отвечает по {base}: {e.__class__.__name__}"}
    if r.status_code in (401, 403):
        return {"ok": False, "msg": "ключ не подходит"}
    return {"ok": True, "msg": f"G-Labs на связи ({base})"}


def ai_image(prompt, fmt, settings, folder: Path, seed=0, log=lambda m: None):
    """ИИ-картинка: G-Labs, потом Google (Nano Banana), если есть ключ, иначе бесплатный Pollinations (Flux)."""
    if settings.get("glabs_key"):
        try:
            return glabs_image(prompt + (f" (variant {seed})" if seed else ""), fmt, settings, folder)
        except Exception as e:
            log(f"  G-Labs не дал картинку: {_why(e)} - пробую дальше")
    if settings.get("google_key"):
        try:
            return google_image(prompt + (f" (variant {seed})" if seed else ""), fmt, settings, folder)
        except Exception as e:
            log(f"  Google не дал картинку: {_why(e)} - пробую дальше")
    if not settings.get("free_ai"):
        raise RuntimeError("NO_KEY: нет Google API ключа для ИИ-картинок")
    import urllib.parse
    w, h = (1088, 1920) if fmt == "9:16" else (1920, 1088)
    url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
           f"?width={w}&height={h}&nologo=true&model=flux&seed={seed + 1}")
    path = folder / (hashlib.md5(f"{prompt}|{seed}|{fmt}".encode()).hexdigest()[:16] + ".jpg")
    if path.exists() and path.stat().st_size > 10000:
        return path
    last = None
    for _ in range(3):
        try:
            r = requests.get(url, timeout=180)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 10000:
                folder.mkdir(parents=True, exist_ok=True)
                path.write_bytes(r.content)
                from PIL import Image  # срезаем водяной знак бесплатного тарифа (низ кадра)
                im = Image.open(path).convert("RGB")
                im.crop((0, 0, im.width, int(im.height * 0.95))).save(path, quality=94)
                return path
            last = f"{r.status_code} {r.text[:120]}"
        except Exception as e:
            last = str(e)
    raise RuntimeError("Pollinations: " + str(last))


def google_image(prompt, fmt, settings, folder: Path):
    """Генерация картинки через Gemini (Nano Banana). Нужен google_key."""
    key = settings.get("google_key")
    if not key:
        raise RuntimeError("Нет Google API ключа в настройках")
    model = settings.get("google_image_model") or "gemini-2.5-flash-image"
    ratio = "9:16" if fmt == "9:16" else "16:9"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": ratio}}}
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      params={"key": key}, json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"Google {r.status_code}: {r.text[:300]}")
    for cand in r.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
            if data:
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (hashlib.md5(prompt.encode()).hexdigest()[:16] + ".png")
                path.write_bytes(base64.b64decode(data))
                return path
    raise RuntimeError("Google не вернул картинку")
