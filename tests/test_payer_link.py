"""Link duplicate payers. Fixtures are fake payers and fake subjects only."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.credentialing.store import credentialing_store
from app.main import app
from tests.test_credentialing import _assign, _auth
from tests.test_time_entry import _alice


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _grid(client: TestClient) -> dict:
    response = client.get("/credentialing/grid", headers=_alice())
    assert response.status_code == 200, response.text
    return response.json()


def _payer(client: TestClient, **body) -> dict:
    payload = {"name": "Fake Payer", "type": "commercial"}
    payload.update(body)
    response = client.post("/credentialing/payers", headers=_alice(), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _subject(client: TestClient, name: str, kind: str = "clinician", license_type: str = "LCSW") -> dict:
    response = client.put(
        "/credentialing/subjects",
        headers=_alice(),
        json={"name": name, "kind": kind, "license_type": license_type},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _enroll(client: TestClient, subject_id: str, payer_id: str, **body) -> dict:
    payload = {"subject_id": subject_id, "payer_id": payer_id, "status": "unknown"}
    payload.update(body)
    response = client.put("/credentialing/enrollments", headers=_alice(), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _stored_payer(payer_id: str, **fields) -> None:
    payload, row = credentialing_store._load_practice("practice-a")
    row["payers"][payer_id].update(fields)
    credentialing_store._save(payload)


def _preview(client: TestClient, body: dict, headers: dict | None = None):
    return client.post("/credentialing/payers/link/preview", headers=headers or _alice(), json=body)


def _commit(client: TestClient, body: dict, headers: dict | None = None):
    return client.post("/credentialing/payers/link", headers=headers or _alice(), json=body)


def _active(grid: dict) -> list[dict]:
    return [row for row in grid["payers"] if not row.get("archived")]


def test_no_conflict_link_moves_enrollment_and_archives_sources(client: TestClient) -> None:
    kept = _payer(
        client,
        name="Fake North Plan",
        type="commercial",
        notes="Same how-to",
        accepts_licenses=["LCSW", "LPC"],
    )
    other = _payer(
        client,
        name="Fake North Alias",
        type="commercial",
        notes="Same how-to",
        accepts_licenses=["lpc", "lcsw"],
        electronic_payer_id="FAKE-NORTH-1",
        practice_network="in_network",
        practice_network_note="Fake network note",
        counties=["Example County"],
        family_members=["Fake Family"],
    )
    clinician = _subject(client, "QA Sample Clinician")
    _enroll(
        client,
        clinician["id"],
        other["id"],
        status="in_network",
        effective_on="2026-04-01",
        has_expiration=False,
        note="Moved with the alias",
    )
    body = {"payer_ids": [kept["id"], other["id"]]}
    before = _grid(client)
    preview = _preview(client, body)
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["ready"] is False
    assert "Name to keep" in shown["unresolved"]
    assert shown["enrollment_conflicts"] == []
    assert _grid(client)["payers"] == before["payers"]
    body["keep_id"] = kept["id"]
    preview = _preview(client, body)
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["ready"] is True
    assert shown["keep_name"] == "Fake North Plan"
    assert shown["aliases"] == ["Fake North Alias"]
    assert shown["errors"] == []
    assert any(move["subject_name"] == "QA Sample Clinician" for move in shown["enrollment_moves"])
    assert _grid(client)["payers"] == before["payers"]

    linked = _commit(client, body)
    assert linked.status_code == 200, linked.text
    payer = linked.json()["payer"]
    assert payer["name"] == "Fake North Plan"
    assert payer["aliases"] == ["Fake North Alias"]
    assert payer["electronic_payer_id"] == "FAKE-NORTH-1"
    assert payer["practice_network"] == "in_network"
    assert payer["practice_network_note"] == "Fake network note"
    assert payer["counties"] == ["Example County"]
    assert payer["family_members"] == ["Fake Family"]
    assert payer["accepts_licenses"] == ["LCSW", "LPC"]
    assert payer["notes"] == "Same how-to"
    assert payer["archived"] is False

    grid = _grid(client)
    active = _active(grid)
    assert [row["name"] for row in active] == ["Fake North Plan"]
    archived = next(row for row in grid["payers"] if row["id"] == other["id"])
    assert archived["archived"] is True
    assert archived["merged_into"] == kept["id"]
    assert archived["name"] == "Fake North Alias"
    cell = next(row for row in grid["cells"] if row["subject_id"] == clinician["id"])
    assert cell["payer_id"] == kept["id"]
    assert cell["status"] == "in_network"
    assert cell["effective_on"] == "2026-04-01"
    assert cell["note"] == "Moved with the alias"
    assert all(row["payer_id"] != other["id"] for row in grid["cells"])
    _payload, row = credentialing_store._load_practice("practice-a")
    assert other["id"] in row["payers"]
    assert f"{clinician['id']}|{other['id']}" not in row["enrollments"]
    assert f"{clinician['id']}|{kept['id']}" in row["enrollments"]
    blocked = client.post(f"/credentialing/payers/{other['id']}/restore", headers=_alice())
    assert blocked.status_code == 422


def test_conflict_choices_are_honored(client: TestClient) -> None:
    kept = _payer(
        client,
        name="Fake Harbor Plan",
        type="commercial",
        electronic_payer_id="HARBOR-1",
        notes="Harbor how-to",
        accepts_licenses=["LCSW"],
        counties=["North County"],
        family_members=["Harbor Family"],
        parent_payer="Fake Harbor Parent",
        practice_network="in_network",
        practice_network_note="Harbor note",
        credentials_expire=True,
    )
    other = _payer(
        client,
        name="Fake Harbor Alias",
        type="eap",
        electronic_payer_id="HARBOR-2",
        notes="Alias how-to",
        accepts_licenses=["LPC"],
        counties=["South County"],
        family_members=["Alias Family"],
        parent_payer="Fake Alias Parent",
        practice_network="in_progress",
        practice_network_note="Alias note",
        credentials_expire=False,
    )
    _stored_payer(kept["id"], follow_up_days=14, recredential_days=1095)
    _stored_payer(other["id"], follow_up_days=30, recredential_days=730)
    body = {"payer_ids": [other["id"], kept["id"], other["id"]]}
    duplicate = _preview(client, body)
    assert duplicate.status_code == 200
    assert duplicate.json()["ready"] is False
    assert any("once" in item for item in duplicate.json()["errors"])

    body = {"payer_ids": [kept["id"], other["id"]], "keep_id": kept["id"]}
    preview = _preview(client, body)
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["ready"] is False
    conflict_names = {field["field"] for field in shown["fields"] if field["status"] == "conflict"}
    assert {
        "type",
        "electronic_payer_id",
        "notes",
        "accepts_licenses",
        "family_members",
        "parent_payer",
        "counties",
        "credentials_expire",
        "practice_network",
        "practice_network_note",
        "follow_up_days",
        "recredential_days",
    } <= conflict_names
    refused = _commit(client, body)
    assert refused.status_code == 422
    assert len(_active(_grid(client))) == 2

    body["choices"] = {
        "type": {"source_id": kept["id"]},
        "electronic_payer_id": {"source_id": other["id"]},
        "notes": {"combine": True},
        "accepts_licenses": {"combine": True},
        "family_members": {"combine": True},
        "parent_payer": {"source_id": kept["id"]},
        "counties": {"source_id": kept["id"]},
        "credentials_expire": {"source_id": kept["id"]},
        "practice_network": {"source_id": kept["id"]},
        "practice_network_note": {"blank": True},
        "follow_up_days": {"source_id": other["id"]},
        "recredential_days": {"source_id": kept["id"]},
    }
    ready = _preview(client, body)
    assert ready.json()["ready"] is True, ready.text
    linked = _commit(client, body)
    assert linked.status_code == 200, linked.text
    payer = linked.json()["payer"]
    assert payer["name"] == "Fake Harbor Plan"
    assert payer["aliases"] == ["Fake Harbor Alias"]
    assert payer["type"] == "commercial"
    assert payer["electronic_payer_id"] == "HARBOR-2"
    assert payer["notes"] == "Harbor how-to\n\nAlias how-to"
    assert payer["accepts_licenses"] == ["LCSW", "LPC"]
    assert payer["family_members"] == ["Harbor Family", "Alias Family"]
    assert payer["parent_payer"] == "Fake Harbor Parent"
    assert payer["counties"] == ["North County"]
    assert payer["credentials_expire"] is True
    assert payer["practice_network"] == "in_network"
    assert payer["practice_network_note"] == ""
    assert payer["follow_up_days"] == 30
    assert payer["recredential_days"] == 1095


def test_enrollment_conflict_requires_a_choice(client: TestClient) -> None:
    kept = _payer(client, name="Fake Harbor Plan", type="commercial", accepts_licenses=["ALL"])
    other = _payer(client, name="Fake Harbor Alias", type="commercial", accepts_licenses=["ALL"])
    clinician = _subject(client, "QA Sample Clinician")
    _enroll(
        client,
        clinician["id"],
        kept["id"],
        status="pending",
        submitted_on="2026-01-15",
        has_expiration=False,
        note="Waiting on the fake portal",
        payer_provider_id="FAKEPROV1001",
    )
    _enroll(
        client,
        clinician["id"],
        other["id"],
        status="in_network",
        submitted_on="2026-02-02",
        effective_on="2026-03-01",
        has_expiration=True,
        expires_on="2027-03-01",
        next_follow_up="2026-12-01",
        note="Fake portal confirmed",
        payer_provider_id="FAKEPROV2002",
    )
    body = {"payer_ids": [kept["id"], other["id"]], "keep_id": kept["id"]}
    preview = _preview(client, body)
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["ready"] is False
    assert "FAKEPROV1001" not in preview.text
    assert "FAKEPROV2002" not in preview.text
    conflict = shown["enrollment_conflicts"][0]
    assert conflict["subject_name"] == "QA Sample Clinician"
    fields = {field["field"]: field for field in conflict["fields"]}
    assert fields["status"]["status"] == "conflict"
    assert fields["note"]["status"] == "conflict"
    assert fields["has_expiration"]["status"] == "conflict"
    assert fields["payer_provider_id"]["status"] == "conflict"
    assert fields["submitted_on"]["status"] == "conflict"
    assert "1001" in json.dumps(fields["payer_provider_id"])
    refused = _commit(client, body)
    assert refused.status_code == 422
    still = _grid(client)
    assert len(_active(still)) == 2
    kept_cell = next(
        row for row in still["cells"] if row["subject_id"] == clinician["id"] and row["payer_id"] == kept["id"]
    )
    assert kept_cell["status"] == "pending"

    body["enrollment_choices"] = {
        clinician["id"]: {
            "status": {"source_id": other["id"]},
            "submitted_on": {"source_id": kept["id"]},
            "note": {"source_id": kept["id"]},
            "has_expiration": {"source_id": other["id"]},
            "payer_provider_id": {"source_id": kept["id"]},
        }
    }
    ready = _preview(client, body)
    assert ready.json()["ready"] is True, ready.text
    assert "FAKEPROV1001" not in ready.text
    chosen = {
        field["field"]: field for field in ready.json()["enrollment_conflicts"][0]["fields"]
    }
    assert chosen["status"]["status"] == "chosen"
    assert chosen["status"]["display"] == "In network"
    assert chosen["note"]["display"] == "Waiting on the fake portal"
    linked = _commit(client, body)
    assert linked.status_code == 200, linked.text
    grid = _grid(client)
    cells = [row for row in grid["cells"] if row["subject_id"] == clinician["id"]]
    assert len(cells) == 1
    cell = cells[0]
    assert cell["payer_id"] == kept["id"]
    assert cell["status"] == "in_network"
    assert cell["submitted_on"] == "2026-01-15"
    assert cell["effective_on"] == "2026-03-01"
    assert cell["expires_on"] == "2027-03-01"
    assert cell["next_follow_up"] == "2026-12-01"
    assert cell["has_expiration"] is True
    assert cell["note"] == "Waiting on the fake portal"
    revealed = client.post(
        "/credentialing/reveal",
        headers=_alice(),
        json={"subject_id": clinician["id"], "field": "payer_provider_id", "payer_id": kept["id"]},
    )
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["value"] == "FAKEPROV1001"
    _payload, row = credentialing_store._load_practice("practice-a")
    assert f"{clinician['id']}|{other['id']}" not in row["enrollments"]


def test_aliases_match_on_later_payers_only_import(client: TestClient) -> None:
    kept = _payer(client, name="Fake North Plan", type="commercial", notes="Same how-to")
    other = _payer(client, name="Fake North Alias", type="commercial")
    linked = _commit(client, {"payer_ids": [kept["id"], other["id"]], "keep_id": kept["id"]})
    assert linked.status_code == 200, linked.text
    text = (
        "payer,family_members,type,accepts_licenses,notes,"
        "electronic_payer_id,practice_network,practice_network_note\n"
        "  fake   NORTH    alias ,,,,Added from the alias file.,,,\n"
    )
    preview = client.post(
        "/credentialing/import/preview",
        headers=_alice(),
        files={"payers": ("payers.csv", text, "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["errors"] == []
    assert "1 payer to update" in body["summary"]
    assert "to create" not in body["summary"]
    assert any("update" in line for line in body["changes"])
    committed = client.post(
        "/credentialing/import/commit",
        headers=_alice(),
        files={"payers": ("payers.csv", text, "text/csv")},
    )
    assert committed.status_code == 200, committed.text
    grid = _grid(client)
    active = _active(grid)
    assert [row["name"] for row in active] == ["Fake North Plan"]
    payer = active[0]
    assert payer["id"] == kept["id"]
    assert "Same how-to" in payer["notes"]
    assert "Added from the alias file." in payer["notes"]
    archived = next(row for row in grid["payers"] if row["id"] == other["id"])
    assert archived["archived"] is True
    canonical = (
        "payer,family_members,type,accepts_licenses,notes,"
        "electronic_payer_id,practice_network,practice_network_note\n"
        "FAKE   NORTH   PLAN,,,,Canonical spelling stays.,,,\n"
    )
    again = client.post(
        "/credentialing/import/commit",
        headers=_alice(),
        files={"payers": ("payers.csv", canonical, "text/csv")},
    )
    assert again.status_code == 200, again.text
    renamed = _active(_grid(client))
    assert len(renamed) == 1
    assert renamed[0]["name"] == "Fake North Plan"
    assert "Canonical spelling stays." in renamed[0]["notes"]


def test_blank_does_not_wipe_unless_chosen(client: TestClient) -> None:
    kept = _payer(
        client,
        name="Fake Keep Plan",
        type="commercial",
        notes="Keep this how-to",
        accepts_licenses=["LCSW"],
        electronic_payer_id="KEEP-ID",
    )
    other = _payer(client, name="Fake Empty Alias", type="commercial")
    clinician = _subject(client, "QA Sample Clinician")
    _enroll(
        client,
        clinician["id"],
        kept["id"],
        status="pending",
        has_expiration=False,
        note="Keep enrollment note",
    )
    _enroll(client, clinician["id"], other["id"], status="pending", has_expiration=False)
    body = {"payer_ids": [kept["id"], other["id"]], "keep_id": kept["id"]}
    linked = _commit(client, body)
    assert linked.status_code == 200, linked.text
    payer = linked.json()["payer"]
    assert payer["notes"] == "Keep this how-to"
    assert payer["accepts_licenses"] == ["LCSW"]
    assert payer["electronic_payer_id"] == "KEEP-ID"
    cell = next(row for row in _grid(client)["cells"] if row["subject_id"] == clinician["id"])
    assert cell["note"] == "Keep enrollment note"

    kept = _payer(
        client,
        name="Fake Clear Plan",
        type="commercial",
        notes="Keep this how-to",
        accepts_licenses=["LCSW"],
        electronic_payer_id="KEEP-ID",
    )
    other = _payer(client, name="Fake Clear Alias", type="commercial")
    _enroll(
        client,
        clinician["id"],
        kept["id"],
        status="pending",
        has_expiration=False,
        note="Keep enrollment note",
    )
    _enroll(client, clinician["id"], other["id"], status="pending", has_expiration=False)
    body = {
        "payer_ids": [kept["id"], other["id"]],
        "keep_id": kept["id"],
        "choices": {
            "notes": {"blank": True},
            "accepts_licenses": {"blank": True},
        },
        "enrollment_choices": {clinician["id"]: {"note": {"blank": True}}},
    }
    cleared = _commit(client, body)
    assert cleared.status_code == 200, cleared.text
    payer = cleared.json()["payer"]
    assert payer["notes"] == ""
    assert payer["accepts_licenses"] == []
    assert payer["electronic_payer_id"] == "KEEP-ID"
    cell = next(
        row
        for row in _grid(client)["cells"]
        if row["subject_id"] == clinician["id"] and row["payer_id"] == kept["id"]
    )
    assert cell["note"] == ""
    assert cell["status"] == "pending"


def test_three_payers_link_in_one_pass(client: TestClient) -> None:
    first = _payer(client, name="Fake Cluster One", type="commercial", notes="Cluster how-to")
    second = _payer(client, name="Fake Cluster Two", type="commercial")
    third = _payer(client, name="Fake Cluster Three", type="commercial")
    one = _subject(client, "QA Sample Clinician")
    two = _subject(client, "QA Other Clinician")
    _enroll(client, one["id"], first["id"], status="pending", submitted_on="2026-05-01", has_expiration=False)
    _enroll(client, two["id"], third["id"], status="in_network", has_expiration=False, note="Cluster cell")
    body = {"payer_ids": [first["id"], second["id"], third["id"]], "keep_id": second["id"]}
    preview = _preview(client, body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["ready"] is True
    assert preview.json()["aliases"] == ["Fake Cluster One", "Fake Cluster Three"]
    linked = _commit(client, body)
    assert linked.status_code == 200, linked.text
    payer = linked.json()["payer"]
    assert payer["name"] == "Fake Cluster Two"
    assert payer["notes"] == "Cluster how-to"
    grid = _grid(client)
    assert [row["name"] for row in _active(grid)] == ["Fake Cluster Two"]
    by_subject = {row["subject_id"]: row for row in grid["cells"]}
    assert by_subject[one["id"]]["status"] == "pending"
    assert by_subject[one["id"]]["payer_id"] == second["id"]
    assert by_subject[two["id"]]["status"] == "in_network"
    assert by_subject[two["id"]]["note"] == "Cluster cell"


def test_link_requires_payer_management(client: TestClient) -> None:
    kept = _payer(client, name="Fake North Plan", type="commercial")
    other = _payer(client, name="Fake North Alias", type="commercial")
    body = {"payer_ids": [kept["id"], other["id"]], "keep_id": kept["id"]}
    viewer = _assign(["tool:credentialing", "tool:time_entry"])
    seen = client.get("/credentialing/grid", headers=_auth(viewer))
    assert seen.status_code == 200
    assert seen.json()["can_manage"] is False
    assert seen.json()["can_payers"] is False
    assert any(row["name"] == "Fake North Plan" for row in seen.json()["payers"])
    assert _preview(client, body, _auth(viewer)).status_code == 403
    assert _commit(client, body, _auth(viewer)).status_code == 403
    manager = _assign(["tool:credentialing", "capability:credentialing_manage", "tool:time_entry"])
    assert _preview(client, body, _auth(manager)).status_code == 403
    assert _commit(client, body, _auth(manager)).status_code == 403
    assert len(_active(_grid(client))) == 2


def test_link_audit_records_who_name_aliases_and_choices(client: TestClient) -> None:
    kept = _payer(
        client,
        name="Fake Harbor Plan",
        type="commercial",
        notes="Harbor how-to",
        electronic_payer_id="HARBOR-1",
    )
    other = _payer(
        client,
        name="Fake Harbor Alias",
        type="eap",
        notes="Alias how-to",
        electronic_payer_id="HARBOR-2",
    )
    clinician = _subject(client, "QA Sample Clinician")
    _enroll(
        client,
        clinician["id"],
        kept["id"],
        status="pending",
        has_expiration=False,
        payer_provider_id="FAKEPROV1001",
    )
    _enroll(
        client,
        clinician["id"],
        other["id"],
        status="in_network",
        has_expiration=False,
        payer_provider_id="FAKEPROV2002",
    )
    body = {
        "payer_ids": [kept["id"], other["id"]],
        "keep_id": kept["id"],
        "choices": {
            "type": {"source_id": kept["id"]},
            "notes": {"combine": True},
            "electronic_payer_id": {"source_id": other["id"]},
        },
        "enrollment_choices": {
            clinician["id"]: {
                "status": {"source_id": other["id"]},
                "payer_provider_id": {"source_id": kept["id"]},
            }
        },
    }
    linked = _commit(client, body)
    assert linked.status_code == 200, linked.text
    audit = client.get("/credentialing/audit", headers=_alice())
    assert audit.status_code == 200, audit.text
    event = audit.json()["links"][-1]
    assert event["action"] == "link_payers"
    assert event["actor"] == "alice@example.com"
    assert event["kept_payer_id"] == kept["id"]
    assert event["kept_name"] == "Fake Harbor Plan"
    assert event["source_ids"] == [kept["id"], other["id"]]
    assert event["source_names"] == ["Fake Harbor Plan", "Fake Harbor Alias"]
    assert event["aliases_added"] == ["Fake Harbor Alias"]
    assert event["fields"]["type"]["value"] == "commercial"
    assert event["fields"]["notes"]["value"] == "Harbor how-to\n\nAlias how-to"
    assert event["fields"]["electronic_payer_id"]["value"] == "HARBOR-2"
    enrollment = event["enrollments"][0]
    assert enrollment["subject_name"] == "QA Sample Clinician"
    assert enrollment["choices"]["status"]["value"] == "in_network"
    assert enrollment["choices"]["payer_provider_id"]["value"] == "kept"
    dumped = json.dumps(event)
    assert "FAKEPROV1001" not in dumped
    assert "FAKEPROV2002" not in dumped
    archived = next(row for row in _grid(client)["payers"] if row["id"] == other["id"])
    assert archived["archived"] is True


def test_link_screen_exposes_the_preview_controls() -> None:
    script = open("app/static/credentialing.js", encoding="utf-8").read()
    assert "cg-link-payers" in script
    assert "cg-link-confirm" in script
    assert "cg-link-cancel" in script
    assert "/credentialing/payers/link" in script
    assert "canLinkPayers" in script
