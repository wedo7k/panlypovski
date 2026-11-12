"""Проверка, что в кадре то, о чём сцена.

1) Во время сборки (fetch_media): кусок с YouTube / стоковое видео смотрит Gemini (google_key) —
   3 кадра + текст сцены → «подходит / нет». Не подходит — кадр не ставим, берём следующий.
2) После подбора: листы data/projects/<id>/check/sNN.jpg — по строке на сцену: текст сцены и
   по 3 кадра (начало/середина/конец) каждого плана с его id. Их смотрит Claude и меняет
   неподходящие планы: GET /api/search → POST /api/projects/<id>/scenes/<sid>/add {..., replace: <shot id>}.

python -m app.engine.check <id проекта>   — нарисовать листы и вывести пути
"""
import base64
import io
import json
import sys
import tempfile
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from . import ff, store
from . import fonts

FW, FH = 192, 108          # один кадр на листе
ROWS_PER_SHEET = 6
TEXT_W = 360


def _font(size):
    return fonts.load(size, "arial.ttf")


def frames(path, kind, n=3):
    """n кадров ролика (начало/середина/конец) или сама картинка — список PIL.Image FW×FH."""
    path = Path(path)
    if kind != "video":
        im = Image.open(path).convert("RGB")
        im.thumbnail((FW, FH))
        return [im]
    dur = ff.video_info(path)["duration"] or 1
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for k in range(n):
            t = dur * (k + 0.5) / n
            f = Path(tmp) / f"{k}.jpg"
            try:
                ff.thumb(path, f, t=round(t, 2), width=FW)
                im = Image.open(f).convert("RGB")
                im.load()
                out.append(im)
            except Exception:
                pass
    return out


def _strip(ims):
    img = Image.new("RGB", (FW * max(1, len(ims)), FH), "black")
    for k, im in enumerate(ims):
        img.paste(im, (k * FW + (FW - im.width) // 2, (FH - im.height) // 2))
    return img


def ask_gemini(ims, scene_text, query, settings):
    """True/False — подходит ли кадр к сцене; None — проверить нечем (нет ключа, ошибка)."""
    key = settings.get("google_key")
    if not key or not ims:
        return None, ""
    buf = io.BytesIO()
    _strip(ims).save(buf, "JPEG", quality=80)
    ask = (f"These are 3 frames (start, middle, end) of a B-roll clip for a video narration.\n"
           f"Narration of this scene: «{scene_text[:500]}»\nSearch query used: «{query}»\n"
           "Does the clip visually fit this narration (right subject, place, era)? "
           "It does NOT fit if it shows: a talking head / presenter / vlogger, a title card, logo, "
           "subscribe screen, mostly text, a video game, or an unrelated subject. "
           'Answer only JSON: {"fits": true/false, "why": "short"}')
    model = settings.get("google_text_model") or "gemini-2.5-flash"
    try:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          params={"key": key}, timeout=60,
                          json={"contents": [{"parts": [
                              {"inline_data": {"mime_type": "image/jpeg",
                                               "data": base64.b64encode(buf.getvalue()).decode()}},
                              {"text": ask}]}],
                              "generationConfig": {"responseMimeType": "application/json", "temperature": 0}})
        if r.status_code != 200:
            return None, f"Gemini {r.status_code}"
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        a = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        return bool(a.get("fits")), str(a.get("why", ""))[:120]
    except Exception as e:
        return None, str(e)[:120]


def fits(path, kind, scene_text, query, settings):
    """(подходит?, почему) для кадра перед вставкой. Без ключа — (None, '')."""
    if not settings.get("google_key"):
        return None, ""
    try:
        ims = frames(path, kind)
    except Exception as e:
        return None, str(e)[:120]
    return ask_gemini(ims, scene_text, query, settings)


def sheets(pid):
    """Листы проверки: по строке на сцену — текст + кадры каждого плана. Возвращает пути."""
    p = store.load_project(pid)
    pd = store.project_dir(pid)
    out_dir = pd / "check"
    out_dir.mkdir(exist_ok=True)
    for f in out_dir.glob("s*.jpg"):
        f.unlink()
    f_txt, f_id = _font(15), _font(13)
    rows = []
    for i, sc in enumerate(p["scenes"]):
        shots = [s for s in sc["shots"] if not s.get("disabled") and s.get("media")
                 and s["media"]["type"] != "graphic"]
        tiles = []
        for s in shots:
            try:
                ims = frames(pd / s["media"]["path"], s["media"]["type"])
            except Exception:
                ims = []
            tiles.append((s, _strip(ims)))
        rows.append((i, sc, tiles))
    paths = []
    for page in range(0, len(rows), ROWS_PER_SHEET):
        chunk = rows[page:page + ROWS_PER_SHEET]
        # план — лента из 3 кадров; планы сцены переносятся по 2 в ряд
        line_h = FH + 20
        heights = [max(1, (len(t) + 1) // 2) * line_h + 10 for _, _, t in chunk]
        W = TEXT_W + 2 * (3 * FW + 10)
        img = Image.new("RGB", (W, sum(heights) + 10), "white")
        dr = ImageDraw.Draw(img)
        y = 5
        for (i, sc, tiles), h in zip(chunk, heights):
            dr.line([(0, y - 3), (W, y - 3)], fill="#999", width=1)
            txt = f"#{i + 1}  {sc.get('title') or ''}\n" + "\n".join(textwrap.wrap(sc["text"], 44)[:7])
            dr.multiline_text((6, y), txt, font=f_txt, fill="black", spacing=2)
            q = "; ".join(sc.get("queries") or [])[:60]
            dr.text((6, y + h - 22), q, font=f_id, fill="#666")
            for k, (s, strip) in enumerate(tiles):
                x = TEXT_W + (k % 2) * (3 * FW + 10)
                yy = y + (k // 2) * line_h
                img.paste(strip, (x, yy))
                dr.text((x + 2, yy + FH + 2), f"{s['id']}  {s['media']['source']}  {s['media'].get('url', '')[:40]}",
                        font=f_id, fill="#333")
            if not tiles:
                dr.text((TEXT_W, y + 10), "нет кадров", font=f_txt, fill="red")
            y += h
        out = out_dir / f"s{page // ROWS_PER_SHEET + 1:02d}.jpg"
        img.save(out, quality=82)
        paths.append(out)
    return paths


if __name__ == "__main__":
    for f in sheets(sys.argv[1]):
        print(f)
