"""Odczyt struktury szablonu docelowego (XLSX/CSV).

Parser niczego nie „wie” o konkretnym rynku. Z pliku wyciąga tylko to, co jest w nim zapisane
(nagłówki, komentarze, listy walidacji danych, arkusze pomocnicze wskazane przez użytkownika).
Wszystko pozostaje PROPOZYCJĄ do czasu zatwierdzenia profilu wymagań przez użytkownika.
"""

from __future__ import annotations

import re
import zipfile

import openpyxl
from openpyxl.utils import get_column_letter, range_boundaries

from .aliases import hint_field
from .normalize import fold, parse_decimal
from .source_parser import cell_to_text, iter_csv

PREVIEW_ROWS = 15
PREVIEW_COLS = 60
REQ_TYPES = ["text", "number", "integer", "gtin", "url", "image_url", "html", "enum"]

_TRUE = {"required", "wymagane", "wymagany", "wymagana", "tak", "yes", "true", "mandatory", "obowiazkowe", "1"}
_FALSE = {"optional", "opcjonalne", "opcjonalny", "nie", "no", "false", "recommended", "zalecane", "0"}
_OFFER_FIELDS = {"price", "quantity", "condition", "lead_time", "currency"}
_PRODUCT_FIELDS = {"title", "description", "category_path", "images", "brand", "color", "material"}
_LOSSY_PARTS = {
    "xl/drawings/": "rysunki/obrazy", "xl/media/": "osadzone obrazy", "xl/charts/": "wykresy",
    "xl/pivotTables/": "tabele przestawne", "xl/externalLinks/": "łącza zewnętrzne",
    "xl/ctrlProps/": "kontrolki formularza", "xl/slicers/": "fragmentatory", "customXml/": "dane customXml",
    "xl/activeX/": "kontrolki ActiveX",
}


def empty_requirement() -> dict:
    return {"required": None, "type": None, "enum": None, "max_length": None, "min": None, "max": None,
            "categories": None, "source": None, "type_source": None}


def lossless_warnings(path: str) -> list[str]:
    """Elementy XLSX, których openpyxl nie zachowuje bezstratnie przy ponownym zapisie."""
    found: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for prefix, label in _LOSSY_PARTS.items():
            if any(n.startswith(prefix) for n in names):
                found.append(f"Szablon zawiera {label} — mogą nie zostać zachowane w eksporcie XLSX.")
        for n in names:
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"):
                with zf.open(n) as fh:
                    if b"<extLst" in fh.read(50_000_000):
                        found.append(f"Arkusz {n.rsplit('/', 1)[-1]} zawiera rozszerzenia (extLst), np. "
                                     "rozszerzone walidacje lub formatowanie — mogą nie zostać zachowane.")
    return found


def _sheet_preview(ws) -> list[list[str]]:
    rows = []
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 0, PREVIEW_ROWS),
                            max_col=min(ws.max_column or 0, PREVIEW_COLS)):
        rows.append([cell_to_text(c.value) for c in row])
    return rows


def _filled(row: list[str]) -> int:
    return sum(1 for c in row if c.strip())


def _resolve_list(wb, ws, formula: str) -> list[str] | None:
    """Wartości listy walidacji: lista inline albo zakres/nazwa zdefiniowana w skoroszycie."""
    f = (formula or "").strip()
    if f.startswith('"') and f.endswith('"'):
        return [v.strip() for v in f[1:-1].split(",") if v.strip()]
    f = f.lstrip("=")
    if f in wb.defined_names:
        f = wb.defined_names[f].attr_text
    sheet = ws
    if "!" in f:
        sheet_name, f = f.rsplit("!", 1)
        sheet_name = sheet_name.strip("'").replace("''", "'")
        if sheet_name not in wb.sheetnames:
            return None
        sheet = wb[sheet_name]
    try:
        min_col, min_row, max_col, max_row = range_boundaries(f.replace("$", ""))
    except (ValueError, TypeError):
        return None
    if min_col is None or min_row is None:
        return None
    max_row = min(max_row or sheet.max_row, sheet.max_row)
    values: list[str] = []
    for row in sheet.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col or min_col):
        for c in row:
            v = cell_to_text(c.value).strip()
            if v and v not in values:
                values.append(v)
    return values


def _apply_validations(wb, ws, columns: list[dict], data_start: int, reqs: dict) -> None:
    by_index = {c["index"]: c["key"] for c in columns}
    for dv in ws.data_validations.dataValidation:
        cols: set[int] = set()
        for rng in dv.sqref.ranges:
            if rng.max_row >= data_start:
                cols.update(range(rng.min_col, rng.max_col + 1))
        for ci in cols:
            key = by_index.get(ci)
            if not key:
                continue
            req = reqs[key]
            if dv.type == "list":
                values = _resolve_list(wb, ws, dv.formula1)
                if values:
                    req.update(type="enum", enum=[{"code": v, "label": None} for v in values],
                               source="data_validation")
            elif dv.type in ("whole", "decimal"):
                req.update(type="integer" if dv.type == "whole" else "number", source="data_validation")
                lo, hi = parse_decimal(dv.formula1), parse_decimal(dv.formula2)
                if dv.operator in (None, "between") and lo is not None and hi is not None:
                    req.update(min=str(lo), max=str(hi))
                elif dv.operator in ("greaterThanOrEqual",) and lo is not None:
                    req["min"] = str(lo)
                elif dv.operator in ("lessThanOrEqual",) and lo is not None:
                    req["max"] = str(lo)
            elif dv.type == "textLength" and dv.operator in ("lessThanOrEqual", "between"):
                limit = parse_decimal(dv.formula2 if dv.operator == "between" else dv.formula1)
                if limit is not None:
                    req.update(max_length=int(limit), source=req["source"] or "data_validation")


def _apply_dictionary_wide(ws, columns: list[dict], reqs: dict, header_row: int) -> int:
    """Arkusz słowników w układzie „kolumna = atrybut”: nagłówek równy kodowi/etykiecie kolumny."""
    by_name: dict[str, str] = {}
    for c in columns:
        by_name[fold(c["code"])] = c["key"]
        if c.get("label"):
            by_name.setdefault(fold(c["label"]), c["key"])
    matched = 0
    for col in ws.iter_cols(min_row=header_row, max_col=ws.max_column):
        head = cell_to_text(col[0].value)
        key = by_name.get(fold(head)) if head.strip() else None
        if not key:
            continue
        values = []
        for cell in col[1:]:
            v = cell_to_text(cell.value).strip()
            if v and v not in values:
                values.append(v)
        if values:
            reqs[key].update(type="enum", enum=[{"code": v, "label": None} for v in values],
                             source="dictionary_sheet")
            matched += 1
    return matched


def _apply_dictionary_long(ws, columns, reqs, header_row: int, roles: dict) -> int:
    """Układ „długi”: kolumna z kodem atrybutu, kodem wartości i (opcjonalnie) etykietą."""
    by_code = {c["code"]: c["key"] for c in columns}
    a, v, lab = roles.get("attribute"), roles.get("value_code"), roles.get("value_label")
    if not a or not v:
        return 0
    collected: dict[str, list[dict]] = {}
    for row in ws.iter_rows(min_row=header_row + 1):
        vals = [cell_to_text(c.value).strip() for c in row]
        attr = vals[a - 1] if a <= len(vals) else ""
        code = vals[v - 1] if v <= len(vals) else ""
        if attr in by_code and code:
            label = vals[lab - 1] if lab and lab <= len(vals) and vals[lab - 1] else None
            collected.setdefault(by_code[attr], []).append({"code": code, "label": label})
    for key, items in collected.items():
        reqs[key].update(type="enum", enum=items, source="dictionary_sheet")
    return len(collected)


def _apply_attribute_sheet(ws, columns, reqs, header_row: int, roles: dict) -> int:
    """Arkusz opisu atrybutów. `roles` to numery kolumn (1..n): code, required, type, max_length."""
    by_code = {c["code"]: c["key"] for c in columns}
    code_col = roles.get("code")
    if not code_col:
        return 0
    matched = 0
    for row in ws.iter_rows(min_row=header_row + 1):
        vals = [cell_to_text(c.value).strip() for c in row]

        def at(role: str, vals=vals) -> str:
            idx = roles.get(role)
            return vals[idx - 1] if idx and idx <= len(vals) else ""

        key = by_code.get(at("code"))
        if not key:
            continue
        matched += 1
        req = reqs[key]
        flag = fold(at("required"))
        if flag in _TRUE:
            req.update(required=True, source=req["source"] or "attribute_sheet")
        elif flag in _FALSE:
            req.update(required=False, source=req["source"] or "attribute_sheet")
        limit = parse_decimal(at("max_length"))
        if limit is not None and limit > 0:
            req["max_length"] = int(limit)
    return matched


def _propose_attribute_roles(preview: list[list[str]]) -> tuple[int, dict] | None:
    for r, row in enumerate(preview[:5]):
        roles: dict[str, int] = {}
        for i, cell in enumerate(row):
            k = fold(cell)
            if k in ("code", "kod", "attribute code", "kod atrybutu", "attribute") and "code" not in roles:
                roles["code"] = i + 1
            elif k.startswith(("required", "wymag", "requirement", "obowiazk")) and "required" not in roles:
                roles["required"] = i + 1
            elif k in ("max length", "maksymalna dlugosc", "max dlugosc", "dlugosc") and "max_length" not in roles:
                roles["max_length"] = i + 1
        if "code" in roles and "required" in roles:
            return r + 1, roles
    return None


def _columns_from_header(header: list[str], labels: list[str] | None, comments: dict[int, str]) -> tuple[list[dict], bool]:
    columns: list[dict] = []
    seen: dict[str, int] = {}
    duplicates = False
    for i, raw in enumerate(header):
        code = raw.strip()
        if not code:
            continue
        seen[code] = seen.get(code, 0) + 1
        key = code
        if seen[code] > 1:
            duplicates = True
            key = f"{code} [{get_column_letter(i + 1)}]"
        label = labels[i].strip() if labels and i < len(labels) else ""
        columns.append({"index": i + 1, "key": key, "code": code, "label": label or None,
                        "comment": comments.get(i + 1)})
    return columns, duplicates


def _kind_proposal(columns: list[dict]) -> str:
    fields = {hint_field(c["code"]) or hint_field(c.get("label") or "") for c in columns}
    offer, product = fields & _OFFER_FIELDS, fields & _PRODUCT_FIELDS
    if offer and product:
        return "mixed"
    if offer:
        return "offers"
    if product:
        return "products"
    return "unknown"


def _name_hints(columns: list[dict], reqs: dict) -> None:
    """Podpowiedź typu na podstawie nazwy kolumny — wyłącznie propozycja (source=name_hint)."""
    for c in columns:
        req = reqs[c["key"]]
        if req["type"]:
            continue
        field = hint_field(c["code"]) or hint_field(c.get("label") or "")
        hint = {"gtin": "gtin", "images": "image_url", "description": "html", "width": "number",
                "height": "number", "depth": "number", "weight": "number", "price": "number",
                "quantity": "integer"}.get(field or "")
        if hint:
            req.update(type=hint, type_source="name_hint")


def _finish(structure: dict, columns: list[dict], reqs: dict, options: dict) -> None:
    _name_hints(columns, reqs)
    structure["columns"] = columns
    structure["kind_proposal"] = _kind_proposal(columns)
    structure["category_candidates"] = [
        c["key"] for c in columns
        if "category_path" in (hint_field(c["code"]), hint_field(c.get("label") or ""))]
    cat = options.get("category_column")  # None = nie wskazano, "" = jawnie „brak kolumny kategorii”
    if cat is None:
        structure["ambiguities"].append({"code": "category_column", "message":
                                         "Wskaż kolumnę z identyfikatorem kategorii (lub potwierdź, że szablon "
                                         "jej nie zawiera)."})
    elif cat and cat not in reqs:
        structure["ambiguities"].append({"code": "category_column", "message":
                                         "Wskazana kolumna kategorii nie istnieje w szablonie."})
        cat = None
    structure["category_column"] = cat or None


def parse_xlsx_template(path: str, options: dict) -> tuple[dict, dict]:
    wb = openpyxl.load_workbook(path, data_only=False)
    amb: list[dict] = []
    sheets = []
    for ws in wb.worksheets:
        prev = _sheet_preview(ws)
        sheets.append({"name": ws.title, "rows": ws.max_row or 0, "columns": ws.max_column or 0,
                       "preview": prev, "hidden": ws.sheet_state != "visible"})
    by_name = {s["name"]: s for s in sheets}

    data_sheet = options.get("data_sheet")
    if data_sheet not in by_name:
        candidates = [s for s in sheets if any(_filled(r) >= 2 for r in s["preview"])]
        if not candidates:
            raise ValueError("Szablon nie zawiera arkusza z nagłówkami kolumn.")
        data_sheet = max(candidates, key=lambda s: max(_filled(r) for r in s["preview"]))["name"]
        if len(candidates) > 1:
            amb.append({"code": "data_sheet", "message":
                        f"Skoroszyt ma {len(candidates)} arkusze z danymi. Potwierdź arkusz danych."})
    ws = wb[data_sheet]
    preview = by_name[data_sheet]["preview"]

    header_row = options.get("header_row")
    if not header_row:
        header_row = next((i + 1 for i, r in enumerate(preview) if _filled(r) >= 2), 1)
        nxt = preview[header_row] if header_row < len(preview) else []
        if _filled(nxt) >= 2:
            amb.append({"code": "header_row", "message":
                        "Pod wierszem nagłówka są kolejne wypełnione wiersze. Wskaż wiersz z kodami kolumn "
                        "oraz pierwszy wiersz danych."})
    label_row = options.get("label_row") or None
    data_start = options.get("data_start_row") or max(header_row, label_row or 0) + 1

    def row_text(n: int) -> list[str]:
        return [cell_to_text(c.value) for c in ws[n]] if n <= (ws.max_row or 0) else []

    header_cells = ws[header_row] if header_row <= (ws.max_row or 0) else []
    comments = {c.column: c.comment.text.strip() for c in header_cells if c.comment}
    columns, dups = _columns_from_header(row_text(header_row), row_text(label_row) if label_row else None, comments)
    if not columns:
        raise ValueError("Wskazany wiersz nagłówka jest pusty.")
    if dups:
        amb.append({"code": "duplicate_columns", "message":
                    "Szablon zawiera powtórzone nazwy kolumn — rozróżniono je literą kolumny."})
    reqs = {c["key"]: empty_requirement() for c in columns}
    _apply_validations(wb, ws, columns, data_start, reqs)

    structure: dict = {"format": "xlsx", "sheets": sheets, "data_sheet": data_sheet,
                       "header_row": header_row, "label_row": label_row, "data_start_row": data_start,
                       "ambiguities": amb, "lossless_warnings": lossless_warnings(path),
                       "existing_data_rows": max((ws.max_row or 0) - data_start + 1, 0)}

    dict_sheet = options.get("dictionary_sheet")
    if dict_sheet in by_name and dict_sheet != data_sheet:
        hr = options.get("dictionary_header_row") or 1
        if options.get("dictionary_layout") == "long":
            n = _apply_dictionary_long(wb[dict_sheet], columns, reqs, hr, options.get("dictionary_roles") or {})
        else:
            n = _apply_dictionary_wide(wb[dict_sheet], columns, reqs, hr)
        structure["dictionary_sheet"] = dict_sheet
        structure["dictionary_matched"] = n
    attr_sheet = options.get("attribute_sheet")
    if attr_sheet in by_name and attr_sheet != data_sheet:
        roles = options.get("attribute_roles") or {}
        hr = options.get("attribute_header_row") or 1
        structure["attribute_sheet"] = attr_sheet
        structure["attribute_matched"] = _apply_attribute_sheet(wb[attr_sheet], columns, reqs, hr, roles)
    else:
        for s in sheets:
            if s["name"] == data_sheet:
                continue
            proposal = _propose_attribute_roles(s["preview"])
            if proposal:
                structure["attribute_sheet_proposal"] = {"sheet": s["name"], "header_row": proposal[0],
                                                         "roles": proposal[1]}
                break
    _finish(structure, columns, reqs, options)
    wb.close()
    return structure, reqs


def parse_csv_template(text: str, delimiter: str, bom: bool, options: dict) -> tuple[dict, dict]:
    rows = []
    for i, row in enumerate(iter_csv(text, delimiter)):
        if i >= 200:
            break
        rows.append(row)
    if not rows:
        raise ValueError("Szablon CSV jest pusty.")
    amb: list[dict] = []
    header_row = options.get("header_row") or next((i + 1 for i, r in enumerate(rows) if _filled(r) >= 2), 1)
    if not options.get("header_row") and header_row < len(rows) and _filled(rows[header_row]) >= 2:
        amb.append({"code": "header_row", "message":
                    "Szablon CSV ma więcej niż jeden wypełniony wiersz. Wskaż wiersz z kodami kolumn oraz "
                    "pierwszy wiersz danych."})
    label_row = options.get("label_row") or None
    data_start = options.get("data_start_row") or max(header_row, label_row or 0) + 1
    labels = rows[label_row - 1] if label_row and label_row <= len(rows) else None
    columns, dups = _columns_from_header(rows[header_row - 1], labels, {})
    if not columns:
        raise ValueError("Wskazany wiersz nagłówka jest pusty.")
    if dups:
        amb.append({"code": "duplicate_columns", "message": "Szablon zawiera powtórzone nazwy kolumn."})
    reqs = {c["key"]: empty_requirement() for c in columns}
    structure = {"format": "csv", "delimiter": delimiter, "bom": bom,
                 "sheets": [{"name": "CSV", "rows": len(rows), "columns": max(len(r) for r in rows),
                             "preview": [r[:PREVIEW_COLS] for r in rows[:PREVIEW_ROWS]], "hidden": False}],
                 "data_sheet": "CSV", "header_row": header_row, "label_row": label_row,
                 "data_start_row": data_start, "ambiguities": amb, "lossless_warnings": [],
                 "existing_data_rows": max(len(rows) - data_start + 1, 0)}
    _finish(structure, columns, reqs, options)
    return structure, reqs


def merge_manual_requirements(parsed: dict, manual: dict) -> dict:
    """Ręczne definicje użytkownika mają pierwszeństwo przed odczytem z pliku."""
    out = {}
    for key, req in parsed.items():
        m = manual.get(key)
        out[key] = {**req, **m, "source": "manual"} if m else req
    return out


def validate_requirement_patch(patch: dict) -> list[str]:
    errors = []
    for key, req in patch.items():
        if req.get("type") not in (None, *REQ_TYPES):
            errors.append(f"{key}: nieznany typ „{req.get('type')}”.")
        if req.get("required") not in (None, True, False):
            errors.append(f"{key}: pole „required” musi być true/false/null.")
        if req.get("type") == "enum" and not req.get("enum"):
            errors.append(f"{key}: typ „enum” wymaga listy dozwolonych wartości.")
        ml = req.get("max_length")
        if ml is not None and (not isinstance(ml, int) or ml <= 0):
            errors.append(f"{key}: maksymalna długość musi być dodatnią liczbą całkowitą.")
        for bound in ("min", "max"):
            if req.get(bound) not in (None, "") and parse_decimal(req[bound]) is None:
                errors.append(f"{key}: „{bound}” musi być liczbą.")
        if req.get("enum") is not None and not all(isinstance(e, dict) and e.get("code") for e in req["enum"]):
            errors.append(f"{key}: każda wartość listy musi mieć niepusty kod.")
    return errors


_RE_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    return _RE_UNSAFE_NAME.sub("_", name).strip("._") or "plik"
