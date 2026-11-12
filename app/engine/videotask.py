"""Разбор чужого ролика через чат с Claude.

Ссылка (или файл) → софт качает ролик, режет раскадровку, считает темп монтажа, достаёт текст
(субтитры YouTube или Whisper) → кладёт задачу data/inbox/v_<id>.md. Claude пишет разбор в
data/analyses/<id>/report.md и, если в задаче просили, сам делает похожий ролик.
"""
import collections
import re
import subprocess
import statistics
import threading
from pathlib import Path

from . import musicscan, ff, store
from .inbox import INBOX

AN = store.DATA / "analyses"
AN.mkdir(parents=True, exist_ok=True)
CUT_LIMIT = 600      # для очень длинных роликов (стримы/музыка > 1 ч) берём только первые 10 минут
FULL_LIMIT = 3600    # до часа — смотрим ролик целиком
SHEET = (6, 5)       # кадров в листе раскадровки (столбцы × строки)


def adir(aid):
    return AN / aid


def load(aid):
    a = store.read_json(adir(aid) / "meta.json")
    if not a:
        raise KeyError(aid)
    rep = adir(aid) / "report.md"
    if rep.exists():
        a["report"] = rep.read_text(encoding="utf-8-sig")
        if a["status"] == "waiting":
            a["status"] = "done"
    pm = _passport_mtime(aid)
    if (rep.exists() and not a.get("ref_notes")) or pm != a.get("passport_mtime"):
        try:
            to_ref(a)
        except Exception:
            pass
    return a


# Паспорт стиля: data/analyses/<id>/passport.json пишет Claude при разборе. Его style + gfx (тема графики)
# вливаются в стиль референса «🔎 …», и любой проект с этим reference_id собирается «как у них».
PASSPORT_EXTRA = {"gfx_theme", "ai_style", "photo_card_share", "grade"}


def _passport_mtime(aid):
    f = adir(aid) / "passport.json"
    return f.stat().st_mtime if f.exists() else None


def apply_passport(a, r):
    pp = store.read_json(adir(a["id"]) / "passport.json")
    if not pp:
        return False
    st = pp.get("style") or {}
    r["style"].update({k: v for k, v in st.items() if k in store.DEFAULT_STYLE or k in PASSPORT_EXTRA})
    if pp.get("gfx"):
        r["style"]["gfx_theme"] = pp["gfx"]
    r["passport"] = pp
    return True


def sync_ref(rid):
    """Влить свежие паспорта разборов в референс rid (разбор «an_<id>» или разбор канала с ref_for=rid)."""
    for m in AN.glob("*/meta.json"):
        a = store.read_json(m) or {}
        if a.get("id") and ("an_" + a["id"] == rid or a.get("ref_for") == rid):
            try:
                load(a["id"])
            except Exception:
                pass


def to_ref(a):
    """Разбор → референс (вкладка «Референсы»): кадры, темп, формат, текст разбора в заметках."""
    import os
    import shutil
    d = adir(a["id"])
    if a.get("ref_for"):  # разбор референс-канала: никакого второго «🔎 …» референса — паспорт идёт в сам референс
        try:
            orig = store.load_ref(a["ref_for"])
        except KeyError:
            orig = None
        if orig is not None:
            orig.setdefault("style", dict(store.DEFAULT_STYLE))
            if apply_passport(a, orig):
                orig["status"] = "ready"
            if a.get("report"):
                orig["notes"] = a["report"]
                a["ref_notes"] = True
            store.save_ref(orig)
            a["passport_mtime"] = _passport_mtime(a["id"])
            a["ref_id"] = orig["id"]
            save(a)
            return orig
    rid = "an_" + a["id"]
    rd = store.REFS / rid
    (rd / "frames").mkdir(parents=True, exist_ok=True)
    try:
        r = store.load_ref(rid)
    except KeyError:
        r = {"id": rid, "created": store.now(), "status": "ready", "analysis_id": a["id"]}
    for f in a.get("frames") or []:
        if not (rd / f).exists():
            shutil.copyfile(d / f, rd / f)
    if a.get("file") and not (rd / a["file"]).exists():
        try:
            os.link(d / a["file"], rd / a["file"])
        except Exception:
            pass
    info = ff.video_info(d / a["file"]) if a.get("file") and (d / a["file"]).exists() else {}
    e = a.get("edit") or {}
    vert = a.get("format") == "9:16"
    r.update({"name": r.get("name") or "🔎 " + a.get("title", ""), "url": a.get("url") or "",
              "file": a["file"] if (rd / a.get("file", "-")).exists() else "",
              "frames": a.get("frames") or [], "channel_title": a.get("channel") or "",
              "analysis": {"width": info.get("width"), "height": info.get("height"), "fps": info.get("fps"),
                           "duration": a.get("duration"), "format": a.get("format"), "cuts": e.get("cuts"),
                           "avg_shot": e.get("avg_shot"), "median_shot": e.get("median_shot"),
                           "cuts_per_min": e.get("cuts_per_min"), "has_audio": info.get("has_audio", True),
                           "mean_volume_db": e.get("mean_volume_db"), "videos": 1}})
    if "style" not in r:
        st = dict(store.DEFAULT_STYLE)
        med = e.get("median_shot") or 4
        st.update({"format": a.get("format") or "16:9", "shot_len": round(min(12.0, max(1.0, med)), 1),
                   "transition_dur": 0.25 if med < 2.5 else 0.6})
        tr = {k: v for k, v in (e.get("transitions") or {}).items() if k != "cut"}
        cuts = e.get("cuts") or 0
        if cuts and sum(tr.values()) < cuts * 0.15:   # почти всё резкие склейки
            st["transitions"] = False
        elif tr:  # самый частый плавный переход (точнее — глазами по transitions/ и в паспорте)
            st.update({"transitions": True, "transition": {"dissolve": "fade"}.get(max(tr, key=tr.get), max(tr, key=tr.get))})
        if vert:
            st.update({"sub_position": "center", "sub_words": 3})
        r["style"] = st
    if a.get("report"):
        r["notes"] = a["report"]
        a["ref_notes"] = True
    apply_passport(a, r)
    if a.get("ref_for"):  # разбор референс-канала: паспорт и заметки — в сам референс, им пользуются каналы/проекты
        try:
            orig = store.load_ref(a["ref_for"])
            orig.setdefault("style", dict(store.DEFAULT_STYLE))
            if apply_passport(a, orig) or a.get("report"):
                if a.get("report"):
                    orig["notes"] = a["report"]
                store.save_ref(orig)
        except KeyError:
            pass
    a["passport_mtime"] = _passport_mtime(a["id"])
    r.setdefault("notes", "")
    store.save_ref(r)
    a["ref_id"] = rid
    save(a)
    ch = store.get_channel(a.get("channel_id")) if a.get("channel_id") else None
    if ch and not ch.get("reference_id"):
        ch["reference_id"] = rid
        store.upsert_channel(ch)
    return r


def save(a):
    adir(a["id"]).mkdir(parents=True, exist_ok=True)
    store.write_json(adir(a["id"]) / "meta.json", {k: v for k, v in a.items() if k != "report"})
    return a


def list_all():
    out = []
    for d in sorted(AN.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            a = load(d.name)
            out.append({k: a.get(k) for k in ("id", "title", "status", "created", "note", "url", "duration")})
        except KeyError:
            pass
    return out


def create(url="", note="", upload: Path = None, channel_id="", ref_for=None):
    a = {"id": store.new_id(), "url": url.strip(), "note": note.strip(), "title": url.strip() or "файл",
         "status": "preparing", "created": store.now(), "log": [], "channel_id": channel_id or None}
    if ref_for:  # до старта потока подготовки, иначе он перезапишет meta.json без этого поля
        a["ref_for"] = ref_for
    save(a)
    if upload:
        dst = adir(a["id"]) / ("video" + upload.suffix.lower())
        upload.replace(dst)
        a["file"] = dst.name
        a["title"] = upload.stem
        save(a)
    threading.Thread(target=_prepare, args=(a["id"],), daemon=True).start()
    return a


def resume_stuck():
    """После перезапуска сервера поток подготовки умирает, задача висит в «preparing» и не доходит до чата — запускаем заново."""
    for d in AN.iterdir() if AN.exists() else []:
        try:
            if load(d.name).get("status") == "preparing":
                retry(d.name)
        except Exception:
            pass


def _log(a, m):
    a["log"].append(m)
    save(a)


def _download(a):
    from .reference import _ydl
    d = adir(a["id"])
    _log(a, "Читаю информацию о ролике…")
    opts = {"format": "best[height<=480][ext=mp4]/bv*[height<=480]+ba/best[height<=480]/best",
            "outtmpl": str(d / "video.%(ext)s"), "merge_output_format": "mp4"}
    with _ydl({"skip_download": True}) as y:
        info = y.extract_info(a["url"], download=False)
    dur = info.get("duration") or 0
    a.update(title=info.get("title") or a["title"], channel=info.get("channel") or info.get("uploader"),
             duration=dur, views=info.get("view_count"), likes=info.get("like_count"),
             upload_date=info.get("upload_date"), tags=(info.get("tags") or [])[:30],
             description=(info.get("description") or "")[:3000], channel_url=info.get("channel_url"),
             chapters=[{"t": c.get("start_time"), "title": c.get("title")} for c in info.get("chapters") or []])
    save(a)
    if dur > FULL_LIMIT:  # очень длинный (музыка/стрим) — хватит первых 10 минут
        from yt_dlp.utils import download_range_func
        opts["download_ranges"] = download_range_func(None, [(0, CUT_LIMIT)])
        _log(a, f"Ролик длинный ({dur // 60} мин) — качаю первые 10 минут для разбора")
    _log(a, "Скачиваю…")
    with _ydl(opts) as y:
        y.download([a["url"]])
    # субтитры отдельно и только язык оригинала: YouTube часто отвечает 429 — тогда просто Whisper
    lang = info.get("language") or "ru"
    try:
        with _ydl({"skip_download": True, "writeautomaticsub": True, "writesubtitles": True,
                   "subtitlesformat": "vtt", "subtitleslangs": [lang, f"{lang}-orig"],
                   "outtmpl": str(d / "video.%(ext)s")}) as y:
            y.download([a["url"]])
    except Exception:
        _log(a, "Субтитры YouTube не отдал — распознаю речь сам")
    f = next((p for p in d.glob("video.*") if p.suffix in (".mp4", ".webm", ".mkv")), None)
    if not f:
        raise RuntimeError("Не скачалось видео")
    a["file"] = f.name


def _ts(sec):
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def _vtt_text(path):
    """(сплошной текст, текст с метками [мм:сс])"""
    out, timed, last, cur = [], [], "", 0.0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"(\d+):(\d+):([\d.]+)\s*-->", line)
        if m:
            cur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
            continue
        if not line.strip() or line.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        t = re.sub(r"<[^>]+>", "", line).strip()
        if t and t != last:
            out.append(t)
            timed.append(f"[{_ts(cur)}] {t}")
            last = t
    return " ".join(out), "\n".join(timed)


def _transcript(a, src):
    d = adir(a["id"])
    # сначала оригинальная дорожка, потом ru/en, автоперевод — в последнюю очередь
    pref = ("orig", ".")
    subs = sorted(d.glob("video*.vtt"), key=lambda p: next((k for k, x in enumerate(pref) if x in p.name), 9))
    if subs:
        a["transcript_source"] = "субтитры YouTube " + subs[0].suffixes[0].strip(".")
        return _vtt_text(subs[0])
    if not ff.video_info(src)["has_audio"]:
        return "", ""
    _log(a, "Распознаю речь (Whisper)…")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "", ""
    wav = d / "audio.wav"
    ff.run(["-i", src, "-t", FULL_LIMIT, "-vn", "-ac", 1, "-ar", 16000, wav])
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segs, info = model.transcribe(str(wav), vad_filter=True)
    segs = list(segs)
    wav.unlink(missing_ok=True)
    a["transcript_source"] = f"Whisper ({info.language})"
    return (" ".join(x.text.strip() for x in segs),
            "\n".join(f"[{_ts(x.start)}] {x.text.strip()}" for x in segs))


def retry(aid):
    a = load(aid)
    a.update(status="preparing", error=None, log=[])
    save(a)
    threading.Thread(target=_prepare, args=(aid,), daemon=True).start()
    return a


def _prepare(aid):
    a = load(aid)
    d = adir(aid)
    try:
        if a.get("url") and not a.get("file"):
            _download(a)
        src = d / a["file"]
        info = ff.video_info(src)
        a["duration"] = a.get("duration") or round(info["duration"])
        a["format"] = "9:16" if info["height"] > info["width"] else "16:9"
        part = info["duration"] if info["duration"] <= FULL_LIMIT else min(info["duration"], CUT_LIMIT)

        _log(a, "Смотрю монтаж целиком (склейки и плавные переходы)…")
        shots, cuts = _shots(d, src, part)
        _log(a, "Разбираю переходы (склейка, растворение, затемнение)…")
        cuts = _transitions(d, src, cuts)
        kinds = {c["t"]: c["kind"] for c in cuts}
        for x in shots:
            if x.get("kind"):
                x["kind"] = kinds.get(x["t"], x["kind"])
        lens = [x["len"] for x in shots]
        a["edit"] = {"analyzed_sec": round(part), "cuts": len(cuts),
                     "hard_cuts": sum(1 for c in cuts if c["kind"] == "cut"),
                     "dissolves": sum(1 for c in cuts if c["kind"] == "dissolve"),
                     "transitions": dict(collections.Counter(c["kind"] for c in cuts)),
                     "median_shot": round(statistics.median(lens), 2) if lens else None,
                     "avg_shot": round(statistics.mean(lens), 2) if lens else None,
                     "cuts_per_min": round(len(cuts) / part * 60, 1) if part else 0,
                     "mean_volume_db": ff.loudness(src) if info["has_audio"] else None}
        (d / "shots.txt").write_text("\n".join(
            f"[{_ts(x['t'])}] план {x['len']:.1f} с" + (f" ← {x['kind']}" if x.get("kind") else "") for x in shots),
            encoding="utf-8")

        _log(a, "Режу полную раскадровку…")
        a["step"], a["sheets"] = _sheets(d, src, part)
        (d / "frames").mkdir(exist_ok=True)
        frames = []
        for k in range(16):
            t = part * (k + 0.5) / 16
            out = d / "frames" / f"f{k:02d}_{int(t)}s.jpg"
            try:
                ff.thumb(src, out, t=round(t, 2), width=480)
                frames.append(f"frames/{out.name}")
            except Exception:
                pass
        a["frames"] = frames
        if frames:  # обзор 4×4 для карточки в софте
            _sheet(d, frames)
            a["sheet"] = "sheet.jpg"

        text, timed = _transcript(a, src)
        (d / "transcript.txt").write_text(text, encoding="utf-8")
        (d / "transcript_timed.txt").write_text(timed, encoding="utf-8")
        a["transcript_words"] = len(text.split())
        if info["has_audio"]:
            _log(a, "Измеряю музыку (темп, тональность, звук)…")
            try:
                mres = musicscan.analyze(src, d, info["duration"])
                if mres:
                    a["music"] = {k: mres[k] for k in ("bpm", "key_main", "bright", "pulse_txt", "track_changes")}
                    if a["transcript_words"] > 30:  # речь мешает измерению — честно предупреждаем
                        with open(d / "music.txt", "a", encoding="utf-8") as fh:
                            fh.write("\n\n⚠ В ролике есть голос — тональность и яркость частично от речи, "
                                     "опирайся на окна без речи (сверь с transcript_timed.txt).")
            except Exception as e:
                _log(a, f"Музыку измерить не вышло: {e}")
        a["status"] = "waiting"
        save(a)
        to_ref(a)
        _task(a)
    except Exception as e:
        a["status"] = "error"
        a["error"] = str(e)
        save(a)


def _sheets(d, src, part):
    """Весь ролик листами 6×5: кадр каждые step секунд (не реже 2 с, не больше ~600 кадров)."""
    step = max(2.0, round(part / 600, 1))
    per = SHEET[0] * SHEET[1]
    sd = d / "sheets"
    sd.mkdir(exist_ok=True)
    for f in sd.glob("*.jpg"):
        f.unlink()
    ff.run(["-t", part, "-i", src, "-vf",
            f"fps=1/{step},scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:-1:-1,"
            f"tile={SHEET[0]}x{SHEET[1]}:padding=2", "-q:v", 4, sd / "s%02d.jpg"])
    out = []
    for k, f in enumerate(sorted(sd.glob("s*.jpg"))):
        t0 = k * per * step
        out.append({"file": f"sheets/{f.name}", "from": _ts(t0), "to": _ts(min(part, t0 + per * step))})
    return step, out


def _shots(d, src, part):
    """Планы с учётом плавных переходов: scene-score 2 раза в секунду (Ken Burns даёт ~0,
    любая смена картинки — всплеск). Резкий одиночный всплеск — склейка, размазанный — растворение."""
    ff.run(["-t", part, "-i", src, "-vf",
            "fps=2,scale=160:-2,select='gte(scene,0)',metadata=print:file=scores.txt",
            "-an", "-f", "null", "-"], cwd=d)
    pts, sc, t = [], [], None
    for line in (d / "scores.txt").read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m[1])
        m = re.search(r"scene_score=([\d.]+)", line)
        if m and t is not None:
            pts.append(t)
            sc.append(float(m[1]))
    (d / "scores.txt").unlink(missing_ok=True)
    cuts, k = [], 0
    while k < len(sc):
        if sc[k] < 0.03:
            k += 1
            continue
        j, total, peak = k, 0.0, k   # группа соседних ненулевых — один переход
        while j < len(sc) and sc[j] >= 0.03:
            total += sc[j]
            peak = j if sc[j] > sc[peak] else peak
            j += 1
        if total >= 0.15 and (not cuts or pts[peak] - cuts[-1]["t"] > 1.0):
            cuts.append({"t": pts[peak], "kind": "cut" if j - k == 1 and sc[peak] >= 0.4 else "dissolve"})
        k = j
    bounds = [0.0] + [c["t"] for c in cuts] + [part]
    kinds = [None] + [c["kind"] for c in cuts]
    shots = [{"t": x, "len": y - x, "kind": kd} for x, y, kd in zip(bounds, bounds[1:], kinds) if y - x > 0.2]
    return shots, cuts


# слова в названии/описании/тегах -> чем сделаны кадры (подсказка; решает просмотр листов)
KIND_WORDS = {
    "ai": ["ai generated", "ai-generated", "ai art", "ai video", "midjourney", "stable diffusion", "dall-e", "dalle",
           "sora", "veo", "kling", "runway", "flux", "leonardo", "nano banana", "made with ai", "ии", "нейросет",
           "сгенерир", "ki-generiert", "künstliche intelligenz", "généré par ia", "umělá inteligence"],
    "2d": ["2d", "animation", "animated", "анимац", "мульт", "cartoon", "motion graphics", "after effects",
           "whiteboard", "doodle", "illustrat", "иллюстрац", "animace", "animiert"],
    "3d": ["3d", "blender", "cinema 4d", "c4d", "unreal engine", "unity", "render", "cgi", "3д", "рендер"],
    "stock": ["stock footage", "stock video", "pexels", "pixabay", "storyblocks", "envato", "shutterstock",
              "стоки", "футаж"],
    "archive": ["archive", "archival", "historical footage", "архив", "хроник", "kinohnika", "archiv"],
}


def visual_hints(a):
    """{вид: [найденные слова]} по названию, описанию и тегам ролика."""
    txt = " ".join([a.get("title") or "", a.get("description") or "", " ".join(a.get("tags") or [])]).lower()
    out = {}
    for kind, words in KIND_WORDS.items():
        hit = [w for w in words if (re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", txt)
                                    if w.isascii() and len(w) <= 3 else w in txt)]
        if hit:
            out[kind] = hit
    return out


def _luma(src, t0, dur, fps=8):
    """Средняя яркость кадров (0–255) на отрезке."""
    raw = subprocess.run([ff.FFMPEG, "-v", "error", "-ss", f"{max(0, t0):.2f}", "-t", f"{dur:.2f}", "-i", str(src),
                          "-vf", f"fps={fps},scale=32:18,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, timeout=60).stdout
    n = len(raw) // (32 * 18)
    return [sum(raw[k * 576:(k + 1) * 576]) / 576 for k in range(n)]


def classify_transition(lum, kind):
    """cut / dissolve -> точнее: fadeblack, fadewhite (провал в чёрное/белое посередине)."""
    if len(lum) < 3:
        return kind
    mid = lum[1:-1]
    edge = (lum[0] + lum[-1]) / 2
    if min(mid) < 16 and edge > min(mid) + 25:
        return "fadeblack"
    if max(mid) > 235 and edge < max(mid) - 25:
        return "fadewhite"
    return kind


def _transitions(d, src, cuts, limit=40):
    """Тип каждого перехода + полоска кадров вокруг (transitions/tNN.jpg), чтобы увидеть, что за переход."""
    td = d / "transitions"
    td.mkdir(exist_ok=True)
    for f in td.glob("*.jpg"):
        f.unlink()
    for c in cuts:
        try:
            c["kind"] = classify_transition(_luma(src, c["t"] - 0.6, 1.2), c["kind"])
        except Exception:
            pass
    shown = [c for c in cuts if c["kind"] != "cut"][:limit] or cuts[:min(limit, 8)]
    for k, c in enumerate(shown):
        try:
            ff.run(["-ss", f"{max(0, c['t'] - 0.75):.2f}", "-t", "1.5", "-i", src, "-vf",
                    "fps=4,scale=240:-2,tile=6x1:padding=2", "-frames:v", 1, "-q:v", 4,
                    td / f"t{k:02d}_{int(c['t'])}s_{c['kind']}.jpg"])
            c["strip"] = f"transitions/t{k:02d}_{int(c['t'])}s_{c['kind']}.jpg"
        except Exception:
            pass
    return cuts


def _sheet(d, frames):
    args = []
    for f in frames:
        args += ["-i", d / f]
    n = len(frames)
    cols = 4
    rows = (n + cols - 1) // cols
    ins = "".join(f"[{k}:v]scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:-1:-1[s{k}];"
                  for k in range(n))
    layout = "|".join(f"{(k % cols) * 480}_{(k // cols) * 270}" for k in range(n))
    ff.run([*args, "-filter_complex", f"{ins}{''.join(f'[s{k}]' for k in range(n))}"
            f"xstack=inputs={n}:layout={layout}:fill=black", "-frames:v", 1, d / "sheet.jpg"])


def _task(a):
    d = adir(a["id"])
    e = a.get("edit") or {}
    lines = [
        f"<!-- video: {a['id']} -->",
        f"# Разбор видео: {a['title']}",
        "",
        f"- Ссылка: {a.get('url') or '— (файл)'}",
        f"- Канал: {a.get('channel') or '—'} · просмотров {a.get('views') or '—'} · лайков {a.get('likes') or '—'}",
        f"- Длина: {a.get('duration')} с, формат {a.get('format')}",
        f"- Монтаж (первые {e.get('analyzed_sec')} с): склеек {e.get('cuts')}, медианный план {e.get('median_shot')} с,"
        f" {e.get('cuts_per_min')} склеек/мин, громкость {e.get('mean_volume_db')} dB",
        f"  (резких склеек {e.get('hard_cuts')}, плавных переходов {e.get('dissolves')})",
        f"- Переходы по типам: {e.get('transitions') or '—'}; кадры вокруг переходов (СМОТРИ, что за переход —"
        f" слайд/шторка/зум/свайп/вспышка): {(d / 'transitions').as_posix()}/",
        f"- Подсказка, чем сделаны кадры (по названию/описанию/тегам): "
        + ("; ".join(f"{k}: {', '.join(v)}" for k, v in visual_hints(a).items()) or "слов нет — решай по листам"),
        f"- Планы по времени: {(d / 'shots.txt').as_posix()}",
        f"- ПОЛНАЯ раскадровка — кадр каждые {a.get('step')} с, СМОТРИ ВСЕ ЛИСТЫ ПО ПОРЯДКУ:",
        *[f"  - {(d / x['file']).as_posix()} ({x['from']}–{x['to']})" for x in a.get("sheets") or []],
        f"- Текст: {(d / 'transcript.txt').as_posix()} ({a.get('transcript_words')} слов, {a.get('transcript_source') or 'нет речи'})",
        f"- Текст с таймкодами (сверяй с листами): {(d / 'transcript_timed.txt').as_posix()}",
        *([f"- Музыка в цифрах (темп, тональность, частоты, смены треков): {(d / 'music.txt').as_posix()}",
           f"  куски по 20 с: {(d / 'music_clips').as_posix()}/",
           f"- Журнал оценок музыки (что пользователю нравилось/нет): {(store.DATA / 'music_journal.md').as_posix()}"]
          if (d / "music.txt").exists() else []),
        f"- Видео: {(d / a['file']).as_posix()}",
        f"- Ответ (разбор) писать в: {(d / 'report.md').as_posix()}",
    ]
    ch = store.get_channel(a.get("channel_id") or "")
    if ch:
        lines.append(f"- Делать для канала: {ch['name']} (channel_id: {ch['id']}) — при создании проекта передай channel_id")
    if a.get("tags"):
        lines.append("- Теги: " + ", ".join(a["tags"]))
    if a.get("chapters"):
        lines.append("- Главы: " + " | ".join(f"{c['t']:.0f}s {c['title']}" for c in a["chapters"]))
    lines += ["", "## Что просит пользователь", "", a.get("note") or "— только разбор", "",
              "## Описание ролика", "", a.get("description") or "—"]
    INBOX.mkdir(parents=True, exist_ok=True)
    (INBOX / f"v_{a['id']}.md").write_text("\n".join(lines), encoding="utf-8")
