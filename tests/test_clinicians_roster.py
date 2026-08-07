"""Phase 1b: practice roster, who's present, voice enroll stub."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audit import audit_log
from app.auth import issue_token, user_store
from app.clinicians import clinician_store
from app.main import app
from app.pipeline import draft_from_transcript
from app.roster import reconcile_with_roster
from app.schemas import SupervisionFields
from app.store import store

SAMPLE = Path("sample_supervision_transcript.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean():
    audit_log.clear()
    store.clear()
    clinician_store.reset()
    yield
    audit_log.clear()
    store.clear()
    clinician_store.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(username: str) -> dict[str, str]:
    _, user = user_store._users[username]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_list_clinicians_scoped_to_practice(client: TestClient) -> None:
    r = client.get("/clinicians", headers=_auth("alice"))
    assert r.status_code == 200
    names = {c["display_name"] for c in r.json()}
    assert "Dana Okonkwo" in names
    assert "Morgan Blake" not in names

    r2 = client.get("/clinicians", headers=_auth("bob"))
    names_b = {c["display_name"] for c in r2.json()}
    assert "Morgan Blake" in names_b
    assert "Dana Okonkwo" not in names_b


def test_voice_enroll_stub_deletes_sample_and_audits(client: TestClient, tmp_path: Path) -> None:
    sample = tmp_path / "enroll.wav"
    sample.write_bytes(b"RIFF....WAVEfmt fake")
    r = client.post(
        "/clinicians/clin-a-dana/voice-enroll",
        headers=_auth("alice"),
        files={"audio": ("enroll.wav", sample.read_bytes(), "audio/wav")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voice_status"] == "enrolled"
    assert body["voice_enrolled"] is True
    assert body["voice_sample_bytes"] > 0
    actions = [e["action"] for e in audit_log.events()]
    assert "voice_profile_enrolled" in actions
    # No display names / filenames in audit (IDs only)
    for e in audit_log.events():
        blob = str(e).lower()
        assert "okonkwo" not in blob
        assert "enroll.wav" not in blob


def test_meeting_bot_not_available(client: TestClient) -> None:
    r = client.get("/capture/meeting-bot", headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json()["available"] is False

    bad = client.post(
        "/sessions/draft/json",
        headers=_auth("alice"),
        json={
            "transcript": SAMPLE,
            "capture_mode": "meeting_bot",
            "present": [{"clinician_id": "clin-a-dana", "role": "supervisor"}],
        },
    )
    assert bad.status_code == 503


def test_draft_with_present_seeds_speaker_map(client: TestClient) -> None:
    r = client.post(
        "/sessions/draft/json",
        headers=_auth("alice"),
        json={
            "transcript": SAMPLE,
            "present": [
                {"clinician_id": "clin-a-dana", "role": "supervisor"},
                {"clinician_id": "clin-a-jordan", "role": "supervisee"},
                {"clinician_id": "clin-a-sam", "role": "supervisee"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["capture_mode"] == "session_surface"
    assert len(data["present"]) == 3
    sm = data["fields"]["speaker_map"]
    assert "Dana Okonkwo" in sm.values()
    assert "Jordan Lee" in sm.values()
    assert "Sam Rivera" in sm.values()
    assert "Dana Okonkwo" in data["note"]


def test_reconcile_prefers_roster_name_match() -> None:
    fields = SupervisionFields.model_validate(
        {
            "supervisor": "Dana",
            "participants": [
                {"speaker_label": "Speaker 0", "name": "Dana", "role": "supervisor"},
                {"speaker_label": "Speaker 1", "name": "Speaker 1", "role": "other"},
            ],
            "speaker_map": {"Speaker 0": "Dana"},
            "guidance_given": "x",
            "supervisee_reflections": "y",
            "competency_focus": "z",
            "risk_ethics_flags": "",
            "plan_next": "n",
            "evidence": {},
        }
    )
    present = clinician_store.resolve_present(
        "practice-a",
        [
            ("clin-a-dana", "supervisor"),
            ("clin-a-jordan", "supervisee"),
        ],
    )
    merged = reconcile_with_roster(fields, present)
    assert merged.speaker_map["Speaker 0"] == "Dana Okonkwo"
    assert merged.speaker_map["Speaker 1"] == "Jordan Lee"
    assert merged.supervisor == "Dana Okonkwo"


def test_pipeline_present_optional_still_works() -> None:
    session = draft_from_transcript(SAMPLE, practice_id="practice-a")
    assert session.fields.supervisor == "Dana Okonkwo"
    assert session.present == []
