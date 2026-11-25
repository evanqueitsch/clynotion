"""Exclusion screening and the tamper-evident audit chain. Synthetic people only."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.access.store import access_store
from app.auth import User, issue_token
from app.compliance.audit_chain import verify_chain
from app.compliance.masking import mask_diff
from app.compliance.schedule import run_scheduled
from app.compliance.screening import roster, run_screening
from app.compliance.sources import ListFile, ListedPerson, parse_leie, parse_medicheck
from app.compliance.store import compliance_store
from app.credentialing.store import credentialing_store
from app.main import app
from app.platform.due import due_engine

client = TestClient(app)
EASTERN = ZoneInfo("America/New_York")


def _alice() -> dict[str, str]:
    user = User("user-alice", "alice", "practice-a", "alice@example.com")
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _user(email: str, username: str) -> dict[str, str]:
    user = User("user-" + username, username, "practice-a", email)
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _lists(rows: list[ListedPerson], *, sam_ready: bool = True, sam_detail: str = "") -> dict[str, ListFile]:
    leie = ListFile(name="leie", updated_on="2026-11-10", file_sha256="leie-hash", rows=rows)
    sam = ListFile(
        name="sam",
        updated_on="2026-11-25" if sam_ready else "",
        file_sha256="sam-hash" if sam_ready else "",
        rows=[],
        ready=sam_ready,
        detail=sam_detail,
    )
    medicheck = ListFile(name="medicheck", updated_on="2026-11-25", file_sha256="med-hash", rows=[])
    return {"leie": leie, "sam": sam, "medicheck": medicheck}


def test_sources_parse_without_network() -> None:
    leie = parse_leie(
        "LASTNAME,FIRSTNAME,MIDNAME,NPI,EXCLTYPE,STATE\nTESTPERSON,ALPHA,Q,1234567893,1128a1,PA\n",
        updated_on="2026-11-10",
    )
    assert leie.rows[0].last == "TESTPERSON"
    assert leie.file_sha256
    med = parse_medicheck(
        "NAM_LAST_PROVR,NAM_FIRST_PROVR,Status,EndDate,IDN_NPI\n"
        "Testperson,Alpha,Precluded,,\n"
        "Gone,Person,Reinstated,01/01/2020,\n",
        updated_on="2026-11-25",
        today=datetime(2026, 11, 25, tzinfo=EASTERN).date(),
    )
    assert len(med.rows) == 1
    assert med.rows[0].last == "Testperson"
    html = parse_medicheck("<html>form</html>", updated_on="", today=datetime(2026, 11, 25).date())
    assert html.ready is False
    assert html.detail == "manual check required"


def test_masking_hides_sensitive_values() -> None:
    diff = mask_diff(
        {"npi": "1234567893", "ssn": "111-22-3333", "preferred_name": "Testperson Alpha"},
        {"npi": "1234567890", "ssn": "", "preferred_name": "Testperson Alpha"},
    )
    assert "1234567893" not in str(diff)
    assert "111-22-3333" not in str(diff)
    assert diff["npi"]["before"].endswith("7893")
    assert "preferred_name" not in diff


def test_roster_includes_active_clinicians_and_paid_supervisors_not_vendors() -> None:
    credentialing_store.save_subject(
        "practice-a",
        {"name": "Testperson Alpha", "kind": "clinician", "active": True},
        actor="qa",
    )
    credentialing_store.save_subject(
        "practice-a",
        {"name": "Practice Desk", "kind": "practice", "active": True},
        actor="qa",
    )
    compliance_store.save_outside_supervisor("practice-a", {"name": "Outside Beta", "paid": True, "active": True})
    people = roster("practice-a")
    names = {item["name"] for item in people}
    assert "Testperson Alpha" in names
    assert "Outside Beta" in names
    assert "Practice Desk" not in names
    vendor_id = "sup-vendor"
    compliance_store._memory["practices"]["practice-a"]["outside_supervisors"].append(
        {"id": vendor_id, "name": "Vendor Gamma", "paid": True, "active": True, "kind": "vendor"}
    )
    assert "Vendor Gamma" not in {item["name"] for item in roster("practice-a")}


def test_screening_is_append_only_and_sam_missing_is_not_a_pass() -> None:
    credentialing_store.save_subject(
        "practice-a",
        {"name": "Testperson Alpha", "kind": "clinician", "active": True},
        actor="qa",
    )
    person_id = next(item["person_id"] for item in roster("practice-a") if item["name"] == "Testperson Alpha")
    first = run_screening(
        "practice-a",
        "qa",
        person_ids=[person_id],
        lists=_lists([], sam_ready=False, sam_detail="not run: API key not configured"),
    )
    assert {item["result"] for item in first["results"] if item["list_name"] == "leie"} == {"clear"}
    assert {item["result"] for item in first["results"] if item["list_name"] == "sam"} == {"not_run"}
    second = run_screening(
        "practice-a",
        "qa",
        person_ids=[person_id],
        lists=_lists([ListedPerson(last="Alpha", first="Testperson", kind="1128a1")], sam_ready=True),
    )
    stored = compliance_store.screenings("practice-a")
    assert len(stored) == 6
    assert stored[0]["result"] == "clear"
    assert any(item["result"] == "possible_match" and item["list_name"] == "leie" for item in second["results"])
    home = [item for item in due_engine.list_open("practice-a") if item.source == "exclusion-review"]
    assert home
    assert home[0].title == "Review a possible exclusion match"


def test_match_review_home_task_and_activate_gate() -> None:
    created = client.post(
        "/hiring/offers",
        headers=_alice(),
        json={
            "display_name": "Testperson Alpha",
            "personal_email": "alpha.testperson@example.com",
            "employment_type": "full-time",
            "candidate_full_name": "Testperson Alpha",
            "candidate_first_name": "Testperson",
            "supervisor_name": "Alex Example",
            "hourly_rate": "$45.00",
            "caseload_range": "25-30",
            "qsehra_monthly_amount": "$250",
            "qsehra_wait_days": "60",
            "offer_date": "11/25/2026",
            "start_date": "11/01/2026",
            "response_deadline": "11/25/2026",
            "direct_client_access": False,
        },
    )
    assert created.status_code == 200, created.text
    offer_id = created.json()["id"]
    assert client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"}).status_code == 200
    accepted = client.post(f"/hiring/offers/{offer_id}/accept", headers=_alice(), json={"typed_name": "Testperson Alpha"})
    hire_id = accepted.json()["hire"]["id"]
    assert client.post(f"/hiring/hires/{hire_id}/qb-invite", headers=_alice(), json={"sent": True}).status_code == 200
    blocked = client.post(
        f"/hiring/hires/{hire_id}/activate",
        headers=_alice(),
        json={"work_email": "alpha.testperson@example.com"},
    )
    assert blocked.status_code == 409
    assert "Exclusion check" in blocked.json()["detail"]
    run_screening(
        "practice-a",
        "qa",
        person_ids=[f"hire:{hire_id}"],
        lists=_lists([ListedPerson(last="Alpha", first="Testperson")], sam_ready=True),
    )
    status = client.get(f"/compliance/screening/hire/{hire_id}", headers=_alice())
    assert status.status_code == 200
    assert status.json()["allowed"] is False
    match = next(item for item in compliance_store.screenings("practice-a") if item["result"] == "possible_match")
    resolved = client.post(
        f"/compliance/reviews/{match['id']}",
        headers=_alice(),
        json={"decision": "not_the_same_person", "note": "Different person."},
    )
    assert resolved.status_code == 200, resolved.text
    assert not any(item.source_ref == match["id"] for item in due_engine.list_open("practice-a"))
    still = client.get(f"/compliance/screening/hire/{hire_id}", headers=_alice()).json()
    assert still["lists"]["leie"]["state"] == "clear"
    assert still["allowed"] is True
    marked = client.post(
        f"/hiring/hires/{hire_id}/items/exclusion_screening",
        headers=_alice(),
        json={"status": "done"},
    )
    assert marked.status_code == 200, marked.text
    access_store.set_assignment("practice-a", "billing.example@example.com", "billing")
    billing = _user("billing.example@example.com", "billing-example")
    assert client.get(f"/compliance/screening/hire/{hire_id}", headers=billing).status_code == 200
    denied = client.post(
        f"/compliance/reviews/{match['id']}",
        headers=billing,
        json={"decision": "confirmed_exclusion", "note": "no"},
    )
    assert denied.status_code == 403


def test_confirmed_exclusion_banner_and_manual_medicheck() -> None:
    credentialing_store.save_subject(
        "practice-a",
        {"name": "Testperson Alpha", "kind": "clinician", "active": True},
        actor="qa",
    )
    person_id = next(item["person_id"] for item in roster("practice-a") if item["name"] == "Testperson Alpha")
    run_screening(
        "practice-a",
        "qa",
        person_ids=[person_id],
        lists=_lists([ListedPerson(last="Alpha", first="Testperson")], sam_ready=False, sam_detail="not run: API key not configured"),
    )
    run_screening(
        "practice-a",
        "qa",
        person_ids=[person_id],
        lists=_lists([], sam_ready=True),
    )
    # The second run cleared SAM and Medicheck. LEIE's latest row is clear.
    # Force one LEIE possible match and confirm it.
    match_run = run_screening(
        "practice-a",
        "qa",
        person_ids=[person_id],
        lists=_lists([ListedPerson(last="Alpha", first="Testperson")], sam_ready=True),
    )
    match = next(item for item in match_run["results"] if item["result"] == "possible_match")
    confirmed = client.post(
        f"/compliance/reviews/{match['id']}",
        headers=_alice(),
        json={"decision": "confirmed_exclusion", "note": "Same person."},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["gate"]["banner"] == "Confirmed exclusion"
    assert confirmed.json()["gate"]["allowed"] is False


def test_schedule_stays_off_and_settings_do_not_run_a_job() -> None:
    health = client.get("/health").json()
    assert health["version"] == "0.29.2"
    assert health["exclusion_schedule"] == "off"
    assert health["audit_drive"] == "not_configured"
    settings = client.get("/compliance/settings", headers=_alice()).json()
    assert settings["monthly_screening_enabled"] is False
    assert settings["drive_status"] == "Not configured"
    assert settings["sam_status"].startswith("not run")
    moment = datetime(2026, 11, 2, 7, 0, tzinfo=EASTERN)
    called = {"uploader": 0}

    def uploader(folder_id: str, filename: str, data: bytes) -> str:
        called["uploader"] += 1
        return "file"

    summary = run_scheduled(moment, lists=_lists([ListedPerson(last="Alpha", first="Testperson")]), uploader=uploader)
    assert summary["screening"] == "off"
    assert summary["drive"] == "not_configured"
    assert called["uploader"] == 0
    assert compliance_store.screenings("practice-a") == []
    saved = client.put(
        "/compliance/settings",
        headers=_alice(),
        json={"monthly_screening_enabled": False, "integrity_check_enabled": False},
    )
    assert saved.status_code == 200
    assert saved.json()["monthly_screening_enabled"] is False


def test_audit_chain_genesis_verify_and_rejects_edits() -> None:
    credentialing_store.save_subject(
        "practice-a",
        {"name": "Testperson Alpha", "kind": "clinician", "active": True},
        actor="qa",
    )
    login = client.post("/auth/token", json={"username": "alice", "password": "alice-pass"})
    assert login.status_code == 200
    failed = client.post("/auth/token", json={"username": "alice", "password": "wrong"})
    assert failed.status_code == 401
    entries = compliance_store.audit_entries("practice-a")
    assert entries[0]["action"] == "genesis"
    assert entries[0]["prev_hash"] == "0" * 64
    assert entries[0]["diff"]["legacy_hash"]
    checked = client.post("/compliance/audit/verify", headers=_alice())
    assert checked.status_code == 200, checked.text
    assert checked.json()["ok"] is True
    before = len(compliance_store.audit_entries("practice-a"))
    for method in ("put", "patch", "delete"):
        response = getattr(client, method)("/compliance/audit/1", headers=_alice())
        assert response.status_code == 405
        response = getattr(client, method)("/compliance/screening/scr-1", headers=_alice())
        assert response.status_code == 405
    assert len(compliance_store.audit_entries("practice-a")) == before
    assert not hasattr(compliance_store, "delete_audit")
    row = compliance_store._memory["practices"]["practice-a"]["audit"][1]
    row["action"] = "tampered"
    broken = verify_chain("practice-a")
    assert broken["ok"] is False
    assert any(item.source == "audit-integrity" for item in due_engine.list_open("practice-a"))


def test_test_hire_can_be_screened_without_email_and_activate_stays_blocked() -> None:
    from app.hiring.mailer import clear_capture_for_tests, set_capture_for_tests
    from app.hr.time_mail import RecordingMailer

    box = RecordingMailer()
    set_capture_for_tests(box)
    try:
        created = client.post("/compliance/test-hires", headers=_alice())
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["test_record"] is True
        assert body["display_name"].startswith("TEST ")
        assert body["exclusion_allowed"] is False
        assert "leie has not been run" in body["exclusion_message"]
        assert "sam has not been run" in body["exclusion_message"]
        assert "medicheck has not been run" in body["exclusion_message"]
        hire_id = body["id"]
        assert box.sent == []
        roster_body = client.get("/compliance/roster", headers=_alice()).json()
        assert all(item["person_id"] != f"hire:{hire_id}" for item in roster_body["people"])
        assert any(item["person_id"] == f"hire:{hire_id}" for item in roster_body["test_people"])
        billing = client.get("/hiring/bootstrap", headers=_alice()).json()
        assert any(item["id"] == hire_id and item["test_record"] for item in billing["hires"])
        real = credentialing_store.save_subject(
            "practice-a",
            {"name": "Testperson Alpha", "kind": "clinician", "active": True},
            actor="qa",
        )
        denied = client.post(
            "/compliance/screening/fixture",
            headers=_alice(),
            json={
                "person_id": "subject:" + real["id"],
                "list_name": "leie",
                "csv_text": "LASTNAME,FIRSTNAME\nAlpha,Testperson\n",
            },
        )
        assert denied.status_code == 403
        blocked = client.post(
            f"/hiring/hires/{hire_id}/activate",
            headers=_alice(),
            json={"work_email": "test.record@example.test"},
        )
        assert blocked.status_code == 409
        assert "Exclusion check" in blocked.json()["detail"]
        run_screening(
            "practice-a",
            "qa",
            person_ids=[f"hire:{hire_id}"],
            lists=_lists([], sam_ready=False, sam_detail="not run: API key not configured"),
        )
        still = client.post(
            f"/hiring/hires/{hire_id}/activate",
            headers=_alice(),
            json={"work_email": "test.record@example.test"},
        )
        assert still.status_code == 409
        assert "sam" in still.json()["detail"]
        run_screening(
            "practice-a",
            "qa",
            person_ids=[f"hire:{hire_id}"],
            lists=_lists([], sam_ready=True),
        )
        allowed = client.post(
            f"/hiring/hires/{hire_id}/activate",
            headers=_alice(),
            json={"work_email": "test.record@example.test"},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["status"] == "active"
        names = {item["name"] for item in credentialing_store.list_subject_names("practice-a")}
        assert "TEST Testperson Alpha" not in names
        assert all(not str(item).startswith("test.record") for item in names)
        page = open("app/static/hiring.js", encoding="utf-8").read()
        assert "hire.exclusion_allowed === false" in page
        html = open("app/static/index.html", encoding="utf-8").read()
        boot = html.split("async function bootstrapAuth", 1)[1].split("function show(", 1)[0]
        assert boot.index("else if (wantScreening)") < boot.rindex("await enterHome()")
        assert boot.index("else if (wantAudit)") < boot.rindex("await enterHome()")
    finally:
        clear_capture_for_tests()


def test_login_role_verify_actor_and_audit_csv_includes_the_export() -> None:
    client.post("/compliance/audit/verify", headers=_alice())
    fresh = client.post("/auth/token", json={"username": "alice", "password": "alice-pass"})
    assert fresh.status_code == 200, fresh.text
    login = next(item for item in reversed(compliance_store.audit_entries("practice-a")) if item["action"] == "login")
    assert login["actor"] == "alice@example.com"
    assert login["actor_role"] == "admin"
    assert login["seq"] > 1
    verified = client.post("/compliance/audit/verify", headers=_alice())
    assert verified.status_code == 200, verified.text
    verify_row = next(item for item in reversed(compliance_store.audit_entries("practice-a")) if item["action"] == "verify_log")
    assert verify_row["actor"] == "alice@example.com"
    assert verify_row["actor_role"] == "admin"
    exported = client.get("/compliance/audit/export.csv", headers=_alice())
    assert exported.status_code == 200
    assert ",export," in exported.text
    settings = client.get("/compliance/settings", headers=_alice()).json()
    sentence = "Off-box audit copy: " + settings["drive_status"] + ". " + settings["drive_detail"]
    assert sentence.count("Not configured") == 1


def test_record_test_match_opens_a_home_item_and_delete_clears_it() -> None:
    created = client.post("/compliance/test-hires", headers=_alice())
    assert created.status_code == 200, created.text
    hire = created.json()
    hire_id = hire["id"]
    exclusion = next(item for item in hire["items"] if item["template_key"] == "exclusion_screening")
    assert exclusion["due_date"] == hire["start_date"]
    assert exclusion["due_date"] >= hire["created_on"]
    matched = client.post(
        "/compliance/screening/fixture",
        headers=_alice(),
        json={"person_id": f"hire:{hire_id}", "csv_text": "not,a,real,list"},
    )
    assert matched.status_code == 200, matched.text
    assert matched.json()["result"] == "possible_match"
    assert matched.json()["detail"] == "TEST possible match"
    assert matched.json()["matches"][0]["kind"] == "TEST"
    home = client.get("/home", headers=_alice()).json()
    open_items = [item for band in ("overdue", "this_week") for item in home["bands"][band]]
    assert any(item["source_ref"] == matched.json()["id"] for item in open_items)
    blocked = client.post(
        f"/hiring/hires/{hire_id}/activate",
        headers=_alice(),
        json={"work_email": "test.record@example.test"},
    )
    assert blocked.status_code == 409
    assert "TEST possible match" in blocked.json()["detail"]
    email = hire["personal_email"]
    removed = client.post(
        f"/hiring/hires/{hire_id}/delete",
        headers=_alice(),
        json={"confirm": hire["display_name"]},
    )
    assert removed.status_code == 200, removed.text
    after = client.get("/home", headers=_alice()).json()
    after_items = [item for band in ("overdue", "this_week") for item in after["bands"][band]]
    assert all(item["source_ref"] != matched.json()["id"] for item in after_items)
    kept = client.get("/compliance/screening?include_test=1", headers=_alice()).json()["results"]
    assert any(item["person_id"] == f"hire:{hire_id}" and item["result"] == "possible_match" for item in kept)
    deleted = [
        item
        for item in compliance_store.audit_entries("practice-a")
        if item["action"] == "deleted" and item["object_id"] == hire_id
    ]
    assert deleted
    assert email not in str(deleted[-1]["diff"])
    assert "••••" in str(deleted[-1]["diff"])
    script = open("app/static/denials.js", encoding="utf-8").read()
    assert 'window.location.hash = "#settings"' in script
    assert "openSettings();" in script


def test_exports_are_logged_and_practice_owner_can_read() -> None:
    client.post("/auth/token", json={"username": "alice", "password": "alice-pass"})
    csv_export = client.get("/compliance/screening/export.csv", headers=_alice())
    assert csv_export.status_code == 200
    assert "list" in csv_export.text
    pdf_export = client.get("/compliance/audit/export.pdf", headers=_alice())
    assert pdf_export.status_code == 200
    assert pdf_export.content.startswith(b"%PDF")
    actions = [item["action"] for item in compliance_store.audit_entries("practice-a")]
    assert actions.count("export") >= 2
    access_store.set_assignment("practice-a", "owner.example@example.com", "practice_owner")
    owner = _user("owner.example@example.com", "owner-example")
    viewed = client.get("/compliance/audit", headers=owner)
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["read_only"] is True
    assert client.post("/compliance/audit/verify", headers=owner).status_code == 403
    assert client.get("/compliance/screening", headers=owner).status_code == 403
