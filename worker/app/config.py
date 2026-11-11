"""Konfiguracja z zmiennych środowiskowych. Sekrety wyłącznie po stronie serwera."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", "postgresql://mdb:mdb@127.0.0.1:5432/mdb"))
    auth_mode: str = field(default_factory=lambda: os.environ.get("AUTH_MODE", "local"))  # local | supabase
    jwt_secret: str = field(default_factory=lambda: os.environ.get("JWT_SECRET", ""))
    supabase_url: str = field(default_factory=lambda: os.environ.get("SUPABASE_URL", "").rstrip("/"))
    supabase_service_role_key: str = field(default_factory=lambda: os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))
    storage_mode: str = field(default_factory=lambda: os.environ.get("STORAGE_MODE", "local"))  # local | supabase
    storage_dir: str = field(default_factory=lambda: os.environ.get("STORAGE_DIR", ".data/storage"))
    storage_bucket: str = field(default_factory=lambda: os.environ.get("STORAGE_BUCKET", "mdb-files"))
    fixtures_dir: str = field(default_factory=lambda: os.environ.get(
        "FIXTURES_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")))
    max_upload_mb: int = field(default_factory=lambda: int(os.environ.get("MAX_UPLOAD_MB", "20")))
    max_rows: int = field(default_factory=lambda: int(os.environ.get("MAX_ROWS", "5000")))
    export_ttl_hours: int = field(default_factory=lambda: int(os.environ.get("EXPORT_TTL_HOURS", "72")))
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()])
    inline_worker: bool = field(default_factory=lambda: _bool("INLINE_WORKER"))
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.environ.get("RATE_LIMIT_PER_MINUTE", "240")))
    upload_limit_per_minute: int = field(default_factory=lambda: int(os.environ.get("UPLOAD_LIMIT_PER_MINUTE", "20")))

    def validate(self) -> None:
        if self.auth_mode not in ("local", "supabase"):
            raise RuntimeError("AUTH_MODE musi mieć wartość local albo supabase.")
        if self.auth_mode == "local" and len(self.jwt_secret) < 32:
            raise RuntimeError("AUTH_MODE=local wymaga JWT_SECRET o długości co najmniej 32 znaków.")
        if self.auth_mode == "supabase" and not (self.jwt_secret or self.supabase_url):
            raise RuntimeError("AUTH_MODE=supabase wymaga SUPABASE_URL (JWKS) lub JWT_SECRET (starszy tryb HS256).")
        if self.storage_mode == "supabase" and not (self.supabase_url and self.supabase_service_role_key):
            raise RuntimeError("STORAGE_MODE=supabase wymaga SUPABASE_URL i SUPABASE_SERVICE_ROLE_KEY.")


settings = Settings()
