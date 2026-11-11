"""Walidacja GTIN-8/12/13/14 (cyfra kontrolna GS1). Niczego nie naprawia ani nie generuje."""

from __future__ import annotations

VALID_LENGTHS = (8, 12, 13, 14)


def check_digit(body: str) -> int:
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def validate_gtin(value: str) -> tuple[bool, str | None]:
    """Zwraca (ok, kod_problemu). Kody: EMPTY, NOT_DIGITS, BAD_LENGTH, BAD_CHECK_DIGIT."""
    if not value:
        return False, "EMPTY"
    if not (value.isascii() and value.isdigit()):
        return False, "NOT_DIGITS"
    if len(value) not in VALID_LENGTHS:
        return False, "BAD_LENGTH"
    if check_digit(value[:-1]) != int(value[-1]):
        return False, "BAD_CHECK_DIGIT"
    return True, None


GTIN_MESSAGES = {
    "EMPTY": "brak kodu",
    "NOT_DIGITS": "kod zawiera znaki inne niż cyfry",
    "BAD_LENGTH": "nieprawidłowa długość (dozwolone 8, 12, 13 lub 14 cyfr)",
    "BAD_CHECK_DIGIT": "nieprawidłowa cyfra kontrolna",
}
