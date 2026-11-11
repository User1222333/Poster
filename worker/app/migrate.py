"""Stosuje migracje SQL: `python -m app.migrate [--local-shim]`.

`--local-shim` dodaje schemat `auth` i rolę `authenticated` na czystym Postgresie (dev/test).
Na Supabase uruchamiaj BEZ tej flagi (albo użyj `supabase db push`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from .config import settings

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = Path(__import__("os").environ.get("MIGRATIONS_DIR", ROOT.parent / "supabase" / "migrations"))


def apply(database_url: str, local_shim: bool) -> list[str]:
    files = sorted(MIGRATIONS.glob("*.sql"))
    applied = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("create extension if not exists pgcrypto")
        if local_shim:
            conn.execute((ROOT / "dev" / "local_auth_shim.sql").read_text(encoding="utf-8"))
            applied.append("local_auth_shim.sql")
        for f in files:
            conn.execute(f.read_text(encoding="utf-8"))
            applied.append(f.name)
    return applied


if __name__ == "__main__":
    print("applied:", ", ".join(apply(settings.database_url, "--local-shim" in sys.argv)))
