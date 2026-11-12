"""State picker and US phone format for the employee contact form.

Synthetic values only. Odd stored state and phone values stay until edited.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import User, issue_token
from app.employees.contact_format import (
    PHONE_EXAMPLE,
    PHONE_HINT,
    STATE_ERROR,
    US_STATES,
    canonical_phone,
    canonical_state,
    format_us_phone,
    normalize_contact,
    normalize_phone,
    normalize_state,
    parse_us_phone,
    phone_error,
)
from app.employees.store import employees_store
from app.main import app

STREET = "1 QA Street"
CANONICAL = PHONE_EXAMPLE


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _alice() -> dict[str, str]:
    user = User("user-alice", "alice", "practice-a", "alice@example.com")
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _contact(**overrides: str) -> dict[str, str]:
    body = {
        "preferred_name": "",
        "street": "",
        "city": "",
        "state": "",
        "zip": "",
        "personal_phone": "",
        "personal_email": "",
        "work_phone": "",
        "emergency_name": "",
        "emergency_phone": "",
    }
    body.update(overrides)
    return body


def _seed_raw(**fields: str) -> None:
    payload, row = employees_store._load("practice-a")
    record = employees_store._record(row, "alice@example.com")
    for key, value in fields.items():
        record[key] = value
    employees_store._save(payload)


def test_state_list_is_fifty_states_plus_dc() -> None:
    assert len(US_STATES) == 51
    abbrs = [abbr for abbr, _name in US_STATES]
    names = [name for _abbr, name in US_STATES]
    assert len(set(abbrs)) == 51
    assert len(set(names)) == 51
    assert names == sorted(names)
    assert "DC" in abbrs
    assert "PR" not in abbrs
    assert dict(US_STATES)["PA"] == "Pennsylvania"
    assert dict(US_STATES)["DC"] == "District of Columbia"


def test_state_maps_abbreviation_and_name() -> None:
    assert canonical_state("PA") == "PA"
    assert canonical_state("pa") == "PA"
    assert canonical_state("Pennsylvania") == "PA"
    assert canonical_state("pennsylvania") == "PA"
    assert canonical_state("  Pennsylvania. ") == "PA"
    assert canonical_state("DC") == "DC"
    assert canonical_state("D.C.") == "DC"
    assert canonical_state("District of Columbia") == "DC"
    assert canonical_state("") == ""
    assert canonical_state("   ") == ""
    assert canonical_state("Commonwealth") is None
    assert normalize_state("pa", previous="Commonwealth") == "PA"
    assert normalize_state("", previous="PA") == ""
    assert normalize_state("Commonwealth", previous="Commonwealth") == "Commonwealth"
    assert normalize_state("commonwealth", previous="Commonwealth") == "Commonwealth"
    with pytest.raises(ValueError, match="Choose a state"):
        normalize_state("Atlantis", previous="")


def test_phone_parse_and_format() -> None:
    assert parse_us_phone("") == ""
    assert parse_us_phone("   ") == ""
    assert canonical_phone("5550100100") == CANONICAL
    assert canonical_phone("555-010-0100") == CANONICAL
    assert canonical_phone("(555)010-0100") == CANONICAL
    assert canonical_phone("(555) 010-0100") == CANONICAL
    assert canonical_phone("555.010.0100") == CANONICAL
    assert canonical_phone("+1 5550100100") == CANONICAL
    assert canonical_phone("1-555-010-0100") == CANONICAL
    assert canonical_phone("+1 (555) 010-0100") == CANONICAL
    assert format_us_phone("5550100100") == "(555)010-0100"
    assert " " not in CANONICAL
    assert canonical_phone("555-0100") is None
    assert canonical_phone("555010010") is None
    assert canonical_phone("555010010012") is None
    assert canonical_phone("25550100100") is None
    assert canonical_phone("() -") is None
    assert normalize_phone("", previous=CANONICAL, label="Personal phone") == ""
    assert normalize_phone("555-0100", previous="555-0100", label="Personal phone") == "555-0100"
    with pytest.raises(ValueError, match="Personal phone") as exc:
        normalize_phone("555-0100", previous="", label="Personal phone")
    assert PHONE_EXAMPLE in str(exc.value)
    assert "(555)010-" not in str(exc.value).replace(PHONE_EXAMPLE, "")


def test_other_contact_fields_pass_through() -> None:
    cleaned = normalize_contact(
        _contact(
            preferred_name="QA",
            street=STREET,
            city="QA City",
            state="Pennsylvania",
            zip="00000",
            personal_email="QA@example.com",
            emergency_name="QA Contact",
            personal_phone="5550100100",
        ),
        previous=_contact(),
    )
    assert cleaned["preferred_name"] == "QA"
    assert cleaned["street"] == STREET
    assert cleaned["city"] == "QA City"
    assert cleaned["zip"] == "00000"
    assert cleaned["personal_email"] == "QA@example.com"
    assert cleaned["emergency_name"] == "QA Contact"
    assert cleaned["state"] == "PA"
    assert cleaned["personal_phone"] == CANONICAL


def test_save_reload_and_clear_on_both_contact_routes(client: TestClient) -> None:
    saved = client.put(
        "/employee/me/contact",
        headers=_alice(),
        json=_contact(
            preferred_name="QA",
            street=STREET,
            city="QA City",
            state="Pennsylvania",
            zip="00000",
            personal_phone="(555) 010-0100",
            personal_email="qa@example.com",
            work_phone="5550100101",
            emergency_name="QA Contact",
            emergency_phone="555-010-0102",
        ),
    )
    assert saved.status_code == 200, saved.text
    contact = client.get("/employee/me", headers=_alice()).json()["contact"]
    assert contact["state"] == "PA"
    assert contact["personal_phone"] == "(555)010-0100"
    assert contact["work_phone"] == "(555)010-0101"
    assert contact["emergency_phone"] == "(555)010-0102"
    assert contact["street"] == STREET
    assert contact["city"] == "QA City"
    assert employees_store.rollout_enabled("practice-a") is False
    for raw in ("PA", "pa", "Pennsylvania", "pennsylvania"):
        mapped = client.put(
            "/employee/people/alice@example.com/contact",
            headers=_alice(),
            json=_contact(state=raw, street=STREET, personal_phone=CANONICAL),
        )
        assert mapped.status_code == 200, mapped.text
        assert mapped.json()["contact"]["state"] == "PA"
    cleared = client.put(
        "/employee/people/alice@example.com/contact",
        headers=_alice(),
        json=_contact(street=STREET, city="QA City"),
    )
    assert cleared.status_code == 200, cleared.text
    empty = client.get("/employee/me", headers=_alice()).json()["contact"]
    assert empty["state"] == ""
    assert empty["personal_phone"] == ""
    assert empty["work_phone"] == ""
    assert empty["emergency_phone"] == ""
    assert empty["street"] == STREET
    assert empty["city"] == "QA City"


def test_unknown_stored_state_and_phone_stay_until_edited(client: TestClient) -> None:
    _seed_raw(
        state="Commonwealth",
        personal_phone="555-0100",
        work_phone="ext 9",
        emergency_phone="call desk",
        street=STREET,
        city="QA City",
    )
    loaded = client.get("/employee/me", headers=_alice()).json()
    contact = loaded["contact"]
    assert contact["state"] == "Commonwealth"
    assert contact["personal_phone"] == "555-0100"
    assert contact["work_phone"] == "ext 9"
    assert contact["emergency_phone"] == "call desk"
    before = len(loaded["history"])
    resaved = client.put("/employee/me/contact", headers=_alice(), json=contact)
    assert resaved.status_code == 200, resaved.text
    again = resaved.json()
    assert len(again["history"]) == before
    assert again["contact"]["state"] == "Commonwealth"
    assert again["contact"]["personal_phone"] == "555-0100"
    assert again["contact"]["work_phone"] == "ext 9"
    assert again["contact"]["emergency_phone"] == "call desk"
    edited = dict(again["contact"])
    edited["state"] = "Pennsylvania"
    edited["personal_phone"] = "5550100100"
    updated = client.put("/employee/me/contact", headers=_alice(), json=edited)
    assert updated.status_code == 200, updated.text
    assert updated.json()["contact"]["state"] == "PA"
    assert updated.json()["contact"]["personal_phone"] == CANONICAL
    assert updated.json()["contact"]["work_phone"] == "ext 9"
    assert updated.json()["contact"]["emergency_phone"] == "call desk"
    cleared = dict(updated.json()["contact"])
    cleared["state"] = ""
    cleared["work_phone"] = ""
    gone = client.put("/employee/me/contact", headers=_alice(), json=cleared)
    assert gone.status_code == 200, gone.text
    reloaded = client.get("/employee/me", headers=_alice()).json()["contact"]
    assert reloaded["state"] == ""
    assert reloaded["work_phone"] == ""
    assert reloaded["personal_phone"] == CANONICAL
    assert reloaded["emergency_phone"] == "call desk"
    assert reloaded["street"] == STREET


def test_non_ten_digit_phone_is_rejected_and_leaves_the_record(client: TestClient) -> None:
    seeded = client.put(
        "/employee/me/contact",
        headers=_alice(),
        json=_contact(street=STREET, state="PA", personal_phone=CANONICAL, city="QA City"),
    )
    assert seeded.status_code == 200, seeded.text
    bad = client.put(
        "/employee/me/contact",
        headers=_alice(),
        json=_contact(
            street="9 QA Street",
            state="Atlantis",
            city="Other City",
            personal_phone="555-0100",
            work_phone="123",
            emergency_phone="555010010012",
        ),
    )
    assert bad.status_code == 422, bad.text
    detail = bad.json()["detail"]
    assert detail == " ".join(
        [
            STATE_ERROR,
            phone_error("Personal phone"),
            phone_error("Work phone"),
            phone_error("Emergency contact phone"),
        ]
    )
    assert "10-digit" in detail
    kept = client.get("/employee/me", headers=_alice()).json()["contact"]
    assert kept["street"] == STREET
    assert kept["city"] == "QA City"
    assert kept["state"] == "PA"
    assert kept["personal_phone"] == CANONICAL
    assert kept["work_phone"] == ""
    assert kept["emergency_phone"] == ""
    assert employees_store.rollout_enabled("practice-a") is False


def test_contact_ui_uses_the_state_picker_and_phone_format() -> None:
    script = Path("app/static/employee.js").read_text(encoding="utf-8")
    match = re.search(r"const US_STATES = \[(.*?)\];", script, re.S)
    assert match
    pairs = re.findall(r'\["([A-Z]{2})", "([^"]+)"\]', match.group(1))
    assert pairs == [(abbr, name) for abbr, name in US_STATES]
    assert "function statePicker(stored)" in script
    assert 'blank.textContent = "State"' in script
    assert 'pair[0] === "state"' in script
    assert 'addEventListener("blur"' in script
    assert 'return "(" + digits.slice(0, 3) + ")" + digits.slice(3, 6) + "-" + digits.slice(6);' in script
    assert PHONE_HINT in script
    assert "input.type = \"tel\"" in script
    page = Path("app/static/index.html").read_text(encoding="utf-8")
    assert 'src="/static/employee.js?v=0.19.7"' in page
    assert 'input[type="tel"]' in page
