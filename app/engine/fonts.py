"""Поиск шрифтов на Windows, macOS и Linux. Имена в коде — виндовые (georgiab.ttf), здесь они
переводятся в файлы конкретной системы. Шрифта нет — понятная ошибка, а не крошечный load_default."""
import os
from pathlib import Path

from PIL import ImageFont

DIRS = [Path("C:/Windows/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"),
        Path("/Library/Fonts"), Path.home() / "Library/Fonts", Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"), Path.home() / ".fonts"]

# имя файла Windows -> варианты на macOS/Linux
ALIASES = {
    "georgia.ttf": ["georgia.ttf", "dejavuserif.ttf"],
    "georgiab.ttf": ["georgia bold.ttf", "dejavuserif-bold.ttf"],
    "georgiai.ttf": ["georgia italic.ttf", "dejavuserif-italic.ttf"],
    "georgiaz.ttf": ["georgia bold italic.ttf", "dejavuserif-bolditalic.ttf"],
    "times.ttf": ["times new roman.ttf", "liberationserif-regular.ttf", "dejavuserif.ttf"],
    "timesbd.ttf": ["times new roman bold.ttf", "liberationserif-bold.ttf", "dejavuserif-bold.ttf"],
    "timesi.ttf": ["times new roman italic.ttf", "liberationserif-italic.ttf"],
    "timesbi.ttf": ["times new roman bold italic.ttf", "liberationserif-bolditalic.ttf"],
    "arial.ttf": ["arial.ttf", "liberationsans-regular.ttf", "dejavusans.ttf"],
    "arialbd.ttf": ["arial bold.ttf", "liberationsans-bold.ttf", "dejavusans-bold.ttf"],
    "ariali.ttf": ["arial italic.ttf", "liberationsans-italic.ttf"],
    "arialbi.ttf": ["arial bold italic.ttf", "liberationsans-bolditalic.ttf"],
    "arialn.ttf": ["arial narrow.ttf"], "arialnb.ttf": ["arial narrow bold.ttf"],
    "verdana.ttf": ["verdana.ttf", "dejavusans.ttf"], "verdanab.ttf": ["verdana bold.ttf", "dejavusans-bold.ttf"],
    "verdanai.ttf": ["verdana italic.ttf"], "verdanaz.ttf": ["verdana bold italic.ttf"],
    "tahoma.ttf": ["tahoma.ttf"], "tahomabd.ttf": ["tahoma bold.ttf"],
    "trebuc.ttf": ["trebuchet ms.ttf"], "trebucbd.ttf": ["trebuchet ms bold.ttf"],
    "trebucit.ttf": ["trebuchet ms italic.ttf"], "trebucbi.ttf": ["trebuchet ms bold italic.ttf"],
    "impact.ttf": ["impact.ttf"],
    "cour.ttf": ["courier new.ttf", "liberationmono-regular.ttf", "dejavusansmono.ttf"],
    "courbd.ttf": ["courier new bold.ttf", "liberationmono-bold.ttf", "dejavusansmono-bold.ttf"],
    "dejavusansmono.ttf": ["courier new.ttf", "menlo.ttc"],
    "inkfree.ttf": ["bradley hand bold.ttf", "noteworthy.ttc", "chalkboard.ttc"],
    "segoepr.ttf": ["bradley hand bold.ttf", "noteworthy.ttc"], "comic.ttf": ["comic sans ms.ttf"],
}
# иероглифы: любое имя семейства ниже подменяется шрифтом с нужным алфавитом
_JA = ["hiragino sans gb.ttc", "songti.ttc", "stheiti light.ttc", "notoserifcjk-regular.ttc", "notosanscjk-regular.ttc"]
_KO = ["applemyungjo.ttf", "applegothic.ttf", "applesdgothicneo.ttc", "notoserifcjk-regular.ttc"]
for _n in ("msgothic.ttc", "msmincho.ttc", "yugothr.ttc", "yugothb.ttc", "yumin.ttf", "yumindb.ttf",
           "simsun.ttc", "simhei.ttf", "msyh.ttc", "msyhbd.ttc"):
    ALIASES[_n] = _JA
for _n in ("batang.ttc", "malgun.ttf", "malgunbd.ttf"):
    ALIASES[_n] = _KO

FALLBACK = ["georgia.ttf", "times new roman.ttf", "times.ttf", "arial.ttf", "dejavuserif.ttf", "dejavusans.ttf",
            "liberationserif-regular.ttf", "liberationsans-regular.ttf", "helvetica.ttc"]

_index = None


def index(dirs=None):
    """{имя файла в нижнем регистре: полный путь}; первая папка в списке главнее."""
    idx = {}
    for d in (DIRS if dirs is None else dirs):
        d = Path(d)
        if not d.is_dir():
            continue
        for root, _, names in os.walk(d):
            for n in names:
                if n.lower().endswith((".ttf", ".otf", ".ttc")):
                    idx.setdefault(n.lower(), str(Path(root) / n))
    return idx


def _idx(idx):
    global _index
    if idx is not None:
        return idx
    if _index is None:
        _index = index()
    return _index


def find(name, idx=None):
    """Путь к шрифту по имени (с учётом замен для этой системы) или None — без запасных вариантов."""
    idx = _idx(idx)
    if os.path.isabs(str(name)) and os.path.exists(str(name)):
        return str(name)
    low = str(name).lower()
    for v in (low, *ALIASES.get(low, ())):
        if v in idx:
            return idx[v]
    return None


def resolve(*names, idx=None):
    """Первый найденный из names, затем общие запасные. Ничего нет — RuntimeError с подсказкой."""
    idx = _idx(idx)
    for n in (*names, *FALLBACK):
        p = find(n, idx)
        if p:
            return p
    raise RuntimeError(f"Не найден шрифт ({', '.join(map(str, names))}) и запасные. Проверь папки: "
                       + ", ".join(map(str, DIRS)) + ". На Linux установи fonts-dejavu.")


def load(size, *names):
    return ImageFont.truetype(resolve(*names), int(size))
