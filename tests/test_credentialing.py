"""Credentialing phase 1: store, grants, guards, masked IDs, and CSV import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.access.guard import required_permission
from app.access.permissions import (
    MANAGE_ROLES,
    PAYROLL_DASHBOARD,
    TIME_ENTRY,
)
from app.access.resolve import has_permission
from app.access.store import access_store
from app.auth import User, issue_token
from app.credentialing.store import CredentialingStore, credentialing_store
from app.crypto import decrypt_utf8, generate_key
from app.main import app
from app.platform.routes import _time_entry_nav
from tests.test_time_entry import _alice

PAYERS = """payer,family_members,type,accepts_licenses,notes
QA Test Payer,QA Family,commercial,LCSW,fake payer
QA Masters Payer,,eap,masters,fake masters
QA Strict Payer,,commercial,LCSW,fake strict
"""
PEOPLE = """subject,kind,license_type,npi,tin,license_number,caqh_id,hire_date,device
QA Test Practice,practice,,,,,,,
QA Test Clinician,clinician,LSW,123456789,,,,2020-01-15,QA-IPAD
"""
GRID = """payer,QA Test Practice,QA Test Clinician
QA Test Payer,Y,2026-04-01
QA Masters Payer,?,
QA Strict Payer,,
"""
NPI = "123456789"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _files(payers: str = PAYERS, people: str = PEOPLE, grid: str = GRID) -> dict:
    return {
        "payers": ("payers.csv", payers, "text/csv"),
        "people": ("people.csv", people, "text/csv"),
        "grid": ("grid.csv", grid, "text/csv"),
    }


def _assign(perms: list[str]) -> User:
    user = User("user-cred", "Credential Tester", "practice-a", "cred@heart4change.org")
    access_store.set_assignment("practice-a", user.email, "clinician")
    access_store.update_role_permissions("practice-a", "clinician", perms)
    return user


def test_time_entry_id_route_guard_and_nav_are_unchanged() -> None:
    from app.platform.catalog import CATALOG_ITEMS

    item = next(row for row in CATALOG_ITEMS if row["id"] == "time_entry")
    assert item["id"] == "time_entry"
    assert item["status"] == "live"
    assert item["href"] == "/#time"
    assert required_permission("/hr/time/me") == TIME_ENTRY
    assert required_permission("/hr/time/entries") == TIME_ENTRY
    assert required_permission("/hr/time/settings") == MANAGE_ROLES
    assert required_permission("/hr/time/self-test") == MANAGE_ROLES
    assert required_permission("/hr/time/directory/refresh") == MANAGE_ROLES
    assert required_permission("/hr/time/gmail-send") == MANAGE_ROLES
    assert required_permission("/hr/time/week") == PAYROLL_DASHBOARD
    assert required_permission("/hr/time/week.csv") == PAYROLL_DASHBOARD
    assert required_permission("/credentialing/grid") == "tool:credentialing"
    source = Path("app/platform/routes.py").read_text(encoding="utf-8")
    assert "def _time_entry_nav(" in source
    assert _time_entry_nav.__name__ == "_time_entry_nav"


def test_catalog_order_retired_tool_and_billing_placeholder(client: TestClient) -> None:
    units = client.get("/catalog", headers=_alice()).json()
    hr = next(unit for unit in units if unit["id"] == "hr")
    assert hr["name"] == "HR & Credentialing"
    assert [tool["id"] for tool in hr["tools"]] == [
        "employee_view",
        "time_entry",
        "credentialing",
        "hiring_onboarding",
    ]
    assert hr["tools"][0]["coming_soon"] is True
    assert hr["tools"][1]["id"] == "time_entry"
    cred = hr["tools"][2]
    assert cred["status"] == "live"
    assert cred["href"] == "/#credentialing"
    assert cred["requires_explicit_grant"] is True
    finance = next(unit for unit in units if unit["id"] == "finance")
    billing = next(tool for tool in finance["tools"] if tool["id"] == "billing")
    assert billing["status"] == "planned"
    assert billing["coming_soon"] is True
    assert billing["href"] == ""
    from app.platform.catalog import CATALOG_ITEMS

    employee = next(row for row in CATALOG_ITEMS if row["id"] == "employee_view")
    assert employee["legacy_ids"] == ["OPS-1"]
    assert all(row["id"] != "time_pto_bonus" for row in CATALOG_ITEMS)
    retired = next(row for row in CATALOG_ITEMS if row["id"] == "onboarding_compliance")
    assert retired["status"] == "retired"
    registry = client.get("/access/roles", headers=_alice()).json()["permissions"]
    ids = [row["id"] for row in registry]
    assert "tool:onboarding_compliance" not in ids
    assert "tool:credentialing" in ids
    assert "capability:quickbooks_money" in ids
    money = next(row for row in registry if row["id"] == "capability:quickbooks_money")
    assert money["sensitive"] is True
    page = Path("app/static/index.html").read_text(encoding="utf-8")
    assert "Only grant them on purpose." in page
    assert 'id="credentialing-panel"' in page


def test_grants_migration_only_adds(tmp_path, monkeypatch) -> None:
    from app.crypto import encrypt_utf8

    monkeypatch.setenv("ATTUNE_ACCESS_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_ACCESS_DATA_PATH", str(tmp_path / "access_roles.enc"))
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())
    original = {
        "version": 1,
        "extra_top": "keep",
        "practices": {
            "practice-hfc": {
                "extra_evan": {"keep": True},
                "roles": {
                    "billing": {
                        "name": "Billing",
                        "permissions": ["tool:time_entry", "tool:intake_log"],
                    },
                    "clinician": {
                        "name": "Clinician",
                        "permissions": ["tool:time_entry", "tool:eligibility"],
                    },
                    "intake": {"name": "Intake", "permissions": ["tool:time_entry"]},
                    "clinical_supervisor": {
                        "name": "Clinical Supervisor",
                        "permissions": ["tool:time_entry"],
                    },
                    "admin": {"name": "Admin", "permissions": ["tool:time_entry", "tool:intake_log"]},
                },
                "assignments": {
                    "operations@heart4change.org": "admin",
                    "clinician@heart4change.org": "clinician",
                },
            }
        },
    }
    path = tmp_path / "access_roles.enc"
    path.write_bytes(encrypt_utf8(json.dumps(original)))
    access_store._memory = None
    billing = access_store.permissions_for_role("practice-hfc", "billing")
    assert billing[:2] == ["tool:time_entry", "tool:intake_log"]
    for permission in (
        "tool:credentialing",
        "capability:credentialing_manage",
        "capability:credentialing_payers",
        "capability:credentialing_sensitive",
        "tool:billing",
    ):
        assert permission in billing
    clinician = access_store.permissions_for_role("practice-hfc", "clinician")
    assert clinician == ["tool:time_entry", "tool:eligibility"]
    assert access_store.role_id_for("practice-hfc", "clinician@heart4change.org") == "clinician"
    assert access_store.role_id_for("practice-hfc", "billing@heart4change.org") == ""
    saved = json.loads(decrypt_utf8(path.read_bytes()))
    assert saved["extra_top"] == "keep"
    practice = saved["practices"]["practice-hfc"]
    assert practice["extra_evan"] == {"keep": True}
    assert practice["assignments"] == original["practices"]["practice-hfc"]["assignments"]
    backup = json.loads(decrypt_utf8((tmp_path / "access_roles.enc.bak-grants-v1").read_bytes()))
    assert backup["practices"]["practice-hfc"]["roles"]["billing"]["permissions"] == [
        "tool:time_entry",
        "tool:intake_log",
    ]
    again = access_store.permissions_for_role("practice-hfc", "billing")
    assert again == billing


def test_unit_hr_does_not_unlock_credentialing(client: TestClient) -> None:
    user = _assign(["unit:hr"])
    assert has_permission(user, "tool:time_entry") is True
    assert has_permission(user, "tool:credentialing") is False
    assert client.get("/credentialing/grid", headers=_auth(user)).status_code == 403
    home = client.get("/home", headers=_auth(user)).json()
    ids = [tool["id"] for unit in home["units"] for tool in unit["tools"]]
    assert "time_entry" in ids
    assert "credentialing" not in ids
    assert "employee_view" not in ids


def test_writes_and_reveals_require_the_capability(client: TestClient) -> None:
    imported = client.post("/credentialing/import/commit", headers=_alice(), files=_files())
    assert imported.status_code == 200, imported.text
    grid = client.get("/credentialing/grid", headers=_alice()).json()
    subject = next(row for row in grid["subjects"] if row["name"] == "QA Test Clinician")
    payer = next(row for row in grid["payers"] if row["name"] == "QA Test Payer")
    viewer = _assign(["tool:credentialing", "tool:time_entry"])
    assert client.get("/credentialing/grid").status_code == 401
    assert client.get("/credentialing/grid", headers=_auth(viewer)).status_code == 200
    writes = [
        client.put(
            "/credentialing/subjects",
            headers=_auth(viewer),
            json={"name": "QA Test Clinician", "kind": "clinician"},
        ),
        client.post(
            "/credentialing/payers",
            headers=_auth(viewer),
            json={"name": "QA Other", "type": "commercial"},
        ),
        client.put(
            f"/credentialing/payers/{payer['id']}",
            headers=_auth(viewer),
            json={"name": "QA Test Payer", "type": "commercial"},
        ),
        client.post(f"/credentialing/payers/{payer['id']}/archive", headers=_auth(viewer)),
        client.put(
            "/credentialing/enrollments",
            headers=_auth(viewer),
            json={"subject_id": subject["id"], "payer_id": payer["id"], "status": "in_network"},
        ),
        client.post(
            f"/credentialing/subjects/{subject['id']}/archive",
            headers=_auth(viewer),
        ),
        client.post("/credentialing/import/preview", headers=_auth(viewer)),
        client.post("/credentialing/import/commit", headers=_auth(viewer)),
        client.post(
            "/credentialing/reveal",
            headers=_auth(viewer),
            json={"subject_id": subject["id"], "field": "npi"},
        ),
        client.post(
            "/credentialing/copy",
            headers=_auth(viewer),
            json={"subject_id": subject["id"], "field": "npi"},
        ),
    ]
    assert [item.status_code for item in writes] == [403] * len(writes)
    removed = client.post(
        f"/credentialing/subjects/{subject['id']}/archive",
        headers=_alice(),
    )
    assert removed.status_code == 200, removed.text
    remaining = client.get("/credentialing/grid", headers=_alice()).json()
    visible = [row for row in remaining["subjects"] if not row.get("archived")]
    assert all(row["name"] != "QA Test Clinician" for row in visible)
    assert any(row["name"] == "QA Test Clinician" and row["archived"] for row in remaining["subjects"])
    restored = client.post(f"/credentialing/subjects/{subject['id']}/restore", headers=_alice())
    assert restored.status_code == 200, restored.text
    back = client.get("/credentialing/grid", headers=_alice()).json()
    assert any(row["name"] == "QA Test Clinician" and not row["archived"] for row in back["subjects"])
    assert client.get("/credentialing/export/people", headers=_auth(viewer)).status_code == 403

    manager = _assign(
        ["tool:credentialing", "capability:credentialing_manage", "tool:time_entry"]
    )
    denied_payer = client.post(
        "/credentialing/payers",
        headers=_auth(manager),
        json={"name": "QA Other", "type": "commercial"},
    )
    assert denied_payer.status_code == 403
    revealed = client.post(
        "/credentialing/reveal",
        headers=_auth(manager),
        json={"subject_id": subject["id"], "field": "npi"},
    )
    assert revealed.status_code == 403
    copied = client.post(
        "/credentialing/copy",
        headers=_auth(manager),
        json={"subject_id": subject["id"], "field": "npi"},
    )
    assert copied.status_code == 403
    exported = client.get("/credentialing/export/people", headers=_auth(manager))
    assert exported.status_code == 200
    assert NPI not in exported.text


def test_page_payload_masks_ids_and_import_formats(client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ATTUNE_CREDENTIALING_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_CREDENTIALING_DATA_PATH", str(tmp_path / "credentialing.enc"))
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())
    credentialing_store.reset()
    preview = client.post("/credentialing/import/preview", headers=_alice(), files=_files())
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert not body["errors"]
    assert any("9 digits" in item for item in body["warnings"])
    preview_text = "\n".join(body["changes"])
    assert "Payer QA Test Payer: create." in preview_text
    assert "Subject QA Test Clinician: create." in preview_text
    assert "QA Test Payer / QA Test Clinician: pending, submitted 2026-04-01." in preview_text
    assert "QA Test Payer / QA Test Practice: in network." in preview_text
    assert "QA Masters Payer / QA Test Practice: needs review." in preview_text
    assert "QA Strict Payer / QA Test Clinician: unknown." in preview_text
    assert NPI not in preview.text
    committed = client.post("/credentialing/import/commit", headers=_alice(), files=_files())
    assert committed.status_code == 200, committed.text
    assert committed.json()["backup"] == ""
    grid_response = client.get("/credentialing/grid", headers=_alice())
    assert NPI not in grid_response.text
    assert "6789" in grid_response.text
    assert "birthday" not in grid_response.text.lower()
    grid = grid_response.json()
    clinician = next(row for row in grid["subjects"] if row["name"] == "QA Test Clinician")
    practice = next(row for row in grid["subjects"] if row["name"] == "QA Test Practice")
    strict = next(row for row in grid["payers"] if row["name"] == "QA Strict Payer")
    masters = next(row for row in grid["payers"] if row["name"] == "QA Masters Payer")
    payer = next(row for row in grid["payers"] if row["name"] == "QA Test Payer")
    cells = {(row["payer_id"], row["subject_id"]): row for row in grid["cells"]}
    assert cells[(payer["id"], practice["id"])]["status"] == "in_network"
    pending = cells[(payer["id"], clinician["id"])]
    assert pending["status"] == "pending"
    assert pending["submitted_on"] == "2026-04-01"
    assert pending["display"] == "warning"
    assert "LSW" in pending["license_warning"]
    assert cells[(masters["id"], practice["id"])]["status"] == "needs_review"
    assert cells[(masters["id"], clinician["id"])]["suggestion_reason"] == ""
    suggested = cells[(strict["id"], clinician["id"])]
    assert suggested["status"] == "unknown"
    assert suggested["display"] == "suggested"
    assert "LSW" in suggested["suggestion_reason"]
    assert cells[(masters["id"], clinician["id"])]["display"] == "plain"
    blocked = client.put(
        "/credentialing/enrollments",
        headers=_alice(),
        json={
            "subject_id": clinician["id"],
            "payer_id": strict["id"],
            "status": "in_network",
            "note": "",
        },
    )
    assert blocked.status_code == 422
    saved = client.put(
        "/credentialing/enrollments",
        headers=_alice(),
        json={
            "subject_id": clinician["id"],
            "payer_id": strict["id"],
            "status": "in_network",
            "note": "QA override",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "in_network"

    revealed = client.post(
        "/credentialing/reveal",
        headers=_alice(),
        json={"subject_id": clinician["id"], "field": "npi"},
    )
    assert revealed.status_code == 200
    assert revealed.json()["value"] == NPI
    copied = client.post(
        "/credentialing/copy",
        headers=_alice(),
        json={"subject_id": clinician["id"], "field": "npi"},
    )
    assert copied.json()["value"] == NPI
    audit = client.get(
        "/credentialing/audit",
        headers=_alice(),
        params={"subject_id": clinician["id"]},
    ).json()["events"]
    assert [item["action"] for item in audit] == ["reveal", "copy"]
    assert all(item["field"] == "npi" for item in audit)
    assert NPI not in json.dumps(audit)

    reloaded = CredentialingStore()
    again = reloaded.public_grid("practice-a")
    assert any(row["name"] == "QA Test Clinician" for row in again["subjects"])
    stored = json.loads(decrypt_utf8((tmp_path / "credentialing.enc").read_bytes()))
    blob = json.dumps(stored)
    assert NPI in blob
    assert "birthday" not in blob.lower()
    assert not (tmp_path / "credentialing.enc.tmp").exists()

    updated_grid = GRID.replace("QA Test Payer,Y,2026-04-01", "QA Test Payer,Y,X")
    second = client.post(
        "/credentialing/import/commit",
        headers=_alice(),
        files=_files(grid=updated_grid),
    )
    assert second.status_code == 200, second.text
    assert second.json()["backup"].startswith("credentialing.enc.bak-")
    refreshed = client.get("/credentialing/grid", headers=_alice()).json()
    cell = next(
        row
        for row in refreshed["cells"]
        if row["subject_id"] == clinician["id"] and row["payer_id"] == payer["id"]
    )
    assert cell["status"] == "not_eligible"
    assert any(event["source"] == "import" for event in cell["history"])
    exported = client.get("/credentialing/export/grid", headers=_alice()).text
    assert exported.splitlines()[0] == "payer,QA Test Practice,QA Test Clinician"
    assert "Y,X" in exported
    people = client.get("/credentialing/export/people", headers=_alice()).text
    assert people.splitlines()[0] == (
        "subject,kind,license_type,npi,tin,license_number,caqh_id,hire_date,device"
    )
    assert NPI in people
    assert "2020-01-15" in people
    assert "QA-IPAD" in people
    export_audit = client.get(
        "/credentialing/audit",
        headers=_alice(),
        params={"subject_id": clinician["id"]},
    ).json()["events"]
    assert any(item["action"] == "export" and item["field"] == "people.csv" for item in export_audit)
    assert NPI not in json.dumps(export_audit)


def test_withheld_export_keeps_hire_date_and_sensitive_round_trip(client: TestClient) -> None:
    people = """subject,kind,license_type,npi,tin,license_number,caqh_id,hire_date,device
QA Test Clinician,clinician,LSW,1234567890,111222333,LIC-99,CAQH-7,2024-02-03,QA-TABLET
"""
    payers = """payer,family_members,type,accepts_licenses,notes
QA Test Payer,,commercial,ALL,fake
"""
    grid = """payer,QA Test Clinician
QA Test Payer,Y
"""
    first = client.post(
        "/credentialing/import/commit",
        headers=_alice(),
        files=_files(payers=payers, people=people, grid=grid),
    )
    assert first.status_code == 200, first.text
    again = client.post(
        "/credentialing/import/preview",
        headers=_alice(),
        files=_files(payers=payers, people=people, grid=grid),
    )
    assert "Payer QA Test Payer: update." in again.json()["changes"]
    assert "Subject QA Test Clinician: update." in again.json()["changes"]
    sensitive = client.get("/credentialing/export/people", headers=_alice()).text
    assert sensitive.splitlines()[0].startswith("subject,")
    assert "1234567890" in sensitive
    assert "111222333" in sensitive
    assert "LIC-99" in sensitive
    assert "CAQH-7" in sensitive
    assert "2024-02-03" in sensitive
    assert "QA-TABLET" in sensitive
    reimported = client.post(
        "/credentialing/import/commit",
        headers=_alice(),
        files=_files(
            payers=payers,
            people=sensitive,
            grid=client.get("/credentialing/export/grid", headers=_alice()).text,
        ),
    )
    assert reimported.status_code == 200, reimported.text
    grid_body = client.get("/credentialing/grid", headers=_alice()).json()
    person = next(row for row in grid_body["subjects"] if row["name"] == "QA Test Clinician")
    assert person["hire_date"] == "2024-02-03"
    assert person["device"] == "QA-TABLET"
    revealed = client.post(
        "/credentialing/reveal",
        headers=_alice(),
        json={"subject_id": person["id"], "field": "tin"},
    )
    assert revealed.json()["value"] == "111222333"

    limited = _assign(["tool:credentialing", "capability:credentialing_manage", "tool:time_entry"])
    withheld = client.get("/credentialing/export/people", headers=_auth(limited)).text
    assert withheld.startswith("# npi, tin, license_number, and caqh_id were withheld")
    assert "2024-02-03" in withheld
    assert "QA-TABLET" in withheld
    assert "1234567890" not in withheld
    assert "111222333" not in withheld
    assert "LIC-99" not in withheld
    assert "CAQH-7" not in withheld
    log = client.get(
        "/credentialing/audit", headers=_alice(), params={"subject_id": person["id"]}
    ).json()["events"]
    exports = [item for item in log if item["action"] == "export"]
    assert len(exports) == 1


def test_payer_archive_restore_and_screen_copy(client: TestClient) -> None:
    created = client.post(
        "/credentialing/payers",
        headers=_alice(),
        json={"name": "QA Test Payer", "type": "eap", "accepts_licenses": ["masters"]},
    )
    assert created.status_code == 200, created.text
    payer_id = created.json()["id"]
    archived = client.post(f"/credentialing/payers/{payer_id}/archive", headers=_alice())
    assert archived.status_code == 200
    hidden = client.get("/credentialing/grid", headers=_alice()).json()
    assert all(row["payer_id"] != payer_id for row in hidden["cells"])
    assert any(row["id"] == payer_id and row["archived"] for row in hidden["payers"])
    restored = client.post(f"/credentialing/payers/{payer_id}/restore", headers=_alice())
    assert restored.status_code == 200
    assert restored.json()["archived"] is False
    page = Path("app/static/index.html").read_text(encoding="utf-8")
    script = Path("app/static/credentialing.js").read_text(encoding="utf-8")
    assert "role.locked ? true" in page
    assert 'toggle.textContent = "Permissions ("' in page
    assert "Add payer" in script
    assert "Archive " in script and "It will be hidden from the grid. History is kept." in script
    assert 'summary.textContent = "Archived ("' in script
    assert 'summary.textContent = "Removed ("' in script
    assert "Restore" in script
    assert 'eye.textContent = "Hide"' in script
    assert "cg-suggested" in script and "cg-warn" in script
    assert 'tab.classList.toggle("primary", selected)' in script
    assert "refreshAudit" in script
    assert "window.confirm" not in script
