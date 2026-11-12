"""Измерения музыки по звуку (Claude звук не слышит — ему нужны цифры).

Темп (BPM), тональность, яркость, баланс частот, громкость/динамика, смены треков.
Длинные миксы не декодируются целиком: берутся окна по 30 с, равномерно по ролику.
"""
import json
import subprocess
from pathlib import Path

import numpy as np

from . import ff

SR = 22050
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTES_RU = {"C": "до", "C#": "до-диез", "D": "ре", "D#": "ми-бемоль", "E": "ми", "F": "фа", "F#": "фа-диез",
            "G": "соль", "G#": "ля-бемоль", "A": "ля", "A#": "си-бемоль", "B": "си"}
# профили Крумхансла для мажора/минора
MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
BANDS = [("суббас <60", 20, 60), ("бас 60–250", 60, 250), ("низкая середина 250–800", 250, 800),
         ("середина 0.8–2.5к", 800, 2500), ("верх 2.5–8к", 2500, 8000), ("воздух >8к", 8000, 11025)]


def _decode(src, t0, dur):
    cmd = [ff.FFMPEG, "-v", "error", "-ss", f"{t0:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
           "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=120).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _stft(y, n=2048, hop=512):
    if len(y) < n:
        return np.zeros((n // 2 + 1, 1))
    win = np.hanning(n).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(y, n)[::hop] * win
    return np.abs(np.fft.rfft(frames, axis=1)).T  # (bins, frames)


def _tempo(S, hop=512):
    flux = np.maximum(0, np.diff(np.log1p(S), axis=1)).sum(axis=0)
    if len(flux) < 100:
        return None, 0.0
    flux = flux - flux.mean()
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    fps = SR / hop
    lags = np.arange(len(ac))
    ok = (lags >= fps * 60 / 180) & (lags <= fps * 60 / 55)
    if not ok.any() or ac[0] <= 0:
        return None, 0.0
    # предпочитаем «живой» диапазон 70–140, как делают трекеры темпа
    bpm_all = 60 * fps / np.maximum(lags, 1)
    weight = np.exp(-0.5 * (np.log2(bpm_all / 100) / 0.9) ** 2)
    score = np.where(ok, ac * weight, -np.inf)
    lag = int(np.argmax(score))
    # уточнение параболой
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        d = (a - c) / (2 * (a - 2 * b + c)) if (a - 2 * b + c) else 0
        lag = lag + d
    return round(float(60 * fps / lag), 1), float(ac[int(round(lag))] / ac[0])  # сила пульса 0..1


def _chroma(S):
    freqs = np.fft.rfftfreq(2048, 1 / SR)
    ok = (freqs > 55) & (freqs < 2000)
    pc = (np.round(12 * np.log2(freqs[ok] / 440.0)) + 9) % 12
    e = S[ok] ** 2
    ch = np.array([e[pc == k].sum() for k in range(12)])
    return ch / (ch.sum() or 1)


def _key(ch):
    best = None
    for k in range(12):
        for mode, prof in (("мажор", MAJ), ("минор", MIN)):
            r = np.corrcoef(ch, np.roll(prof, k))[0, 1]
            if best is None or r > best[0]:
                best = (r, NOTES[k], mode)
    r, note, mode = best
    return f"{NOTES_RU[note]} {mode} ({note}{'m' if mode == 'минор' else ''})", round(float(r), 2)


def _features(y):
    if len(y) < SR * 3:
        return None
    S = _stft(y)
    freqs = np.fft.rfftfreq(2048, 1 / SR)
    P = (S ** 2).mean(axis=1)
    tot = P.sum() or 1
    bands = {name: round(float(P[(freqs >= lo) & (freqs < hi)].sum() / tot * 100), 1) for name, lo, hi in BANDS}
    centroid = float((freqs * P).sum() / tot)
    # громкость по 0.5 с — динамика (разница тихих и громких мест)
    blk = SR // 2
    rms = np.sqrt(np.array([np.mean(y[i:i + blk] ** 2) for i in range(0, len(y) - blk, blk)]) + 1e-12)
    db = 20 * np.log10(rms)
    bpm, pulse = _tempo(S)
    key, conf = _key(_chroma(S))
    return {"bpm": bpm, "pulse": round(pulse, 2), "key": key, "key_conf": conf, "centroid_hz": round(centroid),
            "bands": bands, "rms_db": round(float(np.median(db)), 1),
            "dyn_db": round(float(np.percentile(db, 95) - np.percentile(db, 10)), 1)}


def _bright(c):
    return "тёмный/тёплый" if c < 1300 else "мягкий" if c < 2200 else "нейтральный" if c < 3200 else "яркий/звонкий"


def _pulse(p):
    return "ровный чёткий бит" if p > 0.35 else "бит есть, мягкий" if p > 0.18 else "почти без бита (эмбиент/свободный ритм)"


def _gaps(src, limit):
    """Паузы тишины ≥1 с — обычно стыки треков в миксе."""
    cmd = [ff.FFMPEG, "-hide_banner", "-t", f"{limit:.0f}", "-i", str(src), "-vn",
           "-af", "silencedetect=n=-40dB:d=1", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True, timeout=900).stderr
    return [float(l.split("silence_end: ")[1].split()[0]) for l in err.splitlines() if "silence_end: " in l]


def analyze(src: Path, out_dir: Path, duration: float, clips=3, windows=8, win=30.0):
    """Считает всё и пишет music.txt + music.json + music_clips/*.mp3. Возвращает dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = max(1, min(windows, int(duration // win)))
    starts = [max(0.0, duration * (k + 0.5) / n - win / 2) for k in range(n)]
    parts = []
    for t0 in starts:
        f = _features(_decode(src, t0, win))
        if f:
            parts.append({"t": round(t0), **f})
    if not parts:
        return None
    bpms = [p["bpm"] for p in parts if p["bpm"]]
    keys = {}
    for p in parts:
        keys[p["key"]] = keys.get(p["key"], 0) + 1
    avg_b = {k: round(float(np.mean([p["bands"][k] for p in parts])), 1) for k in parts[0]["bands"]}
    cen = float(np.median([p["centroid_hz"] for p in parts]))
    pulse = float(np.median([p["pulse"] for p in parts]))
    gaps = _gaps(src, min(duration, 4 * 3600)) if duration > 240 else []
    res = {"bpm": round(float(np.median(bpms)), 1) if bpms else None,
           "bpm_range": [float(min(bpms)), float(max(bpms))] if bpms else None,
           "key_main": max(keys, key=keys.get), "keys": keys, "centroid_hz": round(cen), "bright": _bright(cen),
           "pulse": round(pulse, 2), "pulse_txt": _pulse(pulse), "bands": avg_b,
           "rms_db": round(float(np.median([p["rms_db"] for p in parts])), 1),
           "dyn_db": round(float(np.median([p["dyn_db"] for p in parts])), 1),
           "track_changes": len(gaps), "avg_track_min": round(duration / (len(gaps) + 1) / 60, 1) if gaps else None,
           "windows": parts}
    # куски для сравнения на слух
    cd = out_dir / "music_clips"
    cd.mkdir(exist_ok=True)
    res["clips"] = []
    for k in range(clips):
        t0 = duration * (k + 1) / (clips + 1)
        o = cd / f"clip{k + 1}_{int(t0)}s.mp3"
        try:
            ff.run(["-ss", round(t0, 1), "-t", 20, "-i", src, "-vn", "-ac", 2, "-b:a", "160k", o])
            res["clips"].append(f"music_clips/{o.name}")
        except Exception:
            pass
    (out_dir / "music.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "music.txt").write_text(report(res), encoding="utf-8")
    return res


def report(r):
    b = r["bands"]
    lines = [
        f"Темп: ~{r['bpm']} BPM (по окнам {r['bpm_range'][0]}–{r['bpm_range'][1]}); "
        "детектор может ошибаться вдвое — 60 и 120 BPM для него похожи" if r["bpm"] else "Темп: не определился",
        f"Ритм: {r['pulse_txt']} (сила пульса {r['pulse']})",
        f"Тональность: {r['key_main']}; по окнам: " + ", ".join(f"{k} ×{v}" for k, v in r["keys"].items()),
        f"Звук: {r['bright']} (спектральный центр {r['centroid_hz']} Гц)",
        "Баланс частот, % энергии: " + "; ".join(f"{k} {v}" for k, v in b.items()),
        f"Громкость: медиана {r['rms_db']} дБ, динамика {r['dyn_db']} дБ "
        f"({'сжатый ровный' if r['dyn_db'] < 8 else 'умеренная' if r['dyn_db'] < 15 else 'большая — тихие и громкие места'})",
    ]
    if r.get("track_changes"):
        lines.append(f"Смен треков (паузы ≥1 с): {r['track_changes']}, средний трек ~{r['avg_track_min']} мин")
    lines.append("\nПо окнам (время → BPM, тональность, центр Гц, пульс):")
    for p in r["windows"]:
        lines.append(f"  {int(p['t'] // 60)}:{int(p['t'] % 60):02d} → {p['bpm']} BPM, {p['key']}, "
                     f"{p['centroid_hz']} Гц, пульс {p['pulse']}")
    return "\n".join(lines)


def quick(path: Path):
    """Короткая сводка по одному треку (для журнала оценок): без файлов и кусков."""
    dur = ff.duration(path)
    n = max(1, min(3, int(dur // 30)))
    parts = [f for f in (_features(_decode(path, max(0.0, dur * (k + 0.5) / n - 15), 30)) for k in range(n)) if f]
    if not parts:
        return None
    bpms = [p["bpm"] for p in parts if p["bpm"]]
    cen = float(np.median([p["centroid_hz"] for p in parts]))
    pulse = float(np.median([p["pulse"] for p in parts]))
    return {"bpm": round(float(np.median(bpms)), 1) if bpms else None, "key": parts[0]["key"],
            "bright": _bright(cen), "centroid_hz": round(cen), "pulse_txt": _pulse(pulse)}


def summary(q):
    if not q:
        return "не измерено"
    return f"~{q['bpm']} BPM, {q['key']}, {q['bright']} ({q['centroid_hz']} Гц), {q['pulse_txt']}"
