"""Resume prefill and the personnel Drive folder. Synthetic people only."""

from __future__ import annotations

import io
import logging
import zipfile

from fastapi.testclient import TestClient

from app.auth import User, issue_token
from app.hiring.pdf import soffice_command, text_pdf
from app.hiring.personnel_drive import PersonnelDrive, clear_personnel_drive_for_tests, folder_name, set_personnel_drive_for_tests
from app.hiring.resume import ResumeReadError, ground_fields, read_resume, resume_mode
from app.main import app

client = TestClient(app)

RESUME = """Jordan Example
Full legal name: Jordan Example
First name: Jordan
Personal email: jordan.example@example.com
Phone: (555) 010-0142
Credentials: MA, LPC
Position: Outpatient Clinician
""".encode("utf-8")


class FakeDrive:
    def __init__(self) -> None:
        self.folders: dict[str, str] = {}
        self.uploads: list[tuple[str, str, bytes, str]] = []

    def ensure_folder(self, folder_id: str, name: str) -> tuple[str, str]:
        if folder_id:
            return folder_id, f"https://drive.google.com/drive/folders/{folder_id}"
        for existing_id, existing_name in self.folders.items():
            if existing_name == name:
                return existing_id, f"https://drive.google.com/drive/folders/{existing_id}"
        new_id = f"fld-{len(self.folders) + 1}"
        self.folders[new_id] = name
        return new_id, f"https://drive.google.com/drive/folders/{new_id}"

    def upload(self, folder_id: str, filename: str, data: bytes, mime: str) -> str:
        self.uploads.append((folder_id, filename, data, mime))
        return f"file-{len(self.uploads)}"


class RecordingHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))

        class Response:
            status_code = 200

            def json(self):
                if method == "GET":
                    return {"files": []}
                return {"id": "created-1"}

        return Response()


def _alice() -> dict[str, str]:
    user = User("user-alice", "alice", "practice-a", "alice@example.com")
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _offer(resume_id: str = "") -> dict[str, str]:
    return {
        "display_name": "Jordan Example",
        "personal_email": "jordan.example@example.com",
        "employment_type": "full-time",
        "candidate_full_name": "Jordan Example",
        "candidate_first_name": "Jordan",
        "candidate_credentials": "MA, LPC",
        "position_title": "Outpatient Clinician",
        "hourly_rate": "40",
        "caseload_range": "25-30",
        "qsehra_monthly_amount": "100",
        "qsehra_wait_days": "60",
        "offer_date": "11/16/2026",
        "start_date": "01/05/2027",
        "response_deadline": "12/16/2026",
        "resume_id": resume_id,
    }


def test_labeled_resume_keeps_only_text_that_is_present() -> None:
    fields = read_resume(RESUME, "jordan.txt")
    assert fields["candidate_full_name"] == "Jordan Example"
    assert fields["candidate_first_name"] == "Jordan"
    assert fields["personal_email"] == "jordan.example@example.com"
    assert fields["phone"] == "(555) 010-0142"
    assert fields["candidate_credentials"] == "MA, LPC"
    assert fields["position_title"] == "Outpatient Clinician"
    invented = ground_fields(
        "Jordan Example\njordan.example@example.com\n",
        {
            "candidate_full_name": "Jordan Example",
            "candidate_first_name": "Pat",
            "personal_email": "someone.else@example.com",
            "phone": "555-010-0199",
            "candidate_credentials": "PhD",
            "position_title": "Director of Everything",
        },
    )
    assert invented["candidate_full_name"] == "Jordan Example"
    assert invented["candidate_first_name"] == ""
    assert invented["personal_email"] == "jordan.example@example.com"
    assert invented["phone"] == ""
    assert invented["candidate_credentials"] == ""
    assert invented["position_title"] == ""


def test_unlabeled_resume_does_not_invent_a_name() -> None:
    fields = read_resume(b"Available for clinical work.\nWrite jordan.example@example.com.\n", "note.txt")
    assert fields["personal_email"] == "jordan.example@example.com"
    assert fields["candidate_full_name"] == ""
    assert fields["position_title"] == ""


def test_pdf_and_docx_text_are_read() -> None:
    pdf = text_pdf(["Full legal name: Jordan Example", "Personal email: jordan.example@example.com"])
    assert read_resume(pdf, "jordan.pdf")["personal_email"] == "jordan.example@example.com"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Full legal name: Jordan Example</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Personal email: jordan.example@example.com</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        package.writestr("word/document.xml", document)
    assert read_resume(buf.getvalue(), "jordan.docx")["candidate_full_name"] == "Jordan Example"


def test_resume_reader_rejects_other_providers(monkeypatch) -> None:
    monkeypatch.setenv("ATTUNE_RESUME_LLM", "anthropic")
    try:
        resume_mode()
        raise AssertionError("anthropic was accepted")
    except ResumeReadError:
        pass


def test_upload_stores_encrypted_resume_without_logging_it(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    uploaded = client.post(
        "/hiring/resumes",
        headers=_alice(),
        files={"resume": ("jordan.txt", RESUME, "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["fields"]["personal_email"] == "jordan.example@example.com"
    assert "jordan.example@example.com" not in caplog.text
    assert "Jordan Example" not in caplog.text
    bootstrap = client.get("/hiring/bootstrap", headers=_alice()).json()
    uploaded_row = next(item for item in bootstrap["history"] if item["action"] == "uploaded")
    assert uploaded_row["summary"] == "Resume uploaded"
    assert "@" not in uploaded_row["summary"]
    created = client.post("/hiring/offers", headers=_alice(), json=_offer(body["resume_id"]))
    assert created.status_code == 200, created.text
    offer = created.json()
    assert offer["resume"] is True
    assert offer["drive_connected"] is False
    assert offer["drive_folder_url"] == ""
    assert offer["fields"]["hourly_rate"] == "$40.00"
    downloaded = client.get(f"/hiring/offers/{offer['id']}/resume", headers=_alice())
    assert downloaded.status_code == 200
    assert downloaded.content == RESUME
    assert "jordan" not in downloaded.headers["content-disposition"].casefold()
    removed = client.post(
        f"/hiring/offers/{offer['id']}/delete",
        headers=_alice(),
        json={"confirm": "Jordan Example"},
    )
    assert removed.status_code == 200, removed.text
    assert client.get(f"/hiring/offers/{offer['id']}/resume", headers=_alice()).status_code == 404
    rejected = client.post(
        "/hiring/resumes",
        headers=_alice(),
        files={"resume": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"x" * 20, "image/png")},
    )
    assert rejected.status_code == 400
    assert "PDF, DOCX, or TXT" in rejected.text


def test_offer_pdf_command_reuses_a_profile() -> None:
    from pathlib import Path

    command = soffice_command("soffice", Path("offer.docx"), Path("/tmp"))
    assert any(part.startswith("-env:UserInstallation=file://") for part in command)
    assert "warm_offer_pdf" in Path("app/main.py").read_text(encoding="utf-8")


def test_personnel_folder_is_created_once_and_receives_resume_then_pdf() -> None:
    drive = FakeDrive()
    set_personnel_drive_for_tests(drive)
    try:
        uploaded = client.post(
            "/hiring/resumes",
            headers=_alice(),
            files={"resume": ("jordan.txt", RESUME, "text/plain")},
        )
        created = client.post("/hiring/offers", headers=_alice(), json=_offer(uploaded.json()["resume_id"]))
        assert created.status_code == 200, created.text
        offer = created.json()
        assert offer["drive_connected"] is True
        assert offer["drive_folder_url"].endswith("/fld-1")
        assert drive.folders == {"fld-1": "Example, Jordan"}
        assert drive.uploads[0][1] == "resume.txt"
        assert drive.uploads[0][2] == RESUME
        again = client.put(f"/hiring/offers/{offer['id']}", headers=_alice(), json=_offer(uploaded.json()["resume_id"]))
        assert again.status_code == 200, again.text
        assert drive.folders == {"fld-1": "Example, Jordan"}
        assert len(drive.uploads) == 1
        sent = client.post(f"/hiring/offers/{offer['id']}/send", headers=_alice(), json={"channel": "outside"})
        assert sent.status_code == 200, sent.text
        assert [item[1] for item in drive.uploads] == ["resume.txt", "offer-letter.pdf"]
        retry = client.post(f"/hiring/offers/{offer['id']}/drive", headers=_alice())
        assert retry.status_code == 200, retry.text
        assert len(drive.uploads) == 2
        history = client.get("/hiring/bootstrap", headers=_alice()).json()["history"]
        summaries = " ".join(item["summary"] for item in history)
        assert "Personnel folder created" in summaries
        assert "Resume copied" in summaries
        assert "Offer PDF copied" in summaries
        assert "Jordan" not in summaries
        assert "example.com" not in summaries
    finally:
        clear_personnel_drive_for_tests()


def test_retry_reports_drive_not_connected() -> None:
    created = client.post("/hiring/offers", headers=_alice(), json=_offer())
    offer_id = created.json()["id"]
    retried = client.post(f"/hiring/offers/{offer_id}/drive", headers=_alice())
    assert retried.status_code == 200, retried.text
    assert retried.json()["drive_connected"] is False
    assert retried.json()["drive_folder_url"] == ""


def test_drive_calls_stay_on_one_shared_drive() -> None:
    http = RecordingHttp()

    class Token:
        def access_token(self) -> str:
            return "test-token"

    drive = PersonnelDrive(Token(), "personnel-drive", client=http)
    folder_id, url = drive.ensure_folder("", "Example, Jordan")
    assert folder_id == "created-1"
    assert url.endswith("/created-1")
    same_id, _url = drive.ensure_folder(folder_id, "Example, Jordan")
    assert same_id == folder_id
    assert len(http.calls) == 2
    get_call, post_call = http.calls
    assert get_call[0] == "GET"
    assert get_call[2]["params"]["supportsAllDrives"] == "true"
    assert get_call[2]["params"]["driveId"] == "personnel-drive"
    assert post_call[0] == "POST"
    assert post_call[2]["params"]["supportsAllDrives"] == "true"
    assert post_call[2]["json"]["parents"] == ["personnel-drive"]
    assert folder_name("Jordan Example") == "Example, Jordan"
    assert folder_name("Jordan") == "Jordan"
