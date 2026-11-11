"""Testy integracyjne: API + kolejka + Postgres (RLS) + magazyn plików. Wymagają bazy testowej."""

import csv
import io
import os
import resource
import tempfile
import time

os.environ.setdefault("DATABASE_URL", "postgresql://mdb:mdb@127.0.0.1:5432/mdb_test")
os.environ["AUTH_MODE"] = "local"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-1234"
os.environ["STORAGE_MODE"] = "local"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="mdb-test-storage-")
os.environ["RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["UPLOAD_LIMIT_PER_MINUTE"] = "100000"

import openpyxl  # noqa: E402
import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_engine import RULES, TPL_OPTS  # noqa: E402

from app import jobs, pipelines  # noqa: E402
from app.api import app  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import many, one, service_tx, user_tx  # noqa: E402
from app.engine.gtin import check_digit  # noqa: E402
from app.migrate import apply  # noqa: E402

API_RULES = [{"target_column": r["target_column"], "steps": r["steps"]} for r in RULES]
CATS = {"Dom/Tekstylia/Zasłony": "DEMO-ZASLONY", "Dom/Tekstylia/Firany": "DEMO-FIRANY",
        "Dom/Tekstylia/Poszewki": "DEMO-POSZEWKI", "Dom/Dywany": "DEMO-DYWANY",
        "Dom/Kuchnia/Podkładki": "DEMO-PODKLADKI"}


@pytest.fixture(scope="session")
def client():
    apply(settings.database_url, local_shim=True)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("truncate organizations, auth.users cascade")
    with TestClient(app) as c:
        yield c


def auth(client, email):
    r = client.post("/auth/local/register", json={"email": email, "password": "haslo-testowe-1"})
    if r.status_code == 409:
        r = client.post("/auth/local/login", json={"email": email, "password": "haslo-testowe-1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def a(client):
    return auth(client, "anna@example.com")


@pytest.fixture(scope="session")
def b(client):
    return auth(client, "bartek@example.com")


def ok(r, code=200):
    assert r.status_code == code, f"{r.status_code}: {r.text}"
    return r.json()


def prepare_import(client, h, **kw):
    imp = ok(client.post("/imports/demo", headers=h, **kw))
    jobs.drain()
    imp = ok(client.get(f"/imports/{imp['id']}", headers=h))
    return imp


def prepare_template(client, h, confirm=True):
    tpl = ok(client.post("/templates/demo", headers=h, json={"marketplace_id": "empik"}))
    jobs.drain()
    ok(client.put(f"/templates/{tpl['id']}/structure", headers=h, json=TPL_OPTS))
    jobs.drain()
    ok(client.put(f"/templates/{tpl['id']}/requirements", headers=h, json={
        "kind": "products", "rule_profile": {"banned_phrases": ["kup teraz"], "allowed_html_tags": ["p", "b", "br"]}}))
    if confirm:
        ok(client.post(f"/templates/{tpl['id']}/confirm", headers=h, json={}))
    return tpl["id"]


@pytest.fixture(scope="session")
def flow(client, a):
    """Główna ścieżka: import -> szablon -> projekt -> reguły -> walidacja."""
    imp = prepare_import(client, a)
    assert imp["status"] == "needs_mapping" and imp["row_count"] == 20
    assert imp["detected"]["suggested_map"]["nr zapasu"] == "sku"
    ok(client.put(f"/imports/{imp['id']}/column-map", headers=a,
                  json={"column_map": imp["column_map"], "save_profile_name": "Eksport Sellasist firmy"}))
    jobs.drain()
    assert ok(client.get(f"/imports/{imp['id']}", headers=a))["status"] == "ready"
    template_id = prepare_template(client, a)
    project = ok(client.post("/projects", headers=a, json={"name": "Tekstylia demo", "import_id": imp["id"],
                                                           "template_id": template_id}))
    for src, code in CATS.items():
        ok(client.put("/category-mappings", headers=a, json={"marketplace_id": "empik", "source_category": src,
                                                             "target_code": code, "template_id": template_id}))
    ok(client.put(f"/projects/{project['id']}/rules", headers=a, json={"rules": API_RULES}))
    ok(client.post(f"/projects/{project['id']}/run", headers=a))
    jobs.drain()
    return {"import_id": imp["id"], "template_id": template_id, "project_id": project["id"]}


def rerun(client, h, pid):
    jobs.drain()
    return ok(client.get(f"/projects/{pid}", headers=h))


def test_requires_login(client):
    assert client.get("/imports").status_code == 401
    assert client.get("/imports", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_source_issues_and_preview(client, a, flow):
    rows = ok(client.get(f"/imports/{flow['import_id']}/rows?limit=100", headers=a))
    assert len(rows) == 20 and rows[2]["raw"]["EAN"].startswith("00")
    issues = ok(client.get(f"/imports/{flow['import_id']}/issues", headers=a))
    summary = {s["code"]: s["count"] for s in issues["summary"]}
    assert summary["GTIN_INVALID"] == 1 and summary["SKU_DUPLICATE"] == 2
    prods = ok(client.get("/products?q=POS-003", headers=a))
    assert prods["total"] == 2


def test_template_wizard_blocks_confirmation_until_resolved(client, a):
    tpl = ok(client.post("/templates/demo", headers=a, json={"marketplace_id": "castorama"}))
    jobs.drain()
    t = ok(client.get(f"/templates/{tpl['id']}", headers=a))
    assert t["is_demo_fixture"] and t["status"] == "needs_review" and not t["confirmed"]
    assert {x["code"] for x in t["structure"]["ambiguities"]} >= {"header_row", "category_column"}
    assert t["unknown_required"] == len(t["requirements"])
    assert client.post(f"/templates/{tpl['id']}/confirm", headers=a, json={}).status_code == 422
    ok(client.put(f"/templates/{tpl['id']}/structure", headers=a,
                  json={"data_sheet": "Dane", "header_row": 2, "label_row": 1, "data_start_row": 3, "category_column": ""}))
    jobs.drain()
    r = client.post(f"/templates/{tpl['id']}/confirm", headers=a, json={})
    assert r.status_code == 422 and "produktów" in r.json()["detail"]
    ok(client.put(f"/templates/{tpl['id']}/requirements", headers=a, json={
        "kind": "products", "requirements": {"demo_sku": {"required": True, "type": "text", "max_length": 40}}}))
    r = client.post(f"/templates/{tpl['id']}/confirm", headers=a, json={})
    assert r.status_code == 422 and "nieustaloną wymagalność" in r.json()["detail"]
    done = ok(client.post(f"/templates/{tpl['id']}/confirm", headers=a, json={"acknowledge_unknown": True}))
    assert done["unknown_required"] == 12
    t = ok(client.get(f"/templates/{tpl['id']}", headers=a))
    assert t["confirmed"] and t["requirements"]["demo_sku"]["source"] == "manual"
    bad = client.put(f"/templates/{tpl['id']}/requirements", headers=a, json={"requirements": {"x": {"required": True}}})
    assert bad.status_code == 422


def test_validation_shows_real_problems(client, a, flow):
    p = ok(client.get(f"/projects/{flow['project_id']}", headers=a))
    assert p["status"] == "needs_review" and p["counters"]["imported"] == 20
    assert p["counters"]["ready"] + p["counters"]["review"] + p["counters"]["blocked"] == 20
    s = ok(client.get(f"/projects/{flow['project_id']}/issues/summary", headers=a))
    g = {(x["code"], x["target_column"]): x for x in s["groups"]}
    assert g[("REQUIRED_MISSING", "demo_sklad")]["products"] == 2
    assert "Skład materiału" in g[("REQUIRED_MISSING", "demo_sklad")]["sample"]
    assert g[("GTIN_INVALID", "demo_ean")]["products"] == 1
    assert g[("CATEGORY_UNMAPPED", "demo_kategoria")]["products"] == 3
    assert g[("DUPLICATE_SKU", None)]["products"] == 2
    assert any(i["code"] == "DEMO_TEMPLATE" for i in s["project_issues"])
    listed = ok(client.get(f"/projects/{flow['project_id']}/issues?code=GTIN_INVALID", headers=a))
    assert listed["total"] == 1 and listed["rows"][0]["sku"] == "ZAS-004" and listed["rows"][0]["processed_value"]
    assert any("Skład" in x for x in p["blockers"])


def test_rules_preview_and_rejection(client, a, flow):
    pid = flow["project_id"]
    prev = ok(client.post(f"/projects/{pid}/rules/preview", headers=a, json={"rules": API_RULES}))
    assert len(prev["rows"]) == 3 and prev["rows"][0]["values"]["demo_szerokosc_cm"] == "140"
    assert prev["rows"][0]["provenance"]["demo_sku"]["source_field"] == "sku"
    bad = [{"target_column": "demo_sku", "steps": [{"type": "fixed_value", "value": "X"}]}]
    assert client.put(f"/projects/{pid}/rules", headers=a, json={"rules": bad}).status_code == 422
    sugg = ok(client.get(f"/projects/{pid}/rules/suggest", headers=a))
    assert any(r["target_column"] == "demo_ean" for r in sugg["rules"])


def test_category_and_value_dictionaries(client, a, flow):
    pid = flow["project_id"]
    cats = ok(client.get(f"/projects/{pid}/categories", headers=a))
    amb = {c["source"]: c for c in cats["categories"] if not c["mapped_code"]}
    assert set(amb) == {"Dekoracje okienne", "Tekstylia"}
    r = client.put("/category-mappings", headers=a, json={"marketplace_id": "empik", "source_category": "Tekstylia",
                                                          "target_code": "NIE-ISTNIEJE", "template_id": flow["template_id"]})
    assert r.status_code == 422
    vals = ok(client.get(f"/projects/{pid}/unmapped-values", headers=a))
    by_val = {v["value"]: v for v in vals["values"] if v["target_column"] == "demo_kolor"}
    assert {"ciemnozielony", "Butelkowa zieleń", "ciemnoszary"} <= set(by_val)
    assert by_val["ciemnozielony"]["suggestions"][0]["value"] == "zielony"
    body = {"marketplace_id": "empik", "target_column": "demo_kolor", "template_id": flow["template_id"]}
    assert client.put("/value-mappings", headers=a, json={**body, "source_value": "ciemnozielony",
                                                          "target_value": "morski"}).status_code == 422
    for src, dst in (("ciemnozielony", "zielony"), ("Butelkowa zieleń", "zielony"), ("ciemnoszary", "szary")):
        ok(client.put("/value-mappings", headers=a, json={**body, "source_value": src, "target_value": dst}))
    ok(client.post(f"/projects/{pid}/run", headers=a))
    rerun(client, a, pid)
    vals = ok(client.get(f"/projects/{pid}/unmapped-values", headers=a))
    assert not [v for v in vals["values"] if v["target_column"] == "demo_kolor"]


def test_overrides_single_bulk_and_revert(client, a, flow):
    pid = flow["project_id"]
    rows = ok(client.get(f"/projects/{pid}/rows?limit=200", headers=a))["rows"]
    zas5 = next(r for r in rows if r["sku"] == "ZAS-005")
    assert zas5["status"] == "blocked"
    o = ok(client.post(f"/projects/{pid}/overrides", headers=a, json={
        "row_number": zas5["row_number"], "target_column": "demo_sklad", "value": "100% poliester"}))
    rerun(client, a, pid)
    d = ok(client.get(f"/projects/{pid}/rows/{zas5['row_number']}", headers=a))
    assert d["row"]["values"]["demo_sklad"] == "100% poliester"
    assert d["row"]["provenance"]["demo_sklad"]["user_override"] is True
    assert d["raw"]["Skład"] == "" and d["history"][0]["previous_value"] == ""
    assert {i["code"] for i in d["issues"]} == {"CATEGORY_UNMAPPED"}
    bulk = {"target_column": "demo_sklad", "issue_code": "REQUIRED_MISSING", "value": "100% polipropylen"}
    prev = ok(client.post(f"/projects/{pid}/overrides/bulk", headers=a, json=bulk))
    assert prev == {"count": 1, "applied": False, "sample": prev["sample"]} and prev["sample"][0]["sku"] == "DYW-003"
    assert client.post(f"/projects/{pid}/overrides/bulk", headers=a,
                       json={**bulk, "confirm": True, "expected_count": 5}).status_code == 409
    ok(client.post(f"/projects/{pid}/overrides/bulk", headers=a, json={**bulk, "confirm": True, "expected_count": 1}))
    p = rerun(client, a, pid)
    assert not [g for g in p["issue_groups"] if g["code"] == "REQUIRED_MISSING"]
    ok(client.post(f"/projects/{pid}/overrides/{o['id']}/revert", headers=a))
    p = rerun(client, a, pid)
    assert [g for g in p["issue_groups"] if g["code"] == "REQUIRED_MISSING"][0]["rows"] == 1
    ok(client.post(f"/projects/{pid}/overrides", headers=a, json={
        "row_number": zas5["row_number"], "target_column": "demo_sklad", "value": "100% poliester"}))
    rerun(client, a, pid)


def download(client, h, export_id, file="data"):
    r = client.get(f"/exports/{export_id}/download?file={file}", headers=h)
    assert r.status_code == 200, r.text
    return r


def test_export_xlsx_csv_and_report(client, a, flow):
    pid = flow["project_id"]
    p = ok(client.get(f"/projects/{pid}", headers=a))
    ready = p["counters"]["ready"]
    assert ready >= 10 and p["counters"]["blocked"] >= 1
    assert client.post(f"/projects/{pid}/exports", headers=a,
                       json={"format": "xlsx", "scope": "all"}).status_code == 422
    e1 = ok(client.post(f"/projects/{pid}/exports", headers=a, json={"format": "xlsx", "scope": "ready_only"}))
    e2 = ok(client.post(f"/projects/{pid}/exports", headers=a,
                        json={"format": "csv", "scope": "all", "acknowledge_problems": True}))
    jobs.drain()
    exports = {e["id"]: e for e in ok(client.get(f"/exports?project_id={pid}", headers=a))}
    x = exports[e1["id"]]
    assert x["status"] == "completed" and x["filename"].startswith("DEMO-NIE-WYSYLAC_empik_products_")
    assert x["counters"]["exported"] == ready and x["compatibility"]["declared_compatible"] is False
    r = download(client, a, e1["id"])
    assert "attachment" in r.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb["Dane"]
    assert wb.sheetnames == ["Dane", "Listy", "Atrybuty", "README"] and ws.max_row == ready + 2
    assert [c.value for c in ws[2]][:2] == ["demo_sku", "demo_ean"]
    eans = [c.value for c in ws["B"][2:]]
    assert any(e.startswith("00") for e in eans) and all(isinstance(e, str) for e in eans)
    assert not [c for row in ws.iter_rows(min_row=3) for c in row if c.data_type == "f"]
    skus = [c.value for c in ws["A"][2:]]
    assert "ZAS-004" not in skus and "POS-003" not in skus
    c = download(client, a, e2["id"]).content.decode("utf-8")
    lines = list(csv.reader(io.StringIO(c), delimiter=";"))
    assert len(lines) == 22 and any(cell.startswith("'=HYPERLINK") for row in lines for cell in row)
    assert any("apostrof" in x for x in exports[e2["id"]]["compatibility"]["reasons"])
    rep = openpyxl.load_workbook(io.BytesIO(download(client, a, e1["id"], "report").content))
    assert rep["Podsumowanie"]["B1"].value == 20
    codes = {row[6].value for row in rep["Braki"].iter_rows(min_row=2)}
    assert {"GTIN_INVALID", "DUPLICATE_SKU", "CATEGORY_UNMAPPED", "DEMO_TEMPLATE"} <= codes
    assert "GTIN_INVALID" in download(client, a, e1["id"], "report_csv").content.decode("utf-8-sig")


def test_export_expiry_and_cleanup(client, a, flow):
    e = ok(client.post(f"/projects/{flow['project_id']}/exports", headers=a,
                       json={"format": "csv", "scope": "ready_only"}))
    jobs.drain()
    with service_tx() as conn:
        conn.execute("update exports set expires_at = now() - interval '1 minute' where id = %s", [e["id"]])
        key = one(conn, "select storage_path from exports where id = %s", [e["id"]])["storage_path"]
    assert client.get(f"/exports/{e['id']}/download", headers=a).status_code == 410
    assert os.path.exists(os.path.join(settings.storage_dir, key))
    assert pipelines.cleanup_expired() == 1
    assert not os.path.exists(os.path.join(settings.storage_dir, key))


def test_tenant_isolation_api_and_rls(client, a, b, flow):
    ok(client.get("/me", headers=b))
    assert ok(client.get("/imports", headers=b)) == [] and ok(client.get("/projects", headers=b)) == []
    for url in (f"/imports/{flow['import_id']}", f"/imports/{flow['import_id']}/rows",
                f"/templates/{flow['template_id']}", f"/projects/{flow['project_id']}",
                f"/projects/{flow['project_id']}/rows", f"/projects/{flow['project_id']}/issues/summary"):
        assert client.get(url, headers=b).status_code == 404, url
    exp = ok(client.get("/exports", headers=a))[0]
    assert client.get(f"/exports/{exp['id']}/download", headers=b).status_code == 404
    assert client.post("/projects", headers=b, json={"name": "x", "import_id": flow["import_id"],
                                                     "template_id": flow["template_id"]}).status_code == 404
    assert ok(client.get("/category-mappings", headers=b)) == [] and ok(client.get("/products", headers=b))["total"] == 0
    # RLS na poziomie bazy — nawet zapytanie bez filtra organizacji nie zwraca cudzych danych.
    uid_b, org_a = ok(client.get("/me", headers=b))["user_id"], ok(client.get("/me", headers=a))["organization"]["id"]
    with user_tx(uid_b) as conn:
        for table in ("imports", "import_rows", "canonical_products", "target_templates", "projects", "output_rows",
                      "validation_issues", "exports", "audit_events", "category_mappings", "processing_jobs"):
            assert one(conn, f"select count(*)::int as n from {table} where organization_id = %s", [org_a])["n"] == 0
        assert many(conn, "select id from organizations where id = %s", [org_a]) == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege), user_tx(uid_b) as conn:
        conn.execute("""insert into imports (organization_id, filename, storage_path, sha256, format, size_bytes)
                        values (%s, 'x.csv', 'x', 'x', 'csv', 1)""", [org_a])
    with pytest.raises(psycopg.errors.InsufficientPrivilege), user_tx(uid_b) as conn:
        conn.execute("delete from audit_events")
    with service_tx() as conn:
        assert one(conn, "select count(*)::int as n from imports where organization_id = %s", [org_a])["n"] >= 1


def test_reuse_profile_and_preset_in_next_session(client, a, flow):
    ok(client.post("/presets", headers=a, json={"name": "Tekstylia -> demo", "project_id": flow["project_id"]}))
    a2 = auth(client, "anna@example.com")  # nowa sesja
    profile = ok(client.get("/profiles", headers=a2))[0]
    assert profile["name"] == "Eksport Sellasist firmy"
    imp = ok(client.post("/imports/demo", headers=a2, data={"profile_id": profile["id"]}))
    imp = ok(client.post("/imports", headers=a2, data={"profile_id": profile["id"]}, files={
        "file": ("nowy.csv", open(os.path.join(settings.fixtures_dir, "demo_zrodlo_tekstylia.csv"), "rb"), "text/csv")}))
    jobs.drain()
    imp = ok(client.get(f"/imports/{imp['id']}", headers=a2))
    assert imp["detected"]["profile_applied"] == "Eksport Sellasist firmy" and imp["column_map"] == profile["column_map"]
    ok(client.put(f"/imports/{imp['id']}/column-map", headers=a2, json={"column_map": imp["column_map"]}))
    jobs.drain()
    p2 = ok(client.post("/projects", headers=a2, json={"name": "Druga sesja", "import_id": imp["id"],
                                                       "template_id": flow["template_id"]}))
    preset = ok(client.get("/presets?marketplace_id=empik", headers=a2))[0]
    loaded = ok(client.get(f"/projects/{p2['id']}/rules/from-preset/{preset['id']}", headers=a2))
    assert len(loaded["rules"]) == len(API_RULES) and loaded["dropped"] == [] and loaded["template_changed"] is False
    ok(client.put(f"/projects/{p2['id']}/rules", headers=a2, json={"rules": loaded["rules"]}))
    ok(client.post(f"/projects/{p2['id']}/run", headers=a2))
    p = rerun(client, a2, p2["id"])
    # słowniki kategorii i wartości zatwierdzone wcześniej działają od razu
    assert p["counters"]["ready"] >= 10 and p["counters"]["auto_assigned"] == 17


def test_unconfirmed_template_blocks_ready(client, a, flow):
    template_id = prepare_template(client, a, confirm=False)
    p = ok(client.post("/projects", headers=a, json={"name": "Niezatwierdzony", "import_id": flow["import_id"],
                                                     "template_id": template_id}))
    simple = [{"target_column": "demo_sku", "steps": [{"type": "direct_field", "field": "sku"}]}]
    ok(client.put(f"/projects/{p['id']}/rules", headers=a, json={"rules": simple}))
    ok(client.post(f"/projects/{p['id']}/run", headers=a))
    got = rerun(client, a, p["id"])
    assert got["status"] == "needs_review"
    assert any(i["code"] == "TEMPLATE_UNCONFIRMED" and i["severity"] == "BLOCKER" for i in got["project_issues"])


def test_upload_rejections_and_idempotency(client, a):
    def up(name, data, mime="text/csv", **kw):
        return client.post("/imports", headers={**a, **kw.get("headers", {})}, files={"file": (name, data, mime)})

    assert up("makro.xlsm", b"PK\x03\x04", "application/octet-stream").status_code == 422
    assert up("dane.csv", b"PK\x03\x04abc").json()["code"] == "SIGNATURE_MISMATCH"
    assert up("dane.csv", b"a;b\n1;2\n", "image/png").status_code == 422
    assert up("dane.csv", b"").status_code == 422
    assert up("zly.xlsx", b"PK\x03\x04garbage", "application/octet-stream").json()["code"] == "NOT_XLSX"
    old = settings.max_upload_mb
    settings.max_upload_mb = 0
    try:
        assert up("duzy.csv", b"a;b\n" * 10).status_code == 413
    finally:
        settings.max_upload_mb = old
    first = ok(up("idem.csv", b"sku;ean\nA;1\n", headers={"Idempotency-Key": "k-1"}))
    again = ok(up("idem.csv", b"sku;ean\nA;1\n", headers={"Idempotency-Key": "k-1"}))
    assert first["id"] == again["id"]
    legacy = ok(up("cp1250.csv", "Nazwa;Kolor\nZasłona;żółty\n".encode("cp1250")))
    jobs.drain()
    got = ok(client.get(f"/imports/{legacy['id']}", headers=a))
    assert got["detected"]["encoding_required"] is True and got["row_count"] == 0
    ok(client.put(f"/imports/{legacy['id']}/parse-options", headers=a, json={"encoding": "cp1250"}))
    jobs.drain()
    assert ok(client.get(f"/imports/{legacy['id']}/rows", headers=a))[0]["raw"] == {"Nazwa": "Zasłona", "Kolor": "żółty"}


def test_job_retry_then_failure(client, a, monkeypatch):
    imp = ok(client.post("/imports/demo", headers=a))
    calls = {"n": 0}

    def boom(job):
        calls["n"] += 1
        raise RuntimeError("awaria tymczasowa z sekretem=abc")

    monkeypatch.setitem(pipelines.HANDLERS, "parse_import", boom)
    for _ in range(3):
        assert jobs.run_one() is True
        with service_tx() as conn:
            conn.execute("update processing_jobs set run_after = now() where status = 'queued'")
    assert calls["n"] == 3 and jobs.run_one() is False
    got = ok(client.get(f"/imports/{imp['id']}", headers=a))
    assert got["status"] == "failed" and "sekret" not in got["error"]


def test_performance_2000_rows_deterministic(client, a, flow):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["nr zapasu", "EAN", "Nazwa", "Opis", "Kategoria", "Kolor", "Skład", "Wzór", "Wysokość", "Wymiary",
                "Zdjęcie 1"])
    for i in range(2000):
        body = f"00{i:010d}"
        w.writerow([f"SKU-{i:05d}", body + str(check_digit(body)), f"Zasłona próbna {i}", "Opis <b>żółty</b> " * 5,
                    "Dom/Tekstylia/Zasłony", "szary", "100% poliester", "gładki", "260 cm", "140 x 260 cm",
                    f"https://example.com/i/{i}.jpg"])
    t0 = time.perf_counter()
    imp = ok(client.post("/imports", headers=a, files={"file": ("duzy.csv", buf.getvalue().encode(), "text/csv")}))
    jobs.drain()
    imp = ok(client.get(f"/imports/{imp['id']}", headers=a))
    assert imp["row_count"] == 2000
    ok(client.put(f"/imports/{imp['id']}/column-map", headers=a, json={"column_map": imp["column_map"]}))
    jobs.drain()
    p = ok(client.post("/projects", headers=a, json={"name": "Wydajność", "import_id": imp["id"],
                                                     "template_id": flow["template_id"]}))
    ok(client.put(f"/projects/{p['id']}/rules", headers=a, json={"rules": API_RULES}))

    def run():
        ok(client.post(f"/projects/{p['id']}/run", headers=a))
        got = rerun(client, a, p["id"])
        with service_tx() as conn:
            rows = many(conn, """select row_number, status, "values" from output_rows where project_id = %s
                                 order by row_number""", [p["id"]])
        return got, rows

    first, rows1 = run()
    elapsed = time.perf_counter() - t0
    second, rows2 = run()
    assert len(rows1) == 2000 and first["counters"]["imported"] == 2000 and first["counters"]["ready"] == 2000
    assert rows1 == rows2 and first["counters"] == second["counters"]
    assert rows1[7]["values"]["demo_ean"].startswith("00") and len(rows1[7]["values"]["demo_ean"]) == 13
    e = ok(client.post(f"/projects/{p['id']}/exports", headers=a, json={"format": "xlsx", "scope": "ready_only"}))
    jobs.drain()
    exp = next(x for x in ok(client.get(f"/exports?project_id={p['id']}", headers=a)) if x["id"] == e["id"])
    assert exp["counters"]["exported"] == 2000
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"\n[perf] 2000 wierszy: import+kanon+walidacja {elapsed:.2f}s, max RSS procesu testów {rss_mb:.0f} MB")
    assert elapsed < 60 and rss_mb < 700
