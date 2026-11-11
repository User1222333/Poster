"""Uwierzytelnianie (JWT Supabase lub lokalny tryb dev) i kontekst organizacji."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException

from .config import settings
from .db import many, one, service_tx

_jwks_client: jwt.PyJWKClient | None = None


def _decode(token: str) -> dict:
    try:
        if settings.jwt_secret:
            return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"],
                              audience="authenticated", options={"require": ["sub", "exp"]})
        global _jwks_client
        if _jwks_client is None:
            _jwks_client = jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)
        key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(token, key.key, algorithms=["ES256", "RS256"], audience="authenticated",
                          options={"require": ["sub", "exp"]})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Sesja wygasła lub jest nieprawidłowa. Zaloguj się ponownie.") from exc


@dataclass
class Ctx:
    user_id: str
    email: str
    org_id: str
    org_name: str
    role: str


_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window: float = 60.0) -> None:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Zbyt wiele żądań. Spróbuj ponownie za chwilę.")
    q.append(now)


def _membership(user_id: str, email: str, wanted_org: str | None) -> dict:
    with service_tx() as conn:
        rows = many(conn, """select m.organization_id, m.role, o.name from organization_members m
                             join organizations o on o.id = m.organization_id
                             where m.user_id = %s order by m.created_at""", [user_id])
        if not rows:
            # Pierwsze logowanie: własna organizacja użytkownika (rola owner).
            if not one(conn, "select 1 from auth.users where id = %s", [user_id]):
                raise HTTPException(401, "Nieznany użytkownik.")
            org = one(conn, "insert into organizations (name) values (%s) returning id, name",
                      [f"Organizacja {email.split('@')[0]}" if email else "Moja organizacja"])
            conn.execute("insert into organization_members (organization_id, user_id, role) values (%s, %s, 'owner')",
                         [org["id"], user_id])
            return {"organization_id": org["id"], "role": "owner", "name": org["name"]}
        if wanted_org:
            for r in rows:
                if str(r["organization_id"]) == wanted_org:
                    return r
            raise HTTPException(403, "Brak dostępu do tej organizacji.")
        return rows[0]


def current_ctx(authorization: str = Header(default=""), x_organization_id: str | None = Header(default=None)) -> Ctx:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Wymagane logowanie.")
    claims = _decode(authorization[7:].strip())
    user_id, email = claims["sub"], claims.get("email", "")
    rate_limit(f"u:{user_id}", settings.rate_limit_per_minute)
    m = _membership(user_id, email, x_organization_id)
    return Ctx(user_id=user_id, email=email, org_id=str(m["organization_id"]), org_name=m["name"], role=m["role"])


# ------------------------------------------------------------ tryb lokalny (dev)
def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)).split("$")[2], digest)


def issue_local_token(user_id: str, email: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "email": email, "aud": "authenticated", "role": "authenticated",
                       "iat": now, "exp": now + 8 * 3600}, settings.jwt_secret, algorithm="HS256")
