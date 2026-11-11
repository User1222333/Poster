"""Budowa kanonicznego modelu produktu z surowego wiersza + kontrola jakości źródła."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .aliases import CANONICAL_FIELDS, MULTI_FIELDS
from .gtin import GTIN_MESSAGES, validate_gtin
from .normalize import clean_text, parse_decimal, single_line

NUMERIC_FIELDS = ("width", "height", "depth", "weight", "price", "quantity")
_NUM_WITH_UNIT = re.compile(r"^[+-]?[\d\s.,]+\s*[a-zA-Z]*\.?$")


def is_http_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc) and " " not in value


def build_product(raw: dict[str, str], column_map: dict[str, str], row_number: int) -> dict:
    """Niczego nie uzupełnia: brak w źródle = puste pole. Niezmapowane kolumny -> custom_attributes."""
    product: dict = {f: ([] if f in MULTI_FIELDS else "") for f in CANONICAL_FIELDS}
    product["id"] = f"row-{row_number}"
    product["custom_attributes"] = {}
    for col, value in raw.items():
        field = column_map.get(col)
        if field is None:
            product["custom_attributes"][col] = clean_text(value)
        elif field in MULTI_FIELDS:
            product[field].extend(v for v in (single_line(p) for p in re.split(r"[\n|]", value)) if v)
        elif field == "description":
            product[field] = clean_text(value)
        else:
            product[field] = single_line(value)
    return product


def source_issues(product: dict, mapped_fields: set[str]) -> list[dict]:
    """Problemy widoczne już w danych źródłowych (niezależne od rynku)."""
    out: list[dict] = []

    def add(code: str, field: str, message: str, value: str = "") -> None:
        out.append({"code": code, "field": field, "message": message, "value": value})

    if "sku" in mapped_fields and not product["sku"]:
        add("SKU_MISSING", "sku", "Brak SKU.")
    if "title" in mapped_fields and not product["title"]:
        add("TITLE_MISSING", "title", "Pusta nazwa produktu.")
    if "gtin" in mapped_fields:
        gtin = product["gtin"]
        if not gtin:
            add("GTIN_MISSING", "gtin", "Brak kodu EAN/GTIN.")
        else:
            ok, code = validate_gtin(gtin)
            if not ok:
                hint = ""
                if code == "BAD_LENGTH" and gtin.isdigit() and len(gtin) in (7, 11):
                    hint = " Możliwa utrata zera wiodącego w pliku źródłowym — sprawdź u źródła."
                add("GTIN_INVALID", "gtin", f"Błędny EAN/GTIN: {GTIN_MESSAGES[code]}.{hint}", gtin)
    if "images" in mapped_fields:
        if not product["images"]:
            add("IMAGES_MISSING", "images", "Brak zdjęć.")
        for url in product["images"]:
            if not is_http_url(url):
                add("URL_INVALID", "images", "Niepoprawny adres URL zdjęcia.", url)
    for f in NUMERIC_FIELDS:
        v = product.get(f, "")
        if v and f in mapped_fields:
            num_part = re.sub(r"\s*[a-zA-Z]+\.?$", "", v)
            if not _NUM_WITH_UNIT.match(v) or parse_decimal(num_part) is None:
                add("NUMBER_INVALID", f, "Wartość nie jest poprawną liczbą.", v)
    return out


def duplicate_issues(products: list[dict]) -> dict[int, list[dict]]:
    """Duplikaty SKU oraz ten sam EAN pod różnymi SKU. Klucz: indeks produktu na liście."""
    out: dict[int, list[dict]] = {}
    by_sku: dict[str, list[int]] = {}
    by_gtin: dict[str, list[int]] = {}
    for i, p in enumerate(products):
        if p["sku"]:
            by_sku.setdefault(p["sku"], []).append(i)
        if p["gtin"]:
            by_gtin.setdefault(p["gtin"], []).append(i)
    for sku, idxs in by_sku.items():
        if len(idxs) > 1:
            for i in idxs:
                out.setdefault(i, []).append({"code": "SKU_DUPLICATE", "field": "sku", "value": sku,
                                              "message": f"SKU „{sku}” występuje {len(idxs)} razy."})
    for gtin, idxs in by_gtin.items():
        skus = {products[i]["sku"] for i in idxs}
        if len(idxs) > 1 and len(skus) > 1:
            for i in idxs:
                out.setdefault(i, []).append({"code": "GTIN_SHARED", "field": "gtin", "value": gtin,
                                              "message": f"EAN „{gtin}” jest przypisany do {len(skus)} różnych SKU."})
    return out
