"""Графические вставки как в референсе: заставка главы, цитата, карта с маршрутом, итоговая таблица,
счётчик, столбчатый и линейный график, таймлайн, сравнение, список. Цвета и шрифт — из темы паспорта стиля.

Каждая вставка — это последовательность кадров, нарисованных PIL и переданных в ffmpeg.
Описание вставки (dict) Claude пишет в ответе на задачу строками CHAPTER: / QUOTE: / MAP: / TABLE: / TITLE: …
Плашки поверх кадра (CAPTION: / LABEL: / PLATE: / …) — parse_overlay + render_overlay.
"""
import json
import math
import re
import subprocess
import threading
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import ff
from . import fonts

GOLD = (201, 164, 92)
GOLD_D = (120, 96, 52)
INK = (14, 12, 10)
CREAM = (238, 232, 220)
RED = (130, 26, 22)
ASSETS = Path(__file__).resolve().parent.parent / "assets"
GEO_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
           "ne_50m_admin_0_countries.geojson")


# ---------------------------------------------------------------- тема (из паспорта стиля)
# Паспорт стиля референса кладёт в style["gfx_theme"] цвета и шрифт графики — вставки рисуются «как у них».

DEFAULT_THEME = {
    "accent": "#C9A45C",    # главный цвет: цифры, линии, маршрут, столбцы
    "accent2": "#785F34",   # приглушённый акцент: разделители, второстепенные столбцы
    "text": "#EEE8DC",      # основной текст
    "bg": "#34302C",        # фон без фото (центр радиального градиента)
    "hl": "#821A16",        # подсветка строки в таблице/списке
    "map_sea": "#12181E", "map_land": "#605036", "map_line": "#AA8C58",
    "map_route": "",        # маршрут и стрелка (пусто — accent)
    "map_river": "#345C78",
    "font": "Georgia",      # семейство из FAMILIES или имя .ttf/.otf из системных шрифтов
    "label": "ГЛАВА",       # подпись над названием главы
    "dim": 0.7,             # насколько затемнять фото под графикой (0..1)
    # документальные вставки и плашки поверх кадра
    "felt": "#1F4D3A", "paper": "#EFEDE8", "charcoal": "#24221F", "green": "#2E7D4F", "ink": "#1C1A18",
    "ring": "#C9A45C", "plate_bg": "#EAE5E7", "plate_text": "#1C1A18", "name": "#B0221E", "hl_box": "#E9D9A6",
    "pill": "#1A1917", "note_bg": "#5A3A24",
    "chapter_style": "",    # "paper" — глава на светлой бумаге с картой, иначе — большая цифра
}
FAMILIES = {
    "georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "times": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "arial narrow": ("ARIALN.TTF", "ARIALNB.TTF", "ARIALNI.TTF", "ARIALNBI.TTF"),
    "impact": ("impact.ttf",) * 4,
    "verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "segoe ui": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "bahnschrift": ("bahnschrift.ttf",) * 4,
    "trebuchet": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf"),
    "calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
}
_TL = threading.local()


def _rgb(v):
    if isinstance(v, (list, tuple)):
        return tuple(int(x) for x in v[:3])
    v = str(v).lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def make_theme(t=None):
    th = dict(DEFAULT_THEME)
    th.update({k: v for k, v in (t or {}).items() if v not in (None, "")})
    return th


def _T():
    return getattr(_TL, "t", None) or DEFAULT_THEME


# Подписи графики на языке ролика: theme["lang"] ставит pipeline по тексту сцен (а не по паспорту референса).
L10N = {
    "ru": ("ГЛАВА", "км"), "uk": ("РОЗДІЛ", "км"), "en": ("CHAPTER", "km"), "de": ("KAPITEL", "km"),
    "fr": ("CHAPITRE", "km"), "es": ("CAPÍTULO", "km"), "it": ("CAPITOLO", "km"), "pt": ("CAPÍTULO", "km"),
    "pl": ("ROZDZIAŁ", "km"), "cs": ("KAPITOLA", "km"), "nl": ("HOOFDSTUK", "km"), "tr": ("BÖLÜM", "km"),
    "ja": ("第{n}章", "km"), "zh": ("第{n}章", "公里"), "ko": ("제{n}장", "km"),
}
CJK_FONTS = {  # шрифт с засечками для иероглифов (Georgia их не знает)
    "ja": ("yumin.ttf", "yumindb.ttf", "yumin.ttf", "yumindb.ttf"),
    "zh": ("simsun.ttc", "msyhbd.ttc", "simsun.ttc", "msyhbd.ttc"),
    "ko": ("batang.ttc", "malgunbd.ttf", "batang.ttc", "malgunbd.ttf"),
}


def _lang():
    return (_T().get("lang") or "").lower()[:2]


def chapter_label(num=""):
    """«CHAPTER 01» / «ГЛАВА 01» / «第1章» — на языке ролика."""
    lab = L10N.get(_lang(), (None,))[0] or _T().get("label") or ""
    if "{n}" in lab:
        return lab.replace("{n}", str(int(num)) if str(num).isdigit() else str(num))
    return (lab + " " + num).strip() if num else lab


def fmt_km(km):
    unit = L10N.get(_lang(), (None, "км"))[1]
    if _lang() in ("en", "ja", "zh", "ko"):
        return f"{km:,.0f} {unit}" if km >= 10 else f"{km:.1f} {unit}"
    return (f"{km:,.0f} {unit}".replace(",", " ") if km >= 10 else f"{km:.1f} {unit}".replace(".", ","))


def detect_lang(text, fallback="en"):
    """Язык текста по буквам: кириллица → ru/uk, кана → ja, хангыль → ko, иероглифы → zh, латиница → fallback (если он латинский)."""
    s = text or ""
    if re.search(r"[぀-ヿ]", s):
        return "ja"
    if re.search(r"[가-힯]", s):
        return "ko"
    if re.search(r"[一-鿿]", s):
        return "zh"
    cyr = len(re.findall(r"[А-Яа-яЁёІіЇїЄєҐґ]", s))
    lat = len(re.findall(r"[A-Za-z]", s))
    if cyr > lat:
        return "uk" if re.search(r"[ІіЇїЄєҐґ]", s) else "ru"
    fb = (fallback or "en").lower()[:2]
    return fb if fb in L10N and fb not in ("ru", "uk", "ja", "zh", "ko") else "en"


def _c(key):
    return _rgb(_T().get(key) or DEFAULT_THEME[key])


def _lift(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def font(size, bold=False, italic=False):
    fam = str(_T().get("font") or "Georgia")
    files = FAMILIES.get(fam.lower()) or ((fam,) * 4 if fam.lower().endswith((".ttf", ".otf", ".ttc")) else FAMILIES["georgia"])
    files = CJK_FONTS.get(_lang(), files)
    name = files[(1 if bold else 0) + (2 if italic else 0)]
    return fonts.load(size, name, files[0], "georgia.ttf", "times.ttf")


# ---------------------------------------------------------------- разбор строк ответа

def parse_line(s):
    """«CHAPTER: 01 | Яков» → dict. None, если строка не графика."""
    g = _parse_line(s)
    if g:  # в Georgia нет «→» — в подписях рисовался квадрат
        for k, v in g.items():
            if isinstance(v, str):
                g[k] = v.replace("→", "–")
            elif isinstance(v, list):
                g[k] = [x.replace("→", "–") if isinstance(x, str) else x for x in v]
    return g


def _parse_line(s):
    head, _, rest = s.partition(":")
    kind = head.strip().upper()
    rest = rest.strip()
    if kind == "CHAPTER":
        num, _, title = rest.partition("|")
        return {"type": "chapter", "num": num.strip(), "title": title.strip()}
    if kind == "QUOTE":
        txt, _, who = rest.partition("|")
        return {"type": "quote", "text": re.sub(r"[\[\]]", "", txt).strip().strip("«»\""), "who": who.strip()}
    if kind == "MAP":
        pts = []
        for part in rest.split(";"):
            bits = part.strip().split()
            if len(bits) >= 3:
                try:
                    pts.append({"name": " ".join(bits[:-2]), "lat": float(bits[-2]), "lon": float(bits[-1])})
                except ValueError:
                    pass
        return {"type": "map", "points": pts} if pts else None
    if kind == "TABLE":
        title, _, rows = rest.partition("|")
        rr = [[c.strip() for c in r.split("—")] for r in rows.split(";") if r.strip()]
        return {"type": "table", "title": title.strip(), "rows": rr}
    if kind == "COUNTER":  # COUNTER: $1,4 млрд | подпись   или   COUNTER: 1914 → 1918 | подпись
        val, _, label = rest.partition("|")
        a, b = re.split(r"\s*(?:→|->)\s*", val, maxsplit=1) if re.search(r"→|->", val) else ("", val)
        if not _num(b):
            return None
        return {"type": "counter", "value": b.strip(), "from": a.strip(), "label": label.strip()}
    if kind in ("CHART", "LINE", "TIMELINE", "COMPARE", "LIST"):
        title, bar, rows = rest.partition("|")
        if not bar:
            title, rows = "", rest
        rr = [[c.strip() for c in re.split(r"\s+[—–-]\s+", r.strip())] for r in rows.split(";") if r.strip()]
        if kind in ("CHART", "LINE"):
            rr = [[r[0], r[1]] for r in rr if len(r) >= 2 and _num(r[1])]
        if not rr:
            return None
        return {"type": kind.lower(), "title": title.strip(), "rows": rr}
    parts = [x.strip() for x in rest.split("|")]
    if kind == "TITLE" and rest:  # TITLE: A Castle Town | подпись
        return {"type": "title", "text": parts[0], "sub": parts[1] if len(parts) > 1 else ""}
    if kind == "DATE" and rest:  # DATE: JUNE | 2004
        return {"type": "date", "top": parts[0], "bottom": parts[1] if len(parts) > 1 else ""}
    if kind == "REWIND":  # REWIND: 2026 → 1990 | подпись
        a, b = re.split(r"\s*(?:→|->)\s*", parts[0], maxsplit=1) if re.search(r"→|->", parts[0]) else ("", parts[0])
        if not _num(b):
            return None
        return {"type": "rewind", "from": a.strip(), "value": b.strip(), "label": parts[1] if len(parts) > 1 else ""}
    if kind == "PERSON" and rest:  # PERSON: роль | ИМЯ
        return {"type": "person", "role": parts[0] if len(parts) > 1 else "", "name": parts[-1]}
    if kind == "PROFILE" and rest:  # PROFILE: ИМЯ | подпись
        return {"type": "profile", "name": parts[0], "sub": parts[1] if len(parts) > 1 else ""}
    if kind == "SPLIT" and rest:  # SPLIT: подпись 1 | подпись 2 [| подпись 3]
        return {"type": "split", "items": parts[:3]}
    if kind == "ASK" and rest:  # ASK: вопрос
        return {"type": "ask", "text": re.sub(r"[\[\]]", "", rest).strip("«»\"")}
    if kind == "CARD":  # CARD: [felt|grid |] подпись
        bg = parts[0].lower() if parts[0].lower() in ("felt", "grid", "paper") else ""
        return {"type": "card", "bg": bg, "text": parts[-1] if not bg else (parts[1] if len(parts) > 1 else "")}
    if kind == "PORTRAIT" and rest:  # PORTRAIT: [plate|polaroid|pinned |] Имя
        st = parts[0].lower() if parts[0].lower() in ("plate", "polaroid", "pinned") else ""
        return {"type": "portrait", "style": st or "plate", "text": parts[-1] if not st else (parts[1] if len(parts) > 1 else "")}
    if kind == "KICKER" and rest:  # KICKER: надзаголовок | Заголовок с [словом] | подпись
        return {"type": "kicker", "kicker": parts[0] if len(parts) > 1 else "", "text": parts[1] if len(parts) > 1 else parts[0],
                "sub": parts[2] if len(parts) > 2 else ""}
    if kind == "MYTH" and len(parts) >= 2:  # MYTH: [надзаголовок |] было (зачёркнуто) | стало
        return {"type": "myth", "kicker": parts[0] if len(parts) > 2 else "", "old": parts[-2], "new": parts[-1]}
    if kind == "CHECK":  # CHECK: надзаголовок | Заголовок | пункт; пункт
        items = [x.strip() for x in parts[-1].split(";") if x.strip()]
        return {"type": "check", "kicker": parts[0] if len(parts) > 2 else "", "text": parts[-2] if len(parts) > 1 else "",
                "items": items[:6]} if items else None
    if kind == "FACTS":  # FACTS: надзаголовок | Заголовок | ключ — значение; …
        rr = [[c.strip() for c in re.split(r"\s+[—–-]\s+", r.strip(), maxsplit=1)] for r in parts[-1].split(";") if r.strip()]
        rr = [r for r in rr if len(r) == 2]
        return {"type": "facts", "kicker": parts[0] if len(parts) > 2 else "", "text": parts[-2] if len(parts) > 1 else "",
                "rows": rr[:5]} if rr else None
    if kind == "STAMP" and rest:  # STAMP: GENEHMIGT | подпись
        return {"type": "stamp", "text": parts[0], "sub": parts[1] if len(parts) > 1 else ""}
    if kind == "CALENDAR" and rest:  # CALENDAR: 20 | SEPTEMBER 2019 | подпись [| надпись сверху]
        return {"type": "calendar", "day": parts[0], "month": parts[1] if len(parts) > 1 else "",
                "sub": parts[2] if len(parts) > 2 else "", "top": parts[3] if len(parts) > 3 else ""}
    if kind == "RING":  # RING: 8% → 50% | подпись
        a, b = re.split(r"\s*(?:→|->)\s*", parts[0], maxsplit=1) if re.search(r"→|->", parts[0]) else ("", parts[0])
        if not _num(b):
            return None
        return {"type": "ring", "from": a.strip(), "value": b.strip(), "label": parts[1] if len(parts) > 1 else ""}
    if kind == "SPAN" and rest:  # SPAN: 1955 → 2013 | подпись [| надзаголовок]
        a, b = re.split(r"\s*(?:→|->)\s*", parts[0], maxsplit=1) if re.search(r"→|->", parts[0]) else (parts[0], "")
        return {"type": "span", "a": a.strip(), "b": b.strip(), "sub": parts[1] if len(parts) > 1 else "",
                "kicker": parts[2] if len(parts) > 2 else ""}
    if kind == "RIBBON" and rest:  # RIBBON: DRAGON LADY | подпись
        return {"type": "ribbon", "text": parts[0], "sub": parts[1] if len(parts) > 1 else ""}
    if kind == "NEWS" and rest:  # NEWS: Газета | Заголовок | подзаголовок
        return {"type": "news", "paper": parts[0] if len(parts) > 1 else "", "text": parts[1] if len(parts) > 1 else parts[0],
                "sub": parts[2] if len(parts) > 2 else ""}
    if kind == "TELEGRAM" and rest:  # TELEGRAM: место, дата | ТЕКСТ STOP …
        return {"type": "telegram", "head": parts[0] if len(parts) > 1 else "", "text": parts[-1]}
    if kind in ("CIRCLES", "HEX") and rest:  # CIRCLES: подпись | подпись   HEX: центр | узел; узел; узел
        a, _, b = rest.partition("|")
        if kind == "HEX":
            return {"type": "hex", "center": a.strip(), "items": [x.strip() for x in b.split(";") if x.strip()]}
        return {"type": "circles", "items": [x.strip() for x in rest.split("|")]}
    if kind == "FACT" and rest:  # FACT: факт | подпись   (наклонная плашка «WUSSTEST DU?»)
        return {"type": "fact", "text": parts[0], "sub": parts[1] if len(parts) > 1 else ""}
    return None


DUR = {"chapter": 3.5, "quote": 4.5, "map": 7.0, "table": 7.0, "counter": 3.5, "chart": 6.0, "line": 6.0,
       "timeline": 7.0, "compare": 5.0, "list": 6.0, "title": 3.5, "date": 3.0, "rewind": 3.5, "person": 4.5,
       "profile": 4.5, "split": 5.0, "ask": 4.0, "card": 4.0, "portrait": 6.0,
       "kicker": 3.5, "myth": 5.0, "check": 6.0, "facts": 5.5, "stamp": 3.5, "calendar": 4.0, "ring": 4.0,
       "span": 4.0, "ribbon": 3.5, "news": 5.0, "telegram": 6.0, "fact": 5.0,
       "circles": 4.0, "hex": 5.5}
KINDS = tuple(k.upper() for k in DUR)  # строки ответа, которые парсер сценария считает графикой


def _num(s):
    """«$1,4 млрд» → (1.4, префикс, суффикс, знаков после запятой, разделитель тысяч, десятичный знак)."""
    m = re.search(r"\d[\d  .,]*\d|\d", s or "")
    if not m:
        return None
    tok = m[0]
    seps = [c for c in tok if c in "  .,"]
    dec, thou, decs = ",", "", 0
    if seps:
        last = max(tok.rfind(c) for c in ".,")
        tail = tok[last + 1:] if last >= 0 else ""
        if last >= 0 and len(tail) != 3:  # «1,4» / «2.75» — дробь; «1,400» — тысячи
            dec, decs = tok[last], len(tail)
            ip, fp = tok[:last], tail
        else:
            ip, fp = tok, ""
        thou = next((c for c in ip if not c.isdigit()), "")
        tok = re.sub(r"\D", "", ip) + ("." + fp if fp else "")
    return float(tok), s[:m.start()], s[m.end():], decs, thou, dec


def _fmt(v, decs, thou, dec):
    s = f"{v:,.{decs}f}"
    ip, _, fp = s.partition(".")
    ip = ip.replace(",", thou)
    return ip + (dec + fp if fp else "")


# ---------------------------------------------------------------- общие помощники

def _bg(W, H):
    g = Image.radial_gradient("L").resize((W, H))
    return Image.merge("RGB", [g.point(lambda v, c=c: int(c - v * (c - 10) / 255)) for c in _c('bg')])


def _cover(im, W, H):
    k = max(W / im.width, H / im.height)
    im = im.resize((max(W, round(im.width * k)), max(H, round(im.height * k))), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def _sepia(im):
    g = im.convert("L")
    return Image.merge("RGB", [g.point(lambda v, a=a: int(v * a)) for a in (1.0, 0.88, 0.7)])


def _ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _zoom(frame, z):
    """Медленный наезд с субпиксельной точностью."""
    W, H = frame.size
    s = 1 / z
    return frame.transform((W, H), Image.AFFINE, (s, 0, W * (1 - s) / 2, 0, s, H * (1 - s) / 2),
                           resample=Image.BILINEAR)


def _wrap(draw, text, fnt, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _pipe(out, W, H, fps, frames, norm="", enc=()):
    cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-vf", "setsar=1" + norm, *map(str, enc), str(out)]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=ff._NOWIN)
    try:
        for fr in frames:
            pr.stdin.write(fr.tobytes())
        pr.stdin.close()
        err = pr.stderr.read().decode("utf-8", "replace")
        if pr.wait() != 0:
            raise ff.FFError(err[-500:])
    finally:
        if pr.poll() is None:
            pr.kill()
    return out


# ---------------------------------------------------------------- 1. заставка главы

def chapter_frame(g, W, H, t, photo=None):
    im = _bg(W, H)
    num = g.get("num") or "01"
    fn = font(H * 0.62, bold=True)
    d = ImageDraw.Draw(im)
    bx = d.textbbox((0, 0), num, font=fn)
    tw, th = bx[2] - bx[0], bx[3] - bx[1]
    x, y = (W - tw) / 2 - bx[0], H * 0.40 - th / 2 - bx[1]
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).text((x, y), num, font=fn, fill=255)
    if photo is not None:
        fill = _sepia(_cover(photo, W, H)).point(lambda v: min(255, int(v * 1.15)))
    else:
        fill = Image.linear_gradient("L").resize((W, H)).point(lambda v: v)
        fill = Image.merge("RGB", [fill.point(lambda v, a=a, b=b: int(a + (b - a) * v / 255))
                                   for a, b in zip(_lift(_c('accent'), 1.15), _lift(_c('accent'), 0.6))])
    a = _ease(t / 0.25)
    im.paste(fill, (0, 0), mask.point(lambda v: int(v * a)))
    d = ImageDraw.Draw(im)
    lab = chapter_label(g.get("num") or "") if "{n}" in L10N.get(_lang(), ("",))[0] else chapter_label()
    fl = font(H * 0.028)
    spaced = " ".join(lab)
    d.text((W / 2, H * 0.76), spaced, font=fl, fill=tuple(int(c * a) for c in (150, 140, 125)), anchor="mm")
    ft = font(H * 0.06, bold=True)
    a2 = _ease((t - 0.15) / 0.25)
    d.text((W / 2, H * 0.84), g.get("title", ""), font=ft, fill=tuple(int(c * a2) for c in _c('text')), anchor="mm")
    return _zoom(im, 1 + 0.03 * t)


# ---------------------------------------------------------------- 2. цитата-ключ

def quote_frame(g, W, H, t, photo=None, dur=4.5):
    base = _cover(photo, W, H) if photo is not None else _bg(W, H)
    base = _zoom(base, 1 + 0.05 * t)
    dark = _ease(t / 0.15)
    im = Image.blend(base, Image.new("RGB", (W, H), (0, 0, 0)), 0.62 * dark)
    d = ImageDraw.Draw(im)
    fn = font(H * 0.066)
    lines = _wrap(d, g["text"], fn, W * 0.56)
    words = sum(len(l.split()) for l in lines)
    lh = H * 0.09
    y0 = H / 2 - lh * len(lines) / 2
    shown = (t - 0.12) / 0.55 * words  # слова печатаются за первые ~55% вставки
    k = 0
    for i, line in enumerate(lines):
        x = W * 0.12
        for w in line.split():
            a = _ease(shown - k)
            if a > 0:
                d.text((x, y0 + i * lh), w, font=fn, fill=tuple(int(c * a) for c in (245, 242, 235)))
            x += d.textlength(w + " ", font=fn)
            k += 1
    if g.get("who"):  # подпись: кто сказал (появляется после текста)
        a = _ease((t - 0.7) / 0.12)
        fs = font(H * 0.036)
        d.text((W * 0.12, y0 + lh * len(lines) + H * 0.02), g["who"], font=fs,
               fill=tuple(int(c * a) for c in _c('accent')))
    # золотая черта слева
    a = _ease((t - 0.08) / 0.2)
    d.rectangle((W * 0.12 - H * 0.03, y0 + lh * 0.1, W * 0.12 - H * 0.03 + 3, y0 + lh * (len(lines) - 0.15)),
                fill=tuple(int(c * a) for c in _c('accent')))
    return im


# ---------------------------------------------------------------- 3. карта с маршрутом

_GEO = None


def _geo():
    global _GEO
    if _GEO is None:
        f = ASSETS / "countries50.geojson"
        if not f.exists():
            ASSETS.mkdir(parents=True, exist_ok=True)
            r = requests.get(GEO_URL, timeout=120)
            r.raise_for_status()
            f.write_bytes(r.content)
        polys = []
        for feat in json.loads(f.read_text(encoding="utf-8"))["features"]:
            gm = feat["geometry"]
            parts = gm["coordinates"] if gm["type"] == "MultiPolygon" else [gm["coordinates"]]
            for poly in parts:
                polys.append(poly[0])
        _GEO = polys
    return _GEO


_RIV = None
RIV_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
           "ne_10m_rivers_lake_centerlines.geojson")


def _rivers():
    """Реки (Natural Earth 10m), кэш в assets; без сети — просто без рек."""
    global _RIV
    if _RIV is None:
        f = ASSETS / "rivers10.geojson"
        _RIV = []
        try:
            if not f.exists():
                r = requests.get(RIV_URL, timeout=120)
                r.raise_for_status()
                f.write_bytes(r.content)
            for feat in json.loads(f.read_text(encoding="utf-8"))["features"]:
                gm = feat["geometry"] or {}
                parts = gm.get("coordinates") or []
                if gm.get("type") == "LineString":
                    parts = [parts]
                rank = (feat.get("properties") or {}).get("scalerank", 9)
                for line in parts:
                    _RIV.append((rank, line))
        except Exception:
            pass
    return _RIV


def _km(a, b):
    la1, la2 = math.radians(a["lat"]), math.radians(b["lat"])
    dl = math.radians(b["lon"] - a["lon"])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _merc(lat, lon):
    lat = max(-80, min(80, lat))
    return lon, math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def map_base(g, W, H):
    """Камера карты: общий план региона и крупный план маршрута + страны в проекции."""
    pts = [_merc(p["lat"], p["lon"]) for p in g["points"]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    wide = max(max(xs) - min(xs), 16, (max(ys) - min(ys)) * W / H) * 1.6  # весь регион
    rs = max(max(xs) - min(xs), (max(ys) - min(ys)) * W / H)
    tight = min(wide, max(rs * 2.2, 0.02))  # маршрут всегда ~половина кадра, даже если точки в 1 км
    polys = []
    x0, x1 = cx - wide, cx + wide
    y0, y1 = cy - wide * H / W, cy + wide * H / W
    for ring in _geo():
        m = [_merc(lat, lon) for lon, lat in ring]
        if any(x0 < x < x1 and y0 < y < y1 for x, y in m[::4] + m[-1:]):
            polys.append(m)
    rivs = []
    for rank, line in _rivers():
        m = [_merc(lat, lon) for lon, lat in line]
        if any(x0 < x < x1 and y0 < y < y1 for x, y in m):
            rivs.append((rank, m))
    return {"polys": polys, "rivs": rivs, "c": (cx, cy), "wide": wide, "tight": tight, "pts": pts}, None


def _map_view(base, t):
    z = _ease((t - 0.04) / 0.4)  # 0..0.44 — наезд камеры
    return math.exp(math.log(base["wide"]) * (1 - z) + math.log(base["tight"]) * z)


def map_frame(base, P, g, W, H, t):
    S = 2
    RT = _rgb(_T().get('map_route') or _T().get('accent') or DEFAULT_THEME['accent'])
    span = _map_view(base, t)
    cx, cy = base["c"]

    def Q(x, y):
        return ((W / 2 + (x - cx) / span * W) * S, (H / 2 - (y - cy) / (span * H / W) * H) * S)

    im = Image.new("RGB", (W * S, H * S), _c('map_sea'))
    d = ImageDraw.Draw(im)
    lw = max(2, int(S * 1.5))
    for m in base["polys"]:
        xy = [Q(x, y) for x, y in m]
        d.polygon(xy, fill=_c('map_land'))
        d.line(xy + xy[:1], fill=_c('map_line'), width=lw)
    for rank, m in base.get("rivs", []):
        if rank <= 6 or span < 8:
            d.line([Q(x, y) for x, y in m], fill=_c('map_river'), width=max(lw, int(S * (4 if rank <= 2 else 2.5))),
                   joint="curve")
    step = 10 ** math.floor(math.log10(span / 3))  # сетка координат — карта не пустая на любом масштабе
    gx = math.floor((cx - span) / step) * step
    while gx < cx + span:
        X = Q(gx, cy)[0]
        d.line([(X, 0), (X, H * S)], fill=(132, 110, 74), width=S)
        gx += step
    gy = math.floor((cy - span) / step) * step
    while gy < cy + span:
        Y = Q(cx, gy)[1]
        d.line([(0, Y), (W * S, Y)], fill=(132, 110, 74), width=S)
        gy += step
    pts = [Q(x, y) for x, y in base["pts"]]
    n = max(1, len(pts) - 1)
    prog = _ease((t - 0.46) / 0.42) * n  # линия рисуется после наезда
    glow = Image.new("RGB", im.size, (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    lines = []
    for i in range(len(pts) - 1):
        k = max(0.0, min(1.0, prog - i))
        if k <= 0:
            break
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        dist = math.hypot(x2 - x1, y2 - y1)
        qx, qy = (x1 + x2) / 2, (y1 + y2) / 2 - dist * 0.22  # дуга вверх
        steps = 120
        arc = [((1 - u) ** 2 * x1 + 2 * (1 - u) * u * qx + u * u * x2,
                (1 - u) ** 2 * y1 + 2 * (1 - u) * u * qy + u * u * y2)
               for u in (s_ / steps for s_ in range(int(steps * k) + 1))]
        if len(arc) > 1:
            lines.append(arc)
            gd.line(arc, fill=RT, width=int(H * 0.022 * S), joint="curve")
    if lines:
        glow = glow.filter(ImageFilter.GaussianBlur(H * 0.012 * S))
        im = Image.composite(Image.new("RGB", im.size, RT), im, glow.convert("L").point(lambda v: int(v * 0.55)))
        d = ImageDraw.Draw(im)
        for arc in lines:
            w = int(H * 0.008 * S)
            d.line(arc, fill=RT, width=w, joint="curve")
            for x, y in (arc[0], arc[-1]):
                d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=RT)
        if prog >= n * 0.999:  # в конце — сколько км прошли
            km = sum(_km(g["points"][k], g["points"][k + 1]) for k in range(len(pts) - 1))
            txt = fmt_km(km)
            arc_ = max(lines, key=lambda a: math.hypot(a[-1][0] - a[0][0], a[-1][1] - a[0][1]))  # подпись — над самым длинным отрезком
            mid = arc_[len(arc_) // 2]
            fa = _ease((t - 0.88) / 0.08)
            d.text((mid[0], mid[1] - H * 0.04 * S), txt, font=font(H * 0.038 * S, bold=True),
                   fill=tuple(int(c * fa) for c in RT), anchor="mm", stroke_width=S * 2, stroke_fill=(20, 18, 14))
        for arc in lines:  # стрелка на конце каждого отрезка: едет вместе с линией от места к месту
            (ax, ay), (bx, by) = arc[max(0, len(arc) - 6)], arc[-1]
            ang = math.atan2(by - ay, bx - ax)
            L_, Wd = H * 0.038 * S, H * 0.022 * S
            off = -H * 0.011 * S * 2.6 if len(arc) > 120 else L_ * 0.35  # дошла — остриё упирается в кольцо места
            tip = (bx + math.cos(ang) * off, by + math.sin(ang) * off)
            back = (tip[0] - math.cos(ang) * L_, tip[1] - math.sin(ang) * L_)
            nx, ny = -math.sin(ang) * Wd, math.cos(ang) * Wd
            notch = (tip[0] - math.cos(ang) * L_ * 0.72, tip[1] - math.sin(ang) * L_ * 0.72)
            d.polygon([tip, (back[0] + nx, back[1] + ny), notch, (back[0] - nx, back[1] - ny)],
                      fill=RT, outline=(20, 18, 14))
    fl = font(H * 0.042 * S)
    for i, (X, Y) in enumerate(pts):
        a = _ease((t - 0.3) / 0.12) if i == 0 else _ease((prog - i + 0.85) * 4)
        if a <= 0:
            continue
        r = H * 0.011 * S
        d.ellipse((X - r * 2.2, Y - r * 2.2, X + r * 2.2, Y + r * 2.2), outline=tuple(int(c * a) for c in RT), width=2 * S)
        d.ellipse((X - r, Y - r, X + r, Y + r), fill=tuple(int(c * a) for c in _c('text')))
        o = pts[i + 1] if i + 1 < len(pts) else pts[i - 1] if i else (X - 1, Y)
        left = o[0] > X  # подпись — с той стороны, где нет соседней точки
        d.text((X - r * 3.2 if left else X + r * 3.2, Y), g["points"][i]["name"], font=fl,
               fill=tuple(int(c * a) for c in _c('text')), anchor="rm" if left else "lm",
               stroke_width=S * 2, stroke_fill=(20, 18, 14))
    im = im.resize((W, H), Image.LANCZOS)
    vg = Image.radial_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), (6, 5, 4)), im, vg.point(lambda v: int(min(255, max(0, v - 90) * 1.4))))


# ---------------------------------------------------------------- 4. итоговая таблица

def table_frame(g, W, H, t, photo=None):
    base = _cover(photo, W, H) if photo is not None else _bg(W, H)
    base = base.filter(ImageFilter.GaussianBlur(H // 40))
    im = Image.blend(base, Image.new("RGB", (W, H), (0, 0, 0)), 0.7)
    d = ImageDraw.Draw(im)
    a0 = _ease(t / 0.12)
    ft = font(H * 0.07, bold=True)
    d.text((W / 2, H * 0.17), g.get("title", "").upper(), font=ft, fill=tuple(int(c * a0) for c in _c('text')), anchor="mm")
    d.rectangle((W / 2 - W * 0.05, H * 0.235, W / 2 + W * 0.05, H * 0.235 + 2), fill=tuple(int(c * a0) for c in _c('accent')))
    rows = g.get("rows") or []
    n = max(1, len(rows))
    rh = min(H * 0.11, H * 0.6 / n)
    y0 = H * 0.3
    fr = font(rh * 0.42)
    cols = [W * 0.2, W * 0.58, W * 0.78]
    cur = (t - 0.15) / 0.75 * n  # какая строка сейчас подсвечена
    for i, row in enumerate(rows):
        a = _ease((cur - i) * 3 + 1)
        if a <= 0:
            continue
        y = y0 + i * rh
        hl = max(0.0, 1 - abs(cur - i - 0.5) * 2) if i < n else 0
        if hl > 0:
            band = Image.new("RGB", (int(W * 0.66), int(rh * 0.86)), _c('hl'))
            im.paste(Image.blend(im.crop((int(W * 0.17), int(y), int(W * 0.17) + band.width, int(y) + band.height)),
                                 band, 0.75 * hl), (int(W * 0.17), int(y)))
            d = ImageDraw.Draw(im)
        for c, x in zip(row, cols):
            d.text((x, y + rh * 0.43), c, font=fr, fill=tuple(int(v * a) for v in _c('text')), anchor="lm")
        d.line((W * 0.17, y + rh * 0.9, W * 0.83, y + rh * 0.9), fill=tuple(int(v * a * 0.5) for v in _c('accent2')), width=1)
    return im


# ---------------------------------------------------------------- 5–10. инфографика (счётчик, графики, таймлайн…)

def _backdrop(photo, W, H, t, blur=True):
    """Фон инфографики: фото сцены (размытое, затемнённое по теме) или фирменный градиент."""
    base = photo if photo is not None else _bg(W, H)
    if photo is not None and blur:
        base = base.filter(ImageFilter.GaussianBlur(H // 60))
    base = _zoom(base, 1 + 0.04 * t)
    dim = float(_T().get("dim", 0.7)) if photo is not None else 0.25
    return Image.blend(base, Image.new("RGB", (W, H), (0, 0, 0)), dim * _ease(t / 0.12))


def _fade(c, a):
    return tuple(int(v * max(0.0, min(1.0, a))) for v in c)


def _title(d, g, W, H, t, y=0.13):
    if not g.get("title"):
        return
    a = _ease(t / 0.12)
    d.text((W / 2, H * y), g["title"].upper(), font=font(H * 0.058, bold=True), fill=_fade(_c("text"), a), anchor="mm")
    d.rectangle((W / 2 - W * 0.04, H * (y + 0.055), W / 2 + W * 0.04, H * (y + 0.055) + 3), fill=_fade(_c("accent"), a))


def _fit(d, text, bold, size, maxw):
    f = font(size, bold=bold)
    while size > 10 and d.textlength(text, font=f) > maxw:
        size *= 0.92
        f = font(size, bold=bold)
    return f


def counter_frame(g, W, H, t, photo=None):
    im = _backdrop(photo, W, H, t)
    d = ImageDraw.Draw(im)
    n = _num(g["value"])
    v1, pre, suf, decs, thou, dec = n
    v0 = (_num(g.get("from")) or (0,))[0]
    k = 1 - (1 - max(0.0, min(1.0, (t - 0.05) / 0.55))) ** 3  # быстро в начале, плавно к концу
    txt = pre + _fmt(v0 + (v1 - v0) * k, decs, thou, dec) + suf
    full = pre + _fmt(v1, decs, thou, dec) + suf
    fn = _fit(d, full, True, H * 0.24, W * 0.84)
    a = _ease(t / 0.1)
    y = H * (0.44 if g.get("label") else 0.5)
    d.text((W / 2, y), txt, font=fn, fill=_fade(_c("accent"), a), anchor="mm")
    lw = W * 0.18 * _ease((t - 0.3) / 0.3)
    yl = y + H * 0.165
    d.rectangle((W / 2 - lw, yl, W / 2 + lw, yl + 3), fill=_c("accent2"))
    if g.get("label"):
        a2 = _ease((t - 0.45) / 0.2)
        fl = font(H * 0.05)
        for i, line in enumerate(_wrap(d, g["label"], fl, W * 0.7)[:3]):
            d.text((W / 2, yl + H * (0.07 + i * 0.065)), line, font=fl, fill=_fade(_c("text"), a2), anchor="mm")
    return im


def chart_frame(g, W, H, t, photo=None):
    """Столбцы растут по очереди; самый большой — акцентным цветом."""
    im = _backdrop(photo, W, H, t)
    d = ImageDraw.Draw(im)
    _title(d, g, W, H, t)
    rows = g["rows"]
    vals = [_num(r[1])[0] for r in rows]
    top = max(vals) or 1
    n = len(rows)
    x0, x1, yb, yt = W * 0.12, W * 0.88, H * 0.8, H * 0.3
    slot = (x1 - x0) / n
    bw = slot * 0.56
    d.line((x0, yb, x1, yb), fill=_c("accent2"), width=3)
    fl = _fit(d, max((r[0] for r in rows), key=len), False, H * 0.036, slot * 0.95)
    fv = font(H * 0.042, bold=True)
    imax = vals.index(top)
    for i, (r, v) in enumerate(zip(rows, vals)):
        k = _ease((t - 0.12 - i * 0.5 / n) / 0.3)
        cx = x0 + slot * (i + 0.5)
        h = (yb - yt) * v / top * k
        col = _c("accent") if i == imax else _lift(_c("accent"), 0.62)
        if h > 1:
            d.rectangle((cx - bw / 2, yb - h, cx + bw / 2, yb), fill=col)
        a = _ease((t - 0.1 - i * 0.5 / n) / 0.15)
        d.text((cx, yb + H * 0.045), r[0], font=fl, fill=_fade(_c("text"), a), anchor="mm")
        if k > 0.6:
            d.text((cx, yb - h - H * 0.035), r[1], font=fv, fill=_fade(_c("text"), (k - 0.6) / 0.4), anchor="mm")
    return im


def line_frame(g, W, H, t, photo=None):
    """Линия рисуется слева направо, точки с подписями значений."""
    im = _backdrop(photo, W, H, t)
    d = ImageDraw.Draw(im)
    _title(d, g, W, H, t)
    rows = g["rows"]
    vals = [_num(r[1])[0] for r in rows]
    lo, hi = min(min(vals), 0), max(vals) or 1
    n = len(rows)
    x0, x1, yb, yt = W * 0.12, W * 0.88, H * 0.8, H * 0.32
    P = [(x0 + (x1 - x0) * (i / max(1, n - 1) if n > 1 else 0.5), yb - (yb - yt) * (v - lo) / ((hi - lo) or 1))
         for i, v in enumerate(vals)]
    d.line((x0, yb, x1, yb), fill=_c("accent2"), width=3)
    fl = _fit(d, max((r[0] for r in rows), key=len), False, H * 0.034, (x1 - x0) / max(1, n) * 0.95)
    for (x, _), r in zip(P, rows):
        d.text((x, yb + H * 0.045), r[0], font=fl, fill=_fade(_c("text"), _ease(t / 0.15)), anchor="mm")
    prog = _ease((t - 0.12) / 0.6) * (n - 1)
    pts = P[:int(prog) + 1]
    if int(prog) < n - 1:
        f = prog - int(prog)
        (ax, ay), (bx, by) = P[int(prog)], P[int(prog) + 1]
        pts = pts + [(ax + (bx - ax) * f, ay + (by - ay) * f)]
    if len(pts) > 1:
        area = Image.new("L", (W, H), 0)
        ImageDraw.Draw(area).polygon(pts + [(pts[-1][0], yb), (pts[0][0], yb)], fill=60)
        im.paste(Image.new("RGB", (W, H), _c("accent")), (0, 0), area)
        d = ImageDraw.Draw(im)
        d.line(pts, fill=_c("accent"), width=max(3, int(H * 0.007)), joint="curve")
    fv = font(H * 0.04, bold=True)
    r0 = H * 0.011
    for i, ((x, y), r) in enumerate(zip(P, rows)):
        a = _ease((prog - i + 0.3) * 3)
        if a <= 0:
            continue
        d.ellipse((x - r0, y - r0, x + r0, y + r0), fill=_fade(_c("text"), a), outline=_c("accent"), width=2)
        d.text((x, y - H * 0.045), r[1], font=fv, fill=_fade(_c("text"), a), anchor="mm")
    return im


def timeline_frame(g, W, H, t, photo=None):
    """Линия времени: точки загораются по очереди, при многих точках камера едет вдоль линии."""
    im = _backdrop(photo, W, H, t)
    d = ImageDraw.Draw(im)
    _title(d, g, W, H, t)
    rows = g["rows"]
    n = len(rows)
    span = W * 0.7
    step = max(span / max(1, n - 1), W * 0.16) if n > 1 else 0
    total = step * (n - 1)
    cur = max(0.0, (t - 0.1) / 0.8) * n  # 0..n — сколько точек уже «пройдено»
    left = W * 0.15 if n > 1 else W / 2
    if total > span:  # едем так, чтобы активная точка была у центра
        left = max(W * 0.15 - (total - span), min(W * 0.15, W / 2 - min(cur, n - 1) * step))
    y = H * 0.56
    xe = left + min(total, step * cur)
    d.line((max(0, left - W * 0.05), y, min(W, left + total + W * 0.05), y), fill=_c("accent2"), width=3)
    if xe > left:
        d.line((left, y, xe, y), fill=_c("accent"), width=5)
    fd = _fit(d, max((r[0] for r in rows), key=len), True, H * 0.06, min(step * 0.9, W * 0.27) if n > 1 else W * 0.6)  # крайние даты не вылезают за кадр
    fl = font(H * 0.036)
    r0 = H * 0.014
    for i, row in enumerate(rows):
        x = left + step * i
        a = _ease((cur - i) * 2.5)
        if x < -W * 0.2 or x > W * 1.2:
            continue
        on = 0 <= cur - i < 1
        R = r0 * (1.5 if on else 1)
        d.ellipse((x - R, y - R, x + R, y + R), fill=_c("accent") if a > 0 else _c("accent2"),
                  outline=_c("text") if on else None, width=3)
        if a <= 0:
            continue
        d.text((x, y - H * 0.07), row[0], font=fd, fill=_fade(_c("accent"), a), anchor="mm")
        for k, line in enumerate(_wrap(d, " — ".join(row[1:]), fl, min(step, W * 0.3) * 0.92)[:3]):
            d.text((x, y + H * (0.07 + k * 0.05)), line, font=fl, fill=_fade(_c("text"), a), anchor="mm")
    return im


def compare_frame(g, W, H, t, photo=None):
    """Две (три) колонки: имя — большое значение — пояснение; между ними черта и «VS»."""
    im = _backdrop(photo, W, H, t)
    d = ImageDraw.Draw(im)
    _title(d, g, W, H, t)
    rows = g["rows"][:3]
    n = len(rows)
    colw = W * 0.8 / n
    fv = _fit(d, max((r[1] if len(r) > 1 else "" for r in rows), key=len) or "0", True, H * 0.13, colw * 0.78)
    fn = font(H * 0.05, bold=True)
    fs = font(H * 0.036)
    for i, r in enumerate(rows):
        cx = W * 0.1 + colw * (i + 0.5)
        a = _ease((t - 0.1 - i * 0.18) / 0.2)
        dx = (1 - a) * W * 0.04 * (-1 if i == 0 else 1)
        d.text((cx + dx, H * 0.36), r[0], font=fn, fill=_fade(_c("text"), a), anchor="mm")
        if len(r) > 1:
            d.text((cx + dx, H * 0.53), r[1], font=fv, fill=_fade(_c("accent"), a), anchor="mm")
        if len(r) > 2:
            for k, line in enumerate(_wrap(d, " — ".join(r[2:]), fs, colw * 0.8)[:2]):
                d.text((cx + dx, H * (0.68 + k * 0.05)), line, font=fs, fill=_fade(_lift(_c("text"), 0.75), a), anchor="mm")
        if i:
            xs = W * 0.1 + colw * i
            k = _ease((t - 0.05) / 0.25)
            d.line((xs, H * 0.5 - H * 0.22 * k, xs, H * 0.5 + H * 0.22 * k), fill=_c("accent2"), width=3)
            if n == 2:
                d.text((xs, H * 0.36), "VS", font=font(H * 0.045, bold=True), fill=_fade(_c("text"), k), anchor="mm",
                       stroke_width=6, stroke_fill=(0, 0, 0))
    return im


def list_frame(g, W, H, t, photo=None):
    """Нумерованный список: пункты появляются по очереди, текущий — с подсветкой."""
    im = _backdrop(photo, W, H, t)
    d = ImageDraw.Draw(im)
    _title(d, g, W, H, t)
    rows = g["rows"]
    n = len(rows)
    rh = min(H * 0.11, H * 0.62 / n)
    y0 = H * 0.27 + (H * 0.62 - rh * n) / 2
    fr = font(rh * 0.46)
    fnum = font(rh * 0.5, bold=True)
    cur = (t - 0.12) / 0.78 * n
    for i, row in enumerate(rows):
        a = _ease((cur - i) * 3)
        if a <= 0:
            continue
        y = y0 + i * rh
        on = 0 <= cur - i < 1
        if on:
            band = Image.new("RGB", (int(W * 0.66), int(rh * 0.86)), _c("hl"))
            box = (int(W * 0.17), int(y))
            im.paste(Image.blend(im.crop((*box, box[0] + band.width, box[1] + band.height)), band, 0.75), box)
            d = ImageDraw.Draw(im)
        dx = (1 - a) * W * 0.03
        d.text((W * 0.2 + dx, y + rh * 0.43), f"{i + 1:02d}", font=fnum, fill=_fade(_c("accent"), a), anchor="lm")
        tone = 1.0 if on or cur >= n else 0.72
        d.text((W * 0.28 + dx, y + rh * 0.43), " — ".join(row), font=fr, fill=_fade(_lift(_c("text"), tone), a), anchor="lm")
    return im


# ---------------------------------------------------------------- 11–19. документальные вставки (стиль «архив»)
# Титр, дата, отмотка лет, карточка человека, профиль на бумаге, фото-карточки на зелёном, вопрос на сукне,
# фото-карточка на миллиметровке/сукне, глава на бумаге с картой. Рисуются на прозрачном слое и сводятся разом.

_BGC = {}


def _pp(i, photo):
    """i-е фото сцены (по кругу); нет — первое."""
    ph = getattr(_TL, "photos", None) or []
    return ph[i % len(ph)] if ph else photo


def _vignette(im, k=0.5):
    W, H = im.size
    v = Image.radial_gradient("L").resize((W, H)).point(lambda x: int(255 - min(255, x * 1.15) * k))
    return ImageChops.multiply(im, Image.merge("RGB", [v] * 3))


def _tex(kind, W, H):
    """Фоны: сукно, тёмно-зелёный градиент, бумага, миллиметровка, уголь (кэш)."""
    key = (kind, W, H, json.dumps(_T(), sort_keys=True, default=str))
    if key not in _BGC:
        if kind in ("felt", "green", "charcoal"):
            c = _c("charcoal" if kind == "charcoal" else "felt")
            n = Image.effect_noise((W // 2, H // 2), 60 if kind == "felt" else 25).resize((W, H))
            amp = 0.22 if kind == "felt" else 0.08
            im = Image.merge("RGB", [n.point(lambda v, a=a: max(0, min(255, int(a + (v - 128) * amp)))) for a in c])
            im = _vignette(im, 0.55 if kind != "felt" else 0.4)
        else:
            c = _c("paper")
            n = Image.effect_noise((W // 2, H // 2), 30).resize((W, H))
            im = Image.merge("RGB", [n.point(lambda v, a=a: max(0, min(255, int(a + (v - 128) * 0.05)))) for a in c])
            if kind == "grid":
                d = ImageDraw.Draw(im)
                s = H / 18
                lc = _lift(c, 0.8)
                for i in range(int(W / s) + 2):
                    d.line((i * s, 0, i * s, H), fill=lc, width=max(1, H // 540))
                for i in range(int(H / s) + 2):
                    d.line((0, i * s, W, i * s), fill=lc, width=max(1, H // 540))
            im = _vignette(im, 0.18)
        _BGC[key] = im
    return _BGC[key].copy()


def _layer(W, H):
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def _comp(im, L):
    return Image.alpha_composite(im.convert("RGBA"), L).convert("RGB")


def _rgba(c, a):
    return (*c[:3], int(255 * max(0.0, min(1.0, a))))


def _rounded(photo, w, h, r):
    ph = _cover(photo, int(w), int(h)).convert("RGBA")
    m = Image.new("L", ph.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, ph.width - 1, ph.height - 1), radius=int(r), fill=255)
    ph.putalpha(m)
    return ph


def _shadow(L, box, r, a, blur):
    W, H = L.size
    s = _layer(W, H)
    ImageDraw.Draw(s).rounded_rectangle(box, radius=int(r), fill=(0, 0, 0, int(150 * a)))
    return Image.alpha_composite(L, s.filter(ImageFilter.GaussianBlur(blur)))


def _paste(L, im, xy, a):
    """Вставить RGBA-картинку с общей прозрачностью a."""
    if a <= 0:
        return L
    if a < 1:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
    L.alpha_composite(im, (int(xy[0]), int(xy[1])))
    return L


def _spaced(d, xy, text, fnt, fill, sp, anchor="m"):
    """Текст с разрядкой; anchor: m — по центру x, l — от x."""
    ws = [d.textlength(ch, font=fnt) for ch in text]
    total = sum(ws) + sp * max(0, len(text) - 1)
    x = xy[0] - (total / 2 if anchor == "m" else 0)
    for ch, w in zip(text, ws):
        d.text((x, xy[1]), ch, font=fnt, fill=fill, anchor="lm")
        x += w + sp
    return total


def title_frame(g, W, H, t, photo=None):
    """Большой титр по центру поверх затемнённого кадра, буквы проявляются слева направо."""
    im = _backdrop(photo, W, H, t, blur=False)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    txt = g.get("text", "")
    fn = _fit(d, txt, False, H * 0.13, W * 0.82)
    tw = d.textlength(txt, font=fn)
    x0 = (W - tw) / 2
    n = max(1, len(txt))
    p = (t - 0.06) / 0.5 * (n + 6)
    for i, ch in enumerate(txt):
        a = _ease((p - i) / 6)
        if a > 0:
            d.text((x0 + d.textlength(txt[:i], font=fn), H * 0.5 + (1 - a) * H * 0.025), ch, font=fn,
                   fill=_rgba(_c("text"), a), anchor="lm")
    if g.get("sub"):
        a = _ease((t - 0.45) / 0.2)
        _spaced(d, (W / 2, H * 0.6), g["sub"].upper(), font(H * 0.032), _rgba(_c("accent"), a), H * 0.008)
    return _comp(im, L)


def date_frame(g, W, H, t, photo=None):
    """Дата-титр: «— JUNE —» тонкими засечками и разреженный год ниже; буквы расходятся."""
    im = _backdrop(photo, W, H, t, blur=False)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    top, bot = g.get("top", "").upper(), g.get("bottom", "")
    a1, a2 = _ease((t - 0.05) / 0.25), _ease((t - 0.2) / 0.25)
    y = H * float(g.get("y", 0.47))
    fn = font(H * 0.12)
    sp = H * (0.01 + 0.012 * t)
    w = _spaced(d, (W / 2, y), top, fn, _rgba(_c("text"), a1 * 0.92), sp)
    ln = W * 0.05 * _ease((t - 0.15) / 0.3)
    for s in (-1, 1):
        x = W / 2 + s * (w / 2 + H * 0.035)
        d.line((x, y, x + s * ln, y), fill=_rgba(_c("text"), a1 * 0.7), width=max(2, H // 400))
    if bot:
        _spaced(d, (W / 2, y + H * 0.12), bot, font(H * 0.06), _rgba(_c("text"), a2 * 0.8), H * (0.025 + 0.01 * t))
    return _comp(im, L)


def rewind_frame(g, W, H, t, photo=None):
    """Отмотка лет: большой год «докручивается» 2026 → 1990, точка едет по тонкой линии."""
    im = _backdrop(photo, W, H, t, blur=True)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    v0, v1 = (_num(g.get("from")) or (0,))[0], _num(g["value"])[0]
    k = 1 - (1 - max(0.0, min(1.0, (t - 0.08) / 0.62))) ** 3
    a = _ease(t / 0.12)
    d.text((W / 2, H * 0.44), str(int(round(v0 + (v1 - v0) * k))), font=font(H * 0.2), fill=_rgba(_c("text"), a),
           anchor="mm")
    y = H * 0.6
    d.line((W * 0.15, y, W * 0.85, y), fill=_rgba(_c("text"), a * 0.45), width=max(2, H // 500))
    x = W * 0.85 - W * 0.7 * k if v1 < v0 else W * 0.15 + W * 0.7 * k
    r = H * 0.011
    d.ellipse((x - r, y - r, x + r, y + r), fill=_rgba(_c("accent"), a))
    if g.get("label"):
        _spaced(d, (W / 2, y + H * 0.07), g["label"].upper(), font(H * 0.034), _rgba(_c("text"), _ease((t - 0.5) / 0.2) * 0.85),
                H * 0.008)
    return _comp(im, L)


def person_frame(g, W, H, t, photo=None):
    """Знакомство с человеком: уголь, фото в круге с золотым кольцом слева, роль и крупное имя справа."""
    im = _tex("charcoal", W, H)
    L = _layer(W, H)
    R = H * 0.29
    cx, cy = W * 0.29, H * 0.5
    k = _ease(t / 0.22)
    ph = _pp(0, photo)
    if ph is not None and k > 0:
        r = max(2, int(R * (0.85 + 0.15 * k)))
        c = _cover(ph, 2 * r, 2 * r).convert("RGBA")
        m = Image.new("L", c.size, 0)
        ImageDraw.Draw(m).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
        c.putalpha(m)
        _paste(L, c, (cx - r, cy - r), k)
    d = ImageDraw.Draw(L)
    ring = _ease((t - 0.08) / 0.35)
    if ring > 0:
        rr = R + H * 0.018
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), -90, -90 + 360 * ring, fill=_rgba(_c("ring"), 1), width=max(3, int(H * 0.009)))
    x = W * 0.52
    role = g.get("role", "").upper()
    n = int(max(0.0, (t - 0.25) / 0.3) * len(role) + 0.999)
    _spaced(d, (x, H * 0.4), role[:n], font(H * 0.034), _rgba(_lift(_c("text"), 0.85), 1), H * 0.006, anchor="l")
    a = _ease((t - 0.45) / 0.22)
    name = g.get("name", "")
    fn = _fit(d, name, True, H * 0.1, W * 0.44)
    d.text((x + (1 - a) * W * 0.03, H * 0.52), name, font=fn, fill=_rgba(_c("accent"), a), anchor="lm")
    return _comp(im, L)


def profile_frame(g, W, H, t, photo=None):
    """Профиль на бумаге: скруглённое фото слева, имя жирным курсивом, зелёная черта, подпись капсом."""
    im = _tex("paper", W, H)
    im = _zoom(im, 1 + 0.02 * t)
    L = _layer(W, H)
    a = _ease(t / 0.2)
    ph = _pp(0, photo)
    box = (W * 0.07, H * 0.17, W * 0.45, H * 0.83)
    dy = (1 - a) * H * 0.03
    if ph is not None:
        L = _shadow(L, (box[0] + 8, box[1] + 12 + dy, box[2] + 8, box[3] + 12 + dy), H * 0.03, a, H * 0.02)
        _paste(L, _rounded(ph, box[2] - box[0], box[3] - box[1], H * 0.03), (box[0], box[1] + dy), a)
    d = ImageDraw.Draw(L)
    x = W * 0.5
    name = g.get("name", "")
    fn = _fit(d, name, True, H * 0.085, W * 0.45)
    fn = font(fn.size, bold=True, italic=True)
    a2 = _ease((t - 0.2) / 0.2)
    d.text((x, H * 0.45), name, font=fn, fill=_rgba(_c("ink"), a2), anchor="ls")
    lw = min(d.textlength(name, font=fn), W * 0.45) * _ease((t - 0.35) / 0.3)
    d.rectangle((x, H * 0.48, x + lw, H * 0.48 + max(3, H * 0.007)), fill=_rgba(_c("green"), 1))
    if g.get("sub"):
        _spaced(d, (x, H * 0.56), g["sub"].upper(), font(H * 0.032), _rgba(_lift(_c("ink"), 1.9), _ease((t - 0.5) / 0.2)),
                H * 0.005, anchor="l")
    return _comp(im, L)


def split_frame(g, W, H, t, photo=None):
    """2–3 скруглённые фото-карточки на тёмно-зелёном, появляются по очереди, под каждой — подпись капсом."""
    im = _tex("green", W, H)
    L = _layer(W, H)
    items = g.get("items") or [""]
    n = len(items)
    cw = W * (0.34 if n == 2 else 0.26 if n == 3 else 0.5)
    ch = cw * 0.66
    gap = W * 0.06
    x0 = (W - n * cw - (n - 1) * gap) / 2
    y0 = H * 0.42 - ch / 2
    for i, cap in enumerate(items):
        a = _ease((t - 0.06 - i * 0.22) / 0.2)
        if a <= 0:
            continue
        x, y = x0 + i * (cw + gap), y0 + (1 - a) * H * 0.04
        ph = _pp(i, photo)
        L = _shadow(L, (x + 6, y + 10, x + cw + 6, y + ch + 10), H * 0.02, a, H * 0.018)
        if ph is not None:
            _paste(L, _rounded(ph, cw, ch, H * 0.02), (x, y), a)
        d = ImageDraw.Draw(L)
        txt = cap.upper()
        m = int(max(0.0, (t - 0.18 - i * 0.22) / 0.25) * len(txt) + 0.999)
        fl = _fit(d, txt, True, H * 0.036, cw)
        _spaced(d, (x + cw / 2, y0 + ch + H * 0.08), txt[:m] if m < len(txt) else txt, fl, _rgba(_c("text"), 1), H * 0.004)
    return _comp(im, L)


def ask_frame(g, W, H, t, photo=None):
    """Вопрос на зелёном сукне: текст с засечками по центру, слова появляются по одному."""
    im = _tex("felt", W, H)
    im = _zoom(im, 1 + 0.03 * t)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    fn = font(H * 0.068)
    lines = _wrap(d, g.get("text", ""), fn, W * 0.72)
    words = sum(len(l.split()) for l in lines)
    lh = H * 0.1
    y0 = H / 2 - lh * (len(lines) - 1) / 2
    shown = (t - 0.08) / 0.62 * words
    k = 0
    for i, line in enumerate(lines):
        x = (W - d.textlength(line, font=fn)) / 2
        for w in line.split():
            a = _ease(shown - k)
            if a > 0:
                d.text((x, y0 + i * lh), w, font=fn, fill=_rgba(_c("text"), a), anchor="lm")
            x += d.textlength(w + " ", font=fn)
            k += 1
    return _comp(im, L)


def card_frame(g, W, H, t, photo=None):
    """Фото-карточка с белой рамкой и тенью на миллиметровке (или сукне), чуть повёрнута, медленный наезд."""
    bgk = g.get("bg") or "grid"
    im = _tex(bgk, W, H)
    L = _layer(W, H)
    ph = _pp(0, photo)
    a = _ease(t / 0.2)
    cap = g.get("text", "")
    cw, ch = W * 0.6, H * 0.6
    if ph is not None:
        b = int(H * 0.012)
        card = Image.new("RGBA", (int(cw) + 2 * b, int(ch) + 2 * b), (250, 248, 243, 255))
        card.alpha_composite(_cover(ph, int(cw), int(ch)).convert("RGBA"), (b, b))
        ang = (sum(map(ord, cap)) % 5 - 2) * 0.8
        card = card.rotate(ang, resample=Image.BICUBIC, expand=True)
        x, y = (W - card.width) / 2, H * (0.56 if cap else 0.5) - card.height / 2 + (1 - a) * H * 0.03
        sh = _layer(W, H)
        sh.alpha_composite(Image.new("RGBA", card.size, (0, 0, 0, int(140 * a))), (int(x + 10), int(y + 14)))
        L = Image.alpha_composite(L, sh.filter(ImageFilter.GaussianBlur(H * 0.015)))
        _paste(L, card, (x, y), a)
    if cap:
        d = ImageDraw.Draw(L)
        col = _c("ink") if bgk == "grid" else _c("text")
        d.text((W / 2, H * 0.13), cap, font=_fit(d, cap, True, H * 0.06, W * 0.8), fill=_rgba(col, _ease((t - 0.1) / 0.2)),
               anchor="mm")
    return _zoom(_comp(im, L), 1 + 0.03 * t)


def _hand(size):
    """Рукописный шрифт для подписей на полароиде/бумаге."""
    for n in ("Inkfree.ttf", "segoepr.ttf", "comic.ttf"):
        p = fonts.find(n)
        if p:
            return ImageFont.truetype(p, int(size))
    return font(size, italic=True)


def portrait_frame(g, W, H, t, photo=None):
    """Архивный портрет целиком (не обрезан) по центру на сером градиенте, как у @AnselmFaber.
    plate — кремовая табличка с именем под фото; polaroid — фото в полароиде с наклоном, имя от руки;
    pinned — фото приклеено скотчем к бежевой бумаге, рукописная заметка справа."""
    kind = g.get("style") or "plate"
    name = g.get("text", "")
    if kind == "pinned":
        im = _tex("paper", W, H)
    else:
        gr = Image.radial_gradient("L").resize((W, H))
        base = _lift(_c("bg"), 1.9)
        im = Image.merge("RGB", [gr.point(lambda v, c=c: int(c - v * (c - 18) / 255)) for c in base])
    L = _layer(W, H)
    a = _ease(t / 0.3)
    ph = _pp(0, photo)
    d = ImageDraw.Draw(L)
    if ph is not None:
        h = H * (0.62 if kind == "plate" else 0.56)
        w = min(W * 0.5, h * ph.width / ph.height)
        if w >= W * 0.5:
            h = w * ph.height / ph.width
        p = ph.resize((max(2, int(w)), max(2, int(h))), Image.LANCZOS).convert("RGBA")
        if kind == "polaroid":
            b, bb = int(H * 0.025), int(H * 0.11)
            card = Image.new("RGBA", (p.width + 2 * b, p.height + b + bb), (246, 244, 238, 255))
            card.alpha_composite(p, (b, b))
            cd = ImageDraw.Draw(card)
            cd.text((card.width / 2, p.height + b + bb * 0.5), name, font=_hand(H * 0.042), fill=(60, 60, 70, 255), anchor="mm")
            ang = 4 if sum(map(ord, name)) % 2 else -4
        else:
            b = int(H * 0.008)
            card = Image.new("RGBA", (p.width + 2 * b, p.height + 2 * b), (238, 230, 210, 255))
            card.alpha_composite(p, (b, b))
            ang = -6 if kind == "pinned" else 0
        if kind == "pinned":
            td = ImageDraw.Draw(card)
            tw = card.width * 0.32
            td.rectangle((card.width * 0.05, 0, card.width * 0.05 + tw, H * 0.035), fill=(225, 218, 195, 170))
            td.rectangle((card.width * 0.63, 0, card.width * 0.63 + tw, H * 0.035), fill=(225, 218, 195, 170))
        card = card.rotate(ang, resample=Image.BICUBIC, expand=True)
        cx = W * (0.4 if kind == "pinned" else 0.5)
        x, y = cx - card.width / 2, H * (0.46 if kind == "plate" else 0.5) - card.height / 2 + (1 - a) * H * 0.02
        sh = _layer(W, H)
        sh.alpha_composite(Image.new("RGBA", card.size, (0, 0, 0, int(150 * a))), (int(x + H * 0.015), int(y + H * 0.025)))
        L = Image.alpha_composite(L, sh.filter(ImageFilter.GaussianBlur(H * 0.03)))
        _paste(L, card, (x, y), a)
        d = ImageDraw.Draw(L)
        if kind == "plate" and name:
            fn = font(H * 0.03, bold=True)
            pw, phh = d.textlength(name, font=fn) + H * 0.04, H * 0.05
            ya = y + card.height + H * 0.02
            k = _ease((t - 0.3) / 0.25)
            d.rectangle((W / 2 - pw / 2, ya, W / 2 + pw / 2, ya + phh), fill=_rgba((238, 230, 210), k))
            d.text((W / 2, ya + phh / 2), name, font=fn, fill=_rgba(_c("ink"), k), anchor="mm")
    if kind == "pinned" and name:
        lines = _wrap(d, name, _hand(H * 0.045), W * 0.25)
        s = _typed(name, t, 18, 0.4)
        k = 0
        for i, l in enumerate(lines):
            d.text((W * 0.66, H * 0.36 + i * H * 0.065), s[k:k + len(l)], font=_hand(H * 0.045), fill=_rgba((90, 80, 140), 1), anchor="ls")
            k += len(l) + 1
    return _zoom(_comp(im, L), 1 + 0.015 * t)


def chapter_paper_frame(g, W, H, t, photo=None):
    """Глава на светлой бумаге с бледной картой мира: две тонкие линии, подпись и название с золотой подсветкой."""
    im = _tex("paper", W, H)
    d = ImageDraw.Draw(im)
    land = _lift(_c("paper"), 0.9)
    for ring in _geo():
        if len(ring) > 8:
            d.polygon([((lon + 180) / 360 * W, (90 - lat) / 180 * H * 1.25 - H * 0.1) for lon, lat in ring[::3]], fill=land)
    im = _zoom(im, 1 + 0.03 * t)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    k = _ease((t - 0.05) / 0.35)
    for y in (H * 0.37, H * 0.63):
        d.line((W * 0.07, y, W * 0.07 + W * 0.86 * k, y), fill=_rgba(_c("ink"), 0.5), width=max(2, H // 450))
    lab = chapter_label(g.get("num") or "")
    if lab.strip():
        _spaced(d, (W * 0.08, H * 0.43), lab.strip().upper(), font(H * 0.03), _rgba(_lift(_c("ink"), 2.2), _ease((t - 0.15) / 0.2)),
                H * 0.008, anchor="l")
    title = g.get("title", "").upper()
    fn = _fit(d, title, True, H * 0.075, W * 0.8)
    tw = d.textlength(title, font=fn)
    y = H * 0.53
    hw = (tw + H * 0.04) * _ease((t - 0.25) / 0.3)
    d.rectangle((W * 0.08 - H * 0.02, y - H * 0.05, W * 0.08 - H * 0.02 + hw, y + H * 0.05), fill=_rgba(_c("accent"), 0.85))
    d.text((W * 0.08, y), title, font=fn, fill=_rgba(_c("ink"), _ease((t - 0.35) / 0.2)), anchor="lm")
    return _comp(im, L)


# ---------------------------------------------------------------- плашки поверх кадра (печатаются, как в референсе)
# Это не отдельный кадр, а слой поверх фото/видео сцены: CAPTION, LABEL, PLATE, HIGHLIGHT, PILL, NAME, BAND, NOTE.

OVER = ("caption", "label", "plate", "highlight", "pill", "name", "band", "note", "lower", "place", "pin", "tag", "memo",
        "arrow", "zone", "mark", "grade", "loupe", "units", "arc", "trench", "mines", "blast")
# Пометки на карте (стрелка, круг, крестик/галочка, цвет эпизода, лупа) не занимают свой кадр — ложатся на кадр предыдущей плашки
ATTACH = ("arrow", "zone", "mark", "grade", "loupe", "units", "arc", "trench", "mines", "blast")
OVER_KINDS = tuple(k.upper() for k in OVER)


def parse_overlay(s):
    head, _, rest = s.partition(":")
    kind, rest = head.strip().lower(), rest.strip()
    if kind not in OVER or not rest:
        return None
    o = {"type": kind, "text": rest.strip("«»\"")}
    if kind == "plate":
        o["lines"] = [x.strip() for x in rest.split("/") if x.strip()]
    if kind == "pill":
        parts = re.split(r"\s*[•|]\s*", rest, maxsplit=1) + [""]
        o.update({"value": parts[0].strip(), "unit": parts[1].strip()})
    if kind == "tag":  # TAG: [red|blue|black|green|orange |] ТЕКСТ [| top|low|x y] [big|small]   ~ТЕКСТ — зачёркнуто
        parts = [x.strip() for x in rest.split("|")]
        col = parts[0].lower() if parts[0].lower() in _TAGC else ""
        if col:
            parts = parts[1:]
        txt = parts[0] if parts else ""
        o.update({"color": col or "black", "strike": txt.startswith("~"), "text": txt.lstrip("~").strip()})
        pos = parts[1].split() if len(parts) > 1 else []
        for sz in ("big", "small"):  # крупная капсула (главная цифра) / мелкая подпись у объекта
            if sz in pos:
                o["size"] = sz
                pos.remove(sz)
        if len(pos) == 2:
            try:
                o["x"], o["y"] = float(pos[0]), float(pos[1])
            except ValueError:
                pass
        elif pos and pos[0] == "low":
            o["y"] = 0.8
    if kind in ("arrow", "zone", "mark", "loupe", "arc"):
        parts = [x.strip() for x in rest.split("|")]
        col = parts[0].lower() if parts[0].lower() in _TAGC else ""
        if col:
            parts = parts[1:]
        geo = parts[0] if parts else ""
        o["label"] = parts[1] if len(parts) > 1 else ""
        nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", geo)]
        if kind == "arc":  # ARC: [red|blue|…] x1 y1 > x2 y2 > x3 y3 … [| подпись] — линия фронта дугой, рисуется и остаётся
            pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
            if len(pts) < 2:
                return None
            o.update({"color": col or "red", "pts": pts})
        elif kind == "arrow":  # ARROW: [blue|red|orange|…] x1 y1 > x2 y2 [> x3 y3] [| подпись]
            pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
            if len(pts) < 2:
                return None
            o.update({"color": col or "blue", "pts": pts})
        elif kind == "zone":  # ZONE: x y [r] [| подпись] — жёлтый пунктирный круг
            if len(nums) < 2:
                return None
            o.update({"color": col, "x": nums[0], "y": nums[1], "r": nums[2] if len(nums) > 2 else 0.1})
        elif kind == "mark":  # MARK: x|v  x y; x y … — красные крестики / зелёные галочки
            sym = "v" if re.match(r"\s*(v|ok|green|\u2713)", geo, re.I) else "x"
            pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
            if not pts:
                return None
            o.update({"sym": sym, "pts": pts})
        else:  # LOUPE: x y [r] [zoom] [| подпись] — золотая лупа увеличивает участок кадра, вокруг темнеет
            if len(nums) < 2:
                return None
            o.update({"x": nums[0], "y": nums[1], "r": nums[2] if len(nums) > 2 else 0.16,
                      "zoom": nums[3] if len(nums) > 3 else 2.2})
    if kind in ("trench", "mines", "blast"):  # BLAST: x y; x y — взрывы/выстрелы   TRENCH: x1 y1 > x2 y2; x1 y1 > x2 y2 [| подпись]   MINES: x y; x y [| подпись]
        geo, _, lab = rest.partition("|")
        o["label"] = lab.strip()
        lines = []
        for chunk in geo.split(";"):
            nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", chunk)]
            pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
            if pts:
                lines.append(pts)
        if not lines:
            return None
        if kind == "trench":
            o["lines"] = [l for l in lines if len(l) > 1]
            if not o["lines"]:
                return None
        else:
            o["pts"] = [l[0] for l in lines]
    if kind == "units":  # UNITS: blue|gray|red|orange | tank|inf | кол ряд | x1 y1 [> x2 y2] [| flag cz|su|de]
        parts = [x.strip() for x in rest.split("|")]
        col = parts[0].lower() if parts and parts[0].lower() in _UNITC else "blue"
        if parts and parts[0].lower() in _UNITC:
            parts = parts[1:]
        typ = "tank" if parts and parts[0].lower().startswith("tank") else "inf"
        if parts and parts[0].lower().split()[0] in ("tank", "tanks", "inf", "infantry"):
            parts = parts[1:]
        grid = [int(x) for x in re.findall(r"\d+", parts[0])] if len(parts) > 1 else []
        if len(parts) > 1:
            parts = parts[1:]
        nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", parts[0] if parts else "")]
        pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        if not pts:
            return None
        flag = ""
        for x in parts[1:]:
            m = re.match(r"flag\s+(\w+)", x, re.I)
            if m:
                flag = m.group(1).lower()
        o.update({"color": col, "unit": typ, "cols": (grid + [4])[0], "rows": (grid + [4, 3])[1], "pts": pts, "flag": flag})
    if kind == "grade":  # GRADE: night|fog|red|gray|warm|sepia — цвет кадра по эпизоду
        o["mode"] = rest.split()[0].lower()
    if kind == "memo":  # MEMO: НАДЗАГОЛОВОК | Заголовок | мелкая подпись
        parts = [x.strip() for x in rest.split("|")]
        o.update({"kicker": parts[0] if len(parts) > 1 else "", "text": parts[1] if len(parts) > 1 else parts[0],
                  "sub": parts[2] if len(parts) > 2 else ""})
    return o


_TAGC = {"red": (178, 40, 34), "blue": (36, 92, 168), "black": (24, 22, 20), "green": (46, 125, 79), "orange": (214, 128, 34),
         "yellow": (236, 206, 120), "white": (250, 248, 244)}


def _chip(d, X, Y, txt, col, H, W, a, size=0.034):
    """Маленькая капсула-подпись у объекта на карте."""
    if a <= 0:
        return
    fn = font(H * size, bold=True)
    txt = txt.upper()
    w, h = d.textlength(txt, font=fn) + H * 0.03, H * size * 1.65
    x = min(max(W * 0.01, X - w / 2), W * 0.99 - w)
    y = min(max(H * 0.01, Y - h / 2), H * 0.99 - h)
    d.rounded_rectangle((x, y, x + w, y + h), radius=H * 0.007, fill=_rgba(_TAGC.get(col, _TAGC["black"]), 0.94 * a),
                        outline=_rgba((255, 255, 255), 0.25 * a), width=max(1, H // 540))
    d.text((x + w / 2, y + h / 2), txt, font=fn, fill=_rgba((250, 248, 244), a), anchor="mm")


def _curve(pts, n=40):
    """Плавная кривая через точки: 2 точки — лёгкая дуга, больше — Catmull-Rom."""
    if len(pts) == 2:
        (x0, y0), (x1, y1) = pts
        mx, my = (x0 + x1) / 2 - (y1 - y0) * 0.12, (y0 + y1) / 2 + (x1 - x0) * 0.12
        return [((1 - u) ** 2 * x0 + 2 * (1 - u) * u * mx + u * u * x1, (1 - u) ** 2 * y0 + 2 * (1 - u) * u * my + u * u * y1)
                for u in (i / n for i in range(n + 1))]
    P = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    m = max(4, n // (len(pts) - 1))
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for j in range(m):
            u = j / m
            out.append(tuple(0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * u + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * u * u
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * u ** 3) for k in (0, 1)))
    out.append(tuple(pts[-1]))
    return out


# цвета жетонов: (заливка, рамка, светлая деталь) — как в «Bitvy pod lupou»
_UNITC = {"blue": ((62, 112, 204), (22, 40, 92), (150, 190, 245)), "gray": ((104, 108, 116), (34, 36, 42), (190, 194, 200)),
          "red": ((182, 52, 44), (70, 18, 14), (246, 190, 180)), "orange": ((226, 148, 44), (96, 56, 10), (255, 222, 160))}


def _flag(d, X, Y, s, kind, a):
    """Флажок на древке, у основания — круглая метка (как у командира отряда): cz / su / de."""
    ink = _rgba((30, 30, 34), a)
    r = s * 0.42
    d.ellipse((X - r, Y - r, X + r, Y + r), fill=_rgba((120, 150, 120), a), outline=ink, width=max(1, int(s * 0.12)))
    top = Y - s * 2.6
    d.line((X, Y - r, X, top), fill=ink, width=max(1, int(s * 0.16)))
    x0, y0, w, h = X, top, s * 1.9, s * 1.25
    d.rectangle((x0 - 1, y0 - 1, x0 + w + 1, y0 + h + 1), fill=ink)
    if kind == "cz":
        d.rectangle((x0, y0, x0 + w, y0 + h / 2), fill=_rgba((248, 248, 248), a))
        d.rectangle((x0, y0 + h / 2, x0 + w, y0 + h), fill=_rgba((214, 36, 44), a))
        d.polygon([(x0, y0), (x0 + w * 0.5, y0 + h / 2), (x0, y0 + h)], fill=_rgba((26, 70, 160), a))
    elif kind == "de":
        d.rectangle((x0, y0, x0 + w, y0 + h), fill=_rgba((128, 128, 124), a))
        d.line((x0 + w * 0.2, y0 + h / 2, x0 + w * 0.8, y0 + h / 2), fill=ink, width=max(1, int(s * 0.3)))
        d.line((x0 + w / 2, y0 + h * 0.15, x0 + w / 2, y0 + h * 0.85), fill=ink, width=max(1, int(s * 0.3)))
    else:
        d.rectangle((x0, y0, x0 + w, y0 + h), fill=_rgba((204, 32, 32), a))


def _tank(d, cx, cy, H, col, a):
    """Жетон-танк сверху: вертикальная плашка с тёмной рамкой, ствол вверх, кокарда внизу."""
    base, ink, lite = col
    w, h = H * 0.034, H * 0.05
    lw = max(1, int(H / 360))
    sh = H * 0.004
    d.rectangle((cx - w / 2 + sh, cy - h / 2 + sh, cx + w / 2 + sh, cy + h / 2 + sh), fill=_rgba((0, 0, 0), 0.35 * a))
    d.line((cx, cy - h * 0.1, cx, cy - h / 2 - H * 0.02), fill=_rgba(ink, a), width=max(2, int(H * 0.007)))
    d.rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), fill=_rgba(base, a), outline=_rgba(ink, a), width=lw)
    d.line((cx - w / 2 + lw * 2, cy - h / 2 + lw, cx - w / 2 + lw * 2, cy + h / 2 - lw), fill=_rgba(ink, 0.5 * a), width=lw)
    d.line((cx + w / 2 - lw * 2, cy - h / 2 + lw, cx + w / 2 - lw * 2, cy + h / 2 - lw), fill=_rgba(ink, 0.5 * a), width=lw)
    r = w * 0.33
    oy = cy + h * 0.1
    box = (cx - r, oy - r, cx + r, oy + r)
    if col is _UNITC["blue"]:  # свои: бело-красная кокарда ЧСР
        d.ellipse(box, fill=_rgba((250, 250, 250), a), outline=_rgba(ink, a), width=lw)
        d.pieslice(box, -90, 90, fill=_rgba((214, 40, 40), a))
        d.ellipse(box, outline=_rgba(ink, a), width=lw)
    else:
        d.ellipse(box, fill=_rgba(lite, a), outline=_rgba(ink, a), width=lw)
    d.line((cx, cy - h * 0.1, cx, oy - r), fill=_rgba(ink, a), width=max(2, int(H * 0.006)))


def _soldier(d, cx, cy, H, col, a):
    """Фигурка пехоты сверху: каска с полями и винтовка-палочка вверх."""
    base, ink, lite = col
    s = H * 0.011
    lw = max(1, int(H / 540))
    d.line((cx + s * 0.15, cy - s * 0.2, cx + s * 0.15, cy - s * 2.1), fill=_rgba(ink, 0.9 * a), width=max(1, int(H / 400)))
    d.ellipse((cx - s, cy - s * 0.35, cx + s, cy + s * 0.75), fill=_rgba(ink, a))  # поля каски
    mid = tuple((b * 2 + l) // 3 for b, l in zip(base, lite))
    d.ellipse((cx - s * 0.92, cy - s * 0.3, cx + s * 0.92, cy + s * 0.65), fill=_rgba(mid, a))
    d.pieslice((cx - s * 0.75, cy - s * 0.85, cx + s * 0.75, cy + s * 0.55), 180, 360, fill=_rgba(base, a),
               outline=_rgba(ink, a), width=lw)  # купол каски
    d.ellipse((cx - s * 0.32, cy + s * 0.05, cx + s * 0.32, cy + s * 0.55), fill=_rgba(base, a))


def _units(d, o, W, H, t, dur, fo):
    """Отряд-модельки как в стратегии: строй жетонов-танков или густые ряды касок пехоты едут по карте."""
    P = [(W * x, H * y) for x, y in o["pts"]]
    k = _ease(t / max(1.0, dur - 0.4)) if len(P) > 1 else 0.0
    seg = (len(P) - 1) * k
    i = min(int(seg), max(0, len(P) - 2))
    u = seg - i
    X, Y = (P[i][0] + (P[i + 1][0] - P[i][0]) * u, P[i][1] + (P[i + 1][1] - P[i][1]) * u) if len(P) > 1 else P[0]
    a = _ease(t / 0.4) * fo
    col = _UNITC.get(o["color"], _UNITC["blue"])
    cols, rows = max(1, o["cols"]), max(1, o["rows"])
    tank = o["unit"] == "tank"
    gx, gy = (H * 0.046, H * 0.082) if tank else (H * 0.026, H * 0.03)
    x0, y0 = X - (cols - 1) * gx / 2, Y - (rows - 1) * gy / 2
    for r in range(rows):
        for c in range(cols):
            cx, cy = x0 + c * gx, y0 + r * gy
            if tank:
                _tank(d, cx, cy, H, col, a)
            else:  # шахматные ряды, лёгкий шаг
                cx += (gx / 2 if r % 2 else 0) + math.sin(r * 2.1 + c * 1.3) * gx * 0.12
                cy += math.sin(t * 7 + r * 1.7 + c * 2.3) * H * 0.0015
                _soldier(d, cx, cy, H, col, a)
    if o.get("flag"):
        _flag(d, x0 - gx * 1.6 if tank else x0 - gx * 1.2, y0 + (rows - 1) * gy * 0.3, H * 0.018, o["flag"], a)


GRADE_RGBA = {"night": (10, 18, 48, 0.5), "fog": (232, 232, 226, 0.42), "gray": (120, 120, 116, 0.38),
              "warm": (196, 150, 70, 0.22), "sepia": (150, 110, 60, 0.3)}


def _typed(text, t, cps=20.0, t0=0.25):
    return text[:max(0, int((t - t0) * cps + 0.999))] if t > t0 else ""


def overlay_draw(L, o, W, H, t, dur):
    """Нарисовать плашку o на слое L в момент t (с от начала кадра)."""
    if t < 0:
        return
    fo = _ease((dur - t) / 0.35)  # гаснет в конце кадра
    if fo <= 0:
        return
    d = ImageDraw.Draw(L)
    kind, txt = o["type"], o.get("text", "")
    if kind == "caption":  # машинопись внизу слева на затемнении, с кареткой
        key = ("capgrad", W, H)
        if key not in _BGC:
            gr = Image.linear_gradient("L").resize((W, int(H * 0.4)))
            _BGC[key] = gr.point(lambda v: int(v * 0.62))
        sh = _layer(W, H)
        sh.paste((0, 0, 0, 255), (0, H - int(H * 0.4)), _BGC[key].point(lambda v: int(v * _ease(t / 0.3) * fo)))
        L.alpha_composite(sh)
        s = _typed(txt, t, 22)
        car = "|" if (len(s) < len(txt) or int(t * 2.2) % 2 == 0) else ""
        d.text((W * 0.055, H * 0.86), s + car, font=font(H * 0.05), fill=_rgba(_c("text"), fo), anchor="ls")
    elif kind == "label":  # светлая плашка, тёмные заглавные с засечками, печатается по буквам
        fn = font(H * 0.05, bold=True)
        txt = txt.upper()
        pad = H * 0.02
        w, h = d.textlength(txt, font=fn) + 2 * pad, H * 0.078
        right = o.get("pos") == "right"
        x = W * 0.95 - w if right else W * 0.05
        y = H * 0.075
        k = _ease(t / 0.22)
        if right:
            d.rectangle((x + w * (1 - k), y, x + w, y + h), fill=_rgba(_c("plate_bg"), 0.95 * fo))
        else:
            d.rectangle((x, y, x + w * k, y + h), fill=_rgba(_c("plate_bg"), 0.95 * fo))
        d.text((x + pad, y + h / 2), _typed(txt, t, 18, 0.2), font=fn, fill=_rgba(_c("plate_text"), fo), anchor="lm")
    elif kind == "plate":  # золотые плашки в 2 строки: верхняя меньше, нижняя крупнее
        lines = [x.upper() for x in (o.get("lines") or [txt])]
        y = H * 0.6
        for i, line in enumerate(lines):
            fn = font(H * (0.042 if i == 0 and len(lines) > 1 else 0.06))
            pad = H * 0.018
            w, h = d.textlength(line, font=fn) + 2 * pad, fn.size * 1.45
            k = _ease((t - 0.1 - i * 0.18) / 0.22)
            if k > 0:
                d.rectangle((W * 0.06, y, W * 0.06 + w * k, y + h), fill=_rgba(_c("accent"), 0.92 * fo))
                d.text((W * 0.06 + pad, y + h / 2), line, font=fn, fill=_rgba(_c("plate_bg"), _ease((k - 0.6) / 0.4) * fo),
                       anchor="lm")
            y += h + H * 0.012
    elif kind == "highlight":  # фраза внизу, ключевые [слова] на золотых/кремовых подложках
        words = re.findall(r"\[[^\]]+\]|\S+", txt)
        fn = font(H * 0.056)
        sp = d.textlength(" ", font=fn)
        items = [(w.strip("[]"), w.startswith("[")) for w in words]
        ws = [d.textlength(w, font=fn) for w, _ in items]
        rows, cur, cw = [], [], 0
        for it, w in zip(items, ws):
            if cur and cw + sp + w > W * 0.8:
                rows.append(cur)
                cur, cw = [], 0
            cur.append((it, w))
            cw += (sp if cw else 0) + w
        rows.append(cur)
        lh = H * 0.085
        y = H * 0.84 - lh * (len(rows) - 1)
        n, k = len(items), 0
        for row in rows:
            x = (W - (sum(w for _, w in row) + sp * (len(row) - 1))) / 2
            for (w_, hl), w in row:
                a = _ease((t - 0.1) * 8 - k * 0.6) * fo
                if hl:
                    g = _ease((t - 0.35 - k * 0.06) / 0.25)
                    p = H * 0.012
                    d.rectangle((x - p, y - lh * 0.42, x - p + (w + 2 * p) * g, y + lh * 0.42), fill=_rgba(_c("hl_box"), 0.95 * a))
                    d.text((x, y), w_, font=fn, fill=_rgba(_c("ink") if g > 0.5 else _c("text"), a), anchor="lm")
                else:
                    d.text((x, y), w_, font=fn, fill=_rgba(_c("text"), a), anchor="lm", stroke_width=max(1, H // 400),
                           stroke_fill=_rgba((0, 0, 0), a * 0.6))
                x += w + sp
                k += 1
            y += lh
    elif kind == "pill":  # тёмная таблетка-счётчик сверху слева: «383 • SETS»
        n = _num(o.get("value", ""))
        k = 1 - (1 - max(0.0, min(1.0, (t - 0.15) / 0.9))) ** 3
        val = (n[1] + _fmt(n[0] * k, n[3], n[4], n[5]) + n[2]) if n else o.get("value", "")
        full = (n[1] + _fmt(n[0], n[3], n[4], n[5]) + n[2]) if n else val
        fv, fu = font(H * 0.05, bold=True), font(H * 0.032)
        unit = o.get("unit", "").upper()
        pad = H * 0.025
        w = pad * 2 + d.textlength(full, font=fv) + H * 0.05 + d.textlength(unit, font=fu) + len(unit) * H * 0.004
        h = H * 0.08
        x, y = W * 0.045, H * 0.07
        a = _ease(t / 0.2) * fo
        d.rounded_rectangle((x, y, x + w, y + h), radius=h / 2, fill=_rgba(_c("pill"), 0.9 * a))
        d.text((x + pad, y + h / 2), val, font=fv, fill=_rgba(_c("text"), a), anchor="lm")
        xd = x + pad + d.textlength(full, font=fv) + H * 0.025
        d.ellipse((xd - H * 0.006, y + h / 2 - H * 0.006, xd + H * 0.006, y + h / 2 + H * 0.006), fill=_rgba(_c("accent"), a))
        _spaced(d, (xd + H * 0.025, y + h / 2), unit, fu, _rgba(_lift(_c("text"), 0.8), a), H * 0.004, anchor="l")
    elif kind == "name":  # красное имя с засечками на белой плашке
        fn = font(H * 0.062, bold=True)
        pad = H * 0.022
        w, h = d.textlength(txt, font=fn) + 2 * pad, H * 0.095
        x, y = W * 0.06, H * 0.7
        k = _ease(t / 0.22)
        d.rectangle((x, y, x + w * k, y + h), fill=_rgba((248, 246, 242), 0.96 * fo))
        d.text((x + pad, y + h / 2), txt, font=fn, fill=_rgba(_c("name"), _ease((t - 0.18) / 0.2) * fo), anchor="lm")
    elif kind == "band":  # жирный титр на полупрозрачной тёмной полосе внизу слева
        fn = font(H * 0.064, bold=True)
        w = d.textlength(txt, font=fn) + W * 0.1
        k = _ease(t / 0.3)
        y = H * 0.8
        d.rectangle((0, y - H * 0.06, w * k, y + H * 0.06), fill=_rgba((0, 0, 0), 0.55 * fo))
        d.text((W * 0.05 - (1 - k) * W * 0.05, y), txt, font=fn, fill=_rgba(_c("text"), k * fo), anchor="lm")
    elif kind == "lower":  # имя внизу слева: тёмная полупрозрачная плашка, тонкая золотая черта слева
        fn = font(H * 0.042, bold=True)
        pad = H * 0.02
        w, h = d.textlength(txt, font=fn) + 2 * pad, H * 0.075
        x, y = W * 0.055, H * 0.79
        k = _ease(t / 0.3) * fo
        d.rectangle((x, y, x + w, y + h), fill=_rgba((12, 11, 9), 0.55 * k))
        d.rectangle((x, y, x + max(2, H * 0.004), y + h), fill=_rgba(_c("accent"), k))
        d.text((x + pad, y + h / 2), txt, font=fn, fill=_rgba(_c("text"), k), anchor="lm")
    elif kind == "place":  # название места внизу слева: жирно, с золотым подчёркиванием, растущим слева направо
        fn = font(H * 0.06, bold=True)
        x, y = W * 0.05, H * 0.88
        k = _ease(t / 0.3) * fo
        d.text((x, y), txt, font=fn, fill=_rgba(_c("text"), k), anchor="ls", stroke_width=max(1, H // 500),
               stroke_fill=_rgba((0, 0, 0), 0.5 * k))
        lw = d.textlength(txt, font=fn) * _ease((t - 0.2) / 0.4)
        d.rectangle((x, y + H * 0.012, x + lw, y + H * 0.012 + max(2, H * 0.004)), fill=_rgba(_c("accent"), fo))
    elif kind == "pin":  # метка места прямо на кадре: кольцо-точка и подпись справа
        X, Y = W * float(o.get("x", 0.62)), H * float(o.get("y", 0.42))
        k = _ease(t / 0.25) * fo
        r = H * 0.011
        d.ellipse((X - r * 1.7, Y - r * 1.7, X + r * 1.7, Y + r * 1.7), outline=_rgba(_c("accent"), k), width=max(1, H // 500))
        d.ellipse((X - r * 0.7, Y - r * 0.7, X + r * 0.7, Y + r * 0.7), fill=_rgba(_c("accent"), k))
        d.text((X + r * 3, Y), _typed(txt, t, 16, 0.2), font=font(H * 0.04, bold=True), fill=_rgba(_c("text"), fo), anchor="lm",
               stroke_width=max(1, H // 450), stroke_fill=_rgba((0, 0, 0), 0.6 * fo))
    elif kind == "tag":  # капсула-ярлык капсом (карты «Bitvy pod lupou»): по центру сверху или у объекта (x y)
        sc = {"big": 1.45, "small": 0.7}.get(o.get("size"), 1.0)
        fn = font(H * 0.04 * sc, bold=True)
        txt = txt.upper()
        pad = H * 0.02 * sc
        w, h = d.textlength(txt, font=fn) + 2 * pad, H * 0.066 * sc
        X, Y = W * float(o.get("x", 0.5)), H * float(o.get("y", 0.075))
        x, y = min(max(W * 0.02, X - w / 2), W * 0.98 - w), Y
        k = _ease(t / 0.18)
        a = k * fo
        c = _TAGC.get(o.get("color"), _TAGC["black"])
        sy = (1 - k) * H * 0.02
        d.rounded_rectangle((x, y + sy, x + w, y + h + sy), radius=H * 0.008, fill=_rgba(c, 0.94 * a),
                            outline=_rgba((255, 255, 255), 0.25 * a), width=max(1, H // 540))
        d.text((x + w / 2, y + h / 2 + sy), txt, font=fn, fill=_rgba((250, 248, 244), a), anchor="mm")
        if o.get("strike"):
            g = _ease((t - 0.3) / 0.3)
            d.line((x + pad * 0.6, y + h / 2, x + pad * 0.6 + (w - pad * 1.2) * g, y + h / 2),
                   fill=_rgba((250, 248, 244), a), width=max(2, H // 200))
    elif kind == "memo":  # бумажная карточка-справка внизу слева: красный надзаголовок, курсив, мелкая подпись
        fk, ft, fs = font(H * 0.024, bold=True), font(H * 0.05, italic=True), font(H * 0.022)
        lines = _wrap(d, txt, ft, W * 0.36)
        w = max([d.textlength(l, font=ft) for l in lines] + [d.textlength(o.get("sub", ""), font=fs)]) + H * 0.08
        w = max(w, W * 0.3)
        h = H * (0.07 + 0.065 * len(lines) + (0.05 if o.get("sub") else 0))
        k = _ease(t / 0.3)
        a = k * fo
        x, y = W * 0.07, H * 0.8 - h + (1 - k) * H * 0.03
        L.paste(_shadow(L, (x, y, x + w, y + h), H * 0.006, 0.5 * a, H * 0.012))
        d = ImageDraw.Draw(L)
        d.rounded_rectangle((x, y, x + w, y + h), radius=H * 0.006, fill=_rgba((240, 233, 216), 0.97 * a),
                            outline=_rgba((176, 160, 128), a), width=max(1, H // 540))
        cy = y + H * 0.035
        if o.get("kicker"):
            d.text((x + H * 0.04, cy), o["kicker"].upper(), font=fk, fill=_rgba(_TAGC["red"], a), anchor="lm")
        cy += H * 0.05
        for l in lines:
            d.text((x + H * 0.04, cy), l, font=ft, fill=_rgba((40, 32, 24), a), anchor="lm")
            cy += H * 0.065
        if o.get("sub"):
            d.line((x + H * 0.04, cy - H * 0.02, x + w - H * 0.04, cy - H * 0.02), fill=_rgba((176, 160, 128), a), width=1)
            d.text((x + H * 0.04, cy + H * 0.005), o["sub"], font=fs, fill=_rgba((110, 96, 80), a), anchor="lm")
    elif kind == "grade":  # цвет эпизода на весь кадр: ночь, туман, потери (красная виньетка), легенда (серость)
        a = _ease(t / 0.8) * fo
        m = o.get("mode", "night")
        if m == "red":
            key = ("redvig", W, H)
            if key not in _BGC:
                _BGC[key] = Image.radial_gradient("L").resize((W, H)).point(lambda v: int(min(200, max(0, v - 150) * 1.8)))
            sh = Image.new("RGBA", (W, H), (150, 28, 18, 0))
            sh.putalpha(_BGC[key].point(lambda v: int(v * a)))
            L.alpha_composite(sh)
        else:
            r, g_, b, al = GRADE_RGBA.get(m, GRADE_RGBA["night"])
            L.alpha_composite(Image.new("RGBA", (W, H), (r, g_, b, int(255 * al * a))))
    elif kind == "units":
        _units(d, o, W, H, t, dur, fo)
    elif kind == "trench":  # траншеи роют на глазах: зигзаг прорисовывается линия за линией, у края летят комья земли
        n = len(o["lines"])
        per = max(0.6, (min(dur, 7.0) - 0.8) / max(1, n))
        dark, mid = (74, 52, 30), (150, 112, 70)
        for li, line in enumerate(o["lines"]):
            k = _ease((t - 0.3 - li * per) / per)
            if k <= 0:
                continue
            (x1, y1), (x2, y2) = (W * line[0][0], H * line[0][1]), (W * line[-1][0], H * line[-1][1])
            L_ = math.hypot(x2 - x1, y2 - y1)
            ux, uy = (x2 - x1) / max(1, L_), (y2 - y1) / max(1, L_)
            nx, ny = -uy, ux
            step, amp = H * 0.022, H * 0.011
            m = max(2, int(L_ / step))
            pts = [(x1 + ux * L_ * j / m + nx * amp * (1 if j % 2 else -1), y1 + uy * L_ * j / m + ny * amp * (1 if j % 2 else -1))
                   for j in range(m + 1)]
            cut = pts[:max(2, int(round(len(pts) * k)))]
            d.line(cut, fill=_rgba(mid, 0.9 * fo), width=max(6, int(H * 0.017)), joint="curve")  # бруствер
            d.line(cut, fill=_rgba(dark, 0.97 * fo), width=max(3, int(H * 0.008)), joint="curve")  # ров
            if k < 1:  # комья земли у копающего края
                ex, ey = cut[-1]
                for q in range(6):
                    ph = (t * 3.1 + q * 0.37) % 1
                    ang = q * 1.9 + li
                    d.ellipse((ex + math.cos(ang) * H * 0.03 * ph - 2, ey - H * 0.035 * math.sin(ph * math.pi) - 2,
                               ex + math.cos(ang) * H * 0.03 * ph + 3, ey - H * 0.035 * math.sin(ph * math.pi) + 3),
                              fill=_rgba(dark, (1 - ph) * fo))
        if o.get("label"):
            P0 = o["lines"][0][0]
            _chip(d, W * P0[0], H * P0[1] - H * 0.05, o["label"], "black", H, W, _ease((t - 0.4) / 0.3) * fo)
    elif kind == "blast":  # взрывы по точкам: вспышка, затем клуб дыма; идут вразнобой всю сцену
        for i, (x, y) in enumerate(o["pts"]):
            per = 1.6 + (i * 0.37) % 0.9
            ph = (t - 0.6 - i * 0.29) / per
            if ph < 0:
                continue
            ph %= 1.0
            X, Y = W * x, H * y
            if ph < 0.18:  # вспышка
                k = ph / 0.18
                r = H * 0.022 * (0.4 + k)
                d.ellipse((X - r * 1.6, Y - r * 1.6, X + r * 1.6, Y + r * 1.6), fill=_rgba((255, 196, 70), 0.35 * (1 - k) * fo))
                d.ellipse((X - r, Y - r, X + r, Y + r), fill=_rgba((255, 120, 30), 0.9 * (1 - k * 0.5) * fo))
                d.ellipse((X - r * 0.45, Y - r * 0.45, X + r * 0.45, Y + r * 0.45), fill=_rgba((255, 240, 190), 0.95 * fo))
            if ph < 0.9:  # дым поднимается и тает
                k = ph / 0.9
                for q in range(3):
                    rr = H * (0.012 + 0.02 * k) * (1 - q * 0.2)
                    cx = X + (q - 1) * rr * 0.8
                    cy = Y - H * 0.04 * k - q * rr * 0.4
                    d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=_rgba((70, 66, 60), 0.55 * (1 - k) * fo))
    elif kind == "mines":  # мины выскакивают по одной: тёмный диск с шипами
        n = len(o["pts"])
        for i, (x, y) in enumerate(o["pts"]):
            k = _ease((t - 1.0 - i * min(0.15, 4.0 / max(1, n))) / 0.2)
            if k <= 0:
                continue
            X, Y, r = W * x, H * y, H * 0.009 * (0.6 + 0.4 * k)
            for a_ in range(0, 360, 60):
                ra = math.radians(a_)
                d.line((X, Y, X + math.cos(ra) * r * 1.8, Y + math.sin(ra) * r * 1.8), fill=_rgba((30, 28, 26), k * fo), width=2)
            d.ellipse((X - r, Y - r, X + r, Y + r), fill=_rgba((36, 34, 30), k * fo), outline=_rgba((200, 60, 40), k * fo), width=2)
        if o.get("label"):
            x, y = o["pts"][0]
            _chip(d, W * x, H * y + H * 0.05, o["label"], "red", H, W, _ease((t - 1.0) / 0.3) * fo)
    elif kind == "arc":  # линия фронта дугой: толстая линия с белой обводкой рисуется за ~1.5 с и держится весь кадр
        P = [(W * x, H * y) for x, y in _curve(o["pts"])]
        n = max(2, int(round(len(P) * _ease(t / 1.5))))
        c = _TAGC.get(o.get("color"), _TAGC["red"])
        wd = max(5, int(H * 0.016))
        d.line(P[:n], fill=_rgba((250, 248, 244), 0.8 * fo), width=wd + max(3, H // 220), joint="curve")
        d.line(P[:n], fill=_rgba(c, 0.95 * fo), width=wd, joint="curve")
        if o.get("label"):
            m = P[len(P) // 2]
            _chip(d, m[0], m[1] - H * 0.05, o["label"], o.get("color") or "red", H, W, _ease((t - 1.2) / 0.3) * fo)
    elif kind == "arrow":  # стрелка наступления рисуется по карте, с белой обводкой и наконечником
        P = [(W * x, H * y) for x, y in _curve(o["pts"])]
        n = max(2, int(round(len(P) * _ease(t / 1.1))))
        seg = P[:n]
        c = _TAGC.get(o.get("color"), _TAGC["blue"])
        wd = max(4, int(H * 0.014))
        d.line(seg, fill=_rgba((250, 248, 244), 0.75 * fo), width=wd + max(2, H // 270), joint="curve")
        d.line(seg, fill=_rgba(c, 0.95 * fo), width=wd, joint="curve")
        (xa, ya), (xb, yb) = seg[-2], seg[-1]
        ang = math.atan2(yb - ya, xb - xa)
        hl = H * 0.045
        tip = (xb + math.cos(ang) * hl * 0.4, yb + math.sin(ang) * hl * 0.4)
        head = [tip, (tip[0] - math.cos(ang - 0.5) * hl, tip[1] - math.sin(ang - 0.5) * hl),
                (tip[0] - math.cos(ang + 0.5) * hl, tip[1] - math.sin(ang + 0.5) * hl)]
        d.polygon(head, fill=_rgba(c, 0.97 * fo), outline=_rgba((250, 248, 244), 0.75 * fo))
        if o.get("label"):
            _chip(d, P[0][0], P[0][1] + H * 0.05, o["label"], o.get("color") or "blue", H, W, _ease((t - 0.3) / 0.3) * fo)
    elif kind == "zone":  # жёлтый пунктирный круг вокруг места, пунктир обегает по кругу
        X, Y, R = W * o["x"], H * o["y"], H * o["r"]
        c = _TAGC.get(o.get("color") or "yellow", _TAGC["yellow"])
        nd = 28
        wd = max(3, int(H * 0.0065))
        for i in range(int(nd * _ease(t / 0.9) + 0.999)):
            a0 = -90 + i * 360 / nd
            d.arc((X - R, Y - R, X + R, Y + R), a0, a0 + 360 / nd * 0.55, fill=_rgba((0, 0, 0), 0.35 * fo), width=wd + 2)
            d.arc((X - R, Y - R, X + R, Y + R), a0, a0 + 360 / nd * 0.55, fill=_rgba(c, 0.95 * fo), width=wd)
        if o.get("label"):
            _chip(d, X, Y - R - H * 0.035, o["label"], "black", H, W, _ease((t - 0.4) / 0.3) * fo)
    elif kind == "mark":  # красные крестики / зелёные галочки выскакивают по одному
        for i, (x, y) in enumerate(o["pts"]):
            k = _ease((t - i * 0.18) / 0.22)
            if k <= 0:
                continue
            X, Y = W * x, H * y
            s = H * 0.024 * (1 + 0.3 * (1 - k))
            a = k * fo
            if o.get("sym") == "v":
                pts = [(X - s, Y), (X - s * 0.3, Y + s * 0.75), (X + s * 1.1, Y - s * 0.9)]
                d.line(pts, fill=_rgba((250, 248, 244), 0.9 * a), width=max(5, int(H * 0.015)), joint="curve")
                d.line(pts, fill=_rgba((88, 190, 110), a), width=max(3, int(H * 0.009)), joint="curve")
            else:
                d.ellipse((X - s * 1.5, Y - s * 1.5, X + s * 1.5, Y + s * 1.5), fill=_rgba((250, 248, 244), 0.85 * a))
                for p1, p2 in (((X - s, Y - s), (X + s, Y + s)), ((X - s, Y + s), (X + s, Y - s))):
                    d.line((p1, p2), fill=_rgba((196, 40, 32), a), width=max(3, int(H * 0.009)))
    elif kind == "loupe":  # рамка лупы и затемнение вокруг; само увеличение — в render_overlay / overlay_still
        X, Y, R = W * o["x"], H * o["y"], H * o["r"]
        a = _ease(t / 0.35) * fo
        dim = _layer(W, H)
        dd = ImageDraw.Draw(dim)
        dd.rectangle((0, 0, W, H), fill=(0, 0, 0, int(110 * a)))
        dd.ellipse((X - R, Y - R, X + R, Y + R), fill=(0, 0, 0, 0))
        L.alpha_composite(dim)
        d = ImageDraw.Draw(L)
        gold, dark = (214, 170, 82), (120, 84, 30)
        ww = max(4, int(R * 0.12))
        ang = math.radians(45)
        hx0, hy0 = X + math.cos(ang) * (R + ww * 0.4), Y + math.sin(ang) * (R + ww * 0.4)
        hx1, hy1 = X + math.cos(ang) * R * 1.85, Y + math.sin(ang) * R * 1.85
        d.line((hx0, hy0, hx1, hy1), fill=_rgba(dark, a), width=int(ww * 1.6))
        d.line((hx0, hy0, hx1, hy1), fill=_rgba(gold, a), width=int(ww * 1.1))
        d.ellipse((X - R - ww / 2, Y - R - ww / 2, X + R + ww / 2, Y + R + ww / 2), outline=_rgba(dark, a), width=ww + 2)
        d.ellipse((X - R - ww / 3, Y - R - ww / 3, X + R + ww / 3, Y + R + ww / 3), outline=_rgba(gold, a), width=ww)
        if o.get("label"):
            _chip(d, X, Y + R + H * 0.05, o["label"], "black", H, W, _ease((t - 0.4) / 0.3) * fo)
    elif kind == "note":  # маленькая коричневая плашка по центру с вопросом
        fn = font(H * 0.046)
        lines = _wrap(d, txt, fn, W * 0.6)
        lh = H * 0.065
        w = max(d.textlength(l, font=fn) for l in lines) + H * 0.06
        h = lh * len(lines) + H * 0.04
        a = _ease(t / 0.25) * fo
        x, y = (W - w) / 2, H * 0.5 - h / 2
        d.rectangle((x, y, x + w, y + h), fill=_rgba(_c("note_bg"), 0.92 * a))
        for i, l in enumerate(lines):
            d.text((W / 2, y + H * 0.02 + lh * (i + 0.5)), l, font=fn, fill=_rgba(_c("text"), a), anchor="mm")


def _order(overlays):
    """Цвет эпизода и лупа — под остальными плашками; время появления сохраняется."""
    ovs = []
    for k, o in enumerate(overlays):
        o = dict(o)
        o.setdefault("at", 0.25 + 0.6 * k)
        ovs.append(o)
    return sorted(ovs, key=lambda o: 0 if o["type"] in ("grade", "loupe") else 1)


def render_overlay(base: Path, overlays, out: Path, W, H, fps, dur, norm="", enc=(), theme=None):
    """Плашки поверх готового клипа кадра: слой RGBA (только нужный прямоугольник) → ffmpeg overlay."""
    F = max(1, int(round(dur * fps)))
    _TL.t = make_theme(theme)
    try:
        overlays = _order(overlays)
        lp = next((o for o in overlays if o["type"] == "loupe"), None)

        def layer(f):
            L = _layer(W, H)
            for k, o in enumerate(overlays):
                at = float(o.get("at", 0.25 + 0.6 * k))
                overlay_draw(L, o, W, H, f / fps - at, dur - at)
            return L
        box = None
        for f in range(0, F, max(1, fps // 4)):
            b = layer(f).getbbox()
            if b:
                box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
        if box is None:
            import shutil
            shutil.copyfile(base, out)
            return out
        x0, y0 = box[0] // 2 * 2, box[1] // 2 * 2
        x1, y1 = min(W, (box[2] + 1) // 2 * 2), min(H, (box[3] + 1) // 2 * 2)
        fc = f"[0:v][1:v]overlay={x0}:{y0}:eof_action=pass,setsar=1{norm}"
        if lp:  # лупа: круг из увеличенного участка самого кадра под рамкой
            R = int(H * lp["r"]) // 2 * 2
            D = 2 * R
            cw = max(2, int(D / max(1.1, lp["zoom"])) // 2 * 2)
            cx = int(min(max(0, W * lp["x"] - cw / 2), W - cw))
            cy = int(min(max(0, H * lp["y"] - cw / 2), H - cw))
            at = float(lp.get("at", 0)) + 0.15
            fc = (f"[0:v]split[b][z];[z]crop={cw}:{cw}:{cx}:{cy},scale={D}:{D},format=rgba[zz];"
                  f"color=c=black:s={D}x{D},format=gray,geq=lum='255*lte(hypot(X-{R},Y-{R}),{R})'[m];"
                  f"[zz][m]alphamerge[disc];[b][disc]overlay={int(W * lp['x']) - R}:{int(H * lp['y']) - R}:"
                  f"shortest=0:eof_action=pass:enable='gte(t,{at:.2f})'[bb];"
                  f"[bb][1:v]overlay={x0}:{y0}:eof_action=pass,setsar=1{norm}")
        cmd = [ff.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(base), "-f", "rawvideo", "-pix_fmt", "rgba",
               "-s", f"{x1 - x0}x{y1 - y0}", "-r", str(fps), "-i", "-", "-filter_complex",
               fc, "-frames:v", str(F), *map(str, enc), str(out)]
        pr = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=ff._NOWIN)
        try:
            for f in range(F):
                pr.stdin.write(layer(f).crop((x0, y0, x1, y1)).tobytes())
            pr.stdin.close()
            err = pr.stderr.read().decode("utf-8", "replace")
            if pr.wait() != 0:
                raise ff.FFError(err[-500:])
        finally:
            if pr.poll() is None:
                pr.kill()
        return out
    finally:
        _TL.t = None


def overlay_still(base_img: Path, overlays, out: Path, W=960, H=540, t=2.0, theme=None):
    """Превью плашек на кадре (для проверки)."""
    _TL.t = make_theme(theme)
    try:
        im = _cover(Image.open(base_img).convert("RGB"), W, H) if base_img else _bg(W, H)
        overlays = _order(overlays)
        for lp in [o for o in overlays if o["type"] == "loupe"][:1]:
            R = int(H * lp["r"])
            cw = max(2, int(2 * R / max(1.1, lp["zoom"])))
            cx = int(min(max(0, W * lp["x"] - cw / 2), W - cw))
            cy = int(min(max(0, H * lp["y"] - cw / 2), H - cw))
            disc = im.crop((cx, cy, cx + cw, cy + cw)).resize((2 * R, 2 * R), Image.LANCZOS)
            mk = Image.new("L", (2 * R, 2 * R), 0)
            ImageDraw.Draw(mk).ellipse((0, 0, 2 * R - 1, 2 * R - 1), fill=255)
            im.paste(disc, (int(W * lp["x"]) - R, int(H * lp["y"]) - R), mk)
        L = _layer(W, H)
        for k, o in enumerate(overlays):
            at = float(o.get("at", 0.25 + 0.6 * k))
            overlay_draw(L, o, W, H, t - at, 99)
        _comp(im, L).save(out, quality=90)
    finally:
        _TL.t = None
    return out


# ---------------------------------------------------------------- 20–31. «досье» (стиль MachtMarke)
# Надзаголовок «— KICKER —» разрядкой, заголовок с засечками, [слово] — золотом с подчёркиванием.

def _rich(d, xy, text, fnt, a, anchor="m"):
    """Строка, где [слово] — акцентом с подчёркиванием. Возвращает ширину."""
    segs = re.split(r"(\[[^\]]*\])", text)
    plain = "".join(x.strip("[]") for x in segs)
    tw = d.textlength(plain, font=fnt)
    x = xy[0] - (tw / 2 if anchor == "m" else 0)
    for sg in segs:
        if not sg:
            continue
        hot = sg.startswith("[")
        sg = sg.strip("[]")
        w = d.textlength(sg, font=fnt)
        d.text((x, xy[1]), sg, font=fnt, fill=_rgba(_c("accent") if hot else _c("text"), a), anchor="lm")
        if hot:
            y = xy[1] + fnt.size * 0.55
            d.line((x, y, x + w, y), fill=_rgba(_c("accent"), a), width=max(1, int(fnt.size / 22)))
        x += w
    return tw


def _kick(d, W, H, y, text, a):
    if not text:
        return
    fk = font(H * 0.026)
    w = _spaced(d, (W / 2, y), text.upper(), fk, _rgba(_c("accent"), a * 0.9), H * 0.006)
    for s in (-1, 1):
        x = W / 2 + s * (w / 2 + H * 0.02)
        d.line((x, y, x + s * W * 0.03, y), fill=_rgba(_c("accent"), a * 0.6), width=1)


def _head(d, W, H, t, kicker, text, y=0.42, size=0.075):
    """Надзаголовок + заголовок + ромбик под ним. Возвращает y низа."""
    a = _ease((t - 0.03) / 0.2)
    _kick(d, W, H, H * (y - size * 0.95), kicker, a)
    fn = font(H * size)
    lines = _wrap(d, text, fn, W * 0.7) if text else []
    for i, ln in enumerate(lines):
        _rich(d, (W / 2, H * y + i * H * size * 1.25 + (1 - a) * H * 0.015), ln, fn, a)
    yb = H * y + max(0, len(lines) - 1) * H * size * 1.25 + H * size * 0.75
    if lines:
        lw = W * 0.09 * _ease((t - 0.15) / 0.3)
        d.line((W / 2 - lw, yb, W / 2 + lw, yb), fill=_rgba(_c("accent"), a * 0.7), width=1)
        r = H * 0.006
        d.polygon([(W / 2, yb - r), (W / 2 + r, yb), (W / 2, yb + r), (W / 2 - r, yb)], fill=_rgba(_c("accent"), a))
    return yb


def _sub(d, W, H, y, text, a, italic=True):
    if text:
        _rich(d, (W / 2, y), text, font(H * 0.036, italic=italic), a * 0.9)


def kicker_frame(g, W, H, t, photo=None):
    """Титр-досье поверх затемнённого кадра: «— GROOM LAKE —» / Offiziell [geheim] / подпись."""
    im = _vignette(_backdrop(photo, W, H, t, blur=False), 0.6)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    yb = _head(d, W, H, t, g.get("kicker"), g.get("text"), 0.45, 0.085)
    _sub(d, W, H, yb + H * 0.06, g.get("sub"), _ease((t - 0.35) / 0.25))
    return _comp(im, L)


def myth_frame(g, W, H, t, photo=None):
    """Было/стало: старое утверждение зачёркивается красным, ниже золотом — правда."""
    im = _vignette(_backdrop(photo, W, H, t), 0.6)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    a = _ease(t / 0.15)
    _kick(d, W, H, H * 0.3, g.get("kicker"), a)
    lab = font(H * 0.022)
    lang = _lang()
    l1, l2 = {"de": ("DAMALS BEHAUPTET", "HEUTE BEKANNT"), "ru": ("ТОГДА ГОВОРИЛИ", "СЕГОДНЯ ИЗВЕСТНО")}.get(
        lang, ("CLAIMED THEN", "KNOWN TODAY"))
    _spaced(d, (W / 2, H * 0.37), l1, lab, _rgba(_c("text"), a * 0.7), H * 0.005)
    fo = _fit(d, g["old"].upper(), True, H * 0.085, W * 0.8)
    w = d.textlength(g["old"].upper(), font=fo)
    d.text((W / 2, H * 0.45), g["old"].upper(), font=fo, fill=_rgba(_c("text"), a * (1 - 0.35 * _ease((t - 0.4) / 0.2))), anchor="mm")
    k = _ease((t - 0.3) / 0.2)
    if k > 0:
        d.line((W / 2 - w / 2 - 8, H * 0.455, W / 2 - w / 2 - 8 + (w + 16) * k, H * 0.445), fill=_rgba(_c("hl"), 1),
               width=max(3, H // 140))
    a2 = _ease((t - 0.5) / 0.2)
    _spaced(d, (W / 2, H * 0.55), l2, lab, _rgba(_c("text"), a2 * 0.7), H * 0.005)
    fn = _fit(d, g["new"].upper(), True, H * 0.085, W * 0.8)
    d.text((W / 2, H * 0.63 + (1 - a2) * H * 0.02), g["new"].upper(), font=fn, fill=_rgba(_c("accent"), a2), anchor="mm")
    return _comp(im, L)


def check_frame(g, W, H, t, photo=None):
    """Заголовок-досье и пункты с золотым ромбом, появляются по одному (столбиком под центром)."""
    im = _vignette(_backdrop(photo, W, H, t), 0.6)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    yb = _head(d, W, H, t, g.get("kicker"), g.get("text"), 0.34, 0.07)
    items = g["items"]
    fn = font(H * 0.042)
    mw = max(d.textlength(x, font=fn) for x in items)
    x0 = W / 2 - (mw + H * 0.05) / 2
    for i, it in enumerate(items):
        a = _ease((t - 0.25 - i * 0.6 / len(items)) / 0.12)
        if a <= 0:
            continue
        y = yb + H * 0.07 + i * H * 0.065
        r = H * 0.012
        cx = x0 + r + (1 - a) * W * 0.02
        d.polygon([(cx, y - r), (cx + r, y), (cx, y + r), (cx - r, y)], fill=_rgba(_c("accent"), a))
        d.text((cx + H * 0.035, y), it, font=fn, fill=_rgba(_c("text"), a), anchor="lm")
    return _comp(im, L)


def facts_frame(g, W, H, t, photo=None):
    """Справка: заголовок и строки «КЛЮЧ — значение» (ключ мелким золотом), тонкие линии между строками."""
    im = _vignette(_backdrop(photo, W, H, t), 0.6)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    yb = _head(d, W, H, t, g.get("kicker"), g.get("text"), 0.3, 0.07)
    fk, fv = font(H * 0.024), font(H * 0.04)
    for i, (k, v) in enumerate(g["rows"]):
        a = _ease((t - 0.25 - i * 0.12) / 0.15)
        if a <= 0:
            continue
        y = yb + H * 0.08 + i * H * 0.08
        _spaced(d, (W * 0.3, y), k.upper(), fk, _rgba(_c("accent"), a), H * 0.004, anchor="l")
        d.text((W * 0.46, y), v, font=fv, fill=_rgba(_c("text"), a), anchor="lm")
        d.line((W * 0.3, y + H * 0.04, W * 0.3 + W * 0.4 * a, y + H * 0.04), fill=_rgba(_c("text"), 0.18 * a), width=1)
    return _comp(im, L)


def stamp_frame(g, W, H, t, photo=None):
    """Красный штамп падает на кадр (крупно → в размер, с наклоном), ниже подпись."""
    im = _backdrop(photo, W, H, t, blur=False)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    k = _ease((t - 0.1) / 0.15)
    if k > 0:
        txt = g["text"].upper()
        fn = font(H * 0.11, bold=True)
        tw = d.textlength(txt, font=fn)
        S = Image.new("RGBA", (int(tw + H * 0.14), int(H * 0.22)), (0, 0, 0, 0))
        sd = ImageDraw.Draw(S)
        col = _rgba(_c("hl"), 0.9)
        sd.rounded_rectangle((4, 4, S.width - 4, S.height - 4), radius=H * 0.02, outline=col, width=max(4, H // 90))
        sd.text((S.width / 2, S.height / 2), txt, font=fn, fill=col, anchor="mm")
        sc = 1 + 0.6 * (1 - k)
        S = S.resize((int(S.width * sc), int(S.height * sc))).rotate(-8, expand=True, resample=Image.BICUBIC)
        S.putalpha(S.getchannel("A").point(lambda v: int(v * k)))
        L.alpha_composite(S, (int(W / 2 - S.width / 2), int(H * 0.45 - S.height / 2)))
    _sub(d, W, H, H * 0.7, g.get("sub"), _ease((t - 0.4) / 0.2))
    return _comp(im, L)


def calendar_frame(g, W, H, t, photo=None):
    """Отрывной лист календаря: кремовая карточка с большой цифрой дня, месяц, подпись под ней."""
    im = _vignette(_backdrop(photo, W, H, t), 0.6)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    a = _ease(t / 0.15)
    cw, ch = H * 0.36, H * 0.42
    x0, y0 = W / 2 - cw / 2, H * 0.36 - ch / 2 + (1 - a) * H * 0.03
    _shadow(L, (x0, y0, x0 + cw, y0 + ch), 6, 0.5 * a, 14)
    d = ImageDraw.Draw(L)
    d.rectangle((x0, y0, x0 + cw, y0 + ch), fill=_rgba(_c("paper"), a))
    d.rectangle((x0, y0, x0 + cw, y0 + ch * 0.13), fill=_rgba(_c("accent2"), a))
    if g.get("top"):
        _spaced(d, (W / 2, y0 + ch * 0.065), g["top"].upper(), font(H * 0.02), _rgba(_c("paper"), a), H * 0.004)
    ink = _c("bg")
    d.text((W / 2, y0 + ch * 0.5), g["day"], font=font(H * 0.17, bold=True), fill=_rgba(ink, _ease((t - 0.15) / 0.2)), anchor="mm")
    if g.get("month"):
        _spaced(d, (W / 2, y0 + ch * 0.8), g["month"].upper(), font(H * 0.03, bold=True), _rgba(_c("hl"), a), H * 0.004)
    _sub(d, W, H, y0 + ch + H * 0.08, g.get("sub"), _ease((t - 0.35) / 0.2))
    return _comp(im, L)


def ring_frame(g, W, H, t, photo=None):
    """Кольцевой процент: золотая дуга растёт от «from» до «value», число в центре докручивается."""
    im = _vignette(_backdrop(photo, W, H, t), 0.6)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    v1, pre, suf, decs, thou, dec = _num(g["value"])
    v0 = (_num(g.get("from")) or (0,))[0]
    k = 1 - (1 - max(0.0, min(1.0, (t - 0.1) / 0.55))) ** 3
    v = v0 + (v1 - v0) * k
    R, cx, cy = H * 0.2, W / 2, H * 0.42
    wd = max(6, int(H * 0.022))
    a = _ease(t / 0.12)
    d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=_rgba(_c("text"), 0.18 * a), width=wd)
    d.arc((cx - R, cy - R, cx + R, cy + R), -90, -90 + 360 * max(0.0, min(1.0, v / 100)), fill=_rgba(_c("accent"), a), width=wd)
    d.text((cx, cy), pre + _fmt(v, decs, thou, dec) + suf, font=font(H * 0.11, bold=True), fill=_rgba(_c("text"), a), anchor="mm")
    if g.get("label"):
        _spaced(d, (W / 2, cy + R + H * 0.09), g["label"].upper(), font(H * 0.03), _rgba(_c("text"), _ease((t - 0.45) / 0.2) * 0.85),
                H * 0.006)
    return _comp(im, L)


def span_frame(g, W, H, t, photo=None):
    """Отрезок лет: «1955 ◆━━━━━▶ 2013», линия тянется слева направо, подпись под ней."""
    im = _vignette(_backdrop(photo, W, H, t), 0.7)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    a = _ease(t / 0.15)
    _kick(d, W, H, H * 0.36, g.get("kicker"), a)
    fy = font(H * 0.08, bold=True)
    y = H * 0.46
    d.text((W * 0.22, y), g["a"], font=fy, fill=_rgba(_c("text"), a), anchor="rm")
    k = _ease((t - 0.15) / 0.45)
    x0, x1 = W * 0.24, W * 0.76
    xe = x0 + (x1 - x0) * k
    d.line((x0, y, xe, y), fill=_rgba(_c("accent"), a), width=max(3, H // 180))
    r = H * 0.012
    d.polygon([(x0, y - r), (x0 + r, y), (x0, y + r), (x0 - r, y)], fill=_rgba(_c("accent"), a))
    d.polygon([(xe + r * 1.4, y), (xe - r * 0.6, y - r), (xe - r * 0.6, y + r)], fill=_rgba(_c("accent"), a))
    if g.get("b"):
        d.text((W * 0.78, y), g["b"], font=fy, fill=_rgba(_c("text"), _ease((t - 0.55) / 0.15)), anchor="lm")
    _sub(d, W, H, y + H * 0.09, g.get("sub"), _ease((t - 0.5) / 0.2), italic=False)
    return _comp(im, L)


def ribbon_frame(g, W, H, t, photo=None):
    """Золотая плашка-лента с тёмными капсами по центру, подпись ниже."""
    im = _vignette(_backdrop(photo, W, H, t, blur=False), 0.6)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    a = _ease((t - 0.05) / 0.18)
    txt = g["text"].upper()
    fn = font(H * 0.06, bold=True)
    tw = d.textlength(txt, font=fn) + H * 0.12
    w = tw * _ease((t - 0.03) / 0.25)
    y = H * 0.45
    if w > 12:  # в первые кадры ленты ещё нет — рамка вывернулась бы наизнанку
        _shadow(L, (W / 2 - w / 2, y - H * 0.055, W / 2 + w / 2, y + H * 0.055), 2, 0.5 * a, 10)
        d = ImageDraw.Draw(L)
        d.rectangle((W / 2 - w / 2, y - H * 0.055, W / 2 + w / 2, y + H * 0.055), fill=_rgba(_c("accent"), a))
        d.rectangle((W / 2 - w / 2 + 5, y - H * 0.055 + 5, W / 2 + w / 2 - 5, y + H * 0.055 - 5), outline=_rgba(_c("accent2"), a), width=1)
    if w >= tw * 0.98:
        _spaced(d, (W / 2, y), txt, fn, _rgba(_c("bg"), _ease((t - 0.25) / 0.15)), H * 0.006)
    _sub(d, W, H, y + H * 0.11, g.get("sub"), _ease((t - 0.4) / 0.2), italic=False)
    return _comp(im, L)


def news_frame(g, W, H, t, photo=None):
    """Газетная полоса: бумага, шапка-название, жирный заголовок и подзаголовок; чуть наклонена, медленный наезд."""
    im = _vignette(_backdrop(photo, W, H, t), 0.7)
    a = _ease(t / 0.15)
    pw, ph = int(W * 0.56), int(H * 0.62)
    P = Image.new("RGBA", (pw, ph), _rgba(_c("paper"), 1))
    pd = ImageDraw.Draw(P)
    ink = (*_c("bg"), 255)
    fp = font(ph * 0.08, bold=True)
    pd.text((pw / 2, ph * 0.1), g.get("paper") or "", font=fp, fill=ink, anchor="mm")
    for yy in (ph * 0.17, ph * 0.18):
        pd.line((pw * 0.05, yy, pw * 0.95, yy), fill=ink, width=1)
    fh = font(ph * 0.085, bold=True)
    y = ph * 0.3
    for ln in _wrap(pd, g["text"], fh, pw * 0.88)[:3]:
        pd.text((pw / 2, y), ln, font=fh, fill=ink, anchor="mm")
        y += ph * 0.1
    if g.get("sub"):
        for ln in _wrap(pd, g["sub"], font(ph * 0.045, italic=True), pw * 0.8)[:2]:
            pd.text((pw / 2, y + ph * 0.02), ln, font=font(ph * 0.045, italic=True), fill=ink, anchor="mm")
            y += ph * 0.06
    gray = (*_lift(_c("bg"), 2.5), 255)
    for i in range(int((ph * 0.95 - y - ph * 0.06) / (ph * 0.035))):  # «колонки текста»
        yy = y + ph * 0.08 + i * ph * 0.035
        for c in range(3):
            x0 = pw * (0.05 + c * 0.31)
            pd.line((x0, yy, x0 + pw * 0.27 * (0.7 if i % 5 == 4 else 1), yy), fill=gray, width=max(1, ph // 220))
    sc = 1.0 + 0.05 * t
    P = P.resize((int(pw * sc), int(ph * sc))).rotate(-2.5, expand=True, resample=Image.BICUBIC)
    P.putalpha(P.getchannel("A").point(lambda v: int(v * a)))
    L = _layer(W, H)
    L.alpha_composite(P, (int(W / 2 - P.width / 2), int(H / 2 - P.height / 2 + (1 - a) * H * 0.04)))
    return _comp(im, L)


def telegram_frame(g, W, H, t, photo=None):
    """Телеграмма: бланк, «TELEGRAMM» и шапка, текст печатается машинописью по буквам."""
    im = _vignette(_backdrop(photo, W, H, t), 0.7)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    a = _ease(t / 0.12)
    x0, y0, x1, y1 = W * 0.22, H * 0.2, W * 0.78, H * 0.8
    _shadow(L, (x0, y0, x1, y1), 4, 0.5 * a, 14)
    d = ImageDraw.Draw(L)
    d.rectangle((x0, y0, x1, y1), fill=_rgba(_c("paper"), a))
    ink = _c("bg")
    word = {"de": "TELEGRAMM", "ru": "ТЕЛЕГРАММА"}.get(_lang(), "TELEGRAM")
    _spaced(d, (W / 2, y0 + H * 0.06), word, font(H * 0.04, bold=True), _rgba(_c("hl"), a), H * 0.01)
    d.line((x0 + W * 0.03, y0 + H * 0.1, x1 - W * 0.03, y0 + H * 0.1), fill=_rgba(ink, a * 0.6), width=1)
    if g.get("head"):
        d.text((x0 + W * 0.03, y0 + H * 0.14), g["head"], font=font(H * 0.028, italic=True), fill=_rgba(ink, a * 0.8), anchor="lm")
    fn = _mono(H * 0.036)
    txt = g["text"].upper()
    shown = _typed(txt, max(0.0, t - 0.15) * DUR["telegram"], cps=22)
    y = y0 + H * 0.22
    for ln in _wrap(d, shown, fn, x1 - x0 - W * 0.06)[:8]:
        d.text((x0 + W * 0.03, y), ln, font=fn, fill=_rgba(ink, a), anchor="lm")
        y += H * 0.055
    return _comp(im, L)


def _mono(size):
    for f in ("cour.ttf", "courbd.ttf", "DejaVuSansMono.ttf"):
        p = fonts.find(f)
        if p:
            return ImageFont.truetype(p, int(size))
    return font(size)


def fact_frame(g, W, H, t, photo=None):
    """«WUSSTEST DU?» — наклонная золотая плашка сверху, факт крупно, подпись курсивом."""
    im = _vignette(_backdrop(photo, W, H, t), 0.65)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    a = _ease(t / 0.15)
    tag = {"de": "WUSSTEST DU?", "ru": "А ВЫ ЗНАЛИ?"}.get(_lang(), "DID YOU KNOW?")
    ft = font(H * 0.04, bold=True)
    tw = d.textlength(tag, font=ft) + H * 0.08
    S = Image.new("RGBA", (int(tw), int(H * 0.08)), _rgba(_c("accent"), 1))
    ImageDraw.Draw(S).text((S.width / 2, S.height / 2), tag, font=ft, fill=(*_c("bg"), 255), anchor="mm")
    S = S.rotate(4, expand=True, resample=Image.BICUBIC)
    S.putalpha(S.getchannel("A").point(lambda v: int(v * a)))
    L.alpha_composite(S, (int(W / 2 - S.width / 2), int(H * 0.27)))
    a2 = _ease((t - 0.2) / 0.2)
    fn = font(H * 0.06)
    lines = _wrap(d, g["text"], fn, W * 0.68)[:3]
    for i, ln in enumerate(lines):
        _rich(d, (W / 2, H * 0.48 + i * H * 0.075), ln, fn, a2)
    _sub(d, W, H, H * 0.5 + len(lines) * H * 0.075 + H * 0.03, g.get("sub"), _ease((t - 0.45) / 0.2))
    return _comp(im, L)


def circles_frame(g, W, H, t, photo=None):
    """Фон разделён на две половины (светлый/тёмный беж), на каждой — круглое фото, всплывает по очереди, подпись снизу."""
    im = Image.new("RGB", (W, H), "#C8A86A")
    ImageDraw.Draw(im).rectangle((W // 2, 0, W, H), fill="#A88A55")
    L = _layer(W, H)
    items = g.get("items") or [""]
    n = min(len(items), 3)
    r = H * 0.22
    for i, cap in enumerate(items[:n]):
        a = _ease((t - 0.08 - i * 0.3) / 0.25)
        if a <= 0:
            continue
        cx, cy = W * (i + 0.5) / n, H * 0.45
        rr = r * (0.85 + 0.15 * a)
        ph = _pp(i, photo)
        L = _shadow(L, (cx - rr + 6, cy - rr + 10, cx + rr + 6, cy + rr + 10), rr, a, H * 0.02)
        if ph is not None:
            _paste(L, _rounded(ph, rr * 2, rr * 2, rr), (cx - rr, cy - rr), a)
        else:
            ImageDraw.Draw(L).ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=_rgba(_c("felt"), a))
        if cap:
            d = ImageDraw.Draw(L)
            fl = _fit(d, cap.upper(), True, H * 0.034, W / n * 0.8)
            _spaced(d, (cx, cy + r + H * 0.09), cap.upper(), fl, _rgba(_c("ink"), a), H * 0.004)
    return _comp(im, L)


def hex_frame(g, W, H, t, photo=None):
    """Сеть шестиугольников на тёмном: центр с названием, вокруг узлы-фото с подписями, золотые линии вырастают к ним."""
    import math
    im = _tex("charcoal", W, H)
    L = _layer(W, H)
    d = ImageDraw.Draw(L)
    cx, cy, R = W / 2, H / 2, H * 0.11
    items = (g.get("items") or [])[:6]
    n = max(len(items), 1)

    def hexpts(x, y, r):
        return [(x + r * math.cos(math.radians(60 * k + 30)), y + r * math.sin(math.radians(60 * k + 30))) for k in range(6)]

    for i, cap in enumerate(items):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        x, y = cx + math.cos(ang) * W * 0.27, cy + math.sin(ang) * H * 0.32
        a = _ease((t - 0.3 - i * 0.18) / 0.3)
        if a <= 0:
            continue
        d.line((cx, cy, cx + (x - cx) * a, cy + (y - cy) * a), fill=_rgba(_c("accent"), 1), width=max(2, int(H * 0.004)))
        if True:
            ph = _pp(i, photo)
            hr = R * 0.8 * a
            mask = Image.new("L", (int(hr * 2) + 2, int(hr * 2) + 2), 0)
            ImageDraw.Draw(mask).polygon(hexpts(hr + 1, hr + 1, hr), fill=255)
            if ph is not None and hr > 2:
                tile = ph.convert("RGB").resize(mask.size)
                L.paste(tile, (int(x - hr - 1), int(y - hr - 1)), mask)
            d.polygon(hexpts(x, y, hr), outline=_rgba(_c("accent"), 1), width=max(2, int(H * 0.004)))
            fl = _fit(d, cap.upper(), True, H * 0.026, W * 0.2)
            _spaced(d, (x, y + R * 0.8 + H * 0.04), cap.upper(), fl, _rgba(_c("text"), a), H * 0.003)
    a0 = _ease(t / 0.3)
    d.polygon(hexpts(cx, cy, R * a0 + 1), fill=_rgba(_c("accent"), 1))
    txt = (g.get("center") or "").upper()
    if txt and a0 > 0.6:
        fl = _fit(d, txt, True, H * 0.03, R * 1.6)
        _spaced(d, (cx, cy), txt, fl, _rgba(_c("ink"), 1), H * 0.003)
    return _comp(im, L)


INFO = {"counter": counter_frame, "chart": chart_frame, "line": line_frame, "timeline": timeline_frame,
        "compare": compare_frame, "list": list_frame, "title": title_frame, "date": date_frame, "rewind": rewind_frame,
        "person": person_frame, "profile": profile_frame, "split": split_frame, "ask": ask_frame, "card": card_frame,
        "portrait": portrait_frame, "kicker": kicker_frame, "myth": myth_frame, "check": check_frame,
        "facts": facts_frame, "stamp": stamp_frame, "calendar": calendar_frame, "ring": ring_frame, "span": span_frame,
        "ribbon": ribbon_frame, "news": news_frame, "telegram": telegram_frame, "fact": fact_frame,
        "circles": circles_frame, "hex": hex_frame}


# ---------------------------------------------------------------- рендер

def frame_at(g, W, H, t, photo=None, cache=None):
    kind = g["type"]
    if kind == "chapter":
        if _T().get("chapter_style") == "paper":
            return chapter_paper_frame(g, W, H, t, photo)
        return chapter_frame(g, W, H, t, photo)
    if kind == "quote":
        return quote_frame(g, W, H, t, photo)
    if kind == "map":
        if cache is not None and "map" in cache:
            base, P = cache["map"]
        else:
            base, P = map_base(g, W, H)
            if cache is not None:
                cache["map"] = (base, P)
        return map_frame(base, P, g, W, H, t)
    if kind == "table":
        return table_frame(g, W, H, t, photo)
    if kind in INFO:
        return INFO[kind](g, W, H, t, photo)
    raise ValueError("неизвестная вставка: " + kind)


def _photos(paths):
    return [Image.open(p).convert("RGB") for p in (paths or []) if p and Path(p).exists()]


def render(g, out: Path, W, H, fps, dur, photo_path=None, norm="", enc=(), theme=None, photos=None):
    photo = Image.open(photo_path).convert("RGB") if photo_path and Path(photo_path).exists() else None
    if photo is not None and g["type"] != "chapter":
        photo = _cover(photo, W, H)
    F = max(1, int(round(dur * fps)))
    cache = {}
    _TL.t = make_theme(theme)
    _TL.photos = _photos(photos)
    try:
        frames = (frame_at(g, W, H, f / max(1, F - 1), photo, cache) for f in range(F))
        return _pipe(out, W, H, fps, frames, norm, enc)
    finally:
        _TL.t = _TL.photos = None


def still(g, out: Path, W=640, H=360, photo_path=None, theme=None, t=0.95, photos=None):
    """Превью вставки (по умолчанию последний кадр) — для редактора и миниатюр."""
    photo = Image.open(photo_path).convert("RGB") if photo_path and Path(photo_path).exists() else None
    if photo is not None and g["type"] != "chapter":
        photo = _cover(photo, W, H)
    _TL.t = make_theme(theme)
    _TL.photos = _photos(photos)
    try:
        frame_at(g, W, H, t, photo, {}).save(out, quality=90)
    finally:
        _TL.t = _TL.photos = None
    return out
