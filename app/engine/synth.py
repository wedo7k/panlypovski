"""Фоновая музыка кодом (синтез, без чужих сэмплов и без Suno — авторских прав нет).
cue(mood, dur, seed) -> float32 [n,2] 48 kHz; make(prompt, dur, out) — wav по промпту/настроению.
Suno нужен только для чисто музыкальных роликов (режим «песня», раздел миксов)."""
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt, oaconvolve

SR = 48000


def mtof(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def lp(x, f, o=2):
    return sosfilt(butter(o, f / (SR / 2), "low", output="sos"), x, axis=0)


def hp(x, f, o=2):
    return sosfilt(butter(o, f / (SR / 2), "high", output="sos"), x, axis=0)


def bp(x, f1, f2, o=2):
    return sosfilt(butter(o, [f1 / (SR / 2), f2 / (SR / 2)], "band", output="sos"), x, axis=0)


def tt(d):
    return np.arange(int(d * SR)) / SR


def adsr(n, a, d, s, r, sus_len=None):
    a, d, r = int(a * SR), int(d * SR), int(r * SR)
    env = np.ones(n) * s
    a = min(a, n)
    env[:a] = np.linspace(0, 1, a, endpoint=False) if a else env[:a]
    d2 = min(d, n - a)
    if d2 > 0:
        env[a:a + d2] = np.linspace(1, s, d2, endpoint=False)
    if r > 0 and n > r:
        env[n - r:] *= np.linspace(1, 0, r)
    return env


def pan(x, p):
    """p -1..1 -> stereo"""
    l = np.cos((p + 1) * np.pi / 4)
    r = np.sin((p + 1) * np.pi / 4)
    return np.stack([x * l, x * r], 1)


def saw(f, t, phase=0.0):
    return 2 * ((f * t + phase) % 1.0) - 1


# ------------------------------------------------------------------ instruments
def piano(m, dur, vel, rng):
    f = mtof(m)
    L = dur + 1.8
    t = tt(L)
    x = np.zeros_like(t)
    B = 0.0004
    for k in range(1, 9):
        fk = f * k * np.sqrt(1 + B * k * k)
        if fk > 12000:
            break
        amp = 1 / k ** 1.25 * (0.6 + 0.4 * vel)
        dec = (0.9 + 0.35 * k) * (1 + f / 800)
        x += amp * np.sin(2 * np.pi * fk * t + rng.uniform(0, 6.28)) * np.exp(-dec * t * 0.9)
    # отпускание клавиши
    rel = np.ones_like(t)
    i = int(dur * SR)
    if i < len(t):
        rel[i:] = np.exp(-(t[i:] - dur) * 9)
    x *= rel
    att = np.minimum(1, t / 0.004)
    x *= att
    ham = lp(rng.standard_normal(int(0.02 * SR)), 2500) * np.exp(-np.arange(int(0.02 * SR)) / (0.004 * SR)) * 0.25
    x[:len(ham)] += ham
    return x * vel * 0.28


def rhodes(m, dur, vel, rng):
    f = mtof(m)
    L = dur + 1.2
    t = tt(L)
    idx = 1.6 * np.exp(-t * 5) + 0.25
    x = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    x += 0.25 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 3)
    env = np.exp(-t * 1.1)
    i = int(dur * SR)
    if i < len(t):
        env[i:] *= np.exp(-(t[i:] - dur) * 7)
    env *= np.minimum(1, t / 0.003)
    return x * env * vel * 0.2


def pluck(m, dur, vel, rng):
    f = mtof(m)
    t = tt(min(dur, 0.6) + 0.4)
    x = saw(f, t) * np.exp(-t * 9)
    x = lp(x, 1800)
    return x * vel * 0.18


def bass(m, dur, vel, rng):
    f = mtof(m)
    t = tt(dur + 0.08)
    x = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * saw(f, t)
    x = np.tanh(1.6 * x) / np.tanh(1.6)
    env = np.minimum(1, t / 0.006) * np.exp(-t * 1.4)
    env[-int(0.08 * SR):] *= np.linspace(1, 0, int(0.08 * SR))
    return lp(x * env, 900) * vel * 0.34


def pad_chord(ms, dur, vel, rng, bright=1400, att=1.2, rel=1.6, detune=0.12, vib=0.0):
    L = dur + rel
    t = tt(L)
    out = np.zeros((len(t), 2))
    for j, m in enumerate(ms):
        for v in range(3):
            c = (v - 1) * detune
            f = mtof(m + c)
            fm = f * (1 + vib * np.sin(2 * np.pi * 5.2 * t + rng.uniform(0, 6)))
            ph = np.cumsum(fm) / SR
            x = 2 * ((ph + rng.uniform()) % 1) - 1
            out += pan(x, (v - 1) * 0.6 + (j - len(ms) / 2) * 0.08)
    out = lp(out, bright, 2)
    env = adsr(len(t), att, 0.5, 0.85, rel)
    return out * env[:, None] * vel * 0.05 / max(1, len(ms) ** 0.5)


def kick(vel=1.0):
    t = tt(0.45)
    f = 42 + 80 * np.exp(-t * 28)
    ph = np.cumsum(f) / SR
    x = np.sin(2 * np.pi * ph) * np.exp(-t * 7.5)
    x[:int(0.004 * SR)] += np.random.default_rng(1).standard_normal(int(0.004 * SR)) * 0.3
    return np.tanh(x * 1.4) * vel * 0.8


def snare(vel=1.0, rng=None):
    rng = rng or np.random.default_rng(2)
    t = tt(0.3)
    n = bp(rng.standard_normal(len(t)), 1200, 7000) * np.exp(-t * 18)
    tone = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 30)
    return (n * 0.8 + tone * 0.6) * vel * 0.55


def hat(vel=1.0, rng=None, open_=False):
    rng = rng or np.random.default_rng(3)
    t = tt(0.35 if open_ else 0.08)
    n = hp(rng.standard_normal(len(t)), 7500, 4) * np.exp(-t * (9 if open_ else 55))
    return n * vel * 0.22


def tom(vel=1.0, rng=None):
    rng = rng or np.random.default_rng(4)
    t = tt(0.9)
    f = 70 + 70 * np.exp(-t * 10)
    ph = np.cumsum(f) / SR
    x = np.sin(2 * np.pi * ph) * np.exp(-t * 4)
    x += lp(rng.standard_normal(len(t)), 900) * np.exp(-t * 22) * 0.5
    return np.tanh(x * 1.8) * vel * 0.7


def swell(d, rng):
    t = tt(d)
    n = bp(rng.standard_normal(len(t)), 3000, 9000)
    env = (t / d) ** 4
    env[-int(0.05 * SR):] *= np.linspace(1, 0, int(0.05 * SR))
    return n * env * 0.035


def riser(d, rng):
    t = tt(d)
    n = rng.standard_normal(len(t))
    y = np.zeros_like(n)
    # ступенчатый полосовой фильтр, поднимающийся вверх
    steps = 24
    seg = len(t) // steps
    for i in range(steps):
        f = 300 * (20 ** (i / steps))
        a, b = i * seg, (i + 1) * seg if i < steps - 1 else len(t)
        y[a:b] = bp(n[max(0, a - 2000):b], f, min(f * 2.5, 20000))[-(b - a):]
    f = 110 * (4 ** (t / d))
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.3
    env = (t / d) ** 2
    return (y * 0.6 + tone) * env * 0.35


def boom(rng):
    t = tt(2.5)
    f = 38 + 30 * np.exp(-t * 6)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 1.8)
    x += lp(rng.standard_normal(len(t)), 400) * np.exp(-t * 6) * 0.6
    return np.tanh(1.5 * x) * 0.8


# ------------------------------------------------------------------ reverb
_IR = {}


def reverb(x, decay=2.2, mix=0.28, seed=7):
    key = (decay, seed)
    if key not in _IR:
        rng = np.random.default_rng(seed)
        t = tt(decay * 1.3)
        ir = rng.standard_normal((len(t), 2)) * np.exp(-t * 6.9 / decay)[:, None]
        ir = lp(ir, 5000)
        ir[:int(0.012 * SR)] = 0
        ir /= np.sqrt((ir ** 2).sum(0))
        _IR[key] = ir
    ir = _IR[key]
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    wet = np.stack([oaconvolve(x[:, c], ir[:, c])[:len(x)] for c in range(2)], 1)
    return x * (1 - mix) + wet * mix * 1.6


# ------------------------------------------------------------------ chords
CH = {"m": [0, 3, 7], "M": [0, 4, 7], "m7": [0, 3, 7, 10], "M7": [0, 4, 7, 11], "7": [0, 4, 7, 10],
      "m9": [0, 3, 7, 10, 14], "sus": [0, 5, 7], "add9": [0, 4, 7, 14], "13": [0, 4, 10, 14, 21], "m11": [0, 3, 7, 10, 17]}


def chord(name):
    # "A:m" -> (root midi in octave 3, intervals)
    root, q = name.split(":")
    names = {"C": 0, "Db": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "Gb": 6, "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}
    r = 48 + names[root]
    if r > 54:
        r -= 12
    return r, CH[q]


MOODS = {
    #            bpm  progression (1 бар на аккорд)
    "tension": (84, ["A:m", "A:m", "F:M7", "E:sus", "A:m", "A:m", "D:m", "E:M"]),
    "curious": (94, ["D:m9", "G:13", "C:M7", "A:m7"]),
    "groove": (97, ["D:m9", "D:m9", "G:13", "G:13", "D:m9", "D:m9", "Bb:M7", "A:7"]),
    "epic": (88, ["C:m", "Ab:M", "Eb:M", "Bb:M"]),
    "sad": (70, ["A:m", "F:M7", "C:M", "G:M"]),
    "dark": (78, ["E:m", "C:M7", "A:m", "B:sus"]),
    "warm": (80, ["F:add9", "C:M", "D:m7", "Bb:M7"]),
}


def put(buf, x, t, gain=1.0, p=0.0):
    i = int(round(t * SR))
    if i >= len(buf):
        return
    if x.ndim == 1:
        x = pan(x, p)
    n = min(len(x), len(buf) - i)
    buf[i:i + n] += x[:n] * gain


def intensity_plan(nbars, seed):
    """0 тихо, 1 средне, 2 полно. Секции по 8 тактов, с брейками."""
    rng = np.random.default_rng(seed)
    pat = [0, 1, 2, 2, 1, 2, 0, 2, 1, 2]
    lv = []
    for b in range(nbars):
        s = b // 8
        v = pat[s % len(pat)] if s > 0 else 0 if nbars > 12 else 1
        if b % 8 == 7 and v == 2 and rng.random() < 0.5:
            v = 1  # филл-брейк в конце секции
        lv.append(v)
    return lv


def cue(mood, dur, seed=0):
    rng = np.random.default_rng(seed)
    bpm, prog = MOODS[mood]
    beat = 60 / bpm
    bar = beat * 4
    nbars = int(np.ceil(dur / bar)) + 1
    n = int((dur + 3) * SR)
    dry = np.zeros((n, 2))
    pads = np.zeros((n, 2))
    drums = np.zeros((n, 2))
    lv = intensity_plan(nbars, seed)
    K = kick()
    for b in range(nbars):
        t0 = b * bar
        if t0 > dur + 1:
            break
        root, iv = chord(prog[b % len(prog)])
        notes = [root + i for i in iv]
        L = lv[b]
        # ---------------- pad / strings
        if mood in ("tension", "dark"):
            put(pads, pad_chord([root - 12, root - 5], bar, 0.9, rng, bright=500, att=0.8, rel=1.2), t0)
            put(pads, pad_chord([n_ + 12 for n_ in notes[:3]], bar, 0.5 + 0.2 * L, rng, bright=2200, att=1.0, vib=0.004), t0)
        elif mood == "epic":
            put(pads, pad_chord([n_ + 12 for n_ in notes] + [root], bar, 0.8 + 0.3 * L, rng, bright=2600, att=0.6, vib=0.005), t0)
            put(pads, pad_chord([root - 12], bar, 0.9, rng, bright=400, att=0.3), t0)
        elif mood in ("sad", "warm"):
            put(pads, pad_chord([n_ + 12 for n_ in notes[:4]], bar, 0.6 + 0.2 * L, rng, bright=1800, att=1.5, rel=2.0, vib=0.004), t0)
        else:
            put(pads, pad_chord([n_ + 12 for n_ in notes[:4]], bar, 0.35, rng, bright=1200, att=0.8), t0)
        # ---------------- keys
        if mood in ("tension", "dark"):
            arp = [notes[0] + 24, notes[1] + 24, notes[2] + 24, notes[1] + 24]
            for k in range(8):
                if L == 0 and k % 2:
                    continue
                v = 0.35 + (0.15 if k % 4 == 0 else 0) + 0.05 * L
                put(dry, piano(arp[k % 4], beat * 0.45, v, rng), t0 + k * beat / 2, 1, 0.25 * np.sin(k))
        elif mood == "curious":
            seq = [0, 2, 1, 2, 0, 3 if len(notes) > 3 else 2, 1, 2]
            for k in range(8):
                put(dry, pluck(notes[seq[k] % len(notes)] + 24, beat * 0.4, 0.8, rng), t0 + k * beat / 2, 1, -0.3 + 0.6 * (k % 2))
            if L >= 1:
                put(dry, piano(notes[0] + 36, beat * 1.5, 0.3, rng), t0 + beat * 2.5, 1, 0.4)
        elif mood == "groove":
            for off in (0.5, 1.75, 2.5, 3.5):
                if L == 0 and off in (1.75, 3.5):
                    continue
                ln = beat * (0.35 if off % 1 else 0.6)
                for j, m_ in enumerate(notes[1:5]):
                    put(dry, rhodes(m_ + 12, ln, 0.55, rng), t0 + off * beat + j * 0.006, 1, -0.2 + 0.1 * j)
        elif mood == "epic":
            for k in (0, 2, 3.5):
                for m_ in (notes[0] + 12, notes[0] + 24):
                    put(dry, piano(m_, beat, 0.55 + 0.1 * L, rng), t0 + k * beat, 1, 0)
        elif mood in ("sad", "warm"):
            arp = [notes[0] + 12, notes[2] + 12, notes[1] + 24, notes[2] + 24]
            for k in range(4):
                put(dry, piano(arp[k], beat * 1.8, 0.42 + 0.06 * (k == 0), rng), t0 + k * beat, 1, -0.3 + 0.2 * k)
            if mood == "warm" or L == 2:
                mel = notes[-1] + 24 + (2 if b % 2 else 0)
                put(dry, piano(mel, beat * 2, 0.3, rng), t0 + beat * 2, 1, 0.3)
        # ---------------- bass
        if mood in ("tension", "dark"):
            if L >= 1:
                for k in range(8):
                    put(dry, bass(root - 12, beat * 0.4, 0.55 + 0.15 * (k % 2 == 0), rng), t0 + k * beat / 2)
        elif mood == "groove":
            line = [(0, 0, 0.7), (0.75, 12, 0.35), (1.5, 0, 0.4), (2.0, 7, 0.4), (2.75, 10, 0.3), (3.25, 12, 0.35), (3.5, 7, 0.3)]
            for (o, s_, ln) in line:
                if L == 0 and o > 0:
                    continue
                put(dry, bass(root - 12 + s_, beat * ln, 0.8, rng), t0 + o * beat)
        elif mood == "curious":
            for o in (0, 1.5, 2, 3):
                put(dry, bass(root - 12 + (7 if o == 3 else 0), beat * 0.4, 0.6, rng), t0 + o * beat)
        elif mood == "epic":
            put(dry, bass(root - 12, bar * 0.95, 0.75, rng), t0)
        elif mood in ("sad", "warm") and L >= 1:
            put(dry, bass(root - 12, bar * 0.9, 0.45, rng), t0)
        # ---------------- drums
        if mood in ("tension", "dark"):
            if L >= 1:
                put(drums, K, t0, 0.55)
                put(drums, K, t0 + beat * 0.75, 0.3)
                put(drums, K, t0 + beat * 2, 0.5)
            if L == 2:
                for k in range(16):
                    put(drums, hat(0.4 + 0.3 * (k % 4 == 2), rng), t0 + k * beat / 4, 1, 0.3)
                put(drums, snare(0.35, rng), t0 + beat * 3, 1)
        elif mood == "groove":
            sw = beat * 0.06
            if L >= 1:
                for o in (0, 1.5, 2.25) if L == 2 else (0, 2):
                    put(drums, K, t0 + o * beat, 0.8)
                for o in (1, 3):
                    put(drums, snare(0.8, rng), t0 + o * beat, 1, 0.05)
            for k in range(8):
                if L == 0 and k % 2:
                    continue
                put(drums, hat(0.55 if k % 2 == 0 else 0.35, rng, open_=(k == 7 and L == 2)), t0 + k * beat / 2 + (sw if k % 2 else 0), 1, 0.35)
        elif mood == "curious":
            if L >= 1:
                put(drums, K, t0, 0.5)
                put(drums, K, t0 + 2.5 * beat, 0.35)
                for k in range(8):
                    put(drums, hat(0.35, rng), t0 + k * beat / 2, 1, 0.3)
            if L == 2:
                put(drums, snare(0.4, rng), t0 + beat, 1)
                put(drums, snare(0.4, rng), t0 + 3 * beat, 1)
        elif mood == "epic":
            if L >= 1:
                for o in (0, 2, 2.5):
                    put(drums, tom(0.8, rng), t0 + o * beat, 1, -0.2)
                put(drums, K, t0, 0.8)
            if L == 2:
                put(drums, snare(0.7, rng), t0 + beat * 1, 1)
                put(drums, snare(0.7, rng), t0 + beat * 3, 1)
                for k in range(8):
                    put(drums, hat(0.35, rng), t0 + k * beat / 2, 1, 0.3)
            if b % 4 == 3:
                put(drums, swell(bar, rng), t0, 1)
        elif mood == "warm" and L == 2:
            put(drums, K, t0, 0.35)
            put(drums, K, t0 + 2 * beat, 0.3)
    mix = reverb(dry, 2.4, 0.3) + reverb(pads, 3.2, 0.45, seed=9) + reverb(drums, 1.2, 0.12, seed=11)
    mix = hp(mix, 30)
    return mix[:int(dur * SR)].astype(np.float32)


# слова промпта (англ./рус.) -> настроение синтеза
MOOD_WORDS = {
    "tension": ["tension", "suspense", "thriller", "mystery", "mysterious", "war", "battle", "напряж", "тревож", "тайн", "войн"],
    "dark": ["dark", "ominous", "horror", "sinister", "grim", "cold war", "мрач", "тёмн", "темн", "жутк"],
    "epic": ["epic", "cinematic", "heroic", "orchestral", "triumph", "trailer", "эпич", "героич", "торжеств"],
    "sad": ["sad", "melanchol", "tragic", "emotional", "nostalg", "piano ballad", "груст", "печал", "трагич", "ностальг"],
    "warm": ["warm", "cozy", "calm", "gentle", "soft", "ambient", "peaceful", "lofi", "lo-fi", "тёпл", "тепл", "уютн", "спокойн"],
    "groove": ["groove", "funk", "hip hop", "hip-hop", "upbeat", "energetic", "drive", "bounce", "бодр", "энергич", "ритмич"],
    "curious": ["curious", "documentary", "explainer", "light", "playful", "corporate", "inspiring", "документ", "лёгк", "легк", "любопыт"],
}


def mood_for(prompt):
    """Настроение по промпту: явное «mood=dark» или по словам; по умолчанию — документальное curious."""
    low = str(prompt or "").lower()
    for m in MOODS:
        if f"mood={m}" in low or low.strip() == m:
            return m
    best = max(MOODS, key=lambda m: sum(w in low for w in MOOD_WORDS.get(m, ())))
    return best if any(w in low for w in MOOD_WORDS.get(best, ())) else "curious"


def make(prompt, dur, out, seed=0, max_len=150):
    """Пишет wav (16 бит, стерео) длиной до max_len с; в ролике он закольцуется. Возвращает (путь, настроение)."""
    from scipy.io import wavfile
    mood = mood_for(prompt)
    x = cue(mood, float(min(max(dur, 20), max_len)), seed)
    x = x / (np.abs(x).max() + 1e-9) * 0.9
    n = min(len(x), int(SR * 1.5))  # мягкое затухание в конце — стык петли не щёлкает
    x[-n:] *= np.linspace(1, 0, n)[:, None]
    x[:int(SR * 0.05)] *= np.linspace(0, 1, int(SR * 0.05))[:, None]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(out), SR, (x * 32767).astype(np.int16))
    return out, mood
