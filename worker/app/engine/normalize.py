"""Deterministyczna normalizacja tekstu, liczb, jednostek i wymiarów. Bez zgadywania."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_SPACES = re.compile(r"[ \t   -   　]+")
_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿]")
_PL_FOLD = str.maketrans({"ł": "l", "Ł": "L"})


def clean_text(value: object) -> str:
    """Ujednolica końce linii i spacje (także niełamliwe). Zachowuje polskie znaki."""
    if value is None:
        return ""
    s = unicodedata.normalize("NFC", str(value))
    s = _ZERO_WIDTH.sub("", s).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACES.sub(" ", line).strip() for line in s.split("\n")]
    return "\n".join(lines).strip()


def single_line(value: object) -> str:
    return _SPACES.sub(" ", clean_text(value).replace("\n", " ")).strip()


def fold(value: object) -> str:
    """Klucz porównawczy: małe litery, bez diakrytyków i interpunkcji. Tylko do dopasowań."""
    s = single_line(value).translate(_PL_FOLD)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_NUM = r"[+-]?(?:\d{1,3}(?:[ .,]\d{3})+|\d+)(?:[.,]\d+)?"


def parse_decimal(value: object) -> Decimal | None:
    """Parsuje liczbę z przecinkiem lub kropką dziesiętną. Zwraca None, gdy zapis nie jest liczbą.

    Konwencja (polska): pojedynczy przecinek to separator dziesiętny (`1,234` = 1.234).
    Gdy występują oba znaki, ostatni z nich jest separatorem dziesiętnym.
    """
    s = single_line(value).replace(" ", "")
    if not s or not re.fullmatch(r"[+-]?[\d.,]+", s) or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif s.count(",") == 1:
        s = s.replace(",", ".")
    elif s.count(",") > 1 or s.count(".") > 1:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def format_decimal(d: Decimal) -> str:
    """Zapis bez notacji naukowej i zbędnych zer, z kropką dziesiętną."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# Współczynniki do jednostki bazowej (mm / g). Nieznana jednostka => brak konwersji.
UNITS: dict[str, tuple[str, Decimal]] = {
    "mm": ("length", Decimal(1)),
    "cm": ("length", Decimal(10)),
    "dm": ("length", Decimal(100)),
    "m": ("length", Decimal(1000)),
    "mg": ("weight", Decimal("0.001")),
    "g": ("weight", Decimal(1)),
    "dag": ("weight", Decimal(10)),
    "kg": ("weight", Decimal(1000)),
}
_UNIT_ALIASES = {
    "milimetr": "mm", "milimetry": "mm", "centymetr": "cm", "centymetry": "cm", "metr": "m",
    "metry": "m", "gram": "g", "gramy": "g", "kilogram": "kg", "kilogramy": "kg", "deko": "dag",
}


def normalize_unit(unit: object) -> str | None:
    u = single_line(unit).lower().rstrip(".")
    u = _UNIT_ALIASES.get(u, u)
    return u if u in UNITS else None


def convert_unit(value: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    a, b = UNITS.get(from_unit), UNITS.get(to_unit)
    if not a or not b or a[0] != b[0]:
        return None
    return value * a[1] / b[1]


_QTY = re.compile(rf"^({_NUM})\s*([a-zA-Ząćęłńóśźż.]*)$")


def parse_quantity(value: object) -> tuple[Decimal, str | None, str] | None:
    """`140 cm` -> (140, 'cm', 'cm'). Trzeci element to surowy zapis jednostki (może być nieznana)."""
    m = _QTY.match(single_line(value))
    if not m:
        return None
    num = parse_decimal(m.group(1))
    if num is None:
        return None
    raw_unit = m.group(2)
    return num, (normalize_unit(raw_unit) if raw_unit else None), raw_unit


_DIM_SEP = re.compile(r"\s*[x×*]\s*", re.IGNORECASE)


def split_dimensions(value: object) -> tuple[list[Decimal], str | None] | None:
    """`140 x 260 cm` -> ([140, 260], 'cm'). None przy niejednoznacznym lub mieszanym zapisie."""
    s = single_line(value)
    parts = _DIM_SEP.split(s) if s else []
    if len(parts) < 2 or len(parts) > 3:
        return None
    nums: list[Decimal] = []
    units: set[str] = set()
    for part in parts:
        q = parse_quantity(part)
        if q is None:
            return None
        num, unit, raw_unit = q
        if raw_unit and unit is None:
            return None
        nums.append(num)
        if unit:
            units.add(unit)
    if len(units) > 1:
        return None
    return nums, (units.pop() if units else None)
