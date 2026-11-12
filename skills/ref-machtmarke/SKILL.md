---
name: ref-machtmarke
description: Монтаж 1 в 1 как у канала-референса @MachtMarke (Macht & Marke, ref 56571f946a) — немецкий документальный лонгформ «Die wahre Geschichte hinter …» (тайны, холодная война, правда vs легенда), стоки в сепии, очень частая золотая графика-досье. Читать ПЕРЕД ответом на сценарий любого канала/проекта с этим референсом (или an_621b8b96dd).
---

# Монтаж как у Macht & Marke

Разбор: `data/analyses/621b8b96dd/report.md`, паспорт: `passport.json` (влит в ref `56571f946a`).

## Сценарий
- Язык de, 20–26 мин, ~130 слов/мин. Короткие фразы, много риторических вопросов («Die Idee?», «Was ist wahr?»).
- Хук ≤ 45 с: противоречие (все гадают → официально признали только в N) + «что на самом деле» + 2 вопроса
  → заставка `TITLE: Die wahre Geschichte hinter | NAME · ГОД BIS HEUTE`. Потом «Abonniere den Kanal! Los geht's!».
- Части без номеров глав: каждая открывается kicker-титром или вопросом. Финал: миф/факт → SPAN лет → цитата-мораль
  → `CHECK: Die wahre Geschichte | Was [bleibt] | …` → `TITLE: Danke fürs Zuschauen | Jeden Tag eine wahre Geschichte`.

## Сцены и кадры
- Сцена = 1–2 предложения (длина кадра 2,5 с, план меняется растворением ~0,5 с).
- Только стоки: в каждой сцене `>` 2–3 конкретных визуальных запроса (desert airbase, cold war spy plane, CIA documents
  on desk, hands reading files, Nevada salt flat aerial). Для людей/документов — точные имена (Wikimedia, выйдут карточкой).
  `!` — только запасной. Без ИИ-картинок.

## Графика — часто (каждая 3–5-я сцена), всё золото на тёмном, [слово] = золотом с подчёркиванием
- `KICKER: Groom Lake | Offiziell [geheim] | Nevada, 1955` — главный приём, на смену темы/места.
- `ASK: …` — отдельной сценой, озвучка = ровно этот вопрос.
- `MYTH: Die Lichter über der Wüste | Raumschiffe am Himmel | U-2 und A-12` — было/стало, 4–6 на ролик.
- `COUNTER: 21 km | über dem Boden`, `RING: 8% → 50% | laut CIA-Historikern`, `CHART:`/`LINE:` для цифр.
- `CHECK: Gesucht | Der perfekte [Ort] | пункт; пункт; пункт`, `FACTS: Aus dem All gesehen | Groom Lake heute | Landebahnen — mehrere; …`.
- `CALENDAR: 4 | August 1955 | Die U-2 hebt zum ersten Mal ab | Groom Lake` — ключевые даты.
- `STAMP: Genehmigt | подпись`, `NEWS: Die Abendzeitung | Заголовок | подзаголовок`, `TELEGRAM: Roswell, 8. Juli 1947 | … STOP …`,
  `FACT: факт с [числом] | подпись` (WUSSTEST DU?), `RIBBON: DRAGON LADY | seit 1955 in der [Luft]`, `SPAN: 1955 → 2013 | Vom Geheimnis zur Akte | Area 51`.
- `MAP:` при каждом новом месте/маршруте, `TIMELINE:` для проектов по годам, `COMPARE: Mythos | … — …; …` для «Mythos/Fakt»,
  `QUOTE: «…» | — источник` 3–6 раз.

## Звук
`MUSIC_MODE: фон`, MUSIC: `mysterious documentary underscore, A minor, dark warm ambient pads, low strings, subtle piano, slow 70 BPM, no drums, cold war tension, instrumental` (сначала прочитай `data/music_journal.md`). SFX нет, субтитров нет.

## Пример сцен
```
## Groom Lake
KICKER: Groom Lake | Offiziell [geheim] | Nevada
> Nevada dry lake bed aerial; desert salt flat drone
Groom Lake, Nevada.

## Die Idee
ASK: Die [Idee]?
> U-2 spy plane high altitude
Die Idee?
```
