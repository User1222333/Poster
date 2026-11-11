"""Słownik aliasów nagłówków -> pola kanoniczne. Tylko propozycje; konflikt zostaje dla użytkownika."""

from __future__ import annotations

from .normalize import fold

CANONICAL_FIELDS = [
    "source_id", "sku", "gtin", "title", "description", "brand", "manufacturer", "category_path",
    "color", "material", "pattern", "shape", "width", "height", "depth", "dimensions_unit",
    "weight", "weight_unit", "images", "season", "price", "currency", "quantity", "condition",
    "lead_time",
]
# Pola, do których wolno przypisać wiele kolumn źródłowych (lista).
MULTI_FIELDS = {"images"}

FIELD_LABELS = {
    "source_id": "ID źródłowe", "sku": "SKU", "gtin": "EAN / GTIN", "title": "Nazwa",
    "description": "Opis", "brand": "Marka", "manufacturer": "Producent",
    "category_path": "Kategoria (ścieżka)", "color": "Kolor", "material": "Materiał / skład",
    "pattern": "Wzór", "shape": "Kształt", "width": "Szerokość", "height": "Wysokość",
    "depth": "Głębokość", "dimensions_unit": "Jednostka wymiarów", "weight": "Waga",
    "weight_unit": "Jednostka wagi", "images": "Zdjęcia", "season": "Sezon", "price": "Cena",
    "currency": "Waluta", "quantity": "Ilość", "condition": "Stan", "lead_time": "Czas realizacji",
}

_ALIASES: dict[str, list[str]] = {
    "source_id": ["id", "id produktu", "product id", "identyfikator"],
    "sku": ["sku", "nr zapasu", "numer zapasu", "symbol", "kod produktu", "indeks", "index",
            "kod towaru", "symbol produktu", "product sku", "shop sku", "seller sku"],
    "gtin": ["ean", "kod ean", "ean13", "ean 13", "gtin", "kod kreskowy", "barcode", "ean gtin"],
    "title": ["nazwa", "nazwa produktu", "tytul", "tytul produktu", "title", "name", "product title"],
    "description": ["opis", "opis produktu", "description", "opis dlugi", "long description"],
    "brand": ["marka", "brand"],
    "manufacturer": ["producent", "manufacturer", "wytworca"],
    "category_path": ["kategoria", "kategorie", "sciezka kategorii", "category", "category path",
                      "kategoria produktu"],
    "color": ["kolor", "barwa", "color", "colour"],
    "material": ["material", "sklad", "sklad materialu", "sklad materialowy", "tworzywo"],
    "pattern": ["wzor", "pattern", "motyw"],
    "shape": ["ksztalt", "shape"],
    "width": ["szerokosc", "width", "szer"],
    "height": ["wysokosc", "height", "wys"],
    "depth": ["glebokosc", "depth", "dlugosc", "length"],
    "dimensions_unit": ["jednostka wymiarow", "jednostka wymiaru", "dimensions unit"],
    "weight": ["waga", "weight", "masa"],
    "weight_unit": ["jednostka wagi", "weight unit", "jednostka masy"],
    "images": ["zdjecie", "zdjecia", "image", "images", "obraz", "url zdjecia", "image url",
               "zdjecie glowne", "main image"],
    "season": ["sezon", "season"],
    "price": ["cena", "price", "cena brutto", "cena sprzedazy"],
    "currency": ["waluta", "currency"],
    "quantity": ["ilosc", "stan magazynowy", "quantity", "stock"],
    "condition": ["stan produktu", "condition", "kondycja"],
    "lead_time": ["czas realizacji", "lead time", "czas wysylki", "leadtime to ship"],
}
_LOOKUP: dict[str, str] = {alias: field for field, aliases in _ALIASES.items() for alias in aliases}


def field_for_header(header: str) -> str | None:
    key = fold(header)
    if key in _LOOKUP:
        return _LOOKUP[key]
    # „Zdjęcie 1”, „Zdjęcie 2” itd. — numerowane warianty tego samego pola listowego.
    stripped = key.rstrip("0123456789 ").strip()
    if stripped != key and _LOOKUP.get(stripped) in MULTI_FIELDS:
        return _LOOKUP[stripped]
    return None


def suggest_column_map(columns: list[str]) -> dict:
    """Zwraca {'map': {kolumna: pole}, 'conflicts': {pole: [kolumny]}}.

    Jeśli dwie kolumny pasują do tego samego pola pojedynczego, żadna nie jest przypisywana
    automatycznie — decyzję podejmuje użytkownik.
    """
    by_field: dict[str, list[str]] = {}
    for col in columns:
        field = field_for_header(col)
        if field:
            by_field.setdefault(field, []).append(col)
    mapping: dict[str, str] = {}
    conflicts: dict[str, list[str]] = {}
    for field, cols in by_field.items():
        if len(cols) > 1 and field not in MULTI_FIELDS:
            conflicts[field] = cols
            continue
        for col in cols:
            mapping[col] = field
    return {"map": mapping, "conflicts": conflicts}


def validate_column_map(column_map: dict[str, str], columns: list[str]) -> list[str]:
    errors: list[str] = []
    used: dict[str, str] = {}
    for col, field in column_map.items():
        if col not in columns:
            errors.append(f"Kolumna „{col}” nie istnieje w pliku.")
        if field not in CANONICAL_FIELDS:
            errors.append(f"Nieznane pole kanoniczne „{field}”.")
        elif field in used and field not in MULTI_FIELDS:
            errors.append(f"Pole „{field}” przypisano do dwóch kolumn: „{used[field]}” i „{col}”.")
        used[field] = col
    return errors


def hint_field(text: str) -> str | None:
    """Luźniejsze dopasowanie (alias jako ciąg słów wewnątrz nazwy) — WYŁĄCZNIE do propozycji.

    Przy remisie między różnymi polami nie zwraca nic.
    """
    exact = field_for_header(text)
    if exact:
        return exact
    padded = f" {fold(text)} "
    best_len, fields = 0, set()
    for alias, field in _LOOKUP.items():
        if f" {alias} " in padded:
            if len(alias) > best_len:
                best_len, fields = len(alias), {field}
            elif len(alias) == best_len:
                fields.add(field)
    return fields.pop() if len(fields) == 1 else None
