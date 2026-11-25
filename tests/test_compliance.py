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
    assert len(home) == 1
    assert home[0].source_ref == person_id
    assert home[0].title.startswith("Possible exclusion match:")


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
    assert health["version"] == "0.30.2"
    assert health["exclusion_schedule"] == "off"
    assert health["audit_drive"] == "not_configured"
    assert health["sam"] == "not_configured"
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
        shown = next(item for item in roster_body["test_people"] if item["person_id"] == f"hire:{hire_id}")
        assert shown["created_on"] == body["created_on"]
        script = open("app/static/compliance.js", encoding="utf-8").read()
        screen_at = script.index("Screen this test record")
        screen_block = script[screen_at:screen_at + 700]
        assert "pendingTestId = testPicker.value" in screen_block
        assert 'bits.push(item.person_id)' in script
        assert 'bits.push("created "' in script
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
    clearances = next(item for item in hire["items"] if item["template_key"] == "clearances")
    assert exclusion["due_date"] == hire["start_date"]
    assert clearances["due_date"] == hire["start_date"]
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
    assert matched.json()["person_id"] == f"hire:{hire_id}"
    assert matched.json()["created_on"] == hire["created_on"]
    home = client.get("/home", headers=_alice()).json()
    open_items = [item for band in ("overdue", "this_week") for item in home["bands"][band]]
    refs = {item.get("source_ref") for item in open_items if item.get("source") == "exclusion-review"}
    assert matched.json()["id"] not in refs
    assert f"hire:{hire_id}" not in refs
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
    kept_row = next(item for item in kept if item["person_id"] == f"hire:{hire_id}" and item["result"] == "possible_match")
    assert kept_row["detail"] == "TEST possible match"
    stored = next(item for item in compliance_store.screenings("practice-a") if item["id"] == matched.json()["id"])
    assert stored["result"] == "possible_match"
    assert stored["detail"] == "TEST possible match"
    closed = [
        item
        for item in compliance_store.resolutions("practice-a")
        if item.get("result_id") == matched.json()["id"]
    ]
    assert closed[-1]["decision"] == "closed_test_record"
    close_rows = [item for item in compliance_store.audit_entries("practice-a") if item["action"] == "close_test_match"]
    assert close_rows[-1]["diff"] == {"closed": 1}
    assert "TEST possible match" not in str(close_rows[-1]["diff"])
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


def test_health_sam_status_does_not_reveal_the_key(monkeypatch) -> None:
    secret = "not-a-real-sam-key-value"
    monkeypatch.setenv("SAM_API_KEY", secret)
    body = client.get("/health")
    assert body.json()["sam"] == "configured"
    assert secret not in body.text
    settings = client.get("/compliance/settings", headers=_alice())
    assert settings.json()["sam_status"] == "configured"
    assert secret not in settings.text


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


def test_orphaned_test_match_closes_on_history_and_leaves_a_real_decision() -> None:
    from app.hiring.engine import hiring

    credentialing_store.save_subject(
        "practice-a",
        {"name": "Testperson Alpha", "kind": "clinician", "active": True},
        actor="qa",
    )
    person_id = next(item["person_id"] for item in roster("practice-a") if item["name"] == "Testperson Alpha")
    real = run_screening(
        "practice-a",
        "qa",
        person_ids=[person_id],
        lists=_lists([ListedPerson(last="Alpha", first="Testperson", kind="1128a1")]),
    )
    real_id = next(item["id"] for item in real["results"] if item["result"] == "possible_match")
    resolved = client.post(
        f"/compliance/reviews/{real_id}",
        headers=_alice(),
        json={"decision": "not_the_same_person", "note": "Synthetic names only"},
    )
    assert resolved.status_code == 200, resolved.text
    created = client.post("/compliance/test-hires", headers=_alice())
    hire_id = created.json()["id"]
    matched = client.post(
        "/compliance/screening/fixture",
        headers=_alice(),
        json={"person_id": f"hire:{hire_id}"},
    )
    assert matched.status_code == 200, matched.text
    payload = hiring._read()
    hiring._practice(payload, "practice-a")["hires"].pop(hire_id)
    hiring._save(payload)
    listed = client.get("/compliance/screening?include_test=1", headers=_alice()).json()
    closed = [item for item in listed["resolutions"] if item.get("result_id") == matched.json()["id"]]
    assert closed[-1]["decision"] == "closed_test_record"
    again = client.get("/compliance/screening?include_test=1", headers=_alice()).json()
    assert sum(1 for item in again["resolutions"] if item.get("result_id") == matched.json()["id"]) == 1
    real_decisions = [item for item in again["resolutions"] if item.get("result_id") == real_id]
    assert real_decisions[-1]["decision"] == "not_the_same_person"
    assert real_decisions[-1]["note"] == "Synthetic names only"
    real_row = next(item for item in compliance_store.screenings("practice-a") if item["id"] == real_id)
    assert real_row["result"] == "possible_match"


def test_roster_run_writes_one_count_summary() -> None:
    credentialing_store.save_subject(
        "practice-a",
        {"name": "Testperson Alpha", "kind": "clinician", "active": True},
        actor="qa",
    )
    compliance_store.save_outside_supervisor("practice-a", {"name": "Outside Beta", "paid": True, "active": True})
    people = roster("practice-a")
    run_screening("practice-a", "qa", confirm="SCREEN ROSTER", lists=_lists([]))
    summaries = [item for item in compliance_store.audit_entries("practice-a") if item["action"] == "screen_roster"]
    assert summaries
    diff = summaries[-1]["diff"]
    assert set(diff) == {"people", "results", "clear", "possible_match", "not_run"}
    assert diff["people"] == len(people)
    assert diff["results"] == len(people) * 3
    assert diff["clear"] == len(people) * 3
    assert "Testperson" not in str(diff)
    assert "Outside" not in str(diff)
    person_id = next(item["person_id"] for item in people if item["name"] == "Testperson Alpha")
    run_screening("practice-a", "qa", person_ids=[person_id], lists=_lists([]))
    again = [item for item in compliance_store.audit_entries("practice-a") if item["action"] == "screen_roster"]
    assert len(again) == len(summaries)
    body = client.get("/compliance/roster", headers=_alice()).json()
    assert body["counts"]["total"] == len(people)
    assert body["counts"]["employee"] >= 1
    assert body["counts"]["outside_supervisor"] >= 1
    home = client.get("/home", headers=_alice()).json()
    assert home["pulse"]["screening_roster"] == body["counts"]["total"]
    page = open("app/static/index.html", encoding="utf-8").read()
    assert '["Clinicians", pulse.open_headcount]' in page
    assert '["Screening roster", pulse.screening_roster]' in page
    assert '["Roster", pulse.open_headcount]' not in page
    script = open("app/static/compliance.js", encoding="utf-8").read()
    assert "people (employees and paid outside supervisors)" in script
    assert "QA tools (test records only)" in script
    assert "Fake records used only to test this feature; not real staff." in script
    assert "Show all fields" in script
    assert "Same person — confirmed exclusion" in script


def test_review_groups_lists_and_compares_without_changing_a_decision() -> None:
    from app.compliance.review import backfill_list_records, review_packets

    saved = credentialing_store.save_subject(
        "practice-a",
        {
            "name": "Testperson Alpha",
            "kind": "clinician",
            "active": True,
            "npi": "1234567893",
            "license_state": "PA",
            "service_address": "1 Example St, Harrisburg, PA 17101",
            "date_of_birth": "1980-01-02",
        },
        actor="qa",
    )
    person_id = "subject:" + saved["id"]
    leie = ListedPerson(
        last="Alpha",
        first="Testperson",
        npi="1999999999",
        kind="1128a1",
        state="OH",
        city="Columbus",
        dob="1970-05-05",
        address="9 Other St",
        excl_date="2020-01-01",
    )
    sam = ListedPerson(
        last="Alpha",
        first="Testperson",
        kind="Ineligible",
        uei="UEI123456789",
        record_status="Active",
        agency="Health and Human Services",
    )
    lists = _lists([leie])
    lists["sam"] = ListFile(name="sam", updated_on="2026-11-25", file_sha256="sam", rows=[sam])
    run_screening("practice-a", "qa", person_ids=[person_id], lists=lists)
    home = [item for item in due_engine.list_open("practice-a") if item.source == "exclusion-review"]
    assert len(home) == 1
    assert home[0].source_ref == person_id
    stored = [item for item in compliance_store.screenings("practice-a") if item["result"] == "possible_match"]
    assert len(stored) == 2
    target = next(item for item in stored if item["list_name"] == "leie")
    status_before = target["result"]
    decision_before = [
        item for item in compliance_store.resolutions("practice-a") if item.get("result_id") == target["id"]
    ]
    live = next(
        item
        for item in compliance_store._read()["practices"]["practice-a"]["screenings"]
        if item["id"] == target["id"]
    )
    del live["list_records"]
    attached = backfill_list_records("practice-a", lists=lists)
    assert attached == 1
    refreshed = next(item for item in compliance_store.screenings("practice-a") if item["id"] == target["id"])
    assert refreshed["result"] == status_before == "possible_match"
    assert refreshed["list_records"][0]["npi"] == "1999999999"
    assert refreshed["list_records"][0]["dob"] == "1970-05-05"
    assert decision_before == [
        item for item in compliance_store.resolutions("practice-a") if item.get("result_id") == target["id"]
    ]
    packet = review_packets("practice-a")
    assert len(packet) == 1
    assert packet[0]["header"].startswith("Possible exclusion match: Testperson Alpha may match an entry on the OIG LEIE and SAM.gov")
    leie_view = next(item for item in packet[0]["lists"] if item["list_name"] == "leie")
    assert leie_view["why"] == "Last name and first name only."
    assert leie_view["likelihood"] == "Likely different person"
    npi_row = next(item for item in leie_view["rows"] if item["label"] == "NPI")
    assert npi_row["mark"] == "Different"
    name_row = next(item for item in leie_view["rows"] if item["label"] == "Full name")
    assert name_row["mark"] == "Match"
    assert any(link["label"] == "OIG LEIE online search" for link in leie_view["links"])
    assert "Social Security number" in leie_view["ssn_note"]
    sam_view = next(item for item in packet[0]["lists"] if item["list_name"] == "sam")
    assert sam_view["alignment"] == "Same exclusion reported by HHS/OIG"
    assert sam_view["ssn_note"] == ""
    held = client.post(
        f"/compliance/reviews/{target['id']}",
        headers=_alice(),
        json={"decision": "need_more_info", "note": "Need a date of birth from the file"},
    )
    assert held.status_code == 200, held.text
    still = [item for item in due_engine.list_open("practice-a") if item.source == "exclusion-review"]
    assert any(item.source_ref == person_id for item in still)
    logged = [item for item in compliance_store.audit_entries("practice-a") if item["action"] == "resolve_exclusion"]
    assert logged[-1]["diff"]["decision"] == "need_more_info"
    assert "Testperson" not in str(logged[-1]["diff"])
    cleared = client.post(
        f"/compliance/reviews/{target['id']}",
        headers=_alice(),
        json={"decision": "not_the_same_person", "note": "NPI differs"},
    )
    assert cleared.status_code == 200, cleared.text
    sam_id = next(item["id"] for item in stored if item["list_name"] == "sam")
    client.post(
        f"/compliance/reviews/{sam_id}",
        headers=_alice(),
        json={"decision": "not_the_same_person", "note": "NPI differs"},
    )
    assert not any(
        item.source_ref == person_id for item in due_engine.list_open("practice-a") if item.source == "exclusion-review"
    )


def test_state_and_license_make_a_different_person_without_changing_the_result() -> None:
    from app.compliance.review import review_packets, review_token
    from app.compliance.sources import parse_sam
    from app.employees.store import employees_store

    saved = credentialing_store.save_subject(
        "practice-a",
        {
            "name": "Testperson Alpha",
            "kind": "clinician",
            "active": True,
            "license_type": "LPC",
            "license_state": "PA",
            "hire_date": "2024-03-01",
        },
        actor="qa",
    )
    employees_store.save_contact(
        "practice-a",
        "alpha@example.test",
        {"preferred_name": "Testperson Alpha", "city": "Harrisburg", "state": "PA"},
        "qa",
    )
    employees_store.set_link("practice-a", "alpha@example.test", saved["id"], "qa")
    person_id = "subject:" + saved["id"]
    listed = ListedPerson(
        last="Alpha",
        first="Testperson",
        state="OH",
        general="NURSING SERVICE PROVIDERS",
        specialty="Nurse",
        city="Columbus",
    )
    run_screening(
        "practice-a",
        "qa",
        person_ids=[person_id],
        lists=_lists([listed], sam_ready=True),
    )
    packet = review_packets("practice-a")[0]
    assert "Testperson" not in packet["review_id"]
    assert packet["review_id"] == review_token("practice-a", person_id)
    view = packet["lists"][0]
    assert view["likelihood"] == "Likely different person"
    assert "a nurse in OH" in view["likelihood_reason"]
    assert "an LPC" in view["likelihood_reason"]
    assert "PA (contact)" in view["likelihood_reason"]
    assert "Testperson" not in view["likelihood_reason"]
    state_row = next(item for item in view["rows"] if item["label"] == "State")
    discipline = next(item for item in view["rows"] if item["label"] == "Specialty / discipline")
    city = next(item for item in view["rows"] if item["label"] == "City")
    assert state_row["mark"] == "Different"
    assert state_row["staff_value"] == "PA (contact)"
    assert discipline["mark"] == "Different"
    assert city["staff_value"] == "Harrisburg"
    assert all(item["label"] != "License" for item in view["rows"])
    assert any(item["both_blank"] == "yes" for item in view["rows"])
    assert view["ssn_note"]
    home = next(item for item in due_engine.list_open("practice-a") if item.source == "exclusion-review")
    assert home.href.endswith(packet["review_id"])
    assert "Testperson" not in home.href
    decided = client.post(
        "/compliance/reviews/decide",
        headers=_alice(),
        json={"review_id": packet["review_id"], "decision": "not_the_same_person", "note": "State and license differ"},
    )
    assert decided.status_code == 200, decided.text
    stored = next(item for item in compliance_store.screenings("practice-a") if item["list_name"] == "leie")
    assert stored["result"] == "possible_match"
    logged = [item for item in compliance_store.audit_entries("practice-a") if item["action"] == "resolve_exclusion"][-1]
    assert logged["diff"]["decision"] == "not_the_same_person"
    assert logged["diff"]["review_id"] == packet["review_id"]
    assert "Testperson" not in str(logged["diff"])
    _, parsed = parse_sam(
        {
            "excludedEntity": [
                {
                    "exclusionDetails": {
                        "classificationType": "Individual",
                        "exclusionType": "Ineligible",
                        "excludingAgencyName": "Health and Human Services",
                    },
                    "exclusionIdentification": {"firstName": "Test", "lastName": "Person", "ueiSAM": "UEIEXAMPLE1"},
                    "exclusionPrimaryAddress": {
                        "addressLine1": "1 Example St",
                        "city": "Columbus",
                        "stateOrProvinceCode": "OH",
                    },
                    "exclusionActions": {
                        "listOfActions": [{"activateDate": "2020-01-01", "recordStatus": "Active", "terminationDate": ""}]
                    },
                }
            ]
        },
        queried_on="2026-11-25",
        raw=b"{}",
    )
    assert parsed[0].state == "OH"
    assert parsed[0].city == "Columbus"
    assert parsed[0].address == "1 Example St"
    assert parsed[0].agency == "Health and Human Services"
    assert parsed[0].record_status == "Active"
    from app.compliance.review import comparison_rows, likelihood_from_rows
    from app.compliance.sources import load_sam_detail

    aide_rows = comparison_rows(
        {"first": "A", "last": "B", "state": "MO", "specialty": "Nurse's aide", "general": ""},
        {
            "name": "Example Person",
            "license_type": "LCSW",
            "license_state": "PA",
            "state": "",
            "specialties": "",
            "npi": "",
            "dob": "",
            "city": "",
            "service_state": "",
            "hire_date": "",
        },
    )
    aide = likelihood_from_rows(aide_rows, list_name="leie")
    assert aide["label"] == "Likely different person"
    assert "a nurse's aide in MO" in aide["reason"]
    assert "an LCSW" in aide["reason"]
    assert "PA (license)" in aide["reason"]
    assert next(item["staff_value"] for item in aide_rows if item["label"] == "State") == "PA (license)"

    class _Denied:
        def get(self, url: str, params: dict | None = None) -> tuple[int, str, bytes]:
            return 403, "", b""

    import logging
    import os

    captured: list[str] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    log = logging.getLogger("app.compliance.sources")
    handler = _Grab()
    log.addHandler(handler)
    log.setLevel(logging.WARNING)
    previous = os.environ.get("SAM_API_KEY")
    os.environ["SAM_API_KEY"] = "not-a-real-key"
    try:
        from datetime import date

        failed = load_sam_detail(_Denied(), first="Test", last="Person", today=date(2026, 11, 25))
    finally:
        log.removeHandler(handler)
        if previous is None:
            os.environ.pop("SAM_API_KEY", None)
        else:
            os.environ["SAM_API_KEY"] = previous
    assert failed.ready is False
    assert "http_403" in failed.detail
    logged = " ".join(captured)
    assert "http_403" in logged
    assert "not-a-real-key" not in logged
    assert "Person" not in logged
