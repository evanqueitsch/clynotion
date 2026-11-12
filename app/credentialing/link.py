"""Link duplicate payers into one record.

The preview is computed and returned. Nothing is written until the store
applies a ready plan. Blank values never replace a stored value unless the
caller chooses that blank.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.credentialing.csvio import append_howto, normalize_payer_name, union_labels

_SKIP_ENROLLMENT = {
    "subject_id",
    "payer_id",
    "history",
    "updated_by",
    "updated_at",
    "id",
}
_LIST_FIELDS = {"accepts_licenses", "family_members", "counties"}
_DAY_FIELDS = {"follow_up_days", "recredential_days"}


class _Clear:
    """A chosen blank. Distinct from 'no choice'."""


CLEAR = _Clear()


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str
    allow_combine: bool = False
    allow_blank: bool = True


PAYER_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("type", "Type", "text", allow_blank=False),
    FieldSpec("electronic_payer_id", "Electronic payer ID", "text"),
    FieldSpec("practice_network", "Practice network", "network"),
    FieldSpec("practice_network_note", "Network note", "text"),
    FieldSpec("notes", "How-to", "note", allow_combine=True),
    FieldSpec("accepts_licenses", "Licenses", "list", allow_combine=True),
    FieldSpec("family_members", "Family members", "list", allow_combine=True),
    FieldSpec("parent_payer", "Parent payer", "text"),
    FieldSpec("counties", "Counties", "list", allow_combine=True),
    FieldSpec("credentials_expire", "Credentials expire", "bool"),
    FieldSpec("follow_up_days", "Follow-up days", "days"),
    FieldSpec("recredential_days", "Recredential days", "days"),
)

ENROLLMENT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("status", "Status", "status"),
    FieldSpec("submitted_on", "Submitted", "date"),
    FieldSpec("effective_on", "Effective", "date"),
    FieldSpec("expires_on", "Expiration", "date"),
    FieldSpec("next_follow_up", "Follow-up", "date"),
    FieldSpec("has_expiration", "Has expiration", "bool"),
    FieldSpec("note", "Note", "note", allow_combine=True),
    FieldSpec("payer_provider_id", "Payer provider ID", "secret"),
)

_NETWORK_LABELS = {
    "in_network": "In network",
    "needs_ma_first": "Need MA first",
    "in_progress": "In progress",
    "unknown": "Unknown",
}
_STATUS_LABELS = {
    "pending": "Pending",
    "in_network": "In network",
    "not_eligible": "Not eligible",
    "needs_review": "Needs review",
    "unknown": "Unknown",
    "submitted": "Submitted",
}


def _mask(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    last = raw[-4:] if len(raw) > 4 else raw
    return "•••••• " + last


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _days(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _empty_key(spec: FieldSpec, value: Any) -> bool:
    return _compare_key(spec, value) is None


def _compare_key(spec: FieldSpec, value: Any) -> Any:
    if spec.kind == "list":
        items = _list(value)
        if not items:
            return None
        return tuple(sorted(item.casefold() for item in items))
    if spec.kind == "bool":
        if isinstance(value, bool):
            return value
        return None
    if spec.kind == "days":
        return _days(value)
    if spec.kind == "network":
        text = str(value or "").strip().lower()
        if text in {"", "unknown"}:
            return None
        return text
    if spec.kind == "status":
        text = str(value or "").strip().lower()
        if text in {"", "unknown"}:
            return None
        return text
    if spec.kind == "date":
        text = str(value or "").strip()
        return text or None
    text = _text(value)
    return text or None


def _original(spec: FieldSpec, value: Any) -> Any:
    """Value to store when this side is chosen. Empty becomes CLEAR."""
    if _empty_key(spec, value):
        return CLEAR
    if spec.kind == "list":
        return _list(value)
    if spec.kind == "bool":
        return bool(value)
    if spec.kind == "days":
        return _days(value)
    if spec.kind == "network":
        return str(value or "").strip().lower()
    if spec.kind == "status":
        return str(value or "").strip().lower()
    if spec.kind == "date":
        return str(value or "").strip()
    if spec.kind == "secret":
        return str(value or "").strip()
    return str(value or "").strip()


def _display(spec: FieldSpec, value: Any) -> str:
    if value is CLEAR or _empty_key(spec, value):
        return "(blank)"
    if spec.kind == "list":
        return ", ".join(_list(value))
    if spec.kind == "bool":
        return "Yes" if value else "No"
    if spec.kind == "days":
        number = _days(value)
        return "" if number is None else str(number)
    if spec.kind == "network":
        text = str(value or "").strip().lower()
        return _NETWORK_LABELS.get(text, text)
    if spec.kind == "status":
        text = str(value or "").strip().lower()
        return _STATUS_LABELS.get(text, text)
    if spec.kind == "date":
        text = str(value or "").strip()
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return f"{text[5:7]}/{text[8:10]}/{text[0:4]}"
        return text
    if spec.kind == "secret":
        return _mask(str(value or ""))
    return str(value or "").strip()


def _audit_value(spec: FieldSpec, value: Any) -> Any:
    if spec.kind == "secret":
        if value is CLEAR or _empty_key(spec, value):
            return "blank"
        return "kept"
    if value is CLEAR:
        return ""
    if spec.kind == "list":
        return _list(value)
    if spec.kind == "bool":
        return bool(value) if isinstance(value, bool) else None
    if spec.kind == "days":
        return _days(value)
    return str(value or "").strip()


def _read_choice(raw: Any) -> tuple[str, bool, bool, Optional[str]]:
    if not isinstance(raw, dict):
        return "", False, False, None
    source = str(raw.get("source_id") or "").strip()
    combine = bool(raw.get("combine"))
    blank = bool(raw.get("blank"))
    picked = int(bool(source)) + int(combine) + int(blank)
    if picked > 1:
        return source, combine, blank, "Choose one option for each field."
    return source, combine, blank, None


def _selected_token(source: str, combine: bool, blank: bool) -> str:
    if blank:
        return "blank"
    if combine:
        return "combine"
    if source:
        return "source:" + source
    return ""


def _payer_rows(row: dict[str, Any], payer_ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if len(payer_ids) < 2:
        errors.append("Select two or more payers.")
    if len(payer_ids) != len(set(payer_ids)):
        errors.append("Each payer can be selected once.")
    payers: list[dict[str, Any]] = []
    bucket = row.get("payers") if isinstance(row.get("payers"), dict) else {}
    for payer_id in payer_ids:
        current = bucket.get(payer_id)
        if not isinstance(current, dict):
            errors.append("Payer not found.")
            continue
        if current.get("archived"):
            errors.append(f"{current.get('name') or 'That payer'} is archived. Restore it before linking.")
            continue
        payers.append(current)
    return payers, errors


def _outside_names(row: dict[str, Any], selected: set[str]) -> dict[str, str]:
    taken: dict[str, str] = {}
    bucket = row.get("payers") if isinstance(row.get("payers"), dict) else {}
    for payer in bucket.values():
        if not isinstance(payer, dict) or payer.get("archived"):
            continue
        if str(payer.get("id") or "") in selected:
            continue
        labels = [str(payer.get("name") or "")]
        labels.extend(str(item) for item in (payer.get("aliases") or []))
        for label in labels:
            key = normalize_payer_name(label)
            if key and key not in taken:
                taken[key] = str(payer.get("name") or label).strip()
    return taken


def _aliases(payers: list[dict[str, Any]], keep_id: str) -> list[str]:
    keep = next(payer for payer in payers if str(payer.get("id") or "") == keep_id)
    seen = set()
    canonical = normalize_payer_name(str(keep.get("name") or ""))
    if canonical:
        seen.add(canonical)
    ordered: list[str] = []
    others = [payer for payer in payers if str(payer.get("id") or "") != keep_id]
    for payer in others:
        ordered.append(str(payer.get("name") or ""))
    for payer in [keep, *others]:
        for alias in payer.get("aliases") or []:
            ordered.append(str(alias or ""))
    out: list[str] = []
    for label in ordered:
        text = str(label or "").strip()
        key = normalize_payer_name(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _spec_value(record: dict[str, Any], spec: FieldSpec) -> Any:
    return record.get(spec.key)


def _combine(spec: FieldSpec, members: list[tuple[str, Any]], keep_id: str) -> Any:
    ordered = sorted(members, key=lambda item: (0 if item[0] == keep_id else 1, members.index(item)))
    if spec.kind == "list":
        merged: list[str] = []
        for _payer_id, raw in ordered:
            merged = union_labels(merged, _list(raw))
        return merged
    if spec.kind == "note":
        text = ""
        for _payer_id, raw in ordered:
            text = append_howto(text, str(raw or ""))
        return text
    return _original(spec, ordered[0][1])


def _groups(spec: FieldSpec, records: list[tuple[str, str, dict[str, Any]]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for payer_id, payer_name, record in records:
        raw = _spec_value(record, spec)
        key = _compare_key(spec, raw)
        if key is None:
            continue
        found = next((group for group in groups if group["key"] == key), None)
        if found is None:
            groups.append(
                {
                    "key": key,
                    "value": _original(spec, raw),
                    "payer_ids": [payer_id],
                    "payer_names": [payer_name],
                }
            )
            continue
        found["payer_ids"].append(payer_id)
        found["payer_names"].append(payer_name)
    return groups


def _resolve_field(
    spec: FieldSpec,
    records: list[tuple[str, str, dict[str, Any]]],
    choice: Any,
    *,
    keep_id: str,
    selected_ids: set[str],
) -> dict[str, Any]:
    """Return a public field row plus the stored value when one is resolved."""
    source, combine, blank, choice_error = _read_choice(choice)
    groups = _groups(spec, records)
    selected = _selected_token(source, combine, blank)
    row: dict[str, Any] = {
        "field": spec.key,
        "label": spec.label,
        "status": "kept",
        "allow_combine": spec.allow_combine,
        "allow_blank": spec.allow_blank,
        "display": "",
        "selected": "",
        "options": [],
        "error": choice_error or "",
    }
    stored: Any = None
    resolved = False
    if not groups and not (blank or combine or source):
        return {"public": None, "stored": None, "resolved": True, "auto": True}

    def option(value: str, label: str, display: str) -> dict[str, str]:
        return {"value": value, "label": label, "display": display}

    if len(groups) >= 2:
        for group in groups:
            row["options"].append(
                option(
                    "source:" + group["payer_ids"][0],
                    " and ".join(group["payer_names"]),
                    _display(spec, group["value"]),
                )
            )
    if spec.allow_combine and len(groups) >= 2:
        members = []
        for payer_id, _name, record in records:
            raw = _spec_value(record, spec)
            if _compare_key(spec, raw) is not None:
                members.append((payer_id, raw))
        combined = _combine(spec, members, keep_id)
        row["options"].append(option("combine", "Combine", _display(spec, combined)))
    if spec.allow_blank and (groups or source or blank):
        row["options"].append(option("blank", "Clear value", "(blank)"))

    if choice_error:
        row["status"] = "conflict" if len(groups) >= 2 else "kept"
        return {"public": row if groups or selected else None, "stored": None, "resolved": False, "auto": False}

    if blank:
        if not spec.allow_blank:
            row["error"] = f"Choose a {spec.label.lower()}."
            row["status"] = "conflict"
            return {"public": row, "stored": None, "resolved": False, "auto": False}
        stored = CLEAR
        resolved = True
        row["status"] = "chosen"
        row["selected"] = "blank"
        row["display"] = "(blank)"
    elif combine:
        if not spec.allow_combine:
            row["error"] = f"{spec.label} cannot be combined."
            row["status"] = "conflict"
            return {"public": row, "stored": None, "resolved": False, "auto": False}
        members = []
        for payer_id, _name, record in records:
            raw = _spec_value(record, spec)
            if _compare_key(spec, raw) is not None:
                members.append((payer_id, raw))
        stored = _combine(spec, members, keep_id) if members else CLEAR
        resolved = True
        row["status"] = "chosen"
        row["selected"] = "combine"
        row["display"] = _display(spec, stored)
    elif source:
        if source not in selected_ids:
            row["error"] = f"Choose a listed value for {spec.label}."
            row["status"] = "conflict" if len(groups) >= 2 else "kept"
            return {"public": row, "stored": None, "resolved": False, "auto": False}
        match = next((record for payer_id, _name, record in records if payer_id == source), None)
        raw = _spec_value(match, spec) if match else None
        stored = _original(spec, raw)
        if spec.key == "type" and stored is CLEAR:
            row["error"] = "Choose a type."
            row["status"] = "conflict"
            return {"public": row, "stored": None, "resolved": False, "auto": False}
        resolved = True
        row["status"] = "chosen"
        row["selected"] = "source:" + source
        row["display"] = _display(spec, stored)
    elif len(groups) == 1:
        winner = groups[0]
        if keep_id in winner["payer_ids"]:
            match = next(record for payer_id, _name, record in records if payer_id == keep_id)
            stored = _original(spec, _spec_value(match, spec))
        else:
            stored = winner["value"]
        resolved = True
        row["status"] = "kept"
        row["display"] = _display(spec, stored)
    elif len(groups) >= 2:
        row["status"] = "conflict"
        resolved = False
    else:
        return {"public": None, "stored": None, "resolved": True, "auto": True}

    if row["status"] == "conflict":
        row["display"] = ""
    visible = row if groups or selected else None
    if visible is None:
        return {"public": None, "stored": stored, "resolved": resolved, "auto": not selected}
    return {"public": visible, "stored": stored if resolved else None, "resolved": resolved, "auto": row["status"] == "kept"}


def _enrollment_specs(records: list[dict[str, Any]]) -> list[FieldSpec]:
    specs = list(ENROLLMENT_FIELDS)
    known = {spec.key for spec in specs}
    extra: list[str] = []
    for record in records:
        for key in record:
            if key in _SKIP_ENROLLMENT or key in known or key in extra:
                continue
            extra.append(str(key))
    for key in extra:
        label = key.replace("_", " ").strip().capitalize() or key
        specs.append(FieldSpec(key, label, "text"))
    return specs


def _participates(enrollment: dict[str, Any], specs: list[FieldSpec]) -> bool:
    for spec in specs:
        if _compare_key(spec, enrollment.get(spec.key)) is not None:
            return True
    return False


def _load_enrollments(
    row: dict[str, Any], payers: list[dict[str, Any]]
) -> dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]:
    bucket = row.get("enrollments") if isinstance(row.get("enrollments"), dict) else {}
    found: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for payer in payers:
        payer_id = str(payer.get("id") or "")
        for key, enrollment in bucket.items():
            if not isinstance(enrollment, dict):
                continue
            if str(enrollment.get("payer_id") or "") != payer_id and not str(key).endswith("|" + payer_id):
                continue
            subject_id = str(enrollment.get("subject_id") or str(key).split("|", 1)[0])
            if not subject_id:
                continue
            found.setdefault(subject_id, []).append((payer, enrollment))
    return found


def _subject_name(row: dict[str, Any], subject_id: str) -> str:
    subjects = row.get("subjects") if isinstance(row.get("subjects"), dict) else {}
    subject = subjects.get(subject_id)
    if isinstance(subject, dict) and str(subject.get("name") or "").strip():
        return str(subject.get("name") or "").strip()
    return subject_id


def build_link_plan(row: dict[str, Any], body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(preview, resolved)``. ``resolved`` stays on the server."""
    raw_ids = body.get("payer_ids") or []
    payer_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
    payers, errors = _payer_rows(row, payer_ids)
    keep_id = str(body.get("keep_id") or "").strip()
    choices = body.get("choices") if isinstance(body.get("choices"), dict) else {}
    enrollment_choices = (
        body.get("enrollment_choices") if isinstance(body.get("enrollment_choices"), dict) else {}
    )
    unresolved: list[str] = []
    if errors:
        preview = {
            "ready": False,
            "errors": errors,
            "unresolved": [],
            "payer_ids": payer_ids,
            "names": [],
            "keep_id": "",
            "keep_name": "",
            "aliases": [],
            "fields": [],
            "enrollment_moves": [],
            "enrollment_conflicts": [],
        }
        return preview, {}

    selected_ids = {str(payer.get("id") or "") for payer in payers}
    names = [{"id": str(payer.get("id") or ""), "name": str(payer.get("name") or "")} for payer in payers]
    if keep_id and keep_id not in selected_ids:
        errors.append("Choose a name from the selected payers.")
        keep_id = ""
    if not keep_id:
        unresolved.append("Name to keep")

    taken = _outside_names(row, selected_ids)
    keep_name = ""
    aliases: list[str] = []
    if keep_id:
        keep = next(payer for payer in payers if str(payer.get("id") or "") == keep_id)
        keep_name = str(keep.get("name") or "")
        aliases = _aliases(payers, keep_id)
        for label in [keep_name, *aliases]:
            owner = taken.get(normalize_payer_name(label))
            if owner:
                errors.append(f"{label} is already an active payer ({owner}). It is not part of this link.")

    records = [(str(payer.get("id") or ""), str(payer.get("name") or ""), payer) for payer in payers]
    fields: list[dict[str, Any]] = []
    payer_stored: dict[str, Any] = {}
    field_audit: dict[str, Any] = {}
    for spec in PAYER_FIELDS:
        result = _resolve_field(
            spec,
            records,
            choices.get(spec.key),
            keep_id=keep_id,
            selected_ids=selected_ids,
        )
        if result["public"] is not None:
            fields.append(result["public"])
        if result.get("error") or (result["public"] and result["public"].get("error")):
            message = (result["public"] or {}).get("error") or ""
            if message and message not in errors:
                errors.append(message)
        if not result["resolved"]:
            unresolved.append(spec.label)
            continue
        if result["stored"] is not None:
            payer_stored[spec.key] = result["stored"]
            field_audit[spec.key] = {
                "auto": bool(result["auto"]),
                "selected": (result["public"] or {}).get("selected") or "",
                "value": _audit_value(spec, result["stored"]),
            }

    moves: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    resolved_enrollments: list[dict[str, Any]] = []
    by_subject = _load_enrollments(row, payers)
    for subject_id, pairs in by_subject.items():
        specs = _enrollment_specs([enrollment for _payer, enrollment in pairs])
        participating = [(payer, enrollment) for payer, enrollment in pairs if _participates(enrollment, specs)]
        if not participating:
            continue
        subject_name = _subject_name(row, subject_id)
        subject_choices = enrollment_choices.get(subject_id)
        if not isinstance(subject_choices, dict):
            subject_choices = {}
        subject_records = [
            (str(payer.get("id") or ""), str(payer.get("name") or ""), enrollment)
            for payer, enrollment in participating
        ]
        subject_fields: list[dict[str, Any]] = []
        values: dict[str, Any] = {}
        choice_audit: dict[str, Any] = {}
        subject_unresolved = False
        has_conflict = False
        for spec in specs:
            result = _resolve_field(
                spec,
                subject_records,
                subject_choices.get(spec.key),
                keep_id=keep_id,
                selected_ids=selected_ids,
            )
            public_field = result["public"]
            if public_field is not None:
                subject_fields.append(public_field)
                if public_field.get("status") == "conflict":
                    has_conflict = True
                if public_field.get("error") and public_field["error"] not in errors:
                    errors.append(public_field["error"])
            if not result["resolved"]:
                subject_unresolved = True
                unresolved.append(f"{subject_name} · {spec.label}")
                continue
            if result["stored"] is not None:
                values[spec.key] = result["stored"]
                choice_audit[spec.key] = {
                    "auto": bool(result["auto"]),
                    "selected": (public_field or {}).get("selected") or "",
                    "value": _audit_value(spec, result["stored"]),
                }
        from_ids = [str(payer.get("id") or "") for payer, _enrollment in participating]
        from_names = [str(payer.get("name") or "") for payer, _enrollment in participating]
        destination = keep_name or "the payer you keep"
        if len(participating) == 1 and from_ids[0] == keep_id:
            summary = f"Stays on {destination}."
        elif len(participating) == 1:
            summary = f"Moves from {from_names[0]} onto {destination}."
        else:
            summary = f"On {' and '.join(from_names)}. Becomes one cell on {destination}."
        if subject_unresolved:
            summary += " Choose the conflicting values."
        leaves_a_payer = (not keep_id) or any(payer_id != keep_id for payer_id in from_ids)
        if leaves_a_payer or len(participating) > 1:
            moves.append(
                {
                    "subject_id": subject_id,
                    "subject_name": subject_name,
                    "from_payer_ids": from_ids,
                    "from_names": from_names,
                    "summary": summary,
                }
            )
        if len(participating) > 1 or has_conflict:
            conflicts.append(
                {
                    "subject_id": subject_id,
                    "subject_name": subject_name,
                    "fields": subject_fields,
                }
            )
        history: list[Any] = []
        for _payer, enrollment in participating:
            for event in enrollment.get("history") or []:
                if isinstance(event, dict):
                    history.append(event)
        resolved_enrollments.append(
            {
                "subject_id": subject_id,
                "subject_name": subject_name,
                "from_payer_ids": from_ids,
                "values": values,
                "history": history,
                "choices": choice_audit,
            }
        )

    ready = not errors and not unresolved and bool(keep_id)
    preview = {
        "ready": ready,
        "errors": errors,
        "unresolved": unresolved,
        "payer_ids": [str(payer.get("id") or "") for payer in payers],
        "names": names,
        "keep_id": keep_id,
        "keep_name": keep_name,
        "aliases": aliases,
        "fields": fields,
        "enrollment_moves": moves,
        "enrollment_conflicts": conflicts,
    }
    resolved = {
        "keep_id": keep_id,
        "keep_name": keep_name,
        "aliases": aliases,
        "payer_ids": preview["payer_ids"],
        "payer_fields": payer_stored,
        "field_audit": field_audit,
        "enrollments": resolved_enrollments,
        "archived_ids": [payer_id for payer_id in preview["payer_ids"] if payer_id != keep_id],
        "names": {item["id"]: item["name"] for item in names},
    }
    return preview, resolved


def _apply_payer_value(payer: dict[str, Any], field: str, value: Any) -> None:
    if value is CLEAR:
        if field == "credentials_expire":
            payer.pop("credentials_expire", None)
        elif field in _DAY_FIELDS:
            payer.pop(field, None)
        elif field == "practice_network":
            payer["practice_network"] = "unknown"
        elif field in _LIST_FIELDS:
            payer[field] = []
        else:
            payer[field] = ""
        return
    if field in _LIST_FIELDS:
        payer[field] = _list(value)
        return
    if field in _DAY_FIELDS:
        number = _days(value)
        if number is None:
            payer.pop(field, None)
        else:
            payer[field] = number
        return
    if field == "credentials_expire":
        if isinstance(value, bool):
            payer[field] = value
        else:
            payer.pop(field, None)
        return
    payer[field] = value


def _apply_enrollment_value(enrollment: dict[str, Any], field: str, value: Any) -> None:
    if value is CLEAR:
        if field == "has_expiration":
            enrollment.pop("has_expiration", None)
        elif field == "status":
            enrollment["status"] = "unknown"
        else:
            enrollment[field] = ""
        return
    enrollment[field] = value


def apply_payer_link(row: dict[str, Any], resolved: dict[str, Any], *, actor: str, when: str) -> None:
    """Write a ready plan onto ``row``. Caller saves the file."""
    keep_id = str(resolved.get("keep_id") or "")
    payers = row.setdefault("payers", {})
    kept = payers.get(keep_id)
    if not isinstance(kept, dict):
        raise ValueError("Payer not found.")
    kept["name"] = str(resolved.get("keep_name") or kept.get("name") or "")
    kept["aliases"] = list(resolved.get("aliases") or [])
    for field, value in (resolved.get("payer_fields") or {}).items():
        _apply_payer_value(kept, str(field), value)
    kept["archived"] = False
    kept.pop("merged_into", None)
    kept["updated_by"] = actor
    kept["updated_at"] = when

    enrollments = row.setdefault("enrollments", {})
    archived_ids = [str(item) for item in (resolved.get("archived_ids") or [])]
    for item in resolved.get("enrollments") or []:
        subject_id = str(item.get("subject_id") or "")
        key = f"{subject_id}|{keep_id}"
        current: dict[str, Any] = {
            "subject_id": subject_id,
            "payer_id": keep_id,
            "status": "unknown",
            "submitted_on": "",
            "effective_on": "",
            "expires_on": "",
            "next_follow_up": "",
            "note": "",
            "payer_provider_id": "",
            "history": list(item.get("history") or []),
        }
        for field, value in (item.get("values") or {}).items():
            _apply_enrollment_value(current, str(field), value)
        current["updated_by"] = actor
        current["updated_at"] = when
        current.setdefault("history", []).append(
            {
                "at": when,
                "by": actor,
                "source": "link",
                "changes": {
                    "linked_from": {"new": ",".join(item.get("from_payer_ids") or [])},
                },
            }
        )
        enrollments[key] = current

    for source_id in archived_ids:
        source = payers.get(source_id)
        if isinstance(source, dict):
            source["archived"] = True
            source["merged_into"] = keep_id
            source["updated_by"] = actor
            source["updated_at"] = when
        for key, enrollment in list(enrollments.items()):
            if not isinstance(enrollment, dict):
                continue
            if str(enrollment.get("payer_id") or "") == source_id or str(key).endswith("|" + source_id):
                del enrollments[key]

    names = resolved.get("names") if isinstance(resolved.get("names"), dict) else {}
    row.setdefault("link_audit", []).append(
        {
            "at": when,
            "actor": (actor or "").strip().lower(),
            "action": "link_payers",
            "kept_payer_id": keep_id,
            "kept_name": str(resolved.get("keep_name") or ""),
            "source_ids": list(resolved.get("payer_ids") or []),
            "source_names": [str(names.get(payer_id) or payer_id) for payer_id in (resolved.get("payer_ids") or [])],
            "linked_payer_ids": archived_ids,
            "aliases_added": list(resolved.get("aliases") or []),
            "fields": resolved.get("field_audit") or {},
            "enrollments": [
                {
                    "subject_id": item.get("subject_id") or "",
                    "subject_name": item.get("subject_name") or "",
                    "from_payer_ids": list(item.get("from_payer_ids") or []),
                    "choices": item.get("choices") or {},
                }
                for item in (resolved.get("enrollments") or [])
            ],
        }
    )
