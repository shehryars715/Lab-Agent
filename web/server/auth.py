"""Who is asking: accounts, or nobody on your own machine. One signed cookie.

THE MODES, chosen by what is configured (nothing to switch by hand):

  open       nothing set (localhost). No sign-in; every request is user
             "local". Running on your own machine is unchanged.
  supabase   SUPABASE_URL + keys (public sign-up). Accounts through Supabase
             Auth -- email + password with a confirmation email now, Google
             later. However the student signs in, the browser hands us
             Supabase's access token once (POST /api/session); we verify it
             (supa.verify) and set our own cookie carrying the user's id.
  closed     LABSAGENT_REQUIRE_SIGNIN=1 but Supabase is not configured. Every
             /api call is refused. A public server must never fall back to
             "open" because a key went missing from .env.

The single-password login of the private beta was removed on 2026-10-22, once
accounts were live: one way in, not two.

WHY EXCHANGE THE TOKEN FOR OUR OWN COOKIE instead of sending the token on
every call: EventSource, which carries the live run stream, cannot set
headers, and a cookie rides along on its own. It also means one check per
request against a key we hold, not a JWT verification per request.

WHAT THE COOKIE IS. Not a session id pointing at server-side state -- there is
no session store. It is `<expiry>.<user id>.<hmac(expiry.user id)>`: the
server can check it minted it, that it has not expired, and who it was for,
with nothing remembered in between. Changing the user id or stretching the
expiry breaks the HMAC.

WHERE THE KEY COMES FROM. `LABSAGENT_SESSION_KEY` when set, so a deploy no
longer signs every user out -- with the public as users, that matters.
Without it, a random key per process (the old behaviour): a restart signs
everyone out, and there is no secret to leak.

WHY THE UI IS NOT BEHIND IT. The built JS/CSS carries no secrets -- everything
worth protecting is data, and data only arrives through /api. So the static
files stay public, the page asks /api/session on load, and shows the sign-in
screen when told to.

WHY SameSite=Lax IS ENOUGH AGAINST CSRF HERE. Lax cookies are not sent on a
cross-site POST, and every route that changes anything is a POST. A hostile
page can link to the app (a top-level GET, cookie sent) but cannot start a run
or upload a file as you.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from labsagent.config import PROJECT_ROOT

from . import supa

COOKIE = "labsagent_session"
SESSION_S = 7 * 24 * 3600

LOCAL_USER = "local"

# The routes a signed-out browser must still reach: to ask whether it is
# signed in, to sign in, and to sign out. /api/health stays open as well -- it
# says only whether the server is up and configured, and it is what you curl
# right after a deploy, before you have a cookie.
OPEN_PATHS = frozenset({"/api/session", "/api/logout", "/api/health"})

# A user id is a Supabase uuid or "local". Anything else in a cookie
# is refused before it is ever used as a key.
_USER_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class AuthSettings(BaseSettings):
    """Read from the same .env as the core settings, but kept out of them: the
    pipeline has no business knowing the web layer has a login."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Set on any public server: with it, a missing Supabase config closes the
    # API instead of opening it to everyone.
    require_signin: bool = Field(default=False, alias="LABSAGENT_REQUIRE_SIGNIN")
    session_key: str = Field(default="", alias="LABSAGENT_SESSION_KEY")



settings = AuthSettings()
_KEY = (
    hashlib.sha256(settings.session_key.encode()).digest()
    if settings.session_key
    else secrets.token_bytes(32)
)


def mode() -> str:
    """"supabase", "closed" or "open" -- looked up per call, so tests can swap."""
    if supa.configured():
        return "supabase"
    if settings.require_signin:
        return "closed"
    return "open"


def _sign(payload: str, key: bytes) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def mint(user_id: str, *, now: float | None = None, key: bytes | None = None) -> str:
    if not _USER_ID.match(user_id):
        raise ValueError("not a user id")
    expiry = int((now if now is not None else time.time()) + SESSION_S)
    payload = f"{expiry}.{user_id}"
    return f"{payload}.{_sign(payload, key or _KEY)}"


def user_of(token: str | None, *, now: float | None = None, key: bytes | None = None) -> str | None:
    """The user a cookie was minted for, or None if it is not one of ours."""
    if not token or token.count(".") != 2:
        return None
    expiry, user_id, signature = token.split(".")
    # compare_digest, not ==. `==` returns at the first differing character, so
    # how long it takes leaks how much of a guess was right.
    if not hmac.compare_digest(signature, _sign(f"{expiry}.{user_id}", key or _KEY)):
        return None
    if not _USER_ID.match(user_id):
        return None
    try:
        if int(expiry) <= (now if now is not None else time.time()):
            return None
    except ValueError:
        return None
    return user_id


def valid(token: str | None, *, now: float | None = None, key: bytes | None = None) -> bool:
    return user_of(token, now=now, key=key) is not None




def current_user(conn: HTTPConnection) -> str | None:
    """Who this request is from, or None when a sign-in is needed first."""
    current = mode()
    if current == "open":
        return LOCAL_USER
    if current == "closed":
        return None
    return user_of(conn.cookies.get(COOKIE))


def signed_in(conn: HTTPConnection) -> bool:
    return current_user(conn) is not None


class RequireLogin:
    """Pure ASGI middleware, deliberately not `@app.middleware("http")`.

    The decorator form wraps every response in its own stream. That is fine
    for JSON and a known source of trouble for a long-lived SSE response. This
    looks only at the request, then either answers 401 itself or hands the
    call through untouched -- the response never passes through it.

    It also records who is asking, in `scope["state"]["user_id"]`, so every
    route reads the user from one place (`request.state.user_id`).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            if mode() == "closed" and scope["path"] != "/api/health":
                response = JSONResponse(
                    {"detail": "Sign-in is not set up on this server yet."}, status_code=503
                )
                await response(scope, receive, send)
                return
            user = current_user(HTTPConnection(scope))
            scope.setdefault("state", {})["user_id"] = user
            if user is None and scope["path"] not in OPEN_PATHS:
                response = JSONResponse({"detail": "Sign in first."}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
