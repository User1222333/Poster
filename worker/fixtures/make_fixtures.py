"""Generuje dane DEMONSTRACYJNE: źródło (20 produktów) i JAWNIE FIKCYJNY szablon docelowy.

Uruchom: python fixtures/make_fixtures.py
Szablon nie odwzorowuje żadnego rzeczywistego rynku — kody `demo_*` są zmyślone na potrzeby testów.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.engine.gtin import check_digit  # noqa: E402

HERE = Path(__file__).parent
DEMO_MARKER = "MDB-DEMO-FIXTURE"
IMG = "https://example.com/demo/"


def ean(body12: str) -> str:
    return body12 + str(check_digit(body12))


HEAD = ["nr zapasu", "EAN", "Nazwa", "Opis", "Kategoria", "Kolor", "Skład", "Wzór", "Szerokość", "Wysokość",
        "Wymiary", "Marka", "Zdjęcie 1", "Zdjęcie 2", "Cena"]

# (sku, ean, nazwa, opis, kategoria, kolor, skład, wzór, szer, wys, wymiary, zdj2?, cena)
ROWS = [
    ("ZAS-001", ean("590000000001"), "Zasłona welurowa Łucja", "<p>Miękka zasłona z weluru. <b>Gęsty splot</b>.</p>",
     "Dom/Tekstylia/Zasłony", "ciemnozielony", "100% poliester", "gładki", "140 cm", "260 cm", "140 x 260 cm", True, "129,99"),
    ("ZAS-002", ean("590000000002"), "Zasłona zaciemniająca Żaneta", "Zasłona blackout. Kup teraz! Idealna do sypialni.",
     "Dom/Tekstylia/Zasłony", "Szary", "100% poliester", "gładki", "140", "260", "140x260cm", True, "149,00"),
    ("ZAS-003", ean("001234500001"), "Zasłona lniana Świt", "Naturalna zasłona\nz domieszką lnu.",
     "Dom/Tekstylia/Zasłony", "beżowy", "70% len, 30% bawełna", "gładki", "1,4 m", "2,6 m", "140 × 260 cm", False, "199,50"),
    ("ZAS-004", "5900000000049", "Zasłona żakardowa Ćma", "Elegancki żakard z połyskiem.",
     "Dom/Tekstylia/Zasłony", "złoty", "100% poliester", "żakard", "140 cm", "260 cm", "140 x 260 cm", False, "179,00"),
    ("ZAS-005", ean("590000000005"), "Zasłona na przelotkach Ńemo", "Zasłona na przelotkach, łatwy montaż.",
     "Dekoracje okienne", "granatowy", "", "gładki", "140 cm", "260 cm", "140 x 260", True, "99,90"),
    ("FIR-001", ean("590000000006"), "Firana woalowa Mgiełka", "Delikatna firana z woalu.",
     "Dom/Tekstylia/Firany", "biały", "100% poliester", "gładki", "300 cm", "250 cm", "300 x 250 cm", True, "89,00"),
    ("FIR-002", ean("590000000007"), "Firana żakardowa Róża", "Firana z motywem róż.",
     "Dom/Tekstylia/Firany", "kremowy", "100% poliester", "kwiaty", "400 cm", "160 cm", "400 x 160 cm", False, "119,00"),
    ("FIR-003", ean("003456700002"), "Firana siatka Źródło", "Firana typu siatka.",
     "Dom/Tekstylia/Firany", "biały", "100% poliester", "siatka", "140 cm", "250 cm", "140*250 cm", False, "59,99"),
    ("FIR-004", ean("590000000009"), "Firana gotowa Łąka", "Gotowa firana z haftem. Sprawdź i kup teraz.",
     "Dekoracje okienne", "biały", "100% poliester", "haft", "350 cm", "150 cm", "350 x 150 cm", True, "139,00"),
    ("POS-001", ean("590000000010"), "Poszewka welurowa 45x45 Butelkowa zieleń", "Poszewka z zamkiem.",
     "Dom/Tekstylia/Poszewki", "Butelkowa zieleń", "100% poliester", "gładki", "45 cm", "45 cm", "45x45", True, "24,99"),
    ("POS-002", ean("590000000011"), "Poszewka bawełniana 45x45 Krata", "Poszewka w kratę.",
     "Dom/Tekstylia/Poszewki", "czerwony", "100% bawełna", "krata", "45", "45", "45 x 45 cm", False, "19,99"),
    ("POS-003", ean("590000000012"), "Poszewka lniana 45x45 Żółta", "Poszewka z lnu.",
     "Dom/Tekstylia/Poszewki", "żółty", "100% len", "gładki", "45 cm", "45 cm", "45*45 cm", False, "34,00"),
    ("POS-003", ean("590000000013"), "Poszewka lniana 45x45 Szara", "Poszewka z lnu, szara.",
     "Dom/Tekstylia/Poszewki", "szary", "100% len", "gładki", "45 cm", "45 cm", "45 x 45 cm", False, "34,00"),
    ("DYW-001", ean("590000000014"), "Dywan shaggy Miś 120x170", "Miękki dywan z długim włosiem.",
     "Dom/Dywany", "szary", "100% polipropylen", "gładki", "120 cm", "170 cm", "120 x 170 cm", True, "349,00"),
    ("DYW-002", ean("590000000015"), "Dywan wełniany Góral 160x230", "Dywan z wełny owczej.",
     "Dom/Dywany", "beżowy", "80% wełna, 20% bawełna", "geometryczny", "160 cm", "230 cm", "160 x 230 cm", True, "1 299,00"),
    ("DYW-003", ean("590000000016"), "Dywan okrągły Koło 100", "Okrągły dywan do pokoju dziecka.",
     "Dom/Dywany", "różowy", "", "gładki", "100 cm", "100 cm", "100 x 100 cm", False, "159,00"),
    ("DYW-004", ean("590000000017"), "Dywan zewnętrzny Taras 200x290", "=HYPERLINK(\"http://example.com\";\"Dywan\") na taras.",
     "Dom/Dywany", "antracyt", "100% polipropylen", "paski", "200 cm", "290 cm", "200 x 290 cm", False, "279,00"),
    ("POD-001", ean("590000000018"), "Podkładka korkowa pod talerz", "Podkładka z naturalnego korka.",
     "Dom/Kuchnia/Podkładki", "naturalny", "100% korek", "gładki", "30 cm", "40 cm", "30 x 40 cm", True, "12,50"),
    ("POD-002", ean("590000000019"), "Podkładka pleciona Słoma", "Okrągła podkładka pleciona.",
     "Tekstylia", "beżowy", "100% papier", "pleciony", "38 cm", "38 cm", "38 x 38 cm", False, "9,99"),
    ("POD-003", ean("590000000020"), "Podkładka filcowa 4 szt.", "Zestaw 4 podkładek filcowych.",
     "Dom/Kuchnia/Podkładki", "ciemnoszary", "100% filc", "gładki", "30 cm", "45 cm", "30 x 45 cm", True, "29,00"),
]


def build_source(path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        w.writerow(HEAD)
        for sku, code, name, desc, cat, color, comp, pattern, width, height, dims, img2, price in ROWS:
            base = sku.lower()
            w.writerow([sku, code, name, desc, cat, color, comp, pattern, width, height, dims, "Demo Dom",
                        f"{IMG}{base}-1.jpg", f"{IMG}{base}-2.jpg" if img2 else "", price])


COLUMNS = [
    ("demo_sku", "SKU sprzedawcy [DEMO]", "tak", 40), ("demo_ean", "EAN [DEMO]", "tak", 14),
    ("demo_nazwa", "Nazwa [DEMO]", "tak", 120), ("demo_opis", "Opis [DEMO]", "tak", 2000),
    ("demo_kategoria", "Kategoria [DEMO]", "tak", None), ("demo_kolor", "Kolor [DEMO]", "tak", None),
    ("demo_sklad", "Skład materiału [DEMO]", "tak", 200), ("demo_wzor", "Wzór [DEMO]", "nie", None),
    ("demo_szerokosc_cm", "Szerokość cm [DEMO]", "tak", None), ("demo_wysokosc_cm", "Wysokość cm [DEMO]", "tak", None),
    ("demo_marka", "Marka [DEMO]", "nie", 60), ("demo_zdjecie_1", "Zdjęcie 1 [DEMO]", "tak", None),
    ("demo_zdjecie_2", "Zdjęcie 2 [DEMO]", "nie", None),
]
LISTS = {
    "demo_kategoria": ["DEMO-ZASLONY", "DEMO-FIRANY", "DEMO-POSZEWKI", "DEMO-DYWANY", "DEMO-PODKLADKI"],
    "demo_kolor": ["bialy", "bezowy", "szary", "zielony", "czerwony", "zolty", "zloty", "granatowy", "rozowy",
                   "kremowy", "naturalny", "antracyt"],
    "demo_wzor": ["gladki", "kwiaty", "krata", "paski", "geometryczny", "zakard", "siatka", "haft", "pleciony"],
}


def build_template(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dane"
    ws.append([c[1] for c in COLUMNS])
    ws.append([c[0] for c in COLUMNS])
    ws["B2"].comment = Comment("FIKCYJNE pole testowe. GTIN-8/12/13/14.", "MDB demo")
    lists = wb.create_sheet("Listy")
    for i, (code, values) in enumerate(LISTS.items(), start=1):
        lists.cell(row=1, column=i, value=code)
        for r, v in enumerate(values, start=2):
            lists.cell(row=r, column=i, value=v)
    dv = DataValidation(type="list", formula1=f"Listy!$B$2:$B${len(LISTS['demo_kolor']) + 1}", allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("F3:F5000")
    attrs = wb.create_sheet("Atrybuty")
    attrs.append(["Kod", "Etykieta", "Wymagane", "Maksymalna długość"])
    for code, label, required, limit in COLUMNS:
        attrs.append([code, label, required, limit])
    info = wb.create_sheet("README")
    info.append([DEMO_MARKER])
    info.append(["FIKCYJNY szablon testowy Marketplace Data Bridge. Nie pochodzi z żadnego marketplace."])
    info.append(["Plik wygenerowany na tym szablonie NIE jest gotowy do wysłania."])
    wb.save(path)


if __name__ == "__main__":
    build_source(HERE / "demo_zrodlo_tekstylia.csv")
    build_template(HERE / "demo_szablon_FIKCYJNY.xlsx")
    print("ok")
