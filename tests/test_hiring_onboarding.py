"""Acceptance through onboarding. Synthetic people only."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.auth import User, issue_token
from app.credentialing.store import credentialing_store
from app.hiring.checklist import add_business_days, clearance_state, packet_due
from app.hiring.engine import drive_prompt, hire_cell_status
from app.hiring.personnel_drive import clear_personnel_drive_for_tests, set_personnel_drive_for_tests
from app.hiring.resume import ground_fields
from app.main import app
from app.platform.due import due_engine

client = TestClient(app)
EASTERN = ZoneInfo("America/New_York")


def _alice() -> dict[str, str]:
    user = User("user-alice", "alice", "practice-a", "alice@example.com")
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _today() -> str:
    return datetime.now(EASTERN).strftime("%m/%d/%Y")


def _offer(email: str = "jordan.example@example.com", name: str = "Jordan Example", **extra: object) -> dict:
    body = {
        "display_name": name,
        "personal_email": email,
        "employment_type": "full-time",
        "candidate_full_name": name,
        "candidate_first_name": name.split()[0],
        "supervisor_name": "Alex Supervisor",
        "hourly_rate": "$45.00",
        "caseload_range": "25-30",
        "qsehra_monthly_amount": "$250",
        "qsehra_wait_days": "60",
        "offer_date": _today(),
        "start_date": _today(),
        "response_deadline": _today(),
        "direct_client_access": True,
    }
    body.update(extra)
    return body


class RecordingDrive:
    def __init__(self) -> None:
        self.folders: dict[str, str] = {}
        self.files: dict[str, dict] = {}
        self.trashed: list[str] = []
        self.created = 0

    def ensure_folder(self, folder_id: str, name: str) -> tuple[str, str]:
        if folder_id:
            return folder_id, f"https://drive.google.com/drive/folders/{folder_id}"
        self.created += 1
        new_id = f"fld-{self.created}"
        self.folders[new_id] = name
        return new_id, f"https://drive.google.com/drive/folders/{new_id}"

    def ensure_child(self, parent_id: str, name: str) -> str:
        for file_id, meta in self.files.items():
            if meta.get("parent") == parent_id and meta.get("name") == name and meta.get("folder"):
                return file_id
        self.created += 1
        new_id = f"fld-{self.created}"
        self.files[new_id] = {"name": name, "parent": parent_id, "folder": True}
        self.folders[new_id] = name
        return new_id

    def upload(self, folder_id: str, filename: str, data: bytes, mime: str) -> str:
        self.created += 1
        file_id = f"file-{self.created}"
        self.files[file_id] = {"name": filename, "parent": folder_id, "data": data, "mime": mime, "folder": False}
        return file_id

    def rename(self, file_id: str, name: str) -> None:
        if file_id in self.folders and file_id not in self.files:
            self.folders[file_id] = name
            return
        self.files[file_id]["name"] = name
        if file_id in self.folders:
            self.folders[file_id] = name

    def move(self, file_id: str, folder_id: str, name: str = "") -> None:
        self.files[file_id]["parent"] = folder_id
        if name:
            self.files[file_id]["name"] = name

    def trash(self, file_id: str) -> None:
        self.trashed.append(file_id)
        if file_id in self.files:
            self.files[file_id]["trashed"] = True


def test_all_caps_and_lower_names_are_title_cased() -> None:
    caps = "Full legal name: JORDAN EXAMPLE\nFirst name: JORDAN\n"
    fields = ground_fields(
        caps,
        {"candidate_full_name": "JORDAN EXAMPLE", "candidate_first_name": "JORDAN"},
    )
    assert fields["candidate_full_name"] == "Jordan Example"
    assert fields["candidate_first_name"] == "Jordan"
    lower = ground_fields("full legal name: avery sample\n", {"candidate_full_name": "avery sample"})
    assert lower["candidate_full_name"] == "Avery Sample"
    mixed = ground_fields("Full legal name: McDonald\n", {"candidate_full_name": "McDonald"})
    assert mixed["candidate_full_name"] == "McDonald"


def test_saving_a_new_name_renames_the_same_drive_folder() -> None:
    drive = RecordingDrive()
    set_personnel_drive_for_tests(drive)
    try:
        resume = (
            "Full legal name: Jordan Example\nFirst name: Jordan\nPersonal email: jordan.example@example.com\n"
        ).encode()
        uploaded = client.post(
            "/hiring/resumes",
            headers=_alice(),
            files={"resume": ("jordan.txt", resume, "text/plain")},
        )
        assert uploaded.status_code == 200, uploaded.text
        created = client.post("/hiring/offers", headers=_alice(), json=_offer(resume_id=uploaded.json()["resume_id"]))
        assert created.status_code == 200, created.text
        offer_id = created.json()["id"]
        assert drive.folders["fld-1"] == "Example, Jordan"
        resume_file = next(item for item in drive.files.values() if item.get("name", "").endswith("_resume.txt"))
        assert resume_file["name"] == "Example_Jordan_resume.txt"
        renamed = client.put(
            f"/hiring/offers/{offer_id}",
            headers=_alice(),
            json=_offer(name="Avery Jordan Example", resume_id=uploaded.json()["resume_id"]),
        )
        assert renamed.status_code == 200, renamed.text
        assert list(drive.folders) == ["fld-1"]
        assert drive.folders["fld-1"] == "Example, Avery Jordan"
        resumes = [item for item in drive.files.values() if str(item.get("name") or "").endswith("_resume.txt")]
        assert len(resumes) == 1
        assert resumes[0]["name"] == "Example_Avery_Jordan_resume.txt"
        assert drive.created == 2
    finally:
        clear_personnel_drive_for_tests()


def test_paper_acceptance_files_the_signed_pdf_and_builds_the_checklist() -> None:
    drive = RecordingDrive()
    set_personnel_drive_for_tests(drive)
    try:
        created = client.post(
            "/hiring/offers",
            headers=_alice(),
            json=_offer(
                email="riley.sample@example.com",
                name="Riley Sample",
                employment_type="part-time",
                qsehra_monthly_amount="",
                qsehra_wait_days="",
                caseload_range="12-18",
                direct_client_access=True,
            ),
        )
        assert created.status_code == 200, created.text
        offer_id = created.json()["id"]
        sent = client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
        assert sent.status_code == 200, sent.text
        declined = client.post(f"/hiring/offers/{offer_id}/decline", headers=_alice(), json={"reason": "", "note": ""})
        assert declined.status_code == 200, declined.text
        assert declined.json()["status"] == "declined"
        fresh = client.post(
            "/hiring/offers",
            headers=_alice(),
            json=_offer(
                email="riley.part@example.com",
                name="Riley Sample",
                employment_type="part-time",
                qsehra_monthly_amount="",
                qsehra_wait_days="",
                caseload_range="12-18",
            ),
        )
        assert fresh.status_code == 200, fresh.text
        offer_id = fresh.json()["id"]
        sent = client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
        assert sent.status_code == 200, sent.text
        missing = client.post(
            f"/hiring/offers/{offer_id}/accept-paper",
            headers=_alice(),
            data={"accepted_on": _today()},
        )
        assert missing.status_code == 400
        accepted = client.post(
            f"/hiring/offers/{offer_id}/accept-paper",
            headers=_alice(),
            data={"accepted_on": _today()},
            files={"upload": ("signed.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        )
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body["offer"]["status"] == "accepted"
        assert body["offer"]["acceptance"]["accepted_on"]
        hire = body["hire"]
        assert hire["i9_drive"] == "pending"
        benefits = next(item for item in hire["items"] if item["template_key"] == "benefits")
        assert benefits["status"] == "skipped"
        clearances = next(item for item in hire["items"] if item["template_key"] == "clearances")
        assert clearances["warning"] is True
        screening = next(item for item in hire["items"] if item["template_key"] == "exclusion_screening")
        assert screening["warning"] is True
        assert screening["badge"] == clearances["badge"]
        signed = [meta for meta in drive.files.values() if meta.get("name") == "Sample_Riley_offer-signed.pdf"]
        assert len(signed) == 1
        assert signed[0]["data"] == b"%PDF-1.4 synthetic"
        names = {meta["name"] for meta in drive.files.values() if meta.get("folder")}
        assert names == {"01 Onboarding", "02 Payroll & Tax", "03 Credentialing", "04 Clearances"}
        parent = next(meta["parent"] for meta in drive.files.values() if meta.get("name") == "01 Onboarding")
        assert drive.folders[parent] == "Sample, Riley"
        onboarding_id = next(file_id for file_id, meta in drive.files.items() if meta.get("name") == "01 Onboarding")
        onboarding_files = [meta for meta in drive.files.values() if meta.get("parent") == onboarding_id]
        assert any(item.get("name") == "Sample_Riley_offer-letter.pdf" for item in onboarding_files)
        clearances = next(item for item in hire["items"] if item["template_key"] == "clearances")
        assert clearances["warning"] is True
        assert clearances["badge"] == "due before start"
        assert any(item.get("name") == "Sample_Riley_offer-signed.pdf" for item in onboarding_files)
        again = client.post(
            f"/hiring/offers/{offer_id}/accept-paper",
            headers=_alice(),
            data={"accepted_on": _today()},
            files={"upload": ("signed.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        )
        assert again.status_code == 409
        bootstrap = client.get("/hiring/bootstrap", headers=_alice())
        assert bootstrap.json()["mail_queue"] == []
        actions = [item["action"] for item in bootstrap.json()["history"]]
        assert "accepted" in actions
        assert "i9_pending" in actions
    finally:
        clear_personnel_drive_for_tests()


def test_checklist_status_note_undo_and_billing_todo() -> None:
    created = client.post("/hiring/offers", headers=_alice(), json=_offer(email="avery.sample@example.com", name="Avery Sample", direct_client_access=False))
    assert created.status_code == 200, created.text
    offer_id = created.json()["id"]
    sent = client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    assert sent.status_code == 200, sent.text
    accepted = client.post(
        f"/hiring/offers/{offer_id}/accept",
        headers=_alice(),
        json={"typed_name": "Avery Sample"},
    )
    assert accepted.status_code == 200, accepted.text
    hire_id = accepted.json()["hire"]["id"]
    blocked = client.post(
        f"/hiring/hires/{hire_id}/items/welcome_sent/file",
        headers=_alice(),
        files={"upload": ("note.txt", b"not stored", "text/plain")},
    )
    assert blocked.status_code == 400
    done = client.post(
        f"/hiring/hires/{hire_id}/items/welcome_sent",
        headers=_alice(),
        json={"status": "done", "note": "Copied the welcome message."},
    )
    assert done.status_code == 200, done.text
    item = next(row for row in done.json()["items"] if row["template_key"] == "welcome_sent")
    assert item["status"] == "done"
    assert item["done_by"]
    assert item["note"] == "Copied the welcome message."
    undone = client.post(
        f"/hiring/hires/{hire_id}/items/welcome_sent",
        headers=_alice(),
        json={"status": "open"},
    )
    item = next(row for row in undone.json()["items"] if row["template_key"] == "welcome_sent")
    assert item["status"] == "open"
    assert item["done_by"] == ""
    assert item["note"] == ""
    item_only = client.post(
        f"/hiring/hires/{hire_id}/items/packet_signed",
        headers=_alice(),
        json={"status": "done"},
    )
    assert item_only.status_code == 200, item_only.text
    assert not any("W-4" in item.title for item in due_engine.list_open("practice-a"))
    packet = client.post(f"/hiring/hires/{hire_id}/packet-complete", headers=_alice())
    assert packet.status_code == 200, packet.text
    titles = [item.title for item in due_engine.list_open("practice-a")]
    assert "Avery Sample: W-4, direct deposit, and the PA new-hire report" in titles
    todo = next(item for item in due_engine.list_open("practice-a") if item.source_ref == hire_id + ":packet")
    assert "T" in todo.due_at
    assert todo.due_at.endswith("23:59:59-05:00") or todo.due_at.endswith("23:59:59-04:00")
    assert packet.json()["packet_complete"] is True
    undone = client.post(f"/hiring/hires/{hire_id}/packet-undo", headers=_alice())
    assert undone.status_code == 200, undone.text
    assert undone.json()["packet_complete"] is False
    assert all(item.source_ref != hire_id + ":packet" for item in due_engine.list_open("practice-a"))
    form = client.get("/hiring/forms/h4c_packet", headers=_alice())
    assert form.status_code == 200
    assert form.content.startswith(b"%PDF")


def test_activate_adds_credentialing_cells_without_overwriting() -> None:
    payer = credentialing_store.save_payer(
        "practice-a",
        {"name": "QA Example Payer", "type": "commercial", "accepts_licenses": ["LCSW"]},
        actor="qa",
    )
    created = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer(email="casey.sample@example.com", name="Casey Sample", direct_client_access=False),
    )
    offer_id = created.json()["id"]
    client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    accepted = client.post(f"/hiring/offers/{offer_id}/accept", headers=_alice(), json={"typed_name": "Casey Sample"})
    hire_id = accepted.json()["hire"]["id"]
    early = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer(
            email="early.sample@example.com",
            name="Early Sample",
            direct_client_access=False,
            start_date="12/01/2026",
            response_deadline=_today(),
        ),
    )
    if datetime.now(EASTERN).date().isoformat() < "2026-12-01":
        early_id = early.json()["id"]
        client.post(f"/hiring/offers/{early_id}/send", headers=_alice(), json={"channel": "outside"})
        early_hire = client.post(
            f"/hiring/offers/{early_id}/accept",
            headers=_alice(),
            json={"typed_name": "Early Sample"},
        )
        too_soon = client.post(
            f"/hiring/hires/{early_hire.json()['hire']['id']}/activate",
            headers=_alice(),
            json={"work_email": "early.sample@example.com"},
        )
        assert too_soon.status_code == 409
    activated = client.post(
        f"/hiring/hires/{hire_id}/activate",
        headers=_alice(),
        json={"work_email": "casey.sample@example.com"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    grid = credentialing_store.public_grid("practice-a")
    subject = next(item for item in grid["subjects"] if item["name"] == "Casey Sample")
    cell = next(item for item in grid["cells"] if item["subject_id"] == subject["id"] and item["payer_id"] == payer["id"])
    assert cell["status"] == "not_eligible"
    credentialing_store.save_enrollment(
        "practice-a",
        {"subject_id": subject["id"], "payer_id": payer["id"], "status": "in_network", "note": ""},
        actor="qa",
    )
    second = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer(email="casey.sample@example.com", name="Casey Sample", direct_client_access=False),
    )
    assert second.status_code == 200, second.text
    second_id = second.json()["id"]
    client.post(f"/hiring/offers/{second_id}/send", headers=_alice(), json={"channel": "outside"})
    second_hire = client.post(
        f"/hiring/offers/{second_id}/accept",
        headers=_alice(),
        json={"typed_name": "Casey Sample"},
    )
    again = client.post(
        f"/hiring/hires/{second_hire.json()['hire']['id']}/activate",
        headers=_alice(),
        json={"work_email": "casey.work@example.com"},
    )
    assert again.status_code == 200, again.text
    grid = credentialing_store.public_grid("practice-a")
    cell = next(item for item in grid["cells"] if item["subject_id"] == subject["id"] and item["payer_id"] == payer["id"])
    assert cell["status"] == "in_network"


def test_clearance_badge_is_amber_before_the_due_date_and_red_after() -> None:
    from datetime import date, timedelta

    today = date(2026, 11, 16)
    state = clearance_state(direct=True, status="open", due=today + timedelta(days=10), today=today)
    assert state == {"warning": False, "badge": "due before start"}
    late = clearance_state(direct=True, status="open", due=today, today=today)
    assert late == {"warning": True, "badge": "due before start"}
    assert clearance_state(direct=False, status="open", due=today, today=today)["badge"] == ""
    assert clearance_state(direct=True, status="done", due=today, today=today)["warning"] is False
    created = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer(email="late.sample@example.com", name="Late Sample", start_date="06/01/2027"),
    )
    assert created.status_code == 200, created.text
    offer_id = created.json()["id"]
    client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    accepted = client.post(f"/hiring/offers/{offer_id}/accept", headers=_alice(), json={"typed_name": "Late Sample"})
    item = next(row for row in accepted.json()["hire"]["items"] if row["template_key"] == "clearances")
    assert item["badge"] == "due before start"
    assert item["warning"] is False
    screening = next(row for row in accepted.json()["hire"]["items"] if row["template_key"] == "exclusion_screening")
    assert screening["badge"] == "due before start"
    assert screening["warning"] is False


def test_drive_prompt_hides_retry_while_working_and_after_trash() -> None:
    working = {"drive_status": "working", "drive_folder_id": "fld-1", "drive_folder_url": "https://drive.example/fld-1"}
    assert drive_prompt(working, "Jordan Example") == ("Creating Drive folders…", False)
    trashed = {"drive_status": "trashed"}
    assert drive_prompt(trashed, "Jordan Example") == ("Folder trashed", False)
    pending = {
        "drive_status": "connected",
        "drive_folder_id": "fld-1",
        "drive_pdf_file_id": "file-1",
        "drive_pdf_name": "offer-letter.pdf",
    }
    assert drive_prompt(pending, "Jordan Example")[1] is True
    ready = dict(pending)
    ready["drive_pdf_name"] = "Example_Jordan_offer-letter.pdf"
    assert drive_prompt(ready, "Jordan Example") == ("", False)


def test_packet_due_is_the_later_of_the_start_date_and_three_business_days() -> None:
    monday = date(2026, 11, 16)
    assert packet_due(date(2027, 1, 5), monday, set()) == date(2027, 1, 5)
    assert packet_due(monday, monday, set()) == add_business_days(monday, 3, set())
    assert add_business_days(monday, 3, set()) == date(2026, 11, 19)


def test_blank_credentials_are_not_eligible_for_a_restricted_payer() -> None:
    assert hire_cell_status({"name": "Example Plan", "accepts_licenses": ["LCSW"]}, "") == "not_eligible"
    assert hire_cell_status({"name": "Example Plan", "accepts_licenses": ["LCSW"]}, "Jordan Example") == "not_eligible"
    assert hire_cell_status({"name": "Example Plan", "accepts_licenses": ["LCSW"]}, "LCSW") == "unknown"
    assert hire_cell_status({"name": "Open Plan", "accepts_licenses": ["all"]}, "") == "unknown"
    assert hire_cell_status({"name": "Unset Plan", "accepts_licenses": []}, "") == "unknown"


def test_deleting_an_offer_with_no_hire_trashes_its_folder() -> None:
    drive = RecordingDrive()
    set_personnel_drive_for_tests(drive)
    try:
        created = client.post(
            "/hiring/offers",
            headers=_alice(),
            json=_offer(email="folder.sample@example.com", name="Folder Sample"),
        )
        assert created.status_code == 200, created.text
        offer_id = created.json()["id"]
        folder_id = next(iter(drive.folders))
        removed = client.post(
            f"/hiring/offers/{offer_id}/delete",
            headers=_alice(),
            json={"confirm": "Folder Sample"},
        )
        assert removed.status_code == 200, removed.text
        assert folder_id in drive.trashed
        assert folder_id in drive.folders
    finally:
        clear_personnel_drive_for_tests()


def test_delete_hire_requires_the_exact_name_and_only_removes_that_hire() -> None:
    drive = RecordingDrive()
    set_personnel_drive_for_tests(drive)
    try:
        other = credentialing_store.save_subject(
            "practice-a",
            {"name": "Other Example", "kind": "clinician"},
            actor="qa",
        )
        created = client.post(
            "/hiring/offers",
            headers=_alice(),
            json=_offer(email="deleteme.sample@example.com", name="Delete Me Sample", direct_client_access=False),
        )
        offer_id = created.json()["id"]
        client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
        accepted = client.post(
            f"/hiring/offers/{offer_id}/accept-paper",
            headers=_alice(),
            data={"accepted_on": _today()},
            files={"upload": ("signed.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        )
        assert accepted.status_code == 200, accepted.text
        hire_id = accepted.json()["hire"]["id"]
        client.post(
            f"/hiring/hires/{hire_id}/items/packet_signed",
            headers=_alice(),
            json={"status": "done"},
        )
        activated = client.post(
            f"/hiring/hires/{hire_id}/activate",
            headers=_alice(),
            json={"work_email": "deleteme.sample@example.com"},
        )
        assert activated.status_code == 200, activated.text
        kept = client.post(
            f"/hiring/hires/{hire_id}/delete",
            headers=_alice(),
            json={"confirm": "wrong name"},
        )
        assert kept.status_code == 400
        assert any(item["id"] == hire_id for item in client.get("/hiring/bootstrap", headers=_alice()).json()["hires"])
        removed = client.post(
            f"/hiring/hires/{hire_id}/delete",
            headers=_alice(),
            json={"confirm": "Delete Me Sample"},
        )
        assert removed.status_code == 200, removed.text
        hires = client.get("/hiring/bootstrap", headers=_alice()).json()["hires"]
        assert all(item["id"] != hire_id for item in hires)
        names = [item["name"] for item in credentialing_store.public_grid("practice-a")["subjects"]]
        assert "Delete Me Sample" not in names
        assert "Other Example" in names
        assert other["id"]
        open_refs = [item.source_ref for item in due_engine.list_open("practice-a")]
        assert hire_id not in open_refs
        assert hire_id + ":packet" not in open_refs
        assert drive.trashed
        assert any(file_id in drive.folders for file_id in drive.trashed)
        assert all(drive.files[file_id].get("trashed") for file_id in drive.trashed if file_id in drive.files)
        left = client.get("/hiring/offers/" + offer_id, headers=_alice())
        assert left.status_code == 200
        assert left.json()["drive_status"] == "trashed"
        assert left.json()["drive_note"] == "Folder trashed"
        assert left.json()["drive_retry"] is False
    finally:
        clear_personnel_drive_for_tests()
