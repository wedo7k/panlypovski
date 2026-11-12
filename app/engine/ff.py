"""Обёртки над ffmpeg / ffprobe."""
import json
import re
import shutil
import subprocess
from pathlib import Path

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# на Mac обычный brew-ffprobe бывает сломан (нет libx265), рабочий лежит в ffmpeg-full
_EXTRA = ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin")


def _works(exe):
    try:
        return subprocess.run([exe, "-version"], capture_output=True, timeout=15, creationflags=_NOWIN).returncode == 0
    except Exception:
        return False


def _resolve(name):
    """Первая реально запускающаяся программа: из PATH, затем из ffmpeg-full, иначе что нашлось."""
    found = shutil.which(name)
    cands = [found] if found else []
    cands += [str(Path(d) / name) for d in _EXTRA if (Path(d) / name).exists()]
    for c in cands:
        if _works(c):
            return c
    return found or name


FFMPEG = _resolve("ffmpeg")
FFPROBE = _resolve("ffprobe")


class FFError(RuntimeError):
    pass


PROCS = {}  # id потока -> текущий ffmpeg (чтобы «Остановить» мог его убить)


def check():
    """Есть ли ffmpeg/ffprobe и умеют ли они то, что нужно для монтажа."""
    res = {}
    for name, exe in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        try:
            r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=15)
            if r.returncode != 0:  # файл есть, но не запускается (например, нет библиотеки libx265)
                why = (r.stderr or r.stdout or "").strip().splitlines()
                return {"ok": False, "msg": f"{name} сломан ({exe}): {why[0][:200] if why else r.returncode}. "
                                            "На Mac: brew install ffmpeg-full"}
            res[name] = r.stdout.splitlines()[0] if r.stdout else ""
        except Exception:
            res[name] = ""
    if not res["ffmpeg"]:
        return {"ok": False, "msg": "FFmpeg не найден — установи его и добавь в PATH (winget install Gyan.FFmpeg)"}
    if not res["ffprobe"]:
        return {"ok": False, "msg": "ffmpeg есть, но нет ffprobe — переустанови полную сборку FFmpeg"}
    enc = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15).stdout
    miss = [e for e in ("libx264", "aac", "libmp3lame") if e not in enc]
    ver = res["ffmpeg"].split(" Copyright")[0]
    if miss:
        return {"ok": False, "msg": f"{ver}, но нет кодеков: {', '.join(miss)} — нужна полная сборка"}
    return {"ok": True, "msg": f"{ver} — всё на месте", "path": FFMPEG}


def kill_thread(tid):
    pr = PROCS.get(tid)
    if pr and pr.poll() is None:
        pr.kill()


def run(args, cwd=None, timeout=None):
    import threading
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    tid = threading.get_ident()
    pr = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace", creationflags=_NOWIN)
    PROCS[tid] = pr
    try:
        out, err = pr.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        pr.kill()
        raise
    finally:
        PROCS.pop(tid, None)
    r = subprocess.CompletedProcess(cmd, pr.returncode, out, err)
    if r.returncode != 0:
        raise FFError((r.stderr or "")[-1500:])
    return r


def probe(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
                        "-show_streams", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=_NOWIN)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def duration(path) -> float:
    try:
        return float(probe(path).get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0


def video_info(path):
    info = probe(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 30.0
    try:
        n, d = (v.get("avg_frame_rate") or "30/1").split("/")
        fps = float(n) / float(d) if float(d) else 30.0
    except Exception:
        pass
    return {"width": int(v.get("width") or 0), "height": int(v.get("height") or 0), "fps": round(fps, 2),
            "duration": float(info.get("format", {}).get("duration", 0) or 0), "has_audio": a is not None}


def thumb(src, out, t=0.5, width=320):
    if Path(src).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        t = 0
    try:
        run(["-ss", t, "-i", src, "-frames:v", 1, "-vf", f"scale={width}:-2", out])
    except FFError:
        run(["-i", src, "-frames:v", 1, "-vf", f"scale={width}:-2", out])
    if not Path(out).exists():
        run(["-i", src, "-frames:v", 1, "-vf", f"scale={width}:-2", out])
    return out


def scene_cuts(path, threshold=10.0):
    """Времена склеек в видео (фильтр scdet)."""
    cmd = [FFMPEG, "-hide_banner", "-i", str(path), "-vf", f"scale=320:-2,scdet=threshold={threshold}",
           "-an", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=_NOWIN)
    return [float(m) for m in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", r.stderr)]


def loudness(path):
    cmd = [FFMPEG, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=_NOWIN)
    m = re.search(r"mean_volume:\s*(-?[\d.]+)", r.stderr)
    return float(m.group(1)) if m else None


def make_whoosh(out: Path):
    """Синтетический свуш — чтобы SFX работали из коробки без файлов."""
    if out.exists():
        return out
    run(["-f", "lavfi", "-i", "anoisesrc=d=0.7:c=pink:a=0.6",
         "-af", "bandpass=f=1200:width_type=o:w=2,afade=t=in:d=0.35:curve=exp,"
                "afade=t=out:st=0.35:d=0.35,volume=1.6,aformat=sample_rates=44100:channel_layouts=stereo",
         out])
    return out
