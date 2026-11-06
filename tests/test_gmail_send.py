"""Send-only mailbox connect and one-shot self-test sends. Scheduler stays off."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

from app.auth import COOKIE_NAME, User, issue_token, user_store
from app.clinicians import clinician_store
from app.crypto import generate_key
from app.hr.gmail_oauth import GmailSendError, exchange_gmail_send_code, sender_email_allowed
from app.hr.time_mail import MailResult, build_mailer
from app.hr.time_entry import set_clock, time_store
from app.main import app
from app.workspace_directory import (
    clear_directory_tokens,
    gmail_send_record,
    note_gmail_send_error,
    save_directory_tokens,
    save_gmail_send_tokens,
)
from fastapi.testclient import TestClient
import pytest

from tests.test_time_entry import FRI_1130, WED, WEEK, _enter, _roster, _save_settings, _settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _alice_enabled(client: TestClient) -> User:
    _roster()
    alice = user_store._users["alice"][1]
    dana = clinician_store.get("practice-a", "clin-a-dana")
    assert dana is not None
    dana.email = "alice@example.com"
    dana.display_name = "Alice Example"
    set_clock(WED)
    _save_settings(client, participant_emails=["alice@example.com"])
    return alice


def test_personal_gmail_is_rejected_and_workspace_domain_is_allowed() -> None:
    domains = ["heart4change.org"]
    assert sender_email_allowed("operations@heart4change.org", domains)
    assert not sender_email_allowed("person@gmail.com", domains)
    assert not sender_email_allowed("person@gmail.com", [])


def test_directory_and_send_tokens_survive_each_other(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_WORKSPACE_TOKEN_PATH", str(tmp_path / "tokens.enc"))
    from app.crypto import generate_key

    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())
    save_directory_tokens(
        "practice-a",
        refresh_token="directory-refresh",
        access_token="directory-access",
        email="admin@heart4change.org",
    )
    save_gmail_send_tokens(
        "practice-a",
        refresh_token="send-refresh",
        access_token="send-access",
        email="operations@heart4change.org",
    )
    save_directory_tokens(
        "practice-a",
        refresh_token="directory-refresh-2",
        access_token="directory-access-2",
        email="admin@heart4change.org",
    )
    assert gmail_send_record("practice-a")["refresh_token"] == "send-refresh"
    clear_directory_tokens("practice-a")
    assert gmail_send_record("practice-a")["email"] == "operations@heart4change.org"
    monkeypatch.setenv("ATTUNE_WORKSPACE_DIRECTORY", "live")
    from app.workspace_directory import directory_connection_status

    status = directory_connection_status("practice-a")
    assert status["connected"] is False


def test_self_test_send_is_one_shot_and_leaves_payroll_and_scheduler_alone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = _alice_enabled(client)
    calls: list[tuple[str, str]] = []

    def fake_send(practice_id: str, message) -> MailResult:
        calls.append((message.to, message.subject))
        assert practice_id == "practice-a"
        assert message.subject.startswith("[TEST] ")
        return MailResult(status="sent", detail="ok")

    monkeypatch.setattr("app.hr.gmail_oauth.deliver_self_test_message", fake_send)
    first = client.post(
        "/hr/time/self-test/send",
        headers=_auth(alice),
        json={"kind": "thursday"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["send_result"]["status"] == "sent"
    assert first.json()["send_result"]["to"] == "alice@example.com"
    assert calls == [("alice@example.com", "[TEST] Weekly hours are due tomorrow")]
    second = client.post(
        "/hr/time/self-test/send",
        headers=_auth(alice),
        json={"kind": "thursday"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["send_result"]["duplicate"] is True
    assert len(calls) == 1

    friday = client.post(
        "/hr/time/self-test/send",
        headers=_auth(alice),
        json={"kind": "friday"},
    )
    assert friday.status_code == 200, friday.text
    assert friday.json()["send_result"]["to"] == "alice@example.com"
    assert "today" in friday.json()["friday"]["subject"]

    too_soon = client.post(
        "/hr/time/self-test/send",
        headers=_auth(alice),
        json={"kind": "summary"},
    )
    assert too_soon.status_code == 422

    set_clock(FRI_1130)
    assert _enter(client, alice, 3, 0).status_code == 403
    saved = client.put(
        "/hr/time/self-test",
        headers=_auth(alice),
        json={"hours": 5, "minutes": 1},
    )
    assert saved.status_code == 200, saved.text
    summary = client.post(
        "/hr/time/self-test/send",
        headers=_auth(alice),
        json={"kind": "summary"},
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["send_result"]["to"] == "msmith@heart4change.org"
    assert "Jordan" not in summary.json()["summary"]["body"]
    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_auth(alice)).json()
    alice_row = next(row for row in week["rows"] if row["email"] == "alice@example.com")
    assert alice_row["status"] == "missing"
    assert build_mailer() is None

    stranger = User("user-jordan", "Jordan Lee", "practice-a", "jordan@heart4change.org")
    assert client.post(
        "/hr/time/self-test/send",
        headers=_auth(stranger),
        json={"kind": "thursday"},
    ).status_code == 403


def _id_token(payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"header.{body}.sig"


class _FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    def __enter__(self) -> "_FakeHttp":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def post(self, url: str, data: dict | None = None, headers: dict | None = None) -> _FakeResponse:
        assert "attune-auth-code" not in json.dumps(self.response.json())
        return self.response

    def get(self, url: str, headers: dict | None = None) -> _FakeResponse:
        raise AssertionError("userinfo is not required when the id_token identifies the mailbox")


def _google_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ATTUNE_AUTH", "google")
    monkeypatch.setenv("ATTUNE_JWT_SECRET", "test-jwt-secret-for-gmail-send-32b")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "heart4change.org")
    monkeypatch.setenv("ATTUNE_PUBLIC_BASE_URL", "https://clynotion.com")
    monkeypatch.setenv("ATTUNE_WORKSPACE_TOKEN_PATH", str(tmp_path / "tokens.enc"))
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())


def test_id_token_connects_without_refresh_and_keeps_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Google can omit a refresh token after consent. That must not look disconnected."""
    _google_env(monkeypatch, tmp_path)
    save_directory_tokens(
        "practice-a",
        refresh_token="directory-refresh",
        access_token="directory-access",
        email="admin@heart4change.org",
    )
    token = _id_token(
        {
            "aud": "client-id.apps.googleusercontent.com",
            "sub": "ops-sub",
            "email": "operations@heart4change.org",
            "email_verified": True,
            "hd": "heart4change.org",
        }
    )
    response = _FakeResponse(
        200,
        {
            "access_token": "access-token",
            "expires_in": 3600,
            "id_token": token,
            "scope": "openid email https://www.googleapis.com/auth/gmail.send",
        },
    )
    with patch("app.hr.gmail_oauth.httpx.Client", lambda **kwargs: _FakeHttp(response)):
        granted = exchange_gmail_send_code("attune-auth-code")
    assert granted["email"] == "operations@heart4change.org"
    assert granted["refresh_token"] == ""
    save_gmail_send_tokens("practice-a", **{k: granted[k] for k in ("refresh_token", "access_token", "email")}, expires_in=int(granted["expires_in"]))
    from app.hr.gmail_oauth import connect_status

    status = connect_status("practice-a")
    assert status["connected"] is True
    assert status["sender_email"] == "operations@heart4change.org"
    assert status["scheduled_mail"] == "off"
    assert status["refresh_saved"] is False
    assert "short-lived" in status["status_detail"]
    assert gmail_send_record("practice-a")["access_token"] == "access-token"
    from app.workspace_directory import directory_connection_status

    monkeypatch.setenv("ATTUNE_WORKSPACE_DIRECTORY", "live")
    assert directory_connection_status("practice-a")["connected"] is True


def test_userinfo_failure_without_id_token_is_identity_not_disconnected_silence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _google_env(monkeypatch, tmp_path)

    class UserinfoFail(_FakeHttp):
        def get(self, url: str, headers: dict | None = None) -> _FakeResponse:
            return _FakeResponse(401, {})

    response = _FakeResponse(200, {"access_token": "access-token", "expires_in": 3600})
    with patch("app.hr.gmail_oauth.httpx.Client", lambda **kwargs: UserinfoFail(response)):
        with pytest.raises(GmailSendError) as caught:
            exchange_gmail_send_code("attune-auth-code")
    assert caught.value.code == "gmail_send_identity_failed"


def test_personal_gmail_id_token_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _google_env(monkeypatch, tmp_path)
    token = _id_token(
        {
            "aud": "client-id.apps.googleusercontent.com",
            "sub": "person-sub",
            "email": "person@gmail.com",
            "email_verified": True,
            "hd": "gmail.com",
        }
    )
    response = _FakeResponse(200, {"access_token": "access-token", "id_token": token, "expires_in": 3600})
    with patch("app.hr.gmail_oauth.httpx.Client", lambda **kwargs: _FakeHttp(response)):
        with pytest.raises(GmailSendError) as caught:
            exchange_gmail_send_code("attune-auth-code")
    assert caught.value.code == "gmail_send_domain"


def test_missing_send_scope_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _google_env(monkeypatch, tmp_path)
    token = _id_token(
        {
            "aud": "client-id.apps.googleusercontent.com",
            "sub": "ops-sub",
            "email": "operations@heart4change.org",
            "email_verified": True,
            "hd": "heart4change.org",
        }
    )
    response = _FakeResponse(
        200,
        {"access_token": "access-token", "id_token": token, "scope": "openid email", "expires_in": 3600},
    )
    with patch("app.hr.gmail_oauth.httpx.Client", lambda **kwargs: _FakeHttp(response)):
        with pytest.raises(GmailSendError) as caught:
            exchange_gmail_send_code("attune-auth-code")
    assert caught.value.code == "gmail_send_scope"


def test_callback_records_the_failure_and_leaves_directory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _google_env(monkeypatch, tmp_path)
    save_directory_tokens(
        "practice-a",
        refresh_token="directory-refresh",
        access_token="directory-access",
        email="admin@heart4change.org",
    )
    alice = user_store._users["alice"][1]
    start = client.get(
        "/auth/google/gmail-send/start",
        headers=_auth(alice),
        follow_redirects=False,
    )
    assert start.status_code == 302
    location = start.headers["location"]
    assert "gmail.send" in location
    assert "include_granted_scopes=false" in location
    assert "redirect_uri=https%3A%2F%2Fclynotion.com%2Fauth%2Fgoogle%2Fgmail-send%2Fcallback" in location
    state = start.cookies.get("attune_gmail_send_state")
    assert state
    session = issue_token(alice)
    with patch(
        "app.hr.gmail_oauth.exchange_gmail_send_code",
        side_effect=GmailSendError("gmail_send_identity_failed"),
    ):
        callback = client.get(
            "/auth/google/gmail-send/callback",
            params={"code": "fake-code", "state": state},
            cookies={"attune_gmail_send_state": state, COOKIE_NAME: session},
            follow_redirects=False,
        )
    assert callback.status_code == 302
    assert "gmail_send=identity" in callback.headers["location"]
    assert "#time-self-test" in callback.headers["location"]
    from app.hr.gmail_oauth import connect_status

    status = connect_status("practice-a")
    assert status["connected"] is False
    assert status["last_error"] == "gmail_send_identity_failed"
    assert "verified mailbox" in status["status_detail"]
    assert gmail_send_record("practice-a") == {} or gmail_send_record("practice-a")["refresh_token"] == ""
    from app.workspace_directory import directory_connection_status

    monkeypatch.setenv("ATTUNE_WORKSPACE_DIRECTORY", "live")
    directory = directory_connection_status("practice-a")
    assert directory["connected"] is True
    assert directory["admin_email"] == "admin@heart4change.org"


def test_workspace_policy_error_is_visible_and_not_a_silent_disconnect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _google_env(monkeypatch, tmp_path)
    save_directory_tokens("practice-a", refresh_token="directory-refresh", email="admin@heart4change.org")
    alice = user_store._users["alice"][1]
    from app.google_oauth import make_state
    from app.auth import jwt_signing_secret

    state = make_state(jwt_signing_secret())
    callback = client.get(
        "/auth/google/gmail-send/callback",
        params={"error": "admin_policy_enforced", "state": state},
        cookies={"attune_gmail_send_state": state, COOKIE_NAME: issue_token(alice)},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert "gmail_send=policy" in callback.headers["location"]
    from app.hr.gmail_oauth import connect_status

    status = connect_status("practice-a")
    assert status["connected"] is False
    assert status["last_error"] == "gmail_send_policy"
    assert "Workspace policy" in status["status_detail"]
    from app.workspace_directory import directory_connection_status

    monkeypatch.setenv("ATTUNE_WORKSPACE_DIRECTORY", "live")
    assert directory_connection_status("practice-a")["connected"] is True


def test_connected_self_test_stays_sendable_while_scheduled_mail_is_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _google_env(monkeypatch, tmp_path)
    alice = _alice_enabled(client)
    save_gmail_send_tokens(
        "practice-a",
        refresh_token="send-refresh",
        access_token="send-access",
        email="operations@heart4change.org",
    )
    view = client.get("/hr/time/self-test", headers=_auth(alice))
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["scheduled_mail"] == "off"
    assert body["mailbox"]["connected"] is True
    assert body["mailbox"]["sender_email"] == "operations@heart4change.org"
    assert body["mailbox"]["scheduled_mail"] == "off"
    assert body["thursday"]["delivery"]["can_send"] is True
    assert body["friday"]["delivery"]["can_send"] is True
    assert build_mailer() is None


def test_expired_access_token_without_refresh_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _google_env(monkeypatch, tmp_path)
    monkeypatch.setattr("app.workspace_directory.time.time", lambda: 1_000.0)
    save_gmail_send_tokens(
        "practice-a",
        refresh_token="",
        access_token="short-access",
        email="operations@heart4change.org",
        expires_in=60,
    )
    monkeypatch.setattr("app.workspace_directory.time.time", lambda: 1_200.0)
    from app.hr.gmail_oauth import connect_status

    status = connect_status("practice-a")
    assert status["connected"] is False
    assert status["last_error"] == "gmail_send_access_expired"
    assert "expired" in status["status_detail"].lower()


def test_unreadable_token_file_is_not_wiped_by_an_error_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _google_env(monkeypatch, tmp_path)
    path = tmp_path / "tokens.enc"
    path.write_bytes(b"not-a-fernet-token")
    note_gmail_send_error("practice-a", "gmail_send_state")
    assert path.read_bytes() == b"not-a-fernet-token"


def test_self_test_page_reads_connect_result_before_rewriting_the_url() -> None:
    text = Path("app/static/index.html").read_text(encoding="utf-8")
    start = text.index("async function openTimeSelfTest")
    chunk = text[start:text.index("async function saveTimeSelfTest")]
    assert chunk.index("gmail_send") < chunk.index("replaceState")
    assert "must not disable an explicit connected send" in text
    assert "gmail_send_policy" in text


def test_uncertain_self_test_send_is_not_retried(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = _alice_enabled(client)
    calls = {"n": 0}

    def uncertain(practice_id: str, message) -> MailResult:
        calls["n"] += 1
        return MailResult(status="uncertain", detail="gmail_uncertain")

    monkeypatch.setattr("app.hr.gmail_oauth.deliver_self_test_message", uncertain)
    first = client.post(
        "/hr/time/self-test/send",
        headers=_auth(alice),
        json={"kind": "friday"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["send_result"]["status"] == "uncertain"
    again = client.post(
        "/hr/time/self-test/send",
        headers=_auth(alice),
        json={"kind": "friday"},
    )
    assert again.json()["send_result"]["duplicate"] is True
    assert calls["n"] == 1
