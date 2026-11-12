"""Озвучка (edge-tts бесплатно / ElevenLabs) и музыка (Suno API)."""
import asyncio
import time
from pathlib import Path

import requests

from . import ff


def tts_edge(text, voice, out: Path):
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, voice).save(str(out))

    last = None
    for attempt in range(4):  # сервис Edge иногда отвечает пустотой — повторяем
        try:
            asyncio.run(go())
            if out.exists() and out.stat().st_size > 0:
                return out
        except Exception as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Edge TTS не ответил: {last}")


def tts_elevenlabs(text, settings, out: Path, voice=None):
    key = settings.get("elevenlabs_key")
    if not key:
        raise RuntimeError("Нет ключа ElevenLabs")
    # голос из библиотеки ElevenLabs приходит как «voice_id|owner_id» — его надо один раз добавить себе
    vid, _, owner = (voice or settings.get("elevenlabs_voice") or "").partition("|")
    h = {"xi-api-key": key, "Content-Type": "application/json"}

    def go():
        return requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}", headers=h,
                             json={"text": text, "model_id": "eleven_multilingual_v2"}, timeout=180)
    r = go()
    if r.status_code in (400, 404) and owner:
        requests.post(f"https://api.elevenlabs.io/v1/voices/add/{owner}/{vid}", headers=h,
                      json={"new_name": f"yt-{vid[:8]}"}, timeout=60)
        r = go()
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
    out.write_bytes(r.content)
    return out


def elevenlabs_check(settings, out: Path):
    """Проверка ключа ElevenLabs: остаток символов + короткая тестовая фраза (тратит ~40 символов)."""
    key = settings.get("elevenlabs_key")
    if not key:
        return {"ok": False, "msg": "Ключ ElevenLabs не введён"}
    h = {"xi-api-key": key}
    try:
        r = requests.get("https://api.elevenlabs.io/v1/user/subscription", headers=h, timeout=20)
    except Exception as e:
        return {"ok": False, "msg": f"Нет связи с ElevenLabs: {e}"}
    left = ""
    if r.ok:
        j = r.json()
        left = f", осталось символов: {j.get('character_limit', 0) - j.get('character_count', 0)}"
    elif r.status_code == 401:
        try:
            det = r.json().get("detail")
        except Exception:
            det = r.text[:200]
        # ключ без права на подписку всё равно может озвучивать — тогда решит тестовая фраза
        if "missing_permissions" not in str(det):
            return {"ok": False, "msg": f"Ключ не принят: {det}"}
    if not settings.get("elevenlabs_voice"):
        return {"ok": False, "msg": "Ключ есть, но не указан Voice ID"}
    try:
        tts_elevenlabs("Проверка озвучки. Если ты это слышишь — всё работает.", settings, out)
    except Exception as e:
        return {"ok": False, "msg": f"Озвучка не запустилась: {e}"}
    return {"ok": True, "msg": f"Озвучка работает{left}", "audio": True}


def tts_local(text, lang, out: Path):
    """Офлайн-голос Windows (System.Speech). Работает без интернета, качество попроще."""
    import subprocess
    import sys
    if sys.platform != "win32":
        raise RuntimeError("Локальный голос сейчас есть только на Windows")
    wav = out.with_suffix(".local.wav")
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$v=$s.GetInstalledVoices()|?{{$_.VoiceInfo.Culture.Name -like '{lang}*'}}|select -First 1;"
        "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
        f"$s.SetOutputToWaveFile('{wav}');"
        f"$s.Speak([IO.File]::ReadAllText('{txt}',[Text.Encoding]::UTF8));$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=300)
    txt.unlink(missing_ok=True)
    if not wav.exists():
        raise RuntimeError("Локальный голос не сработал")
    ff.run(["-i", wav, "-c:a", "libmp3lame", "-q:a", 3, out])
    wav.unlink(missing_ok=True)
    return out


def tts_lmnt(text, lang, settings, out: Path, voice=None):
    key = settings.get("lmnt_key")
    if not key:
        raise RuntimeError("Нет ключа LMNT")
    r = requests.post("https://api.lmnt.com/v1/ai/speech/bytes", headers={"X-API-Key": key},
                      json={"voice": voice or "leah", "text": text, "language": lang, "format": "mp3"}, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"LMNT {r.status_code}: {r.text[:300]}")
    out.write_bytes(r.content)
    return out


def fish_url(settings):
    return f"http://127.0.0.1:{settings.get('fish_port') or 8180}"


def fish_up(settings, log=lambda m: None):
    """Поднимает локальный Fish Speech, если он ещё не запущен (первый старт 1-3 мин)."""
    import subprocess
    url = fish_url(settings)
    try:
        if requests.get(url + "/v1/health", timeout=3).ok:
            return url
    except Exception:
        pass
    d = Path(settings.get("fish_dir") or "F:/AI/fish-speech")
    py = d / "venv" / "Scripts" / "python.exe"
    if not py.exists():
        raise RuntimeError(f"Fish Speech не найден в {d}")
    log("  запускаю Fish Speech…")
    ck = "checkpoints/fish-speech-1.5"
    subprocess.Popen([str(py), "tools/api_server.py", "--listen", url.split("//")[1],
                      "--llama-checkpoint-path", ck, "--decoder-checkpoint-path",
                      f"{ck}/firefly-gan-vq-fsq-8x1024-21hz-generator.pth", "--decoder-config-name", "firefly_gan_vq"],
                     cwd=d, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdout=open(d / "api_server.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(90):
        time.sleep(2)
        try:
            if requests.get(url + "/v1/health", timeout=3).ok:
                return url
        except Exception:
            pass
    raise RuntimeError(f"Fish Speech не поднялся, смотри {d / 'api_server.log'}")


def tts_fish(text, settings, out: Path, voice=None, log=lambda m: None):
    """Локальная озвучка клоном голоса: voice = папка в fish-speech/references."""
    url = fish_up(settings, log)
    body = {"text": text.replace("ｗ", "").replace("♡", ""), "format": "wav", "chunk_length": 200,
            "top_p": 0.7, "repetition_penalty": 1.2, "temperature": 0.7, "seed": 42,
            "max_new_tokens": 1024, "use_memory_cache": "on"}
    if voice:
        body["reference_id"] = voice
    r = requests.post(url + "/v1/tts", json=body, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"Fish Speech {r.status_code}: {r.text[:300]}")
    wav = out.with_suffix(".fish.wav")
    wav.write_bytes(r.content)
    ff.run(["-i", wav, "-c:a", "libmp3lame", "-q:a", 2, out])
    wav.unlink(missing_ok=True)
    return out


_EDGE_ALL = []


def list_voices(provider, lang, settings):
    """Голоса сервиса для языка: [{id, name, lang, preview}]."""
    lang = (lang or "ru")[:2].lower()
    if provider == "edge":
        if not _EDGE_ALL:
            import edge_tts
            _EDGE_ALL.extend(asyncio.run(edge_tts.list_voices()))
        return [{"id": v["ShortName"], "name": f"{v['ShortName'].split('-')[-1].replace('Neural', '')} · {v['Gender']} · {v['Locale']}",
                 "lang": v["Locale"]} for v in _EDGE_ALL if v["Locale"].lower().startswith(lang)]
    if provider == "elevenlabs":
        key = settings.get("elevenlabs_key")
        if not key:
            raise RuntimeError("Нет ключа ElevenLabs (Настройки)")
        h = {"xi-api-key": key}
        res = []
        r = requests.get("https://api.elevenlabs.io/v1/voices", headers=h, timeout=30)
        if r.ok:  # свои голоса — мультиязычные, подходят к любому языку
            for v in r.json().get("voices", []):
                lab = v.get("labels") or {}
                res.append({"id": v["voice_id"], "name": f"⭐ {v['name']} · {lab.get('gender', '')} {lab.get('accent', '')}".strip(),
                            "lang": "мои", "preview": v.get("preview_url")})
        r = requests.get("https://api.elevenlabs.io/v1/shared-voices", headers=h,
                         params={"language": lang, "page_size": 100, "sort": "trending"}, timeout=30)
        if r.ok:
            for v in r.json().get("voices", []):
                res.append({"id": f"{v['voice_id']}|{v['public_owner_id']}",
                            "name": f"{v['name']} · {v.get('gender', '')} {v.get('accent', '')} · {v.get('use_case', '')}".strip(" ·"),
                            "lang": v.get("language"), "preview": v.get("preview_url")})
        elif not res:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
        return res
    if provider == "lmnt":
        key = settings.get("lmnt_key")
        if not key:
            raise RuntimeError("Нет ключа LMNT (Настройки)")
        r = requests.get("https://api.lmnt.com/v1/ai/voice/list", headers={"X-API-Key": key}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"LMNT {r.status_code}: {r.text[:200]}")
        return [{"id": v["id"], "name": f"{v.get('name', v['id'])} · {v.get('gender', '')} · {v.get('owner', '')}".strip(" ·"),
                 "lang": "все", "preview": v.get("preview_url")} for v in r.json()]
    if provider == "fish":
        d = Path(settings.get("fish_dir") or "F:/AI/fish-speech") / "references"
        return [{"id": p.name, "name": f"🎙 {p.name}", "lang": "все"}
                for p in sorted(d.iterdir()) if p.is_dir()] if d.exists() else []
    return []


SAMPLES = {
    "ru": "Привет! Так будет звучать озвучка твоего ролика.", "en": "Hi! This is how the voiceover of your video will sound.",
    "de": "Hallo! So wird die Stimme in deinem Video klingen.", "uk": "Привіт! Так звучатиме озвучка твого ролика.",
    "es": "¡Hola! Así sonará la voz de tu vídeo.", "fr": "Bonjour ! Voici comment sonnera la voix de ta vidéo.",
    "it": "Ciao! Ecco come suonerà la voce del tuo video.", "pt": "Olá! É assim que a narração do seu vídeo vai soar.",
    "pl": "Cześć! Tak będzie brzmiał lektor twojego filmu.", "cs": "Ahoj! Takhle bude znít hlas tvého videa.",
    "nl": "Hallo! Zo klinkt de stem van je video.", "tr": "Merhaba! Videonun seslendirmesi böyle olacak.",
    "ja": "こんにちは。動画のナレーションはこのように聞こえます。", "ko": "안녕하세요. 영상 내레이션은 이렇게 들립니다.",
    "zh": "你好！这就是你的视频配音的效果。", "ar": "مرحبا! هكذا سيبدو صوت التعليق في الفيديو.",
    "hi": "नमस्ते! आपके वीडियो की आवाज़ ऐसी सुनाई देगी।",
}


def tts_xtts(text, lang, settings, out: Path, voice=None):
    """Локальный XTTS v2 (F:/AI/xtts/xtts_server.py). voice: путь к ref.wav или «speaker:Имя»."""
    base = (settings.get("xtts_base") or "http://127.0.0.1:8020").rstrip("/")
    wav = out.with_suffix(".xtts.wav")
    r = requests.post(f"{base}/tts", json={"text": text, "lang": lang, "voice": voice or settings.get("xtts_voice") or "",
                                            "out": str(wav)}, timeout=600)
    if not r.ok:
        raise RuntimeError(f"XTTS: {r.text[:200]}")
    ff.run(["-i", wav, "-c:a", "libmp3lame", "-q:a", 2, out])
    wav.unlink(missing_ok=True)


def tts(text, style, settings, out: Path, log=lambda m: None):
    lang = (style.get("lang") or "ru")[:2]
    vid = (style.get("voice_id") or "").strip() or None
    if style["voice"] == "elevenlabs":
        tts_elevenlabs(text, settings, out, vid)
    elif style["voice"] == "lmnt":
        tts_lmnt(text, lang, settings, out, vid)
    elif style["voice"] == "fish":
        tts_fish(text, settings, out, vid, log)
    elif style["voice"] == "xtts":
        tts_xtts(text, lang, settings, out, vid)
    elif style["voice"] == "local":
        tts_local(text, lang, out)
    else:
        try:
            tts_edge(text, vid or style.get("edge_voice") or "ru-RU-DmitryNeural", out)
        except Exception as e:
            log(f"  Edge TTS не ответил ({e}) — беру локальный голос Windows")
            tts_local(text, lang, out)
    return ff.duration(out)


EDGE_VOICES = [
    "ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural", "uk-UA-OstapNeural", "uk-UA-PolinaNeural",
    "en-US-GuyNeural", "en-US-AndrewNeural", "en-US-AriaNeural", "en-US-JennyNeural", "en-GB-RyanNeural",
    "de-DE-ConradNeural", "de-DE-KatjaNeural", "es-ES-AlvaroNeural", "fr-FR-HenriNeural",
    "pt-BR-AntonioNeural", "it-IT-DiegoNeural", "pl-PL-MarekNeural", "tr-TR-AhmetNeural",
    "ja-JP-KeitaNeural", "ko-KR-InJoonNeural", "zh-CN-YunxiNeural",
]


# ---------- Suno ----------

def suno_check(settings):
    """Проверка ключа без траты кредитов: запрос остатка кредитов."""
    key = settings.get("suno_key")
    if not key:
        return {"ok": False, "msg": "Ключ Suno не введён"}
    base = (settings.get("suno_base") or "https://api.sunoapi.org").rstrip("/")
    try:
        r = requests.get(f"{base}/api/v1/generate/credit", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        j = r.json()
    except Exception as e:
        return {"ok": False, "msg": f"Нет ответа от {base}: {e}"}
    if r.ok and j.get("code") == 200:
        return {"ok": True, "msg": f"Ключ работает, кредитов: {j.get('data')}", "credits": j.get("data")}
    return {"ok": False, "msg": f"Ключ не принят: {j.get('msg') or j}"}


def suno_generate(prompt, instrumental, settings, folder: Path, log=lambda m: None):
    """Генерация трека через Suno-совместимый API (по умолчанию sunoapi.org).
    Адрес и модель меняются в настройках — можно подключить другой сервис с тем же форматом."""
    key = settings.get("suno_key")
    if not key:
        raise RuntimeError("Нет ключа Suno в настройках")
    base = (settings.get("suno_base") or "https://api.sunoapi.org").rstrip("/")
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"prompt": prompt[:400], "customMode": False, "instrumental": bool(instrumental),
            "model": settings.get("suno_model") or "V4_5", "callBackUrl": "https://example.com/none"}
    r = requests.post(f"{base}/api/v1/generate", headers=h, json=body, timeout=60)
    j = r.json()
    task = (j.get("data") or {}).get("taskId")
    if not task:
        raise RuntimeError(f"Suno: {j}")
    log(f"Suno задача {task}, жду трек…")
    for _ in range(90):  # до ~7.5 минут
        time.sleep(5)
        s = requests.get(f"{base}/api/v1/generate/record-info", headers=h, params={"taskId": task}, timeout=30).json()
        data = s.get("data") or {}
        status = data.get("status")
        tracks = ((data.get("response") or {}).get("sunoData")) or []
        url = next((t.get("audioUrl") for t in tracks if t.get("audioUrl")), None)
        if status == "SUCCESS" and url:
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / f"suno_{task[:8]}.mp3"
            out.write_bytes(requests.get(url, timeout=120).content)
            return out
        if status and ("FAIL" in status.upper() or "ERROR" in status.upper()):
            raise RuntimeError(f"Suno ошибка: {status} {data.get('errorMessage') or ''}")
    raise RuntimeError("Suno: не дождались трека")
