"""Bezpieczne, bezstratne wczytywanie plików źródłowych CSV/XLSX. Wszystko jako tekst."""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal

import openpyxl

from .normalize import format_decimal

LEGACY_ENCODINGS = ["cp1250", "iso-8859-2"]
DELIMITERS = [";", ",", "\t", "|"]
MAX_UNCOMPRESSED = 300 * 1024 * 1024
MAX_RATIO = 250
MAX_ZIP_ENTRIES = 5000


class SourceError(Exception):
    """Błąd czytelny dla użytkownika (kod + komunikat po polsku)."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def sniff_format(data_head: bytes, filename: str) -> str:
    """Rozpoznaje format po faktycznej sygnaturze, a nie tylko po rozszerzeniu."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in ("xlsm", "xltm", "xlsb", "xls"):
        raise SourceError("UNSUPPORTED_FORMAT", f"Format .{ext} nie jest obsługiwany. Zapisz plik jako XLSX "
                          "(bez makr) lub CSV UTF-8.")
    if data_head.startswith(b"PK\x03\x04"):
        if ext != "xlsx":
            raise SourceError("SIGNATURE_MISMATCH", "Zawartość pliku to archiwum ZIP/XLSX, ale rozszerzenie "
                              "jest inne. Zmień rozszerzenie na .xlsx.")
        return "xlsx"
    if data_head.startswith(b"\xd0\xcf\x11\xe0"):
        raise SourceError("UNSUPPORTED_FORMAT", "To stary format Excela (XLS). Zapisz plik jako XLSX lub CSV.")
    if ext not in ("csv", "txt", "tsv"):
        raise SourceError("UNSUPPORTED_FORMAT", "Obsługiwane formaty: CSV (UTF-8) oraz XLSX.")
    if data_head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data_head:
        raise SourceError("UNSUPPORTED_ENCODING", "Plik wygląda na UTF-16 lub binarny. Zapisz go jako CSV UTF-8.")
    return "csv"


def check_xlsx_archive(path: str) -> list[str]:
    """Ochrona przed zip-bombą i makrami. Zwraca listę nazw wpisów archiwum."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            names = [i.filename for i in infos]
            if len(infos) > MAX_ZIP_ENTRIES:
                raise SourceError("ZIP_SUSPICIOUS", "Archiwum XLSX zawiera podejrzanie dużo plików.")
            total = sum(i.file_size for i in infos)
            packed = sum(i.compress_size for i in infos) or 1
            if total > MAX_UNCOMPRESSED or total / packed > MAX_RATIO:
                raise SourceError("ZIP_SUSPICIOUS", "Plik XLSX po rozpakowaniu jest zbyt duży "
                                  "(możliwa zip-bomba). Przetwarzanie przerwano.")
            if "xl/workbook.xml" not in names:
                raise SourceError("NOT_XLSX", "Plik nie jest prawidłowym skoroszytem XLSX.")
            ctypes = zf.read("[Content_Types].xml").decode("utf-8", "replace") if "[Content_Types].xml" in names else ""
            if any(n.lower().endswith("vbaproject.bin") for n in names) or "macroEnabled" in ctypes:
                raise SourceError("UNSUPPORTED_FORMAT", "Skoroszyt zawiera makra (XLSM). Makra nie są "
                                  "uruchamiane ani obsługiwane — zapisz plik jako XLSX.")
            return names
    except zipfile.BadZipFile as exc:
        raise SourceError("NOT_XLSX", "Plik XLSX jest uszkodzony.") from exc


# ------------------------------------------------------------------------ CSV
def decode_csv(data: bytes, encoding: str | None = None) -> tuple[str, str]:
    """UTF-8 (z BOM lub bez) dekodowane ściśle. Starsze kodowanie tylko po jawnym wyborze."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    if encoding in (None, "", "utf-8", "utf-8-sig"):
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError as exc:
            previews = {}
            for enc in LEGACY_ENCODINGS:
                try:
                    previews[enc] = data[:600].decode(enc, "replace")
                except LookupError:  # pragma: no cover
                    continue
            raise SourceError("ENCODING_CONFIRMATION_REQUIRED", "Plik nie jest zapisany w UTF-8. Wybierz "
                              "kodowanie, w którym polskie znaki wyglądają poprawnie.",
                              {"candidates": previews}) from exc
    if encoding not in LEGACY_ENCODINGS:
        raise SourceError("UNSUPPORTED_ENCODING", f"Nieobsługiwane kodowanie: {encoding}.")
    try:
        return data.decode(encoding), encoding
    except UnicodeDecodeError as exc:
        raise SourceError("UNSUPPORTED_ENCODING", f"Pliku nie da się odczytać w kodowaniu {encoding}.") from exc


def detect_delimiters(text: str) -> list[dict]:
    """Ranking separatorów wg spójności liczby kolumn w pierwszych rekordach."""
    sample = text[:200_000]
    out = []
    for delim in DELIMITERS:
        try:
            rows = []
            for i, row in enumerate(csv.reader(io.StringIO(sample), delimiter=delim)):
                if i >= 30:
                    break
                if any(c.strip() for c in row):
                    rows.append(len(row))
        except csv.Error:
            continue
        if not rows:
            continue
        common = max(set(rows), key=rows.count)
        consistency = rows.count(common) / len(rows)
        out.append({"delimiter": delim, "columns": common,
                    "score": round(consistency * (1 if common > 1 else 0), 3)})
    out.sort(key=lambda d: (-d["score"], -d["columns"]))
    return out


def iter_csv(text: str, delimiter: str) -> Iterator[list[str]]:
    csv.field_size_limit(5_000_000)
    yield from csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)


# ----------------------------------------------------------------------- XLSX
_ZERO_FMT = re.compile(r"^0{2,}$")


def cell_to_text(value: object, number_format: str = "General") -> str:
    """Wartość komórki jako tekst: bez notacji naukowej, bez `.0`, z zerami z formatu `0000`."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        s = str(value)
    elif isinstance(value, float):
        s = str(int(value)) if value.is_integer() else format_decimal(Decimal(repr(value)))
    elif isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat(sep=" ")
    elif isinstance(value, dt.date | dt.time):
        return value.isoformat()
    else:
        return str(value)
    if _ZERO_FMT.match(number_format or "") and s.isdigit():
        s = s.zfill(len(number_format))
    return s


def xlsx_sheets(path: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [{"name": ws.title, "rows": ws.max_row or 0, "columns": ws.max_column or 0}
                for ws in wb.worksheets]
    finally:
        wb.close()


def iter_xlsx(path: str, sheet: str | None) -> Iterator[list[str]]:
    """Czyta zapisane wartości (data_only) — formuły i makra nigdy nie są wykonywane."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        for row in ws.iter_rows():
            yield [cell_to_text(c.value, getattr(c, "number_format", "General") or "General") for c in row]
    except KeyError as exc:
        raise SourceError("SHEET_NOT_FOUND", f"Arkusz „{sheet}” nie istnieje w pliku.") from exc
    finally:
        wb.close()


# -------------------------------------------------------------- wspólny odczyt
@dataclass
class ParsedTable:
    columns: list[str]
    column_indexes: list[int]
    rows: Iterator[tuple[int, dict[str, str]]]
    notes: list[str] = field(default_factory=list)


def build_headers(header: list[str], used: set[int]) -> tuple[list[str], list[int]]:
    """Nazwy kolumn: puste nagłówki z danymi -> „Kolumna N”, duplikaty -> sufiks (2), (3)…"""
    names: list[str] = []
    indexes: list[int] = []
    seen: dict[str, int] = {}
    for idx in sorted(used | {i for i, h in enumerate(header) if h.strip()}):
        raw = header[idx].strip() if idx < len(header) else ""
        if not raw and idx not in used:
            continue
        name = " ".join(raw.split()) or f"Kolumna {idx + 1}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name} ({seen[name]})"
        names.append(name)
        indexes.append(idx)
    return names, indexes


def parse_table(raw_rows: list[list[str]], header_row: int, max_rows: int) -> tuple[list[str], list[tuple[int, dict]]]:
    """`header_row` liczony od 1. Zwraca kolumny i rekordy (numer_wiersza_w_pliku, dane)."""
    if header_row < 1 or header_row > len(raw_rows):
        raise SourceError("HEADER_NOT_FOUND", "Wskazany wiersz nagłówka nie istnieje w pliku.")
    header = raw_rows[header_row - 1]
    body = raw_rows[header_row:]
    used = {i for row in body for i, c in enumerate(row) if c.strip()}
    columns, indexes = build_headers(header, used)
    if not columns:
        raise SourceError("HEADER_NOT_FOUND", "Wskazany wiersz nagłówka jest pusty.")
    records: list[tuple[int, dict]] = []
    for offset, row in enumerate(body):
        if not any(c.strip() for c in row):
            continue
        if len(records) >= max_rows:
            raise SourceError("TOO_MANY_ROWS", f"Plik ma więcej niż {max_rows} wierszy danych. "
                              "Podziel go na mniejsze części.")
        records.append((header_row + 1 + offset,
                        {name: (row[i] if i < len(row) else "") for name, i in zip(columns, indexes, strict=True)}))
    return columns, records


def guess_header_row(raw_rows: list[list[str]]) -> int:
    """Pierwszy wiersz z co najmniej dwiema niepustymi komórkami (propozycja do potwierdzenia)."""
    for i, row in enumerate(raw_rows[:30]):
        if sum(1 for c in row if c.strip()) >= 2:
            return i + 1
    return 1
