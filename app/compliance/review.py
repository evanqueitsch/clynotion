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
    """Fields already stored for this person. Nothing here is written back."""
    found = {
        "name": display_name,
        "dob": "",
        "npi": "",
        "city": "",
        "state": "",
        "license_state": "",
        "license_type": "",
        "specialties": "",
        "hire_date": "",
        "service_state": "",
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
    found["npi"] = digits(str(subject.get("npi") or "")) or found["npi"]
    found["license_state"] = str(subject.get("license_state") or "").strip() or found["license_state"]
    found["license_type"] = str(subject.get("license_type") or "").strip() or found["license_type"]
    specialties = [str(item).strip() for item in (subject.get("specialties") or []) if str(item).strip()]
    if specialties:
        found["specialties"] = ", ".join(specialties)
    found["hire_date"] = iso_date(str(subject.get("hire_date") or "")) or found["hire_date"]
    found["dob"] = iso_date(str(subject.get("date_of_birth") or subject.get("dob") or "")) or found["dob"]
    city, service_state = _city_state(str(subject.get("service_address") or ""))
    if city and not found["city"]:
        found["city"] = city
    if service_state:
        found["service_state"] = service_state
    for key in ("license_state", "licensure_state", "licenseState"):
        value = str(subject.get(key) or "").strip()
        if value and not found["license_state"]:
            found["license_state"] = value
    _employee_for_subject(practice_id, subject_id, found, str(subject.get("name") or ""))
    clinician_id = str(subject.get("clinician_id") or "")
    if clinician_id and not found["state"]:
        _employee_for_clinician(practice_id, clinician_id, found)


def _employee_for_subject(practice_id: str, subject_id: str, found: dict[str, str], name: str) -> None:
    from app.employees.store import employees_store

    _, row = employees_store._load(practice_id)
    records = [item for item in (row.get("records") or {}).values() if isinstance(item, dict)]
    match = next((item for item in records if str(item.get("subject_id") or "") == subject_id), None)
    if match is None and name:
        wanted = name.casefold().strip()
        match = next(
            (item for item in records if str(item.get("preferred_name") or "").casefold().strip() == wanted),
            None,
        )
    if match is not None:
        _contact_into(match, found)


def _employee_for_clinician(practice_id: str, clinician_id: str, found: dict[str, str]) -> None:
    from app.clinicians import clinician_store
    from app.employees.store import employees_store

    clinician = clinician_store.get(practice_id, clinician_id)
    email = str(getattr(clinician, "email", "") or "").strip().lower()
    if not email:
        return
    _, row = employees_store._load(practice_id)
    record = (row.get("records") or {}).get(email)
    if isinstance(record, dict):
        _contact_into(record, found)


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
    if not found.get("specialties"):
        names = [str(item).strip() for item in (record.get("specialties") or []) if str(item).strip()]
        if names:
            found["specialties"] = ", ".join(names)


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


_NURSING = {"nursing", "nurse", "rn", "lpn", "aprn", "crnp", "cna", "aide"}
_COUNSELING = {"lpc", "lcsw", "lsw", "lmsw", "lisw", "lmft", "mft", "counselor", "therapist", "psychology", "psychologist", "socialwork"}
_MEDICINE = {"physician", "md", "do", "medicine"}


def _words(value: str) -> set[str]:
    return {"".join(ch for ch in part.casefold() if ch.isalnum()) for part in (value or "").replace("/", " ").replace("-", " ").split() if part}


def _family(value: str) -> str:
    words = _words(value)
    if words & _NURSING or any("nurs" in word for word in words):
        return "nursing"
    if words & _COUNSELING or any(word.startswith("lpc") or word.startswith("lcsw") for word in words):
        return "counseling"
    if words & _MEDICINE:
        return "medicine"
    return ""


def _role_phrase(staff: dict[str, str]) -> str:
    text = " ".join(part for part in (staff.get("license_type") or "", staff.get("specialties") or "") if part)
    family = _family(text)
    license_type = (staff.get("license_type") or "").strip()
    if family == "nursing":
        return "a nurse"
    if family == "counseling":
        folded = license_type.casefold()
        if "lcsw" in folded or folded == "lsw":
            return "a licensed social worker"
        return "a licensed counselor"
    if family == "medicine":
        return "a physician"
    if license_type:
        return "a " + license_type
    return "on our staff"


def _list_role(record: dict[str, str]) -> str:
    text = " ".join(part for part in (record.get("specialty") or "", record.get("general") or "") if part)
    if _family(text) == "nursing":
        return "a nurse"
    if _family(text) == "counseling":
        return "a counselor"
    if _family(text) == "medicine":
        return "a physician"
    if text:
        return text[:80]
    return "an excluded provider"


def _profession(value: str) -> str:
    return value.strip() if _family(value) else ""


def _normalize_state(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    compact = "".join(ch for ch in text if ch.isalpha()).upper()
    if len(compact) == 2:
        return compact
    names = {
        "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
        "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA",
        "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
        "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
        "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO",
        "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEWHAMPSHIRE": "NH", "NEWJERSEY": "NJ",
        "NEWMEXICO": "NM", "NEWYORK": "NY", "NORTHCAROLINA": "NC", "NORTHDAKOTA": "ND", "OHIO": "OH",
        "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODEISLAND": "RI", "SOUTHCAROLINA": "SC",
        "SOUTHDAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
        "VIRGINIA": "VA", "WASHINGTON": "WA", "WESTVIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
        "DISTRICTOFCOLUMBIA": "DC",
    }
    return names.get(compact, "")


def _state_codes(*values: str) -> list[str]:
    found: list[str] = []
    for value in values:
        code = _normalize_state(value)
        if code and code not in found:
            found.append(code)
    return found


def _staff_state(staff: dict[str, str]) -> tuple[str, list[str]]:
    """Contact state, otherwise the credentialing license state. The label says which."""
    contact = _normalize_state(staff.get("state") or "")
    licensed = _normalize_state(staff.get("license_state") or "")
    service = _normalize_state(staff.get("service_state") or "")
    parts: list[str] = []
    codes: list[str] = []
    if contact:
        parts.append(contact + " (contact)")
        codes.append(contact)
    if licensed and licensed not in codes:
        parts.append(licensed + " (license)")
        codes.append(licensed)
    elif licensed and not contact:
        parts.append(licensed + " (license)")
    if not contact and not licensed and service:
        parts.append(service + " (service address)")
        codes.append(service)
    return ", ".join(parts), codes


def _both(left: str, right: str) -> bool:
    return not left.strip() and not right.strip()


def _row(label: str, list_value: str, staff_value: str, *, name: bool = False, npi: bool = False, dob: bool = False, mark: str = "") -> dict[str, str]:
    left = list_value.strip()
    right = staff_value.strip()
    blank = _both(left, right)
    if mark:
        chosen = mark
    elif npi:
        left_digits = digits(left)
        right_digits = digits(right)
        blank = len(left_digits) != 10 and len(right_digits) != 10
        if len(left_digits) == 10 and len(right_digits) == 10:
            chosen = "Match" if left_digits == right_digits else "Different"
        else:
            chosen = "Not available"
    elif dob:
        if iso_date(left) and iso_date(right):
            chosen = "Match" if iso_date(left) == iso_date(right) else "Different"
        else:
            chosen = "Not available"
        left = iso_date(left) or left
        right = iso_date(right) or right
    elif name:
        chosen = "Not available"
        if left and right:
            chosen = "Match" if _name_match(left, right, "", "") else "Different"
    else:
        chosen = _mark(left, right)
    return {
        "label": label,
        "list_value": _show(left),
        "staff_value": _show(right),
        "mark": chosen,
        "both_blank": "yes" if blank else "",
    }


def _reinstate(record: dict[str, str]) -> str:
    parts = [part for part in (record.get("reinstatement_date") or "", record.get("waiver_date") or "") if part]
    return " / ".join(parts)


def _list_profession_text(record: dict[str, str]) -> str:
    specialty = (record.get("specialty") or "").strip()
    general = (record.get("general") or "").strip()
    if _family(specialty):
        return specialty
    if _family(general):
        return general
    return ""


def _article(phrase: str) -> str:
    word = " ".join((phrase or "").split())
    if not word:
        return ""
    head = word.split()[0]
    letters = "".join(ch for ch in head if ch.isalpha())
    initialism = bool(letters) and letters.isupper() and len(letters) <= 6
    if initialism:
        an = letters[:1] in set("AEFHILMNORSX")
        shown = word
    else:
        an = head[:1].lower() in "aeiou"
        shown = word.casefold()
    return ("an " if an else "a ") + shown


def comparison_rows(record: dict[str, str], staff: dict[str, str]) -> list[dict[str, str]]:
    list_name = _full_name(record.get("first") or "", record.get("middle") or "", record.get("last") or "")
    list_profession = _list_profession_text(record)
    staff_profession = " · ".join(part for part in (staff.get("license_type") or "", staff.get("specialties") or "") if part)
    list_states = _state_codes(record.get("state") or "")
    staff_state, our_states = _staff_state(staff)
    if not list_states or not our_states:
        state_mark = "Not available"
    elif list_states[0] in our_states and all(code == list_states[0] for code in our_states):
        state_mark = "Match"
    else:
        state_mark = "Different"
    list_family = _family(list_profession)
    staff_family = _family(staff_profession)
    if list_profession and staff_profession and list_family and staff_family:
        discipline_mark = "Match" if list_family == staff_family else "Different"
    elif list_profession and staff_profession:
        discipline_mark = "Match" if list_profession.casefold() == staff_profession.casefold() else "Different"
    else:
        discipline_mark = "Not available"
    return [
        _row("Full name", list_name, staff.get("name") or "", name=True),
        _row("Date of birth", record.get("dob") or "", staff.get("dob") or "", dob=True),
        _row("NPI", record.get("npi") or "", staff.get("npi") or "", npi=True),
        _row("UPIN", record.get("upin") or "", ""),
        _row("Address", record.get("address") or "", ""),
        _row("City", record.get("city") or "", staff.get("city") or ""),
        _row("State", " ".join(list_states), staff_state, mark=state_mark),
        _row("Business name", record.get("business") or "", ""),
        _row("Specialty / discipline", list_profession, staff_profession, mark=discipline_mark),
        _row("Hire date", "", staff.get("hire_date") or ""),
        _row("Exclusion type", exclusion_meaning(record.get("exclusion_type") or ""), ""),
        _row("Excluding agency", record.get("agency") or "", ""),
        _row("Exclusion date", record.get("exclusion_date") or "", ""),
        _row("Reinstatement / waiver date", _reinstate(record), ""),
        _row("SAM UEI", record.get("uei") or "", ""),
        _row("Record status", record.get("record_status") or "", ""),
        _row("Termination date", record.get("termination_date") or "", ""),
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


def _shown(row: dict[str, str], side: str) -> str:
    value = str(row.get(side) or "")
    return "" if value == "Not available" else value


def likelihood_from_rows(rows: list[dict[str, str]], *, list_name: str = "") -> dict[str, str]:
    """The label follows the table. A Different profession is enough on its own."""
    by_label = {str(row.get("label") or ""): row for row in rows}
    npi = by_label.get("NPI") or {}
    dob = by_label.get("Date of birth") or {}
    specialty = by_label.get("Specialty / discipline") or {}
    state = by_label.get("State") or {}
    identity_match = npi.get("mark") == "Match" or dob.get("mark") == "Match"
    different = [
        row
        for row in (npi, dob, specialty, state)
        if row.get("mark") == "Different"
    ]
    if identity_match and not different:
        reason = "NPI matches." if npi.get("mark") == "Match" else "Date of birth matches."
        return {"label": "Likely same person", "reason": reason, "next_step": ""}
    if different:
        return {"label": "Likely different person", "reason": _difference_sentence(specialty, state, different), "next_step": ""}
    step = "Check date of birth on the OIG site." if list_name == "leie" else "Check date of birth on the source record."
    return {
        "label": "Needs a closer look",
        "reason": "Only the name matches, and there is no state, license, NPI, or date of birth to compare.",
        "next_step": step,
    }


def _difference_sentence(specialty: dict[str, str], state: dict[str, str], different: list[dict[str, str]]) -> str:
    labels = {str(row.get("label") or "") for row in different}
    parts: list[str] = []
    if "Specialty / discipline" in labels:
        list_job = _shown(specialty, "list_value") or "a different profession"
        our_job = _shown(specialty, "staff_value") or "on our staff"
        list_clause = _article(list_job)
        our_clause = _article(our_job) if our_job != "on our staff" else our_job
        list_state = _shown(state, "list_value")
        our_state = _shown(state, "staff_value")
        if list_state:
            list_clause += " in " + list_state
        if our_state:
            our_clause += " in " + our_state
        parts.append(f"The list entry is {list_clause}; our staff member is {our_clause}.")
    elif "State" in labels:
        parts.append("State differs: the list says " + _shown(state, "list_value") + " and our record says " + _shown(state, "staff_value") + ".")
    if "NPI" in labels:
        parts.append("NPI differs.")
    if "Date of birth" in labels:
        parts.append("Date of birth differs.")
    return " ".join(parts) or "A compared field differs."


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
    refreshed = bool(item.get("list_records")) and _record_has_detail(item)
    rows = comparison_rows(record, staff)
    judged = likelihood_from_rows(rows, list_name=list_name)
    stored_note = str(item.get("list_detail_note") or "")
    return {
        "result_id": str(item.get("id") or ""),
        "list_name": list_name,
        "list_label": _LIST_LABEL.get(list_name, list_name),
        "why": why_flagged(record, staff),
        "likelihood": judged["label"],
        "likelihood_reason": judged["reason"],
        "next_step": judged["next_step"],
        "rows": rows,
        "links": source_links(list_name, record),
        "ssn_note": (
            "OIG can also verify a person by Social Security number on its site. Clynotion does not send a Social Security number."
            if list_name == "leie"
            else ""
        ),
        "detail_note": "" if refreshed else (stored_note or "The full list entry is not stored on this result yet. The match result is unchanged."),
        "alignment": "",
    }


def _hhs_agency(value: str) -> bool:
    text = (value or "").casefold()
    return any(token in text for token in ("hhs", "oig", "health and human", "health & human"))


def _mark_same_hhs_record(lists: list[dict[str, Any]]) -> None:
    leie = next((item for item in lists if item.get("list_name") == "leie"), None)
    sam = next((item for item in lists if item.get("list_name") == "sam"), None)
    if leie is None or sam is None:
        return
    leie_name = next((row for row in leie.get("rows") or [] if row.get("label") == "Full name"), {})
    sam_name = next((row for row in sam.get("rows") or [] if row.get("label") == "Full name"), {})
    agency = next((row for row in sam.get("rows") or [] if row.get("label") == "Excluding agency"), {})
    if not _hhs_agency(str(agency.get("list_value") or "")):
        return
    if norm_token(str(leie_name.get("list_value") or "")) != norm_token(str(sam_name.get("list_value") or "")):
        return
    sam["alignment"] = "Same exclusion reported by HHS/OIG"


def review_token(practice_id: str, person_id: str) -> str:
    import hashlib

    return hashlib.sha256(f"{practice_id}\n{person_id}".encode()).hexdigest()[:20]


def _record_has_detail(item: dict[str, Any]) -> bool:
    records = [row for row in (item.get("list_records") or []) if isinstance(row, dict)]
    if not records:
        return False
    if str(item.get("list_name") or "") != "sam":
        return True
    row = records[0]
    return any(str(row.get(key) or "").strip() for key in ("address", "state", "city", "record_status", "exclusion_date", "agency"))


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
        _mark_same_hhs_record(rendered)
        packets.append(
            {
                "review_id": review_token(practice_id, person_id),
                "name": staff.get("name") or name,
                "header": _header(staff.get("name") or name, lists),
                "lists": rendered,
                "consequences": {
                    "not_the_same_person": "Clears every flagged list for this person. Activate stays blocked until each one is clear.",
                    "confirmed_exclusion": "This person cannot bill Medicaid or Medicare. Activate stays blocked.",
                    "need_more_info": "Keeps this review open. Nothing is cleared and nothing is confirmed.",
                },
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
            href="/#screening?review=" + review_token(practice_id, person_id),
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


def _needs_list_detail(item: dict[str, Any]) -> bool:
    if item.get("list_name") == "sam":
        return not _record_has_detail(item)
    return "list_records" not in item


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
        and _needs_list_detail(item)
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
        list_name = ""
        try:
            list_name = str(item.get("list_name") or "")
            listing = supplied.get(list_name)
            if listing is None and fetcher is not None and today is not None:
                from app.compliance.sources import load_leie, load_medicheck, load_sam_detail

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
                    listing = load_sam_detail(fetcher, first=first, last=last, today=today)
            if listing is None or not listing.ready:
                if list_name == "sam" and listing is not None and listing.detail:
                    compliance_store.set_list_note(practice_id, str(item.get("id") or ""), listing.detail)
                continue
            records = _records_for(item, listing)
            if not records:
                if list_name == "sam":
                    compliance_store.set_list_note(
                        practice_id,
                        str(item.get("id") or ""),
                        "SAM detail was not stored (no_readable_record). The match result is unchanged.",
                    )
                continue
            before = str(item.get("result") or "")
            compliance_store.set_list_records(
                practice_id,
                str(item.get("id") or ""),
                records,
                replace=list_name == "sam",
            )
            after = next(
                (row for row in compliance_store.screenings(practice_id) if row.get("id") == item.get("id")),
                {},
            )
            if str(after.get("result") or "") != before:
                continue
            attached += 1
        except Exception:
            from app.compliance.sources import logger

            logger.warning("reviews_backfill reason=item_failed list=%s", list_name or "unknown")
            continue
    return attached
