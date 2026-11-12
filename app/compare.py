"""Сверка собранного ролика с референсом: кадры «референс | наш» в одни и те же доли ролика + цифры.

  python -m app.compare <project_id> [--ref видео.mp4] [--n 12] [--at 12,65,300]

Пишет в data/projects/<pid>/compare/: sheet.jpg (пары кадров) и compare.txt (темп, цвет, графика).
Claude смотрит лист, правит паспорт стиля / сцены и пересобирает, пока кадры не совпадут.
"""
import argparse
import statistics
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

from .engine import ff, gfx, store, videotask

TW, TH = 640, 360


def _ref_video(p, ref_arg=None):
    rid = p.get("reference_id")
    try:
        r = store.load_ref(rid) if rid else {}
    except KeyError:
        r = {}
    if ref_arg:
        return Path(ref_arg), r
    if not r:
        raise SystemExit("У проекта нет референса — передай --ref видео.mp4")
    f = store.REFS / rid / (r.get("file") or "-")
    if not f.exists() and r.get("analysis_id"):
        a = videotask.load(r["analysis_id"])
        f = videotask.adir(a["id"]) / (a.get("file") or "-")
    if not f.exists():
        raise SystemExit(f"Видео референса {rid} не найдено — передай --ref")
    return f, r


def _grab(src, t, out):
    ff.run(["-ss", f"{t:.2f}", "-i", src, "-frames:v", 1, "-vf",
            f"scale={TW}:{TH}:force_original_aspect_ratio=decrease,pad={TW}:{TH}:-1:-1", "-q:v", 3, out])
    return out


def _ts(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def _look(path):
    """Яркость 0–255, насыщенность 0–255, тёплость (R−B) — средние по кадру."""
    im = Image.open(path).convert("RGB")
    r, g, b = ImageStat.Stat(im).mean
    s = ImageStat.Stat(im.convert("HSV")).mean[1]
    return {"bright": 0.299 * r + 0.587 * g + 0.114 * b, "sat": s, "warm": r - b}


def _avg(xs, k):
    return statistics.mean(x[k] for x in xs) if xs else 0


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Сверка ролика с референсом")
    ap.add_argument("pid")
    ap.add_argument("--ref", help="видео-референс (по умолчанию — референс проекта)")
    ap.add_argument("--n", type=int, default=12, help="сколько пар кадров")
    ap.add_argument("--at", help="моменты референса в секундах через запятую (ставятся в ту же долю нашего ролика)")
    a = ap.parse_args()

    p = store.load_project(a.pid)
    if not p.get("output"):
        raise SystemExit("Ролик ещё не собран (нет output)")
    pd = store.project_dir(p["id"])
    ours = pd / p["output"]
    ref, r = _ref_video(p, a.ref)
    od = pd / "compare"
    od.mkdir(exist_ok=True)
    for f in od.glob("*.jpg"):
        f.unlink()

    du_r = float(ff.video_info(ref).get("duration") or 0)
    du_o = float(ff.video_info(ours).get("duration") or 0)
    fr = [float(x) / du_r for x in a.at.split(",")] if a.at else [(k + 0.5) / a.n for k in range(a.n)]
    pairs = []
    for k, f in enumerate(fr):
        tr, to = f * du_r, f * du_o
        pairs.append((tr, _grab(ref, tr, od / f"r{k:02d}.jpg"), to, _grab(ours, to, od / f"o{k:02d}.jpg")))

    # лист: две колонки «РЕФЕРЕНС | НАШ», строка = один момент
    pad, lab = 6, 34
    sheet = Image.new("RGB", (TW * 2 + pad * 3, lab + len(pairs) * (TH + pad) + pad), (16, 16, 16))
    d = ImageDraw.Draw(sheet)
    fn = gfx.font(20, bold=True)
    d.text((pad + TW / 2, lab / 2), "РЕФЕРЕНС", font=fn, fill=(230, 230, 230), anchor="mm")
    d.text((pad * 2 + TW * 1.5, lab / 2), "НАШ", font=fn, fill=(230, 230, 230), anchor="mm")
    fs = gfx.font(18, bold=True)
    for i, (tr, fr_, to, fo) in enumerate(pairs):
        y = lab + i * (TH + pad)
        for x, f, t in ((pad, fr_, tr), (pad * 2 + TW, fo, to)):
            sheet.paste(Image.open(f), (x, y))
            d.text((x + 8, y + 6), f"{i + 1:02d} · {_ts(t)}", font=fs, fill=(255, 255, 255),
                   stroke_width=3, stroke_fill=(0, 0, 0))
    sheet.save(od / "sheet.jpg", quality=86)

    # цифры: темп (наш — меряем, референс — из разбора), цвет, графика
    shots, cuts = videotask._shots(od, ours, du_o)
    lens = [s["len"] for s in shots] or [0]
    ra = (r or {}).get("analysis") or {}
    lr, lo = [_look(x[1]) for x in pairs], [_look(x[3]) for x in pairs]
    kinds = {}
    for sc in p.get("scenes") or []:
        g = sc.get("graphic")
        if g:
            kinds[g["type"]] = kinds.get(g["type"], 0) + 1
    want = {(x.get("type") or "?"): x.get("count") for x in ((r or {}).get("passport") or {}).get("graphics") or []}
    rows = [
        ("длина, с", f"{du_r:.0f}", f"{du_o:.0f}"),
        ("медиана плана, с", ra.get("median_shot", "?"), f"{statistics.median(lens):.2f}"),
        ("склеек в минуту", ra.get("cuts_per_min", "?"), f"{len(cuts) / max(1e-6, du_o / 60):.1f}"),
        ("растворений, %", "", f"{100 * sum(c['kind'] == 'dissolve' for c in cuts) / max(1, len(cuts)):.0f}"),
        ("яркость (0–255)", f"{_avg(lr, 'bright'):.0f}", f"{_avg(lo, 'bright'):.0f}"),
        ("насыщенность (0–255)", f"{_avg(lr, 'sat'):.0f}", f"{_avg(lo, 'sat'):.0f}"),
        ("тёплость R−B", f"{_avg(lr, 'warm'):+.0f}", f"{_avg(lo, 'warm'):+.0f}"),
    ]
    for k in sorted(set(kinds) | set(want)):
        rows.append((f"графика {k}", want.get(k, "—"), kinds.get(k, 0)))
    txt = [f"# Сверка: {p['name']}", f"референс: {ref}", f"наш: {ours}", f"лист: {od / 'sheet.jpg'}", "",
           "| что | референс | наш |", "|---|---|---|", *[f"| {a_} | {b} | {c} |" for a_, b, c in rows], "",
           "| # | реф | наш | яркость реф/наш | насыщ. реф/наш |", "|---|---|---|---|---|",
           *[f"| {i + 1:02d} | {_ts(x[0])} | {_ts(x[2])} | {y['bright']:.0f}/{z['bright']:.0f} | {y['sat']:.0f}/{z['sat']:.0f} |"
             for i, (x, y, z) in enumerate(zip(pairs, lr, lo))]]
    (od / "compare.txt").write_text("\n".join(txt), encoding="utf-8")
    print("\n".join(txt))


if __name__ == "__main__":
    main()
