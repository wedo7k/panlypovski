"""YT Studio — локальный сервер. Запуск: python -m app.server  (или run.bat / run.sh)"""
import re
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .engine import audio, gchrome, inbox, mix, pipeline, reference, sources, store, videotask

app = FastAPI(title="YT Studio")
threading.Thread(target=videotask.resume_stuck, daemon=True).start()


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"  # после обновления софта сразу видна новая версия
    return resp
STATIC = Path(__file__).parent / "static"


def _proj(pid):
    try:
        return store.load_project(pid)
    except KeyError:
        raise HTTPException(404, "Проект не найден")


def _busy(pid):
    if pipeline.is_running(pid):
        raise HTTPException(409, "Идёт сборка — подожди, пока закончится")


def _safe(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if not str(p).startswith(str(base.resolve())):
        raise HTTPException(400, "bad path")
    if not p.exists():
        raise HTTPException(404, "нет файла")
    return p


def _save_upload(f: UploadFile, folder: Path, name=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    ext = Path(f.filename or "file").suffix.lower()
    out = folder / ((name or store.new_id()) + ext)
    with open(out, "wb") as w:
        shutil.copyfileobj(f.file, w)
    return out


# ---------------- настройки

@app.get("/api/settings")
def get_settings():
    s = store.get_settings()
    return {**s, "voices": audio.EDGE_VOICES, "default_style": store.DEFAULT_STYLE}


@app.post("/api/settings")
def set_settings(patch: dict = Body(...)):
    return store.save_settings(patch)


@app.get("/api/ffmpeg/check")
def ffmpeg_check():
    from .engine import ff
    return ff.check()


@app.post("/api/glabs/check")
def glabs_check(data: dict = Body(...)):
    from .engine import sources
    return sources.glabs_check({**store.get_settings(), **data})


@app.post("/api/glabs/video")
def glabs_video(data: dict = Body(...)):
    """Пробный/отдельный клип G-Labs -> data/glabs_video/<hash>.mp4"""
    from .engine import sources
    s = {**store.get_settings(), **{k: v for k, v in data.items() if k.startswith("glabs") and v}}
    if not s.get("glabs_key"):
        raise HTTPException(400, "нет ключа G-Labs")
    try:
        p = sources.glabs_video(data.get("prompt") or "slow cinematic camera move, Japanese anime style", data.get("format") or "16:9",
                                s, store.DATA / "glabs_video", length=data.get("length") or 4)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"path": str(p)}


@app.get("/api/glabs/video.mp4")
def glabs_video_file(p: str):
    from pathlib import Path
    f = Path(p).resolve()
    if (store.DATA / "glabs_video").resolve() not in f.parents:
        raise HTTPException(404)
    return FileResponse(f, media_type="video/mp4")


@app.get("/api/voices")
def voices(provider: str, lang: str = "ru"):
    try:
        return audio.list_voices(provider, lang, store.get_settings())
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/voices/preview")
def voice_preview(data: dict = Body(...)):
    """Короткая фраза выбранным голосом — послушать до озвучки ролика."""
    import hashlib
    lang = (data.get("lang") or "ru")[:2]
    text = (data.get("text") or "").strip() or audio.SAMPLES.get(lang, audio.SAMPLES["en"])
    style = {"voice": data.get("voice") or "edge", "voice_id": data.get("voice_id") or "", "lang": lang,
             "edge_voice": data.get("voice_id") or ""}
    key = hashlib.md5(f'{style["voice"]}|{style["voice_id"]}|{text}'.encode()).hexdigest()[:12]
    d = store.DATA / "voice_preview"
    d.mkdir(exist_ok=True)
    out = d / f"{key}.mp3"
    if not out.exists():
        try:
            audio.tts(text, style, store.get_settings(), out)
        except Exception as e:
            out.unlink(missing_ok=True)
            raise HTTPException(400, str(e))
    return FileResponse(out, media_type="audio/mpeg")


@app.post("/api/elevenlabs/check")
def elevenlabs_check(data: dict = Body({})):
    out = store.DATA / "tts_test.mp3"
    out.unlink(missing_ok=True)
    return audio.elevenlabs_check({**store.get_settings(), **{k: v for k, v in data.items() if v}}, out)


@app.get("/api/elevenlabs/test.mp3")
def elevenlabs_test():
    return FileResponse(store.DATA / "tts_test.mp3", media_type="audio/mpeg")


SUNO_TEST = {"state": "idle", "msg": "", "file": None}


@app.post("/api/suno/test")
def suno_test_start(data: dict = Body({})):
    """Пробная генерация одного трека в Suno (тратит кредиты на 1 генерацию)."""
    if SUNO_TEST["state"] == "running":
        return SUNO_TEST
    st = {**store.get_settings(), **{k: v for k, v in data.items() if v}}
    SUNO_TEST.update(state="running", msg="Отправляю задачу в Suno…", file=None)

    def work():
        try:
            path = audio.suno_generate("calm lofi hip hop, soft piano, vinyl crackle, 80 bpm", True, st,
                                       store.DATA / "suno_test", log=lambda m: SUNO_TEST.update(msg=m))
            SUNO_TEST.update(state="done", msg="Генерация работает — трек готов", file=str(path))
        except Exception as e:
            SUNO_TEST.update(state="error", msg=str(e))
    threading.Thread(target=work, daemon=True).start()
    return SUNO_TEST


@app.get("/api/suno/test")
def suno_test_status():
    return SUNO_TEST


@app.get("/api/suno/test.mp3")
def suno_test_file():
    if not SUNO_TEST["file"]:
        raise HTTPException(404)
    return FileResponse(SUNO_TEST["file"], media_type="audio/mpeg")


@app.post("/api/suno/check")
def suno_check(data: dict = Body({})):
    return audio.suno_check({**store.get_settings(), **{k: v for k, v in data.items() if v}})


# ---------------- Google-аккаунт в отдельном Chrome (сессия хранится в профиле)

@app.get("/api/google_chrome")
def gc_status():
    return gchrome.status()


@app.post("/api/google_chrome/open")
def gc_open(data: dict = Body({})):
    try:
        return gchrome.open_chrome(data.get("url"), data.get("account"))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/google_chrome/reset")
def gc_reset():
    try:
        return gchrome.reset()
    except RuntimeError as e:
        raise HTTPException(400, str(e))


# ---------------- проекты

@app.get("/api/projects")
def projects(channel: str = None):
    return store.list_projects(channel)


@app.post("/api/projects")
def create(data: dict = Body(...)):
    cid = data.get("channel_id") or None
    p = store.create_project(data.get("name", ""), data.get("format", "16:9"), cid)
    rid = data.get("reference_id") or (store.get_channel(cid) or {}).get("reference_id")
    if rid:
        apply_ref(p["id"], {"reference_id": rid})
        p = store.load_project(p["id"])
        if data.get("format"):
            p["style"]["format"] = data["format"]
    v = data.get("voice") or {}  # озвучка, выбранная в диалоге «Новый проект», важнее референса
    p["style"].update({k: v[k] for k in ("voice", "lang", "voice_id") if v.get(k) is not None})
    if v.get("voice") == "edge" and v.get("voice_id"):
        p["style"]["edge_voice"] = v["voice_id"]
    if data.get("brief") or data.get("script"):
        p["brief"] = (data.get("brief") or "").strip()
        parts = [x.strip() for x in (data.get("brief"), data.get("script")) if x and x.strip()]
        p["script"] = "\n\n".join(parts)
    store.save_project(p)
    return p


@app.post("/api/projects/from_task")
def create_from_task(data: dict = Body(...)):
    """Файл задачи (.md) → новый проект; run=true сразу запускает всё."""
    try:
        ch = store.get_channel(data.get("channel_id") or "")
        p = pipeline.project_from_task(data.get("text", ""), ch["name"] if ch else None,
                                       data.get("reference_id") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if data.get("run"):
        pipeline.start(p["id"], ["parse", "voice", "media", "music", "render"])
    return p


# ---------------- каналы

@app.get("/api/channels")
def channels():
    out = []
    for c in store.list_channels():
        out.append({**c, "projects": len(store.list_projects(c["id"]))})
    return out


@app.post("/api/channels")
def channel_save(data: dict = Body(...)):
    c = store.get_channel(data.get("id") or "") or {"id": store.new_id(), "created": store.now()}
    for k in ("name", "handle", "description", "reference_id", "notes", "output_dir"):
        if k in data:
            c[k] = data[k]
    return store.upsert_channel(c)


@app.delete("/api/channels/{cid}")
def channel_delete(cid: str):
    store.save_channels([c for c in store.list_channels() if c["id"] != cid])
    return {"ok": True}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _proj(pid)
    p["job"] = {k: v for k, v in pipeline.job(pid).items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
    return p


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, data: dict = Body(...)):
    _busy(pid)
    p = _proj(pid)
    for k in ("name", "meta", "scenes", "script", "music", "channel_id", "music_prompt", "music_mode", "reference_id", "music_wish", "texts", "sfx_items"):
        if k in data:
            p[k] = data[k]
    if "style" in data:
        p["style"].update(data["style"])
    if data.get("reference_id"):
        try:
            p["style"] = {**store.DEFAULT_STYLE, "format": p["style"].get("format", store.DEFAULT_STYLE.get("format")),
                          **(store.load_ref(data["reference_id"]).get("style") or {})}
        except KeyError:
            pass
    return store.save_project(p)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _busy(pid)
    shutil.rmtree(store.project_dir(pid), ignore_errors=True)
    return {"ok": True}


@app.post("/api/projects/{pid}/script")
def load_script(pid: str, data: dict = Body(...)):
    _busy(pid)
    p = _proj(pid)
    text = data.get("script", "")
    meta, scenes = pipeline.smart_parse(text, p, log=lambda m: None)
    if not scenes:
        raise HTTPException(400, "В сценарии не найдено ни одной сцены")
    pipeline.apply_task(p, meta, scenes, text)
    return store.save_project(p)


@app.post("/api/projects/{pid}/stop")
def stop_job(pid: str):
    return pipeline.stop(pid)


@app.post("/api/projects/{pid}/run")
def run(pid: str, data: dict = Body(...)):
    _proj(pid)
    steps = [s for s in data.get("steps", ["parse", "voice", "media", "music", "render"])
             if s in ("parse", "media", "voice", "music", "render")]
    try:
        pipeline.start(pid, steps)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {k: v for k, v in pipeline.job(pid).items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}


@app.post("/api/projects/{pid}/to_claude")
def to_claude(pid: str, data: dict = Body(...)):
    """Отправить сценарий на разбор в чат Claude."""
    _busy(pid)
    p = _proj(pid)
    if not p.get("script", "").strip():
        raise HTTPException(400, "Сценарий пустой")
    p["auto_run"] = bool(data.get("auto_run", True))
    return inbox.send(p)


@app.get("/api/inbox")
def inbox_list():
    return [f.stem for f in inbox.pending()]


@app.get("/api/projects/{pid}/job")
def job(pid: str):
    return {k: v for k, v in pipeline.job(pid).items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}


@app.get("/api/projects/{pid}/timeline")
def timeline(pid: str):
    return pipeline.layout(_proj(pid))


@app.get("/api/sfx")
def sfx_list():
    return [{"name": f.name, "dur": pipeline._dur(f)} for f in pipeline._sfx_lib()]


@app.post("/api/sfx")
def sfx_upload(file: UploadFile = File(...)):
    store.SFX.mkdir(parents=True, exist_ok=True)
    import re
    stem = re.sub(r"[^\w\-]+", "_", Path(file.filename or "sfx").stem).strip("_")[:40] or store.new_id()
    if any(store.SFX.glob(stem + ".*")):
        stem += "_" + store.new_id()[:4]
    return {"name": _save_upload(file, store.SFX, name=stem).name}


@app.get("/api/sfx/{name}")
def sfx_file(name: str):
    if name == "_whoosh.wav":
        return FileResponse(ff_whoosh())
    return FileResponse(_safe(store.SFX, name))


def ff_whoosh():
    from .engine import ff
    return ff.make_whoosh(store.DATA / "_whoosh.wav")


@app.get("/api/projects/{pid}/file/{rel:path}")
def project_file(pid: str, rel: str):
    return FileResponse(_safe(store.project_dir(pid), rel))


@app.post("/api/projects/{pid}/scenes/{sid}/upload")
def upload_shot(pid: str, sid: str, file: UploadFile = File(...)):
    _busy(pid)
    p = _proj(pid)
    sc = next((s for s in p["scenes"] if s["id"] == sid), None)
    if not sc:
        raise HTTPException(404, "сцена не найдена")
    path = _save_upload(file, store.project_dir(pid) / "media")
    kind = "video" if path.suffix in (".mp4", ".mov", ".webm", ".mkv", ".avi") else "image"
    sc["shots"].append(pipeline.new_shot(p, path, {"type": kind, "source": "upload", "url": "file:" + path.name}))
    pipeline.apply_durations(sc, p["style"])
    return store.save_project(p)


@app.post("/api/projects/{pid}/scenes/{sid}/add")
def add_shot(pid: str, sid: str, c: dict = Body(...)):
    """Добавить кадр из результата поиска."""
    _busy(pid)
    p = _proj(pid)
    sc = next((s for s in p["scenes"] if s["id"] == sid), None)
    if not sc:
        raise HTTPException(404, "сцена не найдена")
    try:
        path = sources.download(c["url"], store.project_dir(pid) / "media", ".mp4" if c["type"] == "video" else "")
    except Exception as e:
        raise HTTPException(502, f"Не скачалось: {e}")
    shot = pipeline.new_shot(p, path, c)
    if c.get("replace"):
        idx = next((i for i, s in enumerate(sc["shots"]) if s["id"] == c["replace"]), None)
        if idx is not None:
            shot["dur"] = sc["shots"][idx]["dur"]
            sc["shots"][idx] = shot
    else:
        sc["shots"].append(shot)
        pipeline.apply_durations(sc, p["style"])
    return store.save_project(p)


@app.post("/api/projects/{pid}/scenes/{sid}/generate")
def gen_shot(pid: str, sid: str, data: dict = Body(...)):
    _busy(pid)
    p = _proj(pid)
    sc = next((s for s in p["scenes"] if s["id"] == sid), None)
    try:
        path = sources.google_image(data["prompt"], p["style"]["format"], store.get_settings(),
                                    store.project_dir(pid) / "media")
    except Exception as e:
        raise HTTPException(502, str(e))
    sc["shots"].append(pipeline.new_shot(p, path, {"type": "image", "source": "google", "url": "ai:" + path.name}))
    pipeline.apply_durations(sc, p["style"])
    return store.save_project(p)


@app.get("/api/search")
def search(q: str, kind: str = "video", fmt: str = "16:9", source: str = ""):
    s = store.get_settings()
    res, errs = sources.search(q, kind, fmt, s, only=[source] if source else None)
    return {"results": res, "errors": errs}


@app.post("/api/projects/{pid}/rebalance")
def rebalance(pid: str):
    _busy(pid)
    p = _proj(pid)
    for sc in p["scenes"]:
        pipeline.apply_durations(sc, p["style"])
    return store.save_project(p)


# ---------------- музыка

@app.post("/api/projects/{pid}/music/upload")
def music_upload(pid: str, file: UploadFile = File(...), mode: str = Form("background")):
    _busy(pid)
    p = _proj(pid)
    path = _save_upload(file, store.project_dir(pid) / "music")
    p["music"] = {"path": f"music/{path.name}", "mode": mode, "name": file.filename}
    return store.save_project(p)


_music_jobs = {}


@app.post("/api/projects/{pid}/music/suno")
def music_suno(pid: str, data: dict = Body(...)):
    _proj(pid)
    if _music_jobs.get(pid, {}).get("running"):
        raise HTTPException(409, "Трек уже генерируется")
    st = _music_jobs[pid] = {"running": True, "log": [], "error": None}

    def go():
        try:
            path = audio.suno_generate(data.get("prompt", ""), data.get("instrumental", True), store.get_settings(),
                                       store.project_dir(pid) / "music", log=st["log"].append)
            p = store.load_project(pid)
            p["music"] = {"path": f"music/{path.name}", "mode": data.get("mode", "background"), "name": path.name}
            store.save_project(p)
            st["log"].append("Трек готов ✅")
        except Exception as e:
            st["error"] = str(e)
        finally:
            st["running"] = False

    threading.Thread(target=go, daemon=True).start()
    return st


@app.get("/api/projects/{pid}/music/status")
def music_status(pid: str):
    return _music_jobs.get(pid, {"running": False, "log": [], "error": None})


# ---------------- референсы

@app.get("/api/refs")
def refs():
    return store.list_refs()


@app.post("/api/refs")
def upload_ref(file: UploadFile = File(...), name: str = Form("")):
    rid = store.new_id()
    d = store.REFS / rid
    path = _save_upload(file, d, "source")
    r = {"id": rid, "name": name or Path(file.filename or "референс").stem, "file": path.name,
         "created": store.now(), "notes": "", "status": "analyzing"}
    store.save_ref(r)

    def go():
        try:
            rr = reference.analyze(store.load_ref(rid))
            _wait_deep(rr)
            deep_ref(rid)
        except Exception as e:
            rr = store.load_ref(rid)
            rr["status"] = "error"
            rr["error"] = str(e)
            store.save_ref(rr)

    threading.Thread(target=go, daemon=True).start()
    return r


@app.post("/api/refs/youtube")
def ref_youtube(data: dict = Body(...)):
    """Референс по ссылке на YouTube-канал (или видео): качаем пару роликов и разбираем стиль."""
    url = (data.get("url") or "").strip()
    if "youtu" not in url:
        raise HTTPException(400, "Нужна ссылка на YouTube-канал, например https://www.youtube.com/@name")
    rid = store.new_id()
    r = {"id": rid, "name": data.get("name") or "YouTube", "url": url, "file": "", "created": store.now(),
         "notes": "", "status": "analyzing", "log": []}
    store.save_ref(r)
    cid = data.get("channel_id")

    def go():
        def log(m):
            rr = store.load_ref(rid)
            rr["log"] = (rr.get("log") or []) + [m]
            store.save_ref(rr)
        try:
            rr = reference.from_youtube(store.load_ref(rid), log=log)
            _wait_deep(rr)
            deep_ref(rid, cid)
            if cid and store.get_channel(cid):
                c = store.get_channel(cid)
                c["reference_id"] = rid
                store.upsert_channel(c)
        except Exception as e:
            rr = store.load_ref(rid)
            rr["status"] = "error"
            rr["error"] = str(e)
            store.save_ref(rr)

    threading.Thread(target=go, daemon=True).start()
    return r


def _wait_deep(r):
    """Лёгкий авто-замер стиль НЕ задаёт: референс ждёт полного разбора в чате Claude, стиль — только из его паспорта."""
    r.pop("style", None)
    r["status"] = "deep"
    store.save_ref(r)


def deep_ref(rid, cid=None):
    """Полный разбор референса в чате Claude: задача v_ в inbox → разбор, паспорт стиля и скил монтажа этого канала.
    Паспорт потом вливается в этот же референс (ref_for), так что проекты/каналы с ним собираются по монтажу канала."""
    r = store.load_ref(rid)
    url = (r.get("video_urls") or [r.get("url")])[0]
    upload = None
    if not url and r.get("file"):  # референс из файла — разбираем копию самого файла
        src = store.REFS / rid / r["file"]
        upload = store.REFS / rid / ("deep_" + src.name)
        shutil.copy2(src, upload)
    if not url and not upload:
        return None
    name = r.get("name") or rid
    tr = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ", "a b v g d e e zh z i y k l m n o p r s t u f h ts ch sh sch - y - e yu ya i i e g".split()))
    slug = re.sub(r"[^a-z0-9]+", "-", "".join(tr.get(c, c) for c in name.lower()).replace("-", " ")).strip("-") or rid
    note = (f"Это РЕФЕРЕНС-КАНАЛ «{name}» ({r.get('url')}), ref_id {rid}. Разбери ПОЛНОСТЬЮ по скилу razbor-video: "
            f"весь монтаж (длина и подача кадров, движение, переходы, плашки, графика, субтитры, звук), паспорт стиля, "
            f"недостающие шаблоны графики. И ОБЯЗАТЕЛЬНО создай скил монтажа этого канала "
            f"(.claude/skills/ref-{slug}/SKILL.md, в name/description, отчёте и паспорте называй его «{name}» — как его назвал пользователь, не по YouTube-каналу или ролику): как писать сценарий, сцены, графику и собирать ролик 1 в 1 как у них. "
            f"Другие ролики канала: {', '.join((r.get('video_urls') or [])[1:]) or '—'}. Сам ролик не делай.")
    a = videotask.create(url=url or "", note=note, upload=upload, channel_id=cid or "", ref_for=rid)
    r["deep_analysis"] = a["id"]
    store.save_ref(r)
    return a


@app.post("/api/refs/{rid}/deep")
def ref_deep(rid: str):
    """Отправить референс на полный разбор в чат Claude (паспорт + скил монтажа)."""
    a = deep_ref(rid)
    if not a:
        raise HTTPException(400, "У референса нет ни ссылки, ни файла ролика")
    return a


@app.post("/api/refs/{rid}/channel")
def ref_to_channel(rid: str, data: dict = Body(...)):
    """Назначить референс каналу (каналов может быть несколько)."""
    want = set(data.get("channel_ids") or [])
    for c in store.list_channels():
        if c["id"] in want:
            c["reference_id"] = rid
        elif c.get("reference_id") == rid:
            c["reference_id"] = None
        store.upsert_channel(c)
    return {"ok": True}


@app.patch("/api/refs/{rid}")
def patch_ref(rid: str, data: dict = Body(...)):
    r = store.load_ref(rid)
    for k in ("name", "notes"):
        if k in data:
            r[k] = data[k]
    if "style" in data:
        r.setdefault("style", dict(store.DEFAULT_STYLE)).update(data["style"])
    return store.save_ref(r)


@app.delete("/api/refs/{rid}")
def delete_ref(rid: str):
    shutil.rmtree(store.REFS / rid, ignore_errors=True)
    return {"ok": True}


@app.get("/api/refs/{rid}/file/{rel:path}")
def ref_file(rid: str, rel: str):
    return FileResponse(_safe(store.REFS / rid, rel))


@app.post("/api/projects/{pid}/apply_ref")
def apply_ref(pid: str, data: dict = Body(...)):
    p = _proj(pid)
    r = store.load_ref(data["reference_id"])
    videotask.sync_ref(data["reference_id"])
    r = store.load_ref(data["reference_id"])
    p["reference_id"] = r["id"]
    pipeline.ref_style(p, r)  # только стиль референса — стиль канала не подмешиваем
    return store.save_project(p)


# ---------------- музыкальные ролики (lofi / jazz)

def _mix(mid):
    try:
        return mix.load(mid)
    except KeyError:
        raise HTTPException(404, "Нет такой библиотеки")


def _mjob(mid, fn, *a):
    try:
        return mix.start(mid, fn, *a)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/mixes")
def mixes():
    return mix.list_all()


@app.post("/api/mixes")
def mix_new(data: dict = Body(...)):
    return mix.create(data.get("name", ""), data.get("channel_id"))


@app.get("/api/mixes/{mid}")
def mix_get(mid: str):
    return {**_mix(mid), "job": mix.job(mid)}


@app.patch("/api/mixes/{mid}")
def mix_patch(mid: str, data: dict = Body(...)):
    m = _mix(mid)
    for k in (*mix.DEFAULT, "name"):
        if k in data:
            m[k] = data[k]
    return mix.save(m)


@app.post("/api/mixes/{mid}/upload")
def mix_upload(mid: str, files: list[UploadFile] = File(...)):
    m = _mix(mid)
    for f in files:
        audio_ = Path(f.filename or "").suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")
        path = _save_upload(f, mix.mdir(mid) / ("music" if audio_ else "visuals"))
        mix.add_track(m, path, Path(f.filename).stem) if audio_ else mix.add_visual(m, path)
    return mix.save(m)


@app.delete("/api/mixes/{mid}/item/{iid}")
def mix_del_item(mid: str, iid: str):
    m = _mix(mid)
    for k in ("tracks", "visuals", "videos"):
        for it in [x for x in m[k] if x["id"] == iid]:
            (mix.mdir(mid) / it["file"]).unlink(missing_ok=True)
            m[k].remove(it)
    return mix.save(m)


@app.post("/api/mixes/{mid}/rate/{tid}")
def mix_rate(mid: str, tid: str, data: dict = Body(...)):
    return mix.rate(_mix(mid), tid, 1 if data.get("rate", 1) > 0 else -1, data.get("note", ""))


@app.post("/api/mixes/{mid}/suno")
def mix_suno(mid: str, data: dict = Body(...)):
    m = _mix(mid)
    return _mjob(mid, mix.gen_music, int(data.get("n", 4)), data.get("prompt") or m["music_prompt"])


@app.post("/api/mixes/{mid}/images")
def mix_images(mid: str, data: dict = Body(...)):
    m = _mix(mid)
    return _mjob(mid, mix.gen_images, int(data.get("n", 6)), data.get("prompt") or m["image_prompt"])


@app.post("/api/mixes/{mid}/ref")
def mix_ref(mid: str, data: dict = Body(...)):
    _mix(mid)
    return _mjob(mid, mix.ref_duration, data.get("url", ""))


@app.post("/api/mixes/{mid}/build")
def mix_build(mid: str):
    _mix(mid)
    return _mjob(mid, mix.build)


@app.get("/api/mixes/{mid}/file/{rel:path}")
def mix_file(mid: str, rel: str):
    return FileResponse(_safe(mix.mdir(mid), rel))


# ---------------- разбор чужих роликов через чат Claude

@app.get("/api/analyses")
def analyses():
    return videotask.list_all()


@app.post("/api/analyses")
def analysis_new(url: str = Form(""), note: str = Form(""), channel_id: str = Form(""), file: UploadFile = File(None)):
    if file is not None and file.filename:
        tmp = _save_upload(file, videotask.AN / "_upload")
        return videotask.create("", note, upload=tmp, channel_id=channel_id)
    if "http" not in url:
        raise HTTPException(400, "Вставь ссылку на ролик или выбери файл")
    return videotask.create(url, note, channel_id=channel_id)


@app.get("/api/analyses/{aid}")
def analysis_get(aid: str):
    try:
        return videotask.load(aid)
    except KeyError:
        raise HTTPException(404, "Нет такого разбора")


@app.post("/api/analyses/{aid}/retry")
def analysis_retry(aid: str):
    try:
        return videotask.retry(aid)
    except KeyError:
        raise HTTPException(404, "Нет такого разбора")


@app.delete("/api/analyses/{aid}")
def analysis_del(aid: str):
    shutil.rmtree(videotask.adir(aid), ignore_errors=True)
    (inbox.INBOX / f"v_{aid}.md").unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/analyses/{aid}/file/{rel:path}")
def analysis_file(aid: str, rel: str):
    return FileResponse(_safe(videotask.adir(aid), rel))


# ---------------- статика

@app.get("/")
def index():
    # ?v=<время изменения> — браузер всегда берёт свежие app.js / style.css
    from fastapi.responses import HTMLResponse
    v = int(max((STATIC / f).stat().st_mtime for f in ("app.js", "style.css")))
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace("/static/app.js", f"/static/app.js?v={v}").replace("/static/style.css", f"/static/style.css?v={v}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def free_port(start):
    for port in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def main():
    import uvicorn
    inbox.watch()
    if not shutil.which("ffmpeg"):
        print("!! Не найден ffmpeg. Установи его (см. README) и перезапусти.")
    nums = [a for a in sys.argv[1:] if a.isdigit()]
    want = int(nums[0]) if nums else 8777
    port = free_port(want)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  YT Studio запущен: {url}\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
