"""Eksport do rzeczywistego szablonu użytkownika (XLSX/CSV) i raport braków.

Nazwy kolumn, ich kolejność i wiersze nagłówkowe pochodzą 1:1 z szablonu. Tekst zaczynający się
od `= + - @` nigdy nie staje się formułą: w XLSX komórka ma wymuszony typ tekstowy, w CSV
(gdzie nie ma typów) nieliczbowy tekst dostaje prefiks apostrofu i jest to raportowane.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable

import openpyxl
from openpyxl.cell.cell import TYPE_STRING

from .normalize import parse_decimal
from .source_parser import cell_to_text

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def is_formula_like(value: str) -> bool:
    return value.startswith(_FORMULA_START) and not _PLAIN_NUMBER.match(value)


def csv_safe(value: str) -> tuple[str, bool]:
    if is_formula_like(value):
        return "'" + value, True
    return value, False


def _set_text(cell, value: str) -> None:
    cell.value = ILLEGAL_XML.sub("", value)
    cell.data_type = TYPE_STRING  # także dla „=…”: zapis jako tekst, nie formuła
    if value.isdigit():
        cell.number_format = "@"  # identyfikatory: bez notacji naukowej i utraty zer


def export_xlsx(template_path: str, structure: dict, requirements: dict,
                rows: Iterable[dict[str, str]], out_path: str) -> dict:
    wb = openpyxl.load_workbook(template_path)
    ws = wb[structure["data_sheet"]]
    start = structure["data_start_row"]
    if ws.max_row >= start:
        ws.delete_rows(start, ws.max_row - start + 1)
    cols = [(c["index"], c["key"], (requirements.get(c["key"]) or {}).get("type")) for c in structure["columns"]]
    written = forced_text = 0
    for offset, values in enumerate(rows):
        r = start + offset
        for index, key, kind in cols:
            value = values.get(key, "")
            if value == "":
                continue
            cell = ws.cell(row=r, column=index)
            num = parse_decimal(value) if kind in ("number", "integer") else None
            if num is not None:
                cell.value = int(num) if num == num.to_integral_value() else float(num)
            else:
                if is_formula_like(value):
                    forced_text += 1
                _set_text(cell, value)
        written += 1
    wb.save(out_path)
    wb.close()
    return {"rows_written": written, "formula_like_cells": forced_text}


def template_head_rows(template_path: str | None, template_text: str | None, structure: dict) -> list[list[str]]:
    """Wiersze poprzedzające dane (nagłówki, opisy) — przenoszone bez zmian."""
    n = structure["data_start_row"] - 1
    if structure["format"] == "csv":
        reader = csv.reader(io.StringIO(template_text or "", newline=""), delimiter=structure["delimiter"])
        return [row for _, row in zip(range(n), reader, strict=False)]
    wb = openpyxl.load_workbook(template_path, read_only=True, data_only=True)
    try:
        ws = wb[structure["data_sheet"]]
        return [[cell_to_text(c.value) for c in row] for row in ws.iter_rows(min_row=1, max_row=n)]
    finally:
        wb.close()


def export_csv(head_rows: list[list[str]], structure: dict, rows: Iterable[dict[str, str]],
               out_path: str, delimiter: str | None = None) -> dict:
    delimiter = delimiter or structure.get("delimiter") or ";"
    width = max([c["index"] for c in structure["columns"]] + [len(r) for r in head_rows])
    written = guarded = 0
    encoding = "utf-8-sig" if structure.get("bom") else "utf-8"
    with open(out_path, "w", encoding=encoding, newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        for row in head_rows:
            w.writerow(row)
        for values in rows:
            line = [""] * width
            for c in structure["columns"]:
                v, was = csv_safe(values.get(c["key"], ""))
                guarded += was
                line[c["index"] - 1] = v
            w.writerow(line)
            written += 1
    return {"rows_written": written, "formula_like_cells": guarded}


REPORT_HEADERS = ["Wiersz źródła", "SKU", "EAN", "Pole", "Kategoria", "Waga", "Kod problemu", "Opis problemu",
                  "Wartość"]
COUNTER_LABELS = [
    ("imported", "Zaimportowano"), ("auto_assigned", "Automatycznie przypisano (kategoria)"),
    ("ready", "Gotowe"), ("review", "Do decyzji"), ("blocked", "Z błędami"), ("skipped", "Pominięte regułą"),
    ("exported", "Wyeksportowano"),
]


def _report_rows(issues: Iterable[dict]) -> Iterable[list[str]]:
    for i in issues:
        yield [str(i.get("row_number") or ""), i.get("sku") or "", i.get("gtin") or "",
               i.get("target_column") or "", i.get("category") or "", i["severity"], i["code"], i["message"],
               i.get("processed_value") or i.get("raw_value") or ""]


def write_report_xlsx(issues: list[dict], counters: dict, notes: list[str], out_path: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Podsumowanie"
    for key, label in COUNTER_LABELS:
        ws.append([label, counters.get(key, 0)])
    ws.append([])
    for note in notes:
        ws.append([note])
    ws2 = wb.create_sheet("Braki")
    ws2.append(REPORT_HEADERS)
    for r, row in enumerate(_report_rows(issues), start=2):
        for c, value in enumerate(row, start=1):
            if value:
                _set_text(ws2.cell(row=r, column=c), value)
    wb.save(out_path)


def write_report_csv(issues: list[dict], out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        w.writerow(REPORT_HEADERS)
        for row in _report_rows(issues):
            w.writerow([csv_safe(v)[0] for v in row])
