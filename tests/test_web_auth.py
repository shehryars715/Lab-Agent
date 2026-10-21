"""Sign-in: the cookie, the modes (open, closed, supabase), and the token exchange.

Offline, through Starlette's TestClient. Supabase is never contacted: its
tokens are signed here with a locally generated ES256 key, and its database
functions are a dict (see conftest-free fakes below).
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from web.server import auth, supa

KEY = b"k" * 32
URL = "https://proj.supabase.co"
UID = "11111111-2222-3333-4444-555555555555"


# --- the cookie itself --------------------------------------------------------


def test_a_minted_token_names_its_user_until_it_expires():
    token = auth.mint(UID, now=1_000, key=KEY)
    assert auth.user_of(token, now=1_000 + auth.SESSION_S - 1, key=KEY) == UID
    assert auth.user_of(token, now=1_000 + auth.SESSION_S, key=KEY) is None


def test_changing_the_user_or_the_expiry_breaks_the_signature():
    token = auth.mint(UID, now=1_000, key=KEY)
    expiry, _user, signature = token.split(".")
    assert auth.user_of(f"{expiry}.someone-else.{signature}", now=2_000, key=KEY) is None
    assert auth.user_of(f"{10**12}.{UID}.{signature}", now=2_000, key=KEY) is None


def test_a_token_from_another_key_is_refused():
    """Which is also what a restart does without LABSAGENT_SESSION_KEY."""
    token = auth.mint(UID, now=1_000, key=KEY)
    assert auth.user_of(token, now=1_000, key=b"x" * 32) is None


@pytest.mark.parametrize("junk", [None, "", "nodot", "abc.def", "..", "12..", "a.b.c.d"])
def test_malformed_tokens_are_refused_not_raised(junk):
    assert auth.user_of(junk, now=0, key=KEY) is None


def test_a_user_id_with_separators_cannot_be_minted():
    with pytest.raises(ValueError):
        auth.mint("a.b")




# --- fixtures: the three modes -------------------------------------------------


def _no_supabase(monkeypatch):
    monkeypatch.setattr(supa, "settings", supa.SupaSettings(SUPABASE_URL="", SUPABASE_SERVICE_KEY="", SUPABASE_ANON_KEY=""))


@pytest.fixture
def closed(monkeypatch):
    """A public server whose Supabase settings went missing."""
    from fastapi.testclient import TestClient

    from web.server import app as app_module

    _no_supabase(monkeypatch)
    monkeypatch.setattr(auth, "settings", auth.AuthSettings(LABSAGENT_REQUIRE_SIGNIN=True))
    return TestClient(app_module.app)


@pytest.fixture
def open_app(monkeypatch):
    from fastapi.testclient import TestClient

    from web.server import app as app_module

    _no_supabase(monkeypatch)
    monkeypatch.setattr(auth, "settings", auth.AuthSettings(LABSAGENT_REQUIRE_SIGNIN=False))
    return TestClient(app_module.app)


class FakeJWKS:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return type("Key", (), {"key": self.public_key})()


@pytest.fixture
def signer():
    private = ec.generate_private_key(ec.SECP256R1())

    def sign(**overrides):
        now = int(time.time())
        claims = {
            "sub": UID,
            "aud": "authenticated",
            "iss": f"{URL}/auth/v1",
            "exp": now + 3600,
            "iat": now,
            "email": "student@example.com",
            "user_metadata": {"full_name": "Ayesha Student"},
            **overrides,
        }
        return jwt.encode(claims, private, algorithm="ES256", headers={"kid": "k1"})

    sign.public = private.public_key()
    return sign


@pytest.fixture
def google(monkeypatch, signer):
    """Supabase mode, with its JWKS and database functions faked."""
    from fastapi.testclient import TestClient

    from web.server import app as app_module

    monkeypatch.setattr(
        supa, "settings",
        supa.SupaSettings(SUPABASE_URL=URL, SUPABASE_ANON_KEY="anon-public", SUPABASE_SERVICE_KEY="svc-secret"),
    )
    monkeypatch.setattr(supa, "_jwks", lambda base: FakeJWKS(signer.public))
    calls = []
    monkeypatch.setattr(supa, "rpc", lambda name, **args: calls.append((name, args)))
    client = TestClient(app_module.app)
    client.calls = calls
    return client


# --- open and closed modes ------------------------------------------------------


def test_with_nothing_configured_nothing_changes(open_app):
    assert open_app.get("/api/history").status_code == 200
    body = open_app.get("/api/session").json()
    assert body == {"mode": "open", "required": False, "signed_in": True}


def test_a_public_server_without_accounts_is_closed_not_open(closed):
    """The failure this guards: Supabase keys missing from .env must not mean
    'no sign-in at all' on a server anyone can reach."""
    assert closed.get("/api/history").status_code == 503
    assert closed.get("/api/runs/whatever").status_code == 503
    assert closed.get("/api/session").status_code == 503
    assert closed.post("/api/runs", data={"instructions": "Task 1: hi"}).status_code == 503


def test_health_still_answers_on_a_closed_server(closed):
    assert closed.get("/api/health").status_code == 200


def test_the_password_login_is_gone(open_app):
    assert open_app.post("/api/login", json={"username": "a", "password": "b"}).status_code in (404, 405)


def test_a_forged_cookie_does_not_get_in(google):
    google.cookies.set(auth.COOKIE, f"{10**12}.{UID}.{'0' * 64}")
    assert google.get("/api/history").status_code == 401


# --- supabase mode: the Google token exchange ---------------------------------------


def test_the_session_tells_the_browser_how_to_start_google_sign_in(google):
    body = google.get("/api/session").json()
    assert body["mode"] == "supabase" and body["required"] and not body["signed_in"]
    assert body["supabase"] == {"url": URL, "anon_key": "anon-public"}
    assert "svc-secret" not in google.get("/api/session").text


def test_a_valid_token_becomes_a_session_for_that_user(google, signer):
    res = google.post("/api/session", json={"access_token": signer()})
    assert res.status_code == 200
    assert google.get("/api/me").json()["user_id"] == UID
    name, args = google.calls[0]
    assert name == "ensure_user"
    assert args == {"p_user_id": UID, "p_email": "student@example.com", "p_name": "Ayesha Student"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "anon"},                       # not a signed-in user
        {"iss": "https://other.supabase.co/auth/v1"},  # another project
        {"exp": int(time.time()) - 300},       # expired (past the 60 s clock allowance)
    ],
)
def test_tokens_for_the_wrong_audience_project_or_time_are_refused(google, signer, overrides):
    res = google.post("/api/session", json={"access_token": signer(**overrides)})
    assert res.status_code == 401
    assert google.get("/api/me").status_code == 401


def test_a_token_signed_with_another_key_is_refused(google):
    stranger = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        {"sub": UID, "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 60},
        stranger, algorithm="ES256",
    )
    assert google.post("/api/session", json={"access_token": token}).status_code == 401


def test_a_shared_secret_token_is_refused(google):
    """HS256 is not accepted at all: anyone holding a shared secret could mint one."""
    token = jwt.encode(
        {"sub": UID, "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 60},
        "a-shared-secret-of-at-least-32-bytes!!", algorithm="HS256",
    )
    assert google.post("/api/session", json={"access_token": token}).status_code == 401




def test_a_token_from_a_clock_a_little_ahead_is_still_accepted(google, signer):
    """Supabase's clock ahead of ours by a few seconds: iat in our future."""
    ahead = int(time.time()) + 5
    res = google.post("/api/session", json={"access_token": signer(iat=ahead, exp=ahead + 3600)})
    assert res.status_code == 200
