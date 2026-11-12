"""Куски с YouTube, выбранные глазами (в чате с Claude), а не вслепую.

1) python -m app.engine.yt_pick prepare <id проекта> [роликов на сцену]
   по каждой сцене ищет ролики, качает их в 240p и рисует лист-раскадровку с таймкодами:
   data/projects/<id>/yt_pick/sNN_<video>.jpg + candidates.json
2) Claude смотрит листы и пишет data/projects/<id>/yt_pick/picks.json:
   {"0": [["<video id>", 132], ["<video id>", 288]], "3": [...]}   (номер сцены с 0 -> старты кусков, с)
3) Сборка (fetch_media) берёт куски только из picks.json и вырезает их в нормальном качестве.
"""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import fonts, store, youtube

SHEET_MAX = 1500   # ролики длиннее 25 мин на лист не берём
TW, TH, COLS = 320, 180, 6


def pick_dir(pid):
    return store.project_dir(pid) / "yt_pick"


def load_picks(pid):
    f = pick_dir(pid) / "picks.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _low(vid, folder: Path):
    import yt_dlp
    out = folder / f"{vid}.low.mp4"
    if out.exists():
        return out
    with yt_dlp.YoutubeDL({"quiet": True, "noprogress": True, "js_runtimes": {"node": {}},
                           "format": "bv*[height<=240][ext=mp4]/bv*[height<=240]/worst",
                           "outtmpl": str(folder / f"{vid}.low.%(ext)s")}) as y:
        y.download([f"https://www.youtube.com/watch?v={vid}"])
    got = sorted(folder.glob(f"{vid}.low.*"))
    if got and got[0] != out:
        got[0].rename(out)
    return out


def sheet(v, folder: Path, title):
    """Лист: кадр каждые step секунд, под каждым таймкод."""
    low = _low(v["id"], folder)
    d = v["duration"]
    step = max(5, int(d / 60) // 5 * 5 or 5)
    tmp = folder / f"_{v['id']}"
    tmp.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(low), "-vf", f"fps=1/{step},scale={TW}:{TH}",
                    str(tmp / "%04d.jpg")], check=True)
    frames = sorted(tmp.glob("*.jpg"))
    rows = (len(frames) + COLS - 1) // COLS
    img = Image.new("RGB", (COLS * TW, rows * (TH + 22) + 40), "white")
    dr = ImageDraw.Draw(img)
    f = fonts.load(18, "arial.ttf")
    dr.text((8, 10), f"{v['id']}  {title[:90]}", font=f, fill="black")
    for k, p in enumerate(frames):
        t = k * step
        x, y = k % COLS * TW, 40 + k // COLS * (TH + 22)
        img.paste(Image.open(p), (x, y))
        dr.text((x + 4, y + TH + 1), f"{t // 60}:{t % 60:02d}  ({t}s)", font=f, fill="black")
        p.unlink()
    tmp.rmdir()
    out = folder / f"{v['id']}.jpg"
    img.save(out, quality=85)
    return out, step


def prepare(pid, per_scene=2, log=print):
    p = store.load_project(pid)
    folder = pick_dir(pid)
    folder.mkdir(parents=True, exist_ok=True)
    cands, seen = [], set()
    for i, sc in enumerate(p["scenes"]):
        q = (sc.get("queries") or [sc["text"][:80]])[0]
        try:
            vids = [v for v in youtube.search(q, per_scene + 2) if v["duration"] <= SHEET_MAX and v["id"] not in seen]
        except Exception as e:
            log(f"Сцена {i}: поиск не сработал - {e}")
            continue
        for v in vids[:per_scene]:
            seen.add(v["id"])
            try:
                sh, step = sheet(v, folder, v["title"])
            except Exception as e:
                log(f"Сцена {i}: {v['id']} не скачался - {str(e)[:120]}")
                continue
            cands.append({"scene": i, "query": q, "text": sc["text"][:300], "id": v["id"], "title": v["title"],
                          "duration": v["duration"], "step": step, "sheet": str(sh)})
            log(f"Сцена {i}: {sh}")
    (folder / "candidates.json").write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    return cands


if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        prepare(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2)
