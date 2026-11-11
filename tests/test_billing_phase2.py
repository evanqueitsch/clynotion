"""Phase 2 reminders, Billing home, and the digest switch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.access.store import access_store
from app.auth import User, issue_token
from app.comply.credentials import credential_store
from app.credentialing.digest import send_weekly_digest
from app.credentialing.reminders import sync_reminders
from app.credentialing.store import credentialing_store
from app.main import app
from app.platform.due import due_engine
from app.workflow.engine import JevAdvisor, RulesEngine, workflow_engine
from tests.test_time_entry import _alice


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _client() -> TestClient:
    return TestClient(app)


def _billing_user() -> User:
    user = User("user-bill", "QA Billing", "practice-a", "billing-test@example.com")
    access_store.set_assignment("practice-a", user.email, "billing")
    return user


def _staff() -> User:
    user = User("user-staff2", "QA Staff", "practice-a", "staff-test@example.com")
    access_store.set_assignment("practice-a", user.email, "clinician")
    access_store.update_role_permissions("practice-a", "clinician", ["tool:time_entry"])
    return user


def _day(offset: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


def _seed_pair(
    *,
    status: str,
    submitted: str = "",
    effective: str = "",
    expires: str = "",
    attested: str = "",
    clocks: dict | None = None,
    subject_id: str = "",
    payer_id: str = "",
) -> tuple[str, str]:
    payer = credentialing_store.save_payer(
        "practice-a",
        {
            "id": payer_id,
            "name": "QA Test Payer",
            "type": "commercial",
            "accepts_licenses": ["LCSW"],
        },
        actor="qa",
    )
    subject = credentialing_store.save_subject(
        "practice-a",
        {
            "id": subject_id,
            "name": "QA Test Clinician",
            "kind": "clinician",
            "license_type": "LSW",
            "license_expires_on": expires,
            "caqh_attested_on": attested,
            "clocks": clocks or {},
        },
        actor="qa",
    )
    credentialing_store.save_enrollment(
        "practice-a",
        {
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": status,
            "submitted_on": submitted,
            "effective_on": effective,
            "note": "QA note",
        },
        actor="qa",
    )
    return subject["id"], payer["id"]


def _refs(practice_id: str = "practice-a") -> set[str]:
    return {
        ob.source_ref
        for ob in due_engine.list_open(practice_id)
        if ob.source == "credentialing"
    }


def test_follow_up_and_stalled_clear_when_status_changes() -> None:
    due_engine.reset()
    credentialing_store.reset()
    subject_id, payer_id = _seed_pair(status="pending", submitted=_day(-10))
    counts = sync_reminders("practice-a", "qa-user")
    assert counts.get("follow_up") == 1
    assert "stalled" not in counts
    assert any(ref.startswith("follow_up|") for ref in _refs())
    _seed_pair(status="pending", submitted=_day(-100), subject_id=subject_id, payer_id=payer_id)
    counts = sync_reminders("practice-a", "qa-user")
    assert counts.get("stalled") == 1
    _seed_pair(
        status="in_network",
        submitted=_day(-100),
        effective=_day(-10),
        subject_id=subject_id,
        payer_id=payer_id,
    )
    counts = sync_reminders("practice-a", "qa-user")
    assert "follow_up" not in counts
    assert "stalled" not in counts
    assert not any(ref.startswith("follow_up|") or ref.startswith("stalled|") for ref in _refs())


def test_recredential_reminders_clear_on_new_effective_date() -> None:
    due_engine.reset()
    credentialing_store.reset()
    subject_id, payer_id = _seed_pair(status="in_network", effective=_day(-(1095 - 40)))
    counts = sync_reminders("practice-a", "qa-user")
    assert counts.get("recredential_90") == 1
    assert counts.get("recredential_30") == 1
    old = {ref for ref in _refs() if ref.startswith("recredential_")}
    assert old
    _seed_pair(status="in_network", effective=_day(-1), subject_id=subject_id, payer_id=payer_id)
    sync_reminders("practice-a", "qa-user")
    new = {ref for ref in _refs() if ref.startswith("recredential_")}
    assert new
    assert new.isdisjoint(old)


def test_license_caqh_and_clock_reminders_clear_on_new_dates() -> None:
    due_engine.reset()
    credentialing_store.reset()
    subject_id, payer_id = _seed_pair(
        status="unknown",
        expires=_day(20),
        attested=_day(-110),
        clocks={"malpractice_rider": _day(-360)},
    )
    counts = sync_reminders("practice-a", "qa-user")
    assert counts.get("license_60") == 1
    assert counts.get("license_30") == 1
    assert counts.get("caqh") == 1
    assert counts.get("malpractice") == 1
    old_license = {ref for ref in _refs() if ref.startswith("license_")}
    old_caqh = {ref for ref in _refs() if ref.startswith("caqh|")}
    old_mal = {ref for ref in _refs() if ref.startswith("malpractice|")}
    _seed_pair(
        status="unknown",
        expires=_day(400),
        attested=_day(-1),
        clocks={"malpractice_rider": _day(-1)},
        subject_id=subject_id,
        payer_id=payer_id,
    )
    sync_reminders("practice-a", "qa-user")
    assert {ref for ref in _refs() if ref.startswith("license_")}.isdisjoint(old_license)
    assert {ref for ref in _refs() if ref.startswith("caqh|")}.isdisjoint(old_caqh)
    assert {ref for ref in _refs() if ref.startswith("malpractice|")}.isdisjoint(old_mal)


def test_exclusion_and_ma_use_existing_cadence() -> None:
    due_engine.reset()
    credentialing_store.reset()
    subject_id, payer_id = _seed_pair(
        status="unknown",
        clocks={
            "clinician_exclusion_screening": _day(-40),
            "ma_revalidation": _day(-(1825 - 3)),
        },
    )
    counts = sync_reminders("practice-a", "qa-user")
    assert counts.get("exclusion") == 1
    assert counts.get("ma_revalidation") == 1
    _seed_pair(
        status="unknown",
        clocks={
            "clinician_exclusion_screening": _day(-1),
            "ma_revalidation": _day(-1),
        },
        subject_id=subject_id,
        payer_id=payer_id,
    )
    sync_reminders("practice-a", "qa-user")
    assert not any(ref.startswith("exclusion|") and _day(-40) in ref for ref in _refs())


def test_billing_role_sees_home_items_and_staff_is_blocked() -> None:
    due_engine.reset()
    credentialing_store.reset()
    client = _client()
    _seed_pair(status="pending", submitted=_day(-20))
    billing = _billing_user()
    home = client.get("/home", headers=_auth(billing))
    assert home.status_code == 200, home.text
    ids = [tool["id"] for unit in home.json()["units"] for tool in unit["tools"]]
    assert "billing" in ids
    assert "credentialing" in ids
    titles = [item["title"] for item in home.json()["bands"]["overdue"]]
    assert any(title.startswith("Follow up") for title in titles)
    staff = _staff()
    assert client.get("/billing", headers=_auth(staff)).status_code == 403
    staff_home = client.get("/home", headers=_auth(staff)).json()
    staff_ids = [tool["id"] for unit in staff_home["units"] for tool in unit["tools"]]
    assert "billing" not in staff_ids
    page = client.get("/billing", headers=_auth(billing))
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["digest_enabled"] is False
    assert {link["href"] for link in body["links"]} == {"/#credentialing", "/#eligibility"}
    assert any(item["code"] == "o4" for item in body["obligations"])
    assert any(item["code"] == "o12" for item in body["obligations"])


def test_o4_completion_is_the_same_record_as_the_registry() -> None:
    due_engine.reset()
    client = _client()
    billing = _billing_user()
    page = client.get("/billing", headers=_auth(billing)).json()
    item = next(row for row in page["obligations"] if row["code"] == "o4")
    done = client.post(
        f"/billing/obligations/{item['obligation_id']}/complete",
        headers=_auth(billing),
    )
    assert done.status_code == 200, done.text
    again = client.get("/billing", headers=_auth(billing)).json()
    assert all(row["code"] != "o4" for row in again["obligations"])
    registry = client.get("/due", headers=_alice()).json()
    flat = registry["overdue"] + registry["this_week"]
    assert all(row.get("source_ref") != "o4" for row in flat)
    seeded = client.post("/comply/seed", headers=_alice())
    assert seeded.status_code == 200
    still = client.get("/billing", headers=_auth(billing)).json()
    assert all(row["code"] != "o4" for row in still["obligations"])


def test_digest_stays_off_and_only_admin_can_toggle() -> None:
    credentialing_store.reset()
    client = _client()
    billing = _billing_user()
    denied = client.put(
        "/billing/digest",
        headers=_auth(billing),
        json={"enabled": True},
    )
    assert denied.status_code == 403
    assert send_weekly_digest("practice-a") == {"sent": False, "reason": "off"}
    saved = client.put("/billing/digest", headers=_alice(), json={"enabled": True})
    assert saved.status_code == 200, saved.text
    assert saved.json()["enabled"] is True
    assert send_weekly_digest("practice-a")["sent"] is False


def test_quickbooks_money_is_forbidden_without_the_capability() -> None:
    client = _client()
    assert client.get("/quickbooks", headers=_alice()).status_code == 403
    assert client.get("/quickbooks", headers=_auth(_billing_user())).status_code == 403


def test_clock_migration_adds_missing_dates_and_keeps_the_source() -> None:
    credentialing_store.reset()
    credential_store.reset()
    from app.clinicians import clinician_store

    clinician = next(iter(clinician_store.list_for_practice("practice-a")), None)
    assert clinician is not None
    credential_store._data.setdefault("practice-a", {}).setdefault(clinician.clinician_id, {})[
        "malpractice_rider"
    ] = "2024-01-15T00:00:00+00:00"
    credentialing_store.save_subject(
        "practice-a",
        {"name": clinician.display_name, "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    credentialing_store.migrate_clock_fields("practice-a")
    view = credentialing_store.reminder_view("practice-a")
    copied = next(row for row in view["subjects"] if row["name"] == clinician.display_name)
    assert copied["clocks"]["malpractice_rider"] == "2024-01-15"
    assert (
        credential_store.last_completed("practice-a", clinician.clinician_id, "malpractice_rider")
        == "2024-01-15T00:00:00+00:00"
    )


def test_workflow_rules_are_local_and_jev_is_off() -> None:
    assert isinstance(workflow_engine(), RulesEngine)
    assert workflow_engine().suggest("which stalled file is next")["source"] == "rules"
    assert JevAdvisor().suggest("stalled") is None
