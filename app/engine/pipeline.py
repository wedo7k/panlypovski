"""Конвейер: сценарий → сцены → медиа → озвучка → монтаж."""
import math
import os
import hashlib
import json
import random
import re
import zlib
import shutil
import threading
import uuid
import traceback
from pathlib import Path

from . import youtube, yt_pick
from . import audio, ff, gfx, llm, sources, store

# ---------------------------------------------------------------- сценарий

META_KEYS = {
    "title": ("TITLE", "НАЗВАНИЕ"),
    "description": ("DESCRIPTION", "ОПИСАНИЕ"),
    "tags": ("TAGS", "ТЕГИ"),
    "thumb": ("THUMB", "ПРЕВЬЮ"),
}
# настройки задачи — можно писать прямо в .md файле
OPT_KEYS = {
    "format": ("FORMAT", "ФОРМАТ"),
    "music": ("MUSIC", "МУЗЫКА"),
    "music_mode": ("MUSIC_MODE", "РЕЖИМ_МУЗЫКИ"),
    "music_file": ("MUSIC_FILE", "ФАЙЛ_МУЗЫКИ"),
    "voice": ("VOICE", "ГОЛОС"),
    "voice_name": ("VOICE_NAME", "ИМЯ_ГОЛОСА"),
    "lang": ("LANG", "ЯЗЫК"),
    "channel": ("CHANNEL", "КАНАЛ"),
    "reference": ("REFERENCE", "РЕФЕРЕНС"),
    "name": ("PROJECT", "ПРОЕКТ"),
}


def _meta_key(line):
    m = re.match(r"^\s*[-*_]*\s*([A-Za-zА-Яа-яЁё_]+)\s*[*_]*\s*:\s*[*_]*\s*(.*)$", line)
    if not m:
        return None, None
    word = m.group(1).upper()
    for k, names in {**META_KEYS, **{"opt:" + a: b for a, b in OPT_KEYS.items()}}.items():
        if word in names:
            return k, m.group(2).strip().strip("*_").strip()
    return None, None


def parse_script(text: str):
    """Формат сценария (его пишет Claude в чате):

    TITLE: название ролика
    DESCRIPTION: описание (можно на несколько строк до пустой строки)
    TAGS: тег1, тег2
    THUMB: промпт превью для Nano Banana (можно несколько строк THUMB:)

    ## Плашка сцены (необязательно)
    > поисковый запрос на английском для стоков (необязательно, можно несколько через ;)
    ! промпт для генерации картинки (необязательно)
    Текст сцены — это озвучка и субтитры.

    (пустая строка — новая сцена)
    """
    meta = {"title": "", "description": "", "tags": "", "thumb_prompts": [], "opts": {}}
    scenes, block = [], []
    blocks = []
    cur_meta = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        k, v = _meta_key(line)
        if k == "title" and block:  # «TITLE: …» внутри сцены — графика-титр, а не название ролика
            k = None
        if k and k.startswith("opt:"):
            meta["opts"][k[4:]] = v
            cur_meta = None
            continue
        if k:
            cur_meta = k
            if k == "thumb":
                meta["thumb_prompts"].append(v)
            else:
                meta[k] = v
            continue
        if not line.strip():
            cur_meta = None
            if block:
                blocks.append(block)
                block = []
            continue
        if cur_meta == "description":
            meta["description"] += "\n" + line
            continue
        if cur_meta == "thumb":
            meta["thumb_prompts"][-1] += " " + line.strip()
            continue
        if re.match(r"^#\s", line.strip()) or line.strip().startswith("```"):
            continue  # заголовки "# ..." и ``` в .md — пропускаем
        if line.strip() in ("---", "***"):
            if block:
                blocks.append(block)
                block = []
            continue
        block.append(line)
    if block:
        blocks.append(block)

    for b in blocks:
        sc = {"id": store.new_id(), "title": "", "queries": [], "ai_prompt": "", "text": "", "shots": []}
        body = []
        for line in b:
            s = line.strip()
            if s.startswith("##"):
                sc["title"] = s.lstrip("#").strip()
            elif s.startswith(">"):
                sc["queries"] += [q.strip() for q in s[1:].split(";") if q.strip()]
            elif s.startswith("!"):
                sc["ai_prompt"] = s[1:].strip()
            elif re.match(r"(%s)\s*:" % "|".join(gfx.KINDS), s, re.I):
                g = gfx.parse_line(s)
                if g:
                    sc["graphic"] = g
            elif re.match(r"(%s)\s*:" % "|".join(gfx.OVER_KINDS), s):  # только капсом: «Note:» в озвучке — не плашка
                o = gfx.parse_overlay(s)
                if o:
                    sc.setdefault("overlays", []).append(o)
            else:
                body.append(s)
        sc["text"] = " ".join(body).strip()
        if sc["text"] or sc["title"] or sc["queries"] or sc.get("graphic"):
            scenes.append(sc)
    return meta, scenes


def is_structured(text):
    """Сценарий в «нашем» формате (есть строки > запрос или ! ИИ-промпт)?"""
    return bool(re.search(r"^\s*[>!]\s*\S", text, re.M))


def smart_parse(text, p=None, log=print):
    """Любой сценарий → (meta, scenes). Наш формат — разбираем строго; свободный — через ИИ."""
    meta, scenes = parse_script(text)
    if is_structured(text) and scenes:
        log(f"Сценарий в готовом формате: сцен {len(scenes)}")
        return meta, scenes
    settings = store.get_settings()
    ref = None
    if p and p.get("reference_id"):
        try:
            ref = store.load_ref(p["reference_id"])
        except KeyError:
            pass
    planned = None
    try:
        planned = llm.plan(text, settings, (p or {}).get("style"), ref, log=log)
    except Exception as e:
        log(f"ИИ-разбор не сработал ({e}) — разбираю сам по абзацам")
    if planned:
        m2, raw = planned
        m2["opts"] = {**m2["opts"], **meta["opts"]}  # явные строки MUSIC:/FORMAT: в тексте главнее
        for k in ("title", "description", "tags"):
            if meta[k]:
                m2[k] = meta[k]
        if meta["thumb_prompts"]:
            m2["thumb_prompts"] = meta["thumb_prompts"]
        meta = m2
    else:
        if not llm.available(settings):
            log("Нет ключа Google/Claude для ИИ-разбора — режу сценарий по абзацам")
        body = "\n\n".join(" ".join(s["text"] for s in [sc]) for sc in scenes) or text
        raw = llm.heuristic(body)
    scenes = [{"id": store.new_id(), "title": (r.get("title") or "").strip(), "text": (r.get("text") or "").strip(),
               "queries": [q for q in (r.get("queries") or []) if q], "ai_prompt": r.get("ai_prompt") or "",
               "shots": []} for r in raw if (r.get("text") or "").strip()]
    log(f"Разобрано сцен: {len(scenes)}")
    return meta, scenes


def parse_step(p, log, j):
    """Шаг «разбор»: перечитывает сценарий, если он поменялся с прошлого разбора."""
    if not p.get("script", "").strip():
        if p["scenes"]:
            return
        raise RuntimeError("Сценарий пустой — вставь текст во вкладке «Сценарий»")
    if p.get("parsed_script") == p["script"] + "|" + (p.get("music_wish") or "") and p["scenes"]:
        log("Сценарий уже разобран")
        return
    text = p["script"]
    if p.get("music_wish"):
        text = f"[Пожелание к музыке: {p['music_wish']}]\n\n" + text
    meta, scenes = smart_parse(text, p, log)
    if not scenes:
        raise RuntimeError("Не получилось выделить ни одной сцены")
    apply_task(p, meta, scenes, p["script"])
    p["parsed_script"] = p["script"] + "|" + (p.get("music_wish") or "")
    if p.get("music_wish") and not meta["opts"].get("music"):
        p["music_prompt"] = p["music_wish"]
    store.save_project(p)


def auto_query(text):
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text)
    words.sort(key=len, reverse=True)
    return " ".join(words[:3]) or "nature"


def _keep_shots(old, new):
    """Повторный разбор: у сцены с тем же текстом остаются id, кадры и озвучка, ручной подбор не теряется."""
    norm = lambda t: " ".join(str(t or "").split()).lower()
    pool = {}
    for sc in old:
        pool.setdefault(norm(sc.get("text")), []).append(sc)
    for sc in new:
        same = pool.get(norm(sc.get("text")))
        if not same or not norm(sc.get("text")):
            continue
        o = same.pop(0)
        sc["id"] = o.get("id", sc.get("id"))
        same_gfx = o.get("graphic") == sc.get("graphic")
        sc["shots"] = [s for s in o.get("shots") or []
                       if same_gfx or (s.get("media") or {}).get("type") != "graphic"]  # новую вставку сделает add_graphic
        for k in ("voice", "voice_dur", "plate_at", "plate_dur"):
            if k in o:
                sc[k] = o[k]
    return new


def apply_task(p, meta, scenes, script_text):
    """Применяет разобранную задачу к проекту: сцены, YouTube-данные, настройки."""
    opts = meta.pop("opts", {}) or {}
    p["script"] = script_text
    for k, v in meta.items():
        if v:
            p["meta"][k] = v
    p["scenes"] = _keep_shots(p.get("scenes") or [], scenes)
    p["parsed_script"] = script_text
    p["output"] = None
    st = p["style"]
    if opts.get("format") in ("16:9", "9:16"):
        st["format"] = opts["format"]
    voice = (opts.get("voice") or "").lower()
    voice_map = {"нет": "none", "none": "none", "edge": "edge", "local": "local", "локальный": "local",
                 "elevenlabs": "elevenlabs", "eleven": "elevenlabs", "lmnt": "lmnt"}
    if voice in voice_map:
        st["voice"] = voice_map[voice]
    if opts.get("voice_name"):
        st["edge_voice"] = st["voice_id"] = opts["voice_name"]
    if opts.get("lang"):
        st["lang"] = opts["lang"]
    if opts.get("music"):
        p["music_prompt"] = opts["music"]
    mode = (opts.get("music_mode") or "").lower()
    if mode:
        p["music_mode"] = ("none" if mode in ("нет", "none", "без музыки", "off") else
                           "song" if mode in ("song", "песня") else "background")
        if p["music_mode"] == "none":
            p["music_prompt"] = ""
    if opts.get("name"):
        p["name"] = opts["name"]
    return opts


def project_from_task(text, channel=None, reference_id=None, name=None):
    """Создаёт проект из файла задачи: канал → референс (стиль) → сценарий/настройки."""
    meta, _ = parse_script(text)
    opts = meta.get("opts", {})
    ch = store.find_channel(channel or opts.get("channel") or "")
    if not ch and (channel or opts.get("channel")):
        ch = store.upsert_channel({"id": store.new_id(), "name": channel or opts["channel"],
                                   "reference_id": None, "created": store.now()})
    p = store.create_project(name or opts.get("name") or meta.get("title") or "Ролик",
                             opts.get("format") or "16:9", ch["id"] if ch else None)
    rid = reference_id or opts.get("reference") or (ch or {}).get("reference_id")
    if rid:
        try:
            r = store.load_ref(rid)
        except KeyError:
            r = next((x for x in store.list_refs() if x["name"].lower() == str(rid).lower()), None)
        if r:
            p["reference_id"] = r["id"]
            p["style"].update(r.get("style") or {})
    p["script"] = text
    if opts.get("format") in ("16:9", "9:16"):
        p["style"]["format"] = opts["format"]
    return store.save_project(p)  # сам разбор — шаг "parse" конвейера


# ---------------------------------------------------------------- задачи

JOBS = {}  # pid -> {"running", "step", "progress", "log", "error", "stages"}
STEP_NAMES = {"parse": "Разбор сценария", "voice": "Озвучка", "media": "Фото и видео", "music": "Музыка",
              "render": "Монтаж"}


class Cancelled(Exception):
    pass


def stop(pid):
    j = job(pid)
    if j["running"]:
        j["cancel"] = True
        if j.get("ev"):
            j["ev"].set()  # сразу прерывает опрос ИИ-генератора и ожидание сцен
        for t in {j.get("tid"), *(j.get("tids") or ())}:
            if t:
                ff.kill_thread(t)
    return j


def job(pid):
    return JOBS.setdefault(pid, {"running": False, "step": "", "progress": 0, "log": [], "error": None})


def is_running(pid):
    return job(pid)["running"]


def _music_off(p):
    return (p.get("music_mode") or (p.get("music") or {}).get("mode")) == "none"


def start(pid, steps):
    j = job(pid)
    if j["running"]:
        raise RuntimeError("Уже идёт задача по этому проекту")
    j.update(running=True, step="", progress=0, log=[], error=None, cancel=False, ev=threading.Event(), tids=set(),
             stages=[{"key": s, "name": STEP_NAMES.get(s, s), "status": "wait", "detail": ""} for s in steps])
    try:
        if _music_off(store.load_project(pid)):
            j["stages"] = [s for s in j["stages"] if s["key"] != "music"]
    except Exception:
        pass
    threading.Thread(target=_run, args=(pid, steps), daemon=True).start()


KEEP_STYLE = ("format", "lang", "voice", "edge_voice", "voice_id")  # своё у проекта, референс не перебивает


def ref_style(p, ref):
    """Стиль проекта = только стиль референса (паспорт), но язык/голос/формат — проекта."""
    keep = {k: p["style"][k] for k in KEEP_STYLE if p.get("style", {}).get(k) is not None}
    if p.get("style", {}).get("plate_color_own"):  # цвет плашек выбран вручную — паспорт его не перебивает
        keep.update(plate_color=p["style"]["plate_color"], plate_color_own=p["style"]["plate_color_own"])
    p["style"] = {**store.DEFAULT_STYLE, **(ref.get("style") or {}), **keep}
    p["ref_sig"] = hashlib.md5(json.dumps(ref.get("style") or {}, sort_keys=True).encode()).hexdigest()
    refresh_graphics(p)
    return p


def sync_ref_style(p, log=print):
    """Паспорт референса могли дописать после создания проекта — подтянуть его, иначе кадры пойдут со стоков/YouTube."""
    rid = p.get("reference_id")
    if not rid:
        return p
    from . import videotask
    videotask.sync_ref(rid)
    try:
        ref = store.load_ref(rid)
    except KeyError:
        return p
    sig = hashlib.md5(json.dumps(ref.get("style") or {}, sort_keys=True).encode()).hexdigest()
    if p.get("ref_sig") != sig:
        ref_style(p, ref)
        store.save_project(p)
        st = p["style"]
        log(f"Стиль взят из паспорта референса: ИИ {st.get('ai_share')}, YouTube {st.get('yt_share')}, {st.get('media_pref')}")
    return p


def _run(pid, steps, echo=None):
    j = job(pid)
    j["running"] = True
    j["tid"] = threading.get_ident()
    ev = j.get("ev")
    if ev is None or ev.is_set():  # запуск мимо start()
        ev = j["ev"] = threading.Event()
    j.setdefault("tids", set())
    j.setdefault("stages", [{"key": s, "name": STEP_NAMES.get(s, s), "status": "wait", "detail": ""} for s in steps])
    cur = {}

    def log(msg):
        if ev.is_set() or j.get("cancel"):  # ev свой у каждого запуска: потоки прошлой сборки не оживут
            raise Cancelled()
        if cur and not str(msg).startswith("==="):
            cur["detail"] = str(msg)
        line = f"[{store.now()[11:]}] {msg}"
        if echo:
            echo(line)
        j["log"].append(line)
        j["log"] = j["log"][-300:]

    try:
        p = sync_ref_style(store.load_project(pid), log)
        names = {"parse": "РАЗБОР СЦЕНАРИЯ", "voice": "ОЗВУЧКА", "media": "ВИДЕО/КАРТИНКИ", "music": "МУЗЫКА", "render": "СБОРКА"}
        for i, step in enumerate(steps, 1):
            if step == "music" and _music_off(p):
                # музыку не выбирали — этап не показываем вовсе
                p["music"] = None
                j["stages"] = [s for s in j["stages"] if s["key"] != "music"]
                continue
            log(f"=== Шаг {i}/{len(steps)}: {names.get(step, step)} ===")
            j["step"] = step
            j["progress"] = 0
            st =next((s for s in j["stages"] if s["key"] == step), None)
            if st:
                cur = st
                st["status"] = "run"
            {"parse": parse_step, "media": fetch_media, "voice": make_voice, "music": make_music, "render": render}[step](p, log, j)
            store.save_project(p)
            if st:
                st["status"] = "done"
            if j.get("cancel"):
                raise Cancelled()
        j["step"] = "готово"
        j["progress"] = 100
        log("Готово ✅")
    except Exception as e:
        stopped = j.get("cancel") or ev.is_set()
        j["error"] = "Остановлено" if stopped else str(e)
        for s in j.get("stages") or []:
            if s["status"] == "run":
                s["status"] = "stop" if stopped else "err"
        j["cancel"] = False
        j["log"].append(f"[{store.now()[11:]}] " + ("⏹ Остановлено пользователем" if stopped else "ОШИБКА: " + str(e)))
        if not stopped:
            traceback.print_exc()
    finally:
        j["running"] = False


# ---------------------------------------------------------------- длительности

def scene_duration(sc, style):
    if sc.get("voice_dur"):
        # + переход: следующая сцена наезжает на эту на transition_dur, голоса не должны перекрываться
        td = float(style.get("transition_dur") or 0) if style.get("transitions") else 0.0
        return sc["voice_dur"] + 0.08 + td  # реплики идут подряд, без пауз
    if sc.get("fixed_dur"):
        return sc["fixed_dur"]
    words = len(sc.get("text", "").split())
    return max(2.5, words / 2.6) if words else max(2.5, style["shot_len"])


def plan_shots(sc, style):
    """Сколько кадров нужно сцене и какой длины — по средней длине кадра из референса."""
    total = scene_duration(sc, style)
    n = max(1, round(total / max(1.0, style["shot_len"])), math.ceil(total / MAX_SHOT))
    return n, total


# Один кадр без плашки на экране не дольше 4 с — иначе зритель залипает на одной картинке
MAX_SHOT = 4.0


def _clone(src, pos):
    """Копия кадра, когда своих кадров не хватает: видео — следующий кусок клипа, фото — другое движение камеры."""
    c = dict(src, id=store.new_id(), locked=False, disabled=False)
    if src["media"]["type"] == "video":
        c["trim_in"] = round(float(src.get("trim_in", 0)) + MAX_SHOT * (pos + 1), 2)
    return c


MIN_REST = 1.5  # меньше этого остатка после вставки фото не показываем: мелькнёт


def apply_durations(sc, style):
    """Растягивает длительности кадров сцены так, чтобы в сумме совпало с озвучкой."""
    shots = [s for s in sc["shots"] if not s.get("disabled")]
    if not shots:
        return
    total = scene_duration(sc, style)
    free = [s for s in shots if not s.get("locked")]
    if not free:  # всё закреплено вручную: сжимаем/растягиваем пропорционально, иначе звук уедет
        k = total / (sum(s["dur"] for s in shots) or 1)
        for s in shots:
            s["dur"] = round(s["dur"] * k, 2)
        shots[-1]["dur"] = round(total - sum(s["dur"] for s in shots[:-1]), 2)
        return
    for g in shots:  # вставка не длиннее голоса: сцена всегда = озвучке, без тишины
        if g.get("media", {}).get("type") == "graphic":
            imgs = [s["media"]["path"] for s in sc["shots"] if s.get("media", {}).get("type") == "image"]  # и выключенные: фон под графикой
            imgs += [s.get("still") for s in sc["shots"] if s.get("media", {}).get("type") == "video" and s.get("still")]
            if not g.get("bg"):  # фон вставки — первое фото сцены (если вставку сделали раньше, чем нашлись фото)
                g["bg"] = imgs[0] if imgs else None
            g["bgs"] = imgs[:3]  # фото для карточек (SPLIT и т. п.)
            kind = ((g.get("graphic") or {}).get("type") or "").lower()
            free = [s for s in free if s is not g]
            want = gfx.DUR.get(kind, 4.0)  # вставка живёт свою длину, дальше идут фото сцены
            if not free or total - want < MIN_REST:  # фото нет или им не осталось места — вставка на всю сцену
                g["dur"] = round(total, 2)
                for s in free:
                    s["disabled"] = True
                return
            g["dur"] = round(want, 2)
            while len(free) > 1 and total - want < MIN_REST * len(free):
                free.pop()["disabled"] = True
    shots = [s for s in shots if not s.get("disabled")]
    locked = sum(s["dur"] for s in shots if s.get("locked"))
    pool = [s for s in free if s["media"]["type"] in ("video", "image")]
    if pool and not sc.get("overlays"):  # кадр с плашкой можно держать дольше
        k = 0
        while (total - locked) / len(free) > MAX_SHOT:
            c = _clone(pool[k % len(pool)], k // len(pool))
            sc["shots"].insert(sc["shots"].index(free[-1]) + 1, c)
            free.append(c)
            k += 1
    if free:
        each = max(0.8, (total - locked) / len(free))
        for s in free:
            s["dur"] = round(each, 2)


# ---------------------------------------------------------------- медиа

def _kinds(style):
    pref = style.get("media_pref", "video")
    return {"video": ["video", "image"], "image": ["image", "video"]}.get(pref, ["video", "image"])


def find_candidates(query, style, settings):
    out, errs = [], []
    only = None
    if sources.is_name(query):  # люди, бренды, места: Pixabay ищет по словам (Scottie Pippen -> терьеры)
        res, errs = sources.search(query, "image", style["format"], settings, only=["wikimedia"])
        if res:
            return res, errs
        only = [s for s in settings.get("sources") or [] if s not in ("pixabay", "wikimedia")]
        if not only:
            return [], errs
    for kind in _kinds(style):
        res, e = sources.search(query, kind, style["format"], settings, only=only)
        out += res
        errs += e
        if len(out) >= 6:
            break
    return out, errs


# Нейтральный запас, если в паспорте референса и в стиле проекта нет ai_style (своё — поле «Стиль ИИ-картинок»)
AI_STYLE = "photorealistic documentary photograph, natural light, era-accurate details, cinematic, no text"
AI_FRAMING = ["wide establishing shot", "medium shot", "close-up detail", "atmospheric scene from behind"]


MEDIA_THREADS = 8   # сцен одновременно; внутри сцены стоки и YouTube ищутся параллельно


def fetch_media(p, log, j):
    """Подбор кадров: несколько сцен сразу, в каждой стоки + YouTube одновременно, пока рисуются ИИ-кадры.
    Видео (YouTube и стоки) перед вставкой смотрит Gemini (check.fits), если есть google_key;
    в конце — листы проверки data/projects/<id>/check/sNN.jpg."""
    from concurrent.futures import ThreadPoolExecutor
    settings = store.get_settings()
    style = p["style"]
    lock = threading.RLock()
    used = {s["media"]["url"] for sc in p["scenes"] for s in sc["shots"] if s.get("media")}
    ctx = {"settings": settings, "style": style, "d": store.project_dir(p["id"]) / "media", "lock": lock,
           "used": used, "yt_used": {u.split(":")[1] for u in used if u.startswith("yt:")},
           "ai_off": {}, "yt_off": False, "yt_miss": 0, "picks": yt_pick.load_picks(p["id"]), "done": 0}
    if not settings.get("google_key"):
        log("Проверка кадров глазами Gemini выключена (нет Google API ключа) — после подбора смотри листы check/")
    n = len(p["scenes"]) or 1
    ev = j.get("ev") or threading.Event()
    ctx["ev"] = ev
    if float(style.get("ai_share", 0)) > 0 and not style.get("ai_style"):
        log("⚠ В паспорте референса нет стиля ИИ-картинок — беру нейтральный. Свой: вкладка «Стиль» → «Стиль ИИ-картинок»")
    pool = ThreadPoolExecutor(MEDIA_THREADS)
    try:
        futs = [pool.submit(_scene_media, p, i, sc, ctx, log) for i, sc in enumerate(p["scenes"])]
        for i, f in enumerate(futs):
            while not f.done():  # не ждём зависший поток: «Стоп» срабатывает сразу
                if ev.wait(0.2):
                    raise Cancelled()
            if ev.is_set():
                raise Cancelled()
            try:
                f.result()
            except Cancelled:
                raise
            except Exception as e:
                log(f"Сцена {i+1}: подбор упал — {e}")
            with lock:
                ctx["done"] += 1
                j["progress"] = int(100 * ctx["done"] / n)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    try:
        from . import check
        pages = check.sheets(p["id"])
        log(f"Листы проверки кадров: {len(pages)} шт. в data/projects/{p['id']}/check/")
    except Exception as e:
        log(f"Листы проверки не нарисовались — {e}")


def _scene_media(p, i, sc, ctx, log):
    from concurrent.futures import ThreadPoolExecutor
    from . import check
    settings, style, d, lock, used = ctx["settings"], ctx["style"], ctx["d"], ctx["lock"], ctx["used"]
    ev = ctx.get("ev") or threading.Event()
    sources.CANCEL.event = ev
    with lock:
        need, total = plan_shots(sc, style)
        add_graphic(p, sc)
        have = [s for s in sc["shots"] if s.get("media")]
        g = next((s for s in have if s["media"]["type"] == "graphic"), None)
        if g:  # вставка занимает свою длину, на остаток сцены нужны фото
            rest = total - gfx.DUR.get(((g.get("graphic") or {}).get("type") or "").lower(), 4.0)
            if rest >= MIN_REST:
                need = 1 + max(1, round(rest / max(1.0, style["shot_len"])), math.ceil(rest / MAX_SHOT))
        if len(have) >= need:
            apply_durations(sc, style)
            redraw_graphic(p, sc)
            return
    share = float(style.get("ai_share", 0.6))
    only_ai = share >= 1.0 or bool(sc.get("ai_prompt") and not sc["queries"])  # рисованный референс или сцена только с «!»
    queries = sc["queries"] or [auto_query(sc["text"])]
    yt_len = float(style.get("yt_len", 4))

    def verify(path, kind="video", q=None):
        return check.fits(path, kind, sc["text"], q or queries[0], settings)

    def stock_search():
        stock = []
        if only_ai:
            return stock
        for q in queries:
            res, errs = find_candidates(q, style, settings)
            for e in errs:
                log("  источник: " + e)
            stock += [dict(c, q=q) for c in res if c["url"] not in used]
            if len(stock) >= need + 2:
                break
        return stock

    picks = ctx["picks"].get(str(i)) or []
    n_yt = need - len(have)
    n_yt = min(n_yt, max(1, round(n_yt * float(style.get("yt_share", 0.3))))) if float(style.get("yt_share", 0.3)) > 0 and not only_ai else 0

    def yt_search():
        yt = []
        for vid, start in picks:  # куски, выбранные глазами (yt_pick/picks.json)
            try:
                path = youtube.cut(vid, int(start), yt_len, d)
                yt.append({"type": "video", "url": f"yt:{vid}:{start}", "source": "youtube", "local": path, "checked": True})
                log(f"Сцена {i+1}: кусок YouTube {vid} @{start}s")
            except Exception as e:
                log(f"Сцена {i+1}: кусок YouTube {vid} @{start}s не вырезался - {str(e)[:150]}")
        if picks or not n_yt or ctx["yt_off"]:
            return yt
        log(f"Сцена {i+1}: ищу кадры на YouTube (параллельно со стоками)…")
        yt = youtube.clips(queries[0], sc["text"], n_yt, settings, d, log, used=ctx["yt_used"], length=yt_len,
                           per_video=1, check=verify)
        with lock:
            ctx["yt_miss"] = 0 if yt else ctx["yt_miss"] + 1
            if ctx["yt_miss"] >= 3 and not ctx["yt_off"]:
                ctx["yt_off"] = True
                log("YouTube не отдаёт куски 3 сцены подряд — дальше без YouTube")
        return [dict(c, checked=True) for c in yt]

    base = sc.get("ai_prompt") or (queries[0] + ", " + (style.get("ai_style") or AI_STYLE))
    drawn = [0]

    def draw(n):
        """Нарисовать n ИИ-кадров (при лимите генератора — сколько вышло)."""
        res = []
        for _ in range(0 if ctx["ai_off"].get("why") or share <= 0 else n):
            k = drawn[0]
            drawn[0] += 1
            prompt = base + ", " + AI_FRAMING[k % len(AI_FRAMING)]
            log(f"Сцена {i+1}: рисую ИИ-кадр {k+1}…")
            try:
                if style.get("ai_kind") == "video" and settings.get("glabs_key"):
                    path = sources.glabs_video(prompt + (f" (variant {k})" if k else ""), style["format"], settings, d)
                    res.append({"type": "video", "url": "ai:" + path.name, "source": "ai", "local": path, "checked": True})
                    continue
                path = sources.ai_image(prompt, style["format"], settings, d, seed=k, log=log)
                res.append({"type": "image", "url": "ai:" + path.name, "source": "ai", "local": path, "checked": True})
            except Exception as e:
                log(f"Сцена {i+1}: ИИ-кадр не получился — {sources._why(e)}")
                if any(x in str(e) for x in ("FREE_LIMIT", "402", "429", "NO_KEY")) or "⛔" in sources._why(e):
                    with lock:
                        if not ctx["ai_off"].get("why"):
                            ctx["ai_off"]["why"] = "лимит бесплатного генератора"
                            log("ИИ-кадры отключены до конца сборки: добавь Google API ключ (Nano Banana) в «Настройки и API»")
                    break
        return res

    with ThreadPoolExecutor(2) as inner:
        f_stock, f_yt = inner.submit(stock_search), inner.submit(yt_search)
        stock = f_stock.result()
        yt = f_yt.result()
    # ИИ только там, где не хватило настоящих кадров (ai_share 1.0 / сцена с «!» — все кадры ИИ)
    ai = draw(max(0, need - len(have) - len(stock) - len(yt)))
    # чередуем: архив, YouTube, ИИ, архив…
    cands = []
    while stock or ai or yt:
        if stock:
            cands.append(stock.pop(0))
        if yt:
            cands.append(yt.pop(0))
        if ai:
            cands.append(ai.pop(0))
    if not cands:
        log(f"Сцена {i+1}: ничего не нашлось по «{'; '.join(queries)}» — замени вручную в редакторе")
    new = []
    words = [w for w in re.findall(r"[A-Za-z]{4,}", " ".join(queries)) if w.lower() not in ("with", "from", "that")]
    extra = [q for q in dict.fromkeys([auto_query(sc["text"]), sc.get("title") or ""] + [" ".join(words[k:k + 2]) for k in range(0, len(words), 2)])
             if q and q not in queries]
    if only_ai:  # рисованный референс: стоки не подмешиваем никогда, нехватку дорисовываем
        extra = []
        short = need - len(have) - len(cands)
        if short > 0:
            cands += draw(short)
    while len(have) + len(new) < need and (cands or extra):
        if ev.is_set():
            raise Cancelled()
        while extra and not cands:  # своих кадров мало — добираем другими запросами, а не повтором того же кадра
            q = extra.pop(0)
            res, _ = find_candidates(q, style, settings)
            cands = [dict(c, q=q) for c in res if c["url"] not in used][:need]
        if not cands:
            break
        c = cands.pop(0)
        with lock:  # url забираем сразу, чтобы соседняя сцена не взяла тот же
            if c["url"] in used:
                continue
            used.add(c["url"])
        try:
            path = c.get("local") or sources.download(c["url"], d, ".mp4" if c["type"] == "video" else "")
        except Exception as e:
            log(f"  не скачалось: {e}")
            continue
        if not c.get("checked") and c["type"] == "video":  # стоковое видео тоже смотрим перед вставкой
            ok, why = verify(path, "video", c.get("q"))
            if ok is False:
                log(f"Сцена {i+1}: стоковое видео не про сцену ({why}) - пропускаю")
                continue
        new.append(new_shot(p, path, c))
    # скачанные стоки могли отсеяться — добираем ИИ, а не копиями одного кадра
    for c in draw(max(0, need - len(have) - len(new))):
        with lock:
            if c["url"] in used:
                continue
            used.add(c["url"])
        new.append(new_shot(p, c["local"], c))
    if ev.is_set():  # после «Стоп» проект не трогаем
        raise Cancelled()
    with lock:
        sc["shots"] += new
        have += new
        k = 0
        pool = [x for x in have if x["media"]["type"] != "graphic"]
        while pool and len(have) < need:
            sc["shots"].append(_clone(pool[k % len(pool)], k // len(pool)))
            have.append(sc["shots"][-1])
            k += 1
        add_graphic(p, sc)
        log(f"Сцена {i+1}: кадров {len(have)}/{need}")
        ensure_stills(p, sc)
        apply_durations(sc, style)
        redraw_graphic(p, sc)
        store.save_project(p)


def gfx_theme(p):
    """Тема графики из паспорта + язык ролика (по тексту сцен): подписи «CHAPTER/ГЛАВА/第1章», км, шрифт для иероглифов."""
    th = dict(p["style"].get("gfx_theme") or {})
    text = " ".join(s.get("text", "") for s in p.get("scenes", [])[:12]) or p.get("script", "")[:3000]
    th["lang"] = gfx.detect_lang(text, p["style"].get("lang"))
    if p["style"].get("plate_color_own"):  # цвет, выбранный пользователем, главнее паспорта
        th["plate_bg"] = p["style"]["plate_color"]
        th["plate_text"] = _contrast(p["style"]["plate_color"])
    return th


def refresh_graphics(p):
    """Перерисовать превью всех вставок проекта (после смены паспорта/языка) — в редакторе видно то, что попадёт в ролик."""
    pd, th = store.project_dir(p["id"]), gfx_theme(p)
    for sc in p.get("scenes", []):
        for s in sc.get("shots", []):
            if (s.get("media") or {}).get("type") == "graphic" and s.get("graphic"):
                bg = s.get("bg")
                try:
                    gfx.still(s["graphic"], pd / s["media"]["path"], photo_path=pd / bg if bg else None, theme=th,
                              photos=[pd / x for x in s.get("bgs") or []])
                except Exception:
                    pass


def person_photo(p, name):
    """Портрет человека с Wikimedia для PERSON/PROFILE: путь относительно папки проекта или None."""
    pd = store.project_dir(p["id"])
    for q in (name.title() + " portrait", name.title()):
        try:
            res = sources.wikimedia(q, "image", p.get("format", "16:9"), n=10)
        except Exception:
            continue
        for c in res:
            try:
                path = sources.download(c["url"], pd / "media")
                return str(path.relative_to(pd)).replace("\\", "/")
            except Exception:
                continue
    return None


def ensure_stills(p, sc):
    """Стоп-кадр из видео сцены — фон для графики, когда в сцене нет фото (референс кладёт графику на видео)."""
    pd = store.project_dir(p["id"])
    for s in sc["shots"]:
        m = s.get("media") or {}
        if m.get("type") == "video" and not s.get("still"):
            rel = m["path"].rsplit(".", 1)[0] + "_still.jpg"
            try:
                if not (pd / rel).exists():
                    ff.run(["-ss", float(s.get("trim_in", 0)) + 1.0, "-i", pd / m["path"], "-frames:v", 1, "-q:v", 3, pd / rel])
                s["still"] = rel
            except Exception:
                pass


def redraw_graphic(p, sc):
    """Перерисовать превью вставки, если фон (фото) появился после того, как её нарисовали — иначе в редакторе пустой круг."""
    pd = store.project_dir(p["id"])
    for s in sc["shots"]:
        if (s.get("media") or {}).get("type") == "graphic" and s.get("graphic") and s.get("bg") != s.get("drawn_bg"):
            try:
                gfx.still(s["graphic"], pd / s["media"]["path"], photo_path=pd / s["bg"] if s.get("bg") else None,
                          theme=gfx_theme(p), photos=[pd / x for x in s.get("bgs") or []])
                s["drawn_bg"] = s.get("bg")
            except Exception:
                pass


def add_graphic(p, sc):
    """Графическая вставка (глава, цитата, карта, таблица, инфографика) — первым кадром сцены; фон — первое фото сцены."""
    g = sc.get("graphic")
    if not g or any(s.get("media", {}).get("type") == "graphic" for s in sc["shots"]):
        return
    pd = store.project_dir(p["id"])
    sid = store.new_id()
    th = pd / "media" / f"gfx_{sid}.jpg"
    th.parent.mkdir(parents=True, exist_ok=True)
    imgs = [s["media"]["path"] for s in sc["shots"] if s.get("media", {}).get("type") == "image"]
    if not imgs and g["type"].lower() in ("person", "profile", "portrait") and g.get("name"):
        ph = person_photo(p, g["name"])  # без фото круг/рамка пустые — ищем портрет по имени
        if ph:
            imgs = [ph]
    photo = imgs[0] if imgs else None
    try:
        gfx.still(g, th, photo_path=pd / photo if photo else None, theme=gfx_theme(p),
                  photos=[pd / x for x in imgs[:3]])
    except Exception:
        pass
    rel = str(th.relative_to(pd)).replace("\\", "/")
    sc["shots"].insert(0, {"id": sid, "dur": gfx.DUR.get(g["type"], 4.0), "trim_in": 0.0, "locked": True,
                           "disabled": False, "graphic": g, "bg": photo, "bgs": imgs[:3],
                           "media": {"type": "graphic", "path": rel, "url": "gfx:" + sid, "source": "gfx",
                                     "credit": "", "src_dur": 0, "thumb": rel}})


def new_shot(p, path: Path, c: dict):
    pd = store.project_dir(p["id"])
    sid = store.new_id()
    th = pd / "media" / f"th_{sid}.jpg"
    try:
        ff.thumb(path, th)
    except Exception:
        th = None
    info = ff.video_info(path) if c["type"] == "video" else {"duration": 0}
    return {"id": sid, "dur": 4.0, "trim_in": 0.0, "locked": False, "disabled": False,
            "media": {"type": c["type"], "path": str(path.relative_to(pd)).replace("\\", "/"),
                      "url": c.get("url", ""), "source": c.get("source", "upload"),
                      "credit": c.get("credit", ""), "src_dur": info.get("duration", 0),
                      "thumb": str(th.relative_to(pd)).replace("\\", "/") if th else None}}


# ---------------------------------------------------------------- озвучка

def make_voice(p, log, j):
    style = p["style"]
    if style["voice"] == "none":
        log("Озвучка выключена — пропускаю")
        for sc in p["scenes"]:
            sc.pop("voice", None)
            sc.pop("voice_dur", None)
            apply_durations(sc, style)
        return
    settings = store.get_settings()
    vd = store.project_dir(p["id"]) / "voice"
    vd.mkdir(exist_ok=True)
    n = len(p["scenes"]) or 1
    for i, sc in enumerate(p["scenes"]):
        if j.get("cancel"):  # готовые сцены идут без log(), поэтому проверяем «Стоп» здесь
            raise Cancelled()
        j["progress"] = int(100 * i / n)
        if not sc.get("text"):
            continue
        key = hashlib.md5(f'{style["voice"]}|{style.get("edge_voice")}|{style.get("voice_id")}|{settings.get("elevenlabs_voice")}|{sc["text"]}'.encode()).hexdigest()[:12]
        out = vd / f"{key}.mp3"
        if not out.exists() or ff.duration(out) <= 0:
            log(f"Озвучка сцены {i+1}…")
            tmp = vd / f"{key}.part.mp3"
            tmp.unlink(missing_ok=True)
            try:
                audio.tts(sc["text"], style, settings, tmp, log)
            except Exception as e:
                log(f"Озвучка сцены {i+1}: {sources._why(e)}")
                raise
            if ff.duration(tmp) <= 0:
                raise RuntimeError(f"Сцена {i+1}: озвучка пустая")
            tmp.replace(out)
        tight = vd / f"{key}_t2.mp3"  # без тишины по краям, паузы между фразами ~0.2 с — речь идёт подряд
        if not tight.exists() or ff.duration(tight) <= 0:
            ff.run(["-i", out, "-af", "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03:"
                    "stop_periods=-1:stop_duration=0.25:stop_threshold=-45dB:stop_silence=0.2",
                    "-c:a", "libmp3lame", "-q:a", 2, tight])
        if ff.duration(tight) > 0.3:
            out = tight
        sc["voice"] = f"voice/{out.name}"
        sc["voice_dur"] = round(ff.duration(out), 2)
        apply_durations(sc, style)
    store.save_project(p)


# ---------------------------------------------------------------- музыка

def make_music(p, log, j):
    pd = store.project_dir(p["id"])
    mode = p.get("music_mode") or (p.get("music") or {}).get("mode") or "background"
    if p.get("music") and (pd / p["music"]["path"]).exists():
        p["music"]["mode"] = mode
        log(f"Музыка уже есть: {p['music'].get('name') or p['music']['path']}")
        return
    if mode == "none":
        p["music"] = None
        log("Ролик без музыки")
        return
    prompt = p.get("music_prompt")
    if mode != "song":  # фон под голосом — синтез кодом, Suno не нужен
        from . import synth
        dur = sum(float(sc.get("voice_dur") or sc.get("dur") or 0) for sc in p.get("scenes") or []) or 120
        out = pd / "music" / f"synth_{zlib.crc32((prompt or '').encode()) % 10**6}.wav"
        path, mood = synth.make(prompt, dur, out, seed=len(p.get("scenes") or []))
        log(f"Фоновая музыка синтезом (настроение {mood}, без Suno)")
        p["music"] = {"path": f"music/{path.name}", "mode": mode, "name": f"синтез: {mood}"}
        store.save_project(p)
        return
    if not prompt:
        log("Музыка не задана (строка MUSIC: в задаче или вкладка «Музыка») — без музыки")
        return
    settings = store.get_settings()
    if not settings.get("suno_key"):
        log("Нет ключа Suno — пропускаю музыку")
        return
    log(f"Генерирую музыку в Suno: «{prompt}»")
    path = audio.suno_generate(prompt, mode != "song", settings, pd / "music", log=log)
    p["music"] = {"path": f"music/{path.name}", "mode": mode, "name": path.name}
    store.save_project(p)


# ---------------------------------------------------------------- монтаж

def _wh(style):
    return (1080, 1920) if style["format"] == "9:16" else (1920, 1080)


LEGACY_PLATE = "#1D4ED8"  # старое значение по умолчанию, которое рендер игнорировал: такие плашки были тёмными


def _plate_hex(style):
    c = str(style.get("plate_color") or "#141414")
    return "#141414" if c.upper() == LEGACY_PLATE and not style.get("plate_color_own") else c


def _contrast(hex_):
    """Чёрный или светлый текст — что читается на этом фоне."""
    h = str(hex_).lstrip("#")
    if len(h) != 6:
        return "#F2EEE6"
    r, g, b = (int(h[k:k + 2], 16) for k in (0, 2, 4))
    return "#141414" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#F2EEE6"


def _ass_color(hex_, alpha="00"):
    h = hex_.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{alpha}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _card(src: Path, W, H, out: Path):
    """Фото целиком «карточкой» с тенью на тёмно-сером фоне с виньеткой (как у Anselm Faber)."""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.open(src).convert("RGB")
    # фон: радиальный градиент от серого к почти чёрному
    g = Image.radial_gradient("L").resize((W, H))
    bg = Image.merge("RGB", [g.point(lambda v, c=c: int(c - v * (c - 12) / 255)) for c in (78, 76, 72)])
    k = min(W * 0.78 / im.width, H * 0.82 / im.height)
    w, h = int(im.width * k), int(im.height * k)
    im = im.resize((w, h), Image.LANCZOS)
    x, y = (W - w) // 2, (H - h) // 2
    r = max(4, W // 240)
    sh = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sh).rounded_rectangle((x, y + H // 60, x + w, y + h + H // 60), r * 2, fill=200)
    sh = sh.filter(ImageFilter.GaussianBlur(W // 70))
    bg.paste((0, 0, 0), (0, 0), sh)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255)
    bg.paste(im, (x, y), mask)
    bg.save(out, quality=95)
    return out


def _seg_ok(out: Path, dur):
    """Сегмент цел: файл есть, читается и длина совпадает (прерванный рендер оставлял обрубки)."""
    try:
        return out.exists() and out.stat().st_size > 0 and abs(ff.duration(out) - float(dur)) < 0.3
    except Exception:
        return False


def _tmp(out: Path):
    return out.with_name(f"{out.stem}.{uuid.uuid4().hex[:8]}.part.mp4")


def _kenburns(src: Path, out: Path, W, H, fps, dur, idx, small=False, norm="", enc=()):
    """Плавный наезд/сдвиг: каждый кадр — аффинное преобразование с субпиксельной точностью (без дрожи)."""
    import subprocess
    from PIL import Image
    im = Image.open(src).convert("RGB")
    if im.width / im.height < W / H * 0.8:
        # вертикальное фото: целиком в рамке по центру на тёмном фоне со свечением вокруг рамки
        from PIL import ImageFilter, ImageDraw
        CW, CH = round(W * 1.15), round(H * 1.15)
        fh = round(CH * 0.74)
        fg = im.resize((round(im.width * fh / im.height), fh), Image.LANCZOS)
        b = max(4, CH // 90)  # толщина рамки
        x0, y0 = (CW - fg.width) // 2, (CH - fg.height) // 2
        bg = Image.new("RGB", (CW, CH), (14, 13, 12))
        glow = Image.new("RGB", (CW, CH), (0, 0, 0))
        g = CH // 30
        ImageDraw.Draw(glow).rectangle((x0 - b - g, y0 - b - g, x0 + fg.width + b + g, y0 + fg.height + b + g),
                                       fill=(190, 150, 80))
        glow = glow.filter(ImageFilter.GaussianBlur(CH // 22))
        bg = Image.blend(bg, glow, 0.55)
        ImageDraw.Draw(bg).rectangle((x0 - b, y0 - b, x0 + fg.width + b - 1, y0 + fg.height + b - 1),
                                     fill=(236, 226, 205))
        bg.paste(fg, (x0, y0))
        im = bg
        small = True
    k = max(W / im.width, H / im.height) * 1.15  # запас под движение
    im = im.resize((max(W, round(im.width * k)), max(H, round(im.height * k))), Image.LANCZOS)
    iw, ih = im.size
    F = max(1, int(round(dur * fps)))
    amp = 0.04 if small else 0.10
    mode = idx % (2 if small else 4)

    def ease(t):
        return t * t * (3 - 2 * t)

    cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-vf", "setsar=1" + norm, *map(str, enc), str(out)]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=ff._NOWIN)
    try:
        for f in range(F):
            t = ease(f / max(1, F - 1))
            if mode == 0:
                z, px, py = 1 + amp * t, 0.5, 0.5
            elif mode == 1:
                z, px, py = 1 + amp * (1 - t), 0.5, 0.5
            elif mode == 2:
                z, px, py = 1 + amp * 0.6, 0.3 + 0.4 * t, 0.5
            else:
                z, px, py = 1 + amp * 0.6, 0.7 - 0.4 * t, 0.5
            # видимое окно в координатах исходника
            s = min(iw / W, ih / H) / z            # пикселей исходника на пиксель кадра
            cw, ch = W * s, H * s
            x0 = (iw - cw) * px
            y0 = (ih - ch) * py
            fr = im.transform((W, H), Image.AFFINE, (s, 0, x0, 0, s, y0), resample=Image.BILINEAR)
            pr.stdin.write(fr.tobytes())
        pr.stdin.close()
        err = pr.stderr.read().decode("utf-8", "replace")
        if pr.wait() != 0:
            raise ff.FFError(err[-500:])
    finally:
        if pr.poll() is None:
            pr.kill()
    return out


def _use_card(style, src: Path, W, H):
    mode = style.get("photo_card", "auto")
    if mode == "all":
        return True
    if mode not in ("auto", "mix"):
        return False
    try:
        from PIL import Image
        with Image.open(src) as im:
            a = im.width / im.height
    except Exception:
        return False
    if abs(a / (W / H) - 1) > 0.2:  # портрет в 16:9 / пейзаж в 9:16 — не режем
        return True
    if mode == "mix":  # вперемешку, как в референсе: та же доля фото карточкой (стабильно для файла)
        import zlib
        return zlib.crc32(src.name.encode()) % 100 < float(style.get("photo_card_share", 0.5)) * 100
    return False


def _seg(p, shot, idx, style, rd: Path, log, overlays=None):
    """Кадр + плашки поверх него (LABEL, CAPTION, …) — анимированный слой gfx, кэшируется отдельно."""
    base = _seg_base(p, shot, idx, style, rd, log)
    if not overlays:
        return base
    W, H = _wh(style)
    fps = style["fps"]
    dur = max(0.3, float(shot["dur"]))
    th = gfx_theme(p)
    key = hashlib.md5(f'ov|{base.name}|{json.dumps(overlays, ensure_ascii=False, sort_keys=True)}|'
                      f'{json.dumps(th, sort_keys=True)}|{dur}|v1'.encode()).hexdigest()[:14]
    out = rd / "segs" / f"{key}_ov.mp4"
    if not _seg_ok(out, dur):
        norm = ",scale=out_range=tv,format=yuv420p,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-color_range", "tv",
               "-an", "-r", fps]
        tmp = _tmp(out)
        gfx.render_overlay(base, overlays, tmp, W, H, fps, dur, norm=norm, enc=enc, theme=th)
        tmp.replace(out)
    return out


def _overlay_map(scenes, style):
    """Плашки сцен → кадры: k-я плашка ложится на k-й кадр с фото/видео (лишние — на последний).
    При plate_style="label" заголовок сцены (## …) тоже становится печатающейся плашкой поверх первого кадра."""
    res = {}
    for i, sc in enumerate(scenes):
        ovs = [dict(o) for o in sc.get("overlays") or []]
        shots = [s for s in sc["shots"] if s.get("media") and not s.get("disabled")]
        free = [s for s in shots if s["media"]["type"] != "graphic"]
        chap = any((s.get("graphic") or {}).get("type") == "chapter" for s in shots)
        if style.get("plate_style") == "label" and style["plates"] and sc.get("title") and not chap:
            ovs.insert(0, {"type": "label", "text": sc["title"], "pos": "right" if i % 2 else "left"})
        if not free:
            continue
        k = -1
        for o in ovs:  # стрелки, круги, крестики, лупа, цвет эпизода — на кадр предыдущей плашки
            if o["type"] not in gfx.ATTACH or k < 0:
                k += 1
            res.setdefault(free[min(k, len(free) - 1)]["id"], []).append(o)
    return res


# Тонировка фото/видео (style["grade"]) — как в референсе; графику не трогает
GRADES = {
    "sepia": ",eq=saturation=0.35:contrast=1.06:brightness=-0.03,"
             "colorbalance=rs=0.10:gs=0.04:bs=-0.10:rm=0.08:gm=0.02:bm=-0.08,vignette=PI/4.2",
    "bw": ",hue=s=0,eq=contrast=1.1,vignette=PI/4.2",
}


def _seg_base(p, shot, idx, style, rd: Path, log):
    """Рендер одного кадра в нормализованный клип (кэшируется)."""
    pd = store.project_dir(p["id"])
    W, H = _wh(style)
    fps = style["fps"]
    m = shot["media"]
    src = pd / m["path"]
    dur = max(0.3, float(shot["dur"]))
    zoom = style["zoom"] and m["type"] == "image"
    card = m["type"] == "image" and _use_card(style, src, W, H)
    if m["type"] == "graphic":
        g = shot.get("graphic") or {}
        bg = shot.get("bg")
        bgs = shot.get("bgs") or []
        th = gfx_theme(p)
        key = hashlib.md5(f'gfx|{json.dumps(g, ensure_ascii=False)}|{json.dumps(th, sort_keys=True)}|{bg}|{bgs}|{dur}|{W}x{H}|{fps}|v8'.encode()).hexdigest()[:14]
        out = rd / "segs" / f"{key}.mp4"
        if not _seg_ok(out, dur):
            out.parent.mkdir(exist_ok=True)
            norm = ",scale=out_range=tv,format=yuv420p,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
            enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-color_range", "tv",
                   "-an", "-r", fps]
            tmp = _tmp(out)
            gfx.render(g, tmp, W, H, fps, dur, photo_path=pd / bg if bg else None, norm=norm, enc=enc, theme=th,
                       photos=[pd / x for x in bgs])
            tmp.replace(out)
        return out
    grade = GRADES.get(style.get("grade") or "", "")
    tall = ""
    if m["type"] == "image" and zoom and not card:
        from PIL import Image
        with Image.open(src) as _im:
            tall = "|tall" if _im.width / _im.height < W / H * 0.8 else ""
    key = hashlib.md5(f'{m["path"]}|{dur}|{shot.get("trim_in",0)}|{zoom}|{W}x{H}|{fps}|{idx%4}|tv709|card{card}|{grade}{tall}'.encode()).hexdigest()[:14]
    out = rd / "segs" / f"{key}.mp4"
    if _seg_ok(out, dur):
        return out
    out.parent.mkdir(exist_ok=True)
    final, out = out, _tmp(out)  # пишем во временный файл: прерванный рендер не оставит обрубок под настоящим именем
    cover = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    # все кадры — в одном формате (JPEG дают yuvj420p/pc): иначе склейка -c copy ломает переходы xfade
    norm = grade + ",scale=out_range=tv,format=yuv420p,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-color_range", "tv",
           "-an", "-r", fps]
    if card:
        src = _card(src, W, H, rd / "segs" / f"{key}_card.jpg")
    if m["type"] == "image" and zoom:
        _kenburns(src, out, W, H, fps, dur, idx, small=card, norm=norm, enc=enc)
    elif m["type"] == "image":
        if False:
            # карточка: очень медленный наезд, как в референсе
            F = int(round(dur * fps))
            z = f"1+0.04*on/{F}" if idx % 2 == 0 else f"1.04-0.04*on/{F}"
            vf = (f"scale={W*2}:{H*2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d={F}:s={W}x{H}:fps={fps},setsar=1{norm}")
            ff.run(["-i", src, "-vf", vf, "-frames:v", F, *enc, out])
        elif zoom:
            F = int(round(dur * fps))
            moves = [
                ("1+0.12*on/{F}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
                ("1.12-0.12*on/{F}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
                ("1.1", "(iw-iw/zoom)*on/{F}", "ih/2-(ih/zoom/2)"),
                ("1.1", "(iw-iw/zoom)*(1-on/{F})", "ih/2-(ih/zoom/2)"),
            ]
            z, x, y = (s.format(F=F) for s in moves[idx % 4])
            vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
                  f"zoompan=z='{z}':x='{x}':y='{y}':d={F}:s={W}x{H}:fps={fps},setsar=1{norm}")
            ff.run(["-i", src, "-vf", vf, "-frames:v", F, *enc, out])
        else:
            ff.run(["-loop", 1, "-i", src, "-t", dur, "-vf", cover + ",setsar=1" + norm, *enc, out])
    else:
        ti = float(shot.get("trim_in", 0))
        if ti:  # копия кадра берёт дальше по клипу; короткий клип — по кругу
            ti %= max(1.0, ff.duration(src) - 0.5)
        ff.run(["-ss", ti, "-stream_loop", -1, "-i", src, "-t", dur,
                "-vf", f"{cover},fps={fps},setsar=1{norm}", *enc, out])
    out.replace(final)
    return final


def _starts(durs, td, CH=20):
    """Старты сцен в итоговом ролике: внутри куска по CH сцен — переходы (наложение td), между кусками — стык."""
    starts, t = [], 0.0
    for c0 in range(0, len(durs), CH):
        part = durs[c0:c0 + CH]
        if len(part) == 1 or td == 0:
            for d in part:
                starts.append(t)
                t += d
        else:
            L = part[0]
            starts.append(t)
            for k in range(1, len(part)):
                off = L - td
                starts.append(t + off)
                L = off + part[k]
            t += L
    return starts, t


def _render_scenes(p):
    out, prev = [], None
    for sc in p["scenes"]:
        ok = [s for s in sc["shots"] if s.get("media") and not s.get("disabled")]
        if not ok and sc.get("voice") and prev:
            # сцена без кадров, но с голосом — не выкидываем озвучку, берём фото соседней сцены
            c = json.loads(json.dumps(prev))
            c["id"] = store.new_id()
            sc["shots"] = [c]
            ok = [c]
        imgs = [s for s in ok if s["media"]["type"] != "graphic"]
        if imgs:
            prev = imgs[-1]
        if ok:
            out.append(sc)
    return out


def _fixed_sfx(style):
    """Один и тот же звук на всех переходах (style.sfx_name), иначе — случайный из библиотеки."""
    name = style.get("sfx_name") or ""
    if name == "_whoosh.wav":
        return ff.make_whoosh(store.DATA / "_whoosh.wav")
    f = store.SFX / name
    return f if name and f.is_file() else None


def _sfx_on(sc, style):
    """Свайп сам — только на смене темы (сцена с заголовком ## …), а не на каждой смене сцены."""
    return style.get("sfx_mode") == "all" or bool(sc.get("title"))


def _sfx_lib():
    return sorted(f for f in store.SFX.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".ogg"))


_DUR_CACHE = {}


def _dur(path: Path):
    try:
        k = (str(path), path.stat().st_mtime)
    except OSError:
        return 0.0
    if k not in _DUR_CACHE:
        _DUR_CACHE[k] = ff.duration(path) or 0.0
    return _DUR_CACHE[k]


def layout(p):
    """Раскладка ролика по дорожкам — ровно как её соберёт render() (для вкладки «Таймлайн»)."""
    style = p["style"]
    pd = store.project_dir(p["id"])
    fps = style["fps"]
    scenes = _render_scenes(p)
    td = float(style["transition_dur"]) if style["transitions"] and len(scenes) > 1 else 0.0
    durs, shots = [], []
    k = 0
    for sc in scenes:
        off = 0.0
        for s in sc["shots"]:
            if not s.get("media") or s.get("disabled"):
                continue
            d = round(float(s["dur"]) * fps) / fps
            shots.append({"scene": sc["id"], "shot": s["id"], "off": off, "dur": d, "idx": k})
            off += d
            k += 1
        durs.append(off)
    starts, total = _starts(durs, td)
    sc_start = {sc["id"]: st for sc, st in zip(scenes, starts)}
    for x in shots:
        x["start"] = round(sc_start[x["scene"]] + x["off"], 3)
    plates, subs = _text_events(scenes, starts, style, td)
    voices = []
    for i, sc in enumerate(scenes):
        if sc.get("voice") and style["voice"] != "none" and (pd / sc["voice"]).exists():
            voices.append({"scene": sc["id"], "start": round(starts[i] + (td / 2 if i else 0), 3),
                           "dur": sc.get("voice_dur") or _dur(pd / sc["voice"]), "file": sc["voice"]})
    sfx = []
    if style["sfx"] and len(scenes) > 1:
        lib = _sfx_lib()
        rnd = random.Random(p["id"])
        for i in range(1, len(scenes)):
            if not _sfx_on(scenes[i], style):
                continue
            f = _fixed_sfx(style) or (rnd.choice(lib) if lib else store.DATA / "_whoosh.wav")
            sfx.append({"auto": True, "start": round(max(0, starts[i] - 0.25), 3), "name": Path(f).name,
                        "dur": _dur(Path(f)) or 0.7, "vol": style["sfx_volume"]})
    for it in p.get("sfx_items") or []:
        f = store.SFX / it.get("name", "")
        if f.exists():
            sfx.append({**it, "auto": False, "dur": _dur(f) or 0.5})
    music = None
    m = p.get("music")
    if m and (pd / m["path"]).exists():
        song = m.get("mode") == "song"
        music = {"file": m["path"], "name": m.get("name") or m["path"], "mode": m.get("mode"),
                 "loop": _dur(pd / m["path"]),
                 "vol": 1.0 if song else (style["music_volume"] if voices else 0.6)}
    W, H = _wh(style)
    return {"total": round(total, 3), "td": td, "W": W, "H": H, "scenes": [{"id": sc["id"], "start": round(st, 3),
            "dur": round(d, 3)} for sc, st, d in zip(scenes, starts, durs)], "shots": shots, "plates": plates,
            "subs": subs, "texts": [t for t in (p.get("texts") or []) if t.get("text")], "voices": voices,
            "sfx": sfx, "music": music}


def render(p, log, j):
    style = p["style"]
    pd = store.project_dir(p["id"])
    rd = pd / "render"
    rd.mkdir(exist_ok=True)
    for f in (rd / "segs").glob("*.part.mp4"):  # остатки прерванной сборки
        f.unlink(missing_ok=True)
    W, H = _wh(style)
    fps = style["fps"]
    scenes = _render_scenes(p)
    if not scenes:
        raise RuntimeError("Нет кадров. Сначала подбери медиа.")
    for sc in scenes:
        apply_durations(sc, style)  # длины кадров — строго под озвучку

    # режим «песня»: без озвучки растягиваем ролик под длину трека
    music = p.get("music")
    if music and music.get("mode") == "song" and style["voice"] == "none":
        mdur = ff.duration(pd / music["path"])
        weights = [max(1, len(sc.get("text", "")) or 20) for sc in scenes]
        tw = sum(weights)
        td_total = style["transition_dur"] * (len(scenes) - 1) if style["transitions"] else 0
        for sc, w in zip(scenes, weights):
            sc["fixed_dur"] = round((mdur + td_total) * w / tw, 2)
            apply_durations(sc, style)

    # Карта-стратегия: войска/дуга живут всю сцену — сцена идёт одним кадром карты, модельки не пропадают посреди фразы
    for sc in scenes:
        if any(o.get("type") in ("units", "arc") for o in sc.get("overlays") or []):
            live = [s for s in sc["shots"] if s.get("media") and not s.get("disabled")]
            pics = [s for s in live if s["media"]["type"] != "graphic"]
            if len(pics) > 1:
                pics[0]["dur"] = round(sum(float(s["dur"]) for s in pics), 3)
                for s in pics[1:]:
                    s["disabled"] = True

    # 1. кадры → клипы
    all_shots = [(sc, s) for sc in scenes for s in sc["shots"] if s.get("media") and not s.get("disabled")]
    log(f"Рендер кадров: {len(all_shots)}")
    seg_files = {}
    ov_map = _overlay_map(scenes, style)
    from concurrent.futures import ThreadPoolExecutor

    def one(k):
        sc, s = all_shots[k]
        j.setdefault("tids", set()).add(threading.get_ident())  # чтобы «Стоп» убил ffmpeg и в этом потоке
        if j.get("cancel"):
            raise Cancelled()
        try:
            return s["id"], _seg(p, s, k, style, rd, log, ov_map.get(s["id"]))
        except Cancelled:
            raise
        except Exception as e:
            raise RuntimeError(f"Кадр {k+1} ({s['media']['path']}): {e}")

    with ThreadPoolExecutor(4) as ex:  # кадры рендерятся параллельно
        for n, (sid, f) in enumerate(ex.map(one, range(len(all_shots))), 1):
            seg_files[sid] = f
            j["progress"] = int(60 * n / len(all_shots))
            log(f"Готово кадров {n}/{len(all_shots)}")

    # 2. кадры сцены склеиваем жёстко
    scene_clips, scene_durs = [], []
    for i, sc in enumerate(scenes):
        files = [seg_files[s["id"]] for s in sc["shots"] if s["id"] in seg_files]
        lst = rd / f"scene_{i:03d}.txt"
        lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
        out = rd / f"scene_{i:03d}.mp4"
        ff.run(["-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", out])
        scene_clips.append(out)
        scene_durs.append(ff.duration(out))
    j["progress"] = 65

    # 3. сцены между собой — с переходами (кусками по 20 сцен)
    td = float(style["transition_dur"]) if style["transitions"] and len(scenes) > 1 else 0.0
    starts, chunks = [], []
    t = 0.0
    CH = 20
    for c0 in range(0, len(scene_clips), CH):
        part = scene_clips[c0:c0 + CH]
        pdur = scene_durs[c0:c0 + CH]
        out = rd / f"chunk_{c0//CH:02d}.mp4"
        if len(part) == 1 or td == 0:
            for k in range(len(part)):
                starts.append(t)
                t += pdur[k]
            lst = rd / f"chunk_{c0//CH:02d}.txt"
            lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in part), encoding="utf-8")
            ff.run(["-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", out])
        else:
            args, flt = [], []
            for f in part:
                args += ["-i", f]
            L = pdur[0]
            starts.append(t)
            prev = "[0:v]"
            for k in range(1, len(part)):
                off = L - td
                starts.append(t + off)
                lab = f"[x{k}]"
                flt.append(f"{prev}[{k}:v]xfade=transition={style['transition']}:duration={td}:offset={off:.3f}{lab}")
                prev = lab
                L = off + pdur[k]
            t += L
            ff.run([*args, "-filter_complex", ";".join(flt), "-map", prev, "-c:v", "libx264", "-preset",
                    "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", fps, out])
        chunks.append(out)
    lst = rd / "chunks.txt"
    lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in chunks), encoding="utf-8")
    video = rd / "video.mp4"
    ff.run(["-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", video])
    total = ff.duration(video)
    j["progress"] = 80
    log(f"Видеоряд собран: {total:.1f} сек")

    # 4. субтитры и плашки (ASS)
    _write_ass(rd / "subs.ass", scenes, starts, style, W, H, td, p.get("texts") or [])

    # 5. звук
    log("Свожу звук…")
    inputs, flt, mix = ["-i", "video.mp4"], [], []
    n = 1
    voice_lbls = []
    for i, sc in enumerate(scenes):
        if sc.get("voice") and style["voice"] != "none":
            vp = pd / sc["voice"]
            if vp.exists():
                inputs += ["-i", vp.as_posix()]
                delay = int((starts[i] + (td / 2 if i else 0)) * 1000)
                flt.append(f"[{n}:a]aformat=sample_rates=44100:channel_layouts=stereo,adelay={delay}|{delay}[v{n}]")
                voice_lbls.append(f"[v{n}]")
                n += 1
    if voice_lbls:
        flt.append(f"{''.join(voice_lbls)}amix=inputs={len(voice_lbls)}:normalize=0,apad[voice]")
    sfx_lbl = None
    if style["sfx"] and len(scenes) > 1:
        lib = _sfx_lib()
        whoosh = ff.make_whoosh(store.DATA / "_whoosh.wav")
        rnd = random.Random(p["id"])
        lbls = []
        for i in range(1, len(scenes)):
            if not _sfx_on(scenes[i], style):
                continue
            f = _fixed_sfx(style) or (rnd.choice(lib) if lib else whoosh)
            inputs += ["-i", Path(f).as_posix()]
            delay = max(0, int((starts[i] - 0.25) * 1000))
            flt.append(f"[{n}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={style['sfx_volume']},"
                       f"highpass=f=120,lowpass=f=3500,afade=t=in:d=0.08,afade=t=out:st=0.4:d=0.3,adelay={delay}|{delay}[s{n}]")
            lbls.append(f"[s{n}]")
            n += 1
        if lbls:
            flt.append(f"{''.join(lbls)}amix=inputs={len(lbls)}:normalize=0[sfx]")
            sfx_lbl = "[sfx]"
    # звуки, которые поставили руками на таймлайне
    own = [it for it in (p.get("sfx_items") or []) if (store.SFX / it.get("name", "")).exists()
           and float(it.get("start", 0)) < total]
    if own:
        lbls = [sfx_lbl] if sfx_lbl else []
        for it in own:
            inputs += ["-i", (store.SFX / it["name"]).as_posix()]
            delay = int(max(0, float(it.get("start", 0))) * 1000)
            flt.append(f"[{n}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={float(it.get('vol', 0.8))},"
                       f"adelay={delay}|{delay}[u{n}]")
            lbls.append(f"[u{n}]")
            n += 1
        flt.append(f"{''.join(lbls)}amix=inputs={len(lbls)}:normalize=0[sfxall]")
        sfx_lbl = "[sfxall]"
    music_lbl = None
    if music and (pd / music["path"]).exists():
        inputs += ["-stream_loop", -1, "-i", (pd / music["path"]).as_posix()]
        song = music.get("mode") == "song"
        vol = 1.0 if song else (style["music_volume"] if voice_lbls else 0.6)
        fade = max(0, total - 2.5)
        flt.append(f"[{n}:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim=0:{total:.2f},"
                   f"volume={vol},afade=t=out:st={fade:.2f}:d=2.5[mus0]")
        if voice_lbls and not song:
            flt.append("[voice]asplit=2[voice][vkey]")
            flt.append("[mus0][vkey]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[mus]")
        else:
            flt.append("[mus0]anull[mus]")
        music_lbl = "[mus]"
        n += 1
    parts = [x for x in ("[voice]" if voice_lbls else None, sfx_lbl, music_lbl) if x]
    if parts:
        flt.append(f"{''.join(parts)}amix=inputs={len(parts)}:normalize=0,alimiter=limit=0.95[aout]")

    vf = "ass=subs.ass" if (style["subtitles"] or style["plates"]) else "null"
    flt.insert(0, f"[0:v]{vf}[vout]")
    # 300+ сцен не влезают в командную строку Windows (WinError 206): пути — относительно rd, фильтр — из файла
    inputs = [os.path.relpath(x, rd).replace("\\", "/") if isinstance(x, str) and os.path.isabs(x) else x for x in inputs]
    (rd / "filter.txt").write_text(";".join(flt), encoding="utf-8")
    args = [*inputs, "-filter_complex_script", "filter.txt", "-map", "[vout]"]
    if parts:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    j["progress"] = 88
    log("Финальная сборка…")
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
             "-t", f"{total:.2f}", "-movflags", "+faststart", "final.mp4"]
    ff.run(args, cwd=rd)
    out_name = re.sub(r"[^\w\-а-яА-ЯёЁ ]+", "", p["name"]).strip() or "video"
    final = pd / f"{out_name}.mp4"
    shutil.copyfile(rd / "final.mp4", final)
    p["output"] = final.name
    stem = final.stem
    meta = p.get("meta") or {}
    info = "\n\n".join(x for x in (meta.get("title"), meta.get("description"),
                                    meta.get("tags") and "Теги: " + meta["tags"]) if x)
    store.export_to_channel(p.get("channel_id"), final, {f"{stem}.txt": info}, log=log)
    p["timeline"] = [{"scene": sc["id"], "start": round(s, 2)} for sc, s in zip(scenes, starts)]
    log(f"Ролик: {final.name} ({total:.1f} сек)")


def _fmt_t(t):
    t = max(0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _chunks(text, n):
    words = text.split()
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= n or re.search(r"[.!?…]$", w):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def _text_events(scenes, starts, style, td):
    """Плашки и субтитры с таймингом (общие для ASS и таймлайна)."""
    plates, subs = [], []
    for i, sc in enumerate(scenes):
        st = starts[i] + (td / 2 if i else 0)
        g = next((s for s in sc.get("shots") or [] if not s.get("disabled")), None)
        hide = 0.0  # пока на экране цитата или заставка главы — текст внизу не нужен
        if g and g.get("media", {}).get("type") == "graphic" and (g.get("graphic") or {}).get("type") in ("quote", "chapter"):
            hide = st + float(g["dur"]) - td / 2
        if (style["plates"] and style.get("plate_style") != "label" and sc.get("title")
                and not (hide and g["graphic"]["type"] == "chapter")):
            a = st + max(0.0, float(sc.get("plate_at") or 0))
            plates.append({"scene": sc["id"], "start": round(a, 3), "dur": max(0.3, float(sc.get("plate_dur") or 3.0)),
                           "text": sc["title"]})
        if style["subtitles"] and sc.get("text"):
            dur = sc.get("voice_dur") or scene_duration(sc, style) - 0.3
            parts = _chunks(sc["text"], int(style["sub_words"]))
            total_chars = sum(len(x) for x in parts) or 1
            t = st
            for ptxt in parts:
                d = dur * len(ptxt) / total_chars
                if t + d > hide:
                    a = max(t, hide)
                    subs.append({"scene": sc["id"], "start": round(a, 3), "dur": round(t + d - a, 3), "text": ptxt})
                t += d
    return plates, subs


def _write_ass(path, scenes, starts, style, W, H, td, texts=()):
    vert = H > W
    size = int(style["sub_size"] * (1.1 if vert else 1.0))
    align = 5 if style["sub_position"] == "center" else 2
    marg_v = int(H * (0.22 if vert else 0.08))
    plate_size = int(size * 0.7)
    font = style.get("font") or "Georgia"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Sub,{font},{size},{_ass_color(style['sub_color'])},&H000000FF,&H40000000,&H80000000,0,0,0,0,100,100,0.5,0,1,"
        f"{max(2, size // 24)},{max(2, size // 20)},{align},80,80,{marg_v},1",
        f"Style: Plate,{font},{plate_size},{_ass_color(_contrast(_plate_hex(style)))},&H000000FF,{_ass_color(_plate_hex(style), '28')},"
        f"{_ass_color(_plate_hex(style), '28')},0,0,0,0,100,100,1,0,3,{max(10, plate_size // 3)},0,7,70,70,{int(H * 0.07)},1",
        f"Style: Txt,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(3, size // 14)},1,8,80,80,{int(H * 0.08)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    plates, subs = _text_events(scenes, starts, style, td)
    for e in plates:
        lines.append(f"Dialogue: 1,{_fmt_t(e['start'])},{_fmt_t(e['start'] + e['dur'])},Plate,,0,0,0,,"
                     f"{{\\fad(250,250)}}▍ {_ass_esc(e['text'])}")
    subs = sorted(subs, key=lambda e: e["start"])
    for k, e in enumerate(subs):  # никогда две реплики разом: прошлая гаснет, когда начинается следующая
        if k + 1 < len(subs):
            e["dur"] = max(0.2, min(e["dur"], subs[k + 1]["start"] - e["start"] - 0.02))
        lines.append(f"Dialogue: 0,{_fmt_t(e['start'])},{_fmt_t(e['start'] + e['dur'])},Sub,,0,0,0,,{_ass_esc(e['text'])}")
    an = {"top": 8, "center": 5, "bottom": 2}
    for e in texts:
        if not e.get("text"):
            continue
        a = float(e.get("start", 0))
        lines.append(f"Dialogue: 2,{_fmt_t(a)},{_fmt_t(a + float(e.get('dur', 2)))},Txt,,0,0,0,,"
                     f"{{\\an{an.get(e.get('pos'), 8)}\\fad(200,200)}}{_ass_esc(e['text'])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def _ass_esc(s):
    return s.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")
