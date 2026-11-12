"""Разбор референса: формат, темп монтажа, громкость, раскадровка-превью → пресет стиля."""
import re
import statistics
import time
from pathlib import Path

from . import ff, store


def _mmss(sec):
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def _num(n):
    if not n:
        return "?"
    return f"{n / 1e6:.1f} млн" if n >= 1e6 else f"{n / 1e3:.0f} тыс" if n >= 1e3 else str(n)


def analyze(r: dict, log=None):
    log = log or (lambda *_: None)
    d = store.REFS / r["id"]
    src = d / r["file"]
    info = ff.video_info(src)
    dur = info["duration"] or 1
    log(f"  видео: {info['width']}×{info['height']}, {info['fps']} к/с, длина {_mmss(dur)}"
        f"{', есть звук' if info['has_audio'] else ', без звука'}")
    log("  ищу склейки (смены планов)…")
    cuts = ff.scene_cuts(src)
    bounds = [0.0] + cuts + [dur]
    shots = [b - a for a, b in zip(bounds, bounds[1:]) if b - a > 0.15]
    avg = statistics.mean(shots) if shots else dur
    med = statistics.median(shots) if shots else dur
    vert = info["height"] > info["width"]
    log(f"  склеек: {len(cuts)} ({len(cuts) / dur * 60:.1f} в минуту), план: медиана {med:.1f} с, "
        f"среднее {avg:.1f} с, от {min(shots, default=0):.1f} до {max(shots, default=0):.1f} с")
    loud = ff.loudness(src) if info["has_audio"] else None
    if loud is not None:
        log(f"  громкость: в среднем {loud} дБ")

    # раскадровка: 12 кадров равномерно
    frames = []
    (d / "frames").mkdir(exist_ok=True)
    for k in range(12):
        t = dur * (k + 0.5) / 12
        out = d / "frames" / f"f{k:02d}.jpg"
        try:
            ff.thumb(src, out, t=round(t, 2), width=360)
            frames.append(f"frames/{out.name}")
        except Exception:
            pass

    r["analysis"] = {
        "width": info["width"], "height": info["height"], "fps": info["fps"], "duration": round(dur, 1),
        "format": "9:16" if vert else "16:9", "cuts": len(cuts), "avg_shot": round(avg, 2),
        "median_shot": round(med, 2), "cuts_per_min": round(len(cuts) / dur * 60, 1),
        "has_audio": info["has_audio"], "mean_volume_db": loud,
    }
    r["frames"] = frames
    log(f"  раскадровка: {len(frames)} кадров, смотрю подачу фото и плашки…")
    share = card_share([d / f for f in frames])
    r["analysis"]["photo_card_share"] = share
    style = dict(store.DEFAULT_STYLE)
    style.update(r.get("style") or {})
    style["format"] = "9:16" if vert else "16:9"
    style["shot_len"] = round(min(8.0, max(1.0, med)), 1)
    style["transition_dur"] = 0.25 if med < 2.5 else 0.45
    if vert:
        style["sub_position"] = "center"
        style["sub_words"] = 3
    if share is not None:
        style["photo_card"], style["photo_card_share"] = card_mode(share), share
    pc = plate_color([d / f for f in frames])
    if pc:
        style["plate_color"] = r["analysis"]["plate_color"] = pc
    card = "не определить" if share is None else f"{share * 100:.0f}% кадров → режим «{style['photo_card']}»"
    log(f"  фото карточкой: {card}; плашки: {'цвет ' + pc if pc else 'не найдены'}")
    log(f"  → стиль: формат {style['format']}, кадр {style['shot_len']} с, переход {style['transition_dur']} с")
    r["style"] = style
    return store.save_ref(r)


def _is_card(path: Path):
    """Кадр «карточкой»: со всех четырёх сторон ровная тёмная рамка, а внутри — картинка."""
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        a = np.asarray(im.convert("RGB"), dtype=np.int16)
    H, W, _ = a.shape
    g = a.mean(axis=2)
    # край кадра — ровный тёмный фон
    c = max(2, int(min(H, W) * 0.04))
    edge = np.concatenate([g[:c].ravel(), g[-c:].ravel(), g[:, :c].ravel(), g[:, -c:].ravel()])
    if edge.mean() > 110 or edge.std() > 20:
        return False
    # у карточки четыре длинных прямых края фото; у рисованной анимации и полноэкранных кадров их нет
    dy = np.abs(np.diff(g, axis=0)) > 8
    dx = np.abs(np.diff(g, axis=1)) > 8
    zy, zx = int(H * 0.4), int(W * 0.4)
    t = int(np.argmax(dy[:zy].mean(1)))
    b = H - 1 - zy + int(np.argmax(dy[-zy:].mean(1)))
    l = int(np.argmax(dx[:, :zx].mean(0)))
    r = W - 1 - zx + int(np.argmax(dx[:, -zx:].mean(0)))
    if t < 2 or l < 2 or b > H - 4 or r > W - 4:
        return False
    s = [dy[t, l:r].mean(), dy[b, l:r].mean(), dx[t:b, l].mean(), dx[t:b, r].mean()]
    return min(s) >= 0.5 and sum(s) / 4 >= 0.6


def card_share(paths):
    ok = [p for p in paths if p.exists()]
    if not ok:
        return None
    return round(sum(1 for p in ok if _is_card(p)) / len(ok), 2)


def plate_boxes(path):
    """Плашки в кадре: ровно залитый прямоугольник среднего размера, внутри — контрастный текст."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    with Image.open(path) as im:
        a = np.asarray(im.convert("RGB"), dtype=np.int16)
    H, W, _ = a.shape
    d1 = np.abs(a[1:, :-1] - a[:-1, :-1]).sum(2)
    d2 = np.abs(a[:-1, 1:] - a[:-1, :-1]).sum(2)
    flat = np.zeros((H, W), bool)
    flat[:-1, :-1] = (d1 < 18) & (d2 < 18)
    lab, n = ndimage.label(flat)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if not (0.04 * H <= h <= 0.2 * H and 0.08 * W <= w <= 0.7 * W and w > 1.8 * h):
            continue
        if sl[0].start < 2 or sl[1].start < 2 or sl[0].stop > H - 2 or sl[1].stop > W - 2:
            continue
        m = lab[sl] == i
        if m.mean() < 0.25:
            continue
        # прямоугольник: верхняя/нижняя строка и боковые столбцы почти целиком залиты
        if min(m[0].mean(), m[-1].mean()) < 0.55 or min(m[:, 0].mean(), m[:, -1].mean()) < 0.6:
            continue
        # текст внутри, а не по краям
        core = ~m[h // 5: h - h // 5 or None, w // 20: w - w // 20 or None]
        if core.sum() < 0.6 * (~m).sum():
            continue
        fill = a[sl][m].mean(0)
        inner = a[sl][~m]
        txt = (np.abs(inner - fill).sum(1) > 150).sum() / m.size
        if 0.02 <= txt <= 0.4:
            out.append((fill, int(m.sum())))
    return out


def plate_color(paths):
    """Цвет плашек референса (самый частый среди найденных); нет плашек — None."""
    import numpy as np
    found = []
    for p in paths:
        if p.exists():
            found += plate_boxes(p)
    if not found:
        return None
    # плашки обычно одного цвета — берём самый частый
    q = {}
    for c, n in found:
        k = tuple((c // 20).astype(int))
        q.setdefault(k, []).append(c)
    best = max(q.values(), key=len)
    return "#%02X%02X%02X" % tuple(int(x) for x in np.mean(best, 0))


def card_mode(share):
    # как в референсе: почти все карточкой → все; вперемешку → та же доля; редко → только не по формату
    return "all" if share >= 0.85 else "mix" if share >= 0.3 else "auto" if share >= 0.05 else "off"


# ---------------------------------------------------------------- референс = YouTube-канал

def _ydl(opts):
    import yt_dlp
    # socket_timeout: без него зависшее соединение ждёт бесконечно
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True,
                             "socket_timeout": 30, "retries": 5, "fragment_retries": 5, **opts})


def _progress(log):
    """Прогресс скачивания в лог: каждые 25%, по каждой дорожке (видео/звук)."""
    last = {}

    def hook(h):
        fn = Path(h.get("filename") or "").name
        if h["status"] == "downloading":
            tot = h.get("total_bytes") or h.get("total_bytes_estimate")
            if not tot:
                return
            pct = int(h.get("downloaded_bytes", 0) * 100 / tot) // 25 * 25
            if pct > last.get(fn, -1):
                last[fn] = pct
                sp = h.get("speed")
                log(f"  {fn}: {pct}% из {tot / 2**20:.0f} МБ" + (f", {sp / 2**20:.1f} МБ/с" if sp else ""))
    return hook


def _h264(f: Path, log):
    """YouTube отдаёт до 480p в AV1/VP9 — не каждый браузер его покажет. Перекодируем в H.264/AAC."""
    v = next((x for x in ff.probe(f).get("streams", []) if x.get("codec_type") == "video"), {})
    if v.get("codec_name") == "h264":
        return f
    log(f"  перекодирую {f.name} ({v.get('codec_name') or '?'} → H.264), чтобы ролик открывался в браузере…")
    out = f.with_name(f.stem + ".h264.mp4")
    ff.run(["-i", f, "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out])
    f.unlink()
    return out.replace(f.with_suffix(".mp4"))


def from_youtube(r: dict, n_videos=3, log=print):
    """Ссылка на канал/видео → скачиваем несколько последних роликов (360p), анализируем, усредняем стиль."""
    url = r["url"].strip()
    name0 = r.get("name")
    d = store.REFS / r["id"]
    d.mkdir(parents=True, exist_ok=True)
    is_channel = any(x in url for x in ("/@", "/channel/", "/c/", "/user/")) and "watch?v=" not in url
    shorts = "/shorts" in url
    if is_channel:
        url = url.split("?")[0]  # ?si=… из «Поделиться» мешает дописать /videos
    if is_channel and not re.search(r"/(videos|shorts|streams)/?$", url):
        url = url.rstrip("/") + "/videos"
    log(f"Читаю список роликов: {url}")
    with _ydl({"extract_flat": True, "playlistend": 30}) as y:
        info = y.extract_info(url, download=False)
    entries = [e for e in (info.get("entries") or [info]) if e]
    r["channel_title"] = info.get("channel") or info.get("uploader") or info.get("title") or ""
    subs = info.get("channel_follower_count")
    log(f"Канал: {r['channel_title'] or '?'}" + (f", подписчиков {_num(subs)}" if subs else "")
        + f". Нашёл роликов: {len(entries)}" + (" (первые 30, сначала новые)" if is_channel else ""))
    for i, e in enumerate(entries[:10]):
        mark = "→" if i < n_videos else " "
        log(f"  {mark} {i + 1:2d}. {(e.get('title') or '')[:70]} · {_mmss(e.get('duration'))}"
            f" · {_num(e.get('view_count'))} просм.")
    if len(entries) > 1:
        log(f"Беру для замера {min(n_videos, len(entries))} последних (→): по ним видно, "
            f"как канал монтирует сейчас; стиль — медиана по ним")
    if not r.get("name") or r["name"] == "YouTube":
        r["name"] = r["channel_title"] or "YouTube-канал"
    r["titles"] = [e.get("title") for e in entries if e.get("title")][:30]
    r["video_urls"] = [u if str(u).startswith("http") else "https://www.youtube.com/watch?v=" + str(u)
                       for u in (e.get("url") or e.get("webpage_url") or e.get("id") for e in entries[:n_videos]) if u]
    vids = []
    for e in entries[:n_videos]:
        vurl = e.get("url") or e.get("webpage_url") or e.get("id")
        if vurl and not str(vurl).startswith("http"):
            vurl = "https://www.youtube.com/watch?v=" + vurl
        k = len(vids)
        log(f"Скачиваю пример {k + 1}/{min(n_videos, len(entries))}: {(e.get('title') or '')[:60].strip()}"
            f" ({_mmss(e.get('duration'))}, до 480p — для замера хватает)")
        t0 = time.time()
        try:
            # сначала H.264 (avc1) — его играет любой браузер; AV1/VP9 перекодируем ниже
            with _ydl({"format": "bv*[height<=480][vcodec^=avc1]+ba[ext=m4a]/b[height<=480][vcodec^=avc1]/"
                                 "best[height<=480][ext=mp4]/bv*[height<=480]+ba/best[height<=480]/best",
                       "outtmpl": str(d / f"v{k}.%(ext)s"), "merge_output_format": "mp4",
                       "progress_hooks": [_progress(log)]}) as y:
                y.download([vurl])
            f = next(iter(sorted(d.glob(f"v{k}.*"))), None)
            if f:
                log(f"  скачано: {f.stat().st_size / 2**20:.0f} МБ за {time.time() - t0:.0f} с")
                vids.append(_h264(f, log))
        except Exception as ex:
            log(f"  не скачалось: {ex}")
    if not vids:
        raise RuntimeError("Не удалось скачать ни одного ролика с канала")
    # анализ каждого, стиль — по медиане
    results = []
    for k, f in enumerate(vids):
        log(f"Анализирую монтаж {k + 1}/{len(vids)}: {f.name}")
        rr = dict(r, file=f.name)
        rr = analyze(rr, log)
        results.append(rr)
    base = results[-1]  # кадры-превью остались от последнего проанализированного
    meds = [x["analysis"]["median_shot"] for x in results]
    st = base["style"]
    med = statistics.median(meds)
    st["shot_len"] = round(min(8.0, max(1.0, med)), 1)
    st["transition_dur"] = 0.25 if med < 2.5 else 0.45
    base["analysis"]["median_shot"] = round(med, 2)
    base["analysis"]["avg_shot"] = round(statistics.mean(x["analysis"]["avg_shot"] for x in results), 2)
    base["analysis"]["videos"] = len(results)
    shares = [x["analysis"]["photo_card_share"] for x in results if x["analysis"].get("photo_card_share") is not None]
    if shares:
        base["analysis"]["photo_card_share"] = round(statistics.mean(shares), 2)
        st["photo_card"] = card_mode(base["analysis"]["photo_card_share"])
        st["photo_card_share"] = base["analysis"]["photo_card_share"]
    if shorts:
        st["format"] = base["analysis"]["format"] = "9:16"
    base["file"] = vids[-1].name
    base["videos"] = [f.name for f in vids]
    base["source"] = "youtube"
    log(f"Итог по {len(results)} роликам: медианы плана {', '.join(f'{m:.1f}' for m in meds)} с → "
        f"длина кадра {st['shot_len']} с, переход {st['transition_dur']} с, формат {st['format']}, "
        f"фото карточкой «{st.get('photo_card')}», "
        f"плашки {base['analysis'].get('plate_color') or 'не найдены (цвет по умолчанию)'}")
    log("Готово: стиль сохранён в референс, его можно выбрать в проекте")
    disk = store.load_ref(r["id"])  # лог писался прямо в файл, а имя могли поменять во время разбора
    base["log"] = disk.get("log") or []
    if disk.get("name") and disk["name"] != name0:
        base["name"] = disk["name"]
    return store.save_ref(base)
