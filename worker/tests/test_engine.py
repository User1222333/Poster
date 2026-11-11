"""Testy jednostkowe silnika: normalizacja, parsery, GTIN, reguły, walidacja, eksport."""

import csv
import zipfile
from decimal import Decimal

import openpyxl
import pytest
from conftest import FIXTURES

from app.engine import exporter, rules, source_parser as sp, template_parser as tp, validate
from app.engine.aliases import suggest_column_map
from app.engine.canonical import build_product, duplicate_issues, source_issues
from app.engine.gtin import validate_gtin
from app.engine.normalize import clean_text, fold, parse_decimal, split_dimensions

SRC = FIXTURES / "demo_zrodlo_tekstylia.csv"
TPL = FIXTURES / "demo_szablon_FIKCYJNY.xlsx"
TPL_OPTS = {"data_sheet": "Dane", "header_row": 2, "label_row": 1, "data_start_row": 3,
            "dictionary_sheet": "Listy", "attribute_sheet": "Atrybuty",
            "attribute_roles": {"code": 1, "required": 3, "max_length": 4}, "category_column": "demo_kategoria"}


# ---------------------------------------------------------------- normalizacja
def test_clean_text_keeps_polish_and_fixes_spaces():
    assert clean_text("Zażółć gęślą  jaźń\r\nŁódź ​") == "Zażółć gęślą jaźń\nŁódź"
    assert fold("Skład  Materiału") == "sklad materialu"


@pytest.mark.parametrize("raw,expected", [
    ("12,5", "12.5"), ("1 299,00", "1299.00"), ("1.299,50", "1299.50"), ("1,299.50", "1299.50"),
    ("140", "140"), ("-3,2", "-3.2"),
])
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "abc", "12,5,3", "1.2.3", "12 cm"])
def test_parse_decimal_rejects(raw):
    assert parse_decimal(raw) is None


@pytest.mark.parametrize("raw,dims,unit", [
    ("140 x 260 cm", [140, 260], "cm"), ("140x260cm", [140, 260], "cm"), ("140 × 260", [140, 260], None),
    ("45*45 cm", [45, 45], "cm"), ("1,4 x 2,6 m", [Decimal("1.4"), Decimal("2.6")], "m"),
])
def test_split_dimensions(raw, dims, unit):
    assert split_dimensions(raw) == ([Decimal(d) for d in dims], unit)


@pytest.mark.parametrize("raw", ["140", "140 cm x 2 m", "szeroka x długa", "1 x 2 x 3 x 4", "140 x 260 łokci"])
def test_split_dimensions_ambiguous(raw):
    assert split_dimensions(raw) is None


# ------------------------------------------------------------------------ GTIN
@pytest.mark.parametrize("code", ["5900000000015", "96385074", "036000291452", "10012345678902", "0012345000010"])
def test_gtin_valid(code):
    assert validate_gtin(code) == (True, None)


@pytest.mark.parametrize("code,problem", [
    ("5900000000049", "BAD_CHECK_DIGIT"), ("590000000001", "BAD_CHECK_DIGIT"), ("12345", "BAD_LENGTH"),
    ("59000000000A5", "NOT_DIGITS"), ("", "EMPTY"), ("５９０００００００００１５", "NOT_DIGITS"),
])
def test_gtin_invalid(code, problem):
    assert validate_gtin(code) == (False, problem)


# --------------------------------------------------------------------- parsery
def load_source():
    text, enc = sp.decode_csv(SRC.read_bytes())
    assert enc == "utf-8-sig"
    assert sp.detect_delimiters(text)[0]["delimiter"] == ";"
    raw = list(sp.iter_csv(text, ";"))
    return sp.parse_table(raw, 1, 5000)


def test_csv_lossless_ids_and_rows():
    columns, records = load_source()
    assert columns[:3] == ["nr zapasu", "EAN", "Nazwa"]
    assert len(records) == 20
    by_sku = {r["nr zapasu"]: r for _, r in records}
    assert by_sku["ZAS-003"]["EAN"].startswith("00") and len(by_sku["ZAS-003"]["EAN"]) == 13
    assert records[0][0] == 2  # numer wiersza w pliku
    assert "\n" in by_sku["ZAS-003"]["Opis"]


def test_csv_legacy_encoding_requires_confirmation():
    data = "Nazwa;Kolor\nZasłona;żółty\n".encode("cp1250")
    with pytest.raises(sp.SourceError) as exc:
        sp.decode_csv(data)
    assert exc.value.code == "ENCODING_CONFIRMATION_REQUIRED" and "cp1250" in exc.value.details["candidates"]
    assert "żółty" in sp.decode_csv(data, "cp1250")[0]


def test_csv_delimiter_detection_variants():
    assert sp.detect_delimiters("a,b,c\n1,2,3\n")[0]["delimiter"] == ","
    assert sp.detect_delimiters("a\tb\n1\t2\n")[0]["delimiter"] == "\t"


def test_headers_empty_and_duplicate():
    cols, recs = sp.parse_table([["A", "", "A", ""], ["1", "x", "2", ""]], 1, 10)
    assert cols == ["A", "Kolumna 2", "A (2)"]
    assert recs == [(2, {"A": "1", "Kolumna 2": "x", "A (2)": "2"})]


def test_row_limit():
    with pytest.raises(sp.SourceError) as exc:
        sp.parse_table([["a", "b"]] + [["1", "2"]] * 6, 1, 5)
    assert exc.value.code == "TOO_MANY_ROWS"


def test_xlsx_identifiers_as_text(tmp_path):
    p = tmp_path / "s.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["SKU", "EAN", "Cena", "Kod"])
    ws.append(["0001", 5900000000015, 12.5, 123])
    ws["A2"].number_format = "@"
    ws["D2"].number_format = "000000"
    ws.append(["=1+1", 1.0e13, 3.0, None])
    wb.save(p)
    sp.check_xlsx_archive(str(p))
    rows = list(sp.iter_xlsx(str(p), None))
    assert rows[1] == ["0001", "5900000000015", "12.5", "000123"]
    assert rows[2][1] == "10000000000000" and rows[2][2] == "3"
    assert [s["name"] for s in sp.xlsx_sheets(str(p))] == ["Sheet"]


def test_format_sniffing_rejects_macros_and_mismatch(tmp_path):
    with pytest.raises(sp.SourceError):
        sp.sniff_format(b"PK\x03\x04", "makro.xlsm")
    with pytest.raises(sp.SourceError) as exc:
        sp.sniff_format(b"PK\x03\x04rest", "dane.csv")
    assert exc.value.code == "SIGNATURE_MISMATCH"
    with pytest.raises(sp.SourceError):
        sp.sniff_format(b"\xd0\xcf\x11\xe0", "stary.xlsx")
    assert sp.sniff_format(b"a;b\n", "dane.csv") == "csv"
    macro = tmp_path / "m.xlsx"
    with zipfile.ZipFile(macro, "w") as zf:
        zf.writestr("xl/workbook.xml", "<x/>")
        zf.writestr("xl/vbaProject.bin", "x")
    with pytest.raises(sp.SourceError) as exc:
        sp.check_xlsx_archive(str(macro))
    assert exc.value.code == "UNSUPPORTED_FORMAT"


def test_zip_bomb_rejected(tmp_path):
    bomb = tmp_path / "b.xlsx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", "<x/>")
        zf.writestr("xl/worksheets/sheet1.xml", b"0" * 30_000_000)
    with pytest.raises(sp.SourceError) as exc:
        sp.check_xlsx_archive(str(bomb))
    assert exc.value.code == "ZIP_SUSPICIOUS"


# --------------------------------------------------------------------- aliasy
def test_alias_suggestions_and_conflicts():
    columns, _ = load_source()
    s = suggest_column_map(columns)
    assert s["map"]["nr zapasu"] == "sku" and s["map"]["EAN"] == "gtin" and s["map"]["Skład"] == "material"
    assert s["map"]["Zdjęcie 1"] == s["map"]["Zdjęcie 2"] == "images"
    assert "Wymiary" not in s["map"]
    c = suggest_column_map(["Kolor", "Barwa", "Nazwa"])
    assert c["conflicts"] == {"color": ["Kolor", "Barwa"]} and "Kolor" not in c["map"]


def products():
    columns, records = load_source()
    cmap = suggest_column_map(columns)["map"]
    return [build_product(raw, cmap, n) for n, raw in records], set(cmap.values())


def test_canonical_and_source_issues():
    prods, mapped = products()
    p = {x["title"]: x for x in prods}
    assert prods[0]["custom_attributes"]["Wymiary"] == "140 x 260 cm"
    assert len(prods[0]["images"]) == 2 and len(prods[2]["images"]) == 1
    assert "na przelotkach" in p["Zasłona na przelotkach Ńemo"]["description"]  # NBSP -> spacja
    bad = source_issues(p["Zasłona żakardowa Ćma"], mapped)
    assert [i["code"] for i in bad] == ["GTIN_INVALID"]
    dups = duplicate_issues(prods)
    assert sorted(prods[i]["sku"] for i in dups) == ["POS-003", "POS-003"]


def test_missing_values_are_not_invented():
    prods, _ = products()
    assert [p["sku"] for p in prods if p["material"] == ""] == ["ZAS-005", "DYW-003"]
    assert all(p["manufacturer"] == "" and p["weight"] == "" for p in prods)


# -------------------------------------------------------------------- szablon
def test_template_ambiguities_without_options():
    structure, reqs = tp.parse_xlsx_template(str(TPL), {})
    codes = {a["code"] for a in structure["ambiguities"]}
    assert {"data_sheet", "header_row", "category_column"} <= codes
    assert structure["attribute_sheet_proposal"]["sheet"] == "Atrybuty"
    assert all(r["required"] is None for r in reqs.values())  # nic nie jest zgadywane


def test_template_with_confirmed_structure():
    structure, reqs = tp.parse_xlsx_template(str(TPL), TPL_OPTS)
    assert structure["ambiguities"] == []
    assert [c["key"] for c in structure["columns"]][:3] == ["demo_sku", "demo_ean", "demo_nazwa"]
    assert structure["columns"][1]["label"] == "EAN [DEMO]" and "FIKCYJNE" in structure["columns"][1]["comment"]
    assert structure["kind_proposal"] == "products"
    assert reqs["demo_sklad"]["required"] is True and reqs["demo_sklad"]["max_length"] == 200
    assert reqs["demo_wzor"]["required"] is False
    assert reqs["demo_kolor"]["type"] == "enum" and reqs["demo_kolor"]["source"] == "dictionary_sheet"
    _, only_dv = tp.parse_xlsx_template(str(TPL), {**TPL_OPTS, "dictionary_sheet": None})
    assert only_dv["demo_kolor"]["source"] == "data_validation" and len(only_dv["demo_kolor"]["enum"]) == 12
    assert only_dv["demo_kategoria"]["enum"] is None
    assert {e["code"] for e in reqs["demo_kategoria"]["enum"]} >= {"DEMO-ZASLONY", "DEMO-DYWANY"}
    assert reqs["demo_ean"]["type"] == "gtin" and reqs["demo_ean"]["type_source"] == "name_hint"
    assert structure["category_candidates"] == ["demo_kategoria"]


def test_csv_template_and_requirement_patch_validation():
    structure, reqs = tp.parse_csv_template("sku;ean;price\n", ";", False, {"category_column": ""})
    assert [c["key"] for c in structure["columns"]] == ["sku", "ean", "price"]
    assert structure["kind_proposal"] == "offers" and structure["ambiguities"] == []
    assert tp.validate_requirement_patch({"sku": {"type": "enum"}}) != []
    assert tp.validate_requirement_patch({"sku": {"type": "text", "required": True, "max_length": 40}}) == []
    merged = tp.merge_manual_requirements(reqs, {"sku": {"required": True}})
    assert merged["sku"]["required"] is True and merged["sku"]["source"] == "manual"


# --------------------------------------------------------------------- reguły
def make_env(**kw):
    structure, reqs = tp.parse_xlsx_template(str(TPL), TPL_OPTS)
    cats = {fold(s): {"code": c, "label": None} for s, c in [
        ("Dom/Tekstylia/Zasłony", "DEMO-ZASLONY"), ("Dom/Tekstylia/Firany", "DEMO-FIRANY"),
        ("Dom/Tekstylia/Poszewki", "DEMO-POSZEWKI"), ("Dom/Dywany", "DEMO-DYWANY"),
        ("Dom/Kuchnia/Podkładki", "DEMO-PODKLADKI")]}
    vals = {("demo_kolor", ""): {fold("ciemnozielony"): "zielony", fold("Butelkowa zieleń"): "zielony",
                                 fold("ciemnoszary"): "szary"}}
    env = rules.Env(marketplace="empik", columns=[c["key"] for c in structure["columns"]],
                    category_column="demo_kategoria", requirements=reqs, category_maps=cats, value_maps=vals,
                    rule_profile={"banned_phrases": ["kup teraz"], "allowed_html_tags": ["p", "b", "br"]},
                    generated_at="2026-01-01T00:00:00Z")
    for k, v in kw.items():
        setattr(env, k, v)
    return env, structure


RULES = [
    {"id": "r1", "target_column": "demo_sku", "steps": [{"type": "direct_field", "field": "sku"}]},
    {"id": "r2", "target_column": "demo_ean", "steps": [{"type": "direct_field", "field": "gtin"}]},
    {"id": "r3", "target_column": "demo_nazwa", "steps": [{"type": "direct_field", "field": "title"}]},
    {"id": "r4", "target_column": "demo_opis", "steps": [
        {"type": "direct_field", "field": "description"}, {"type": "transform_html"},
        {"type": "sanitize_description"}]},
    {"id": "r5", "target_column": "demo_kategoria", "steps": [
        {"type": "direct_field", "field": "category_path"}, {"type": "category_mapping"}]},
    {"id": "r6", "target_column": "demo_kolor", "steps": [
        {"type": "direct_field", "field": "color"}, {"type": "value_dictionary"}]},
    {"id": "r7", "target_column": "demo_sklad", "steps": [{"type": "direct_field", "field": "material"}]},
    {"id": "r8", "target_column": "demo_wzor", "steps": [
        {"type": "direct_field", "field": "pattern"}, {"type": "value_dictionary"}]},
    {"id": "r9", "target_column": "demo_szerokosc_cm", "steps": [
        {"type": "direct_field", "field": "custom:Wymiary"},
        {"type": "split_dimension", "index": 0, "order_trusted": True, "to": "cm", "default_unit": "cm"}]},
    {"id": "r10", "target_column": "demo_wysokosc_cm", "steps": [
        {"type": "direct_field", "field": "height"}, {"type": "normalize_units", "to": "cm", "default_unit": "cm"}]},
    {"id": "r11", "target_column": "demo_marka", "steps": [{"type": "fixed_value", "value": "Demo Dom", "approved": True}]},
    {"id": "r12", "target_column": "demo_zdjecie_1", "steps": [{"type": "direct_field", "field": "images[0]"}]},
    {"id": "r13", "target_column": "demo_zdjecie_2", "steps": [{"type": "direct_field", "field": "images[1]"}]},
]


def run_all(rule_list=RULES, **kw):
    env, structure = make_env(**kw)
    prods, _ = products()
    labels = {c["key"]: c["label"] for c in structure["columns"]}
    out = []
    for p in prods:
        res = rules.evaluate_row(p, rule_list, env)
        res["issues"] += validate.validate_values(res["values"], res["category"], env, labels)
        res["product"] = p
        out.append(res)
    return out, env, structure


def by_sku(results, sku):
    return [r for r in results if r["product"]["sku"] == sku]


def codes(res):
    return sorted({i["code"] for i in res["issues"]})


def test_rules_are_valid_and_invalid_ones_rejected():
    env, _ = make_env()
    assert rules.validate_rules(RULES, env.columns) == []
    bad = [
        {"target_column": "nie_ma", "steps": [{"type": "direct_field", "field": "sku"}]},
        {"target_column": "demo_sku", "steps": [{"type": "fixed_value", "value": "x"}]},
        {"target_column": "demo_ean", "steps": [{"type": "direct_field", "field": "__import__('os')"}]},
        {"target_column": "demo_nazwa", "steps": [{"type": "direct_field", "field": "title"},
                                                  {"type": "regex_extract", "pattern": "(a+)+$"}]},
        {"target_column": "demo_opis", "steps": [{"type": "eval", "code": "1"}]},
    ]
    assert len(rules.validate_rules(bad, env.columns)) >= 5


def test_dimension_split_units_and_decimal_comma():
    results, _, _ = run_all()
    for sku in ("ZAS-001", "ZAS-002", "ZAS-003"):
        v = by_sku(results, sku)[0]["values"]
        assert (v["demo_szerokosc_cm"], v["demo_wysokosc_cm"]) == ("140", "260"), sku
    assert by_sku(results, "POS-001")[0]["values"]["demo_szerokosc_cm"] == "45"
    prov = by_sku(results, "ZAS-003")[0]["provenance"]["demo_wysokosc_cm"]
    assert prov["source_field"] == "height" and "m→cm" in prov["transformation"] and prov["rule_id"] == "r10"


def test_dimension_split_requires_trusted_order():
    untrusted = [dict(r) for r in RULES]
    untrusted[8] = {**RULES[8], "steps": [RULES[8]["steps"][0], {"type": "split_dimension", "index": 0, "to": "cm"}]}
    results, _, _ = run_all(untrusted)
    r = results[0]
    assert r["values"]["demo_szerokosc_cm"] == "" and "DIMENSION_ORDER_UNTRUSTED" in codes(r)


def test_missing_unit_is_review_not_guess():
    no_default = [dict(r) for r in RULES]
    no_default[9] = {**RULES[9], "steps": [RULES[9]["steps"][0], {"type": "normalize_units", "to": "cm"}]}
    results, _, _ = run_all(no_default)
    r = by_sku(results, "ZAS-002")[0]
    assert r["values"]["demo_wysokosc_cm"] == "260" and "UNIT_MISSING" in codes(r)


def test_description_sanitized_and_html_allowlisted():
    results, _, _ = run_all()
    a = by_sku(results, "ZAS-002")[0]
    assert "kup teraz" not in a["values"]["demo_opis"].lower()
    assert a["values"]["demo_opis"] == "Zasłona blackout. Idealna do sypialni."
    assert "usunięto: kup teraz" in a["provenance"]["demo_opis"]["transformation"]
    assert by_sku(results, "ZAS-001")[0]["values"]["demo_opis"] == "<p>Miękka zasłona z weluru. <b>Gęsty splot</b>.</p>"
    assert rules.sanitize_html('<p onclick="x()">a<script>alert(1)</script><a href="j">b</a></p>', ["p"])[0] == "<p>ab</p>"


def test_banned_phrase_blocks_when_not_sanitized():
    raw = [dict(r) for r in RULES]
    raw[3] = {"id": "r4", "target_column": "demo_opis", "steps": [{"type": "direct_field", "field": "description"}]}
    results, _, _ = run_all(raw)
    assert "BANNED_PHRASE" in codes(by_sku(results, "ZAS-002")[0])
    assert "BANNED_PHRASE" not in codes(by_sku(results, "ZAS-001")[0])


def test_category_only_from_approved_alias():
    results, _, _ = run_all()
    assert by_sku(results, "ZAS-001")[0]["values"]["demo_kategoria"] == "DEMO-ZASLONY"
    for sku in ("ZAS-005", "FIR-004", "POD-002"):  # „Dekoracje okienne”, „Tekstylia” — dwuznaczne
        r = by_sku(results, sku)[0]
        assert r["values"]["demo_kategoria"] == "" and "CATEGORY_UNMAPPED" in codes(r), sku


def test_value_dictionary_uses_only_allowed_values():
    results, _, _ = run_all()
    assert by_sku(results, "ZAS-001")[0]["values"]["demo_kolor"] == "zielony"   # wpis słownika
    assert by_sku(results, "ZAS-002")[0]["values"]["demo_kolor"] == "szary"     # „Szary” -> kod z listy
    assert by_sku(results, "ZAS-003")[0]["values"]["demo_kolor"] == "bezowy"    # jednoznaczny kod bez diakrytyków
    env, _ = make_env(value_maps={})
    prods, _ = products()
    r = rules.evaluate_row(prods[0], RULES, env)
    issues = validate.validate_values(r["values"], r["category"], env, {})
    enum_issue = [i for i in issues if i["code"] == "ENUM_NOT_ALLOWED"]
    assert r["values"]["demo_kolor"] == "ciemnozielony" and enum_issue[0]["severity"] == "REVIEW"
    assert enum_issue[0]["suggestions"][0]["value"] == "zielony"  # tylko propozycja


def test_validation_real_problems():
    results, _, _ = run_all()
    assert "GTIN_INVALID" in codes(by_sku(results, "ZAS-004")[0])
    for sku in ("ZAS-005", "DYW-003"):
        r = by_sku(results, sku)[0]
        missing = [i for i in r["issues"] if i["code"] == "REQUIRED_MISSING" and i["target_column"] == "demo_sklad"]
        assert missing and missing[0]["severity"] == "BLOCKER"
    assert codes(by_sku(results, "POD-001")[0]) == []
    assert validate.row_status(by_sku(results, "POD-001")[0]["issues"]) == "ready"
    assert by_sku(results, "ZAS-003")[0]["values"]["demo_ean"].startswith("00")
    dup = validate.dataset_issues([{"row_number": i, "sku": r["product"]["sku"], "gtin": r["product"]["gtin"],
                                    "skipped": False} for i, r in enumerate(results)])
    assert len(dup) == 2 and all(v[0]["code"] == "DUPLICATE_SKU" for v in dup.values())


def test_fictional_required_field_blocks():
    env, structure = make_env()
    env.requirements["demo_marka"] = {**env.requirements["demo_marka"], "required": True}
    no_brand = [r for r in RULES if r["target_column"] != "demo_marka"]
    prods, _ = products()
    r = rules.evaluate_row(prods[0], no_brand, env)
    issues = validate.validate_values(r["values"], r["category"], env, {})
    assert any(i["code"] == "REQUIRED_MISSING" and i["target_column"] == "demo_marka" for i in issues)
    assert validate.row_status(issues) == "blocked"


def test_misc_steps():
    env, _ = make_env()
    p = {"sku": "A", "color": "Czerwony", "pattern": "czerwony", "title": "Zasłona", "brand": "", "images": [],
         "custom_attributes": {"X": "bawełna 80%"}}

    def run(steps, target="demo_nazwa"):
        return rules.evaluate_row(p, [{"target_column": target, "steps": steps}], env)

    assert run([{"type": "alias_lookup", "fields": ["brand", "title"]}])["values"]["demo_nazwa"] == "Zasłona"
    amb = run([{"type": "alias_lookup", "fields": ["color", "title"]}])
    assert amb["values"]["demo_nazwa"] == "" and amb["issues"][0]["code"] == "AMBIGUOUS_SOURCE"
    cat = run([{"type": "concatenate", "parts": [{"field": "title"}, {"text": "—"}, {"field": "color"}]}])
    assert cat["values"]["demo_nazwa"] == "Zasłona — Czerwony"
    rx = run([{"type": "direct_field", "field": "custom:X"}, {"type": "regex_extract", "pattern": "percent"}])
    assert rx["values"]["demo_nazwa"] == "80"
    cond = run([{"type": "conditional", "when": {"field": "brand", "op": "empty"},
                 "then": [{"type": "direct_field", "field": "title"}],
                 "otherwise": [{"type": "direct_field", "field": "brand"}]}])
    assert cond["values"]["demo_nazwa"] == "Zasłona"
    flag = run([{"type": "direct_field", "field": "brand"}, {"type": "flag_missing", "severity": "WARNING"}])
    assert flag["issues"][0]["severity"] == "WARNING"
    rev = run([{"type": "direct_field", "field": "title"}, {"type": "require_review", "reason": "sprawdź"}])
    assert rev["issues"][0]["code"] == "REVIEW_REQUIRED"
    skip = rules.evaluate_row(p, [{"target_column": rules.ROW_TARGET, "steps": [
        {"type": "skip_row", "when": {"field": "sku", "op": "equals", "value": "a"}}]}], env)
    assert skip["skipped"] is True
    nofix = run([{"type": "fixed_value", "value": "x"}])
    assert nofix["values"]["demo_nazwa"] == "" and nofix["issues"][0]["code"] == "FIXED_VALUE_NOT_APPROVED"


def test_suggest_rules_from_aliases():
    env, structure = make_env()
    prop = rules.suggest_rules(structure["columns"], {"sku", "gtin", "title", "category_path", "color", "images"},
                               "demo_kategoria", env.requirements)
    got = {r["target_column"]: [s["type"] for s in r["steps"]] for r in prop}
    assert got["demo_ean"] == ["direct_field"] and got["demo_kategoria"] == ["direct_field", "category_mapping"]
    assert got["demo_kolor"] == ["direct_field", "value_dictionary"] and "demo_sklad" not in got
    assert rules.validate_rules(prop, env.columns) == []


# --------------------------------------------------------------------- eksport
def test_export_xlsx_preserves_template_and_blocks_formulas(tmp_path):
    results, env, structure = run_all()
    rows = [r["values"] for r in results]
    out = tmp_path / "out.xlsx"
    stats = exporter.export_xlsx(str(TPL), structure, env.requirements, rows, str(out))
    assert stats["rows_written"] == 20 and stats["formula_like_cells"] == 1
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["Dane", "Listy", "Atrybuty", "README"]
    ws = wb["Dane"]
    src = openpyxl.load_workbook(TPL)["Dane"]
    for r in (1, 2):
        assert [c.value for c in ws[r]] == [c.value for c in src[r]]
    assert ws["B2"].comment is not None and len(ws.data_validations.dataValidation) == 1
    assert ws["B5"].value == by_sku(results, "ZAS-003")[0]["product"]["gtin"] and ws["B5"].data_type == "s"
    assert ws["I3"].value == 140 and ws["I3"].data_type == "n"
    formula_cells = [c for row in ws.iter_rows(min_row=3) for c in row if c.data_type == "f"]
    assert formula_cells == []
    d = [c for c in ws["D"] if isinstance(c.value, str) and c.value.startswith("=HYPERLINK")]
    assert len(d) == 1 and d[0].data_type == "s"
    assert ws.max_row == 22


def test_export_csv_guard_and_head(tmp_path):
    results, env, structure = run_all()
    head = exporter.template_head_rows(str(TPL), None, structure)
    assert len(head) == 2 and head[1][0] == "demo_sku"
    out = tmp_path / "out.csv"
    stats = exporter.export_csv(head, structure, [r["values"] for r in results], str(out))
    data = out.read_text(encoding="utf-8")
    rows = list(csv.reader(data.splitlines(), delimiter=";"))
    assert rows[1][:2] == ["demo_sku", "demo_ean"] and len(rows) == 22
    assert stats["formula_like_cells"] == 1 and any(c.startswith("'=HYPERLINK") for r in rows for c in r)
    assert "Zasłona welurowa Łucja" in data
    assert exporter.csv_safe("-12,5") == ("-12,5", False) and exporter.csv_safe("+48123456789")[1] is False
    assert exporter.csv_safe("@SUM(A1)") == ("'@SUM(A1)", True) and exporter.csv_safe("-cmd|x")[1] is True


def test_deterministic_second_run():
    a, _, _ = run_all()
    b, _, _ = run_all()
    assert [(r["values"], r["provenance"], r["issues"]) for r in a] == \
           [(r["values"], r["provenance"], r["issues"]) for r in b]


def test_report_files(tmp_path):
    issues = [{"row_number": 5, "sku": "ZAS-004", "gtin": "5900000000049", "target_column": "demo_ean",
               "category": "DEMO-ZASLONY", "severity": "BLOCKER", "code": "GTIN_INVALID",
               "message": "Błędny EAN", "processed_value": "=5900000000049"}]
    exporter.write_report_xlsx(issues, {"imported": 20, "ready": 10}, ["uwaga"], str(tmp_path / "r.xlsx"))
    exporter.write_report_csv(issues, str(tmp_path / "r.csv"))
    ws = openpyxl.load_workbook(tmp_path / "r.xlsx")["Braki"]
    assert ws["B2"].value == "ZAS-004" and ws["I2"].data_type == "s"
    assert "GTIN_INVALID" in (tmp_path / "r.csv").read_text(encoding="utf-8-sig")
