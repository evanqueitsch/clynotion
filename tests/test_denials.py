"""Denial analysis phase A. Synthetic rows only. No model calls."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient

from app.access.permissions import CHRISTINE_EMAIL, PHI_CLAIMS, DENIALS_TOOL, admin_permissions
from app.access.resolve import has_permission, permissions_for
from app.access.store import access_store
from app.auth import User, issue_token
from app.denials.store import _now, denials_store
from app.denials.triage import ACTION_LABELS, CLINICIAN_REVIEW, COMPLIANCE_NOTE, decide
from app.hr.time_entry import MELISSA_EMAIL
from app.main import app
from app.platform.due import due_engine

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "denials"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
MAPPING = {
    "Payer": "payer",
    "CPT": "cpt",
    "Modifiers": "modifiers",
    "POS": "pos",
    "Diagnosis": "diagnosis",
    "Service month": "service_month",
    "Billed": "billed",
    "Allowed": "allowed",
    "Paid": "paid",
    "Claim status": "claim_status",
    "CARC": "carc",
    "RARC": "rarc",
    "Remark": "remark",
    "Clinician code": "clinician_code",
    "Claim key": "claim_key",
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def denials_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("DENIALS_FERNET_KEY", key)
    monkeypatch.setenv("ATTUNE_DENIALS_PERSISTENCE", "memory")
    denials_store.reset()
    return key


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _alice() -> User:
    return User("user-alice", "alice", "practice-a", "alice@example.com")


def _christine() -> User:
    access_store.set_assignment("practice-a", CHRISTINE_EMAIL, "billing")
    return User("user-christine", "billing", "practice-a", CHRISTINE_EMAIL)


def _melissa() -> User:
    access_store.ensure("practice-hfc")
    return User("user-melissa", "Melissa", "practice-hfc", MELISSA_EMAIL)


def _clinician() -> User:
    email = "clinician@example.com"
    access_store.set_assignment("practice-a", email, "clinician")
    return User("user-clin", "Clinician", "practice-a", email)


def _upload(client: TestClient, user: User, name: str, file_type: str) -> dict:
    payload = (FIXTURES / name).read_bytes()
    response = client.post(
        "/denials/uploads",
        headers=_auth(user),
        data={"file_type": file_type, "mapping": json.dumps(MAPPING)},
        files={"upload": (name, payload, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_permissions_split_summary_full_and_none(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    melissa = _melissa()
    christine = _christine()
    clinician = _clinician()
    alice = _alice()
    billing = access_store.permissions_for_role("practice-a", "billing")
    assert DENIALS_TOOL not in billing
    assert PHI_CLAIMS not in billing
    assert "tool:denials_summary" not in billing
    assert has_permission(melissa, "tool:denials_summary") is True
    assert has_permission(melissa, DENIALS_TOOL) is False
    assert has_permission(melissa, PHI_CLAIMS) is False
    assert has_permission(christine, DENIALS_TOOL) is True
    assert has_permission(christine, PHI_CLAIMS) is True
    assert DENIALS_TOOL in admin_permissions()
    assert PHI_CLAIMS in admin_permissions()
    assert "tool:denials_summary" not in admin_permissions()
    assert has_permission(clinician, DENIALS_TOOL) is False
    assert has_permission(clinician, "tool:denials_summary") is False
    assert has_permission(alice, DENIALS_TOOL) is True

    other = User("user-bill", "Other", "practice-a", "other.billing@heart4change.org")
    access_store.set_assignment("practice-a", other.email, "billing")
    assert has_permission(other, DENIALS_TOOL) is False
    saved = access_store.update_role_permissions(
        "practice-a",
        "billing",
        ["tool:time_entry", DENIALS_TOOL, PHI_CLAIMS, "tool:denials_summary"],
    )
    assert DENIALS_TOOL not in saved["permissions"]
    assert PHI_CLAIMS not in saved["permissions"]
    assert has_permission(other, DENIALS_TOOL) is False

    _upload(client, alice, "synthetic_filed_claims.csv", "filed_claims")
    summary = client.get("/denials/dashboard", headers=_auth(melissa))
    assert summary.status_code == 200, summary.text
    assert "items" not in summary.json()
    assert summary.json()["access"] == "summary"
    assert summary.json()["totals"]["claims"] == 0
    full = client.get("/denials/dashboard", headers=_auth(christine))
    assert full.status_code == 200, full.text
    assert "items" not in full.json()
    assert full.json()["access"] == "full"
    assert full.json()["totals"]["claims"] == 6
    work = client.get("/denials/worklist", headers=_auth(christine))
    assert work.status_code == 200
    assert client.get("/denials/worklist", headers=_auth(melissa)).status_code == 403
    assert client.get("/denials/export", headers=_auth(melissa)).status_code == 403
    assert client.get("/denials/worklist", headers=_auth(clinician)).status_code == 403
    assert client.get("/denials", headers=_auth(clinician)).status_code == 403
    home = client.get("/home", headers=_auth(clinician)).json()
    assert all(tool["id"] != "denials" for unit in home["units"] for tool in unit["tools"])


def test_deidentification_rejects_sample_files(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    alice = _alice()
    rejected = FIXTURES / "rejected"
    for path in sorted(rejected.glob("*.csv")):
        response = client.post(
            "/denials/uploads",
            headers=_auth(alice),
            data={"file_type": "filed_claims", "mapping": json.dumps(MAPPING)},
            files={"upload": (path.name, path.read_bytes(), "text/csv")},
        )
        assert response.status_code == 422, path.name
        detail = response.json()["detail"]
        assert detail.startswith("This file must be de-identified first.")
        assert "Date of Birth" not in detail or "date of birth" in detail.lower()
        assert "123-45-6789" not in detail
        assert "person@example.com" not in detail
        assert "Jane Doe" not in detail
        assert "123 Main Street" not in detail
    name_value = b"Payer,CPT,Claim status,Claim key\nSample Payer A,Jane Doe,denied,KEY-910\n"
    named = client.post(
        "/denials/uploads",
        headers=_auth(alice),
        data={"file_type": "filed_claims", "mapping": json.dumps(MAPPING)},
        files={"upload": ("names.csv", name_value, "text/csv")},
    )
    assert named.status_code == 422
    assert "patient name" in named.json()["detail"]
    dated = b"Payer,Service month,Claim status,Claim key\nSample Payer A,2026-01-15,denied,KEY-911\n"
    full_date = client.post(
        "/denials/uploads",
        headers=_auth(alice),
        data={"file_type": "filed_claims", "mapping": json.dumps(MAPPING)},
        files={"upload": ("dates.csv", dated, "text/csv")},
    )
    assert full_date.status_code == 422
    assert "date of birth" in full_date.json()["detail"]


def test_upload_dedupes_and_matches_expected_totals(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    alice = _alice()
    first = _upload(client, alice, "synthetic_filed_claims.csv", "filed_claims")
    assert first["added"] == 6
    again = _upload(client, alice, "synthetic_filed_claims.csv", "filed_claims")
    assert again["added"] == 0
    assert again["skipped_duplicates"] == 6
    second = _upload(client, alice, "synthetic_payment_report.csv", "payment_reports")
    assert second["added"] == 1
    assert second["skipped_duplicates"] == 1
    dashboard = client.get("/denials/dashboard", headers=_auth(alice)).json()
    assert dashboard["totals"] == EXPECTED["totals"]
    assert dashboard["by_payer"] == EXPECTED["by_payer"]
    assert dashboard["by_cpt"] == EXPECTED["by_cpt"]
    assert dashboard["by_modifier_pos"] == EXPECTED["by_modifier_pos"]
    assert dashboard["by_category"] == EXPECTED["by_category"]
    assert dashboard["by_carc"] == EXPECTED["by_carc"]
    assert dashboard["trend"] == EXPECTED["trend"]
    assert dashboard["phi_mode"] is False
    assert dashboard["compliance_note"] == COMPLIANCE_NOTE
    work = client.get("/denials/worklist", headers=_auth(alice)).json()
    actions = {item["claim_key"]: item["action"] for item in work["items"]}
    assert actions == EXPECTED["worklist_actions"]
    assert all(item["action_label"] for item in work["items"])
    for label in ACTION_LABELS.values():
        lowered = label.lower()
        assert "change the cpt" not in lowered
        assert "change the diagnosis" not in lowered
    category, action = decide("denied", "11")
    assert category == "documentation"
    assert action == CLINICIAN_REVIEW
    assert ACTION_LABELS[action] == "Flag for clinician review only"


def test_remark_email_is_scrubbed_and_not_audited(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    alice = _alice()
    raw = (
        "Payer,CPT,Claim status,Claim key,Remark\n"
        "Sample Payer A,90837,denied,KEY-912,missing info person@example.com\n"
    )
    response = client.post(
        "/denials/uploads",
        headers=_auth(alice),
        data={
            "file_type": "filed_claims",
            "mapping": json.dumps(
                {"Payer": "payer", "CPT": "cpt", "Claim status": "claim_status", "Claim key": "claim_key", "Remark": "remark"}
            ),
        },
        files={"upload": ("remark.csv", raw.encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    work = client.get("/denials/worklist", headers=_auth(alice)).json()
    assert work["items"][0]["remark"] == "missing info [removed]"
    audit = client.get("/denials/settings", headers=_auth(alice)).json()["audit"]
    dumped = json.dumps(audit)
    assert "person@example.com" not in dumped
    assert "Sample Payer" not in dumped
    assert "KEY-912" not in dumped
    assert "90837" not in dumped


def test_work_status_persists_and_clears(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    alice = _alice()
    _upload(client, alice, "synthetic_filed_claims.csv", "filed_claims")
    work = client.get("/denials/worklist", headers=_auth(alice)).json()["items"]
    target = work[0]
    saved = client.patch(
        f"/denials/work/{target['id']}",
        headers=_auth(alice),
        json={"owner": "Billing", "work_status": "in_progress"},
    )
    assert saved.status_code == 200, saved.text
    again = client.get("/denials/worklist", headers=_auth(alice)).json()["items"]
    match = next(item for item in again if item["id"] == target["id"])
    assert match["work_status"] == "in_progress"
    assert match["owner"] == "Billing"
    cleared = client.patch(
        f"/denials/work/{target['id']}",
        headers=_auth(alice),
        json={"owner": "", "work_status": "open"},
    )
    assert cleared.status_code == 200
    final = client.get("/denials/worklist", headers=_auth(alice)).json()["items"]
    match = next(item for item in final if item["id"] == target["id"])
    assert match["work_status"] == "open"
    assert match["owner"] == ""


def test_retention_floor_confirmation_and_audit(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    alice = _alice()
    _upload(client, alice, "synthetic_filed_claims.csv", "filed_claims")
    low_days = client.put(
        "/denials/settings",
        headers=_auth(alice),
        json={"upload_retention_days": 29, "row_retention_months": 24, "confirmation": "PURGE DENIALS"},
    )
    assert low_days.status_code == 422
    low_months = client.put(
        "/denials/settings",
        headers=_auth(alice),
        json={"upload_retention_days": 90, "row_retention_months": 5, "confirmation": "PURGE DENIALS"},
    )
    assert low_months.status_code == 422
    locked = client.post("/denials/phi-mode", headers=_auth(alice), json={"phi_mode": True})
    assert locked.status_code == 409
    assert "cannot be turned on" in locked.json()["detail"]
    extra = client.put(
        "/denials/settings",
        headers=_auth(alice),
        json={"upload_retention_days": 90, "row_retention_months": 24, "audit_retention_years": 1},
    )
    assert extra.status_code == 422
    payload = denials_store._read()
    practice = payload["practices"]["practice-a"]
    aged = (_now() - timedelta(days=400)).isoformat()
    for item in practice["uploads"]:
        item["uploaded_at"] = aged
    for item in practice["rows"]:
        item["imported_at"] = aged
    denials_store._write(payload)
    blocked = client.put(
        "/denials/settings",
        headers=_auth(alice),
        json={"upload_retention_days": 30, "row_retention_months": 6},
    )
    assert blocked.status_code == 409
    assert "PURGE DENIALS" in blocked.json()["detail"]
    confirmed = client.put(
        "/denials/settings",
        headers=_auth(alice),
        json={"upload_retention_days": 30, "row_retention_months": 6, "confirmation": "PURGE DENIALS"},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["upload_retention_days"] == 30
    assert body["row_retention_months"] == 6
    assert body["audit_retention_years"] == 6
    assert body["audit_retention_editable"] is False
    assert body["phi_mode"] is False
    assert all(item["checked"] is False and item["enabled"] is False for item in body["phi_checklist"])
    assert client.get("/denials/dashboard", headers=_auth(alice)).json()["totals"]["claims"] == 0
    restored = client.put(
        "/denials/settings",
        headers=_auth(alice),
        json={"upload_retention_days": 90, "row_retention_months": 24},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["upload_retention_days"] == 90
    actions = [item["action"] for item in restored.json()["audit"]]
    assert "upload" in actions
    assert "view" in actions
    assert "retention" in actions
    dumped = json.dumps(restored.json()["audit"])
    assert "Sample Payer" not in dumped
    assert "KEY-001" not in dumped
    assert "F41.1" not in dumped


def test_delete_batch_empties_the_dashboard(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    alice = _alice()
    uploaded = _upload(client, alice, "synthetic_filed_claims.csv", "filed_claims")
    removed = client.delete(f"/denials/uploads/{uploaded['upload_id']}", headers=_auth(alice))
    assert removed.status_code == 200, removed.text
    assert removed.json()["rows"] == 6
    dashboard = client.get("/denials/dashboard", headers=_auth(alice)).json()
    assert dashboard["totals"]["claims"] == 0
    assert client.get("/denials/worklist", headers=_auth(alice)).json()["items"] == []


def test_missing_key_refuses_uploads(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DENIALS_FERNET_KEY", raising=False)
    denials_store.reset()
    alice = _alice()
    dashboard = client.get("/denials/dashboard", headers=_auth(alice))
    assert dashboard.status_code == 200
    assert dashboard.json()["configured"] is False
    upload = client.post(
        "/denials/uploads",
        headers=_auth(alice),
        data={"file_type": "filed_claims", "mapping": json.dumps(MAPPING)},
        files={"upload": ("synthetic_filed_claims.csv", (FIXTURES / "synthetic_filed_claims.csv").read_bytes(), "text/csv")},
    )
    assert upload.status_code == 409
    assert upload.json()["detail"] == "Denial analysis is not configured."


def test_store_uses_its_own_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    denials_key = Fernet.generate_key().decode("ascii")
    other_key = Fernet.generate_key().decode("ascii")
    path = tmp_path / "denials.enc"
    monkeypatch.setenv("DENIALS_FERNET_KEY", denials_key)
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", other_key)
    monkeypatch.setenv("ATTUNE_DENIALS_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_DENIALS_DATA_PATH", str(path))
    denials_store.reset()
    client = TestClient(app)
    alice = _alice()
    _upload(client, alice, "synthetic_filed_claims.csv", "filed_claims")
    blob = path.read_bytes()
    with pytest.raises(InvalidToken):
        Fernet(other_key.encode("ascii")).decrypt(blob)
    assert b"Sample Payer" not in blob
    assert b"KEY-001" not in blob
    reloaded = denials_store.dashboard("practice-a", "alice@example.com")
    assert reloaded["totals"]["claims"] == 6


def test_due_is_filtered_by_permission_and_skips_denials(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    due_engine.reset()
    past = (_now() - timedelta(hours=2)).isoformat()
    for domain, ref in (
        ("comply", "comply-1"),
        ("credentialing", "cred-1"),
        ("grow", "grow-1"),
        ("platform", "platform-1"),
    ):
        due_engine.upsert(
            practice_id="practice-a",
            domain=domain,  # type: ignore[arg-type]
            source="manual",
            title=f"{domain} item",
            owner_user_id="user-alice",
            due_at=past,
            href="/",
            source_ref=ref,
        )
    alice = _alice()
    everyone = {item["domain"] for item in client.get("/due", headers=_auth(alice)).json()["overdue"]}
    assert {"comply", "credentialing", "grow", "platform"} <= everyone
    email = "limited@example.com"
    access_store.set_assignment("practice-a", email, "clinician")
    access_store.update_role_permissions("practice-a", "clinician", ["tool:compliance_registry"])
    limited = User("user-limited", "Limited", "practice-a", email)
    domains = {item["domain"] for item in client.get("/due", headers=_auth(limited)).json()["overdue"]}
    assert domains == {"comply"}
    access_store.update_role_permissions("practice-a", "clinician", ["tool:time_entry"])
    clinician = _clinician()
    assert client.get("/due", headers=_auth(clinician)).json() == {"overdue": [], "this_week": []}
    _upload(client, alice, "synthetic_filed_claims.csv", "filed_claims")
    due = client.get("/due", headers=_auth(alice)).json()
    dumped = json.dumps(due)
    assert "KEY-001" not in dumped
    assert "Sample Payer" not in dumped
    home = client.get("/home", headers=_auth(alice)).json()
    home_dump = json.dumps(home)
    assert "KEY-001" not in home_dump
    assert "Sample Payer" not in home_dump
    denial = next(tool for unit in home["units"] for tool in unit["tools"] if tool["id"] == "denials")
    assert denial["count"] == 5
    hidden = next(item for item in due["overdue"] if item["domain"] == "credentialing")
    access_store.update_role_permissions("practice-a", "clinician", ["tool:compliance_registry"])
    blocked = client.post(f"/due/{hidden['obligation_id']}/complete", headers=_auth(limited))
    assert blocked.status_code == 404


def _xlsx_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    import io
    import zipfile
    from xml.sax.saxutils import escape

    sheet_rows = []
    for index, row in enumerate([headers, *rows], start=1):
        cells = []
        for column, value in enumerate(row, start=1):
            letters = chr(64 + column)
            cells.append(f'<c r="{letters}{index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        sheet_rows.append(f'<row r="{index}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def test_xlsx_upload_and_dob_column_are_distinguished(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    alice = _alice()
    import csv

    text = (FIXTURES / "synthetic_filed_claims.csv").read_text(encoding="utf-8")
    parsed = list(csv.reader(text.splitlines()))
    payload = _xlsx_bytes(parsed[0], parsed[1:])
    (FIXTURES / "synthetic_filed_claims.xlsx").write_bytes(payload)
    response = client.post(
        "/denials/uploads",
        headers=_auth(alice),
        data={"file_type": "filed_claims", "mapping": json.dumps(MAPPING)},
        files={"upload": ("synthetic_filed_claims.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["added"] == 6
    dob = _xlsx_bytes(["Payer", "Date of Birth", "Claim status", "Claim key"], [["Sample Payer A", "1990-04-02", "denied", "KEY-950"]])
    (FIXTURES / "rejected" / "date_of_birth.xlsx").write_bytes(dob)
    rejected = client.post(
        "/denials/uploads",
        headers=_auth(alice),
        data={"file_type": "filed_claims", "mapping": json.dumps(MAPPING)},
        files={"upload": ("date_of_birth.xlsx", dob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"].startswith("This file must be de-identified first.")
    assert "1990-04-02" not in rejected.json()["detail"]


def test_denials_package_does_not_call_a_model() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "denials"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "anthropic" not in text
        assert "print(" not in text
        assert "app.providers" not in text


def test_summary_user_sees_the_card_without_rows(client: TestClient, denials_key: str) -> None:
    _ = denials_key
    melissa = _melissa()
    page = client.get("/", headers=_auth(melissa))
    assert page.status_code == 200
    home = client.get("/home", headers=_auth(melissa)).json()
    ids = [tool["id"] for unit in home["units"] for tool in unit["tools"]]
    assert "denials" in ids
    assert has_permission(melissa, "tool:denials_summary") is True
    assert DENIALS_TOOL not in permissions_for(melissa)
