"""Master product catalog — business units, items, and regulatory crosswalk.

OPS-/CLY-/FIN- codes are *legacy internal aliases* from the master scope, not
industry-wide compliance codes. User-facing labels use plain item names only.
"""

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Business units (operator-facing organization)
# ---------------------------------------------------------------------------

BUSINESS_UNITS: tuple[dict[str, Any], ...] = (
    {
        "id": "clinical",
        "name": "Clinical Tools",
        "description": "Supervision, compliance, intake, eligibility, and charting ops around SimplePractice.",
        "order": 1,
    },
    {
        "id": "hr",
        "name": "HR",
        "description": "People, time, hiring, and onboarding.",
        "order": 2,
    },
    {
        "id": "marketing",
        "name": "Marketing",
        "description": "Growth, outreach, and public intake surfaces.",
        "order": 3,
    },
    {
        "id": "finance",
        "name": "Finance",
        "description": "Books, claims aging context, and QuickBooks ops.",
        "order": 4,
    },
    {
        "id": "technology",
        "name": "Technology",
        "description": "Platform pipes: ingest, identity, audit, telephony retention.",
        "order": 5,
    },
)


def _auth(*codes: str) -> list[str]:
    return list(codes)


# ---------------------------------------------------------------------------
# Catalog items (tools + obligations). Authorities are the real crosswalk.
# ---------------------------------------------------------------------------

CATALOG_ITEMS: tuple[dict[str, Any], ...] = (
    # --- Clinical Tools (live) ---
    {
        "id": "supervision_notes",
        "name": "Supervision notes",
        "unit": "clinical",
        "kind": "tool",
        "status": "live",
        "href": "/#supervision",
        "description": "Clinical supervision capture, review, and finalize.",
        "legacy_ids": ["CLY-1"],
        "authorities": _auth(
            "HIPAA Security Rule (45 CFR 164.312) — PHI handling for transcription",
        ),
    },
    {
        "id": "compliance_registry",
        "name": "Compliance registry",
        "unit": "clinical",
        "kind": "tool",
        "status": "live",
        "href": "/#comply",
        "description": "Recurring compliance calendar and credentialing clocks.",
        "legacy_ids": ["OPS-2"],
        "authorities": _auth(
            "42 CFR 455.436",
            "42 CFR 455.104",
            "45 CFR 164.308(a)(1)(ii)(A)",
            "45 CFR 164.502(e)",
            "55 Pa. Code 1101.51(e)(1)(iii)",
            "PerformCare Provider Manual Ch. VI",
            "PerformCare CM-MS-003",
            "MA Bulletin 99-89-05",
        ),
    },
    {
        "id": "intake_log",
        "name": "Intake log",
        "unit": "clinical",
        "kind": "tool",
        "status": "live",
        "href": "/#intake",
        "description": "First-contact timestamps and access-standard clocks (case codes only).",
        "legacy_ids": ["OPS-3"],
        "authorities": _auth(
            "PerformCare Provider Manual — routine outpatient access within 7 days of request",
        ),
    },
    {
        "id": "eligibility",
        "name": "Eligibility",
        "unit": "clinical",
        "kind": "tool",
        "status": "live",
        "href": "/#eligibility",
        "description": "Append-only eligibility verification records (mock/manual; live adapters deferred).",
        "legacy_ids": ["OPS-5"],
        "authorities": _auth(
            "Hard Gate — eligibility verified before every service",
            "PA EVS / PROMISe 270–271 (when adapter enabled)",
        ),
    },
    # --- Technology (live) ---
    {
        "id": "sp_ingest",
        "name": "SimplePractice ingest",
        "unit": "technology",
        "kind": "tool",
        "status": "live",
        "href": "/#ingest",
        "description": "Batched SP CSV uploads (documentation reports). No scrape, no SP API.",
        "legacy_ids": ["Platform · Ingest"],
        "authorities": _auth(
            "55 Pa. Code 1101.51(e)(1)(iii) — unsigned note aging (via documentation report)",
        ),
    },
    # --- Planned / not live ---
    {
        "id": "time_pto_bonus",
        "name": "Time, PTO & bonus",
        "unit": "hr",
        "kind": "tool",
        "status": "planned",
        "href": "",
        "description": "Leave, productivity, and bonus computations.",
        "legacy_ids": ["OPS-1"],
        "authorities": _auth("FLSA wage-and-hour recordkeeping (when time drives pay)"),
    },
    {
        "id": "time_entry",
        "name": "Time entry",
        "unit": "hr",
        "kind": "tool",
        "status": "planned",
        "href": "",
        "description": "Non-session hours logged when worked.",
        "legacy_ids": ["OPS-6"],
        "authorities": _auth("FLSA — accurate hours worked for non-exempt employees"),
    },
    {
        "id": "hiring_onboarding",
        "name": "Hiring & onboarding",
        "unit": "hr",
        "kind": "tool",
        "status": "planned",
        "href": "",
        "description": "Candidates through first-day clocks and roster handoff.",
        "legacy_ids": ["OPS-7"],
        "authorities": _auth("42 CFR 455.436 — exclusion screening at hire"),
    },
    {
        "id": "marketing_workbench",
        "name": "Marketing workbench",
        "unit": "marketing",
        "kind": "tool",
        "status": "planned",
        "href": "",
        "description": "Campaign and outreach ops.",
        "legacy_ids": ["OPS-8"],
        "authorities": [],
    },
    {
        "id": "financial_ops",
        "name": "Financial ops",
        "unit": "finance",
        "kind": "tool",
        "status": "planned",
        "href": "",
        "description": "QuickBooks context and operational cash views.",
        "legacy_ids": ["FIN-1"],
        "authorities": _auth("PerformCare Ch. VI — claims aging / filing bar (context)"),
    },
    {
        "id": "telephony_capture",
        "name": "Telephony capture",
        "unit": "technology",
        "kind": "tool",
        "status": "planned",
        "href": "",
        "description": "Persist call events beyond vendor retention ceilings.",
        "legacy_ids": ["OPS-4"],
        "authorities": _auth("MA record retention — four years (beyond vendor 180-day ceilings)"),
    },
)

# Compliance registry clocks (children of compliance_registry) — authority crosswalk
COMPLIANCE_CLOCKS: tuple[dict[str, Any], ...] = (
    {
        "id": "exclusion_screening",
        "code": "o1",
        "name": "Exclusion screening — all staff, contractors, vendors",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 30,
        "owner_role": "admin",
        "authorities": _auth("42 CFR 455.436", "PerformCare Provider Manual Ch. VI"),
    },
    {
        "id": "chart_audit_sample",
        "code": "o2",
        "name": "Chart audit sample — 5 charts per clinician",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 90,
        "owner_role": "coo",
        "authorities": _auth("Compliance program element 6"),
    },
    {
        "id": "compliance_committee",
        "code": "o3",
        "name": "Compliance committee meeting + minutes",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 90,
        "owner_role": "coo",
        "authorities": _auth("Compliance program element 2"),
    },
    {
        "id": "encounter_reconciliation",
        "code": "o4",
        "name": "Encounter form reconciliation against notes",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 30,
        "owner_role": "billing",
        "authorities": _auth("MA Bulletin 99-89-05"),
    },
    {
        "id": "unsigned_note_sweep",
        "code": "o5",
        "name": "Unsigned note sweep — aging over 72 hours",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 7,
        "owner_role": "coo",
        "authorities": _auth("55 Pa. Code 1101.51(e)(1)(iii)"),
    },
    {
        "id": "program_exception_attestation",
        "code": "o6",
        "name": "Program Exception Attestation to Provider Relations",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 365,
        "owner_role": "coo",
        "authorities": _auth("PerformCare Provider Manual Ch. VI — due Jan 1"),
    },
    {
        "id": "annual_compliance_training",
        "code": "o7",
        "name": "Annual compliance + HIPAA training, all staff",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 365,
        "owner_role": "coo",
        "authorities": _auth("Compliance program element 3"),
    },
    {
        "id": "security_risk_analysis",
        "code": "o8",
        "name": "HIPAA Security Risk Analysis review",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 365,
        "owner_role": "coo",
        "authorities": _auth("45 CFR 164.308(a)(1)(ii)(A)"),
    },
    {
        "id": "baa_registry_review",
        "code": "o9",
        "name": "BAA registry review — all PHI vendors",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 365,
        "owner_role": "coo",
        "authorities": _auth("45 CFR 164.502(e)"),
    },
    {
        "id": "ownership_disclosure",
        "code": "o10",
        "name": "Ownership & control disclosure refresh",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 365,
        "owner_role": "ceo",
        "authorities": _auth("42 CFR 455.104"),
    },
    {
        "id": "policy_register_review",
        "code": "o11",
        "name": "Policy register review — versions and owners",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 365,
        "owner_role": "coo",
        "authorities": _auth("Compliance program element 1"),
    },
    {
        "id": "claims_aging_review",
        "code": "o12",
        "name": "Claims aging review — 365-day filing bar",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 30,
        "owner_role": "billing",
        "authorities": _auth("PerformCare Provider Manual Ch. VI", "Admin Appeals"),
    },
    {
        "id": "adjunct_rescreen",
        "code": "o13",
        "name": "Adjunct re-screen at each treatment plan review",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 0,
        "owner_role": "clinician",
        "authorities": _auth("PerformCare CM-MS-003"),
        "notes": "Per plan cycle — not auto-seeded on a fixed cadence.",
    },
    {
        "id": "retention_clock",
        "code": "o14",
        "name": "Retention clock per closed case — destruction-eligible date",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "obligation",
        "cadence_days": 0,
        "owner_role": "admin",
        "authorities": _auth("4-year minimum retention — encounter forms"),
        "notes": "Per episode — not auto-seeded on a fixed cadence.",
    },
)

# Six credential clocks per clinician (Compliance registry tracker)
CREDENTIAL_CLOCKS: tuple[dict[str, Any], ...] = (
    {
        "id": "license_renewal",
        "name": "License renewal",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "credential_clock",
        "cadence_days": 730,
        "authorities": _auth("PA licensing board"),
    },
    {
        "id": "caqh_reattestation",
        "name": "CAQH re-attestation",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "credential_clock",
        "cadence_days": 120,
        "authorities": _auth("CVO pulls from CAQH"),
    },
    {
        "id": "malpractice_rider",
        "name": "Malpractice rider",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "credential_clock",
        "cadence_days": 365,
        "authorities": _auth("Credentialing packet"),
    },
    {
        "id": "recredentialing",
        "name": "Recredentialing",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "credential_clock",
        "cadence_days": 1095,
        "authorities": _auth("PerformCare Provider Manual Ch. VI"),
    },
    {
        "id": "ma_revalidation",
        "name": "MA revalidation",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "credential_clock",
        "cadence_days": 1825,
        "authorities": _auth("ACA — 5-year MA revalidation"),
    },
    {
        "id": "clinician_exclusion_screening",
        "name": "Exclusion screening",
        "parent": "compliance_registry",
        "unit": "clinical",
        "kind": "credential_clock",
        "cadence_days": 30,
        "authorities": _auth("42 CFR 455.436"),
    },
)


def live_tools() -> list[dict[str, Any]]:
    return [i for i in CATALOG_ITEMS if i.get("status") == "live" and i.get("href")]


def catalog_by_unit(*, include_planned: bool = True) -> list[dict[str, Any]]:
    """Home-ready tree: units → tools (and obligation counts for compliance)."""
    items = [
        i
        for i in CATALOG_ITEMS
        if include_planned or i.get("status") == "live"
    ]
    out: list[dict[str, Any]] = []
    for unit in sorted(BUSINESS_UNITS, key=lambda u: u["order"]):
        unit_items = [i for i in items if i["unit"] == unit["id"]]
        if not unit_items:
            continue
        tools = []
        for item in unit_items:
            row = {
                "id": item["id"],
                "name": item["name"],
                "href": item.get("href") or "",
                "description": item.get("description") or "",
                "status": item.get("status") or "planned",
                "legacy_ids": list(item.get("legacy_ids") or []),
                "authorities": list(item.get("authorities") or []),
            }
            if item["id"] == "compliance_registry":
                row["obligations"] = [
                    {
                        "id": c["id"],
                        "code": c["code"],
                        "name": c["name"],
                        "cadence_days": c["cadence_days"],
                        "owner_role": c["owner_role"],
                        "authorities": list(c["authorities"]),
                    }
                    for c in COMPLIANCE_CLOCKS
                    if c.get("cadence_days", 0) > 0
                ]
                row["credential_clocks"] = [
                    {
                        "id": c["id"],
                        "name": c["name"],
                        "cadence_days": c["cadence_days"],
                        "authorities": list(c["authorities"]),
                    }
                    for c in CREDENTIAL_CLOCKS
                ]
            tools.append(row)
        out.append(
            {
                "id": unit["id"],
                "name": unit["name"],
                "description": unit["description"],
                "tools": tools,
            }
        )
    return out


def crosswalk_rows() -> list[dict[str, Any]]:
    """Flat master list: every item/obligation → authorities (+ legacy id)."""
    rows: list[dict[str, Any]] = []
    unit_names = {u["id"]: u["name"] for u in BUSINESS_UNITS}
    for item in CATALOG_ITEMS:
        rows.append(
            {
                "id": item["id"],
                "name": item["name"],
                "unit": unit_names.get(item["unit"], item["unit"]),
                "kind": item.get("kind") or "tool",
                "status": item.get("status") or "",
                "legacy_ids": list(item.get("legacy_ids") or []),
                "authorities": list(item.get("authorities") or []),
            }
        )
    for clock in COMPLIANCE_CLOCKS:
        rows.append(
            {
                "id": clock["id"],
                "name": clock["name"],
                "unit": unit_names.get(clock["unit"], clock["unit"]),
                "kind": "obligation",
                "status": "live" if clock.get("cadence_days", 0) > 0 else "manual",
                "legacy_ids": [clock["code"], "OPS-2"],
                "authorities": list(clock["authorities"]),
                "parent": clock.get("parent"),
            }
        )
    for clock in CREDENTIAL_CLOCKS:
        rows.append(
            {
                "id": clock["id"],
                "name": clock["name"],
                "unit": unit_names.get(clock["unit"], clock["unit"]),
                "kind": "credential_clock",
                "status": "live",
                "legacy_ids": ["OPS-2", "credential_tracker"],
                "authorities": list(clock["authorities"]),
                "parent": clock.get("parent"),
                "cadence_days": clock["cadence_days"],
            }
        )
    return rows


def clock_by_code(code: str) -> Optional[dict[str, Any]]:
    for c in COMPLIANCE_CLOCKS:
        if c["code"] == code:
            return c
    return None
