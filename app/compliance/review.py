"""Possible-match review packets.

One Home item per person. The comparison is built from the list entry stored
at screening time. A missing entry can be re-read from the public list and
attached without changing the result or any decision. Test records are not
Home items.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.compliance.sources import ListFile, digits, iso_date, norm_token
from app.compliance.store import compliance_store
from app.hiring.checklist import add_business_days

EASTERN = ZoneInfo("America/New_York")
_CLOSED = {"not_the_same_person", "confirmed_exclusion"}
_LIST_LABEL = {"leie": "OIG LEIE", "sam": "SAM.gov", "medicheck": "PA Medicheck"}
_EXCLUSION_MEANING = {
    "1128a1": "Conviction for a program-related crime",
    "1128a2": "Conviction relating to patient abuse",
    "1128a3": "Felony conviction relating to health care fraud",
    "1128a4": "Felony conviction relating to a controlled substance",
    "1128b1": "Misdemeanor conviction relating to health care fraud",
    "1128b2": "Misdemeanor conviction relating to a controlled substance",
    "1128b3": "Conviction relating to obstructing an investigation",
    "1128b4": "License revoked, suspended, or surrendered",
    "1128b5": "Excluded or suspended by a federal or state health program",
    "1128b6": "Claims for excessive charges or unnecessary services",
    "1128b7": "Fraud, kickbacks, or other prohibited activities",
    "1128b8": "Entity owned or controlled by a sanctioned person",
    "1128b14": "Default on a health education loan or scholarship",
    "1128b15": "Person controlling a sanctioned entity",
    "1156": "Failed to meet professionally recognized standards of care",
}


def _today():
    return datetime.now(EASTERN).date()


def _closed_days(practice_id: str):
    from app.compliance.screening import _closed

    return _closed(practice_id)


def _decision(result_id: str, resolutions: list[dict[str, Any]]) -> str:
    found = [item for item in resolutions if item.get("result_id") == result_id]
    if not found:
        return ""
    return str(found[-1].get("decision") or "")


def _code_key(value: str) -> str:
    return "".join(ch for ch in (value or "").casefold() if ch.isalnum())


def exclusion_meaning(code: str) -> str:
    text = (code or "").strip()
    if not text:
        return ""
    meaning = _EXCLUSION_MEANING.get(_code_key(text))
    if meaning:
        return f"{text} — {meaning}"
    return text


def _full_name(first: str, middle: str, last: str) -> str:
    return " ".join(part for part in (first, middle, last) if part).strip()


def _tokens(value: str) -> list[str]:
    return [norm_token(part) for part in (value or "").split() if norm_token(part)]


def _name_match(list_name: str, staff_name: str, first: str, last: str) -> bool:
    if first and last:
        staff = _tokens(staff_name)
        if staff and norm_token(first) == staff[0] and norm_token(last) == staff[-1]:
            return True
        if len(staff) >= 2 and norm_token(first) == staff[-1] and norm_token(last) == staff[0]:
            return True
    left = _tokens(list_name)
    right = _tokens(staff_name)
    if len(left) >= 2 and len(right) >= 2:
        return left[0] == right[0] and left[-1] == right[-1]
    return bool(left and right and left == right)


def _city_state(address: str) -> tuple[str, str]:
    parts = [part.strip() for part in (address or "").split(",") if part.strip()]
    if len(parts) < 2:
        return "", ""
    tail = parts[-1].split()
    if tail and len(tail[0]) == 2 and tail[0].isalpha():
        return parts[-2], tail[0].upper()
    return "", ""


def staff_record(practice_id: str, person_id: str, display_name: str) -> dict[str, str]:
    """Fields we have on file. Missing values stay blank and render as Not available."""
    found = {
        "name": display_name,
        "dob": "",
        "npi": "",
        "city": "",
        "state": "",
        "license_state": "",
    }
    if person_id.startswith("subject:"):
        _subject_into(practice_id, person_id.split(":", 1)[1], found)
    elif person_id.startswith("employee:"):
        _employee_into(practice_id, person_id.split(":", 1)[1], found)
    elif person_id.startswith("hire:"):
        _hire_into(practice_id, person_id.split(":", 1)[1], found)
    elif person_id.startswith("supervisor:"):
        _supervisor_into(practice_id, person_id.split(":", 1)[1], found)
    if not found["name"]:
        found["name"] = display_name
    return found


def _subject_into(practice_id: str, subject_id: str, found: dict[str, str]) -> None:
    from app.credentialing.store import credentialing_store

    _, row = credentialing_store._load_practice(practice_id)
    subject = (row.get("subjects") or {}).get(subject_id)
    if not isinstance(subject, dict):
        return
    found["name"] = str(subject.get("legal_name") or subject.get("name") or found["name"])
    found["npi"] = digits(str(subject.get("npi") or ""))
    found["license_state"] = str(subject.get("license_state") or "").strip()
    found["dob"] = iso_date(str(subject.get("date_of_birth") or subject.get("dob") or ""))
    city, state = _city_state(str(subject.get("service_address") or ""))
    found["city"] = city
    found["state"] = state
    _employee_for_subject(practice_id, subject_id, found)


def _employee_for_subject(practice_id: str, subject_id: str, found: dict[str, str]) -> None:
    from app.employees.store import employees_store

    _, row = employees_store._load(practice_id)
    for record in (row.get("records") or {}).values():
        if isinstance(record, dict) and str(record.get("subject_id") or "") == subject_id:
            _contact_into(record, found)
            return


def _employee_into(practice_id: str, email: str, found: dict[str, str]) -> None:
    from app.employees.store import employees_store

    _, row = employees_store._load(practice_id)
    record = (row.get("records") or {}).get(email.strip().lower())
    if not isinstance(record, dict):
        return
    found["name"] = str(record.get("preferred_name") or found["name"])
    _contact_into(record, found)
    subject_id = str(record.get("subject_id") or "")
    if subject_id:
        _subject_into(practice_id, subject_id, found)
        found["name"] = str(record.get("preferred_name") or found["name"])


def _contact_into(record: dict[str, Any], found: dict[str, str]) -> None:
    if not found["city"]:
        found["city"] = str(record.get("city") or "").strip()
    if not found["state"]:
        found["state"] = str(record.get("state") or "").strip()
    if not found["dob"]:
        found["dob"] = iso_date(str(record.get("date_of_birth") or record.get("dob") or ""))


def _hire_into(practice_id: str, hire_id: str, found: dict[str, str]) -> None:
    from app.compliance.screening import hire_person

    try:
        person = hire_person(practice_id, hire_id)
    except Exception:
        return
    found["name"] = person.get("name") or found["name"]
    found["npi"] = digits(person.get("npi") or "")


def _supervisor_into(practice_id: str, supervisor_id: str, found: dict[str, str]) -> None:
    for item in compliance_store.outside_supervisors(practice_id):
        if item.get("id") == supervisor_id:
            found["name"] = str(item.get("name") or found["name"])
            return


def _show(value: str) -> str:
    return value.strip() if value and value.strip() else "Not available"


def _mark(left: str, right: str) -> str:
    if not left.strip() or not right.strip():
        return "Not available"
    if left.strip().casefold() == right.strip().casefold():
        return "Match"
    return "Different"


def _row(label: str, list_value: str, staff_value: str, *, name: bool = False, npi: bool = False, dob: bool = False) -> dict[str, str]:
    left = list_value.strip()
    right = staff_value.strip()
    if npi:
        left_digits = digits(left)
        right_digits = digits(right)
        if len(left_digits) == 10 and len(right_digits) == 10:
            mark = "Match" if left_digits == right_digits else "Different"
        else:
            mark = "Not available"
        return {"label": label, "list_value": _show(left), "staff_value": _show(right), "mark": mark}
    if dob:
        left_iso = iso_date(left) or left
        right_iso = iso_date(right) or right
        if iso_date(left) and iso_date(right):
            mark = "Match" if iso_date(left) == iso_date(right) else "Different"
        else:
            mark = "Not available"
        return {"label": label, "list_value": _show(left_iso), "staff_value": _show(right_iso), "mark": mark}
    if name:
        mark = "Not available"
        if left and right:
            mark = "Match" if _name_match(left, right, "", "") else "Different"
        return {"label": label, "list_value": _show(left), "staff_value": _show(right), "mark": mark}
    return {"label": label, "list_value": _show(left), "staff_value": _show(right), "mark": _mark(left, right)}


def _reinstate(record: dict[str, str]) -> str:
    parts = [part for part in (record.get("reinstatement_date") or "", record.get("waiver_date") or "") if part]
    return " / ".join(parts)


def comparison_rows(record: dict[str, str], staff: dict[str, str]) -> list[dict[str, str]]:
    list_name = _full_name(record.get("first") or "", record.get("middle") or "", record.get("last") or "")
    specialty = " / ".join(part for part in (record.get("specialty") or "", record.get("general") or "") if part)
    return [
        _row("Full name", list_name, staff.get("name") or "", name=True),
        _row("Date of birth", record.get("dob") or "", staff.get("dob") or "", dob=True),
        _row("NPI", record.get("npi") or "", staff.get("npi") or "", npi=True),
        _row("UPIN", record.get("upin") or "", ""),
        _row("Address", record.get("address") or "", ""),
        _row("City", record.get("city") or "", staff.get("city") or ""),
        _row("State", record.get("state") or "", staff.get("state") or ""),
        _row("Business name", record.get("business") or "", ""),
        _row("Specialty / general", specialty, ""),
        _row("Exclusion type", exclusion_meaning(record.get("exclusion_type") or ""), ""),
        _row("Exclusion date", record.get("exclusion_date") or "", ""),
        _row("Reinstatement / waiver date", _reinstate(record), ""),
        _row("SAM UEI", record.get("uei") or "", ""),
        _row("Record status", record.get("record_status") or "", ""),
        _row("Termination date", record.get("termination_date") or "", ""),
        _row("License state", "", staff.get("license_state") or ""),
    ]


def why_flagged(record: dict[str, str], staff: dict[str, str]) -> str:
    list_name = _full_name(record.get("first") or "", "", record.get("last") or "")
    names = _name_match(list_name, staff.get("name") or "", record.get("first") or "", record.get("last") or "")
    list_npi = digits(record.get("npi") or "")
    staff_npi = digits(staff.get("npi") or "")
    npi = len(list_npi) == 10 and list_npi == staff_npi
    if names and npi:
        return "Last name, first name, and NPI."
    if names:
        return "Last name and first name only."
    if npi:
        return "NPI only."
    return "The stored result flagged this list entry."


def likelihood(record: dict[str, str], staff: dict[str, str]) -> str:
    list_npi = digits(record.get("npi") or "")
    staff_npi = digits(staff.get("npi") or "")
    list_dob = iso_date(record.get("dob") or "")
    staff_dob = iso_date(staff.get("dob") or "")
    npi_differs = len(list_npi) == 10 and len(staff_npi) == 10 and list_npi != staff_npi
    dob_differs = bool(list_dob and staff_dob and list_dob != staff_dob)
    if npi_differs or dob_differs:
        return "Likely different person"
    if (len(list_npi) == 10 and list_npi == staff_npi) or (list_dob and list_dob == staff_dob):
        return "Likely same person"
    return "Needs a closer look"


def source_links(list_name: str, record: dict[str, str]) -> list[dict[str, str]]:
    last = record.get("last") or ""
    first = record.get("first") or ""
    links: list[dict[str, str]] = []
    if list_name == "leie":
        query = quote(last) if last else ""
        first_q = quote(first) if first else ""
        links.append(
            {
                "label": "OIG LEIE online search",
                "url": f"https://exclusions.oig.hhs.gov/?lastname={query}&firstname={first_q}",
            }
        )
    if list_name == "sam":
        uei = (record.get("uei") or "").strip()
        if uei:
            links.append({"label": "SAM.gov exclusion record", "url": f"https://sam.gov/entity/{quote(uei)}"})
        who = quote(" ".join(part for part in (first, last) if part))
        links.append({"label": "SAM.gov exclusion search", "url": f"https://sam.gov/search/?index=ex&q={who}"})
    if list_name == "medicheck":
        links.append(
            {
                "label": "PA Medicheck search",
                "url": "https://www.humanservices.dhs.pa.gov/Medchk/MedchkSearch/Index",
            }
        )
    return links


def _header(name: str, lists: list[str]) -> str:
    labels = [_LIST_LABEL.get(item, item) for item in lists]
    if len(labels) > 1:
        shown = " and ".join([", ".join(labels[:-1]), labels[-1]]) if len(labels) > 2 else " and ".join(labels)
        target = f"the {shown} exclusion lists"
    else:
        target = f"the {labels[0] if labels else 'exclusion'} exclusion list"
    return (
        f"Possible exclusion match: {name} may match an entry on {target}. "
        "Review before this person bills Medicaid/Medicare."
    )


def _packet_for(practice_id: str, item: dict[str, Any], staff: dict[str, str]) -> dict[str, Any]:
    records = [row for row in (item.get("list_records") or []) if isinstance(row, dict)]
    if not records:
        proof = (item.get("matches") or [{}])[0] if item.get("matches") else {}
        records = [
            {
                "last": str(proof.get("last") or ""),
                "first": str(proof.get("first") or ""),
                "middle": str(proof.get("middle") or ""),
                "state": str(proof.get("state") or ""),
                "exclusion_type": str(proof.get("kind") or ""),
            }
        ]
    record = {str(key): str(value or "") for key, value in records[0].items()}
    list_name = str(item.get("list_name") or "")
    refreshed = "list_records" in item and bool(item.get("list_records"))
    return {
        "result_id": str(item.get("id") or ""),
        "list_name": list_name,
        "list_label": _LIST_LABEL.get(list_name, list_name),
        "why": why_flagged(record, staff),
        "likelihood": likelihood(record, staff),
        "rows": comparison_rows(record, staff),
        "links": source_links(list_name, record),
        "ssn_note": "OIG can also verify a person by Social Security number on its site. Clynotion does not send a Social Security number.",
        "detail_note": "" if refreshed else "The full list entry is not stored on this result yet. The match result is unchanged.",
        "consequences": {
            "not_the_same_person": "Clears this match. Activate stays blocked until every flagged list is cleared.",
            "confirmed_exclusion": "This person cannot bill Medicaid or Medicare. Activate stays blocked.",
            "need_more_info": "Keeps this review open. Nothing is cleared and nothing is confirmed.",
        },
    }


def review_packets(practice_id: str) -> list[dict[str, Any]]:
    """Open official possible matches, grouped by person. Test rows are omitted."""
    resolutions = compliance_store.resolutions(practice_id)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in compliance_store.screenings(practice_id):
        if item.get("kind") == "test" or item.get("source_mode") == "fixture":
            continue
        if item.get("result") != "possible_match" or item.get("source_mode") != "official":
            continue
        if _decision(str(item.get("id") or ""), resolutions) in _CLOSED:
            continue
        grouped.setdefault(str(item.get("person_id") or ""), []).append(item)
    packets = []
    for person_id, items in grouped.items():
        name = str(items[0].get("display_name") or "This person")
        staff = staff_record(practice_id, person_id, name)
        lists = []
        seen: set[str] = set()
        rendered = []
        for item in items:
            list_name = str(item.get("list_name") or "")
            if list_name not in seen:
                seen.add(list_name)
                lists.append(list_name)
            rendered.append(_packet_for(practice_id, item, staff))
        packets.append(
            {
                "person_id": person_id,
                "name": staff.get("name") or name,
                "header": _header(staff.get("name") or name, lists),
                "lists": rendered,
            }
        )
    return packets


def sync_review_obligations(practice_id: str) -> None:
    """One open Home item per person. Retire per-list items and every test item."""
    from app.platform.due import due_engine

    resolutions = compliance_store.resolutions(practice_id)
    open_people: dict[str, str] = {}
    retire: set[str] = set()
    for item in compliance_store.screenings(practice_id):
        result_id = str(item.get("id") or "")
        person_id = str(item.get("person_id") or "")
        if item.get("kind") == "test" or item.get("source_mode") == "fixture":
            retire.add(result_id)
            retire.add(person_id)
            continue
        if item.get("result") != "possible_match" or item.get("source_mode") != "official":
            continue
        retire.add(result_id)
        if _decision(result_id, resolutions) in _CLOSED:
            continue
        open_people[person_id] = str(item.get("display_name") or "Staff member")
    for ref in retire:
        if ref:
            due_engine.complete(practice_id=practice_id, source="exclusion-review", source_ref=ref)
    known = {
        str(item.get("person_id") or "")
        for item in compliance_store.screenings(practice_id)
        if item.get("source_mode") == "official" and item.get("result") == "possible_match" and item.get("kind") != "test"
    }
    for person_id in known:
        if person_id not in open_people:
            due_engine.complete(practice_id=practice_id, source="exclusion-review", source_ref=person_id)
    closed = _closed_days(practice_id)
    due = add_business_days(_today(), 1, closed)
    due_at = datetime.combine(due, time(17, 0), tzinfo=EASTERN).isoformat()
    for person_id, name in open_people.items():
        if not person_id:
            continue
        due_engine.upsert(
            practice_id=practice_id,
            domain="people",
            source="exclusion-review",
            title=("Possible exclusion match: " + name)[:180],
            owner_user_id="admin",
            due_at=due_at,
            href="/#screening?review=" + person_id,
            source_ref=person_id,
            status="open",
        )


def _records_for(item: dict[str, Any], listing: ListFile) -> list[dict[str, str]]:
    from app.compliance.screening import _split_name, match_rows

    proofs = [row for row in (item.get("matches") or []) if isinstance(row, dict)]
    found = []
    for proof in proofs:
        last = str(proof.get("last") or "")
        first = str(proof.get("first") or "")
        hits = [
            person
            for person in listing.rows
            if norm_token(person.last) == norm_token(last) and norm_token(person.first) == norm_token(first)
        ]
        masked = str(proof.get("npi_masked") or "")
        tail = "".join(ch for ch in masked if ch.isdigit())
        if tail:
            narrowed = [person for person in hits if digits(person.npi).endswith(tail)]
            if narrowed:
                hits = narrowed
        if hits:
            found.append(hits[0])
    if found:
        return [person.record() for person in found[:8]]
    name = str(item.get("display_name") or "")
    first, last = _split_name(name)
    hits = match_rows(first, last, "", listing.rows)
    return [person.record() for person in hits[:8]]


def backfill_list_records(
    practice_id: str,
    *,
    lists: Optional[dict[str, ListFile]] = None,
) -> int:
    """Attach list entries that were not stored. Does not change result or decisions."""
    pending = [
        item
        for item in compliance_store.screenings(practice_id)
        if item.get("result") == "possible_match"
        and item.get("source_mode") == "official"
        and item.get("kind") != "test"
        and "list_records" not in item
    ]
    if not pending:
        return 0
    supplied = dict(lists or {})
    fetcher = None
    today = None
    if lists is None:
        from datetime import date

        from app.compliance.sources import HttpxFetcher

        fetcher = HttpxFetcher()
        today = date.today()
    attached = 0
    for item in pending:
        list_name = str(item.get("list_name") or "")
        listing = supplied.get(list_name)
        if listing is None and fetcher is not None and today is not None:
            from app.compliance.sources import load_leie, load_medicheck, load_sam_person

            if list_name == "leie":
                supplied["leie"] = load_leie(fetcher, today=today)
                listing = supplied["leie"]
            elif list_name == "medicheck":
                supplied["medicheck"] = load_medicheck(fetcher, today=today)
                listing = supplied["medicheck"]
            elif list_name == "sam":
                parts = str(item.get("display_name") or "").split()
                first = parts[0] if parts else ""
                last = parts[-1] if parts else ""
                listing = load_sam_person(fetcher, first=first, last=last, npi="", today=today)
        if listing is None or not listing.ready:
            continue
        records = _records_for(item, listing)
        if not records:
            continue
        before = str(item.get("result") or "")
        compliance_store.set_list_records(practice_id, str(item.get("id") or ""), records)
        after = next(
            (row for row in compliance_store.screenings(practice_id) if row.get("id") == item.get("id")),
            {},
        )
        if str(after.get("result") or "") != before:
            continue
        attached += 1
    return attached
