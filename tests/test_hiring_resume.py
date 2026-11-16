"""Resume prefill and the personnel Drive folder. Synthetic people only."""

from __future__ import annotations

import io
import json
import logging
import zipfile

import pytest

from fastapi.testclient import TestClient

from app.auth import User, issue_token
from app.hiring.pdf import soffice_command, text_pdf
from app.hiring.personnel_drive import PersonnelDrive, clear_personnel_drive_for_tests, folder_name, resume_filename, set_personnel_drive_for_tests
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
    disposition = downloaded.headers["content-disposition"]
    assert disposition == 'attachment; filename="Example_Jordan_resume.txt"'
    assert downloaded.headers["content-type"].startswith("text/plain")
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
        assert drive.uploads[0][1] == "Example_Jordan_resume.txt"
        assert drive.uploads[0][2] == RESUME
        again = client.put(f"/hiring/offers/{offer['id']}", headers=_alice(), json=_offer(uploaded.json()["resume_id"]))
        assert again.status_code == 200, again.text
        assert drive.folders == {"fld-1": "Example, Jordan"}
        assert len(drive.uploads) == 1
        sent = client.post(f"/hiring/offers/{offer['id']}/send", headers=_alice(), json={"channel": "outside"})
        assert sent.status_code == 200, sent.text
        assert [item[1] for item in drive.uploads] == ["Example_Jordan_resume.txt", "offer-letter.pdf"]
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


def test_every_offer_field_round_trips() -> None:
    body = {
        "display_name": "Jordan Example",
        "personal_email": "jordan.fields@example.com",
        "employment_type": "full-time",
        "candidate_full_name": "Jordan Example",
        "candidate_first_name": "Jordan",
        "candidate_credentials": "MYCRED",
        "position_title": "Staff Clinician",
        "supervisor_title": "Director",
        "supervisor_name": "Alex Supervisor",
        "hourly_rate": "40",
        "licensed_rate": "55",
        "caseload_range": "25-30",
        "qsehra_monthly_amount": "100",
        "qsehra_wait_days": "60",
        "offer_date": "11/16/2026",
        "start_date": "01/05/2027",
        "response_deadline": "12/16/2026",
        "direct_client_access": False,
    }
    created = client.post("/hiring/offers", headers=_alice(), json=body)
    assert created.status_code == 200, created.text
    offer_id = created.json()["id"]
    loaded = client.get(f"/hiring/offers/{offer_id}", headers=_alice())
    assert loaded.status_code == 200, loaded.text
    saved = loaded.json()
    fields = saved["fields"]
    assert saved["personal_email"] == "jordan.fields@example.com"
    assert saved["employment_type"] == "full-time"
    assert saved["direct_client_access"] is False
    assert fields["candidate_full_name"] == "Jordan Example"
    assert fields["candidate_first_name"] == "Jordan"
    assert fields["candidate_credentials"] == "MYCRED"
    assert fields["position_title"] == "Staff Clinician"
    assert fields["supervisor_title"] == "Director"
    assert fields["supervisor_name"] == "Alex Supervisor"
    assert fields["hourly_rate"] == "$40.00"
    assert fields["licensed_rate"] == "$55.00"
    assert fields["caseload_range"] == "25–30"
    assert fields["qsehra_monthly_amount"] == "$100"
    assert fields["qsehra_wait_days"] == "60"
    assert fields["offer_date"] == "11/16/2026"
    assert fields["start_date"] == "01/05/2027"
    assert fields["response_deadline"] == "12/16/2026"
    edited = dict(body)
    edited["candidate_credentials"] = "MYCRED"
    edited["hourly_rate"] = "$40.00"
    edited["licensed_rate"] = "$55.00"
    edited["qsehra_monthly_amount"] = "$100"
    edited["caseload_range"] = "25–30"
    updated = client.put(f"/hiring/offers/{offer_id}", headers=_alice(), json=edited)
    assert updated.status_code == 200, updated.text
    assert updated.json()["fields"]["candidate_credentials"] == "MYCRED"


def test_unconfigured_resume_is_stored_without_a_network_call(monkeypatch) -> None:
    monkeypatch.setenv("ATTUNE_RESUME_LLM", "gemini")
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_DRIVE_SA_JSON", raising=False)

    def explode(*_args, **_kwargs):
        raise AssertionError("resume reading contacted the network")

    monkeypatch.setattr("httpx.post", explode)
    payload = b"Jordan Example\nPersonal email: jordan.example@example.com\n"
    import time

    started = time.perf_counter()
    uploaded = client.post(
        "/hiring/resumes",
        headers=_alice(),
        files={"resume": ("jordan.txt", payload, "text/plain")},
    )
    assert time.perf_counter() - started < 1
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["warning"] == "Resume reading is not configured."
    assert body["fields"]["candidate_full_name"] == ""
    created = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer(body["resume_id"]) | {"candidate_credentials": "MYCRED", "personal_email": "jordan.unread@example.com"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["resume"] is True
    assert created.json()["fields"]["candidate_credentials"] == "MYCRED"
    downloaded = client.get(f"/hiring/offers/{created.json()['id']}/resume", headers=_alice())
    assert downloaded.status_code == 200
    assert downloaded.content == payload


def test_delete_requires_the_exact_candidate_name() -> None:
    created = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer() | {"personal_email": "jordan.delete@example.com"},
    )
    offer_id = created.json()["id"]
    for bad in ("", "jordan example", "Wrong Name", "Jordan  Example"):
        denied = client.post(f"/hiring/offers/{offer_id}/delete", headers=_alice(), json={"confirm": bad})
        assert denied.status_code == 400, denied.text
        assert client.get(f"/hiring/offers/{offer_id}", headers=_alice()).status_code == 200
    missing = client.post(f"/hiring/offers/{offer_id}/delete", headers=_alice(), json={})
    assert missing.status_code == 400, missing.text
    blank = client.post(f"/hiring/offers/{offer_id}/delete", headers=_alice(), json={"confirm": None})
    assert blank.status_code == 400, blank.text
    assert client.get(f"/hiring/offers/{offer_id}", headers=_alice()).status_code == 200
    removed = client.post(
        f"/hiring/offers/{offer_id}/delete",
        headers=_alice(),
        json={"confirm": "Jordan Example"},
    )
    assert removed.status_code == 200, removed.text
    assert client.get(f"/hiring/offers/{offer_id}", headers=_alice()).status_code == 404


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
    assert resume_filename("Jordan Example", "application/pdf") == "Example_Jordan_resume.pdf"
    assert resume_filename("Jordan Example", "application/vnd.openxmlformats-officedocument.wordprocessingml.document") == "Example_Jordan_resume.docx"


_RILEY_JSON = json.dumps(
    {
        "candidate_full_name": "Riley Sample",
        "candidate_first_name": "Riley",
        "personal_email": "riley.sample@example.com",
        "phone": "(555) 010-0199",
        "candidate_credentials": "MA, LPC-Candidate",
        "position_title": "",
    }
)
_RILEY_FENCED = "```json\n" + _RILEY_JSON + "\n```"


def _thinking_answer(text: str, finish: str = "STOP") -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"thought": True, "text": "The degree line is MA, LPC-Candidate. Emit only the JSON object."},
                        {"thoughtSignature": "opaque", "text": ""},
                        {"text": text},
                    ],
                },
                "finishReason": finish,
            },
            {"content": {"parts": [{"text": '{"candidate_full_name":"Wrong Person"}'}]}, "finishReason": "STOP"},
        ]
    }


class _VertexResponse:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body


def test_not_found_model_falls_back_and_logs_without_resume_text(monkeypatch, caplog) -> None:
    from app.hiring.resume import clear_model_cache

    clear_model_cache()
    monkeypatch.setenv("ATTUNE_RESUME_LLM", "gemini")
    monkeypatch.setenv(
        "GOOGLE_SA_JSON",
        '{"client_email":"sa@attune-clynotion.iam.gserviceaccount.com","private_key":"unused","project_id":"attune-clynotion"}',
    )
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-east4")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr("app.hiring.resume._gemini_token", lambda: "token")
    calls: list[str] = []

    def fake_get(url: str, **_kwargs):
        assert "publishers/google/models" in url
        return _VertexResponse(
            200,
            {
                "publisherModels": [
                    {"name": "publishers/google/models/gemini-2.0-flash", "launchStage": "GA"},
                    {"name": "publishers/google/models/gemini-3-flash", "launchStage": "GA"},
                    {"name": "publishers/google/models/gemini-3-flash-preview", "launchStage": "PUBLIC_PREVIEW"},
                ]
            },
        )

    def fake_post(url: str, **_kwargs):
        calls.append(url)
        if "/locations/global/" in url and "/models/gemini-3-flash:" in url:
            return _VertexResponse(
                404,
                {"error": {"code": 404, "status": "NOT_FOUND", "message": "model was not found", "details": [{"reason": "NOT_FOUND"}]}},
            )
        return _VertexResponse(
            200,
            {"candidates": [{"content": {"parts": [{"text": '{"candidate_full_name":"Jordan Example","personal_email":"jordan.example@example.com"}'}]}}]},
        )

    monkeypatch.setattr("app.hiring.resume.httpx.get", fake_get)
    monkeypatch.setattr("app.hiring.resume.httpx.post", fake_post)
    caplog.set_level(logging.WARNING)
    fields = read_resume(RESUME, "jordan.txt")
    assert fields["candidate_full_name"] == "Jordan Example"
    assert calls[0].startswith("https://aiplatform.googleapis.com/v1/projects/attune-clynotion/locations/global/")
    assert "/models/gemini-3-flash:" in calls[0]
    assert "resume_read_failed" in caplog.text
    assert "http_status=404" in caplog.text
    assert "jordan.example@example.com" not in caplog.text
    clear_model_cache()


def test_permission_error_does_not_try_another_model(monkeypatch, caplog) -> None:
    from app.hiring.resume import clear_model_cache

    clear_model_cache()
    monkeypatch.setenv("ATTUNE_RESUME_LLM", "gemini")
    monkeypatch.setenv(
        "GOOGLE_SA_JSON",
        '{"client_email":"sa@attune-clynotion.iam.gserviceaccount.com","private_key":"unused","project_id":"attune-clynotion"}',
    )
    monkeypatch.setattr("app.hiring.resume._gemini_token", lambda: "token")
    monkeypatch.setattr(
        "app.hiring.resume.httpx.get",
        lambda *_args, **_kwargs: _VertexResponse(200, {"publisherModels": []}),
    )
    calls: list[str] = []

    def fake_post(url: str, **_kwargs):
        calls.append(url)
        return _VertexResponse(
            403,
            {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "denied", "details": [{"reason": "IAM_PERMISSION_DENIED"}]}},
        )

    monkeypatch.setattr("app.hiring.resume.httpx.post", fake_post)
    caplog.set_level(logging.WARNING)
    with pytest.raises(ResumeReadError) as caught:
        read_resume(RESUME, "jordan.txt")
    assert caught.value.stage == "vertex"
    assert caught.value.http_status == 403
    assert len(calls) == 1
    assert "PERMISSION_DENIED" in caplog.text
    assert "jordan.example@example.com" not in caplog.text
    assert calls[0].startswith("https://aiplatform.googleapis.com/")
    assert "/locations/global/" in calls[0]
    clear_model_cache()


def test_resume_reader_health_reports_attempts(monkeypatch) -> None:
    from app.hiring.resume import clear_model_cache, reader_health

    clear_model_cache()
    monkeypatch.setenv("ATTUNE_RESUME_LLM", "gemini")
    monkeypatch.setenv(
        "GOOGLE_SA_JSON",
        '{"client_email":"sa@attune-clynotion.iam.gserviceaccount.com","private_key":"unused","project_id":"attune-clynotion"}',
    )
    monkeypatch.setattr("app.hiring.resume._gemini_token", lambda: "token")
    monkeypatch.setattr(
        "app.hiring.resume.httpx.get",
        lambda *_args, **_kwargs: _VertexResponse(
            200,
            {"publisherModels": [{"name": "publishers/google/models/gemini-3-flash", "launchStage": "GA"}]},
        ),
    )

    def fake_post(url: str, **kwargs):
        if "/models/gemini-3-flash:" in url and "/locations/global/" in url:
            return _VertexResponse(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "missing"}})
        config = kwargs["json"]["generationConfig"]
        assert config["maxOutputTokens"] >= 2048
        assert config["responseMimeType"] == "application/json"
        assert config["responseSchema"]["type"] == "OBJECT"
        assert config["thinkingConfig"] == {"thinkingLevel": "LOW"}
        assert url.startswith("https://aiplatform.googleapis.com/v1/projects/attune-clynotion/locations/global/")
        return _VertexResponse(200, _thinking_answer(_RILEY_FENCED))

    monkeypatch.setattr("app.hiring.resume.httpx.post", fake_post)
    report = reader_health()
    assert report["ok"] is True
    assert report["chosen_model"] == "gemini-3.1-flash"
    assert report["location"] == "global"
    assert report["extract_probe"]["ok"] is True
    assert report["extract_probe"]["stage"] == "grounded"
    assert report["extract_probe"]["fields"]["personal_email"] == "riley.sample@example.com"
    assert report["extract_probe"]["fields"]["candidate_credentials"] == "MA, LPC-Candidate"
    assert report["extract_probe"]["raw_text"] == ""
    assert report["project_id"] == "attune-clynotion"
    assert report["service_account"] == "sa@attune-clynotion.iam.gserviceaccount.com"
    assert "private_key" not in json.dumps(report)
    assert len(report["model_lists"]) == 2
    assert report["model_lists"][0]["http_status"] == 200
    assert "gemini-3-flash" in report["model_lists"][0]["model_ids"]
    assert report["first_attempt"]["http_status"] == 404
    assert "missing" in report["first_attempt"]["body"]
    assert report["attempts"][0] == {"model": "gemini-3-flash", "location": "global", "status": 404}
    assert report["attempts"][1]["model"] == "gemini-3.1-flash"
    assert report["attempts"][1]["status"] == 200
    clear_model_cache()


def test_pdf_extraction_failure_names_the_stage(caplog) -> None:
    caplog.set_level(logging.WARNING)
    with pytest.raises(ResumeReadError) as caught:
        read_resume(b"%PDF-1.4\nnot-a-pdf", "bad.pdf")
    assert caught.value.stage == "text_extraction"
    assert "stage=text_extraction" in caplog.text
    assert "not-a-pdf" not in caplog.text


def test_resume_reader_health_is_admin_only_and_offline() -> None:
    from app.auth import User, issue_token

    ok = client.get("/hiring/resume-reader/health", headers=_alice())
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["ok"] is True
    assert body["mode"] == "mock"
    assert body["pypdf"] is True
    assert body["extract_probe"]["stage"] == "grounded"
    assert body["extract_probe"]["fields"]["candidate_full_name"] == "Riley Sample"
    assert body["extract_probe"]["raw_text"] == ""
    assert body["scope"] == "https://www.googleapis.com/auth/cloud-platform"
    other = User("user-bob", "bob", "practice-a", "bob@example.com")
    denied = client.get("/hiring/resume-reader/health", headers={"Authorization": f"Bearer {issue_token(other)}"})
    assert denied.status_code == 403


def test_thinking_model_parts_fences_and_finish_reason_parse() -> None:
    from app.hiring.resume import PROBE_RESUME, _json_object, _response_text

    text, finish = _response_text(_thinking_answer(_RILEY_FENCED))
    assert finish == "STOP"
    assert "Wrong Person" not in text
    assert "Emit only" not in text
    fields = _json_object(text)
    grounded = ground_fields(PROBE_RESUME, fields)
    assert grounded["candidate_full_name"] == "Riley Sample"
    assert grounded["candidate_first_name"] == "Riley"
    assert grounded["personal_email"] == "riley.sample@example.com"
    assert grounded["phone"] == "(555) 010-0199"
    assert grounded["candidate_credentials"] == "MA, LPC-Candidate"

    thought_only = {
        "candidates": [
            {
                "content": {"parts": [{"thought": True, "text": "```json\n" + _RILEY_JSON + "\n```"}]},
                "finishReason": "STOP",
            }
        ]
    }
    thought_text, thought_finish = _response_text(thought_only)
    assert thought_finish == "STOP"
    assert _json_object(thought_text)["candidate_credentials"] == "MA, LPC-Candidate"

    truncated = _thinking_answer('{"candidate_full_name": "Riley', finish="MAX_TOKENS")
    partial, reason = _response_text(truncated)
    assert reason == "MAX_TOKENS"
    with pytest.raises(ValueError):
        _json_object(partial)


def test_upload_uses_the_cached_global_flash_model(monkeypatch) -> None:
    from app.hiring.resume import PROBE_RESUME, clear_model_cache

    clear_model_cache()
    monkeypatch.setenv("ATTUNE_RESUME_LLM", "gemini")
    monkeypatch.setenv(
        "GOOGLE_SA_JSON",
        '{"client_email":"sa@attune-clynotion.iam.gserviceaccount.com","private_key":"unused","project_id":"attune-clynotion"}',
    )
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr("app.hiring.resume._gemini_token", lambda: "token")
    lists = {"left": 1}

    def fake_get(url: str, **_kwargs):
        if lists["left"]:
            lists["left"] -= 1
            return _VertexResponse(
                200,
                {
                    "publisherModels": [
                        {"name": "publishers/google/models/gemini-2.5-flash"},
                        {"name": "publishers/google/models/gemini-3.8-flash"},
                        {"name": "publishers/google/models/gemini-3.8-flash-preview"},
                    ]
                },
            )
        return _VertexResponse(404, {"error": {"status": "NOT_FOUND", "message": "list missing"}})

    posts: list[tuple[str, dict]] = []

    def fake_post(url: str, **kwargs):
        posts.append((url, kwargs["json"]))
        return _VertexResponse(200, _thinking_answer(_RILEY_FENCED))

    monkeypatch.setattr("app.hiring.resume.httpx.get", fake_get)
    monkeypatch.setattr("app.hiring.resume.httpx.post", fake_post)
    first = read_resume(PROBE_RESUME.encode(), "riley.txt")
    second = read_resume(PROBE_RESUME.encode(), "riley.txt")
    assert first["personal_email"] == "riley.sample@example.com"
    assert second["candidate_credentials"] == "MA, LPC-Candidate"
    assert len(posts) == 2
    for url, body in posts:
        assert url == (
            "https://aiplatform.googleapis.com/v1/projects/attune-clynotion/locations/global"
            "/publishers/google/models/gemini-3.8-flash:generateContent"
        )
        assert "us-central1" not in url
        assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}
        assert body["generationConfig"]["responseMimeType"] == "application/json"
    clear_model_cache()


def test_extract_probe_reports_parse_failure_without_a_secret(monkeypatch) -> None:
    from app.hiring.resume import clear_model_cache, reader_health

    clear_model_cache()
    monkeypatch.setenv("ATTUNE_RESUME_LLM", "gemini")
    monkeypatch.setenv(
        "GOOGLE_SA_JSON",
        '{"client_email":"sa@attune-clynotion.iam.gserviceaccount.com","private_key":"secret-value","project_id":"attune-clynotion"}',
    )
    monkeypatch.setattr("app.hiring.resume._gemini_token", lambda: "token")
    monkeypatch.setattr(
        "app.hiring.resume.httpx.get",
        lambda *_args, **_kwargs: _VertexResponse(
            200,
            {"publisherModels": [{"name": "publishers/google/models/gemini-3.8-flash"}]},
        ),
    )
    monkeypatch.setattr(
        "app.hiring.resume.httpx.post",
        lambda url, **_kwargs: _VertexResponse(
            200,
            _thinking_answer('{"candidate_full_name":', finish="MAX_TOKENS"),
        ),
    )
    report = reader_health()
    assert report["ok"] is True
    assert report["chosen_model"] == "gemini-3.8-flash"
    assert report["location"] == "global"
    probe = report["extract_probe"]
    assert probe["ok"] is False
    assert probe["stage"] == "parse"
    assert probe["error_class"] == "ValueError"
    assert probe["finish_reason"] == "MAX_TOKENS"
    assert probe["raw_text"].startswith('{"candidate_full_name":')
    assert len(probe["raw_text"]) <= 300
    assert "secret-value" not in json.dumps(report)
    assert "private_key" not in json.dumps(report)
    clear_model_cache()
