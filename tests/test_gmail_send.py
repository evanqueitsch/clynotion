"""Send-only mailbox connect and one-shot self-test sends. Scheduler stays off."""

from __future__ import annotations

from app.auth import User, issue_token, user_store
from app.clinicians import clinician_store
from app.hr.gmail_oauth import sender_email_allowed
from app.hr.time_mail import MailResult, build_mailer
from app.hr.time_entry import set_clock, time_store
from app.main import app
from app.workspace_directory import (
    clear_directory_tokens,
    gmail_send_record,
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
