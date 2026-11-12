"""Умный разбор: любой сценарий → план ролика (сцены, запросы, музыка, YouTube-данные).

Работает через Gemini (google_key) или Claude (anthropic_key). Без ключей — простой разбор по абзацам.
"""
import json
import re

import requests

PLAN_PROMPT = """Ты — режиссёр монтажа YouTube-роликов. Тебе дали сценарий/задачу в свободной форме.
Разбери его и верни ТОЛЬКО JSON (без пояснений) такого вида:
{
 "project": "короткое имя проекта",
 "format": "16:9" или "9:16" или "" (если не сказано),
 "lang": "код языка озвучки, например ru",
 "music": "промпт для Suno НА АНГЛИЙСКОМ: жанр, настроение, инструменты, темп (учти пожелания автора и стиль канала)",
 "music_mode": "background" или "song",
 "title": "название ролика для YouTube",
 "description": "описание для YouTube, 2-4 предложения",
 "tags": "теги через запятую",
 "thumb_prompts": ["2-3 промпта для превью (Nano Banana), подробные"],
 "scenes": [
   {"title": "короткая плашка 1-4 слова или пусто",
    "text": "ТОЧНЫЙ текст для озвучки этой сцены (дословно из сценария, без ремарок)",
    "queries": ["2-3 поисковых запроса НА АНГЛИЙСКОМ для стоковых видео/фото"],
    "ai_prompt": "промпт для генерации картинки, если стоков не найти (англ.)"}
 ]
}
Правила:
- Текст озвучки бери из сценария дословно, весь, ничего не выкидывай и не сокращай. Ремарки, пометки
  автора, указания (музыка, «тут показать…», тайминги) в озвучку НЕ попадают — используй их для запросов/музыки.
- Одна сцена = 1-3 предложения (примерно {scene_sec} сек речи).
- Если в сценарии уже есть название/теги/описание — используй их.
{extra}
СЦЕНАРИЙ:
"""


def _gemini(prompt, key, model="gemini-2.5-flash"):
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      params={"key": key}, timeout=300,
                      json={"contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4}})
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _claude(prompt, key, model):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=300,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": model, "max_tokens": 16000, "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f"Claude {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json()["content"])


def ask(prompt, settings):
    if settings.get("anthropic_key"):
        return _claude(prompt, settings["anthropic_key"], settings.get("anthropic_model") or "claude-sonnet-5-5")
    if settings.get("google_key"):
        return _gemini(prompt, settings["google_key"], settings.get("google_text_model") or "gemini-2.5-flash")
    raise RuntimeError("нет ключа")


def available(settings):
    return bool(settings.get("anthropic_key") or settings.get("google_key"))


def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def plan(script, settings, style=None, ref=None, log=print):
    """Возвращает (meta, scenes_raw) или None, если ИИ недоступен."""
    if not available(settings):
        return None
    style = style or {}
    extra = ""
    if ref:
        a = ref.get("analysis") or {}
        extra += (f"- Стиль канала-референса «{ref.get('name')}»: формат {a.get('format')}, средний кадр "
                  f"{a.get('avg_shot')} c, длительность ~{a.get('duration')} c.\n")
        if ref.get("titles"):
            extra += "- Примеры названий роликов канала (пиши в похожем стиле): " + " | ".join(ref["titles"][:8]) + "\n"
        if ref.get("notes"):
            extra += "- Заметки о стиле: " + ref["notes"] + "\n"
    prompt = PLAN_PROMPT.replace("{scene_sec}", str(max(3, round(style.get("shot_len", 4) * 2)))) \
                        .replace("{extra}", extra) + script
    log("ИИ разбирает сценарий…")
    data = _json(ask(prompt, settings))
    meta = {"title": data.get("title", ""), "description": data.get("description", ""),
            "tags": data.get("tags", ""), "thumb_prompts": data.get("thumb_prompts") or [],
            "opts": {k: data.get(k) for k in ("project", "format", "lang", "music", "music_mode") if data.get(k)}}
    if "project" in meta["opts"]:
        meta["opts"]["name"] = meta["opts"].pop("project")
    return meta, data.get("scenes") or []


def heuristic(script):
    """Без ИИ: абзацы → сцены по 1-3 предложения, запросы из ключевых слов."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*(музык|кадр|показ|music|sfx)[^)]*\)", " ", script, flags=re.I)
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+|\n{2,}", text) if len(s.strip()) > 2]
    scenes, cur = [], []
    for s in sents:
        if s.lstrip().startswith("#"):
            s = s.lstrip("# ").strip()
            if not s:
                continue
        cur.append(s)
        if sum(len(x.split()) for x in cur) >= 22:
            scenes.append(" ".join(cur))
            cur = []
    if cur:
        scenes.append(" ".join(cur))
    out = []
    for t in scenes:
        words = sorted({w for w in re.findall(r"[A-Za-zА-Яа-яЁё]{6,}", t)}, key=len, reverse=True)[:2]
        out.append({"title": "", "text": t, "queries": [" ".join(words)] if words else [], "ai_prompt": ""})
    return out
