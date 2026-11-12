"""Сборка ролика из файла задачи без браузера.

  python -m app.cli задача.md [--channel "Название"] [--ref видео.mp4|id] [--steps voice,media,music,render]
"""
import argparse
import sys
from pathlib import Path

from .engine import pipeline, reference, store


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="YT Studio: задача → готовый ролик")
    ap.add_argument("files", nargs="+", help="файл задачи .md/.txt (+ можно видео-референс)")
    ap.add_argument("--channel", help="канал (имя); если нет — создастся")
    ap.add_argument("--ref", help="видео-референс (файл) или id/имя уже загруженного")
    ap.add_argument("--steps", default="parse,voice,media,music,render")
    a = ap.parse_args()

    vids = [f for f in a.files if Path(f).suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".avi")]
    texts = [f for f in a.files if f not in vids]
    if not texts:
        sys.exit("Нужен файл задачи .md")
    if vids and not a.ref:
        a.ref = vids[0]
    task = Path(texts[0])
    text = task.read_text(encoding="utf-8-sig")
    print(f"\n=== Шаг 0: РАЗБОР ЗАДАЧИ {task.name} ===")

    rid = a.ref
    if a.ref and Path(a.ref).is_file():
        import shutil
        rid = store.new_id()
        d = store.REFS / rid
        d.mkdir(parents=True, exist_ok=True)
        src = Path(a.ref)
        shutil.copy(src, d / ("source" + src.suffix.lower()))
        r = {"id": rid, "name": src.stem, "file": "source" + src.suffix.lower(),
             "created": store.now(), "notes": "", "status": "analyzing"}
        store.save_ref(r)
        print(f"Анализирую референс {src.name} …")
        r = reference.analyze(r)
        r["status"] = "ready"
        store.save_ref(r)
        print(f"  склеек: {len(r.get('cuts', []))}, средний кадр: {r.get('avg_shot')} c, формат {r.get('format')}")

    p = pipeline.project_from_task(text, a.channel, rid)
    ch = store.get_channel(p.get("channel_id") or "")
    print(f"Проект: {p['name']} (id {p['id']})  канал: {ch['name'] if ch else '—'}  "
          f"формат: {p['style']['format']}")

    steps = [s.strip() for s in a.steps.split(",") if s.strip()]
    pipeline._run(p["id"], steps, echo=print)
    j = pipeline.job(p["id"])
    if j["error"]:
        print("\n!! Не получилось:", j["error"])
        sys.exit(1)
    p = store.load_project(p["id"])
    out = store.project_dir(p["id"]) / (p.get("output") or "")
    print(f"\nГОТОВО ✅  Ролик: {out}")
    m = p["meta"]
    print(f"Название: {m.get('title')}\nТеги: {m.get('tags')}")


if __name__ == "__main__":
    main()
