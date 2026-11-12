"""Разбор сценария через чат с Claude.

Софт кладёт задачу в data/inbox/<id>.md → в чате Claude (открытом в папке софта) пишешь «разбери» →
Claude пишет ответ в data/inbox/<id>.answer.md → софт сам его подхватывает и (если надо) собирает ролик.
"""
import shutil
import threading
import time

from . import pipeline, store

INBOX = store.DATA / "inbox"
DONE = INBOX / "done"


def send(p):
    INBOX.mkdir(parents=True, exist_ok=True)
    ref = None
    if p.get("reference_id"):
        try:
            ref = store.load_ref(p["reference_id"])
        except KeyError:
            pass
    ch = store.get_channel(p.get("channel_id") or "")
    st = p["style"]
    lines = [
        f"<!-- project: {p['id']} -->",
        f"# Задача на разбор: {p['name']}",
        "",
        f"- Канал: {ch['name'] if ch else '—'}",
        f"- Формат: {st['format']}, типичная длина кадра {st.get('shot_len')} с, язык озвучки: {st.get('lang', 'ru')}",
        *([f"- Что хочет автор (учитывай!): {p['brief']}"] if p.get("brief") else []),
        f"- Пожелание к музыке: {p.get('music_wish') or '— (подбери под настроение)'}",
    ]
    if ref:
        a = ref.get("analysis") or {}
        lines.append(f"- Референс: {ref.get('name')} ({a.get('format')}, кадр ~{a.get('median_shot')} с)")
        if ref.get("titles"):
            lines.append("- Названия роликов канала-референса: " + " | ".join(ref["titles"][:12]))
        if ref.get("notes"):
            lines.append("- Заметки о стиле: " + ref["notes"])
    lines += ["", "## Сценарий", "", p.get("script", "")]
    (INBOX / f"{p['id']}.md").write_text("\n".join(lines), encoding="utf-8")
    p["claude_status"] = "waiting"
    return store.save_project(p)


def pending():
    if not INBOX.exists():
        return []
    return [f for f in INBOX.glob("*.md") if not f.name.endswith(".answer.md")]


def _take(answer):
    pid = answer.name.split(".")[0]
    try:
        p = store.load_project(pid)
    except KeyError:
        answer.unlink(missing_ok=True)
        return
    if pipeline.is_running(pid):
        return
    text = answer.read_text(encoding="utf-8-sig")
    meta, scenes = pipeline.parse_script(text)
    DONE.mkdir(parents=True, exist_ok=True)
    if not scenes:
        p["claude_status"] = "error"
        store.save_project(p)
        shutil.move(str(answer), DONE / f"{pid}.bad.{int(time.time())}.md")
        return
    script = p["script"]
    pipeline.apply_task(p, meta, scenes, script)
    p["parsed_script"] = script + "|" + (p.get("music_wish") or "")
    p["claude_status"] = "done"
    store.save_project(p)
    shutil.move(str(answer), DONE / f"{pid}.answer.md")
    task = INBOX / f"{pid}.md"
    if task.exists():
        shutil.move(str(task), DONE / f"{pid}.md")
    if p.get("auto_run"):
        pipeline.start(pid, ["voice", "media", "music", "render"])


def watch():
    def loop():
        while True:
            try:
                for a in INBOX.glob("*.answer.md") if INBOX.exists() else []:
                    if time.time() - a.stat().st_mtime > 1.5:  # файл дописан
                        _take(a)
            except Exception as e:
                print("inbox:", e)
            time.sleep(2)
    threading.Thread(target=loop, daemon=True).start()
