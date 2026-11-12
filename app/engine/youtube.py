"""Клипы с YouTube по теме сцены: поиск -> выбор моментов (Gemini смотрит ролик) -> вырезка 3-5 с.
Качается только нужный кусок (yt-dlp download_ranges), не весь ролик.
"""
import hashlib
import json
import re
import struct
from pathlib import Path

import requests

MIN_DUR, MAX_DUR = 60, 3600   # ролики короче минуты (shorts) и длиннее часа не берём
GEMINI_MAX = 1500             # дольше 25 мин Gemini не показываем - дорого, берём моменты равномерно
# ролики-обзоры, реклама, уроки, мультики: в кадре товар, говорящая голова или текст, а не тема сцены
BAD_TITLE = ("how to", "review", "unboxing", "haul", "tutorial", "organiz", "product", "guide", "store", "shop",
             "fold", "diy", "hack", "tips", "recipe", "drawing", "draw ", "cartoon", "animation", "kids", "song",
             "reaction", "podcast", "interview", "news", "live", "vlog", "asmr", "minecraft", "roblox", "game",
             "sale", "buy", "best ", "top 10", "vs ", "ad ", "commercial", "#shorts", "explained", "lesson")


def bad_title(t):
    t = f" {t.lower()} "
    return any(w in t for w in BAD_TITLE)


def search(query, n=5):
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "js_runtimes": {"node": {}}, "extract_flat": True, "skip_download": True, "noprogress": True}) as y:
        info = y.extract_info(f"ytsearch{n * 2}:{query}", download=False)
    out = []
    for e in info.get("entries") or []:
        d = e.get("duration") or 0
        if e.get("id") and MIN_DUR <= d <= MAX_DUR and e.get("live_status") not in ("is_live", "is_upcoming") \
                and not bad_title(e.get("title", "")):
            out.append({"id": e["id"], "title": e.get("title", ""), "duration": d})
    return out[:n]


def pick_moments(v, scene_text, k, length, settings, log=lambda m: None):
    """Секунды начала k кусков. Gemini смотрит ролик и выбирает кадры под текст сцены; без ключа - равномерно."""
    d = v["duration"]
    even = [int(d * (0.15 + 0.7 * (i + 0.5) / k)) for i in range(k)]
    key = settings.get("google_key")
    if not key or d > GEMINI_MAX:
        return even
    model = settings.get("google_text_model") or "gemini-2.5-flash"
    ask = (f"This video will be used as B-roll for a narration: «{scene_text[:600]}».\n"
           f"Pick {k} different moments, each {length} seconds long, that best show this visually. "
           "Avoid intros, outros, logos, title cards, subscribe screens, text overlays and talking heads in a studio. "
           'Answer only JSON: [{"start": seconds, "why": "short"}]')
    body = {"contents": [{"parts": [{"file_data": {"file_uri": f"https://www.youtube.com/watch?v={v['id']}"}},
                                    {"text": ask}]}],
            "generationConfig": {"responseMimeType": "application/json", "mediaResolution": "MEDIA_RESOLUTION_LOW"}}
    try:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          params={"key": key}, json=body, timeout=240)
        if r.status_code != 200:
            raise RuntimeError(f"Google {r.status_code}: {r.text[:200]}")
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        starts = [int(float(m["start"])) for m in json.loads(re.sub(r"^```\w*|```$", "", txt.strip()))]
        starts = [s for s in starts if 3 <= s <= d - length - 3][:k]
        if starts:
            return starts
    except Exception as e:
        from .sources import _why
        log(f"  YouTube: Gemini не выбрал моменты ({_why(e)}) - беру равномерно")
    return even


def _boxes(b, off=0):
    while off + 8 <= len(b):
        size, typ = struct.unpack(">I4s", b[off:off + 8])
        if size == 1:
            size = struct.unpack(">Q", b[off + 8:off + 16])[0]
        if size < 8:
            return
        yield typ.decode("latin1"), off, size
        off += size


def _sidx(b, off):
    """Оглавление DASH-потока: [(байт, секунд)] по фрагментам, смещение первого, время первого."""
    ver = b[off + 8]
    p = off + 16
    ts = struct.unpack(">I", b[p:p + 4])[0]
    p += 4
    if ver == 0:
        ept, first = struct.unpack(">II", b[p:p + 8])
        p += 8
    else:
        ept, first = struct.unpack(">QQ", b[p:p + 16])
        p += 16
    n = struct.unpack(">H", b[p + 2:p + 4])[0]
    p += 4
    refs = []
    for _ in range(n):
        a, dur, _sap = struct.unpack(">III", b[p:p + 12])
        p += 12
        refs.append((a & 0x7FFFFFFF, dur / ts))
    return refs, first, ept / ts


def _fast_cut(vid, start, length, path: Path):
    """Быстро (~3 с): берём из DASH-потока только заголовок и фрагменты нужных секунд (по оглавлению sidx),
    двумя запросами с &range= — без этого YouTube режет скорость и кусок качается минутами."""
    import subprocess
    import tempfile
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "noprogress": True, "js_runtimes": {"node": {}},
                           "format": "bv*[height<=1080][ext=mp4][vcodec^=avc1]/bv*[height<=1080][ext=mp4]"}) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    url, hdr = info["url"], info.get("http_headers") or {}
    head = requests.get(url + "&range=0-262143", headers=hdr, timeout=20).content
    init_end, refs = 0, None
    for typ, off, size in _boxes(head):
        if typ in ("ftyp", "moov"):
            init_end = off + size
        if typ == "sidx":
            refs, first, t = _sidx(head, off)
            pos = off + size + first
            break
    if not refs or not init_end:
        raise RuntimeError("в потоке нет оглавления sidx")
    rng = None
    for size, dur in refs:
        if rng is None and t + dur > start:
            rng = [pos, pos + size - 1, t]
        elif rng and t < start + length:
            rng[1] = pos + size - 1
        pos += size
        t += dur
    if not rng:
        raise RuntimeError("момент за концом ролика")
    body = requests.get(url + f"&range={rng[0]}-{rng[1]}", headers=hdr, timeout=60).content
    if len(body) < rng[1] - rng[0]:
        raise RuntimeError("фрагмент скачался не целиком")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.mp4"
        raw.write_bytes(head[:init_end] + body)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start - rng[2]:.2f}", "-i", str(raw), "-t", str(length),
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(path)], check=True)
    return path


def cut(vid, start, length, folder: Path):
    import yt_dlp
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.md5(f"yt|{vid}|{start}|{length}".encode()).hexdigest()[:16]
    path = folder / f"{name}.mp4"
    if path.exists() and path.stat().st_size > 20000:
        return path
    try:
        _fast_cut(vid, start, length, path)
        if path.stat().st_size > 20000:
            return path
    except Exception:
        path.unlink(missing_ok=True)  # не вышло быстро — качаем по-старому, через yt-dlp
    opts = {"quiet": True, "noprogress": True, "js_runtimes": {"node": {}}, "outtmpl": str(folder / f"{name}.%(ext)s"),
            "format": "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/best[height<=1080]",
            "download_ranges": yt_dlp.utils.download_range_func(None, [(start, start + length)]),
            "force_keyframes_at_cuts": True, "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={vid}"])
    if not path.exists():
        got = sorted(folder.glob(f"{name}.*"))
        if not got:
            raise RuntimeError("кусок не скачался")
        got[0].rename(path)
    return path


def clips(query, scene_text, need, settings, folder: Path, log=lambda m: None, used=(), length=4, per_video=2,
          check=None):
    """need кусков по length секунд из разных роликов по запросу.
    used — id роликов, занятых другими сценами (общий set: сцены ищутся параллельно, ролик забираем сразу);
    check(path) -> (подходит?, почему) — кусок, который не про сцену, не берём."""
    out = []
    try:
        vids = [v for v in search(query) if v['id'] not in used]
    except Exception as e:
        from .sources import _why
        log(f"  YouTube: поиск не сработал - {_why(e)}")
        return out
    for v in vids:
        if len(out) >= need:
            break
        if isinstance(used, set):
            if v["id"] in used:
                continue
            used.add(v["id"])
        k = min(per_video, need - len(out))
        for s in pick_moments(v, scene_text, k, length, settings, log):
            try:
                p = cut(v["id"], s, length, folder)
                ok, why = check(p) if check else (None, "")
                if ok is False:
                    log(f"  YouTube: «{v['title'][:50]}» {s // 60}:{s % 60:02d} не про сцену ({why}) - пропускаю")
                    continue
                out.append({"type": "video", "url": f"yt:{v['id']}:{s}", "source": "youtube", "local": p,
                            "title": v["title"]})
                log(f"  YouTube: «{v['title'][:60]}» {s // 60}:{s % 60:02d} (+{length} с)")
            except Exception as e:
                log(f"  YouTube: не вырезался кусок {v['id']} @{s}s - {str(e)[:150]}")
    return out
