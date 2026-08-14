# Clynotion — item ↔ authority crosswalk

**Purpose:** One master list of product items, organized by business unit, with the
regulatory / industry authorities each item implements or tracks.

**Naming:** Plain item names only in the product UI. Codes like `OPS-2`, `CLY-1`,
`FIN-1`, and `o1`…`o14` are **legacy internal aliases** from the original scope
document — not industry-wide compliance codes. The authorities column is the
real crosswalk.

Source of truth in code: `app/platform/catalog.py` (`GET /catalog`, `GET /catalog/crosswalk`).

---

## Business units

| Unit | What belongs here |
|---|---|
| **Clinical Tools** | Supervision notes, compliance registry, intake log, eligibility |
| **HR** | Time/PTO/bonus, time entry, hiring & onboarding |
| **Marketing** | Marketing workbench (and later public growth tools) |
| **Finance** | Financial ops / QuickBooks context |
| **Technology** | SimplePractice ingest, telephony capture, platform pipes |

---

## Tools

| Item | Unit | Status | Legacy alias | Authorities |
|---|---|---|---|---|
| Supervision notes | Clinical Tools | live | CLY-1 | HIPAA Security Rule (45 CFR 164.312) — PHI path for transcription |
| Compliance registry | Clinical Tools | live | OPS-2 | See obligation rows below |
| Intake log | Clinical Tools | live | OPS-3 | PerformCare — routine OP access within 7 days of request |
| Eligibility | Clinical Tools | live | OPS-5 | Hard gate — eligibility before every service; PA EVS/PROMISe when enabled |
| SimplePractice ingest | Technology | live | Platform · Ingest | Feeds unsigned-note aging (55 Pa. Code 1101.51) |
| Time, PTO & bonus | HR | planned | OPS-1 | FLSA wage-and-hour (when time drives pay) |
| Time entry | HR | planned | OPS-6 | FLSA — accurate hours for non-exempt employees |
| Hiring & onboarding | HR | planned | OPS-7 | 42 CFR 455.436 (exclusion screening at hire) |
| Marketing workbench | Marketing | planned | OPS-8 | — |
| Financial ops | Finance | planned | FIN-1 | PerformCare Ch. VI claims aging (context) |
| Telephony capture | Technology | planned | OPS-4 | MA 4-year retention (beyond vendor ceilings) |

---

## Compliance registry obligations

These are calendar clocks inside **Compliance registry**. Seeded cadence clocks
(`o1`–`o12`) write to the due engine. `o13`/`o14` are per-episode / per-plan.

| Item | Cadence | Owner | Legacy | Authorities |
|---|---|---|---|---|
| Exclusion screening — all staff, contractors, vendors | 30d | Admin | o1 | 42 CFR 455.436; PerformCare Ch. VI |
| Chart audit sample — 5 charts per clinician | 90d | COO | o2 | Compliance program element 6 |
| Compliance committee meeting + minutes | 90d | COO | o3 | Compliance program element 2 |
| Encounter form reconciliation against notes | 30d | Billing | o4 | MA Bulletin 99-89-05 |
| Unsigned note sweep — aging over 72 hours | 7d | COO | o5 | 55 Pa. Code 1101.51(e)(1)(iii) |
| Program Exception Attestation to Provider Relations | 365d | COO | o6 | PerformCare Ch. VI — due Jan 1 |
| Annual compliance + HIPAA training, all staff | 365d | COO | o7 | Compliance program element 3 |
| HIPAA Security Risk Analysis review | 365d | COO | o8 | 45 CFR 164.308(a)(1)(ii)(A) |
| BAA registry review — all PHI vendors | 365d | COO | o9 | 45 CFR 164.502(e) |
| Ownership & control disclosure refresh | 365d | CEO | o10 | 42 CFR 455.104 |
| Policy register review — versions and owners | 365d | COO | o11 | Compliance program element 1 |
| Claims aging review — 365-day filing bar | 30d | Billing | o12 | PerformCare Ch. VI; Admin Appeals |
| Adjunct re-screen at each treatment plan review | Per plan | Clinician | o13 | PerformCare CM-MS-003 |
| Retention clock per closed case | Per episode | Admin | o14 | 4-year minimum retention — encounter forms |
