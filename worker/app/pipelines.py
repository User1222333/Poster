"""Obsługa zadań: parse_import, build_canonical, parse_template, run_project, export_project."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import tempfile
import zipfile

from .config import settings
from .db import J, many, one, service_tx
from .engine import exporter, rules, validate
from .engine import source_parser as sp
from .engine import template_parser as tp
from .engine.aliases import suggest_column_map
from .engine.canonical import build_product, duplicate_issues, source_issues
from .engine.normalize import fold
from .jobs import UserError, set_progress
from .storage import object_key, storage

DEMO_MARKER = "MDB-DEMO-FIXTURE"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(storage_path: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="mdb-")
    os.close(fd)
    storage().fetch(storage_path, path)
    return path


def audit(conn, org_id, user_id, action: str, entity: str, entity_id, details: dict | None = None) -> None:
    conn.execute("""insert into audit_events (organization_id, user_id, action, entity, entity_id, details)
                    values (%s, %s, %s, %s, %s, %s)""",
                 [org_id, user_id, action, entity, str(entity_id), J(details or {})])


# ------------------------------------------------------------------- importy
def parse_import(job: dict) -> dict:
    org, import_id = job["organization_id"], job["payload"]["import_id"]
    with service_tx() as conn:
        imp = one(conn, "select * from imports where id = %s and organization_id = %s", [import_id, org])
        if not imp:
            raise UserError("Import nie istnieje.")
        conn.execute("update imports set status = 'parsing', error = null where id = %s", [import_id])
    opts = dict(imp["parse_options"])
    detected: dict = {}
    path = _fetch(imp["storage_path"], "." + imp["format"])
    try:
        if imp["format"] == "csv":
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                text, encoding = sp.decode_csv(data, opts.get("encoding"))
            except sp.SourceError as exc:
                if exc.code != "ENCODING_CONFIRMATION_REQUIRED":
                    raise
                with service_tx() as conn:
                    conn.execute("""update imports set status = 'needs_mapping', detected = %s, columns = '[]',
                                    row_count = 0 where id = %s""",
                                 [J({"encoding_required": True, "encoding_candidates": exc.details["candidates"]}),
                                  import_id])
                return {"encoding_required": True}
            delimiters = sp.detect_delimiters(text)
            if not delimiters:
                raise UserError("Plik CSV jest pusty.")
            delimiter = opts.get("delimiter") or delimiters[0]["delimiter"]
            if delimiter not in sp.DELIMITERS:
                raise UserError("Nieobsługiwany separator.")
            raw_rows = []
            for row in sp.iter_csv(text, delimiter):
                raw_rows.append(row)
                if len(raw_rows) > settings.max_rows + 200:
                    raise sp.SourceError("TOO_MANY_ROWS", f"Plik ma więcej niż {settings.max_rows} wierszy danych.")
            detected.update(encoding=encoding, delimiters=delimiters)
            opts.update(encoding=encoding, delimiter=delimiter)
        else:
            sp.check_xlsx_archive(path)
            sheets = sp.xlsx_sheets(path)
            sheet = opts.get("sheet") or sheets[0]["name"]
            raw_rows = []
            for row in sp.iter_xlsx(path, sheet):
                raw_rows.append(row)
                if len(raw_rows) > settings.max_rows + 200:
                    raise sp.SourceError("TOO_MANY_ROWS", f"Arkusz ma więcej niż {settings.max_rows} wierszy danych.")
            detected.update(sheets=sheets)
            opts.update(sheet=sheet)
    finally:
        os.unlink(path)
    set_progress(job["id"], 40)
    header_row = int(opts.get("header_row") or sp.guess_header_row(raw_rows))
    columns, records = sp.parse_table(raw_rows, header_row, settings.max_rows)
    opts["header_row"] = header_row
    detected["head_preview"] = [r[:40] for r in raw_rows[:8]]
    suggestion = suggest_column_map(columns)
    detected["suggested_map"], detected["conflicts"] = suggestion["map"], suggestion["conflicts"]
    column_map = suggestion["map"]
    if job["payload"].get("profile_id"):
        with service_tx() as conn:
            prof = one(conn, "select * from source_mapping_profiles where id = %s and organization_id = %s",
                       [job["payload"]["profile_id"], org])
        if prof:
            column_map = {c: f for c, f in prof["column_map"].items() if c in columns}
            detected["profile_applied"] = prof["name"]
            detected["profile_missing_columns"] = [c for c in prof["column_map"] if c not in columns]
    with service_tx() as conn:
        conn.execute("delete from canonical_products where import_id = %s", [import_id])
        conn.execute("delete from import_rows where import_id = %s", [import_id])
        with conn.cursor() as cur:
            cur.executemany("insert into import_rows (organization_id, import_id, row_number, raw) values (%s,%s,%s,%s)",
                            [(org, import_id, n, J(raw)) for n, raw in records])
        conn.execute("""update imports set status = 'needs_mapping', parse_options = %s, detected = %s, columns = %s,
                        column_map = %s, column_map_confirmed = false, row_count = %s where id = %s""",
                     [J(opts), J(detected), J(columns), J(column_map), len(records), import_id])
        audit(conn, org, job["created_by"], "import.parsed", "import", import_id, {"rows": len(records)})
    return {"rows": len(records), "columns": len(columns)}


def build_canonical(job: dict) -> dict:
    org, import_id = job["organization_id"], job["payload"]["import_id"]
    with service_tx() as conn:
        imp = one(conn, "select * from imports where id = %s and organization_id = %s", [import_id, org])
        if not imp:
            raise UserError("Import nie istnieje.")
        rows = many(conn, "select row_number, raw from import_rows where import_id = %s order by row_number",
                    [import_id])
    cmap = imp["column_map"]
    mapped = set(cmap.values())
    products = [build_product(r["raw"], cmap, r["row_number"]) for r in rows]
    dups = duplicate_issues(products)
    params = []
    for i, p in enumerate(products):
        issues = source_issues(p, mapped) + dups.get(i, [])
        params.append((org, import_id, rows[i]["row_number"], p["sku"] or None, p["gtin"] or None,
                       p["title"] or None, p["category_path"] or None, J(p), J(issues)))
    with service_tx() as conn:
        conn.execute("delete from canonical_products where import_id = %s", [import_id])
        with conn.cursor() as cur:
            cur.executemany("""insert into canonical_products (organization_id, import_id, row_number, sku, gtin, title,
                               category_path, data, source_issues) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", params)
        conn.execute("update imports set status = 'ready' where id = %s", [import_id])
        audit(conn, org, job["created_by"], "import.canonical_built", "import", import_id, {"products": len(products)})
    return {"products": len(products)}


# ------------------------------------------------------------------ szablony
def _contains_demo_marker(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return any(DEMO_MARKER.encode() in zf.read(n) for n in zf.namelist()
                       if n.endswith(".xml") and zf.getinfo(n).file_size < 20_000_000)
    except zipfile.BadZipFile:
        return False


def parse_template(job: dict) -> dict:
    org, template_id = job["organization_id"], job["payload"]["template_id"]
    with service_tx() as conn:
        tpl = one(conn, "select * from target_templates where id = %s and organization_id = %s", [template_id, org])
        if not tpl:
            raise UserError("Szablon nie istnieje.")
        conn.execute("update target_templates set status = 'parsing', error = null where id = %s", [template_id])
    options = dict(tpl["structure_options"])
    manual = options.pop("manual_requirements", {})
    path = _fetch(tpl["storage_path"], "." + tpl["format"])
    demo = tpl["is_demo_fixture"]
    try:
        if tpl["format"] == "xlsx":
            sp.check_xlsx_archive(path)
            demo = demo or _contains_demo_marker(path)
            try:
                structure, reqs = tp.parse_xlsx_template(path, options)
            except ValueError as exc:
                raise UserError(str(exc)) from exc
        else:
            with open(path, "rb") as fh:
                data = fh.read()
            text, encoding = sp.decode_csv(data, options.get("encoding"))
            demo = demo or DEMO_MARKER in text
            delims = sp.detect_delimiters(text)
            delimiter = options.get("delimiter") or (delims[0]["delimiter"] if delims else ";")
            try:
                structure, reqs = tp.parse_csv_template(text, delimiter, encoding == "utf-8-sig", options)
            except ValueError as exc:
                raise UserError(str(exc)) from exc
            structure["encoding"] = encoding
    finally:
        os.unlink(path)
    manual = {k: v for k, v in manual.items() if k in reqs}
    reqs = tp.merge_manual_requirements(reqs, manual)
    with service_tx() as conn:
        conn.execute("""update target_templates set status = 'needs_review', structure = %s, requirements = %s,
                        is_demo_fixture = %s, confirmed = false, confirmed_by = null, confirmed_at = null
                        where id = %s""", [J(structure), J(reqs), demo, template_id])
        audit(conn, org, job["created_by"], "template.parsed", "template", template_id,
              {"columns": len(structure["columns"]), "ambiguities": [a["code"] for a in structure["ambiguities"]]})
    return {"columns": len(structure["columns"])}


# ------------------------------------------------------------------- projekt
def load_env(conn, project: dict, template: dict) -> rules.Env:
    org, market = project["organization_id"], project["marketplace_id"]
    cats = {r["source_key"]: {"code": r["target_code"], "label": r["target_label"]}
            for r in many(conn, "select * from category_mappings where organization_id = %s and marketplace_id = %s",
                          [org, market])}
    vals: dict[tuple[str, str], dict[str, str]] = {}
    for r in many(conn, "select * from value_mappings where organization_id = %s and marketplace_id = %s",
                  [org, market]):
        vals.setdefault((r["target_column"], r["category_code"]), {})[r["source_key"]] = r["target_value"]
    structure = template["structure"]
    # Wymagania działają dopiero po zatwierdzeniu profilu przez użytkownika.
    reqs = template["requirements"] if template["confirmed"] else {}
    return rules.Env(marketplace=market, columns=[c["key"] for c in structure.get("columns", [])],
                     category_column=structure.get("category_column"), requirements=reqs,
                     category_maps=cats, value_maps=vals, rule_profile=template["rule_profile"] or {},
                     generated_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))


def load_rules(conn, project: dict) -> list[dict]:
    return [{"id": str(r["id"]), "target_column": r["target_column"], "steps": r["steps"]}
            for r in many(conn, "select * from mapping_rules where project_id = %s and version = %s order by position",
                          [project["id"], project["rules_version"]])]


def column_labels(structure: dict) -> dict[str, str]:
    return {c["key"]: (f"{c['label']} ({c['code']})" if c.get("label") else c["code"])
            for c in structure.get("columns", [])}


def evaluate_product(product: dict, rule_list: list[dict], env: rules.Env, labels: dict,
                     overrides: dict[str, str]) -> dict:
    res = rules.evaluate_row(product, rule_list, env)
    for col, value in overrides.items():
        if col not in res["values"]:
            continue
        res["values"][col] = value
        prev = res["provenance"].get(col, {})
        res["provenance"][col] = {**prev, "source_field": prev.get("source_field"), "rule_id": prev.get("rule_id"),
                                  "transformation": (prev.get("transformation") or "") + " > korekta użytkownika",
                                  "user_override": True, "generated_at": env.generated_at}
        # Decyzja użytkownika zamyka zgłoszenia reguł dla tej komórki; walidacja i tak ją sprawdzi.
        res["issues"] = [i for i in res["issues"] if i["target_column"] != col]
        if col == env.category_column:
            res["category"] = value
    # Pusta wartość, którą reguła już zgłosiła (np. brak przypisania kategorii), nie jest dublowana.
    flagged = {i["target_column"] for i in res["issues"]}
    res["issues"] += [i for i in validate.validate_values(res["values"], res["category"], env, labels)
                      if not (i["code"] == "REQUIRED_MISSING" and i["target_column"] in flagged)]
    return res


def run_project(job: dict) -> dict:
    org, project_id = job["organization_id"], job["payload"]["project_id"]
    with service_tx() as conn:
        project = one(conn, "select * from projects where id = %s and organization_id = %s", [project_id, org])
        if not project:
            raise UserError("Projekt nie istnieje.")
        template = one(conn, "select * from target_templates where id = %s and organization_id = %s",
                       [project["template_id"], org])
        imp = one(conn, "select * from imports where id = %s and organization_id = %s", [project["import_id"], org])
        if imp["status"] != "ready":
            raise UserError("Import nie ma jeszcze zatwierdzonego mapowania kolumn.")
        if template["status"] not in ("needs_review", "ready"):
            raise UserError("Szablon nie został poprawnie odczytany.")
        conn.execute("update projects set status = 'validating', error = null where id = %s", [project_id])
        env = load_env(conn, project, template)
        rule_list = load_rules(conn, project)
        overrides: dict[int, dict[str, str]] = {}
        for o in many(conn, """select distinct on (row_number, target_column) row_number, target_column, value
                               from value_overrides where project_id = %s and reverted_at is null
                               order by row_number, target_column, created_at desc""", [project_id]):
            overrides.setdefault(o["row_number"], {})[o["target_column"]] = o["value"]
        products = many(conn, "select row_number, data from canonical_products where import_id = %s order by row_number",
                        [imp["id"]])
    labels = column_labels(template["structure"])
    results = []
    total = len(products) or 1
    for i, p in enumerate(products):
        res = evaluate_product(p["data"], rule_list, env, labels, overrides.get(p["row_number"], {}))
        res.update(row_number=p["row_number"], sku=p["data"]["sku"], gtin=p["data"]["gtin"])
        results.append(res)
        if i % 500 == 499:
            set_progress(job["id"], int(80 * i / total))
    ds = validate.dataset_issues([{"row_number": r["row_number"], "sku": r["sku"], "gtin": r["gtin"],
                                   "skipped": r["skipped"]} for r in results])
    counters = {"imported": len(results), "ready": 0, "review": 0, "blocked": 0, "skipped": 0, "auto_assigned": 0}
    out_params, issue_params = [], []
    for r in results:
        issues = r["issues"] + ds.get(r["row_number"], [])
        status = "skipped" if r["skipped"] else validate.row_status(issues)
        counters[status] += 1
        cat_prov = r["provenance"].get(env.category_column or "", {})
        if r["category"] and not cat_prov.get("user_override") and not r["skipped"]:
            counters["auto_assigned"] += 1
        out_params.append((org, project_id, r["row_number"], r["sku"] or None, r["gtin"] or None,
                           r["category"] or None, status, r["skipped"], J(r["values"]), J(r["provenance"])))
        if r["skipped"]:
            continue
        for i in issues:
            issue_params.append((org, project_id, r["row_number"], r["sku"] or None, r["gtin"] or None,
                                 r["category"] or None, i["target_column"], i["severity"], i["code"], i["message"],
                                 i["raw_value"] or None, i["processed_value"] or None, J(i["suggestions"])))
    p_issues = validate.project_issues(template, bool(rule_list))
    blocked_project = any(i["severity"] == "BLOCKER" for i in p_issues)
    clean = counters["review"] == 0 and counters["blocked"] == 0 and not blocked_project and counters["ready"] > 0
    status = "ready" if clean else "needs_review"
    with service_tx() as conn:
        conn.execute("delete from validation_issues where project_id = %s", [project_id])
        conn.execute("delete from output_rows where project_id = %s", [project_id])
        with conn.cursor() as cur:
            cur.executemany("""insert into output_rows (organization_id, project_id, row_number, sku, gtin, category,
                               status, skipped, "values", provenance) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            out_params)
            cur.executemany("""insert into validation_issues (organization_id, project_id, row_number, sku, gtin,
                               category, target_column, severity, code, message, raw_value, processed_value,
                               suggestions) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", issue_params)
        conn.execute("""update projects set status = %s, counters = %s, project_issues = %s, last_run_version = %s
                        where id = %s""", [status, J(counters), J(p_issues), project["rules_version"], project_id])
        audit(conn, org, job["created_by"], "project.validated", "project", project_id, counters)
    return {"status": status, **counters}


# -------------------------------------------------------------------- eksport
def export_project(job: dict) -> dict:
    org, export_id = job["organization_id"], job["payload"]["export_id"]
    with service_tx() as conn:
        exp = one(conn, "select * from exports where id = %s and organization_id = %s", [export_id, org])
        if not exp:
            raise UserError("Eksport nie istnieje.")
        project = one(conn, "select * from projects where id = %s and organization_id = %s", [exp["project_id"], org])
        template = one(conn, "select * from target_templates where id = %s", [project["template_id"]])
        if project["last_run_version"] != project["rules_version"] or project["status"] not in ("ready", "needs_review",
                                                                                                "completed"):
            raise UserError("Przed eksportem uruchom walidację na aktualnych regułach.")
        prior_status = project["status"]
        conn.execute("update exports set status = 'exporting' where id = %s", [export_id])
        conn.execute("update projects set status = 'exporting' where id = %s", [project["id"]])
        where = "and status = 'ready'" if exp["scope"] == "ready_only" else ""
        rows = many(conn, f"""select "values" from output_rows where project_id = %s and not skipped {where}
                              order by row_number""", [project["id"]])
        issues = many(conn, """select * from validation_issues where project_id = %s
                               order by row_number nulls first, id""", [project["id"]])
    structure, reqs = template["structure"], (template["requirements"] if template["confirmed"] else {})
    counters = dict(project["counters"])
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d_%H%M")
    prefix = "DEMO-NIE-WYSYLAC_" if template["is_demo_fixture"] else ""
    scope_tag = "gotowe" if exp["scope"] == "ready_only" else "pelny"
    base = f"{prefix}{project['marketplace_id']}_{template['kind']}_{stamp}_{scope_tag}"
    tmp = tempfile.mkdtemp(prefix="mdb-exp-")
    tpl_path = _fetch(template["storage_path"], "." + template["format"])
    try:
        out_name = f"{base}.{exp['format']}"
        out_path = os.path.join(tmp, out_name)
        values = [r["values"] for r in rows]
        if exp["format"] == "xlsx":
            if template["format"] != "xlsx":
                raise UserError("Eksport XLSX wymaga szablonu XLSX. Wybierz eksport CSV.")
            stats = exporter.export_xlsx(tpl_path, structure, reqs, values, out_path)
        else:
            text = None
            if template["format"] == "csv":
                with open(tpl_path, "rb") as fh:
                    text, _ = sp.decode_csv(fh.read(), structure.get("encoding"))
            head = exporter.template_head_rows(tpl_path, text, structure)
            stats = exporter.export_csv(head, structure, values, out_path)
        counters["exported"] = stats["rows_written"]
        reasons = []
        if template["is_demo_fixture"]:
            reasons.append("Szablon demonstracyjny — plik nie jest przeznaczony do wysyłki.")
        if not template["confirmed"]:
            reasons.append("Wymagania szablonu nie zostały zatwierdzone.")
        if exp["format"] == "xlsx":
            reasons += structure.get("lossless_warnings", [])
        if exp["format"] != template["format"]:
            reasons.append(f"Format eksportu ({exp['format'].upper()}) różni się od formatu szablonu "
                           f"({template['format'].upper()}).")
        problem_rows = counters.get("review", 0) + counters.get("blocked", 0)
        if exp["scope"] == "all" and problem_rows:
            reasons.append(f"Plik zawiera {problem_rows} wierszy z nierozwiązanymi problemami.")
        if exp["format"] == "csv" and stats["formula_like_cells"]:
            reasons.append(f"{stats['formula_like_cells']} komórek zaczynało się od znaku formuły — dodano apostrof "
                           "ochronny. Sprawdź te wartości w danych źródłowych.")
        if any(i["severity"] == "BLOCKER" for i in project["project_issues"]):
            reasons.append("Projekt ma blokujące zgłoszenia na poziomie konfiguracji.")
        compatibility = {"declared_compatible": not reasons, "reasons": reasons,
                         "formula_like_cells": stats["formula_like_cells"], "template_sha256": template["sha256"],
                         "note": "Zgodność dotyczy struktury dostarczonego szablonu. Marketplace może stosować "
                                 "dodatkową weryfikację katalogową."}
        issue_rows = [dict(i) for i in issues] + [{**i, "row_number": None} for i in project["project_issues"]]
        rep_x, rep_c = f"{base}_raport.xlsx", f"{base}_raport.csv"
        exporter.write_report_xlsx(issue_rows, counters, reasons, os.path.join(tmp, rep_x))
        exporter.write_report_csv(issue_rows, os.path.join(tmp, rep_c))
        keys = {name: object_key(org, "exports", export_id, name) for name in (out_name, rep_x, rep_c)}
        for name, key in keys.items():
            storage().put(key, os.path.join(tmp, name))
    finally:
        os.unlink(tpl_path)
        for f in os.listdir(tmp):
            os.unlink(os.path.join(tmp, f))
        os.rmdir(tmp)
    final = "completed" if prior_status in ("ready", "completed") and exp["scope"] == "ready_only" else prior_status
    with service_tx() as conn:
        conn.execute("""update exports set status = 'completed', filename = %s, storage_path = %s,
                        report_filename = %s, report_path = %s, report_csv_filename = %s, report_csv_path = %s,
                        counters = %s, compatibility = %s,
                        expires_at = now() + make_interval(hours => %s) where id = %s""",
                     [out_name, keys[out_name], rep_x, keys[rep_x], rep_c, keys[rep_c], J(counters), J(compatibility),
                      settings.export_ttl_hours, export_id])
        conn.execute("update projects set status = %s, counters = %s where id = %s",
                     [final, J(counters), project["id"]])
        audit(conn, org, job["created_by"], "export.created", "export", export_id,
              {"rows": stats["rows_written"], "format": exp["format"], "scope": exp["scope"]})
    return {"rows": stats["rows_written"], "compatible": not reasons}


def cleanup_expired() -> int:
    """Usuwa pliki eksportów po upływie czasu życia."""
    with service_tx() as conn:
        expired = many(conn, """select id, storage_path, report_path, report_csv_path from exports
                                where expires_at < now() and deleted_at is null and status = 'completed'""")
        for e in expired:
            for key in (e["storage_path"], e["report_path"], e["report_csv_path"]):
                if key:
                    storage().delete(key)
            conn.execute("update exports set deleted_at = now(), status = 'expired' where id = %s", [e["id"]])
    return len(expired)


def mark_failed(job: dict, message: str) -> None:
    table = {"parse_import": ("imports", "import_id"), "build_canonical": ("imports", "import_id"),
             "parse_template": ("target_templates", "template_id"), "run_project": ("projects", "project_id"),
             "export_project": ("exports", "export_id")}.get(job["kind"])
    if not table:
        return
    with service_tx() as conn:
        conn.execute(f"update {table[0]} set status = 'failed', error = %s where id = %s and organization_id = %s",
                     [message, job["payload"][table[1]], job["organization_id"]])
        if job["kind"] == "export_project":
            conn.execute("""update projects set status = 'needs_review' where status = 'exporting' and id =
                            (select project_id from exports where id = %s)""", [job["payload"]["export_id"]])


HANDLERS = {"parse_import": parse_import, "build_canonical": build_canonical, "parse_template": parse_template,
            "run_project": run_project, "export_project": export_project}

__all__ = ["HANDLERS", "audit", "cleanup_expired", "column_labels", "evaluate_product", "fold", "load_env",
           "load_rules", "mark_failed", "sha256_file"]
