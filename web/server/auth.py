"""One username, one password, one signed cookie.

Off unless BOTH `LABSAGENT_USER` and `LABSAGENT_PASSWORD` are set, so running
on localhost is unchanged. Set them (in .env, or the container's environment)
and every /api route except the three below answers 401 until you sign in.

WHAT THE COOKIE IS. Not a session id pointing at server-side state -- there is
no session store. It is `<expiry>.<hmac(expiry)>`: the server can check it was
the one that minted it, and that it has not expired, with nothing remembered in
between. Forging one needs the key; stretching one's expiry breaks the HMAC.

WHERE THE KEY COMES FROM. `secrets.token_bytes` at import, so it lives only in
this process. The deliberate cost: a restart (a deploy, a reboot) signs
everyone out. The deliberate gain: no extra secret to create, store or leak.

WHY THE UI IS NOT BEHIND IT. The built JS/CSS carries no secrets -- everything
worth protecting is data, and data only arrives through /api. So the static
files stay public, the page asks /api/session on load, and shows the sign-in
form when told to. The alternative (gate every path, serve a login page from
the server) needs a second, non-React page for no gain in safety.

WHY SameSite=Lax IS ENOUGH AGAINST CSRF HERE. Lax cookies are not sent on a
cross-site POST, and every route that changes anything is a POST. A hostile
page can link to the app (a top-level GET, cookie sent) but cannot start a run
or upload a file as you.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from labsagent.config import PROJECT_ROOT

COOKIE = "labsagent_session"
SESSION_S = 7 * 24 * 3600

# The routes a signed-out browser must still reach: to ask whether it is
# signed in, to sign in, and to sign out. /api/health stays open as well -- it
# says only whether the server is up and configured, and it is what you curl
# right after a deploy, before you have a cookie.
OPEN_PATHS = frozenset({"/api/session", "/api/login", "/api/logout", "/api/health"})

# Slows a guessing loop to one try per second per request. It does not stop a
# parallel attack -- a long random password does that. This only makes a
# typo-and-retry by hand indistinguishable from a real delay.
FAILURE_DELAY_S = 1.0


class AuthSettings(BaseSettings):
    """Read from the same .env as the core settings, but kept out of them: the
    pipeline has no business knowing the web layer has a login."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    user: str = Field(default="", alias="LABSAGENT_USER")
    password: str = Field(default="", alias="LABSAGENT_PASSWORD")

    @property
    def enabled(self) -> bool:
        return bool(self.user and self.password)


settings = AuthSettings()
_KEY = secrets.token_bytes(32)


def _sign(payload: str, key: bytes) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def mint(*, now: float | None = None, key: bytes = _KEY) -> str:
    expiry = int((now if now is not None else time.time()) + SESSION_S)
    return f"{expiry}.{_sign(str(expiry), key)}"


def valid(token: str | None, *, now: float | None = None, key: bytes = _KEY) -> bool:
    if not token or "." not in token:
        return False
    expiry, _, signature = token.partition(".")
    # compare_digest, not ==. `==` returns at the first differing character, so
    # how long it takes leaks how much of a guess was right.
    if not hmac.compare_digest(signature, _sign(expiry, key)):
        return False
    try:
        return int(expiry) > (now if now is not None else time.time())
    except ValueError:
        return False


def credentials_match(user: str, password: str, config: AuthSettings | None = None) -> bool:
    config = settings if config is None else config
    # Both compared every time, joined with `&` rather than `and`: a wrong
    # username must cost the same as a wrong password, or the timing tells you
    # which half you got right. Bytes, because compare_digest refuses non-ASCII str.
    user_ok = hmac.compare_digest(user.encode(), config.user.encode())
    password_ok = hmac.compare_digest(password.encode(), config.password.encode())
    return user_ok & password_ok


def signed_in(conn: HTTPConnection, config: AuthSettings | None = None) -> bool:
    config = settings if config is None else config
    return not config.enabled or valid(conn.cookies.get(COOKIE))


class RequireLogin:
    """Pure ASGI middleware, deliberately not `@app.middleware("http")`.

    The decorator form wraps every response in its own stream. That is fine
    for JSON and a known source of trouble for a long-lived SSE response. This
    looks only at the request, then either answers 401 itself or hands the
    call through untouched -- the response never passes through it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # `settings` is looked up per request, not captured at startup, so a
        # test can swap it without rebuilding the app.
        if (
            scope["type"] == "http"
            and settings.enabled
            and scope["path"].startswith("/api/")
            and scope["path"] not in OPEN_PATHS
            and not signed_in(HTTPConnection(scope), settings)
        ):
            response = JSONResponse({"detail": "Sign in first."}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
