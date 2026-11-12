"""Музыкальные ролики (lofi / jazz / ambient): библиотека треков и клипов → длинный ролик на 1–4 часа.

Библиотека живёт в data/mixes/<id>/: music/ (треки), visuals/ (картинки и видео), clips/ (готовые
нормализованные клипы), out/ (ролики). Каждый ролик собирается из случайной смеси: сначала всё новое
(ещё не использованное), потом то, что использовалось реже всего, — так ролики канала не повторяют
друг друга. Видео склеивается без перекодирования (-c copy), поэтому даже 3 часа собираются за минуты.
"""
import random
import statistics
import threading
from pathlib import Path

from . import audio, ff, musicscan, sources, store

MIXES = store.DATA / "mixes"
MIXES.mkdir(parents=True, exist_ok=True)
IMG = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

DEFAULT = {
    "target_min": 90,            # длина ролика, мин (подставляется из референса)
    "block_min": "5,10,15,20,25",  # сколько минут играет один трек (повтор); пусто = каждый трек один раз
    "crossfade": 6,              # сек, переход между треками
    "clip_len": 10,              # длина клипа из картинки, сек
    "max_clip": 15,              # видео длиннее — обрезаются
    "visuals_per_video": 25,     # сколько клипов берём в один ролик
    "format": "16:9",
    "music_prompt": "lofi jazz hip hop, mellow piano, soft brushed drums, warm vinyl crackle, 75 bpm, relaxing, instrumental",
    "image_prompt": "cozy rainy evening cafe window, warm lights, lofi anime illustration style, calm atmosphere",
}

_jobs = {}


# ------------------------------------------------------------------ хранилище

def mdir(mid):
    return MIXES / mid


def load(mid):
    m = store.read_json(mdir(mid) / "mix.json")
    if not m:
        raise KeyError(mid)
    for k, v in DEFAULT.items():
        m.setdefault(k, v)
    return m


def save(m):
    mdir(m["id"]).mkdir(parents=True, exist_ok=True)
    store.write_json(mdir(m["id"]) / "mix.json", m)
    return m


def create(name, channel_id=None):
    m = {"id": store.new_id(), "name": name or "Музыкальный канал", "created": store.now(), "channel_id": channel_id,
         "tracks": [], "visuals": [], "videos": [], **DEFAULT}
    return save(m)


def list_all():
    out = []
    for d in sorted(MIXES.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        m = store.read_json(d / "mix.json")
        if m:
            out.append({"id": m["id"], "name": m["name"], "tracks": len(m["tracks"]),
                        "visuals": len(m["visuals"]), "videos": len(m["videos"])})
    return out


def add_track(m, path: Path, name=None, prompt=None):
    t = {"id": store.new_id(), "file": f"music/{path.name}", "name": name or path.stem,
         "dur": round(ff.duration(path), 1), "uses": 0, "prompt": prompt}
    try:
        t["scan"] = musicscan.quick(path)
    except Exception:
        t["scan"] = None
    m["tracks"].append(t)


JOURNAL = store.DATA / "music_journal.md"


def rate(m, tid, rate_, note=""):
    """Оценка трека 👍/👎 + что не так → в трек и в общий журнал (его читает Claude перед новой музыкой)."""
    t = next(x for x in m["tracks"] if x["id"] == tid)
    t["rate"], t["note"] = rate_, note.strip()
    if not t.get("scan"):  # старые/загруженные треки меряем при первой оценке
        try:
            t["scan"] = musicscan.quick(mdir(m["id"]) / t["file"])
        except Exception:
            t["scan"] = None
    import time
    lines = [f"## {time.strftime('%Y-%m-%d %H:%M')} · микс «{m['name']}» · {'👍' if rate_ > 0 else '👎'}",
             f"- трек: {t['name']}",
             f"- промпт: {t['prompt']}" if t.get("prompt") else "- промпт: — (трек загружен вручную)",
             f"- измерено: {musicscan.summary(t.get('scan'))}"]
    if m.get("ref_url"):
        lines.append(f"- референс: {m['ref_url']}")
    if t["note"]:
        lines.append(f"- что сказал пользователь: {t['note']}")
    head = "" if JOURNAL.exists() else ("# Журнал оценок музыки\n\nЧитать перед каждым промптом для Suno: "
                                        "👍 — повторять приёмы, 👎 — избегать.\n")
    with open(JOURNAL, "a", encoding="utf-8") as fh:
        fh.write(head + "\n" + "\n".join(lines) + "\n")
    return save(m)


def add_visual(m, path: Path):
    kind = "image" if path.suffix.lower() in IMG else "video"
    v = {"id": store.new_id(), "file": f"visuals/{path.name}", "kind": kind, "uses": 0}
    thumb = mdir(m["id"]) / "visuals" / f"{v['id']}_thumb.jpg"
    try:
        ff.thumb(mdir(m["id"]) / v["file"], thumb, t=1)
        v["thumb"] = f"visuals/{thumb.name}"
    except Exception:
        pass
    m["visuals"].append(v)


# ------------------------------------------------------------------ фоновые задачи

def job(mid):
    return _jobs.get(mid, {"running": False, "log": [], "error": None})


def start(mid, fn, *args):
    if job(mid)["running"]:
        raise RuntimeError("Уже идёт задача — подожди")
    st = _jobs[mid] = {"running": True, "log": [], "error": None}

    def go():
        try:
            fn(mid, *args, log=st["log"].append)
            st["log"].append("Готово ✅")
        except Exception as e:
            st["error"] = str(e)
        finally:
            st["running"] = False

    threading.Thread(target=go, daemon=True).start()
    return st


def gen_music(mid, n, prompt, log):
    s = store.get_settings()
    for k in range(n):
        log(f"Трек {k + 1}/{n}…")
        path = audio.suno_generate(prompt, True, s, mdir(mid) / "music", log=log)
        m = load(mid)
        add_track(m, path, f"{(prompt.split(',')[0])[:30]} #{len(m['tracks']) + 1}", prompt)
        save(m)


def gen_images(mid, n, prompt, log):
    s = store.get_settings()
    m = load(mid)
    for k in range(n):
        log(f"Картинка {k + 1}/{n}…")
        # уникальный хвост — чтобы каждая картинка была новой (и с новым именем файла)
        p = f"{prompt}. Variation {store.new_id()}: slightly different composition, angle and details."
        path = sources.google_image(p, m["format"], s, mdir(mid) / "visuals")
        m = load(mid)
        add_visual(m, path)
        save(m)


def ref_duration(mid, url, log):
    """Ссылка на канал-референс → медианная длина его роликов и их названия."""
    from .reference import _ydl
    url = url.strip()
    if "watch?v=" not in url and not url.rstrip("/").endswith(("/videos", "/streams")):
        url = url.rstrip("/") + "/videos"
    log("Читаю ролики референса…")
    with _ydl({"extract_flat": True, "playlistend": 30}) as y:
        info = y.extract_info(url, download=False)
    ents = [e for e in (info.get("entries") or [info]) if e]
    durs = [e["duration"] for e in ents if e.get("duration")]
    if not durs:
        raise RuntimeError("Не нашёл длительность роликов референса")
    m = load(mid)
    m["ref_url"] = url
    m["ref_titles"] = [e.get("title") for e in ents if e.get("title")][:30]
    m["ref_durations"] = durs
    m["target_min"] = round(statistics.median(durs) / 60)
    save(m)
    log(f"Роликов: {len(durs)}, медианная длина {m['target_min']} мин")


# ------------------------------------------------------------------ сборка

def _size(fmt):
    return (1080, 1920) if fmt == "9:16" else (1920, 1080)


def _clip(m, v, fades):
    """Картинка/видео → нормализованный клип (h264, 30 fps, без звука). Делается один раз, потом из кэша."""
    w, h = _size(m["format"])
    src = mdir(m["id"]) / v["file"]
    key = f"{v['id']}_{w}x{h}_{m['clip_len']}_{m['max_clip']}_{int(fades)}"
    out = mdir(m["id"]) / "clips" / f"{key}.mp4"
    if out.exists():
        return out, ff.duration(out)
    out.parent.mkdir(exist_ok=True)
    fps = 30
    if v["kind"] == "image":
        L = float(m["clip_len"])
        n = int(L * fps)
        if fades:  # медленный наезд в случайную сторону
            x, y = random.choice([("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"), ("0", "0"), ("iw-iw/zoom", "0"),
                                  ("0", "ih-ih/zoom"), ("iw-iw/zoom", "ih-ih/zoom")])
            vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
                  f"zoompan=z='min(zoom+{0.12 / n:.6f},1.12)':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={fps}")
        else:  # одна картинка на весь ролик — статично, без рывков на стыках
            vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps}"
        inp = ["-loop", 1, "-t", L, "-i", src]
    else:
        L = min(float(m["max_clip"]), ff.duration(src) or m["clip_len"])
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps}"
        inp = ["-t", L, "-i", src]
    if fades:
        vf += f",fade=t=in:d=0.6,fade=t=out:st={L - 0.6:.2f}:d=0.6"
    vf += ",format=yuv420p,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
    ff.run([*inp, "-vf", vf, "-an", "-t", L, "-c:v", "libx264", "-preset", "veryfast", "-crf", 20,
            "-g", fps * 2, "-r", fps, "-color_range", "tv", "-video_track_timescale", 15360, out])
    return out, ff.duration(out)


def _pick(items, n):
    """Сначала всё новое, затем реже всего использованное; порядок перемешан."""
    pool = items[:]
    random.shuffle(pool)
    pool.sort(key=lambda x: x["uses"])
    return pool[:n]


def _music_plan(m, total):
    tracks = [t for t in m["tracks"] if t["dur"] > 20]
    if not tracks:
        raise RuntimeError("В библиотеке нет музыки — сгенерируй или загрузи треки")
    blocks = [float(x) * 60 for x in str(m["block_min"]).replace(" ", "").split(",") if x and float(x) > 0]
    xf = float(m["crossfade"])
    order = _pick(tracks, len(tracks))
    plan, t, i = [], 0.0, 0
    while t < total:
        tr = order[i % len(order)]
        i += 1
        if i % len(order) == 0:  # круг пройден — новый порядок, но без повтора на стыке
            nxt = _pick(tracks, len(tracks))
            if len(nxt) > 1 and nxt[0]["id"] == tr["id"]:
                nxt.append(nxt.pop(0))
            order = nxt
            i = 0
        L = max(tr["dur"], random.choice(blocks)) if blocks else tr["dur"]
        L = min(L, total - t + xf)
        if L < 30 and plan:  # хвост — удлиняем предыдущий блок
            plan[-1]["len"] += max(0.0, L - 2 * xf)
            break
        plan.append({"track": tr, "start": t, "len": L})
        t += L - xf
    return plan


def _render_audio(m, plan, out, log):
    xf = float(m["crossfade"])
    args, parts = [], []
    for k, b in enumerate(plan):
        args += ["-stream_loop", -1, "-i", mdir(m["id"]) / b["track"]["file"]]
        # повтор трека — с короткой склейкой хвоста и начала, без щелчка
        parts.append(f"[{k}:a]atrim=0:{b['len']:.2f},asetpts=PTS-STARTPTS,"
                     f"aformat=sample_rates=44100:channel_layouts=stereo[a{k}]")
    cur = "a0"
    for k in range(1, len(plan)):
        parts.append(f"[{cur}][a{k}]acrossfade=d={xf}:c1=tri:c2=tri[x{k}]")
        cur = f"x{k}"
    parts.append(f"[{cur}]afade=t=in:d=2,loudnorm=I=-16:TP=-1.5[out]")
    log(f"Сводка музыки: {len(plan)} блоков…")
    ff.run([*args, "-filter_complex", ";".join(parts), "-map", "[out]", "-c:a", "aac", "-b:a", "192k", out])
    return ff.duration(out)


def _ts(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def build(mid, log):
    m = load(mid)
    total = float(m["target_min"]) * 60
    if not m["visuals"]:
        raise RuntimeError("В библиотеке нет картинок/клипов")
    d = mdir(mid)
    vid = store.new_id()
    out_dir = d / "out"
    out_dir.mkdir(exist_ok=True)

    plan = _music_plan(m, total)
    music = out_dir / f"{vid}_music.m4a"
    dur = _render_audio(m, plan, music, log)

    vis = _pick(m["visuals"], int(m["visuals_per_video"]))
    fades = len(vis) > 1
    clips = []
    for k, v in enumerate(vis):
        log(f"Клип {k + 1}/{len(vis)}")
        clips.append((v, *_clip(m, v, fades)))
    seq, t, last = [], 0.0, None
    while t < dur:
        batch = clips[:]
        random.shuffle(batch)
        if last and len(batch) > 1 and batch[0][0]["id"] == last:
            batch.append(batch.pop(0))
        for c in batch:
            seq.append(c[1])
            t += c[2]
            last = c[0]["id"]
            if t >= dur:
                break
    lst = out_dir / f"{vid}_list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in seq), encoding="utf-8")
    final = out_dir / f"{m['name']} {len(m['videos']) + 1}.mp4"
    log(f"Склейка {len(seq)} клипов + музыка ({_ts(dur)})…")
    ff.run(["-f", "concat", "-safe", 0, "-i", lst, "-i", music, "-map", "0:v", "-map", "1:a",
            "-c", "copy", "-t", f"{dur:.2f}", "-movflags", "+faststart", final])
    lst.unlink(missing_ok=True)
    music.unlink(missing_ok=True)

    new_t = sum(1 for t_ in {b["track"]["id"]: b["track"] for b in plan}.values() if t_["uses"] == 0)
    new_v = sum(1 for v in vis if v["uses"] == 0)
    used_t = {b["track"]["id"] for b in plan}
    used_v = {v["id"] for v in vis}
    m = load(mid)
    for t_ in m["tracks"]:
        t_["uses"] += t_["id"] in used_t
    for v in m["visuals"]:
        v["uses"] += v["id"] in used_v
    m["videos"].insert(0, {"id": vid, "file": f"out/{final.name}", "dur": round(dur), "created": store.now(),
                           "tracklist": "\n".join(f"{_ts(b['start'])} {b['track']['name']}" for b in plan),
                           "new_tracks": new_t, "tracks": len(used_t), "new_visuals": new_v, "visuals": len(vis)})
    save(m)
    store.export_to_channel(m.get("channel_id"), final, {f"{final.stem} треклист.txt": m["videos"][0]["tracklist"]}, log=log)
    log(f"Ролик: {final.name} · треков {len(used_t)} (новых {new_t}) · клипов {len(vis)} (новых {new_v})")
