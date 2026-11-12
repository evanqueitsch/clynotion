"""Phase 2 reminders, Billing home, and the digest switch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    has_expiration: bool | None = None,
    enrollment_expires: str = "",
    follow: str = "",
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
    enrollment = {
        "subject_id": subject["id"],
        "payer_id": payer["id"],
        "status": status,
        "submitted_on": submitted,
        "effective_on": effective,
        "note": "QA note",
    }
    if has_expiration is not None:
        enrollment["has_expiration"] = has_expiration
        enrollment["expires_on"] = enrollment_expires
        enrollment["next_follow_up"] = follow
    credentialing_store.save_enrollment("practice-a", enrollment, actor="qa")
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


def test_recredential_reminders_follow_the_expiration_date() -> None:
    due_engine.reset()
    credentialing_store.reset()
    subject_id, payer_id = _seed_pair(status="in_network", effective=_day(-(1095 - 40)))
    counts = sync_reminders("practice-a", "qa-user")
    assert "recredential_90" not in counts
    assert "recredential_30" not in counts
    _seed_pair(
        status="in_network",
        effective=_day(-10),
        has_expiration=True,
        enrollment_expires=_day(40),
        subject_id=subject_id,
        payer_id=payer_id,
    )
    counts = sync_reminders("practice-a", "qa-user")
    assert counts.get("recredential_90") == 1
    assert counts.get("recredential_30") == 1
    old = {ref for ref in _refs() if ref.startswith("recredential_")}
    assert old
    _seed_pair(
        status="in_network",
        has_expiration=True,
        enrollment_expires=_day(400),
        subject_id=subject_id,
        payer_id=payer_id,
    )
    sync_reminders("practice-a", "qa-user")
    new = {ref for ref in _refs() if ref.startswith("recredential_")}
    assert new
    assert new.isdisjoint(old)
    _seed_pair(status="in_network", has_expiration=False, subject_id=subject_id, payer_id=payer_id)
    sync_reminders("practice-a", "qa-user")
    assert not any(ref.startswith("recredential_") or ref.startswith("expiration_follow_up|") for ref in _refs())


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


def _js_block(source: str, open_brace: int) -> tuple[str, int]:
    """Return the interior of the `{` at open_brace and the index just after its close."""
    if source[open_brace] != "{":
        raise AssertionError("expected a block")
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : index], index + 1
    raise AssertionError("unclosed block")


def test_home_bands_render_when_full_catalog_is_false() -> None:
    """Credentialing Home must paint overdue and this-week rows without Admin catalog."""
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function enterHome()")
    body, _end = _js_block(html, html.index("{", start))
    gate = body.index("if (fullCatalog)")
    if_body, after_if = _js_block(body, body.index("{", gate))
    tail = body[after_if:].lstrip()
    assert tail.startswith("else"), tail[:80]
    else_body, after_else = _js_block(tail, tail.index("{"))
    outside = body[:gate] + tail[after_else:]
    assert "renderHomeBands(home.bands || { overdue: [], this_week: [] })" in outside
    assert "renderPulse" in if_body
    assert "renderAttention" in if_body
    assert "home-pulse" in else_body
    assert "home-attention-wrap" in else_body
    for element_id in ("home-overdue-wrap", "home-week-wrap", "home-due-empty"):
        assert element_id not in else_body
    assert 'rolesCard.classList.toggle("hidden", !fullCatalog)' in body[:gate]


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


def test_clock_fields_save_on_the_clinician_and_survive_reload() -> None:
    due_engine.reset()
    credentialing_store.reset()
    client = _client()
    practice = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Practice", "kind": "practice"},
        actor="qa",
    )
    clinician = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    saved = client.put(
        "/credentialing/subjects",
        headers=_alice(),
        json={
            "id": clinician["id"],
            "name": "QA Test Clinician",
            "kind": "clinician",
            "license_type": "LCSW",
            "license_expires_on": "2026-12-01",
            "caqh_attested_on": "2026-08-01",
            "clocks": {
                "license_renewal": "2026-01-15",
                "caqh_reattestation": "2026-08-01",
                "malpractice_rider": "2026-12-01",
                "recredentialing": "2026-06-01",
                "ma_revalidation": "2026-03-01",
                "clinician_exclusion_screening": "2026-11-01",
            },
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["id"] == clinician["id"]
    reloaded = client.get("/credentialing/grid", headers=_alice()).json()
    again = next(row for row in reloaded["subjects"] if row["id"] == clinician["id"])
    other = next(row for row in reloaded["subjects"] if row["id"] == practice["id"])
    assert again["license_expires_on"] == "2026-12-01"
    assert again["caqh_attested_on"] == "2026-08-01"
    assert again["clocks"]["malpractice_rider"] == "2026-12-01"
    assert again["clocks"]["license_renewal"] == "2026-01-15"
    assert "malpractice_rider" not in (other.get("clocks") or {})
    script = open("app/static/credentialing.js", encoding="utf-8").read()
    assert 'placeholder = "MM/DD/YYYY"' in script
    assert "Use Nov 9, 2026 as 11/09/2026." in script
    assert 'input.type = "date"' not in script
    assert "cg-profile-pick" in script and "previous" in script


def test_empty_clock_date_clears_the_saved_value() -> None:
    credentialing_store.reset()
    client = _client()
    clinician = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    payload = {
        "id": clinician["id"],
        "name": "QA Test Clinician",
        "kind": "clinician",
        "license_type": "LCSW",
        "license_expires_on": "2027-01-01",
        "clocks": {"malpractice_rider": "2026-12-01"},
    }
    saved = client.put("/credentialing/subjects", headers=_alice(), json=payload)
    assert saved.status_code == 200, saved.text
    stored = client.get("/credentialing/grid", headers=_alice()).json()
    row = next(item for item in stored["subjects"] if item["id"] == clinician["id"])
    assert row["clocks"]["malpractice_rider"] == "2026-12-01"
    assert row["license_expires_on"] == "2027-01-01"
    payload["clocks"] = {"malpractice_rider": ""}
    payload["license_expires_on"] = ""
    cleared = client.put("/credentialing/subjects", headers=_alice(), json=payload)
    assert cleared.status_code == 200, cleared.text
    again = client.get("/credentialing/grid", headers=_alice()).json()
    row = next(item for item in again["subjects"] if item["id"] == clinician["id"])
    assert not (row.get("clocks") or {}).get("malpractice_rider")
    assert row["license_expires_on"] == ""
    payload["clocks"] = {"malpractice_rider": None}
    nulled = client.put("/credentialing/subjects", headers=_alice(), json=payload)
    assert nulled.status_code == 200, nulled.text
    final = client.get("/credentialing/grid", headers=_alice()).json()
    row = next(item for item in final["subjects"] if item["id"] == clinician["id"])
    assert not (row.get("clocks") or {}).get("malpractice_rider")
    script = open("app/static/credentialing.js", encoding="utf-8").read()
    assert "Due one year after this date." in script


def test_drawer_follow_up_matches_the_home_due_date() -> None:
    from zoneinfo import ZoneInfo

    due_engine.reset()
    credentialing_store.reset()
    client = _client()
    subject_id, payer_id = _seed_pair(status="pending", submitted="2026-10-30")
    grid = client.get("/credentialing/grid", headers=_alice()).json()
    cell = next(
        row
        for row in grid["cells"]
        if row["subject_id"] == subject_id and row["payer_id"] == payer_id
    )
    assert cell["follow_up_on"] == "2026-11-13"
    sync_reminders("practice-a", "qa-user", now=datetime(2026, 11, 11, 15, tzinfo=timezone.utc))
    item = next(ob for ob in due_engine.list_open("practice-a") if ob.source_ref.startswith("follow_up|"))
    shown = datetime.fromisoformat(item.due_at).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    assert shown == cell["follow_up_on"]
    past_id, past_payer = _seed_pair(status="pending", submitted="2026-09-01")
    past = client.get("/credentialing/grid", headers=_alice()).json()
    past_cell = next(
        row
        for row in past["cells"]
        if row["subject_id"] == past_id and row["payer_id"] == past_payer
    )
    assert past_cell["follow_up_on"] == "2026-09-15"
    assert past_cell["follow_up_overdue"] is True


def test_open_counter_matches_the_listed_bands() -> None:
    from app.platform.routes import _build_pulse

    due_engine.reset()
    now = datetime.now(timezone.utc)
    due_engine.upsert(
        practice_id="practice-a",
        domain="credentialing",
        source="credentialing",
        title="Later QA item",
        owner_user_id="qa-user",
        due_at=(now + timedelta(days=30)).isoformat(),
        href="/#credentialing",
        source_ref="later-qa",
    )
    due_engine.upsert(
        practice_id="practice-a",
        domain="credentialing",
        source="credentialing",
        title="Overdue QA item",
        owner_user_id="qa-user",
        due_at=(now - timedelta(days=2)).isoformat(),
        href="/#credentialing",
        source_ref="overdue-qa",
    )
    pulse = _build_pulse("practice-a")
    bands = due_engine.bands("practice-a")
    listed = len(bands["overdue"]) + len(bands["this_week"])
    assert pulse.open_obligations == listed
    assert pulse.open_obligations < len(due_engine.list_open("practice-a"))


def test_reminder_due_instant_stays_on_that_eastern_date() -> None:
    from datetime import date
    from zoneinfo import ZoneInfo

    from app.credentialing.reminders import _due

    shown = datetime.fromisoformat(_due(date(2026, 11, 13))).astimezone(ZoneInfo("America/New_York"))
    assert shown.date().isoformat() == "2026-11-13"
    assert shown.hour == 12


def test_short_roster_name_migrates_one_clock_date() -> None:
    credentialing_store.reset()
    credential_store.reset()
    from app.clinicians import clinician_store

    clinician = next(iter(clinician_store.list_for_practice("practice-a")))
    credential_store._data.setdefault("practice-a", {}).setdefault(clinician.clinician_id, {})[
        "malpractice_rider"
    ] = "2025-06-01T00:00:00+00:00"
    credentialing_store.save_subject(
        "practice-a",
        {"name": clinician.display_name.split()[0], "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    added_before = __import__("app.credentialing.store", fromlist=["clocks_migrated_total"]).clocks_migrated_total()
    credentialing_store.migrate_clock_fields("practice-a")
    _payload, row = credentialing_store._load_practice("practice-a")
    copied = next(
        item
        for item in row["subjects"].values()
        if item.get("name") == clinician.display_name.split()[0]
    )
    assert copied["clocks"]["malpractice_rider"] == "2025-06-01"
    from app.credentialing.store import clocks_migrated_total

    assert clocks_migrated_total() == added_before + 1


def test_compliance_home_due_is_the_eastern_date_without_a_time() -> None:
    import subprocess
    from pathlib import Path

    html = Path("app/static/index.html").read_text(encoding="utf-8")
    start = html.index("function dateOnlyDue")
    end = html.index("function renderDueRow")
    script = html[start:end] + """
const cases = [
  ["2026-11-13T22:57:00Z", "comply", "compliance_registry", "Nov 13, 2026"],
  ["2026-11-16T22:57:00Z", "comply", "compliance_registry", "Nov 16, 2026"],
  ["2026-11-13T22:57:00Z", "comply", "credential_clocks", "Nov 13, 2026"],
];
for (const [iso, domain, source, expected] of cases) {
  const text = formatDue(iso, domain, source);
  if (text !== expected) {
    console.error("expected", expected, "got", text);
    process.exit(2);
  }
  if (/\\d:\\d\\d|AM|PM|5:57|7:00|8:00/.test(text)) process.exit(3);
}
const timed = formatDue("2026-11-13T22:57:00Z", "people", "supervision");
if (!/PM|AM|\\d:\\d\\d/.test(timed)) process.exit(4);
"""
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_workflow_rules_are_local_and_jev_is_off() -> None:
    assert isinstance(workflow_engine(), RulesEngine)
    assert workflow_engine().suggest("which stalled file is next")["source"] == "rules"
    assert JevAdvisor().suggest("stalled") is None
