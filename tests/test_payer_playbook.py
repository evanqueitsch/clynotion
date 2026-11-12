"""Payer playbook fields and additive payers-only import. Fixtures are fake."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.credentialing.csvio import parse_payers
from app.credentialing.store import credentialing_store
from app.main import app
from tests.test_time_entry import _alice

HEADER = (
    "payer,family_members,type,accepts_licenses,notes,"
    "electronic_payer_id,practice_network,practice_network_note"
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _payer_file(text: str) -> dict:
    return {"payers": ("payers.csv", text, "text/csv")}


def _commit(client: TestClient, text: str):
    return client.post("/credentialing/import/commit", headers=_alice(), files=_payer_file(text))


def _preview(client: TestClient, text: str):
    return client.post("/credentialing/import/preview", headers=_alice(), files=_payer_file(text))


def _payer(name: str = "Fake Commercial Payer") -> dict:
    grid = client_grid()
    return next(row for row in grid["payers"] if row["name"] == name)


def client_grid() -> dict:
    client = TestClient(app)
    response = client.get("/credentialing/grid", headers=_alice())
    assert response.status_code == 200, response.text
    return response.json()


def test_public_payer_defaults_and_save_round_trip(client: TestClient) -> None:
    created = client.post(
        "/credentialing/payers",
        headers=_alice(),
        json={"name": "Fake Commercial Payer", "type": "commercial", "accepts_licenses": ["LCSW"]},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["electronic_payer_id"] == ""
    assert body["practice_network"] == "unknown"
    assert body["practice_network_note"] == ""
    assert body["credentials_expire"] is None
    assert body["follow_up_days"] == 14
    saved = client.put(
        f"/credentialing/payers/{body['id']}",
        headers=_alice(),
        json={
            "name": "Fake Commercial Payer",
            "type": "commercial",
            "accepts_licenses": ["LCSW"],
            "notes": "Join at https://example.com/join",
            "electronic_payer_id": "??61101??",
            "practice_network": "needs_ma_first",
            "practice_network_note": "SENT IN 8/7/26",
            "credentials_expire": False,
            "parent_payer": "Fake Parent",
            "counties": ["Example County"],
        },
    )
    assert saved.status_code == 200, saved.text
    again = saved.json()
    assert again["electronic_payer_id"] == "??61101??"
    assert again["practice_network"] == "needs_ma_first"
    assert again["practice_network_note"] == "SENT IN 8/7/26"
    assert again["notes"] == "Join at https://example.com/join"
    assert again["credentials_expire"] is False
    assert again["parent_payer"] == "Fake Parent"
    assert again["counties"] == ["Example County"]
    grid = client.get("/credentialing/grid", headers=_alice()).json()
    shown = next(row for row in grid["payers"] if row["id"] == body["id"])
    assert shown["electronic_payer_id"] == "??61101??"
    assert shown["practice_network"] == "needs_ma_first"
    assert shown["practice_network_note"] == "SENT IN 8/7/26"
    assert shown["notes"] == "Join at https://example.com/join"
    assert shown["accepts_licenses"] == ["LCSW"]
    partial = credentialing_store.save_payer(
        "practice-a",
        {"id": body["id"], "name": "Fake Commercial Payer", "type": "commercial"},
        actor="qa",
    )
    assert partial["electronic_payer_id"] == "??61101??"
    assert partial["practice_network"] == "needs_ma_first"
    assert partial["practice_network_note"] == "SENT IN 8/7/26"
    rejected = client.put(
        f"/credentialing/payers/{body['id']}",
        headers=_alice(),
        json={
            "name": "Fake Commercial Payer",
            "type": "commercial",
            "practice_network": "not_in_network",
        },
    )
    assert rejected.status_code == 422


def test_old_and_extended_headers_parse() -> None:
    old = """payer,family_members,type,accepts_licenses,notes
Fake Commercial Payer,QA Family,commercial,LCSW,Join at https://example.com/join
"""
    rows, errors, warnings, changes = parse_payers(old)
    assert errors == []
    assert warnings == []
    assert rows[0]["family_members"] == ["QA Family"]
    assert rows[0]["accepts_licenses"] == ["LCSW"]
    assert rows[0]["electronic_payer_id"] == ""
    assert rows[0]["practice_network"] == ""
    assert changes == ["Payer Fake Commercial Payer: create."]
    extended = f"""{HEADER}
Fake Commercial Payer,,commercial,"LSW. LCSW",Join at https://example.com/join,??61101??,Y,
"""
    rows, errors, warnings, _changes = parse_payers(extended)
    assert errors == []
    assert warnings == []
    assert rows[0]["accepts_licenses"] == ["LSW", "LCSW"]
    assert rows[0]["electronic_payer_id"] == "??61101??"
    assert rows[0]["practice_network"] == "in_network"


def test_sheet_aliases_continuation_and_network_words() -> None:
    sheet = """Insurance Co,Licenses,How-to,Electronic payer ID,In network
Fake Sheet Payer,"LSW, LCSW",Join at https://example.com/join,PABLS,Need MA 1st
,,Second line of how-to,,
,,,,"emailed 8/14 faxed 8/26"
Fake Started Payer,masters,Portal steps,on provider portal,STARTED
Fake Sent Payer,LCSW,Wait here,22248 and 77062,SENT IN 8/7/26
Fake Unknown Payer,LCSW,Plain how-to,60054,
Fake Odd Payer,LCSW,Odd how-to,99999,call the portal
"""
    rows, errors, warnings, changes = parse_payers(sheet)
    assert errors == []
    by_name = {row["name"]: row for row in rows}
    assert list(by_name) == [
        "Fake Sheet Payer",
        "Fake Started Payer",
        "Fake Sent Payer",
        "Fake Unknown Payer",
        "Fake Odd Payer",
    ]
    sheet_payer = by_name["Fake Sheet Payer"]
    assert sheet_payer["type"] == "commercial"
    assert sheet_payer["accepts_licenses"] == ["LSW", "LCSW"]
    assert sheet_payer["electronic_payer_id"] == "PABLS"
    assert sheet_payer["practice_network"] == "needs_ma_first"
    assert sheet_payer["notes"] == (
        "Join at https://example.com/join\n\n"
        "Second line of how-to\n\n"
        "emailed 8/14 faxed 8/26"
    )
    assert sheet_payer["practice_network_note"] == ""
    assert by_name["Fake Started Payer"]["practice_network"] == "in_progress"
    assert by_name["Fake Started Payer"]["practice_network_note"] == "STARTED"
    assert by_name["Fake Started Payer"]["electronic_payer_id"] == "on provider portal"
    assert by_name["Fake Sent Payer"]["practice_network"] == "in_progress"
    assert by_name["Fake Sent Payer"]["practice_network_note"] == "SENT IN 8/7/26"
    assert by_name["Fake Sent Payer"]["electronic_payer_id"] == "22248 and 77062"
    assert by_name["Fake Unknown Payer"]["practice_network"] == ""
    assert by_name["Fake Unknown Payer"]["electronic_payer_id"] == "60054"
    assert by_name["Fake Odd Payer"]["practice_network"] == "in_progress"
    assert by_name["Fake Odd Payer"]["practice_network_note"] == "call the portal"
    assert any("Unexpected in-network value" in item for item in warnings)
    assert all("??" not in item for item in warnings)
    assert changes[0] == "Payer Fake Sheet Payer: create."
    assert not any(line.startswith("Subject ") for line in changes)


def test_type_inference_does_not_merge_similar_names() -> None:
    text = f"""{HEADER}
MA,,,,,,,
Fake Medicaid Medicare,,,,,,,
Fake EAP Plan,,,,,,,
Fake CHIP Plan,,,,,,,
Fake Tricare East,,,,,,,
Fake Highmark,,,,,11111,,
Fake Highmark Blue,,,,,22222,,
"""
    rows, errors, warnings, _changes = parse_payers(text)
    assert errors == []
    by_name = {row["name"]: row for row in rows}
    assert by_name["MA"]["type"] == "medicaid_mco"
    assert by_name["Fake Medicaid Medicare"]["type"] == "commercial"
    assert by_name["Fake EAP Plan"]["type"] == "eap"
    assert by_name["Fake CHIP Plan"]["type"] == "chip"
    assert by_name["Fake Tricare East"]["type"] == "tricare"
    assert by_name["Fake Highmark"]["electronic_payer_id"] == "11111"
    assert by_name["Fake Highmark Blue"]["electronic_payer_id"] == "22222"
    assert any("Medicaid and Medicare" in item for item in warnings)


def test_additive_import_fills_blanks_and_does_not_wipe(client: TestClient) -> None:
    payer = credentialing_store.save_payer(
        "practice-a",
        {
            "name": "Fake Commercial Payer",
            "type": "commercial",
            "accepts_licenses": ["LCSW", "LPC"],
            "notes": "Call the example desk.",
            "electronic_payer_id": "99999",
            "practice_network": "in_network",
            "practice_network_note": "STARTED",
            "parent_payer": "Fake Parent",
            "counties": ["Example County"],
            "credentials_expire": False,
        },
        actor="qa",
    )
    _payload, row = credentialing_store._load_practice("practice-a")
    row["payers"][payer["id"]]["follow_up_days"] = 21
    credentialing_store._save(_payload)
    blank = f"""{HEADER}
Fake Commercial Payer,,commercial,,,,
"""
    preview = _preview(client, blank)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["errors"] == []
    assert body["summary"] == "1 payer to update, 0 cells, 0 warnings"
    assert body["changes"] == ["Payer Fake Commercial Payer: update."]
    committed = _commit(client, blank)
    assert committed.status_code == 200, committed.text
    kept = _payer()
    assert kept["notes"] == "Call the example desk."
    assert kept["accepts_licenses"] == ["LCSW", "LPC"]
    assert kept["electronic_payer_id"] == "99999"
    assert kept["practice_network"] == "in_network"
    assert kept["practice_network_note"] == "STARTED"
    assert kept["parent_payer"] == "Fake Parent"
    assert kept["counties"] == ["Example County"]
    assert kept["credentials_expire"] is False
    assert kept["follow_up_days"] == 21
    assert kept["type"] == "commercial"
    extra = f"""{HEADER}
  fake   commercial payer ,,eap,lCsw. masters,Join at https://example.com/join,PABLS,,
"""
    warned = _preview(client, extra)
    assert warned.status_code == 200, warned.text
    assert any("left unchanged" in item for item in warned.json()["warnings"])
    assert "1 payer to update" in warned.json()["summary"]
    assert _commit(client, extra).status_code == 200
    updated = _payer()
    assert updated["name"] == "Fake Commercial Payer"
    assert updated["type"] == "commercial"
    assert updated["accepts_licenses"] == ["LCSW", "LPC", "masters"]
    assert updated["electronic_payer_id"] == "PABLS"
    assert updated["notes"] == "Call the example desk.\n\nJoin at https://example.com/join"
    assert updated["practice_network"] == "in_network"
    assert updated["practice_network_note"] == "STARTED"
    assert updated["counties"] == ["Example County"]
    assert updated["follow_up_days"] == 21
    again = _commit(client, extra)
    assert again.status_code == 200, again.text
    repeated = _payer()
    assert repeated["notes"].count("https://example.com/join") == 1
    assert repeated["accepts_licenses"] == ["LCSW", "LPC", "masters"]


def test_payers_only_import_does_not_touch_people_or_cells(client: TestClient) -> None:
    subject = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Drawer Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    payer = credentialing_store.save_payer(
        "practice-a",
        {"name": "Prefered One", "type": "commercial", "accepts_licenses": ["LCSW"]},
        actor="qa",
    )
    credentialing_store.save_enrollment(
        "practice-a",
        {
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "pending",
            "submitted_on": "2026-04-01",
            "has_expiration": True,
            "expires_on": "2027-01-15",
            "note": "QA enrollment",
        },
        actor="qa",
    )
    _payload, before = credentialing_store._load_practice("practice-a")
    subject_before = json.dumps(before["subjects"])
    enrollment_before = json.dumps(before["enrollments"])
    text = f"""{HEADER}
Prefered One,,commercial,LCSW,Join at https://example.com/join,99999,Y,
Preferred One,,commercial,LPC,Different how-to,88888,need MA 1st,
"""
    preview = _preview(client, text)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["errors"] == []
    assert "Subject " not in "\n".join(body["changes"])
    assert " / " not in "\n".join(body["changes"])
    assert "people" not in body["summary"]
    assert "1 payer to update" in body["summary"]
    assert "1 payer to create" in body["summary"]
    assert body["summary"].endswith("0 cells, 0 warnings")
    committed = _commit(client, text)
    assert committed.status_code == 200, committed.text
    _payload, after = credentialing_store._load_practice("practice-a")
    assert json.dumps(after["subjects"]) == subject_before
    assert json.dumps(after["enrollments"]) == enrollment_before
    prefered = next(row for row in after["payers"].values() if row["name"] == "Prefered One")
    preferred = next(row for row in after["payers"].values() if row["name"] == "Preferred One")
    assert prefered["id"] == payer["id"]
    assert prefered["electronic_payer_id"] == "99999"
    assert prefered["practice_network"] == "in_network"
    assert "https://example.com/join" in prefered["notes"]
    assert preferred["electronic_payer_id"] == "88888"
    assert preferred["practice_network"] == "needs_ma_first"
    assert "npi" not in prefered
    assert "tin" not in prefered
    cell = client.get("/credentialing/grid", headers=_alice()).json()
    shown = next(row for row in cell["cells"] if row["payer_id"] == payer["id"])
    assert shown["status"] == "pending"
    assert shown["submitted_on"] == "2026-04-01"
    assert shown["expires_on"] == "2027-01-15"
    assert shown["has_expiration"] is True
    playbook = next(row for row in cell["payers"] if row["id"] == payer["id"])
    assert playbook["electronic_payer_id"] == "99999"
    assert playbook["practice_network"] == "in_network"
    assert playbook["accepts_licenses"] == ["LCSW"]
    assert "https://example.com/join" in playbook["notes"]


def test_payers_only_ignores_secret_columns_and_uncertain_ids(client: TestClient) -> None:
    text = f"""{HEADER},tin
Fake Commercial Payer,,commercial,LCSW,Join at https://example.com/join,??61101??,Y,,000000000
Fake Portal Payer,,commercial,masters,Portal how-to,on provider portal,need ma first,
"""
    preview = _preview(client, text)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["errors"] == []
    assert any("tin" in item and "ignored" in item for item in body["warnings"])
    assert not any("??61101??" in item for item in body["warnings"])
    assert body["summary"] == "2 payers to create, 0 cells, 1 warning"
    assert _commit(client, text).status_code == 200
    _payload, row = credentialing_store._load_practice("practice-a")
    assert row["subjects"] == {}
    assert row["enrollments"] == {}
    stored = json.dumps(row["payers"])
    assert "000000000" not in stored
    assert "not_in_network" not in stored
    uncertain = next(item for item in row["payers"].values() if item["name"] == "Fake Commercial Payer")
    assert uncertain["electronic_payer_id"] == "??61101??"
    assert "tin" not in uncertain
    assert "npi" not in uncertain
    portal = next(item for item in row["payers"].values() if item["name"] == "Fake Portal Payer")
    assert portal["electronic_payer_id"] == "on provider portal"
    assert portal["practice_network"] == "needs_ma_first"


def test_export_round_trip_keeps_playbook_and_does_not_duplicate_notes(client: TestClient) -> None:
    credentialing_store.save_payer(
        "practice-a",
        {
            "name": "Fake Commercial Payer",
            "type": "commercial",
            "accepts_licenses": ["LCSW", "masters"],
            "notes": "Join at https://example.com/join, then wait.\n\nSecond line.",
            "electronic_payer_id": "??61101??",
            "practice_network": "in_progress",
            "practice_network_note": "SENT IN 8/7/26",
        },
        actor="qa",
    )
    exported = client.get("/credentialing/export/payers", headers=_alice()).text
    assert exported.splitlines()[0] == HEADER
    assert "??61101??" in exported
    imported = _commit(client, exported)
    assert imported.status_code == 200, imported.text
    payer = _payer()
    assert payer["electronic_payer_id"] == "??61101??"
    assert payer["practice_network"] == "in_progress"
    assert payer["practice_network_note"] == "SENT IN 8/7/26"
    assert payer["notes"].count("https://example.com/join") == 1
    assert "Second line." in payer["notes"]
    assert payer["accepts_licenses"] == ["LCSW", "masters"]


def test_drawer_script_reads_playbook_fields() -> None:
    script = Path("app/static/credentialing.js").read_text(encoding="utf-8")
    page = Path("app/static/index.html").read_text(encoding="utf-8")
    assert 'src="/static/credentialing.js?v=0.19.8"' in page
    assert 'panel.id = "cg-playbook"' in script
    assert "Electronic payer ID" in script
    assert "Practice network" in script
    assert "Need MA first" in script
    assert "How-to notes" in script
    assert 'edit.textContent = "Edit payer"' in script
    assert 'network !== "unknown"' in script
    assert "function linkify" in script
    assert 'anchor.target = "_blank"' in script
    assert "cg-payer-electronic-id" in script
    assert "cg-payer-network" in script
