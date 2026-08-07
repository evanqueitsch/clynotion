"""Reconcile extracted speakers with the session 'who's present' roster."""

from __future__ import annotations

import re

from app.clinicians import PresentClinician
from app.notes import render_supervision_note
from app.schemas import Participant, SupervisionFields


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _match_roster_name(candidate: str, present: list[PresentClinician]) -> PresentClinician | None:
    if not candidate or candidate.lower().startswith("speaker"):
        return None
    cn = _norm(candidate)
    for p in present:
        pn = _norm(p.display_name)
        if cn == pn or cn in pn or pn in cn:
            return p
    first = candidate.strip().split()[0].lower() if candidate.strip() else ""
    if first:
        hits = [p for p in present if p.display_name.lower().startswith(first)]
        if len(hits) == 1:
            return hits[0]
    return None


def _speaker_sort_key(label: str) -> tuple[int, str]:
    m = re.search(r"(\d+)", label)
    if m:
        return (int(m.group(1)), label)
    return (999, label)


def reconcile_with_roster(
    fields: SupervisionFields,
    present: list[PresentClinician],
) -> SupervisionFields:
    """
    Merge extraction with declared present clinicians.

    - Prefer extracted names that match the roster.
    - Soft-propose remaining Speaker N → present[N] by declaration order
      (review still required; clinician overrides win).
    - Set supervisor from present roster when needed.
    """
    if not present:
        return fields

    data = fields.model_dump()
    speaker_map = dict(data.get("speaker_map") or {})
    participants = list(data.get("participants") or [])

    for p in participants:
        label = p.get("speaker_label") or ""
        name = speaker_map.get(label) or p.get("name") or ""
        hit = _match_roster_name(name, present)
        if hit:
            speaker_map[label] = hit.display_name
            p["name"] = hit.display_name
            if p.get("role") not in ("supervisor", "supervisee"):
                p["role"] = hit.role

    labels = sorted(
        {
            *(p.get("speaker_label") for p in participants if p.get("speaker_label")),
            *speaker_map.keys(),
        },
        key=_speaker_sort_key,
    )

    assigned_names = {
        n for n in speaker_map.values() if n and not str(n).lower().startswith("speaker")
    }
    unused = [p for p in present if p.display_name not in assigned_names]

    for label in labels:
        mapped = (speaker_map.get(label) or "").strip()
        if mapped and not mapped.lower().startswith("speaker"):
            continue
        if not unused:
            break
        choice = unused.pop(0)
        speaker_map[label] = choice.display_name
        row = next((p for p in participants if p.get("speaker_label") == label), None)
        if row is None:
            participants.append(
                {
                    "speaker_label": label,
                    "name": choice.display_name,
                    "role": choice.role,
                }
            )
        else:
            row["name"] = choice.display_name
            if row.get("role") not in ("supervisor", "supervisee"):
                row["role"] = choice.role

    supervisors = [p for p in present if p.role == "supervisor"]
    if supervisors:
        current = str(data.get("supervisor") or "").strip()
        hit = _match_roster_name(current, present) if current else None
        if hit is not None:
            data["supervisor"] = hit.display_name
        else:
            data["supervisor"] = supervisors[0].display_name

    data["speaker_map"] = speaker_map
    data["participants"] = [Participant.model_validate(p).model_dump() for p in participants]
    data.setdefault("evidence", {})
    data["evidence"]["present_roster"] = (
        "session present: " + ", ".join(f"{p.display_name} ({p.role})" for p in present)
    )
    return SupervisionFields.model_validate(data)


def apply_roster_and_render(
    fields: SupervisionFields,
    present: list[PresentClinician],
) -> tuple[SupervisionFields, str]:
    merged = reconcile_with_roster(fields, present)
    return merged, render_supervision_note(merged)
