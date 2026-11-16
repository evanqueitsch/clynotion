"""Acceptance through onboarding. Synthetic people only."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.auth import User, issue_token
from app.credentialing.store import credentialing_store
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
        signed = [meta for meta in drive.files.values() if meta.get("name") == "Sample_Riley_offer-signed.pdf"]
        assert len(signed) == 1
        assert signed[0]["data"] == b"%PDF-1.4 synthetic"
        names = {meta["name"] for meta in drive.files.values() if meta.get("folder")}
        assert names == {"01 Onboarding", "02 Payroll & Tax", "03 Credentialing", "04 Clearances"}
        parent = next(meta["parent"] for meta in drive.files.values() if meta.get("name") == "01 Onboarding")
        assert drive.folders[parent] == "Sample, Riley"
        onboarding_id = next(file_id for file_id, meta in drive.files.items() if meta.get("name") == "01 Onboarding")
        onboarding_files = [meta for meta in drive.files.values() if meta.get("parent") == onboarding_id]
        assert any(item.get("name") == "offer-letter.pdf" for item in onboarding_files)
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
    packet = client.post(
        f"/hiring/hires/{hire_id}/items/packet_signed",
        headers=_alice(),
        json={"status": "done"},
    )
    assert packet.status_code == 200, packet.text
    titles = [item.title for item in due_engine.list_open("practice-a")]
    assert "W-4, direct deposit, and the PA new-hire report" in titles
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
    assert cell["status"] == "unknown"
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
