"""Google Workspace SSO gate — domain allowlist + password disabled in google mode."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth import COOKIE_NAME, issue_token, user_from_google
from app.google_oauth import GoogleIdentity, make_state, verify_state
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_auth_config_dev_by_default(client: TestClient) -> None:
    r = client.get("/auth/config")
    assert r.status_code == 200
    body = r.json()
    assert body["auth"] == "dev"
    assert body["password_login"] is True
    assert body["google_login_url"] is None


def test_env_secret_quote_stripping(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.google_oauth import _client_id, _client_secret, redirect_uri

    monkeypatch.setenv("GOOGLE_CLIENT_ID", '"abc.apps.googleusercontent.com"')
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "'GOCSPX-test'")
    monkeypatch.setenv("ATTUNE_PUBLIC_BASE_URL", '"https://clynotion.fly.dev"')
    assert _client_id() == "abc.apps.googleusercontent.com"
    assert _client_secret() == "GOCSPX-test"
    assert redirect_uri() == "https://clynotion.fly.dev/auth/google/callback"


def test_identity_from_id_token_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    import json

    from app.google_oauth import _claims_from_id_token, _identity_from_claims

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "hfc.example")
    payload = {
        "sub": "99",
        "email": "pat@hfc.example",
        "email_verified": "true",
        "name": "Pat",
        "hd": "hfc.example",
        "aud": "client.apps.googleusercontent.com",
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"hdr.{body}.sig"
    claims = _claims_from_id_token(token)
    ident = _identity_from_claims(claims)
    assert ident.email == "pat@hfc.example"
    assert ident.sub == "99"


def test_password_login_works_in_dev(client: TestClient) -> None:
    r = client.post("/auth/token", json={"username": "alice", "password": "alice-pass"})
    assert r.status_code == 200
    assert r.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


def test_oauth_state_roundtrip() -> None:
    secret = "test-secret"
    state = make_state(secret)
    assert verify_state(state, secret)
    assert not verify_state(state, "other")
    assert not verify_state("bad.state", secret)


def test_google_user_maps_to_default_practice() -> None:
    user = user_from_google(sub="123", email="clinician@hfc.example", name="Pat Clinician")
    assert user.user_id == "google:123"
    assert user.email == "clinician@hfc.example"
    assert user.practice_id == "practice-hfc"


def test_google_mode_disables_password_and_completes_callback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATTUNE_AUTH", "google")
    monkeypatch.setenv("ATTUNE_JWT_SECRET", "test-jwt-secret-for-google-mode-32b")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "hfc.example")
    monkeypatch.setenv("ATTUNE_PUBLIC_BASE_URL", "https://clynotion.fly.dev")
    monkeypatch.setenv("ATTUNE_DEFAULT_PRACTICE_ID", "practice-hfc")

    denied = client.post(
        "/auth/token", json={"username": "alice", "password": "alice-pass"}
    )
    assert denied.status_code == 403

    cfg = client.get("/auth/config").json()
    assert cfg["auth"] == "google"
    assert cfg["password_login"] is False
    assert cfg["google_login_url"] == "/auth/google/start"
    assert cfg["redirect_uri"] == "https://clynotion.fly.dev/auth/google/callback"
    assert cfg["allowed_domains"] == ["hfc.example"]
    assert cfg["client_secret_set"] is True

    start = client.get("/auth/google/start", follow_redirects=False)
    assert start.status_code == 302
    loc = start.headers["location"]
    assert "accounts.google.com" in loc
    assert "hd=hfc.example" in loc
    state_cookie = start.cookies.get("attune_oauth_state")
    assert state_cookie

    identity = GoogleIdentity(
        sub="g-sub-1",
        email="pat@hfc.example",
        name="Pat",
        hd="hfc.example",
    )
    with patch("app.main.exchange_code", return_value=identity):
        cb = client.get(
            "/auth/google/callback",
            params={"code": "fake-code", "state": state_cookie},
            cookies={"attune_oauth_state": state_cookie},
            follow_redirects=False,
        )
    assert cb.status_code == 302
    assert cb.headers["location"] == "/"
    assert COOKIE_NAME in cb.cookies
    token = cb.cookies.get(COOKIE_NAME)
    assert token
    me = client.get("/auth/me", cookies={COOKIE_NAME: token})
    assert me.status_code == 200
    assert me.json()["email"] == "pat@hfc.example"
    assert me.json()["practice_id"] == "practice-hfc"


def test_google_callback_rejects_other_domain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATTUNE_AUTH", "google")
    monkeypatch.setenv("ATTUNE_JWT_SECRET", "test-jwt-secret-for-google-mode-32b")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "hfc.example")
    monkeypatch.setenv("ATTUNE_PUBLIC_BASE_URL", "https://clynotion.fly.dev")

    from app.auth import jwt_signing_secret

    state = make_state(jwt_signing_secret())
    with patch(
        "app.main.exchange_code",
        side_effect=PermissionError("google_domain_not_allowed"),
    ):
        cb = client.get(
            "/auth/google/callback",
            params={"code": "x", "state": state},
            cookies={"attune_oauth_state": state},
            follow_redirects=False,
        )
    assert cb.status_code == 302
    assert "auth_error=google_domain" in cb.headers["location"]


def test_cookie_auth_allows_api(client: TestClient) -> None:
    from app.auth import user_store

    _, user = user_store._users["alice"]
    token = issue_token(user)
    r = client.get("/clinicians", cookies={COOKIE_NAME: token})
    assert r.status_code == 200
    assert len(r.json()) >= 1
