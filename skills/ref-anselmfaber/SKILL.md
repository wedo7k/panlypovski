---
name: ref-anselmfaber
description: Монтаж 1 в 1 как у канала-референса @AnselmFaber («немец», ref cf80fe3042) — немецкий исторический лонгформ «Was wurde aus …?» про Габсбургов. Читать ПЕРЕД ответом на сценарий любого канала/проекта с этим референсом (или an_23d72bf672): как писать сценарий, делить сцены, какую графику ставить и как собирать.
---

# Монтаж канала @AnselmFaber («Was wurde aus …?»)

Полный разбор: `data/analyses/23d72bf672/report.md`, паспорт: `passport.json` (влит в ref `cf80fe3042`).

## Сценарий
- Название: `Was wurde aus <людях/вещи> nach <событие>?` (или та же формула на языке канала).
- **Хук 25–35 с**: конкретная сцена из будущего героя (место, месяц, год, возраст) → связь с громким событием →
  ударная фраза-парадокс (она же QUOTE) → вопрос-название. Потом «Ich bin …» и одна фраза маршрута (MAP всего пути).
- 5–6 глав, каждая = один человек или одно место. Внутри: факт → легенда → «Die Wahrheit ist …» (развенчание).
- Финал: итоговая TABLE (имя — † год, mit N), закольцовка на героя хука, тихий атмосферный кадр.
- Тон спокойный, без оценок, даты и цифры точные. ~140 слов/мин, 8–10 мин ≈ 1200–1400 слов.

## Сцены и кадры
- План длинный: **~9 с** (сцена = 1–3 предложения, обычно 1 кадр, иногда 2). Растворения 0,8 с, без SFX, без субтитров.
- Картинки: **2/3 — `!` ИИ-живопись** (реконструкции: интерьеры, кареты, снег, суд, кабинет) в стиле
  `19th-century academic oil painting, muted sepia and umber palette, soft candle light, fine brushwork` — карточкой на тёмном фоне;
  **1/4 — архивные портреты** (`>` точное имя на английском/немецком, напр. `Crown Prince Rudolf portrait`) — через PORTRAIT;
  остальное — современные фото мест (`>` `Karmel Mayerling`, `Hernalser Friedhof`).

## Графика (строки в сцене ответа)
| Когда | Строка | Частота |
|---|---|---|
| начало главы | `CHAPTER: 01 \| Mayerling` (название = место) | 5–6 |
| перед главой / в хуке | `MAP: Mayerling 48.05 16.10; Baden 48.01 16.23; Wien 48.21 16.37` — точки накапливаются | 4–5 |
| ударная фраза озвучки | `QUOTE: «Die Wahrheit ist trauriger.»` — ровно те слова, что звучат | 10–12 |
| первое появление человека с архивным фото | `PORTRAIT: plate \| Kronprinz Rudolf` (чиновники, аристократы) / `PORTRAIT: polaroid \| Josef Bratfisch` (простые люди, «частные» фото) | 6–8 |
| документ, памятный предмет | `PORTRAIT: pinned \| Hochaltar an der Sterbestelle` | 1 |
| первое появление человека на ИИ-кадре | `LOWER: Johann Loschek` | 3 |
| новое место на ИИ-кадре | `PLACE: Hernals` или `PIN: Baden` | 3–4 |
| финал | `TABLE: DIE ZEUGEN VON MAYERLING \| Bratfisch — † 1892 — mit 45; …` (последняя строка — главный герой) | 1 |
Не ставить: COUNTER, CHART, ASK, LIST, яркие плашки. Цифры — только из сценария.

## Звук
Голос мужской, спокойный, язык канала. `MUSIC_MODE: фон`, тихо (0.1):
`dark ambient documentary score, melancholic A minor, slow cello and soft string pads, sparse piano, 60 BPM, warm dark sound, no drums, instrumental`
(сначала прочитать `data/music_journal.md`).

## Пример сцен
```
## Mayerling
CHAPTER: 01 | Mayerling
> Mayerling hunting lodge 1889
! 19th-century academic oil painting, hunting lodge in snowy Vienna Woods at dawn, muted sepia palette
Ein Jagdschloss im Wienerwald am 30. Januar 1889.

## Loschek
LOWER: Johann Loschek
! 19th-century oil painting, valet in black coat knocking on a locked wooden door, candle, dim corridor with antlers
Eine Stunde später klopft der Kammerdiener Johann Loschek. Keine Antwort.

## Bratfisch
PORTRAIT: polaroid | Josef Bratfisch
> Josef Bratfisch
Der Wagen gehört Josef Bratfisch, dem Leibfiaker des Kronprinzen.
```
