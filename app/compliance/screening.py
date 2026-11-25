"""Exclusion screening runs.

Monthly screening stays off until an Admin turns it on. A roster run also
requires the confirmation phrase. Development and deploy do not call this
against real staff by themselves.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.compliance.audit_chain import note
from app.compliance.masking import mask_text
from app.compliance.sources import (
    HttpxFetcher,
    ListFile,
    ListedPerson,
    load_leie,
    load_medicheck,
    load_sam_person,
    norm_token,
    sam_configured,
)
from app.compliance.store import LISTS, compliance_store
from app.hiring.checklist import add_business_days, is_business_day

EASTERN = ZoneInfo("America/New_York")
ROSTER_CONFIRM = "SCREEN ROSTER"


class ScreeningError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _today(moment: Optional[datetime] = None) -> date:
    current = moment or datetime.now(EASTERN)
    if current.tzinfo is None:
        current = current.replace(tzinfo=EASTERN)
    return current.astimezone(EASTERN).date()


def _closed(practice_id: str) -> set[date]:
    try:
        from app.hiring.engine import hiring

        payload = hiring._read()
        row = hiring._practice(payload, practice_id)
        raw = (row.get("settings") or {}).get("business_closed_days") or []
        return {date.fromisoformat(str(item)[:10]) for item in raw if str(item or "").strip()}
    except Exception:
        return set()


def _person_id_ok(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z]+:[A-Za-z0-9_@.+-]{1,120}", value or ""))


def _variants(name: str) -> list[str]:
    parts = [part for part in re.split(r"\s+", (name or "").replace(",", " ").strip()) if part]
    if not parts:
        return []
    if len(parts) == 1:
        return [parts[0]]
    first, last = parts[0], parts[-1]
    middle = " ".join(parts[1:-1])
    found = [f"{first} {last}", f"{last}, {first}"]
    if middle:
        found.append(f"{first} {middle} {last}")
    if first:
        found.append(f"{first[0]} {last}")
    return found


def _split_name(name: str) -> tuple[str, str]:
    parts = [part for part in re.split(r"\s+", (name or "").replace(",", " ").strip()) if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _names_match(person_first: str, person_last: str, row: ListedPerson) -> bool:
    last = norm_token(person_last)
    first = norm_token(person_first)
    row_last = norm_token(row.last)
    row_first = norm_token(row.first)
    if not last or not row_last or last != row_last:
        return False
    if not first or not row_first:
        return False
    if first == row_first:
        return True
    shorter, longer = (first, row_first) if len(first) <= len(row_first) else (row_first, first)
    return len(shorter) >= 1 and longer.startswith(shorter) and (len(shorter) == 1 or len(shorter) >= 3)


def _npi_match(npi: str, row: ListedPerson) -> bool:
    left = "".join(ch for ch in (npi or "") if ch.isdigit())
    right = "".join(ch for ch in (row.npi or "") if ch.isdigit())
    return len(left) == 10 and left == right


def match_rows(first: str, last: str, npi: str, rows: list[ListedPerson]) -> list[ListedPerson]:
    found: list[ListedPerson] = []
    for row in rows:
        if not row.active:
            continue
        if _npi_match(npi, row) or _names_match(first, last, row):
            found.append(row)
    return found[:8]


def _credentialing_people(practice_id: str) -> list[dict[str, str]]:
    from app.credentialing.store import credentialing_store

    _, row = credentialing_store._load_practice(practice_id)
    found: list[dict[str, str]] = []
    for subject in (row.get("subjects") or {}).values():
        if not isinstance(subject, dict) or subject.get("archived"):
            continue
        if str(subject.get("kind") or "") != "clinician":
            continue
        if subject.get("active") is False:
            continue
        found.append(
            {
                "person_id": "subject:" + str(subject.get("id") or ""),
                "name": str(subject.get("name") or ""),
                "npi": str(subject.get("npi") or ""),
                "kind": "employee",
            }
        )
    return found


def _employee_people(practice_id: str, already: set[str]) -> list[dict[str, str]]:
    from app.credentialing.store import credentialing_store
    from app.employees.store import employees_store

    _, cred = credentialing_store._load_practice(practice_id)
    subjects = cred.get("subjects") or {}
    _, row = employees_store._load(practice_id)
    found: list[dict[str, str]] = []
    for record in (row.get("records") or {}).values():
        if not isinstance(record, dict):
            continue
        subject_id = str(record.get("subject_id") or "")
        if subject_id:
            subject = subjects.get(subject_id) if isinstance(subjects, dict) else None
            if isinstance(subject, dict) and (subject.get("archived") or subject.get("active") is False):
                continue
            if "subject:" + subject_id in already:
                continue
        name = str(record.get("preferred_name") or "").strip()
        if not name:
            continue
        found.append(
            {
                "person_id": "employee:" + str(record.get("email") or ""),
                "name": name,
                "npi": "",
                "kind": "employee",
            }
        )
    return found


def _supervisor_people(practice_id: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for item in compliance_store.outside_supervisors(practice_id):
        if not item.get("active") or not item.get("paid"):
            continue
        if str(item.get("kind") or "") == "vendor":
            continue
        found.append(
            {
                "person_id": "supervisor:" + str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "npi": "",
                "kind": "outside_supervisor",
            }
        )
    return found


def roster(practice_id: str) -> list[dict[str, str]]:
    """Active employees and paid outside supervisors. Vendors are not included."""
    people = _credentialing_people(practice_id)
    seen = {item["person_id"] for item in people}
    for item in _employee_people(practice_id, seen):
        if item["person_id"] not in seen:
            people.append(item)
            seen.add(item["person_id"])
    for item in _supervisor_people(practice_id):
        if item["person_id"] not in seen and item["name"]:
            people.append(item)
    return [item for item in people if item.get("name")]


def hire_person(practice_id: str, hire_id: str) -> dict[str, str]:
    from app.hiring.engine import hiring

    payload = hiring._read()
    row = hiring._practice(payload, practice_id)
    hire = (row.get("hires") or {}).get(hire_id)
    if not isinstance(hire, dict):
        raise ScreeningError(404, "Hire not found.")
    candidate = (row.get("candidates") or {}).get(hire.get("candidate_id") or "") or {}
    offer = (row.get("offers") or {}).get(hire.get("offer_id") or "") or {}
    version = (row.get("offer_versions") or {}).get(offer.get("current_version_id") or "") or {}
    values = version.get("field_values") if isinstance(version.get("field_values"), dict) else {}
    name = str(values.get("candidate_full_name") or candidate.get("display_name") or "").strip()
    if not name:
        raise ScreeningError(400, "That hire has no name to screen.")
    kind = "test" if hire.get("test_record") else "hire"
    person = {"person_id": f"hire:{hire_id}", "name": name, "npi": str(values.get("npi") or ""), "kind": kind}
    if hire.get("test_record"):
        person["created_on"] = str(hire.get("created_on") or "")[:10]
    return person


def test_people(practice_id: str) -> list[dict[str, str]]:
    """Flagged test hires only. They are not part of the employee roster."""
    from app.hiring.engine import hiring

    payload = hiring._read()
    row = hiring._practice(payload, practice_id)
    found: list[dict[str, str]] = []
    for hire in (row.get("hires") or {}).values():
        if not isinstance(hire, dict) or not hire.get("test_record"):
            continue
        if hire.get("status") == "archived":
            continue
        candidate = (row.get("candidates") or {}).get(hire.get("candidate_id") or "") or {}
        name = str(candidate.get("display_name") or "").strip()
        if not name:
            continue
        found.append(
            {
                "person_id": f"hire:{hire.get('id')}",
                "name": name,
                "npi": "",
                "kind": "test",
                "created_on": str(hire.get("created_on") or "")[:10],
            }
        )
    return found


def is_test_person(practice_id: str, person_id: str) -> bool:
    if not str(person_id or "").startswith("hire:"):
        return False
    return any(item["person_id"] == person_id for item in test_people(practice_id))


def _search_terms(name: str, npi: str) -> list[str]:
    terms = _variants(name)
    digits = "".join(ch for ch in (npi or "") if ch.isdigit())
    if len(digits) == 10:
        terms.append("npi:" + mask_text(digits))
    return terms


def _result_row(
    person: dict[str, str],
    listing: ListFile,
    matches: list[ListedPerson],
    *,
    purpose: str,
    actor: str,
    source_mode: str,
) -> dict[str, Any]:
    if not listing.ready:
        result = "not_run"
        detail = listing.detail or "not run"
    elif matches or (listing.reported_count > 0 and listing.name == "sam" and not matches):
        result = "possible_match"
        detail = ""
        if listing.reported_count and not matches:
            detail = "The source reported records that could not be read as names."
    else:
        result = "clear"
        detail = ""
    proofs = [match.proof() for match in matches]
    row = {
        "person_id": person["person_id"],
        "display_name": person["name"],
        "kind": person.get("kind") or "",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "list_name": listing.name,
        "list_updated_on": listing.updated_on,
        "search_terms": _search_terms(person["name"], person.get("npi") or ""),
        "result": result,
        "detail": detail,
        "source_sha256": listing.file_sha256,
        "matches": proofs,
        "purpose": purpose,
        "source_mode": source_mode,
        "actor": actor,
    }
    if person.get("created_on"):
        row["created_on"] = person["created_on"]
    row["list_records"] = [match.record() for match in matches]
    return row


def _store_result(practice_id: str, row: dict[str, Any]) -> dict[str, Any]:
    stored = compliance_store.append_screening(practice_id, row)
    note(
        practice_id,
        action="screen",
        object_type="exclusion_screening",
        object_id=str(stored.get("id") or ""),
        actor=row.get("actor") or "",
        diff={
            "person_id": stored.get("person_id"),
            "list_name": stored.get("list_name"),
            "result": stored.get("result"),
            "list_updated_on": stored.get("list_updated_on"),
            "source_mode": stored.get("source_mode"),
        },
    )
    if stored.get("result") == "possible_match" and stored.get("kind") != "test" and stored.get("source_mode") != "fixture":
        from app.compliance.review import sync_review_obligations

        sync_review_obligations(practice_id)
    return stored


def _official_lists(person: dict[str, str], today: date, fetcher: HttpxFetcher, leie: Optional[ListFile], medicheck: Optional[ListFile]) -> list[ListFile]:
    first, last = _split_name(person["name"])
    if leie is None:
        leie = load_leie(fetcher, today=today)
    if medicheck is None:
        medicheck = load_medicheck(fetcher, today=today)
    try:
        sam = load_sam_person(fetcher, first=first, last=last, npi="".join(ch for ch in person.get("npi", "") if ch.isdigit()), today=today)
    except Exception:
        sam = ListFile(name="sam", updated_on="", file_sha256="", ready=False, detail="source unavailable")
    return [leie, sam, medicheck]


def run_screening(
    practice_id: str,
    actor: str,
    *,
    person_ids: Optional[list[str]] = None,
    purpose: str = "manual",
    confirm: str = "",
    lists: Optional[dict[str, ListFile]] = None,
    fetcher: Optional[HttpxFetcher] = None,
    moment: Optional[datetime] = None,
) -> dict[str, Any]:
    """Screen one or more people. A full roster requires the confirmation phrase."""
    if person_ids:
        selected = _select(practice_id, person_ids)
    else:
        if confirm != ROSTER_CONFIRM:
            raise ScreeningError(400, "Choose one person, or type SCREEN ROSTER to run the active roster.")
        selected = roster(practice_id)
    if not selected:
        raise ScreeningError(400, "Nobody to screen.")
    today = _today(moment)
    client = fetcher or HttpxFetcher()
    supplied = lists or {}
    leie = supplied.get("leie")
    medicheck = supplied.get("medicheck")
    if leie is None and "leie" not in supplied and lists is None:
        try:
            leie = load_leie(client, today=today)
        except Exception:
            leie = ListFile(name="leie", updated_on="", file_sha256="", ready=False, detail="source unavailable")
    if medicheck is None and lists is None:
        try:
            medicheck = load_medicheck(client, today=today)
        except Exception:
            medicheck = ListFile(name="medicheck", updated_on="", file_sha256="", ready=False, detail="manual check required")
    stored_rows: list[dict[str, Any]] = []
    for person in selected:
        if lists is not None and "sam" in lists:
            files = [supplied["leie"], supplied["sam"], supplied["medicheck"]]
        else:
            files = _official_lists(person, today, client, leie, medicheck)
        first, last = _split_name(person["name"])
        for listing in files:
            if listing is None:
                continue
            matches = match_rows(first, last, person.get("npi") or "", listing.rows) if listing.ready else []
            row = _result_row(person, listing, matches, purpose=purpose, actor=actor, source_mode="official")
            stored_rows.append(_store_result(practice_id, row))
    if person_ids is None:
        tally = {"clear": 0, "possible_match": 0, "not_run": 0}
        for item in stored_rows:
            key = str(item.get("result") or "")
            if key in tally:
                tally[key] += 1
        note(
            practice_id,
            action="screen_roster",
            object_type="exclusion_screening",
            object_id=practice_id,
            actor=actor,
            diff={
                "people": len(selected),
                "results": len(stored_rows),
                "clear": tally["clear"],
                "possible_match": tally["possible_match"],
                "not_run": tally["not_run"],
            },
        )
    return {"count": len(stored_rows), "results": [public_screening(item) for item in stored_rows]}


def _select(practice_id: str, person_ids: list[str]) -> list[dict[str, str]]:
    known = {item["person_id"]: item for item in roster(practice_id)}
    selected: list[dict[str, str]] = []
    for person_id in person_ids:
        if person_id.startswith("hire:"):
            selected.append(hire_person(practice_id, person_id.split(":", 1)[1]))
            continue
        item = known.get(person_id)
        if item is None:
            raise ScreeningError(404, "That person is not on the screening roster.")
        selected.append(item)
    return selected


def record_test_match(practice_id: str, actor: str, *, person_id: str) -> dict[str, Any]:
    """Always record a labeled TEST LEIE possible match. Name text is not parsed."""
    if not is_test_person(practice_id, person_id):
        raise ScreeningError(403, "A test match can only be recorded for a test record.")
    person = _select(practice_id, [person_id])[0]
    listing = ListFile(name="leie", updated_on="test", file_sha256="test-fixture", rows=[], ready=True)
    synthetic = ListedPerson(last="TEST", first="RECORD", kind="TEST", state="TEST", active=True)
    row = _result_row(person, listing, [synthetic], purpose="fixture", actor=actor, source_mode="fixture")
    row["detail"] = "TEST possible match"
    row["list_name"] = "leie"
    stored = _store_result(practice_id, row)
    return public_screening(stored)


def close_open_reviews(practice_id: str, person_id: str) -> None:
    """Close Home items for one person. Screening rows stay."""
    from app.platform.due import due_engine

    for item in compliance_store.screenings(practice_id):
        if item.get("person_id") != person_id:
            continue
        due_engine.complete(
            practice_id=practice_id,
            source="exclusion-review",
            source_ref=str(item.get("id") or ""),
        )


_TEST_CLOSED = "closed_test_record"


def _is_open_test_match(item: dict[str, Any], resolutions: list[dict[str, Any]]) -> bool:
    """A synthetic TEST possible match with no decision yet. Official rows are not included."""
    if item.get("source_mode") != "fixture" or item.get("kind") != "test":
        return False
    if item.get("result") != "possible_match":
        return False
    if str(item.get("detail") or "") != "TEST possible match":
        return False
    return _resolution_for(str(item.get("id") or ""), resolutions) is None


def close_test_matches(practice_id: str, person_id: str, *, actor: str) -> int:
    """Close unresolved TEST matches for one hire. Stored results and other decisions stay."""
    from app.platform.due import due_engine

    resolutions = compliance_store.resolutions(practice_id)
    closed = 0
    for item in compliance_store.screenings(practice_id):
        if item.get("person_id") != person_id or not _is_open_test_match(item, resolutions):
            continue
        result_id = str(item.get("id") or "")
        compliance_store.append_resolution(
            practice_id,
            {
                "result_id": result_id,
                "person_id": person_id,
                "decision": _TEST_CLOSED,
                "note": "",
                "actor": actor or "clynotion",
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )
        due_engine.complete(practice_id=practice_id, source="exclusion-review", source_ref=result_id)
        closed += 1
        resolutions = compliance_store.resolutions(practice_id)
    if closed:
        note(
            practice_id,
            action="close_test_match",
            object_type="exclusion_review",
            object_id=person_id,
            actor=actor or "clynotion",
            diff={"closed": closed},
        )
    return closed


def close_orphaned_test_matches(practice_id: str, *, actor: str) -> int:
    """Close TEST matches whose hire is already gone. Official results stay."""
    from app.hiring.engine import hiring

    payload = hiring._read()
    row = hiring._practice(payload, practice_id)
    hires = row.get("hires") or {}
    resolutions = compliance_store.resolutions(practice_id)
    pending: list[str] = []
    for item in compliance_store.screenings(practice_id):
        person_id = str(item.get("person_id") or "")
        if not person_id.startswith("hire:") or person_id in pending:
            continue
        if not _is_open_test_match(item, resolutions):
            continue
        hire_id = person_id.split(":", 1)[1]
        if hire_id in hires:
            continue
        pending.append(person_id)
    return sum(close_test_matches(practice_id, person_id, actor=actor) for person_id in pending)


def roster_counts(people: list[dict[str, str]]) -> dict[str, int]:
    """How many people are on the screening roster, by kind. Test hires are not included."""
    counts = {"total": len(people), "employee": 0, "outside_supervisor": 0}
    for item in people:
        kind = str(item.get("kind") or "")
        if kind in counts:
            counts[kind] += 1
    return counts


def _latest(rows: list[dict[str, Any]], list_name: str) -> Optional[dict[str, Any]]:
    found = [item for item in rows if item.get("list_name") == list_name]
    if not found:
        return None
    return found[-1]


def _resolution_for(result_id: str, resolutions: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    found = [item for item in resolutions if item.get("result_id") == result_id]
    return found[-1] if found else None


def gate(practice_id: str, person_id: str) -> dict[str, Any]:
    """Clean pre-hire screening on LEIE, SAM, and Medicheck. A missing SAM key does not pass."""
    rows = [
        item
        for item in compliance_store.screenings(practice_id)
        if item.get("person_id") == person_id and item.get("source_mode") == "official"
    ]
    resolutions = [item for item in compliance_store.resolutions(practice_id) if item.get("person_id") == person_id]
    confirmed = any(item.get("decision") == "confirmed_exclusion" for item in resolutions)
    lists_out: dict[str, Any] = {}
    allowed = not confirmed
    messages: list[str] = []
    if confirmed:
        messages.append("Confirmed exclusion.")
        allowed = False
    manuals = [item for item in compliance_store.manual_checks(practice_id) if item.get("person_id") == person_id]
    manual = manuals[-1] if manuals else None
    for name in LISTS:
        latest = _latest(rows, name)
        state = "missing"
        if latest:
            decision = _resolution_for(str(latest.get("id") or ""), resolutions)
            if decision and decision.get("decision") == "not_the_same_person":
                state = "clear"
            elif decision and decision.get("decision") == "confirmed_exclusion":
                state = "confirmed_exclusion"
            else:
                state = str(latest.get("result") or "missing")
            if state == "possible_match":
                allowed = False
                messages.append(f"{name} has a possible match to review.")
            elif state == "not_run":
                if name == "medicheck" and manual and manual.get("checked_on") and manual.get("file_sha256"):
                    state = "manual"
                else:
                    allowed = False
                    detail = str(latest.get("detail") or "not run")
                    messages.append(f"{name}: {detail}.")
            elif state == "confirmed_exclusion":
                allowed = False
            elif state != "clear":
                allowed = False
                messages.append(f"{name} is not clear.")
        else:
            if name == "medicheck" and manual and manual.get("checked_on") and manual.get("file_sha256"):
                state = "manual"
            else:
                allowed = False
                messages.append(f"{name} has not been run.")
        lists_out[name] = {
            "state": state,
            "checked_at": (latest or {}).get("checked_at") or "",
            "list_updated_on": (latest or {}).get("list_updated_on") or "",
            "detail": (latest or {}).get("detail") or "",
        }
    if not rows and not confirmed:
        allowed = False
    for item in compliance_store.screenings(practice_id):
        if item.get("person_id") != person_id or item.get("source_mode") != "fixture":
            continue
        if item.get("result") != "possible_match":
            continue
        decision = _resolution_for(str(item.get("id") or ""), resolutions)
        if decision and decision.get("decision") == "not_the_same_person":
            continue
        if decision and decision.get("decision") == "confirmed_exclusion":
            confirmed = True
            allowed = False
            messages.append("Confirmed exclusion.")
            continue
        allowed = False
        messages.append("A TEST possible match is still open.")
    return {
        "person_id": person_id,
        "allowed": allowed and not confirmed,
        "confirmed": confirmed,
        "banner": "Confirmed exclusion" if confirmed else "",
        "lists": lists_out,
        "message": " ".join(messages) if not allowed else "Exclusion check is clear.",
        "manual_medicheck": bool(manual and manual.get("checked_on") and manual.get("file_sha256")),
    }


def activation_block_reason(practice_id: str, hire_id: str) -> str:
    status = gate(practice_id, f"hire:{hire_id}")
    if status["allowed"]:
        return ""
    return "Exclusion check is not clear. " + status["message"]


def resolve_match(
    practice_id: str,
    result_id: str,
    *,
    decision: str,
    note_text: str,
    actor: str,
    audit: bool = True,
    sync: bool = True,
) -> dict[str, Any]:
    if decision not in {"not_the_same_person", "confirmed_exclusion", "need_more_info"}:
        raise ScreeningError(400, "Choose not the same person, confirmed exclusion, or need more info.")
    text = (note_text or "").strip()
    if not text:
        raise ScreeningError(400, "A short reason is required.")
    if len(text) > 500:
        raise ScreeningError(400, "That note is too long.")
    rows = compliance_store.screenings(practice_id)
    result = next((item for item in rows if item.get("id") == result_id), None)
    if result is None:
        raise ScreeningError(404, "That screening result was not found.")
    if result.get("result") != "possible_match":
        raise ScreeningError(409, "That result is not a possible match.")
    stored = compliance_store.append_resolution(
        practice_id,
        {
            "result_id": result_id,
            "person_id": result.get("person_id") or "",
            "decision": decision,
            "note": text,
            "actor": actor,
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )
    from app.platform.due import due_engine

    due_engine.complete(practice_id=practice_id, source="exclusion-review", source_ref=result_id)
    if sync and result.get("kind") != "test" and result.get("source_mode") != "fixture":
        from app.compliance.review import sync_review_obligations

        sync_review_obligations(practice_id)
    if audit:
        note(
            practice_id,
            action="resolve_exclusion",
            object_type="exclusion_review",
            object_id=str(stored.get("id") or ""),
            actor=actor,
            diff={"decision": decision, "result_id": result_id, "note": text[:180]},
        )
    return {
        "id": stored["id"],
        "decision": decision,
        "gate": gate(practice_id, str(result.get("person_id") or "")),
    }


def resolve_person(
    practice_id: str,
    review_id: str,
    *,
    decision: str,
    note_text: str,
    actor: str,
) -> dict[str, Any]:
    """Apply one decision to every open list for that person. Stored results stay as they are."""
    from app.compliance.review import review_token, sync_review_obligations

    if decision not in {"not_the_same_person", "confirmed_exclusion", "need_more_info"}:
        raise ScreeningError(400, "Choose not the same person, confirmed exclusion, or need more info.")
    text = (note_text or "").strip()
    if not text:
        raise ScreeningError(400, "A short reason is required.")
    chosen = ""
    open_ids: list[str] = []
    for item in compliance_store.screenings(practice_id):
        if item.get("kind") == "test" or item.get("source_mode") != "official":
            continue
        if item.get("result") != "possible_match":
            continue
        person_id = str(item.get("person_id") or "")
        if review_token(practice_id, person_id) != review_id:
            continue
        chosen = person_id
        resolutions = compliance_store.resolutions(practice_id)
        latest = [row for row in resolutions if row.get("result_id") == item.get("id")]
        if latest and str(latest[-1].get("decision") or "") in {"not_the_same_person", "confirmed_exclusion"}:
            continue
        open_ids.append(str(item.get("id") or ""))
    if not chosen:
        raise ScreeningError(404, "That review was not found.")
    last: dict[str, Any] = {"decision": decision}
    for result_id in open_ids:
        last = resolve_match(
            practice_id,
            result_id,
            decision=decision,
            note_text=text,
            actor=actor,
            audit=False,
            sync=False,
        )
    sync_review_obligations(practice_id)
    note(
        practice_id,
        action="resolve_exclusion",
        object_type="exclusion_review",
        object_id=review_id,
        actor=actor,
        diff={"decision": decision, "review_id": review_id, "lists": len(open_ids), "note": text[:180]},
    )
    return last


def record_manual_medicheck(
    practice_id: str,
    *,
    person_id: str,
    checked_on: str,
    filename: str,
    data: bytes,
    actor: str,
    comment: str = "",
) -> dict[str, Any]:
    if not _person_id_ok(person_id):
        raise ScreeningError(400, "That person id is not valid.")
    try:
        checked = date.fromisoformat(checked_on[:10]).isoformat()
    except ValueError as exc:
        raise ScreeningError(400, "Use a YYYY-MM-DD date.") from exc
    if not data or len(data) > 8_000_000:
        raise ScreeningError(400, "Attach a screenshot or PDF under 8 MB.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "", filename or "")[:80] or "medicheck-result.pdf"
    digest = hashlib.sha256(data).hexdigest()
    stored = compliance_store.append_manual(
        practice_id,
        {
            "person_id": person_id,
            "checked_on": checked,
            "filename": safe_name,
            "file_sha256": digest,
            "size": len(data),
            "actor": actor,
            "at": datetime.now(timezone.utc).isoformat(),
            "comment": (comment or "")[:300],
        },
    )
    compliance_store.write_manual_file(practice_id, str(stored["id"]), data)
    note(
        practice_id,
        action="manual_medicheck",
        object_type="medicheck_check",
        object_id=str(stored["id"]),
        actor=actor,
        diff={"person_id": person_id, "checked_on": checked, "file_sha256": digest},
    )
    return {"id": stored["id"], "checked_on": checked, "file_sha256": digest, "gate": gate(practice_id, person_id)}


def public_screening(item: dict[str, Any]) -> dict[str, Any]:
    from app.compliance.audit_chain import show_et

    return {
        "id": item.get("id") or "",
        "person_id": item.get("person_id") or "",
        "display_name": item.get("display_name") or "",
        "kind": item.get("kind") or "",
        "checked_at": item.get("checked_at") or "",
        "checked_at_et": show_et(str(item.get("checked_at") or "")),
        "list_name": item.get("list_name") or "",
        "list_updated_on": item.get("list_updated_on") or "",
        "search_terms": list(item.get("search_terms") or []),
        "result": item.get("result") or "",
        "detail": item.get("detail") or "",
        "source_sha256": item.get("source_sha256") or "",
        "matches": list(item.get("matches") or []),
        "purpose": item.get("purpose") or "",
        "source_mode": item.get("source_mode") or "",
        "actor": item.get("actor") or "",
        "created_on": item.get("created_on") or "",
    }


def filtered_screenings(
    practice_id: str,
    *,
    person_id: str = "",
    month: str = "",
    list_name: str = "",
    include_test: bool = False,
) -> list[dict[str, Any]]:
    rows = [public_screening(item) for item in compliance_store.screenings(practice_id)]
    if not include_test:
        rows = [item for item in rows if item.get("kind") != "test"]
    if person_id:
        rows = [item for item in rows if item["person_id"] == person_id]
    if list_name:
        rows = [item for item in rows if item["list_name"] == list_name]
    if month:
        rows = [item for item in rows if str(item["checked_at"]).startswith(month)]
    return rows


def first_business_day(year: int, month: int, closed: set[date]) -> date:
    cursor = date(year, month, 1)
    while not is_business_day(cursor, closed):
        cursor += timedelta(days=1)
    return cursor


def monthly_window(moment: datetime, closed: set[date], last_key: str) -> bool:
    """True only at 7:00 AM Eastern on the first business day, once per month."""
    local = moment.astimezone(EASTERN)
    if local.hour != 7 or local.minute > 1:
        return False
    if local.date() != first_business_day(local.year, local.month, closed):
        return False
    key = f"{local.year:04d}-{local.month:02d}"
    return last_key != key


def sam_status() -> str:
    if sam_configured():
        return "configured"
    return "not run: API key not configured"
