"""Supabase, from the server's side: check a sign-in, call a database function.

Two jobs, and nothing else:

1. `verify(token)` -- is this access token one that Supabase Auth signed for
   our project, and who is it? The token is a JWT. Supabase signs it with a
   PRIVATE key and publishes the matching PUBLIC keys (a JWKS) at a fixed URL,
   so we can check a signature without holding any Supabase secret at all. We
   also check the audience ("authenticated": a signed-in user, not the anon
   role), the issuer (OUR project, not someone else's) and the expiry.

2. `rpc(name, **args)` -- call one of OUR Postgres functions
   (deploy/supabase/*.sql) through PostgREST with the server-only key. The
   browser never talks to the database: every table has row-level security
   switched on with no policies, so the public anon key can read nothing.
   Authorisation lives in FastAPI, in one place (ownership.py).

Off unless SUPABASE_URL and SUPABASE_SERVICE_KEY are both set, so localhost
and the password-protected beta behave exactly as before.
"""

from __future__ import annotations

import functools
from typing import Any

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from labsagent.config import PROJECT_ROOT


class SupaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    url: str = Field(default="", alias="SUPABASE_URL")
    # Public by design: the browser needs it to start a Google sign-in.
    anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    # Server only. Bypasses row-level security, so it must never reach the
    # browser, a log line, or a model-written program (program_env and the
    # runner's fixed environment both keep it out).
    service_key: str = Field(default="", alias="SUPABASE_SERVICE_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.service_key and self.anon_key)

    @property
    def base(self) -> str:
        return self.url.rstrip("/")


settings = SupaSettings()


class SupaError(RuntimeError):
    """The database said no, or could not be reached."""


class BadToken(ValueError):
    """Not a valid sign-in for this project."""


def configured() -> bool:
    return settings.enabled


@functools.cache
def _jwks(base: str):
    import jwt

    # Caches the keys and fetches again when a token names a key id it has
    # not seen -- which is what happens after Supabase rotates its keys.
    return jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json", cache_keys=True)


def verify(token: str, *, config: SupaSettings | None = None, jwks=None) -> dict[str, Any]:
    """The token's claims, or BadToken. `sub` is the user's id."""
    import jwt

    config = config or settings
    if not token or token.count(".") != 2:
        raise BadToken("not a JWT")
    try:
        client = jwks or _jwks(config.base)
        key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            # Asymmetric only. Accepting HS256 here would let anyone who
            # learns a shared secret mint tokens; the public-key schemes
            # cannot be forged with anything the server holds.
            algorithms=["ES256", "RS256", "EdDSA"],
            audience="authenticated",
            issuer=f"{config.base}/auth/v1",
            options={"require": ["exp", "sub", "aud", "iss"]},
            # CLOCKS DISAGREE. A token is stamped with Supabase's time; if
            # this server's clock is even one second behind, a brand-new
            # token is "not yet valid". Found against the real project with
            # this machine 0.9 s behind. A minute either way is the usual
            # allowance, and it barely extends an hour-long token.
            leeway=60,
        )
    except jwt.PyJWTError as exc:
        raise BadToken(str(exc)) from exc
    if not claims.get("sub"):
        raise BadToken("no subject")
    return claims


def _headers(key: str) -> dict[str, str]:
    headers = {"apikey": key, "Content-Type": "application/json"}
    # Legacy keys are JWTs and go in Authorization too; the newer
    # `sb_secret_...` keys are not JWTs and belong in `apikey` only.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def rpc(name: str, *, config: SupaSettings | None = None, **args: Any) -> Any:
    """Call `public.<name>(args)` with the server key. Returns its JSON."""
    config = config or settings
    try:
        response = httpx.post(
            f"{config.base}/rest/v1/rpc/{name}",
            json=args,
            headers=_headers(config.service_key),
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise SupaError(f"database unreachable: {exc}") from exc
    if response.status_code >= 400:
        raise SupaError(f"{name} failed ({response.status_code}): {response.text[:300]}")
    if not response.content:
        return None
    return response.json()
