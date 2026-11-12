"""Canonical US state and phone values for the employee contact form.

Known states are stored as postal abbreviations. 10-digit US numbers are stored
as ``(###)###-####`` with no space after the area code. A blank value stays
blank. A value this module does not recognize is kept only when it is already
the stored value, so a save does not drop legacy data.
"""

from __future__ import annotations

US_STATES: tuple[tuple[str, str], ...] = (
    ("AL", "Alabama"),
    ("AK", "Alaska"),
    ("AZ", "Arizona"),
    ("AR", "Arkansas"),
    ("CA", "California"),
    ("CO", "Colorado"),
    ("CT", "Connecticut"),
    ("DE", "Delaware"),
    ("DC", "District of Columbia"),
    ("FL", "Florida"),
    ("GA", "Georgia"),
    ("HI", "Hawaii"),
    ("ID", "Idaho"),
    ("IL", "Illinois"),
    ("IN", "Indiana"),
    ("IA", "Iowa"),
    ("KS", "Kansas"),
    ("KY", "Kentucky"),
    ("LA", "Louisiana"),
    ("ME", "Maine"),
    ("MD", "Maryland"),
    ("MA", "Massachusetts"),
    ("MI", "Michigan"),
    ("MN", "Minnesota"),
    ("MS", "Mississippi"),
    ("MO", "Missouri"),
    ("MT", "Montana"),
    ("NE", "Nebraska"),
    ("NV", "Nevada"),
    ("NH", "New Hampshire"),
    ("NJ", "New Jersey"),
    ("NM", "New Mexico"),
    ("NY", "New York"),
    ("NC", "North Carolina"),
    ("ND", "North Dakota"),
    ("OH", "Ohio"),
    ("OK", "Oklahoma"),
    ("OR", "Oregon"),
    ("PA", "Pennsylvania"),
    ("RI", "Rhode Island"),
    ("SC", "South Carolina"),
    ("SD", "South Dakota"),
    ("TN", "Tennessee"),
    ("TX", "Texas"),
    ("UT", "Utah"),
    ("VT", "Vermont"),
    ("VA", "Virginia"),
    ("WA", "Washington"),
    ("WV", "West Virginia"),
    ("WI", "Wisconsin"),
    ("WY", "Wyoming"),
)

PHONE_FIELDS: tuple[tuple[str, str], ...] = (
    ("personal_phone", "Personal phone"),
    ("work_phone", "Work phone"),
    ("emergency_phone", "Emergency contact phone"),
)

PHONE_EXAMPLE = "(555)010-0100"
PHONE_HINT = f"must be a 10-digit US number like {PHONE_EXAMPLE}, or leave it blank."
STATE_ERROR = "Choose a state or leave State blank."

_BY_ABBR = {abbr.casefold(): abbr for abbr, _name in US_STATES}
_BY_NAME = {name.casefold(): abbr for abbr, name in US_STATES}


def phone_error(label: str) -> str:
    return f"{label} {PHONE_HINT}"


def canonical_state(value: str) -> str | None:
    """Return an abbreviation, ``""`` when blank, or ``None`` when unknown."""
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    folded = text.casefold().rstrip(".")
    found = _BY_NAME.get(folded)
    if found:
        return found
    letters = "".join(ch for ch in folded if ch.isalpha())
    if len(letters) == 2:
        return _BY_ABBR.get(letters)
    return None


def parse_us_phone(value: str) -> str | None:
    """Return 10 digits, ``""`` when blank, or ``None`` when not a US number."""
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return digits
    return None


def format_us_phone(digits: str) -> str:
    return f"({digits[:3]}){digits[3:6]}-{digits[6:]}"


def canonical_phone(value: str) -> str | None:
    """Return ``(###)###-####``, ``""`` when blank, or ``None`` when not 10 digits."""
    parsed = parse_us_phone(value)
    if parsed is None:
        return None
    if parsed == "":
        return ""
    return format_us_phone(parsed)


def normalize_state(value: str, *, previous: str) -> str:
    canonical = canonical_state(value)
    if canonical is not None:
        return canonical
    stored = str(previous or "")
    if value.strip().casefold() == stored.strip().casefold():
        return stored
    raise ValueError(STATE_ERROR)


def normalize_phone(value: str, *, previous: str, label: str) -> str:
    canonical = canonical_phone(value)
    if canonical is not None:
        return canonical
    stored = str(previous or "")
    if value.strip() == stored.strip():
        return stored
    raise ValueError(phone_error(label))


def normalize_contact(fields: dict[str, str], *, previous: dict[str, str]) -> dict[str, str]:
    """Normalize state and the three phone fields. Other keys pass through."""
    cleaned = {key: str(value or "") for key, value in fields.items()}
    prior = previous or {}
    errors: list[str] = []
    try:
        cleaned["state"] = normalize_state(
            cleaned.get("state", ""),
            previous=str(prior.get("state") or ""),
        )
    except ValueError as exc:
        errors.append(str(exc))
    for field, label in PHONE_FIELDS:
        try:
            cleaned[field] = normalize_phone(
                cleaned.get(field, ""),
                previous=str(prior.get(field) or ""),
                label=label,
            )
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError(" ".join(errors))
    return cleaned
