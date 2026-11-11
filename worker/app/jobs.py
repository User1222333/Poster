"""Kolejka zadań w Postgresie: SKIP LOCKED, retry z backoffem, klucz idempotencji."""

from __future__ import annotations

import logging
import time
import traceback

from .db import J, one, service_tx
from .engine.source_parser import SourceError

log = logging.getLogger("mdb.jobs")
STALE_MINUTES = 15


class UserError(Exception):
    """Błąd deterministyczny (zła zawartość pliku/konfiguracji) — ponawianie nie ma sensu."""


def enqueue(conn, org_id: str, kind: str, payload: dict, user_id: str | None = None,
            idempotency_key: str | None = None) -> dict:
    if idempotency_key:
        existing = one(conn, "select * from processing_jobs where organization_id = %s and idempotency_key = %s",
                       [org_id, idempotency_key])
        if existing:
            return existing
    return one(conn, """insert into processing_jobs (organization_id, kind, payload, created_by, idempotency_key)
                        values (%s, %s, %s, %s, %s) returning *""",
               [org_id, kind, J(payload), user_id, idempotency_key])


def enqueue_unique(conn, org_id: str, kind: str, payload: dict, user_id: str | None, match_key: str) -> dict:
    """Nie dubluje oczekującego zadania tego samego rodzaju dla tego samego obiektu."""
    pending = one(conn, """select * from processing_jobs where organization_id = %s and kind = %s
                           and status = 'queued' and payload ->> %s = %s limit 1""",
                  [org_id, kind, match_key, str(payload[match_key])])
    return pending or enqueue(conn, org_id, kind, payload, user_id)


def set_progress(job_id: str, progress: int) -> None:
    with service_tx() as conn:
        conn.execute("update processing_jobs set progress = %s where id = %s", [progress, job_id])


def _claim() -> dict | None:
    with service_tx() as conn:
        conn.execute("""update processing_jobs set status = 'queued', locked_at = null
                        where status = 'running' and locked_at < now() - make_interval(mins => %s)""", [STALE_MINUTES])
        return one(conn, """update processing_jobs set status = 'running', attempts = attempts + 1,
                            locked_at = now(), progress = 0, error = null
                            where id = (select id from processing_jobs
                                        where status = 'queued' and run_after <= now()
                                        order by created_at for update skip locked limit 1)
                            returning *""")


def run_one() -> bool:
    """Wykonuje jedno zadanie z kolejki. Zwraca False, gdy kolejka jest pusta."""
    from .pipelines import HANDLERS, mark_failed

    job = _claim()
    if not job:
        return False
    started = time.monotonic()
    try:
        result = HANDLERS[job["kind"]](job)
        with service_tx() as conn:
            conn.execute("""update processing_jobs set status = 'completed', progress = 100, result = %s,
                            finished_at = now(), locked_at = null where id = %s""", [J(result or {}), job["id"]])
        log.info("job %s %s ok in %.2fs", job["kind"], job["id"], time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 - każde niepowodzenie musi trafić do rekordu zadania
        deterministic = isinstance(exc, UserError | SourceError | KeyError)
        final = deterministic or job["attempts"] >= job["max_attempts"]
        message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
        if not deterministic:
            log.error("job %s %s failed: %s", job["kind"], job["id"], traceback.format_exc())
            if final:
                message = "Wewnętrzny błąd przetwarzania. Spróbuj ponownie lub skontaktuj się z administratorem."
        with service_tx() as conn:
            if final:
                conn.execute("""update processing_jobs set status = 'failed', error = %s, finished_at = now(),
                                locked_at = null where id = %s""", [message, job["id"]])
            else:
                conn.execute("""update processing_jobs set status = 'queued', error = %s, locked_at = null,
                                run_after = now() + make_interval(secs => %s) where id = %s""",
                             [message, 5 * 2 ** job["attempts"], job["id"]])
        if final:
            mark_failed(job, message)
    return True


def drain(limit: int = 1000) -> int:
    n = 0
    while n < limit and run_one():
        n += 1
    return n
