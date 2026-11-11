"""Dostęp do Postgresa. Zapytania użytkownika wykonują się jako rola `authenticated` (RLS)."""

from __future__ import annotations

import json
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings.database_url, min_size=1, max_size=10, open=True,
                               kwargs={"row_factory": dict_row})
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def service_tx():
    """Połączenie serwisowe (worker, bootstrap). Każde zapytanie MUSI filtrować organization_id."""
    with pool().connection() as conn:
        yield conn


@contextmanager
def user_tx(user_id: str):
    """Transakcja w kontekście użytkownika: SET LOCAL ROLE authenticated + claims => działa RLS."""
    with pool().connection() as conn:
        conn.execute("select set_config('request.jwt.claims', %s, true)",
                     [json.dumps({"sub": user_id, "role": "authenticated"})])
        conn.execute("set local role authenticated")
        yield conn


def J(value) -> Jsonb:
    return Jsonb(value)


def one(conn, sql: str, params=()) -> dict | None:
    return conn.execute(sql, params).fetchone()


def many(conn, sql: str, params=()) -> list[dict]:
    return conn.execute(sql, params).fetchall()
