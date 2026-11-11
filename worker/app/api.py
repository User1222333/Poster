"""HTTP API (FastAPI). Każdy endpoint działa w kontekście organizacji i pod RLS."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import jobs
from .auth import Ctx, current_ctx, hash_password, issue_local_token, rate_limit, verify_password
from .config import settings
from .db import J, close_pool, many, one, service_tx, user_tx
from .engine import rules as rules_engine
from .engine import source_parser as sp
from .engine import template_parser as tp
from .engine.aliases import CANONICAL_FIELDS, FIELD_LABELS, validate_column_map
from .engine.normalize import UNITS, fold, single_line
from .engine.template_parser import safe_filename
from .pipelines import audit, cleanup_expired, column_labels, evaluate_product, load_env
from .storage import object_key, storage

CSV_MIME = {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel", "application/octet-stream", ""}
XLSX_MIME = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream", ""}
MARKETS = ("empik", "media_expert", "castorama")


def _worker_loop(stop: threading.Event) -> None:
    last_cleanup = 0.0
    while not stop.is_set():
        try:
            if not jobs.run_one():
                stop.wait(0.4)
            if time.monotonic() - last_cleanup > 600:
                cleanup_expired()
                last_cleanup = time.monotonic()
        except Exception:  # noqa: BLE001
            jobs.log.exception("worker loop error")
            stop.wait(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate()
    stop = threading.Event()
    if settings.inline_worker:
        threading.Thread(target=_worker_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()
    close_pool()


app = FastAPI(title="Marketplace Data Bridge API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                   allow_headers=["Authorization", "Content-Type", "X-Organization-Id", "Idempotency-Key"],
                   expose_headers=["Content-Disposition"])


@app.exception_handler(sp.SourceError)
async def source_error_handler(_: Request, exc: sp.SourceError):
    return JSONResponse(status_code=422, content={"detail": exc.message, "code": exc.code, "details": exc.details})


def owned(conn, table: str, object_id: str, ctx: Ctx, what: str) -> dict:
    try:
        uuid.UUID(str(object_id))
    except ValueError as exc:
        raise HTTPException(404, f"{what}: nie znaleziono.") from exc
    row = one(conn, f"select * from {table} where id = %s and organization_id = %s", [object_id, ctx.org_id])
    if not row:
        raise HTTPException(404, f"{what}: nie znaleziono.")
    return row


# ------------------------------------------------------------------ pliki
def receive_upload(file: UploadFile) -> tuple[str, str, int, str]:
    """Zapis do pliku tymczasowego z limitem rozmiaru + kontrola MIME i sygnatury przed przetwarzaniem."""
    name = os.path.basename(file.filename or "plik")
    limit = settings.max_upload_mb * 1024 * 1024
    fd, path = tempfile.mkstemp(prefix="mdb-up-")
    h, size, head = hashlib.sha256(), 0, b""
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Plik przekracza limit {settings.max_upload_mb} MB.")
                if not head:
                    head = chunk[:4096]
                h.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "Plik jest pusty.")
        fmt = sp.sniff_format(head, name)
        mime = (file.content_type or "").split(";")[0].strip().lower()
        if mime not in (XLSX_MIME if fmt == "xlsx" else CSV_MIME):
            raise HTTPException(422, f"Typ pliku „{mime}” nie pasuje do formatu {fmt.upper()}.")
        if fmt == "xlsx":
            sp.check_xlsx_archive(path)
        return path, h.hexdigest(), size, fmt
    except Exception:
        os.unlink(path)
        raise


def fixture_copy(name: str) -> tuple[str, str, int]:
    src = os.path.join(settings.fixtures_dir, name)
    if not os.path.exists(src):
        raise HTTPException(404, "Plik demonstracyjny nie jest dostępny na tym serwerze.")
    fd, path = tempfile.mkstemp(prefix="mdb-demo-")
    os.close(fd)
    shutil.copyfile(src, path)
    with open(path, "rb") as fh:
        data = fh.read()
    return path, hashlib.sha256(data).hexdigest(), len(data)


def create_import(ctx: Ctx, path: str, sha: str, size: int, fmt: str, filename: str, profile_id: str | None,
                  idem: str | None) -> dict:
    import_id = str(uuid.uuid4())
    key = object_key(ctx.org_id, "sources", import_id, safe_filename(filename))
    with user_tx(ctx.user_id) as conn:
        if idem:
            prev = one(conn, "select * from processing_jobs where organization_id = %s and idempotency_key = %s",
                       [ctx.org_id, idem])
            if prev:
                return owned(conn, "imports", prev["payload"]["import_id"], ctx, "Import")
        opts = {}
        if profile_id:
            prof = owned(conn, "source_mapping_profiles", profile_id, ctx, "Profil importu")
            opts = prof["parse_options"]
        storage().put(key, path)
        imp = one(conn, """insert into imports (id, organization_id, filename, storage_path, sha256, format, size_bytes,
                           parse_options, created_by) values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
                  [import_id, ctx.org_id, filename, key, sha, fmt, size, J(opts), ctx.user_id])
        jobs.enqueue(conn, ctx.org_id, "parse_import", {"import_id": import_id, "profile_id": profile_id},
                     ctx.user_id, idem)
        audit(conn, ctx.org_id, ctx.user_id, "import.uploaded", "import", import_id, {"filename": filename, "sha256": sha})
    return imp


def create_template(ctx: Ctx, path: str, sha: str, fmt: str, filename: str, marketplace_id: str, name: str,
                    demo: bool) -> dict:
    template_id = str(uuid.uuid4())
    key = object_key(ctx.org_id, "templates", template_id, safe_filename(filename))
    name = single_line(name)[:120] or filename
    with user_tx(ctx.user_id) as conn:
        if not one(conn, "select 1 from marketplace_targets where id = %s", [marketplace_id]):
            raise HTTPException(422, "Nieznany marketplace.")
        version = one(conn, """select coalesce(max(version), 0) + 1 as v from target_templates
                               where organization_id = %s and marketplace_id = %s and name = %s""",
                      [ctx.org_id, marketplace_id, name])["v"]
        storage().put(key, path)
        tpl = one(conn, """insert into target_templates (id, organization_id, marketplace_id, name, version, filename,
                           storage_path, sha256, format, is_demo_fixture) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           returning *""",
                  [template_id, ctx.org_id, marketplace_id, name, version, filename, key, sha, fmt, demo])
        jobs.enqueue(conn, ctx.org_id, "parse_template", {"template_id": template_id}, ctx.user_id)
        audit(conn, ctx.org_id, ctx.user_id, "template.uploaded", "template", template_id,
              {"filename": filename, "sha256": sha, "marketplace": marketplace_id, "version": version, "demo": demo})
    return tpl


# ----------------------------------------------------------------- publiczne
@app.get("/health")
def health():
    with service_tx() as conn:
        conn.execute("select 1")
    return {"ok": True}


@app.get("/config/public")
def public_config():
    return {"auth_mode": settings.auth_mode}


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=200)


def _local_only(request: Request) -> None:
    if settings.auth_mode != "local":
        raise HTTPException(404, "Logowanie lokalne jest wyłączone. Użyj Supabase Auth.")
    rate_limit(f"ip:{request.client.host if request.client else '?'}", 20)


@app.post("/auth/local/register")
def local_register(body: Credentials, request: Request):
    _local_only(request)
    email = body.email.strip().lower()
    with service_tx() as conn:
        if one(conn, "select 1 from auth.users where email = %s", [email]):
            raise HTTPException(409, "Konto z tym adresem już istnieje.")
        user = one(conn, "insert into auth.users (email, encrypted_password) values (%s, %s) returning id",
                   [email, hash_password(body.password)])
    return {"access_token": issue_local_token(user["id"], email), "email": email}


@app.post("/auth/local/login")
def local_login(body: Credentials, request: Request):
    _local_only(request)
    email = body.email.strip().lower()
    with service_tx() as conn:
        user = one(conn, "select id, encrypted_password from auth.users where email = %s", [email])
    if not user or not verify_password(body.password, user["encrypted_password"] or ""):
        raise HTTPException(401, "Nieprawidłowy e-mail lub hasło.")
    return {"access_token": issue_local_token(user["id"], email), "email": email}


# ------------------------------------------------------------ konto i panel
@app.get("/me")
def me(ctx: Ctx = Depends(current_ctx)):
    return {"user_id": ctx.user_id, "email": ctx.email, "role": ctx.role,
            "organization": {"id": ctx.org_id, "name": ctx.org_name},
            "limits": {"max_upload_mb": settings.max_upload_mb, "max_rows": settings.max_rows,
                       "export_ttl_hours": settings.export_ttl_hours}}


@app.get("/meta")
def meta(ctx: Ctx = Depends(current_ctx)):
    return {"canonical_fields": [{"id": f, "label": FIELD_LABELS[f]} for f in CANONICAL_FIELDS],
            "step_types": rules_engine.STEP_TYPES, "ops": rules_engine.OPS,
            "patterns": [{"id": k, "label": v[1]} for k, v in rules_engine.SAFE_PATTERNS.items()],
            "units": list(UNITS), "requirement_types": tp.REQ_TYPES, "row_target": rules_engine.ROW_TARGET}


@app.get("/dashboard")
def dashboard(ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        o = [ctx.org_id]
        return {
            "imports_by_status": many(conn, "select status, count(*)::int as count from imports "
                                            "where organization_id = %s group by status", o),
            "projects_by_status": many(conn, "select status, count(*)::int as count from projects "
                                             "where organization_id = %s group by status", o),
            "recent_imports": many(conn, """select id, filename, status, row_count, created_at from imports
                                            where organization_id = %s order by created_at desc limit 5""", o),
            "recent_projects": many(conn, """select id, name, marketplace_id, status, counters, created_at from projects
                                             where organization_id = %s order by created_at desc limit 5""", o),
            "recent_exports": many(conn, """select e.id, e.filename, e.status, e.format, e.scope, e.created_at,
                                            e.expires_at, e.counters, p.name as project_name from exports e
                                            join projects p on p.id = e.project_id
                                            where e.organization_id = %s order by e.created_at desc limit 5""", o),
            "imports_last_30d": one(conn, """select count(*)::int as n from imports where organization_id = %s
                                             and created_at > now() - interval '30 days'""", o)["n"],
        }


@app.get("/marketplaces")
def marketplaces(ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, """select t.*, (select count(*)::int from target_templates x where x.marketplace_id = t.id
                             and x.organization_id = %s) as templates,
                             (select count(*)::int from projects p where p.marketplace_id = t.id
                             and p.organization_id = %s) as projects
                             from marketplace_targets t order by t.name""", [ctx.org_id, ctx.org_id])


@app.get("/jobs/{job_id}")
def get_job(job_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        j = owned(conn, "processing_jobs", job_id, ctx, "Zadanie")
    return {k: j[k] for k in ("id", "kind", "status", "progress", "attempts", "error", "result", "created_at")}


@app.get("/audit")
def audit_log(limit: int = Query(50, le=200), ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, "select * from audit_events where organization_id = %s order by id desc limit %s",
                    [ctx.org_id, limit])


class MemberIn(BaseModel):
    email: str
    role: Literal["owner", "member"] = "member"


@app.get("/org/members")
def members(ctx: Ctx = Depends(current_ctx)):
    with service_tx() as conn:
        return many(conn, """select m.user_id, m.role, m.created_at, u.email from organization_members m
                             join auth.users u on u.id = m.user_id where m.organization_id = %s order by m.created_at""",
                    [ctx.org_id])


@app.post("/org/members")
def add_member(body: MemberIn, ctx: Ctx = Depends(current_ctx)):
    if ctx.role != "owner":
        raise HTTPException(403, "Tylko właściciel organizacji może zarządzać członkami.")
    with service_tx() as conn:
        user = one(conn, "select id from auth.users where lower(email) = %s", [body.email.strip().lower()])
        if not user:
            raise HTTPException(404, "Nie znaleziono konta z tym adresem. Użytkownik musi się najpierw zarejestrować.")
        conn.execute("""insert into organization_members (organization_id, user_id, role) values (%s, %s, %s)
                        on conflict (organization_id, user_id) do update set role = excluded.role""",
                     [ctx.org_id, user["id"], body.role])
        audit(conn, ctx.org_id, ctx.user_id, "member.added", "user", user["id"], {"role": body.role})
    return {"ok": True}


@app.delete("/org/members/{user_id}")
def remove_member(user_id: str, ctx: Ctx = Depends(current_ctx)):
    if ctx.role != "owner":
        raise HTTPException(403, "Tylko właściciel organizacji może zarządzać członkami.")
    if user_id == ctx.user_id:
        raise HTTPException(422, "Nie możesz usunąć samego siebie.")
    with service_tx() as conn:
        conn.execute("delete from organization_members where organization_id = %s and user_id = %s",
                     [ctx.org_id, user_id])
        audit(conn, ctx.org_id, ctx.user_id, "member.removed", "user", user_id)
    return {"ok": True}


# -------------------------------------------------------------------- importy
@app.post("/imports")
def upload_import(file: UploadFile = File(...), profile_id: str | None = Form(default=None),
                  idempotency_key: str | None = Header(default=None), ctx: Ctx = Depends(current_ctx)):
    rate_limit(f"up:{ctx.user_id}", settings.upload_limit_per_minute)
    path, sha, size, fmt = receive_upload(file)
    try:
        return create_import(ctx, path, sha, size, fmt, os.path.basename(file.filename or "plik"), profile_id or None,
                             idempotency_key)
    finally:
        os.unlink(path)


@app.post("/imports/demo")
def demo_import(ctx: Ctx = Depends(current_ctx)):
    path, sha, size = fixture_copy("demo_zrodlo_tekstylia.csv")
    try:
        return create_import(ctx, path, sha, size, "csv", "demo_zrodlo_tekstylia.csv", None, None)
    finally:
        os.unlink(path)


@app.get("/imports")
def list_imports(ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, """select id, filename, format, status, row_count, size_bytes, error, column_map_confirmed,
                             created_at from imports where organization_id = %s order by created_at desc limit 200""",
                    [ctx.org_id])


@app.get("/imports/{import_id}")
def get_import(import_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        imp = owned(conn, "imports", import_id, ctx, "Import")
        imp.pop("storage_path")
        return imp


@app.get("/imports/{import_id}/rows")
def import_rows(import_id: str, limit: int = Query(100, ge=1, le=100), ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        owned(conn, "imports", import_id, ctx, "Import")
        return many(conn, """select row_number, raw from import_rows where import_id = %s and organization_id = %s
                             order by row_number limit %s""", [import_id, ctx.org_id, limit])


class ParseOptions(BaseModel):
    sheet: str | None = None
    header_row: int | None = Field(default=None, ge=1, le=200)
    delimiter: Literal[";", ",", "\t", "|"] | None = None
    encoding: Literal["utf-8", "cp1250", "iso-8859-2"] | None = None


@app.put("/imports/{import_id}/parse-options")
def set_parse_options(import_id: str, body: ParseOptions, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        imp = owned(conn, "imports", import_id, ctx, "Import")
        opts = {**imp["parse_options"], **body.model_dump(exclude_none=True)}
        conn.execute("update imports set parse_options = %s, status = 'queued' where id = %s", [J(opts), import_id])
        jobs.enqueue_unique(conn, ctx.org_id, "parse_import", {"import_id": import_id}, ctx.user_id, "import_id")
    return {"ok": True}


class ColumnMapIn(BaseModel):
    column_map: dict[str, str]
    save_profile_name: str | None = Field(default=None, max_length=100)


@app.put("/imports/{import_id}/column-map")
def set_column_map(import_id: str, body: ColumnMapIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        imp = owned(conn, "imports", import_id, ctx, "Import")
        if not imp["columns"]:
            raise HTTPException(409, "Plik nie został jeszcze odczytany.")
        errors = validate_column_map(body.column_map, imp["columns"])
        if errors:
            raise HTTPException(422, " ".join(errors))
        conn.execute("update imports set column_map = %s, column_map_confirmed = true, status = 'queued' where id = %s",
                     [J(body.column_map), import_id])
        if body.save_profile_name and body.save_profile_name.strip():
            conn.execute("""insert into source_mapping_profiles (organization_id, name, column_map, parse_options)
                            values (%s, %s, %s, %s) on conflict (organization_id, name) do update
                            set column_map = excluded.column_map, parse_options = excluded.parse_options""",
                         [ctx.org_id, body.save_profile_name.strip(), J(body.column_map),
                          J({k: v for k, v in imp["parse_options"].items() if k != "sheet"})])
        # Zmiana mapowania kolumn unieważnia wyniki projektów korzystających z importu.
        conn.execute("update projects set last_run_version = null where import_id = %s and organization_id = %s",
                     [import_id, ctx.org_id])
        jobs.enqueue_unique(conn, ctx.org_id, "build_canonical", {"import_id": import_id}, ctx.user_id, "import_id")
        audit(conn, ctx.org_id, ctx.user_id, "import.column_map_confirmed", "import", import_id,
              {"profile": body.save_profile_name})
    return {"ok": True}


@app.get("/imports/{import_id}/issues")
def import_issues(import_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        owned(conn, "imports", import_id, ctx, "Import")
        rows = many(conn, """select row_number, sku, gtin, title, source_issues from canonical_products
                             where import_id = %s and organization_id = %s and source_issues <> '[]'::jsonb
                             order by row_number""", [import_id, ctx.org_id])
    summary: dict[str, int] = {}
    for r in rows:
        for i in r["source_issues"]:
            summary[i["code"]] = summary.get(i["code"], 0) + 1
    return {"summary": [{"code": c, "count": n} for c, n in sorted(summary.items())], "rows": rows[:500]}


@app.get("/profiles")
def list_profiles(ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, "select * from source_mapping_profiles where organization_id = %s order by name", [ctx.org_id])


@app.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        owned(conn, "source_mapping_profiles", profile_id, ctx, "Profil importu")
        conn.execute("delete from source_mapping_profiles where id = %s", [profile_id])
    return {"ok": True}


@app.get("/products")
def list_products(import_id: str | None = None, q: str = "", limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0), ctx: Ctx = Depends(current_ctx)):
    where, params = ["p.organization_id = %s"], [ctx.org_id]
    if import_id:
        where.append("p.import_id = %s")
        params.append(import_id)
    if q.strip():
        where.append("(p.sku ilike %s or p.gtin ilike %s or p.title ilike %s)")
        params += [f"%{q.strip()}%"] * 3
    w = " and ".join(where)
    with user_tx(ctx.user_id) as conn:
        total = one(conn, f"select count(*)::int as n from canonical_products p where {w}", params)["n"]
        rows = many(conn, f"""select p.id, p.import_id, p.row_number, p.sku, p.gtin, p.title, p.category_path, p.data,
                              p.source_issues, i.filename from canonical_products p join imports i on i.id = p.import_id
                              where {w} order by i.created_at desc, p.row_number limit %s offset %s""",
                    [*params, limit, offset])
    return {"total": total, "rows": rows}


# ------------------------------------------------------------------- szablony
def template_public(t: dict) -> dict:
    t = dict(t)
    t.pop("storage_path", None)
    reqs = t.get("requirements") or {}
    t["unknown_required"] = sum(1 for r in reqs.values() if r.get("required") is None)
    return t


@app.post("/templates")
def upload_template(file: UploadFile = File(...), marketplace_id: str = Form(...), name: str = Form(""),
                    is_demo: bool = Form(False), ctx: Ctx = Depends(current_ctx)):
    rate_limit(f"up:{ctx.user_id}", settings.upload_limit_per_minute)
    path, sha, _, fmt = receive_upload(file)
    try:
        filename = os.path.basename(file.filename or "szablon")
        return template_public(create_template(ctx, path, sha, fmt, filename, marketplace_id, name or filename, is_demo))
    finally:
        os.unlink(path)


class DemoTemplateIn(BaseModel):
    marketplace_id: str


@app.post("/templates/demo")
def demo_template(body: DemoTemplateIn, ctx: Ctx = Depends(current_ctx)):
    path, sha, _ = fixture_copy("demo_szablon_FIKCYJNY.xlsx")
    try:
        return template_public(create_template(ctx, path, sha, "xlsx", "demo_szablon_FIKCYJNY.xlsx", body.marketplace_id,
                                               "DEMO — fikcyjny szablon testowy", True))
    finally:
        os.unlink(path)


@app.get("/templates")
def list_templates(marketplace_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        rows = many(conn, """select id, marketplace_id, name, version, filename, sha256, format, status, kind,
                             is_demo_fixture, confirmed, confirmed_at, error, created_at,
                             jsonb_array_length(coalesce(structure -> 'columns', '[]'::jsonb)) as column_count
                             from target_templates where organization_id = %s
                             and (%s::text is null or marketplace_id = %s) order by created_at desc""",
                    [ctx.org_id, marketplace_id, marketplace_id])
    return rows


@app.get("/templates/{template_id}")
def get_template(template_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return template_public(owned(conn, "target_templates", template_id, ctx, "Szablon"))


class StructureIn(BaseModel):
    data_sheet: str | None = None
    header_row: int | None = Field(default=None, ge=1, le=500)
    label_row: int | None = Field(default=None, ge=0, le=500)
    data_start_row: int | None = Field(default=None, ge=2, le=1000)
    category_column: str | None = None  # "" = szablon nie ma kolumny kategorii
    dictionary_sheet: str | None = None
    dictionary_layout: Literal["wide", "long"] | None = None
    dictionary_header_row: int | None = Field(default=None, ge=1, le=500)
    dictionary_roles: dict[str, int] | None = None
    attribute_sheet: str | None = None
    attribute_header_row: int | None = Field(default=None, ge=1, le=500)
    attribute_roles: dict[str, int] | None = None
    delimiter: Literal[";", ",", "\t", "|"] | None = None


def _invalidate_template(conn, template_id: str, ctx: Ctx) -> None:
    conn.execute("""update target_templates set confirmed = false, confirmed_by = null, confirmed_at = null
                    where id = %s""", [template_id])
    conn.execute("update projects set last_run_version = null where template_id = %s and organization_id = %s",
                 [template_id, ctx.org_id])


@app.put("/templates/{template_id}/structure")
def set_structure(template_id: str, body: StructureIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        tpl = owned(conn, "target_templates", template_id, ctx, "Szablon")
        opts = body.model_dump(exclude_unset=True)
        if opts.get("data_start_row") and opts.get("header_row") and opts["data_start_row"] <= opts["header_row"]:
            raise HTTPException(422, "Pierwszy wiersz danych musi znajdować się pod wierszem nagłówka.")
        merged = {**tpl["structure_options"], **opts}
        conn.execute("update target_templates set structure_options = %s, status = 'queued' where id = %s",
                     [J(merged), template_id])
        _invalidate_template(conn, template_id, ctx)
        jobs.enqueue_unique(conn, ctx.org_id, "parse_template", {"template_id": template_id}, ctx.user_id, "template_id")
        audit(conn, ctx.org_id, ctx.user_id, "template.structure_set", "template", template_id, opts)
    return {"ok": True}


class RuleProfile(BaseModel):
    banned_phrases: list[str] = []
    allowed_html_tags: list[str] = []
    image_extensions: list[str] = []


class RequirementsIn(BaseModel):
    requirements: dict[str, dict] = {}
    kind: Literal["products", "offers", "mixed", "unknown"] | None = None
    rule_profile: RuleProfile | None = None


@app.put("/templates/{template_id}/requirements")
def set_requirements(template_id: str, body: RequirementsIn, ctx: Ctx = Depends(current_ctx)):
    allowed = {"required", "type", "enum", "max_length", "min", "max", "categories"}
    with user_tx(ctx.user_id) as conn:
        tpl = owned(conn, "target_templates", template_id, ctx, "Szablon")
        if tpl["status"] not in ("needs_review", "ready"):
            raise HTTPException(409, "Szablon nie został jeszcze odczytany.")
        patch = {}
        for key, req in body.requirements.items():
            if key not in tpl["requirements"]:
                raise HTTPException(422, f"Kolumna „{key}” nie istnieje w szablonie.")
            patch[key] = {k: v for k, v in req.items() if k in allowed}
        errors = tp.validate_requirement_patch(patch)
        if errors:
            raise HTTPException(422, " ".join(errors))
        opts = dict(tpl["structure_options"])
        manual = {**opts.get("manual_requirements", {})}
        for key, req in patch.items():
            manual[key] = {**manual.get(key, {}), **req}
        opts["manual_requirements"] = manual
        reqs = tp.merge_manual_requirements(tpl["requirements"], patch)
        conn.execute("""update target_templates set requirements = %s, structure_options = %s,
                        kind = coalesce(%s, kind), rule_profile = coalesce(%s, rule_profile), status = 'needs_review'
                        where id = %s""",
                     [J(reqs), J(opts), body.kind, J(body.rule_profile.model_dump()) if body.rule_profile else None,
                      template_id])
        _invalidate_template(conn, template_id, ctx)
        audit(conn, ctx.org_id, ctx.user_id, "template.requirements_edited", "template", template_id,
              {"columns": sorted(patch), "kind": body.kind})
    return {"ok": True}


class ConfirmIn(BaseModel):
    acknowledge_unknown: bool = False


@app.post("/templates/{template_id}/confirm")
def confirm_template(template_id: str, body: ConfirmIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        tpl = owned(conn, "target_templates", template_id, ctx, "Szablon")
        if tpl["status"] not in ("needs_review", "ready"):
            raise HTTPException(409, "Szablon nie został jeszcze odczytany.")
        amb = tpl["structure"].get("ambiguities", [])
        if amb:
            raise HTTPException(422, "Najpierw rozstrzygnij niejednoznaczności struktury: " +
                                " ".join(a["message"] for a in amb))
        if tpl["kind"] == "unknown":
            raise HTTPException(422, "Określ, czy szablon dotyczy produktów, ofert, czy jest mieszany.")
        unknown = [k for k, r in tpl["requirements"].items() if r.get("required") is None]
        if unknown and not body.acknowledge_unknown:
            raise HTTPException(422, f"{len(unknown)} kolumn ma nieustaloną wymagalność. Zdefiniuj ją ręcznie albo "
                                "jawnie potwierdź, że mają być traktowane jako niewymagane.")
        conn.execute("""update target_templates set confirmed = true, confirmed_by = %s, confirmed_at = now(),
                        status = 'ready' where id = %s""", [ctx.user_id, template_id])
        conn.execute("update projects set last_run_version = null where template_id = %s and organization_id = %s",
                     [template_id, ctx.org_id])
        audit(conn, ctx.org_id, ctx.user_id, "template.confirmed", "template", template_id,
              {"unknown_required_acknowledged": len(unknown)})
    return {"ok": True, "unknown_required": len(unknown)}


# ------------------------------------------------------------------- projekty
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    import_id: str
    template_id: str


def _project_bundle(conn, project_id: str, ctx: Ctx) -> tuple[dict, dict, dict]:
    project = owned(conn, "projects", project_id, ctx, "Projekt")
    template = owned(conn, "target_templates", project["template_id"], ctx, "Szablon")
    imp = owned(conn, "imports", project["import_id"], ctx, "Import")
    return project, template, imp


@app.post("/projects")
def create_project(body: ProjectIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        imp = owned(conn, "imports", body.import_id, ctx, "Import")
        tpl = owned(conn, "target_templates", body.template_id, ctx, "Szablon")
        p = one(conn, """insert into projects (organization_id, name, import_id, template_id, marketplace_id, created_by)
                         values (%s,%s,%s,%s,%s,%s) returning *""",
                [ctx.org_id, body.name.strip(), imp["id"], tpl["id"], tpl["marketplace_id"], ctx.user_id])
        audit(conn, ctx.org_id, ctx.user_id, "project.created", "project", p["id"],
              {"import": imp["filename"], "template": tpl["name"], "template_version": tpl["version"]})
    return p


@app.get("/projects")
def list_projects(ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, """select p.id, p.name, p.marketplace_id, p.status, p.counters, p.created_at, p.error,
                             i.filename as import_filename, t.name as template_name, t.version as template_version,
                             t.is_demo_fixture from projects p join imports i on i.id = p.import_id
                             join target_templates t on t.id = p.template_id
                             where p.organization_id = %s order by p.created_at desc limit 200""", [ctx.org_id])


@app.get("/projects/{project_id}")
def get_project(project_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project, template, imp = _project_bundle(conn, project_id, ctx)
        groups = many(conn, """select severity, code, target_column, count(distinct row_number)::int as rows,
                               min(message) as sample from validation_issues where project_id = %s
                               group by severity, code, target_column order by severity, rows desc""", [project_id])
    c = project["counters"] or {}
    active = max(c.get("imported", 0) - c.get("skipped", 0), 0)
    stale = project["last_run_version"] != project["rules_version"] or project["last_run_version"] is None
    labels = column_labels(template["structure"])
    blockers = [i["message"] for i in project["project_issues"] if i["severity"] == "BLOCKER"]
    if imp["status"] != "ready":
        blockers.append("Mapowanie kolumn pliku źródłowego nie zostało zatwierdzone.")
    if project["rules_version"] == 0:
        blockers.append("Nie zapisano jeszcze reguł mapowania.")
    elif stale:
        blockers.append("Reguły, szablon lub dane zmieniły się od ostatniej walidacji — uruchom ją ponownie.")
    for g in groups:
        if g["severity"] in ("BLOCKER", "REVIEW"):
            label = labels.get(g["target_column"], g["target_column"]) if g["target_column"] else ""
            col = f" [{label}]" if label and label not in g["sample"] else ""
            blockers.append(f"{g['sample']}{col} — {g['rows']} prod.")
    return {**project, "stale": stale,
            "readiness_percent": round(100 * c.get("ready", 0) / active) if active and not stale else 0,
            "blockers": blockers[:30], "issue_groups": groups,
            "import": {k: imp[k] for k in ("id", "filename", "status", "row_count", "column_map_confirmed", "error")},
            "template": template_public({k: template[k] for k in (
                "id", "name", "version", "marketplace_id", "status", "kind", "confirmed", "is_demo_fixture", "format",
                "sha256", "requirements", "error")}) | {
                "columns": template["structure"].get("columns", []),
                "category_column": template["structure"].get("category_column"),
                "ambiguities": template["structure"].get("ambiguities", []),
                "lossless_warnings": template["structure"].get("lossless_warnings", [])}}


class RuleIn(BaseModel):
    target_column: str
    steps: list[dict]


class RulesIn(BaseModel):
    rules: list[RuleIn]


def _check_rules(rule_list: list[dict], template: dict) -> None:
    columns = [c["key"] for c in template["structure"].get("columns", [])]
    errors = rules_engine.validate_rules(rule_list, columns)
    if errors:
        raise HTTPException(422, " ".join(errors[:10]))


@app.get("/projects/{project_id}/rules")
def get_rules(project_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project = owned(conn, "projects", project_id, ctx, "Projekt")
        rows = many(conn, """select id, target_column, steps from mapping_rules where project_id = %s and version = %s
                             order by position""", [project_id, project["rules_version"]])
    return {"version": project["rules_version"], "rules": rows}


@app.put("/projects/{project_id}/rules")
def put_rules(project_id: str, body: RulesIn, ctx: Ctx = Depends(current_ctx)):
    rule_list = [r.model_dump() for r in body.rules if r.steps]
    with user_tx(ctx.user_id) as conn:
        project, template, _ = _project_bundle(conn, project_id, ctx)
        _check_rules(rule_list, template)
        version = project["rules_version"] + 1
        with conn.cursor() as cur:
            cur.executemany("""insert into mapping_rules (organization_id, project_id, version, position, target_column,
                               steps) values (%s,%s,%s,%s,%s,%s)""",
                            [(ctx.org_id, project_id, version, i, r["target_column"], J(r["steps"]))
                             for i, r in enumerate(rule_list)])
        conn.execute("update projects set rules_version = %s, rules_template_sha256 = %s where id = %s",
                     [version, template["sha256"], project_id])
        audit(conn, ctx.org_id, ctx.user_id, "rules.saved", "project", project_id,
              {"version": version, "rules": len(rule_list)})
    return {"version": version}


@app.get("/projects/{project_id}/rules/suggest")
def suggest_rules(project_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        _, template, imp = _project_bundle(conn, project_id, ctx)
    s = template["structure"]
    return {"rules": rules_engine.suggest_rules(s.get("columns", []), set(imp["column_map"].values()),
                                                s.get("category_column"), template["requirements"]),
            "note": "Propozycja na podstawie słownika aliasów nazw kolumn. Przejrzyj ją przed zapisaniem."}


class PreviewIn(BaseModel):
    rules: list[RuleIn]
    limit: int = Field(default=3, ge=1, le=10)


@app.post("/projects/{project_id}/rules/preview")
def preview_rules(project_id: str, body: PreviewIn, ctx: Ctx = Depends(current_ctx)):
    rule_list = [r.model_dump() for r in body.rules if r.steps]
    with user_tx(ctx.user_id) as conn:
        project, template, imp = _project_bundle(conn, project_id, ctx)
        _check_rules(rule_list, template)
        env = load_env(conn, project, template)
        products = many(conn, """select row_number, data from canonical_products where import_id = %s
                                 and organization_id = %s order by row_number limit %s""",
                        [imp["id"], ctx.org_id, body.limit])
    labels = column_labels(template["structure"])
    out = []
    for p in products:
        res = evaluate_product(p["data"], rule_list, env, labels, {})
        out.append({"row_number": p["row_number"], "sku": p["data"]["sku"], "values": res["values"],
                    "provenance": res["provenance"], "issues": res["issues"], "skipped": res["skipped"]})
    return {"rows": out}


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_id: str


@app.get("/presets")
def list_presets(marketplace_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, """select id, marketplace_id, name, template_sha256, created_at,
                             jsonb_array_length(rules) as rule_count from mapping_presets where organization_id = %s
                             and (%s::text is null or marketplace_id = %s) order by name""",
                    [ctx.org_id, marketplace_id, marketplace_id])


@app.post("/presets")
def save_preset(body: PresetIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project, template, _ = _project_bundle(conn, body.project_id, ctx)
        if project["rules_version"] == 0:
            raise HTTPException(409, "Najpierw zapisz reguły w projekcie.")
        rows = many(conn, """select target_column, steps from mapping_rules where project_id = %s and version = %s
                             order by position""", [project["id"], project["rules_version"]])
        p = one(conn, """insert into mapping_presets (organization_id, marketplace_id, name, template_sha256, rules)
                         values (%s,%s,%s,%s,%s) on conflict (organization_id, name) do update
                         set rules = excluded.rules, template_sha256 = excluded.template_sha256,
                             marketplace_id = excluded.marketplace_id returning id""",
                [ctx.org_id, project["marketplace_id"], body.name.strip(), template["sha256"], J(rows)])
        audit(conn, ctx.org_id, ctx.user_id, "preset.saved", "preset", p["id"], {"name": body.name})
    return {"id": p["id"]}


@app.delete("/presets/{preset_id}")
def delete_preset(preset_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        owned(conn, "mapping_presets", preset_id, ctx, "Preset")
        conn.execute("delete from mapping_presets where id = %s", [preset_id])
    return {"ok": True}


@app.get("/projects/{project_id}/rules/from-preset/{preset_id}")
def rules_from_preset(project_id: str, preset_id: str, ctx: Ctx = Depends(current_ctx)):
    """Zwraca reguły presetu dopasowane do szablonu projektu. Niczego nie zapisuje."""
    with user_tx(ctx.user_id) as conn:
        _, template, _ = _project_bundle(conn, project_id, ctx)
        preset = owned(conn, "mapping_presets", preset_id, ctx, "Preset")
    columns = {c["key"] for c in template["structure"].get("columns", [])} | {rules_engine.ROW_TARGET}
    kept = [r for r in preset["rules"] if r["target_column"] in columns]
    return {"rules": kept, "dropped": [r["target_column"] for r in preset["rules"] if r["target_column"] not in columns],
            "template_changed": preset["template_sha256"] != template["sha256"]}


@app.post("/projects/{project_id}/run")
def run_project(project_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project, template, imp = _project_bundle(conn, project_id, ctx)
        if imp["status"] != "ready":
            raise HTTPException(409, "Najpierw zatwierdź mapowanie kolumn pliku źródłowego.")
        if template["status"] not in ("needs_review", "ready"):
            raise HTTPException(409, "Szablon nie został jeszcze odczytany.")
        if project["status"] in ("validating", "exporting"):
            raise HTTPException(409, "Projekt jest właśnie przetwarzany.")
        conn.execute("update projects set status = 'queued', error = null where id = %s", [project_id])
        job = jobs.enqueue_unique(conn, ctx.org_id, "run_project", {"project_id": project_id}, ctx.user_id, "project_id")
    return {"job_id": job["id"]}


# ------------------------------------------------------ słowniki kategorii/wartości
@app.get("/projects/{project_id}/categories")
def project_categories(project_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project, template, imp = _project_bundle(conn, project_id, ctx)
        cats = many(conn, """select coalesce(category_path, '') as source, count(*)::int as products
                             from canonical_products where import_id = %s and organization_id = %s
                             group by 1 order by 2 desc""", [imp["id"], ctx.org_id])
        maps = {m["source_key"]: m for m in many(conn, """select * from category_mappings where organization_id = %s
                                                          and marketplace_id = %s""",
                                                 [ctx.org_id, project["marketplace_id"]])}
    col = template["structure"].get("category_column")
    enum = (template["requirements"].get(col) or {}).get("enum") or [] if col else []
    options = [(e["code"], e.get("label") or "") for e in enum]
    out = []
    for c in cats:
        m = maps.get(fold(c["source"]))
        leaf = c["source"].replace(">", "/").split("/")[-1]
        out.append({**c, "mapped_code": m["target_code"] if m else None, "mapping_id": m["id"] if m else None,
                    "mapped_valid": (not enum or any(e["code"] == m["target_code"] for e in enum)) if m else None,
                    "candidates": [] if m or not c["source"] else rules_engine.similar(leaf, options)})
    return {"category_column": col, "options": enum, "categories": out, "marketplace_id": project["marketplace_id"]}


class CategoryMapIn(BaseModel):
    marketplace_id: str
    source_category: str = Field(min_length=1, max_length=500)
    target_code: str = Field(min_length=1, max_length=200)
    template_id: str | None = None


def _enum_for(conn, ctx: Ctx, template_id: str | None, column: str | None) -> list[dict]:
    if not template_id or not column:
        return []
    tpl = owned(conn, "target_templates", template_id, ctx, "Szablon")
    return (tpl["requirements"].get(column) or {}).get("enum") or []


@app.put("/category-mappings")
def put_category_mapping(body: CategoryMapIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        label = None
        if body.template_id:
            tpl = owned(conn, "target_templates", body.template_id, ctx, "Szablon")
            enum = _enum_for(conn, ctx, body.template_id, tpl["structure"].get("category_column"))
            hit = [e for e in enum if e["code"] == body.target_code]
            if enum and not hit:
                raise HTTPException(422, "Wybrany kod kategorii nie występuje na liście z szablonu.")
            label = hit[0].get("label") if hit else None
        conn.execute("""insert into category_mappings (organization_id, marketplace_id, source_category, source_key,
                        target_code, target_label, approved_by) values (%s,%s,%s,%s,%s,%s,%s)
                        on conflict (organization_id, marketplace_id, source_key) do update
                        set target_code = excluded.target_code, target_label = excluded.target_label,
                            approved_by = excluded.approved_by, source_category = excluded.source_category""",
                     [ctx.org_id, body.marketplace_id, single_line(body.source_category), fold(body.source_category),
                      body.target_code, label, ctx.user_id])
        audit(conn, ctx.org_id, ctx.user_id, "category_mapping.approved", "marketplace", body.marketplace_id,
              {"source": body.source_category, "target": body.target_code})
    return {"ok": True}


@app.get("/category-mappings")
def list_category_mappings(marketplace_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, """select * from category_mappings where organization_id = %s
                             and (%s::text is null or marketplace_id = %s) order by source_category""",
                    [ctx.org_id, marketplace_id, marketplace_id])


@app.delete("/category-mappings/{mapping_id}")
def delete_category_mapping(mapping_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        owned(conn, "category_mappings", mapping_id, ctx, "Mapowanie kategorii")
        conn.execute("delete from category_mappings where id = %s", [mapping_id])
    return {"ok": True}


class ValueMapIn(BaseModel):
    marketplace_id: str
    target_column: str
    category_code: str = ""
    source_value: str = Field(min_length=1, max_length=500)
    target_value: str = Field(min_length=1, max_length=500)
    template_id: str | None = None


@app.put("/value-mappings")
def put_value_mapping(body: ValueMapIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        enum = _enum_for(conn, ctx, body.template_id, body.target_column)
        if enum and not any(e["code"] == body.target_value for e in enum):
            raise HTTPException(422, "Wartość docelowa nie znajduje się na liście dozwolonych wartości z szablonu.")
        conn.execute("""insert into value_mappings (organization_id, marketplace_id, target_column, category_code,
                        source_value, source_key, target_value, approved_by) values (%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict (organization_id, marketplace_id, target_column, category_code, source_key)
                        do update set target_value = excluded.target_value, approved_by = excluded.approved_by""",
                     [ctx.org_id, body.marketplace_id, body.target_column, body.category_code,
                      single_line(body.source_value), fold(body.source_value), body.target_value, ctx.user_id])
        audit(conn, ctx.org_id, ctx.user_id, "value_mapping.approved", "marketplace", body.marketplace_id,
              {"column": body.target_column, "source": body.source_value, "target": body.target_value})
    return {"ok": True}


@app.get("/value-mappings")
def list_value_mappings(marketplace_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, """select * from value_mappings where organization_id = %s
                             and (%s::text is null or marketplace_id = %s) order by target_column, source_value""",
                    [ctx.org_id, marketplace_id, marketplace_id])


@app.delete("/value-mappings/{mapping_id}")
def delete_value_mapping(mapping_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        owned(conn, "value_mappings", mapping_id, ctx, "Mapowanie wartości")
        conn.execute("delete from value_mappings where id = %s", [mapping_id])
    return {"ok": True}


@app.get("/projects/{project_id}/unmapped-values")
def unmapped_values(project_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project, template, _ = _project_bundle(conn, project_id, ctx)
        rows = many(conn, """select target_column, processed_value as value, count(*)::int as products,
                             (array_agg(suggestions))[1] as suggestions from validation_issues
                             where project_id = %s and code = 'ENUM_NOT_ALLOWED'
                             group by 1, 2 order by 1, 3 desc""", [project_id])
    labels = column_labels(template["structure"])
    for r in rows:
        r["label"] = labels.get(r["target_column"], r["target_column"])
        r["options"] = (template["requirements"].get(r["target_column"]) or {}).get("enum") or []
    return {"marketplace_id": project["marketplace_id"], "template_id": template["id"], "values": rows}


# -------------------------------------------------------------- centrum błędów
@app.get("/projects/{project_id}/issues/summary")
def issues_summary(project_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project, template, _ = _project_bundle(conn, project_id, ctx)
        groups = many(conn, """select severity, code, target_column, count(distinct row_number)::int as products,
                               min(message) as sample from validation_issues where project_id = %s
                               group by 1, 2, 3 order by case severity when 'BLOCKER' then 0 when 'REVIEW' then 1
                               else 2 end, 4 desc""", [project_id])
        categories = many(conn, """select coalesce(category, '') as category, count(distinct row_number)::int as products
                                   from validation_issues where project_id = %s group by 1 order by 2 desc""",
                          [project_id])
    labels = column_labels(template["structure"])
    for g in groups:
        g["label"] = labels.get(g["target_column"]) if g["target_column"] else None
    return {"groups": groups, "categories": categories, "project_issues": project["project_issues"],
            "counters": project["counters"]}


@app.get("/projects/{project_id}/issues")
def list_issues(project_id: str, severity: str | None = None, code: str | None = None, column: str | None = None,
                category: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                ctx: Ctx = Depends(current_ctx)):
    where, params = ["project_id = %s", "organization_id = %s"], [project_id, ctx.org_id]
    for field, value in (("severity", severity), ("code", code), ("target_column", column)):
        if value:
            where.append(f"{field} = %s")
            params.append(value)
    if category is not None:
        where.append("coalesce(category, '') = %s")
        params.append(category)
    w = " and ".join(where)
    with user_tx(ctx.user_id) as conn:
        owned(conn, "projects", project_id, ctx, "Projekt")
        total = one(conn, f"select count(*)::int as n from validation_issues where {w}", params)["n"]
        rows = many(conn, f"select * from validation_issues where {w} order by row_number, id limit %s offset %s",
                    [*params, limit, offset])
    return {"total": total, "rows": rows}


@app.get("/projects/{project_id}/rows")
def list_output_rows(project_id: str, status: str | None = None, limit: int = Query(50, ge=1, le=200),
                     offset: int = Query(0, ge=0), ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        owned(conn, "projects", project_id, ctx, "Projekt")
        params = [project_id, ctx.org_id, status, status]
        total = one(conn, """select count(*)::int as n from output_rows where project_id = %s and organization_id = %s
                             and (%s::text is null or status = %s)""", params)["n"]
        rows = many(conn, """select row_number, sku, gtin, category, status, "values" from output_rows
                             where project_id = %s and organization_id = %s and (%s::text is null or status = %s)
                             order by row_number limit %s offset %s""", [*params, limit, offset])
    return {"total": total, "rows": rows}


@app.get("/projects/{project_id}/rows/{row_number}")
def output_row_detail(project_id: str, row_number: int, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project = owned(conn, "projects", project_id, ctx, "Projekt")
        row = one(conn, "select * from output_rows where project_id = %s and organization_id = %s and row_number = %s",
                  [project_id, ctx.org_id, row_number])
        if not row:
            raise HTTPException(404, "Wiersz: nie znaleziono.")
        source = one(conn, "select raw from import_rows where import_id = %s and row_number = %s",
                     [project["import_id"], row_number])
        product = one(conn, "select data, source_issues from canonical_products where import_id = %s and row_number = %s",
                      [project["import_id"], row_number])
        issues = many(conn, "select * from validation_issues where project_id = %s and row_number = %s order by id",
                      [project_id, row_number])
        history = many(conn, """select id, target_column, value, previous_value, created_at, reverted_at, batch_id
                                from value_overrides where project_id = %s and row_number = %s
                                order by created_at desc""", [project_id, row_number])
    return {"row": row, "raw": source["raw"] if source else {}, "product": product, "issues": issues,
            "history": history}


class OverrideIn(BaseModel):
    row_number: int
    target_column: str
    value: str = Field(max_length=20000)


def _queue_rerun(conn, project_id: str, ctx: Ctx) -> str:
    conn.execute("update projects set status = 'queued' where id = %s and status not in ('validating', 'exporting')",
                 [project_id])
    return str(jobs.enqueue_unique(conn, ctx.org_id, "run_project", {"project_id": project_id}, ctx.user_id,
                                   "project_id")["id"])


@app.post("/projects/{project_id}/overrides")
def add_override(project_id: str, body: OverrideIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        _, template, _ = _project_bundle(conn, project_id, ctx)
        if body.target_column not in {c["key"] for c in template["structure"].get("columns", [])}:
            raise HTTPException(422, "Kolumna docelowa nie istnieje w szablonie.")
        row = one(conn, """select "values" from output_rows where project_id = %s and organization_id = %s
                           and row_number = %s""", [project_id, ctx.org_id, body.row_number])
        if not row:
            raise HTTPException(404, "Wiersz: nie znaleziono. Uruchom najpierw walidację.")
        o = one(conn, """insert into value_overrides (organization_id, project_id, row_number, target_column, value,
                         previous_value, created_by) values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                [ctx.org_id, project_id, body.row_number, body.target_column, body.value,
                 row["values"].get(body.target_column, ""), ctx.user_id])
        audit(conn, ctx.org_id, ctx.user_id, "override.added", "project", project_id,
              {"row": body.row_number, "column": body.target_column})
        return {"id": o["id"], "job_id": _queue_rerun(conn, project_id, ctx)}


@app.post("/projects/{project_id}/overrides/{override_id}/revert")
def revert_override(project_id: str, override_id: str, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        owned(conn, "projects", project_id, ctx, "Projekt")
        o = owned(conn, "value_overrides", override_id, ctx, "Korekta")
        if str(o["project_id"]) != project_id:
            raise HTTPException(404, "Korekta: nie znaleziono.")
        conn.execute("update value_overrides set reverted_at = now() where id = %s and reverted_at is null", [override_id])
        audit(conn, ctx.org_id, ctx.user_id, "override.reverted", "project", project_id,
              {"row": o["row_number"], "column": o["target_column"]})
        return {"job_id": _queue_rerun(conn, project_id, ctx)}


class BulkIn(BaseModel):
    target_column: str
    value: str = Field(max_length=20000)
    issue_code: str | None = None
    current_value: str | None = None
    confirm: bool = False
    expected_count: int | None = None


@app.post("/projects/{project_id}/overrides/bulk")
def bulk_override(project_id: str, body: BulkIn, ctx: Ctx = Depends(current_ctx)):
    """Masowa korekta: najpierw podgląd (liczba i próbka), zapis dopiero po potwierdzeniu liczby rekordów."""
    if body.issue_code is None and body.current_value is None:
        raise HTTPException(422, "Podaj kod problemu lub bieżącą wartość, aby wskazać rekordy.")
    with user_tx(ctx.user_id) as conn:
        _, template, _ = _project_bundle(conn, project_id, ctx)
        if body.target_column not in {c["key"] for c in template["structure"].get("columns", [])}:
            raise HTTPException(422, "Kolumna docelowa nie istnieje w szablonie.")
        where, params = ["o.project_id = %s", "o.organization_id = %s", "not o.skipped"], [project_id, ctx.org_id]
        if body.issue_code:
            where.append("""exists (select 1 from validation_issues v where v.project_id = o.project_id
                            and v.row_number = o.row_number and v.code = %s and v.target_column = %s)""")
            params += [body.issue_code, body.target_column]
        if body.current_value is not None:
            where.append('coalesce(o."values" ->> %s, \'\') = %s')
            params += [body.target_column, body.current_value]
        rows = many(conn, f"""select o.row_number, o.sku, o."values" ->> %s as current from output_rows o
                              where {' and '.join(where)} order by o.row_number""", [body.target_column, *params])
        if not body.confirm:
            return {"count": len(rows), "sample": rows[:10], "applied": False}
        if body.expected_count != len(rows):
            raise HTTPException(409, f"Liczba rekordów zmieniła się (teraz {len(rows)}). Odśwież podgląd.")
        if not rows:
            raise HTTPException(422, "Brak rekordów do zmiany.")
        batch = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.executemany("""insert into value_overrides (organization_id, project_id, row_number, target_column, value,
                               previous_value, batch_id, created_by) values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                            [(ctx.org_id, project_id, r["row_number"], body.target_column, body.value, r["current"] or "",
                              batch, ctx.user_id) for r in rows])
        audit(conn, ctx.org_id, ctx.user_id, "override.bulk", "project", project_id,
              {"column": body.target_column, "count": len(rows), "batch_id": batch})
        return {"count": len(rows), "applied": True, "batch_id": batch, "job_id": _queue_rerun(conn, project_id, ctx)}


# -------------------------------------------------------------------- eksporty
class ExportIn(BaseModel):
    format: Literal["xlsx", "csv"]
    scope: Literal["ready_only", "all"]
    acknowledge_problems: bool = False


@app.post("/projects/{project_id}/exports")
def create_export(project_id: str, body: ExportIn, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        project, template, _ = _project_bundle(conn, project_id, ctx)
        if project["last_run_version"] != project["rules_version"] or project["status"] not in (
                "ready", "needs_review", "completed"):
            raise HTTPException(409, "Przed eksportem uruchom walidację na aktualnych danych i regułach.")
        c = project["counters"]
        problems = c.get("review", 0) + c.get("blocked", 0)
        if body.scope == "all" and problems and not body.acknowledge_problems:
            raise HTTPException(422, f"Pełny plik zawiera {problems} wierszy z problemami. Potwierdź, że chcesz je "
                                "wyeksportować mimo ostrzeżenia.")
        if body.scope == "ready_only" and not c.get("ready", 0):
            raise HTTPException(422, "Żaden wiersz nie ma statusu „gotowy”.")
        if body.format == "xlsx" and template["format"] != "xlsx":
            raise HTTPException(422, "Szablon jest plikiem CSV — dostępny jest tylko eksport CSV.")
        exp = one(conn, """insert into exports (organization_id, project_id, format, scope, created_by)
                           values (%s,%s,%s,%s,%s) returning id""",
                  [ctx.org_id, project_id, body.format, body.scope, ctx.user_id])
        jobs.enqueue(conn, ctx.org_id, "export_project", {"export_id": str(exp["id"])}, ctx.user_id)
    return {"id": exp["id"]}


@app.get("/exports")
def list_exports(project_id: str | None = None, ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        return many(conn, """select e.id, e.project_id, e.format, e.scope, e.status, e.filename, e.report_filename,
                             e.report_csv_filename, e.counters, e.compatibility, e.error, e.expires_at, e.created_at,
                             p.name as project_name, p.marketplace_id from exports e
                             join projects p on p.id = e.project_id where e.organization_id = %s
                             and (%s::uuid is null or e.project_id = %s) order by e.created_at desc limit 100""",
                    [ctx.org_id, project_id, project_id])


@app.get("/exports/{export_id}/download")
def download_export(export_id: str, background: BackgroundTasks, file: Literal["data", "report", "report_csv"] = "data",
                    ctx: Ctx = Depends(current_ctx)):
    with user_tx(ctx.user_id) as conn:
        exp = owned(conn, "exports", export_id, ctx, "Eksport")
        if exp["status"] != "completed" or exp["deleted_at"]:
            raise HTTPException(410 if exp["deleted_at"] else 409, "Plik nie jest dostępny.")
        if exp["expires_at"] and exp["expires_at"] < dt.datetime.now(dt.UTC):
            raise HTTPException(410, "Czas życia pliku minął. Wygeneruj eksport ponownie.")
        key, name = {"data": (exp["storage_path"], exp["filename"]),
                     "report": (exp["report_path"], exp["report_filename"]),
                     "report_csv": (exp["report_csv_path"], exp["report_csv_filename"])}[file]
        audit(conn, ctx.org_id, ctx.user_id, "export.downloaded", "export", export_id, {"file": file})
    fd, path = tempfile.mkstemp(prefix="mdb-dl-")
    os.close(fd)
    storage().fetch(key, path)
    background.add_task(os.unlink, path)
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if name.endswith(".xlsx")
             else "text/csv; charset=utf-8")
    return FileResponse(path, filename=name, media_type=media,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
