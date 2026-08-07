"""Deterministic note rendering from validated fields — never LLM prose for structured values."""

from __future__ import annotations

from app.schemas import EmdrFields, SupervisionFields


def _bullets(items: list[str], indent: str = "  - ") -> str:
    if not items:
        return f"{indent}(none)"
    return "\n".join(f"{indent}{item}" for item in items if item)


def render_supervision_note(fields: SupervisionFields) -> str:
    """Render clinical supervision note from validated fields + confirmed speaker_map."""
    parts: list[str] = []
    fmt = fields.supervision_format.upper()
    header = f"CLINICAL SUPERVISION NOTE ({fmt})"
    if fields.session_date:
        header += f" — {fields.session_date}"
    if fields.duration_minutes is not None:
        header += f" ({fields.duration_minutes} min)"
    parts.append(header)

    supervisor = fields.supervisor or "(unspecified)"
    parts.append(f"Supervisor: {supervisor}")
    if fields.setting:
        parts.append(f"Setting: {fields.setting}")

    if fields.participants:
        plist = []
        for p in fields.participants:
            name = fields.display_name(p.speaker_label)
            plist.append(f"{name} [{p.role}] ({p.speaker_label})")
        parts.append("Participants: " + "; ".join(plist))

    parts.append("Agenda:\n" + _bullets(fields.agenda_items))

    if fields.cases_discussed:
        case_lines = []
        for c in fields.cases_discussed:
            owner = c.supervisee_owner or "n/a"
            focus = c.presenting_focus or "n/a"
            case_lines.append(f"{c.label} (supervisee: {owner}) — {focus}")
        parts.append("Cases discussed:\n" + _bullets(case_lines))
    else:
        parts.append("Cases discussed:\n  - (none)")

    parts.append("Themes:\n" + _bullets(fields.discussion_themes))
    parts.append(f"Guidance given: {fields.guidance_given or 'n/a'}")
    parts.append(f"Supervisee reflections: {fields.supervisee_reflections or 'n/a'}")
    parts.append(f"Competency focus: {fields.competency_focus or 'n/a'}")
    parts.append(f"Risk / ethics: {fields.risk_ethics_flags or 'none noted'}")
    if fields.gatekeeping_notes:
        parts.append(f"Gatekeeping: {fields.gatekeeping_notes}")

    if fields.action_items:
        actions = []
        for a in fields.action_items:
            due = f" (due: {a.due})" if a.due else ""
            owner = a.owner_name or "unassigned"
            actions.append(f"{owner}: {a.item}{due}")
        parts.append("Action items:\n" + _bullets(actions))
    else:
        parts.append("Action items:\n  - (none)")

    parts.append(f"Plan for next supervision: {fields.plan_next or 'n/a'}")
    return "\n".join(parts)


def render_emdr_note(fields: EmdrFields) -> str:
    """Parked EMDR renderer — kept for future modality phase."""
    return (
        f"EMDR PROGRESS NOTE (Phase {fields.phase} — Desensitization)\n"
        f'Target: {fields.target_memory}; image "{fields.image}".\n'
        f'NC: "{fields.negative_cognition}"  PC: "{fields.positive_cognition}"\n'
        f"SUDS {fields.suds_pre}->{fields.suds_post} (/10).  "
        f"VOC {fields.voc_pre}->{fields.voc_post} (/7).\n"
        f"BLS: {fields.bls_type or 'n/a'}. "
        f"Reprocessing: {fields.imagery_shift or 'n/a'}.\n"
        f"Body: {fields.emotions_body or 'n/a'}.\n"
        f"Closure: {fields.closure_method or 'n/a'}. "
        f"Plan: {fields.plan_next_target or 'n/a'}."
    )
