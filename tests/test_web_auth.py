"""The single-account sign-in: the cookie, the gate, and what stays open.

Offline, through Starlette's TestClient. Nothing here starts a run -- the gate
answers before any route does, which is the point being tested.
"""

from __future__ import annotations

import pytest

from web.server import auth

KEY = b"k" * 32


# --- the cookie itself --------------------------------------------------------


def test_a_minted_token_is_valid_until_it_expires():
    token = auth.mint(now=1_000, key=KEY)
    assert auth.valid(token, now=1_000 + auth.SESSION_S - 1, key=KEY)
    assert not auth.valid(token, now=1_000 + auth.SESSION_S, key=KEY)


def test_stretching_the_expiry_breaks_the_signature():
    token = auth.mint(now=1_000, key=KEY)
    _, _, signature = token.partition(".")
    forged = f"{10**12}.{signature}"
    assert not auth.valid(forged, now=2_000, key=KEY)


def test_a_token_from_another_key_is_refused():
    """Which is also what a restart does: a new key, everyone signed out."""
    token = auth.mint(now=1_000, key=KEY)
    assert not auth.valid(token, now=1_000, key=b"x" * 32)


@pytest.mark.parametrize("junk", [None, "", "nodot", "abc.def", ".", "12.", ".sig"])
def test_malformed_tokens_are_refused_not_raised(junk):
    assert not auth.valid(junk, now=0, key=KEY)


def test_credentials_must_match_in_both_halves():
    config = auth.AuthSettings(LABSAGENT_USER="reviewer", LABSAGENT_PASSWORD="s3cret")
    assert auth.credentials_match("reviewer", "s3cret", config)
    assert not auth.credentials_match("reviewer", "wrong", config)
    assert not auth.credentials_match("wrong", "s3cret", config)
    # compare_digest refuses non-ASCII str; bytes it accepts.
    assert not auth.credentials_match("rëviewer", "sécret", config)


def test_a_half_set_config_leaves_the_gate_off():
    """Setting only one of the two must not lock you out of your own server."""
    assert not auth.AuthSettings(LABSAGENT_USER="reviewer", LABSAGENT_PASSWORD="").enabled
    assert not auth.AuthSettings(LABSAGENT_USER="", LABSAGENT_PASSWORD="x").enabled


# --- the gate, through the real app -------------------------------------------


@pytest.fixture
def gated(monkeypatch):
    from fastapi.testclient import TestClient

    from web.server import app as app_module

    monkeypatch.setattr(
        auth, "settings", auth.AuthSettings(LABSAGENT_USER="reviewer", LABSAGENT_PASSWORD="s3cret")
    )
    monkeypatch.setattr(auth, "FAILURE_DELAY_S", 0)
    return TestClient(app_module.app)


@pytest.fixture
def open_app(monkeypatch):
    from fastapi.testclient import TestClient

    from web.server import app as app_module

    monkeypatch.setattr(
        auth, "settings", auth.AuthSettings(LABSAGENT_USER="", LABSAGENT_PASSWORD="")
    )
    return TestClient(app_module.app)


def test_with_no_credentials_configured_nothing_changes(open_app):
    assert open_app.get("/api/history").status_code == 200
    assert open_app.get("/api/session").json() == {"required": False, "signed_in": True}


def test_signed_out_api_calls_get_401(gated):
    assert gated.get("/api/history").status_code == 401
    assert gated.get("/api/runs/whatever").status_code == 401
    assert gated.get("/api/docs").status_code == 401


def test_the_sign_in_routes_and_health_stay_reachable(gated):
    assert gated.get("/api/health").status_code == 200
    assert gated.get("/api/session").json() == {"required": True, "signed_in": False}


def test_a_wrong_password_sets_no_cookie(gated):
    res = gated.post("/api/login", json={"username": "reviewer", "password": "nope"})
    assert res.status_code == 401
    assert auth.COOKIE not in res.cookies
    assert gated.get("/api/history").status_code == 401


def test_signing_in_opens_the_api_and_signing_out_closes_it(gated):
    res = gated.post("/api/login", json={"username": "reviewer", "password": "s3cret"})
    assert res.status_code == 200
    header = res.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
    # TestClient speaks plain http, so the cookie must NOT be Secure -- or it
    # would never come back and localhost sign-in would silently loop.
    assert "secure" not in header

    assert gated.get("/api/history").status_code == 200
    assert gated.get("/api/session").json()["signed_in"] is True

    gated.post("/api/logout")
    assert gated.get("/api/history").status_code == 401


def test_a_forged_cookie_does_not_get_in(gated):
    gated.cookies.set(auth.COOKIE, f"{10**12}.{'0' * 64}")
    assert gated.get("/api/history").status_code == 401
