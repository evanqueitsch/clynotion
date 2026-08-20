> **Product rename:** This document historically says “Attune.” The product name is now **Clynotion** (clynotion.com). Treat Attune as the prior working title.

# Attune — Master Scope Document

**Heart for Change internal platform · living document**

| Field | Value |
|---|---|
| Version | 2.1 |
| Last updated | 2026-08-20 |
| Owner | Evan, COO |
| Status | Architecture of record |

> **HOW TO USE THIS DOCUMENT**
> One file, three registers:
> - **Modules (§2–§6)** = things Attune builds. Each has an ID and its own scope section.
> - **Appendix A** = organizational compliance obligations Attune *tracks* but does not satisfy. Filings, policies, appointments. Not software.
> - **Appendix C** = the source gap analysis — what SimplePractice does not cover. Modules derive from this list.
>
> Never let an Appendix A item become a build ticket. Never let a shipped module be mistaken for a satisfied obligation. Mark closed items `Closed` and keep the row.

---

## Changelog

| Version | Date | Change |
|---|---|---|
| **2.1** | 2026-08-20 | Added §12.9 Onboarding & Compliance Tracking (design of record from the 2026-08-18 module scope). Replaces `H4C_Onboarding_Tracker.xlsx`. HR/Employee PII zone — distinct from §6 COMPLY registry (legacy OPS-2). Canonical module text: `docs/modules/ops2-onboarding-compliance-scope.md`. |
| 1.0 | — | Initial scope. Platform overview + OPS-1 (PTO/Productivity/Bonus). |
| 1.1 | 2026-08-07 | Markdown conversion. Added OPS-2 (Compliance & Credentialing Register). Added Appendix A (Medicaid/MA gap items), Appendix B (open decisions). |
| 1.2 | 2026-08-07 | **Architecture of record set** — Attune is a custom application built in Cursor; PHI constraint overridden. Expanded OPS-2 to full compliance registry scope. Added OPS-3 (Intake Log & Access Standards), OPS-4 (Telephony Event Capture). Added Appendix C (SimplePractice gap analysis), Appendix D (related artifacts). Expanded Appendix A with enrollment prerequisites and PerformCare findings. |
| **2.0** | 2026-08-07 | **Restructured from module list to product architecture.** Four domains (Comply, People, Grow, Books) over a shared Platform Core. Added §3 Platform Core, §4 Home dashboard, §3.4 SimplePractice ingest, FIN-1 QuickBooks. Multi-tenancy promoted to design requirement. Module IDs retained as aliases. |
| 1.8 | 2026-08-07 | Added OPS-6 (Time Entry), OPS-7 (Hiring & Onboarding), OPS-8 (Marketing Workbench) as §9–§11; sections renumbered. Added §2.1 build sequence. Added B-24 through B-30. |
| 1.7 | 2026-08-07 | Added OPS-5 — Eligibility Verification Layer (§8), adopted as the design of record: one internal interface, pluggable backends, uniform record shape. Sections renumbered. Added B-21 through B-23. |
| 1.6 | 2026-08-07 | **Clinical documentation decision: buy, not build.** SimplePractice + Care Aide adopted; CLY-1 Phase 2 (clinical session transcription) deferred with named reversal triggers (§7.8). Added Care Aide to systems of record and Appendix E. |
| 1.5 | 2026-08-07 | Added Appendix E — BAA register (Google, Deepgram, Anthropic, Neon Scale, Fly.io) with coverage traps. Resolved B-15 (hosting = Neon Scale + Fly.io). |
| 1.4 | 2026-08-07 | Added CLY-1 (Clynotion transcription, Deepgram engine) as §7; sections renumbered. Added Deepgram to systems of record. Added B-16, B-17, B-18. |
| 1.3 | 2026-08-07 | Folded in the MA Client Workflow and the OPS-2 prototype source. Added §2.3 systems of record, §2.4 handoff taxonomy, §2.5 crossing budget, §12 four hard gates, §13 document conflicts (X-1/X-2/X-3). Added §4.4a adjunct screen, obligations o13–o14, credential clock cadences, six-point audit rubric. Expanded OPS-3 with channels and outcomes. |

---

## Status conventions

| Label | Meaning |
|---|---|
| `Not started` | Identified, no work done |
| `Scoping` | Being defined in this document |
| `Prototyped` | Working model exists; not production |
| `In progress` | Actively building |
| `Blocked` | Waiting on an external dependency (name it) |
| `Done` | Shipped / filed / adopted |
| `Closed` | No longer pursued (keep the row, note why) |

---

## 1. What Attune is

**Attune is the practice operations layer for small clinical practices.** SimplePractice runs the clinical side and runs it well. Attune runs everything else — compliance, people, growth, and money — on one surface instead of five products.

### 1.1 The thesis

A solo or small group practice moving into growth stage hits the same wall: the clinical system is solved, and nothing else is. Compliance lives in spreadsheets, hiring lives in an inbox, time lives in memory, marketing lives nowhere, and the books live in an accountant's head. The available answers are enterprise HR suites, standalone ATS tools, compliance consultants, and marketing agencies — five products, five logins, five bills, none of which know about each other.

**Attune's job is to be the one place the operator opens.** Not the most features. The fewest places to look.

### 1.2 What it is not

- **Not a clinical system.** No progress notes, no treatment plans, no scheduling, no claims. SimplePractice owns the chart and stays the system of record.
- **Not a payroll processor.** It computes and exports; a payroll system pays.
- **Not an accounting system.** QuickBooks owns the ledger; Attune reads it and adds operational context.
- **Not a replacement for judgment.** It surfaces what is due and what is off. People decide.

### 1.3 Product principles

| Principle | Meaning |
|---|---|
| **One surface** | Every domain writes to one dashboard. If a thing is due, it appears in one place, regardless of which domain owns it. |
| **Two-minute week** | The core loop is a weekly check that takes two minutes. Anything requiring more attention than that must earn it. |
| **Nothing per-session** | Clinical work stays in SimplePractice. Attune receives batched data. A tool that taxes every session gets abandoned. |
| **Records, not transactions** | Attune's durable asset is the record of what happened. Integrations are pipes; pipes get replaced. |
| **Boring and reliable** | This is compliance infrastructure. Novelty is a liability. |

### 1.4 Multi-tenancy is a design requirement, not a maybe

[Certain] If Attune ever serves another practice, Heart for Change becomes a Business Associate to that practice — separate BAAs, independent breach-notification duty, and security questions from buyers. Retrofitting multi-tenancy onto a live PHI database is among the most painful migrations in software.

**Therefore, from the first schema:** `tenant_id` on every table, row-level security policies, no cross-tenant queries, tenant-scoped storage keys. Single-tenant deployment, multi-tenant schema. The cost today is near zero.

---

## 2. About the platform

Attune is Heart for Change's in-house compliance and operations platform. It sits around SimplePractice (SP), which remains the system of record for scheduling, intake charting, clinical documentation, billing, and records. Attune adds guidance, routing, analytics, compliance tracking, and internal operations — it never replaces SP or becomes a second clinical repository.

### 2.1 Architecture decisions of record

| Decision | Position of record | Basis |
|---|---|---|
| **Build environment** | **Cursor.** Custom-coded application, owned source. | Evan builds it |
| **Deployment** | Standalone HIPAA-compliant application. No-code/low-code platforms are ruled out. | Security Rule footprint and BAA hosting requirements |
| **PHI** | **Attune holds PHI.** | [Certain] Evan's executive call, overriding the earlier no-PHI constraint |
| **Hosting / database** | **Undecided — see B-15** | Must be BAA-covered |

> **⚑ CURSOR IS NOT A HOSTING DECISION**
> [Certain] Cursor is where the code gets written. It says nothing about who signs the BAA for the database, the app host, error monitoring, or backups. Every one of those is a Business Associate the moment PHI lands in the system. Pick the hosting stack before the schema, because the choice constrains encryption-at-rest, audit logging, and restore testing. See **B-15**.

**The unresolved consequence of the PHI reversal.** [Certain] The entire compliance registry design — case-code convention, no free-text fields on intake records, crosswalk stored only in SimplePractice — existed to keep Attune out of PHI scope. That rationale is now gone. The design still assumes it.

[Likely] Two coherent paths, and the failure mode is picking neither:

1. **Keep case codes as operational discipline.** Attune is a PHI system by architecture but minimizes actual PHI in practice — smaller breach surface, easier audits. Requires the rule to be written down and enforced.
2. **Drop the constraint.** Names in Attune, full Security Rule build, richer functionality, larger exposure.

Half-holding the rule is worse than either. See **B-10**.

**Consequences of holding PHI** [Certain] — all now in scope and none optional:

- Full HIPAA Security Rule implementation
- BAA-covered hosting
- No PHI in application logs
- Synthetic data in development environments
- Tested, documented restores
- Access controls and an append-only audit trail

### 2.2 Governing principles

- SP is the system of record for clinical content.
- **No SP API exists.** Do not scrape or use unsupported automation. Every SP↔Attune handoff is a human retyping something.
- Therefore: **the workflow must survive zero per-session crossings.** Session-level work stays entirely inside SP. Attune receives batched, low-frequency crossings only. [Certain] A design requiring "log each note to Attune" is abandoned by week three.
- Do not duplicate clinician calendars or force clinicians to maintain new systems.
- Write to the backend only through validated, server-side ingestion.
- Build **tenant isolation into the schema now**, even as a single-tenant deployment. [Certain] If Attune ever serves another practice, Heart for Change becomes a Business Associate — separate BAAs, independent breach-notification duty, SOC 2 questions from buyers. Multi-tenant retrofit on a live PHI database is among the more painful migrations in software. A `tenant_id` column and row-level policies today cost nothing.
- Build the **append-only audit log with as-of reconstruction first.** [Likely] It is a database design, not a UI feature, and it is the hardest thing to retrofit.
- Attune earns its way in by replacing processes already being executed manually. It does not gate the compliance program.

### 2.3 Systems of record

| System | Owns | Holds PHI |
|---|---|---|
| **SimplePractice** | Clinical record, scheduling, notes, treatment plans, claims, client portal | Yes |
| **NaviNet** | Eligibility inquiry, authorization (Jiva), claim status, claim submission | Yes |
| **EVS** | Pennsylvania eligibility verification of record | Yes |
| **Document store** | Signed encounter forms, pre-amendment note archives, ROIs | Yes |
| **Attune** | Compliance clocks, screening, audits, registers, intake log | **Contested — see B-10** |
| **Fax vendor** | Records requests, PCP coordination, appeal packets | Yes |
| **PROMISe** | Enrollment and 5-year revalidation | No |
| **Deepgram** | Speech-to-text engine behind Clynotion (CLY-1) | Yes — audio, transcripts, derived data |
| **Neon (Scale)** | Attune's Postgres database | Yes |
| **Fly.io** | Attune application hosting | Yes |
| **Anthropic (Claude API)** | LLM processing — summarization, parsing | Yes |
| **Google Workspace** | Mail, Calendar, Drive, Vault, Voice | Yes |
| **SimplePractice Care Aide** | AI note drafting, treatment planning, intake summaries | Yes — under existing SP BAA |

### 2.4 Handoff taxonomy

Five types, descending cost. [Certain] Choosing the wrong type is how workflows fail.

| Type | Definition | Use when |
|---|---|---|
| **Hard gate** | Work cannot proceed. No override without executive sign-off | Failure creates an unbillable or unrecoverable claim |
| **Warm handoff** | Synchronous, person-to-person, same day | Clinical risk or judgment is involved |
| **Task with receipt** | Assigned async, must be acknowledged, escalates on miss | Ownership transfers and the clock matters |
| **Batched crossing** | Accumulates, transfers on a cadence | Crossing a system boundary with no integration |
| **Exception-only** | Surfaces only when something is wrong | Volume is high and the normal case needs no attention |

**Design rule:** every SimplePractice → Attune movement is a **batched crossing** or **exception-only**. Never per-session.

### 2.5 Crossing budget — the platform health metric

[Certain] The design's health is measurable. Count SimplePractice → Attune crossings:

| Frequency | Crossings | Notes |
|---|---|---|
| Per session | **0** | Non-negotiable |
| Per episode | 3 | Open case, close case, retention clock |
| Per exception | 1 | Note amendment |
| Per month | ~4 | Screening batch, reconciliation result, claims exception, eligibility exception |
| Per quarter | 2 | Audit batch, committee minutes |
| Per year | ~6 | Training, SRA, BAA, disclosure, policy, attestation |

**Target: 80–100 crossings per year at three clinicians** — a few minutes a week. If the real number climbs, something has been designed as per-session that shouldn't be. Treat this as a standing acceptance criterion, not a one-time check.

---

## 2.9 Domain map

Four domains over a shared platform core. Every module belongs to exactly one domain. Legacy module IDs retained as aliases so existing references and code don't break.

| Domain | What the operator comes here for | Modules |
|---|---|---|
| **COMPLY** | "Am I covered, and what's due?" | Compliance Registry (`OPS-2`) · Eligibility (`OPS-5`) |
| **PEOPLE** | "Who works here, what are they owed, who's coming?" | Time & PTO (`OPS-1`, `OPS-6`) · Hiring & Onboarding (`OPS-7`) · Supervision (`CLY-1`) |
| **GROW** | "Where do clients come from, and are we reachable?" | Intake & Access (`OPS-3`) · Matcher (`MATCH`) · Intake Routing (`INTAKE-R`) · Analytics (`ANALYTICS`) · Marketing Workbench (`OPS-8`) |
| **BOOKS** | "What came in, what went out, what's stuck?" | QuickBooks & Financial Ops (`FIN-1`) |
| **PLATFORM** | *(not user-facing)* | Ingest · Due engine · Identity & RBAC · Audit log · Document refs · AI provider layer · Telephony capture (`OPS-4`) |

### 2.9.1 Full module register

| ID | Module | Domain | Status | § |
|---|---|---|---|---|
| `OPS-2` | Compliance & Credentialing Registry | COMPLY | Prototyped | §6 |
| `OPS-5` | Eligibility Verification Layer | COMPLY | Scoping | §10 |
| `OPS-1` | PTO, Productivity & Bonus | PEOPLE | Scoping | §5 |
| `OPS-6` | Time Entry | PEOPLE | Not started | §11 |
| `OPS-7` | Hiring & Onboarding | PEOPLE | Not started | §12 |
| `CLY-1` | Clynotion — supervision transcription | PEOPLE | In progress | §9 |
| `OPS-3` | Intake Log & Access Standards | GROW | Prototyped | §7 |
| `MATCH` | Clinician matcher (public) | GROW | In progress | — |
| `INTAKE-R` | Intake routing (public) | GROW | In progress | — |
| `ANALYTICS` | Visitor analytics (anonymous) | GROW | In progress | — |
| `OPS-8` | Marketing Workbench | GROW | Not started | §13 |
| `FIN-1` | **QuickBooks & Financial Ops** | BOOKS | Not started | §14 |
| `OPS-4` | Telephony Event Capture | PLATFORM | Not started | §8 |

### 2.10 Build sequence

[Certain] Thirteen modules. Sequence is a scope decision, not a preference.

| Tier | Modules | Why |
|---|---|---|
| **0 — Foundation** | Platform Core (§3), Home (§4), SP Ingest (§3.4) | Nothing else works without these. Build first, always. |
| **1 — Regulatory clock attached** | `OPS-2`, `OPS-3`, `OPS-5` | External deadlines. Missing these costs money or enrollment. |
| **2 — In flight** | `CLY-1` Phase 1 | Already building |
| **3 — Operational value, no external clock** | `OPS-1`, `OPS-6`, `FIN-1` | Real value; a missed month costs nothing regulatory |
| **4 — Opportunistic** | `OPS-4`, `OPS-7`, `OPS-8` | Build when the pain is felt |

**Rule:** Tier 0 ships before Tier 1. Nothing in Tier 3 or 4 starts until Tier 1 has a working v1 in production use.

---

## 3. Platform Core

The shared services every domain depends on. [Certain] These are what make Attune one product instead of five tabs — build them once, correctly, before any domain module.

### 5.1 The seven core services

| Service | Responsibility | Why it's shared |
|---|---|---|
| **Due engine** | Every module writes obligations here: due date, owner, domain, status, source. One query answers "what's due." | This *is* the single pane of glass. Without it, each module has its own list and the operator checks four places. |
| **Ingest** | All inbound data: CSV uploads, API adapters, email parsing, manual entry. Normalizes, validates, records provenance. | Every module needs data in. One ingest layer means one place to debug when a feed breaks. |
| **Identity & RBAC** | Users, roles, permissions, domain-level access walls. | HR data, compliance data, and marketing data have different audiences. One permission model. |
| **Audit log** | Append-only, with as-of reconstruction. Who changed what, when, from what to what. | [Certain] Hardest thing to retrofit. It is a database design, not a UI feature. Build first. |
| **Document refs** | Pointers to artifacts stored elsewhere (SimplePractice, Drive, document store). Attune holds the reference and metadata, not the file. | Keeps Attune from becoming a second clinical repository. |
| **AI provider layer** | One internal interface, swappable model backends, per-purpose profiles, provenance on every generation. | Used by `OPS-8` and any future AI feature. Enforces the BAA boundary at the application layer. |
| **Tenant isolation** | `tenant_id` on every table, row-level security, tenant-scoped keys. | §1.4. Near-zero cost now, migration nightmare later. |

### 5.2 The record spine

Four entity families everything else hangs off:

| Family | Entities | Owned by |
|---|---|---|
| **People** | `Employee`, `Clinician`, `Candidate`, `User` | PEOPLE |
| **Obligations** | `Obligation`, `ComplianceTask`, `Clock` | Due engine |
| **Events** | `IntakeEvent`, `CallEvent`, `EligibilityCheck`, `AuditFinding`, `Grievance`, `NoteUnlock`, `TimeEntry` | Various |
| **Artifacts** | `PolicyArtifact`, `DocumentRef`, `MarketingAsset`, `Transcript` | Various |

[Certain] One `Employee` record. Not one in HR, one in compliance, and one in time tracking. A clinician hired through `OPS-7` becomes an `Employee` in `OPS-1` and a `Clinician` in `OPS-2` with six clocks started — same underlying person, one row, linked views.

### 5.3 Design rule for every module

A module is a **view and a workflow over the record spine**, not a silo. If a new module needs its own copy of employee data, its own task list, or its own audit trail, it has been designed wrong.

---

### 3.4 SimplePractice ingest — the data pipe

**The data pipe.** [Certain] No SimplePractice API exists. Scraping is prohibited. Everything comes in through supported exports, and the design must be honest about that.

### 7.1 The three channels

| Channel | Carries | Cadence | Effort |
|---|---|---|---|
| **Report CSV upload** | Sessions held, appointments with documentation status, billing and claims detail, client demographics | Weekly / monthly | ~2 min per upload |
| **Google Calendar API** | Appointment ledger via domain-wide delegation across clinician calendars | Continuous | Zero after setup |
| **Manual entry** | Anything not covered | As needed | Varies |

### 7.2 Reports worth ingesting

| SP report | Feeds | Value |
|---|---|---|
| **Appointments with "Include Documentation"** | `OPS-2` o5 unsigned-note sweep | [Certain] Solves the documentation-gap detection problem directly. No inference needed. |
| **Appointment status / attendance** | `OPS-1` productivity, `OPS-3` access performance | Session counts for bonus computation |
| **Billing & claims detail** | `FIN-1`, `OPS-2` o4 encounter reconciliation, o12 claims aging | Denial patterns drive audit sampling |
| **Client demographics** | `OPS-5` eligibility roster | Who needs a monthly check |

### 7.3 Design requirements

- **Upload is idempotent.** Re-uploading the same report changes nothing. Operators will double-upload; the system must not care.
- **Provenance on every row.** Which report, which upload, which date range, who uploaded.
- **Column mapping is configurable.** [Likely] SimplePractice changes report formats without notice. A hardcoded column index becomes a silent data-corruption bug.
- **Ingest failures surface on Home.** A feed that stops working must be loud, not silent.

### 7.4 The crossing math

| Ingest activity | Frequency | Time |
|---|---|---|
| Weekly documentation report | 52/yr | ~2 min |
| Monthly billing report | 12/yr | ~3 min |
| Monthly demographics refresh | 12/yr | ~2 min |
| Calendar sync | continuous | 0 |

**~2.5 hours per year of upload effort** to feed the entire platform. That is the number that makes this design viable.

---

## 4. Home — the single pane of glass

**One screen. The reason the product exists.**

### 6.1 What it shows

| Band | Content | Source |
|---|---|---|
| **Overdue** | Anything past due, any domain, red | Due engine |
| **This week** | Due in 7 days, grouped by domain | Due engine |
| **Attention** | Exceptions: eligibility gaps, unsigned-note aging, denied claims, expiring credentials, stalled candidates | Module exception feeds |
| **Pulse** | Four numbers: access-standard %, documentation compliance %, cash position, open headcount | Cross-domain rollup |

### 6.2 Rules

- **Two-minute week.** [Certain] The weekly loop is: open Home, clear what's due, act on exceptions, close. If that takes longer than two minutes on a normal week, the module feeding it is over-reporting.
- **Nothing appears on Home without an owner and a due date.** Unowned items are invisible by design — they go to a triage queue instead.
- **Exception-only for high-volume data.** Sessions, claims, and calls never appear individually. Only the ones that are wrong.
- **One click to complete.** Marking an obligation done takes one action, no confirmation dialog, no required note.

### 6.3 The failure mode this is built against

[Certain] Compliance tools die when nobody opens them between audits. Every design decision above trades features for the probability that someone opens this screen on a Monday. That trade is always correct.

---

## 5. PEOPLE · Time & Compensation (`OPS-1`)

> **● INTERNAL — MANAGEMENT USE ONLY**
> Confidential HR and compensation data. Restricted to owner/back-office admin. Access-walled from the public matcher, from visitor analytics, and from clinicians.

### 5.1 Objective

Replace the standalone spreadsheet tracker with an access-controlled module that keeps each employee's PTO balance, weekly productivity, and quarterly bonus always current — a live lookup, not a period-end reconstruction from calendars and text messages.

### 5.2 Background & rationale

A recent separation showed the cost of not having this: PTO entitlement and sub-minimum weeks had to be rebuilt by hand from scattered records, and the absence of a single source of truth turned a simple question into a dispute. This module encodes the adopted policies (Employee Handbook and Quarterly Productivity Bonus program) so the numbers are computed the same way every time, for everyone.

### 5.3 In scope

- Employee roster with per-employee settings: role, benefit eligibility, annual PTO grant, weekly session expectation / bonus target.
- PTO & leave log: dated entries typed as PTO, Holiday, or Unpaid, with notes; PTO balance computed against the flat annual grant.
- Productivity log: weekly completed-session counts per clinician, with automatic flagging of weeks below expectation.
- Quarterly bonus computation: pro-rated target (net of PTO/holidays), sessions over target, bonus = max(0, over) × per-session rate.
- Summary dashboard per employee: PTO used/remaining, holidays taken, weeks logged, weeks under, current-quarter bonus.
- Role-based access control, audit trail of edits, export to spreadsheet/PDF for payroll and the personnel file.

### 5.4 Out of scope (v1)

- Payroll disbursement — Attune computes figures; it does not pay wages.
- Replacing or writing back to SimplePractice.
- Clinician self-service editing (read-only self-view is a later phase).
- Automated bonus approval/payment — payout remains a deliberate management action.

### 5.5 Data model (initial)

| Entity | Key fields |
|---|---|
| `Employee` | id, name, role, benefit_eligible, annual_pto_days, weekly_target, active, start_date |
| `LeaveEntry` | id, employee_id, date, type (PTO \| Holiday \| Unpaid), days, note |
| `ProductivityWeek` | id, employee_id, week_ending, sessions_held, source (manual \| import) |
| `BonusConfig` | id, quarter_label, start_date, end_date, scheduled_weeks, per_session_rate |
| *(computed)* `Summary` / `BonusResult` | pto_used, pto_remaining, weeks_under, available_weeks, prorated_target, over_target, bonus_amount |

Computed values are derived views, not stored source-of-truth, so a correction to any log entry re-derives balances and bonuses automatically.

### 5.6 Business rules (from adopted policy)

- PTO is a flat annual grant: no hourly accrual, no rollover, no payout at separation. Only entries typed "PTO" draw down the balance.
- Holidays are tracked separately and never charged against PTO.
- A paid day off counts by its record and type, not by a label applied after the fact.
- Weekly target is a performance expectation only — it never adjusts pay. The module flags under-target weeks for coaching; it does not dock.
- Bonus is quarterly and marginal:
  - `available_weeks = scheduled_weeks − (PTO days + Holiday days) / 5`
  - `prorated_target = weekly_target × available_weeks`
  - `bonus = MAX(0, sessions − prorated_target) × per_session_rate`
- Only completed/attended sessions count; define intake/group/telehealth handling in configuration.
- The bonus is discretionary and requires active employment on the payout date (surface as non-editable policy notes in the UI).

### 5.7 Data sources & SimplePractice dependency

Session counts logically originate in SP. Per §1.2, no API exists and scraping is prohibited. Therefore:

- v1 productivity data is entered manually or imported from an SP report/CSV export; PTO and holidays are entered/approved directly in Attune.
- SP-sourced auto-import is a later phase, contingent on confirmed SP export or partner API access.
- Every `ProductivityWeek` records its source (manual vs import) so provenance is auditable.

> **⚑ BUILD CONSTRAINT**
> Do not build this module in a way that blocks on SP integration. The spreadsheet's computation logic is already proven; v1 ships on manual/CSV entry.

### 5.8 Roles & access control

| Role | Access |
|---|---|
| Owner (Melissa) | Full: view/edit all employees, config, payouts |
| Back-office admin | View/edit logs and run summaries; per config, may or may not see bonus $ and comp |
| Clinician | No access in v1. Later: read-only view of own productivity/PTO |
| Public matcher / visitor analytics | No access — fully separated |

### 5.9 Phasing

| Phase | Delivers |
|---|---|
| v1 — Core | Data model, admin CRUD, computations matching the spreadsheet, RBAC, export. Manual / CSV entry. |
| v2 — SP import | Import completed-session counts from confirmed SP export or partner API; provenance tracking. |
| v3 — Self-service | Clinician read-only view; optional PTO-request/approval workflow. |

### 5.10 Acceptance criteria

- For test employees and logs, PTO used/remaining, weeks-under, pro-rated target, and bonus match the delivered spreadsheet exactly.
- PTO flat-grant, no-rollover, no-payout behavior enforced; holidays never reduce PTO.
- Bonus never goes negative and pro-rates correctly for PTO/holiday weeks.
- RBAC verified; audit log records edits.
- Export reproduces summary and bonus figures for payroll and the personnel file.

---

## 6. COMPLY · Compliance & Credentialing Registry (`OPS-2`)

> **● INTERNAL — MANAGEMENT USE ONLY**
> **Status: Prototyped.** A working React prototype with persistent storage exists. [Certain] It is a model validator, not the production system — its storage is environment-scoped. Use it to find out which fields the admin actually wants before anyone writes a schema.

### 6.1 Objective

Fill what SimplePractice does not cover for MA compliance and governance. Not submission — **management and maintenance**. One screen answers *"what's overdue, what's due next,"* and a completed control logs in two clicks.

[Certain] The failure mode for this class of tool is not missing features. It is non-use. Compliance systems die when nobody opens them between audits. Everything else is subordinate to the two-minute weekly check.

### 6.2 Why this exists

[Certain] The MA enrollment work surfaced fourteen compliance obligations SimplePractice does not handle (Appendix C), plus required artifacts the practice does not have — a named Compliance Officer, an adopted Code of Conduct, a written Corporate Compliance Plan. [Certain] It also surfaced a state where critical regulatory guidance existed only as one person's memory of a phone call with no reference number captured. Same failure pattern OPS-1 was built to fix, in a domain with worse consequences.

### 6.3 In scope

- **Compliance calendar with escalation** — recurring obligations, cadence, owner, statutory authority, overdue/due/clear states.
- **Credential & revalidation tracker** — six automated clocks per clinician:

  | Clock | Cadence | Authority |
  |---|---|---|
  | License renewal | 730d | PA licensing board |
  | CAQH re-attestation | 120d | CVO pulls from CAQH |
  | Malpractice rider | 365d | Credentialing packet |
  | Recredentialing | 1095d (3yr) | PerformCare Ch. VI |
  | MA revalidation | 1825d (5yr) | ACA |
  | Exclusion screening | 30d | 42 CFR 455.436 |

  [Certain] PerformCare prepares and tracks MA revalidation for in-lieu-of providers — **keep your own copy of the date anyway.**
- **Exclusion screening results register** — OIG LEIE, SAM.gov, PA precluded provider list. Every employee, contractor, vendor. Monthly.
- **BAA registry** — every PHI-touching vendor, with review dates.
- **Chart audit program** — six-point scored rubric, five charts per clinician per quarter, trending over time:

  | # | Criterion | Tests for |
  |---|---|---|
  | 1 | Progress note complete | Reason, interventions, response, plan, next steps |
  | 2 | Clock times + units reconcile | Start/stop with AM/PM; units match claim |
  | 3 | Valid treatment plan for DOS | Active plan covering the date of service |
  | 4 | Medical necessity documented | Symptoms, impairment, response, rationale to continue |
  | 5 | Signatures present and dated | Clinician credentials; corrections initialed |
  | 6 | Encounter form present + matches | Signed; service type, date, units agree with note |

  Audit findings go back to clinicians as a **warm handoff — individual, not group.**
- **Note-unlock / amendment event register** — unlock date, clinician, reason, pre-amendment archive confirmed.
- **Grievance log with MCO clocks** — code, category, dates.
- **Training attestation log** — annual compliance and HIPAA training, per person.
- **Policy version control** — each policy with version, owner, adoption date, review-due date.
- **Overpayment / self-disclosure log** — keyed by claim number.
- **Compliance committee minutes** register.
- **Correspondence log** — dated regulator/payer contacts: rep name, reference number, instruction received, written confirmation attached. A row without written confirmation is flagged.

### 6.4 Seeded recurring obligations

Pre-loaded from the PerformCare manual and cited authority.

| # | Obligation | Cadence | Owner | Authority |
|---|---|---|---|---|
| o1 | Exclusion screening — all staff, contractors, vendors | 30d | Admin | 42 CFR 455.436; PerformCare Ch. VI |
| o2 | Chart audit sample — 5 charts per clinician | 90d | COO | Compliance program element 6 |
| o3 | Compliance committee meeting + minutes | 90d | COO | Compliance program element 2 |
| o4 | Encounter form reconciliation against notes | 30d | Billing | MA Bulletin 99-89-05 |
| o5 | Unsigned note sweep — aging over 72 hours | 7d | COO | 55 Pa. Code 1101.51(e)(1)(iii) |
| o6 | Program Exception Attestation to Provider Relations | 365d | COO | PerformCare Ch. VI — due Jan 1 |
| o7 | Annual compliance + HIPAA training, all staff | 365d | COO | Compliance program element 3 |
| o8 | HIPAA Security Risk Analysis review | 365d | COO | 45 CFR 164.308(a)(1)(ii)(A) |
| o9 | BAA registry review — all PHI vendors | 365d | COO | 45 CFR 164.502(e) |
| o10 | Ownership & control disclosure refresh | 365d | CEO | 42 CFR 455.104 |
| o11 | Policy register review — versions and owners | 365d | COO | Compliance program element 1 |
| o12 | Claims aging review — 365-day filing bar | 30d | Billing | PerformCare Ch. VI, Admin Appeals |
| o13 | **Adjunct re-screen at each treatment plan review** | Per plan cycle | Clinician | PerformCare CM-MS-003 — see §4.4a |
| o14 | Retention clock per closed case — destruction-eligible date | Per episode | Admin | 4 years minimum, encounter forms |

### 6.4a The adjunct screen — the obligation most likely to be missed

[Certain] Routine outpatient MH requires neither registration nor prior authorization (PerformCare dropped registration for most ambulatory/outpatient services in April 2012, per CM-MS-003 rev. 06/24/24). There is no session cap, no authorized-units burn-down, no concurrent review.

**What replaces it is concurrent-service detection.** Outpatient therapy running alongside another BH service may be classified a duplication — "adjunct" — and *does* require prior authorization.

| Concurrent service | Effect on the outpatient claim |
|---|---|
| FBMHS, IBHS, psychiatric rehabilitation, targeted case management, or any approved level of care including therapy | **Adjunct — prior authorization required** |
| Substance use disorder treatment | **Not adjunct.** No prior authorization needed in either direction |
| Nothing else active | No authorization, no registration |

**Also still requiring registration or prior approval:** psychological and neuropsychological testing, initial targeted case management, music therapy, psychiatric rehabilitation including clubhouses, peer support.

**Why this is the sharper risk.** [Certain] A session cap announces itself — you can count. A member who starts family-based services in month four announces nothing, and outpatient claims silently become adjunct without authorization. On appeal, PerformCare upholds denials for provider failure in authorization management.

**Submission paths if adjunct:** Cabinet SHARE (preferred) · fax 1-888-987-5828 · NaviNet/Jiva. Outpatient requests may be entered up to 90 days in advance.

### 6.5 Out of scope

- **Attune performs no filing, enrollment, or credentialing action.** It records state. Every obligation in Appendix A is satisfied by a person.
- Legal or regulatory determination. The register stores what was decided and by whom.
- **Coordination-of-care tracking.** [Certain] Deliberately left in SimplePractice — it belongs in the chart and an auditor will look for it there. Attune reports the aggregate only.
- Document storage and form generation (v1).
- DOT SAP billing pathways. [Certain] SAP evaluations are employment-compliance services under 49 CFR Part 40 — not medically necessary treatment, not Medicaid-billable. Cash/employer-pay. Out of scope for this module's payer logic.

### 6.6 Known SimplePractice constraint

[Certain] **The SP HIPAA Audit Log cannot be exported.** It displays when a team member viewed client information — including printing and downloading — filterable by client, team member, and date range. There is no documented export path. It cannot feed Attune, and producing it for an auditor means screenshots or a vendor request.

**Action:** find out the vendor's turnaround time *before* it is needed inside a 30-day audit window. Logged as **A-24**.

### 6.7 Data model (initial)

| Entity | Key fields |
|---|---|
| `Clinician` | id, name, credentials, npi, license_no, and six clock dates |
| `Enrollment` | id, payer_program, legal_entity, provider_number, mpi, service_location, status, effective_date, revalidation_due, portal_owner |
| `Obligation` | id, title, cadence_days, owner, authority, last_completed |
| `Screening` | id, subject, list (LEIE \| SAM \| PA), run_date, result, evidence_link |
| `PolicyArtifact` | id, name, required_by, status, version, adopted_date, review_due, doc_link, owner |
| `Attestation` | id, employee_id, policy_artifact_id, attested_at, method |
| `ChartAudit` | id, clinician_id, case_code, audit_date, six rubric scores, findings |
| `Event` | id, type (note_unlock \| grievance), case_code, opened, closed, reason |
| `ContactLog` | id, date, agency, rep_name, reference_number, subject, instruction, confirmation_link |
| `ComplianceTask` | id, title, source_ref, owner, due_date, status, blocking, notes |

### 6.8 Business rules

- Every `ComplianceTask` and `Obligation` has exactly one named human owner. No unowned rows.
- A `PolicyArtifact` is not `Done` without both an adoption date and a document link. Drafted ≠ adopted.
- Verbal regulator/payer guidance must be entered as a `ContactLog` row with rep name and reference number.
- Enrollment records are never deleted. Closed enrollments retain status `Closed` plus closure date — the history is the point.
- Expiration and revalidation dates warn at 90/60/30 days.
- Manual entry throughout. These are monthly-to-quarterly writes — minutes of data entry, not hours.

### 6.9 Governance constraint

[Likely] The single biggest governance flaw in two-person-led practices: the COO is the compliance officer *and* oversees billing. **Segregate those.** If Evan owns billing oversight, Melissa or an outside reviewer owns compliance auditing. The module's RBAC should reflect the segregation, not paper over it.

[Certain] Also decide now whether Attune gets a contracted developer or a documented handoff path. A compliance system only one person can patch is a risk dressed as a control — and it will be holding audit evidence.

### 6.10 Acceptance criteria

- Every item in Appendix A exists as a task row with an owner and a due date.
- Dashboard surfaces any credential, revalidation, or filing date inside 90 days.
- No policy shows `Done` without adoption date and document link.
- A completed control logs in two clicks.
- RBAC verified: clinicians and the public layer cannot reach the registry.

### 6.11 Validation plan

Run the prototype for two weeks against real dates. [Certain] What that reveals is which of the twelve controls people actually mark done and which get ignored — the ignored ones are either wrongly scoped or wrongly owned, and that is worth more than any feature list.

---

## 7. GROW · Intake Log & Access Standards (`OPS-3`)

### 7.1 Objective

Timestamp every request for service at first contact, from every channel, so access-standard performance is machine-recorded rather than reconstructed.

### 7.2 Why this exists

[Certain] PerformCare requires routine outpatient appointments within **7 days of request**, measures the practice's ability to meet access standards, and feeds that measurement into recredentialing and rate-adjustment decisions. Call-based intake with verbal assignment leaves no timestamp. Today there would be no way to prove compliance.

Phone stays the primary channel — for MA specifically it is better, because eligibility and the adjunct screen happen live while the person is on the line. A web form yields a name and a callback, after which the screening happens anyway.

### 7.3 The sequencing decision

[Certain] **Attune generates the intake number, not SimplePractice.** Admin logs the call in Attune first — channel, triage, date — and receives an ID, which is then written beside the name in SimplePractice. Attune is the *first* system touched, not the second, so this stops being a crossing at all.

### 7.4 In scope

- One intake log covering every channel: channel, request type, triage level, request date (stamped at first contact), date offered, date scheduled, outcome.
- **Triage at first contact** — emergent, urgent, and routine carry separate clocks, so the script sorts before anything else.
- Rolling 30-day access performance: requests received, offered within standard, percentage met.
- Sequential intake number generation (`IN-0001` format).

### 7.5 Access standards

| Triage | Standard | Source |
|---|---|---|
| Routine | 7 days | [Certain] PerformCare Provider Manual — `confirmed: true` |
| Urgent | 1 day *(placeholder)* | `confirmed: false` — confirm with Account Executive |
| Emergent | Same day *(placeholder)* | `confirmed: false` — confirm with Account Executive |

**Channels:** Phone · PerformCare Member Services · Clinical Care Manager · PCP or provider · Website form · Walk-in

**Outcomes:** Open · Scheduled · Declined by caller · Referred out · No response · Waitlisted

[Certain] The clock starts at **request**, not at intake.

> **⚑ BLOCKING**
> Emergent and urgent windows are placeholders. Get them from Julie Merring before this module goes live, or the clock math is wrong for the two triage levels where being wrong matters most. Logged as **A-25**.

### 7.6 Design constraints

- **No free-text fields on intake records.** Originally the rule that kept names out of Attune. Under the PHI reversal (§2.1) its status is pending decision B-10, but until that resolves, hold the rule.
- **Website form is an after-hours net only**, feeding the same log. [Likely] Add it only once someone owns a same-business-day response — an unmonitored form starts the 7-day clock with nobody aware of it.
- **No SP integration.** [Guessing] SP's portal appointment-request feature appears built for existing clients rather than cold prospective intake — verify before assuming it can serve as the front door.

### 7.7 Deliverables not yet built

- Triage script for the admin (emergent / urgent / routine sorting at first contact).
- Written log structure handed to the admin as a working procedure.

---

## 8. PLATFORM · Telephony Event Capture (`OPS-4`)

**Status: Not started. Blocked on B-11 (phone platform decision).**

### 8.1 Objective

Persist call events so intake timestamps and the four-year MA retention requirement are satisfied beyond the vendor's own retention ceiling.

### 8.2 The constraint that defines this module

[Certain] The Google Admin SDK audit ceiling is **180 days**. The MA retention requirement is four years. Therefore Attune must **pull and persist call events on a schedule** — this is not an optional convenience feature; without it there is a retention gap by design.

### 8.3 Platform evaluation (decision pending)

| Platform | Cost | BAA | Retention | API |
|---|---|---|---|---|
| **Grasshopper** (current) | — | **No BAA** | — | No API |
| **Google Voice Standard** *(recommended)* | ~$120/mo incremental at 6 users | Existing Workspace BAA | Vault eDiscovery covers calls, texts, voicemail | No |
| **Quo / OpenPhone Business** | ~$138/mo annual | Separate BAA required | No Vault equivalent | Full telephony API + webhooks |

**Recommendation:** Google Voice Standard, with Attune integration handled by manual entry. [Likely] Vault coverage and BAA consolidation outweigh Quo's API advantage.

**Current-state exposure:** [Certain] Grasshopper has no BAA and a shared inbox, which is an access-control compliance problem independent of the retention question.

### 8.4 Porting risks

Number porting from Grasshopper is supported by both candidates. Known risks: extension configurations do not port · text and voicemail history do not transfer · Grasshopper must stay active through port completion.

### 8.5 Note

Google Voice Standard has **no trial available**. Quo offers 7 days. If a trial is a decision prerequisite, that constrains the order of evaluation.

---

## 9. PEOPLE · Clynotion — Supervision Transcription (`CLY-1`)

**Status: In progress.** Building in Cursor.

### 9.1 Objective

Transcription inside Attune. **Phase 1: supervision discussions. Phase 2: clinical settings.**

### 9.2 Engine — Deepgram

| Item | Position |
|---|---|
| Vendor | Deepgram (speech-to-text API) |
| BAA | **Required. Sales-gated, not self-serve.** Start procurement now — see B-16 |
| Deployment | Cloud · self-hosted · VPC/private cloud all available. Self-hosted keeps audio inside HFC infrastructure |
| Encryption | TLS (incl. 1.3) in transit; AES-256 at rest |
| Retention | Configurable — minutes to immediate deletion. **Set explicitly; do not inherit defaults** |
| Certifications | SOC 2 Type II, HIPAA, GDPR, PCI |

### 9.3 BAA scope requirement

[Certain] The BAA must explicitly cover **audio recordings, transcripts, and derived data.** Summaries, embeddings, and any Clynotion-generated artifact are derived data. A BAA naming only "audio" leaves the actual product uncovered.

### 9.4 PHI status

[Certain] **Clynotion is a PHI system from Phase 1.** Clinical supervision discusses named clients' cases. There is no non-PHI phase of this module — scope the Security Rule build accordingly from the first commit.

### 9.5 Legal requirements in scope

| Requirement | Detail |
|---|---|
| **PA all-party consent** | [Certain] 18 Pa.C.S. § 5703 — every participant must consent before recording. Supervisee *and* supervisor for Phase 1; client, clinician, and any third party for Phase 2. Criminal statute. Consent capture and storage is a CLY-1 build requirement, not a policy afterthought. |
| **42 CFR Part 2** | [Certain] Applies the moment SUD content appears — likely given the SAP line and treatment lane. Stricter than HIPAA, different consent rules, flows down to the vendor. See Appendix C. |
| **Redaction ≠ de-identification** | [Certain] Automated PHI redaction does not satisfy HIPAA de-identification. A redacted transcript remains PHI. |
| **Retention** | Transcripts fall under the 4-year MA retention floor. Set the schedule; record it per case in OPS-2. |

### 9.6 In scope

- Audio capture with consent gate — recording cannot start without all-party consent recorded.
- Deepgram integration with explicit retention flags set per request.
- Transcript storage under Attune's PHI controls (§2.1).
- Consent register: participants, date, method, scope of consent.
- Retention clock per transcript, feeding OPS-2 (o14).
- Audit trail of every access to a transcript.

### 9.7 Out of scope (v1)

- **Clinical-setting capture (Phase 2) — deferred. See §7.8.**
- Any write-back to SimplePractice.
- Automated note generation from transcript.

### 9.8 Phase 2 — deferred by decision

**Decision (2026-08-07, Evan):** Clinical session documentation is **bought, not built.** SimplePractice + Care Aide covers it. CLY-1 Phase 2 is deferred indefinitely — not cancelled.

**Rationale of record:**

| Reason | Confidence |
|---|---|
| Care Aide writes notes directly into the chart; Clynotion would require a human to paste each note into SimplePractice — a per-session crossing, which §2.5 sets at zero | [Certain] |
| Care Aide is HITRUST-certified and already under the SimplePractice BAA | [Certain] |
| SimplePractice ships in this lane fast (Care Aide June 2026, Note Checker July 2026). Anything built here is likely re-shipped by the vendor within 12–18 months | [Likely] |
| Cost is low relative to build: $59/mo owner + $49/mo per clinician ≈ $206/mo at four clinicians | [Certain] |

**Reversal triggers — revisit Phase 2 if any of these occur:**

1. **SimplePractice raises Care Aide pricing materially** (the top complaint on therapist forums post-Vista acquisition is repeated price increases).
2. **A SimplePractice API becomes available**, which would eliminate the paste-crossing problem in either direction.
3. **In-office volume becomes the majority of the caseload.** [Certain] Note Taker and Session Sidekick work only in SimplePractice Telehealth. If most MA sessions happen in the office, Care Aide covers only the non-audio features and the value proposition changes.
4. **SimplePractice becomes untenable as the PMS** for any reason — pricing, reliability, acquisition, or a compliance failure. [Certain] AI notes do not export; leaving SP forfeits them.
5. **Care Aide's transcript-retention terms change** in a way incompatible with 42 CFR Part 2 or PA consent requirements.

**Standing scope statement:** CLY-1 covers **supervision only**. Clynotion does not generate, draft, or store clinical progress notes. Anyone extending it into that lane must first clear this section.

### 9.9 What Care Aide does *not* cover — stays with Attune

[Certain] Adopting Care Aide closes the documentation-quality gap and none of the compliance gaps:

| Uncovered | Where it lives |
|---|---|
| Encounter form present and matching the note | OPS-2 rubric item 6 · Hard Gate #3 |
| Valid treatment plan covering the date of service | OPS-2 rubric item 3 · Hard Gate #2 |
| Clock times and units reconciling to the claim | OPS-2 rubric item 2 |
| The adjunct / concurrent-service screen | §4.4a · Hard Gate #4 |
| Eligibility verified before service | Hard Gate #1 |
| Supervision conversations | CLY-1 Phase 1 |
| Every credential, screening, and filing clock | OPS-2 |

**Division of labor:** Care Aide improves note *quality*. Attune verifies note *compliance*. Clynotion covers *supervision*. No overlap.

### 9.10 Care Aide adoption notes

- **Trial:** 30 days free. [Likely] Run it during the mock case rather than before, so it's evaluated against real workflow.
- **Measure one thing:** whether Treatment Planner reduces expired-treatment-plan incidents. That is Hard Gate #2 — the gate that silently unbills every subsequent session.
- **Note Checker is prelicensed-only.** [Certain] It won't apply to incoming experienced LPC/LCSW hires; it will apply to the candidate with 500 hours remaining toward LCSW.
- **Transcript retention is a decision, not a default.** [Certain] As of 17 June 2026 SimplePractice may retain de-identified, decoupled transcripts to improve its AI, configurable per clinician, per client, or per session. Set this deliberately given 42 CFR Part 2 exposure. Logged as **B-19**.
- **Consent forms need review.** SimplePractice supplies a Consent for Use of AI Tools with De-Identified Transcript Retention. It has not been checked against PA all-party consent (18 Pa.C.S. § 5703) or Part 2. Logged as **B-20**.

### 9.11 Open items

- **B-16** — Deepgram BAA execution.
- **B-17** — Cloud vs. self-hosted deployment. Interacts directly with B-15; self-hosted changes what the hosting BAA must carry.
- **B-18** — Discoverability posture on recorded supervision. Recorded supervision creates a permanent record of clinical decision-making. Melissa + counsel, before Phase 1 goes live.

---

## 10. COMPLY · Eligibility Verification Layer (`OPS-5`)

**Status: Scoping.** Design of record adopted 2026-08-07.

### 10.1 Objective

Satisfy Hard Gate #1 — eligibility verified before every service — with a record that survives an administrative appeal, regardless of which payer, which system, or which person performed the check.

### 10.2 Design of record

**One internal interface. Pluggable backends. Uniform record shape.**

[Certain] The verification *record* is the durable asset. The pipe that fills it is interchangeable and will change several times over the life of the practice. Attune owns the record; adapters own the transport.

```
                    ┌─────────────────────────────┐
   Manual entry ───▶│                             │
   Availity API ───▶│   Attune eligibility API    │──▶  EligibilityCheck
   Aggregator   ───▶│   (one internal interface)  │      (uniform record)
   EVS direct   ───▶│                             │
                    └─────────────────────────────┘
```

Adding a backend must never require changing the record, the UI, or the gate logic.

### 10.3 Backend adapters

| Adapter | Covers | Status | Notes |
|---|---|---|---|
| **Manual entry** | Everything | v1 — always available | Permanent fallback. Never remove it. |
| **SimplePractice native** | Whatever SP already supports | **Check first — B-21** | [Certain] SP has real-time eligibility built in. If it covers PerformCare, Attune stores the record and builds no transport at all. |
| **Availity API** | Commercial + CHIP | v2 | [Certain] 270/271 eligibility available. **Payer coverage unconfirmed for PerformCare — B-22.** Highmark's Medicaid lines did not transition to Availity. |
| **Aggregator API** | Long-tail payers | v2 alternative | Stedi, Optum/Change, pVerify, Opkit. [Likely] One REST API across many payers incl. state Medicaid, priced per transaction. Price against Availity before committing. |
| **EVS direct (PROMISe)** | PA Medicaid | v3 — gated | [Certain] EVS accepts HIPAA 270 inquiries and returns 271 responses; own software is permitted but **must pass PROMISe certification before gaining access.** Free alternatives exist: Provider Electronic Solutions software with batch EVS, and the AVRS line at 1-800-766-5387. |

### 10.4 Record shape

| Entity | Key fields |
|---|---|
| `EligibilityCheck` | id, case_code, payer, plan, service_date_covered, checked_at, checked_by, method (manual \| sp \| availity \| aggregator \| evs), outcome (eligible \| ineligible \| error), coverage_detail, evidence_ref, notes |

**Evidence handling.** [Certain] PA requires hard copies of the EVS printout to be maintained in the member's medical record. The artifact therefore lives in **SimplePractice or the document store** — Attune holds `evidence_ref`, a pointer, not the document. This preserves §2.2: Attune never becomes a second clinical repository.

### 10.5 Business rules

- **Monthly refresh is mandatory.** MA eligibility changes month to month; a check from last month does not cover this month's session.
- No `EligibilityCheck` row may be edited after creation — corrections are new rows. The history is the appeal defense.
- `method` is always recorded. A manual check and an API check are equally valid evidence; what is not valid is an unrecorded one.
- Outcome `error` is a distinct state from `ineligible` and must surface for retry, not be treated as a denial.
- A scheduling attempt without a current check surfaces as an exception — see §8.7 on how hard to make that gate.

### 10.6 Phasing

| Phase | Delivers |
|---|---|
| **v1** | Record + manual entry + monthly-refresh dashboard. Ships without any integration. |
| **v2** | First automated adapter — SimplePractice native if it covers PerformCare, otherwise Availity or an aggregator. |
| **v3** | EVS direct, contingent on PROMISe certification (B-23). Optional; may never be worth it. |

### 10.7 Open items

- **B-21** — Confirm what SimplePractice's built-in eligibility check already covers for PerformCare. May eliminate most of v2.
- **B-22** — Confirm Availity payer coverage for PerformCare / PA HealthChoices behavioral health before designing against it.
- **B-23** — Decide whether PROMISe certification for direct EVS access is worth the effort versus an aggregator or the free PES software.
- **Gate hardness.** Whether Attune *blocks* scheduling without a current check or merely flags it. [Likely] Start with flag, move to block once the data is trustworthy — a hard block on day one, fed by manual entry, will be routed around within a week.

### 10.8 Acceptance criteria

- Every service date for an MA client has a corresponding `EligibilityCheck` covering it.
- Adding a new backend adapter requires zero change to the record, the UI, or the gate logic.
- Monthly refresh dashboard surfaces every active client without a current-month check.
- `evidence_ref` resolves to an artifact stored in the chart, not in Attune.

---

## 11. PEOPLE · Time Entry (`OPS-6`)

**Status: Not started.** Tier 3. Extends OPS-1 rather than standing alone.

### 11.1 Objective

Log clinical administrative hours and other non-session work practice-wide, so hours worked are recorded at the time they happen rather than reconstructed at payroll.

### 11.2 Relationship to OPS-1

[Certain] This is not a separate system. OPS-1 already owns `Employee`, `LeaveEntry`, and `ProductivityWeek`. OPS-6 adds `TimeEntry` to the same schema and the same RBAC. Building it as its own module produces two sources of truth for the same person's week — the exact failure OPS-1 exists to fix.

### 11.3 The compliance line

[Certain] The moment time entries drive wages, this is a **wage-and-hour record**, not an ops convenience. FLSA requires accurate records of hours worked for non-exempt employees, and in a dispute the employer's records are the evidence. Two consequences:

- **Entries are append-only with an edit trail.** A supervisor adjusting a subordinate's hours must leave a visible record showing the original value, the change, who made it, and when. Silent edits to time records are the single worst thing this module could do.
- **Classification matters.** Exempt vs. non-exempt per employee drives whether overtime calculation is in scope. Record it on `Employee`. See **B-24**.

### 11.4 In scope

- `TimeEntry`: employee, date, category, minutes, note, source, created_at, plus an append-only `TimeEntryEdit` trail.
- Categories tuned to a clinical practice: documentation, supervision (given), supervision (received), case coordination, PCP/care coordination, intake calls, training, administrative, meetings, marketing/outreach, SAP evaluation, SAP coordination.
- Weekly per-person view: session hours (from OPS-1) alongside admin hours, so the ratio is visible.
- Manager view: admin-hour trend by category, by clinician.
- Export for payroll.

### 11.5 Why the ratio matters more than the total

[Likely] The useful output is not "how many hours" but **documentation time per session, per clinician**. A clinician averaging 45 minutes of documentation per 53-minute session has a template problem, a training problem, or a caseload problem — and that is invisible without this data. It also directly informs whether Care Aide is earning its $49/month per clinician (§7.10).

### 11.6 Out of scope

- Payroll disbursement. Same boundary as OPS-1.
- Clocking in and out. [Certain] Category-based logging, not punch-clock; punch-clock in a professional practice is a culture cost with no compliance benefit.
- Client-billable time capture — that lives in SimplePractice.

### 11.7 Design constraint

[Certain] Adoption is the entire risk. A time system requiring more than ~30 seconds per entry will be filled in on Friday from memory, which produces worse data than none — it looks authoritative and isn't. Default to a small set of categories, a one-tap "log 15 minutes of documentation" action, and no required note field.

---

## 12. PEOPLE · Hiring & Onboarding (`OPS-7`)

**Status: Not started.** Tier 4.

### 12.1 Objective

Replace the resume-in-inbox process with an internal applicant tracker built for clinical hiring, carrying a candidate from application through interview notes to a structured onboarding and marketing launch plan.

### 12.2 Why in-house

[Certain] BambooHR, Workday, and comparable platforms are priced for enterprise headcount. At four to seven employees the per-seat cost is not defensible, and none of them model the thing that actually matters here — **licensure, supervision eligibility, and payer credentialing** — which is exactly what a clinical practice needs to track and what a generic ATS treats as a custom field.

### 12.3 ⚑ The legal constraint that shapes the whole module

> **RANK AND SURFACE. NEVER AUTO-REJECT.**

[Certain] Automated screening that rejects candidates creates disparate-impact exposure under Title VII regardless of intent, and the regulatory environment for AI hiring tools is tightening — New York City requires bias audits for automated employment decision tools, Illinois regulates AI in video interviews, and Colorado has enacted broader AI-in-employment requirements. Pennsylvania has no such statute today, but federal law applies everywhere and state law changes.

**Build rules, non-negotiable:**

- The system **flags and ranks**; a human makes every advance/reject decision and that decision is recorded with the human's name.
- Screening criteria are **objective and job-related only**: license type and status, supervision eligibility, years in a named modality, availability, caseload capacity. Never inferred attributes.
- **No resume screening by LLM in v1.** [Likely] A model summarizing a resume is low-risk; a model scoring or ranking candidates is an automated employment decision tool. Keep AI on the summarize-and-extract side of that line, and log it as **B-25** before crossing it.
- Every screening decision is logged with the criterion applied — this is the audit trail that defends the process if it is ever challenged.
- **Retain applications for at least one year** per EEOC recordkeeping requirements, including rejected applicants. Do not build a delete button that undermines that.

### 12.4 Ingest — the realistic path

[Likely] Indeed and ZipRecruiter do not offer general-purpose APIs for pulling applications into a third-party system at small-employer tier. What they reliably do is **email you each application**.

| Path | Assessment |
|---|---|
| **Email ingest** | [Likely] The practical route. Applications land in a dedicated mailbox; Attune parses sender, role, candidate name, and attachment. Same mechanism as the appointment-ledger idea, and it works because job-board notification emails are highly structured. |
| **Job-board API** | [Guessing] May exist at partner tier. Confirm before assuming — **B-26**. |
| **Manual upload** | Always available. Fallback and the path for direct/referral applicants. |

**Do not build parsing against a format you have not seen.** Collect two weeks of real notification emails from each board first, then write the parser.

### 12.5 In scope

- `Candidate`: name, contact, source, role applied, applied_at, stage, owner.
- **Clinical qualification fields as first-class data**, not custom fields: license type (LCSW/LPC/LSW/LAPC/LMFT/psychologist), license status and number, expiration, supervision hours remaining, supervision eligibility, modalities, populations served, telehealth vs. in-office, availability, caseload capacity, languages.
- Pipeline stages: Applied → Screened → Phone screen → Interview → Reference/license check → Offer → Accepted/Declined.
- **Structured interview notes** — same question set per role, scored, so candidates are compared on the same basis. [Certain] This is both better hiring and better legal defense than free-form impressions.
- Resume/document attachment with access control.
- Rejection reason captured against objective criteria.
- **Onboarding plan generation** on acceptance (§10.6).
- Handoff to OPS-2: a hired candidate becomes a `Clinician` row with all six credential clocks started, and to OPS-1 as an `Employee`.

### 12.6 Onboarding and launch plan

On acceptance, generate a dated checklist from role and license type. For a clinical hire that includes: credentialing packet and CAQH · PerformCare roster addition · malpractice rider · exclusion screening (o1) before first paid day · license verification · SimplePractice account and Care Aide seat · Google Workspace account · policy attestations (OPS-2) · supervision assignment · caseload ramp schedule · **marketing launch** — website bio, matcher profile in MATCH, referral-source announcement.

[Certain] The exclusion screening item is not optional and must complete **before** the first paid day. Paying an excluded person makes every associated claim false.

### 12.7 Out of scope

- Payroll, benefits administration, performance reviews.
- Background check execution — record the result, don't run the check.
- Public job-posting management. Post on the boards; track here.

### 12.8 Data classification

[Certain] Applicant data is **not PHI** but is sensitive PII with its own retention rules and its own access wall. Same tier as OPS-1 HR data: owner and back-office admin only. Interview notes about a candidate are discoverable in an employment claim — write them accordingly, and say so in the UI.

### 12.9 Onboarding & Compliance Tracking (spreadsheet replacement)

**Status: In progress (Clynotion v0.14).** Design of record scoped 2026-08-18.

> **Naming note.** The practice module sheet is titled “OPS-2 · Onboarding & Compliance Tracking.” In this master document, **§6 OPS-2** remains the COMPLY Compliance & Credentialing Registry (practice calendar + six clinician clocks). This §12.9 module is the **HR Employee onboarding tracker** (legacy alias `OPS-2-onboarding` / handoff from OPS-7). Do not merge the two stores.

**Purpose.** Track each new hire’s onboarding progress and each employee’s ongoing clearance/license status in one admin view, replacing `H4C_Onboarding_Tracker.xlsx`. Encodes the Digital Onboarding Playbook: packet e-sign, W-4 + direct deposit via QuickBooks Workforce (status mirrors only), I-9 verified in person, PA clearances, insurance credentialing, and — for LSW hires — a clinical supervision agreement plus supervised-hours logging.

**Design principles**

- **PII minimization (hard rule).** Store *status + dates + short notes only.* Do NOT store SSNs, bank details, clearance document contents, or DOB. Drive links and QuickBooks completion flags only.
- **v1 = manual entry.** No SimplePractice or third-party APIs required to ship.
- **Conditional by license type.** LSW surfaces supervision fields; LCSW does not.
- **Compliance is ongoing.** Clearances and licenses expire; renewal dates surface on Home.

**Data model (shared Employee spine with OPS-1 — do not duplicate)**

- `Employee`: `licenseType` (LCSW|LSW|LPC|admin|other), `licenseNumber`, `licenseExpiry`, `supervisorId`, `startDate`, `driveFolderUrl`.
- `OnboardingRecord`: step statuses (`NotStarted|InProgress|Complete|NA`) including I-9 §1/§2, W-4/DD QuickBooks mirrors, Acts 151/34/114, Drive folder, welcome email, `supervisionAgreement` (LSW only).
- `ComplianceItem`: Act151|Act34|Act114|License|Other with issue/expiry (PA clearances default +60 months) and Drive link only.
- `SupervisionLog` (LSW only): date, duration, format, notes, signedByBoth.

**Computed:** `percentComplete`, `overallStatus`, `i9Section2Overdue` (startDate + 3 business days), `expiringSoon` (default 60 days), `supervisionHoursToDate` vs configurable PA LCSW target.

**Access:** Owner + back-office admin only. Employee-PII zone — separate from client/PHI.

**Migration seed:** Alex Mistovich (LCSW), Nathan Sterry (LSW), Kayleigh (LSW).

**Open questions (unchanged from module scope):** email vs in-app alerts; day-to-day owner; confirm PA Board supervised-hours target; license expiry for all staff vs new hires only; Drive links in-app (recommended: yes).

Full acceptance criteria and step list: `docs/modules/ops2-onboarding-compliance-scope.md`.

---

## 13. GROW · Marketing Workbench (`OPS-8`)

**Status: Not started.** Tier 4.

### 13.1 Objective

A practice-wide workspace for building marketing approaches and campaign plans with AI assistance, with explicit control over which model or tool is used for which purpose.

### 13.2 ⚑ The constraint that governs everything here

> **NO CLIENT INFORMATION ENTERS THIS MODULE. EVER.**

[Certain] Marketing content built from client stories, testimonials, outcomes, or session details is PHI use requiring specific authorization, and this is the single most common HIPAA violation in small-practice marketing. The module must make the safe path the easy path:

- Inputs are **practice-level and market-level only**: services, specialties, clinician bios, payer mix, referral sources, geography, competitor landscape, aggregate outcome data with no client-level granularity.
- No free-text field in OPS-8 accepts a client name, and the UI says so at the point of entry.
- [Certain] Client testimonials carry a separate problem: professional ethics boards restrict soliciting testimonials from current clients. Keep the module structurally incapable of collecting them.
- **A different BAA posture than the rest of Attune.** If no PHI enters, the model providers used here are not business associates for this purpose — which is precisely what makes multi-model selection viable. That property is only preserved by enforcing the rule above.

### 13.3 Multi-model selection

The requirement is model choice per purpose. [Likely] The clean design is a **provider abstraction mirroring OPS-5's adapter pattern** — one internal interface, swappable backends, uniform record of what was generated by what.

| Element | Design |
|---|---|
| Provider adapters | Anthropic, and others as added. Same interface. |
| Purpose profiles | Named configurations pairing a purpose with a default model and prompt — e.g. "long-form article," "referral one-pager," "campaign plan," "social copy." |
| Override | Per-generation model choice, always available. |
| Provenance | Every artifact records model, prompt version, and timestamp. |

**Two things to get right:**

- [Certain] **Any provider used here that is not under a BAA must be walled from the rest of Attune at the application layer**, not by convention. Same codebase, PHI in other modules, no-PHI in this one — that boundary needs enforcement, not discipline. See **B-28**.
- [Certain] Prompt development for *any* Attune module still happens on synthetic data. The Anthropic BAA excludes Console and Workbench (Appendix E.2); that rule does not relax because the module is marketing.

### 13.4 In scope

- Knowledge base of practice-level marketing inputs: services, specialties, clinician profiles, payer mix, referral relationships, geography, brand voice, positioning.
- Campaign objects: audience, channel, message, assets, dates, owner, status.
- AI-assisted generation against the knowledge base with model selection per purpose.
- Asset library with version history and provenance.
- Referral-source register — who refers, what volume, last contact.
- **Human review gate before publication.** Nothing generated publishes without a named approver.

### 13.5 Compliance review requirement

[Certain] Marketing claims by a licensed clinical practice are regulated. Anything asserting outcomes, efficacy, or superiority needs review against PA licensure board advertising rules before publication. Build the approval step as a required stage, not an optional one. **Melissa approves clinical claims** — this is a licensure matter, not an operations one.

### 13.6 Natural adjacency — SAP and referral marketing

[Likely] The strongest near-term use is not consumer marketing. It is **employer and TPA outreach for the DOT SAP line** — item 6 on the SAP priority list. That audience is B2B, involves zero client information, and needs exactly the kind of structured collateral this module produces. If OPS-8 gets built, build it for that first.

### 13.7 Out of scope

- Sending email campaigns or posting to social platforms directly. Generate and export; use existing channel tools to send. [Certain] A sending integration turns this into a system that transmits on the practice's behalf, with a different compliance profile.
- Website content management.
- Any client-facing communication.

---

## 14. BOOKS · QuickBooks & Financial Ops (`FIN-1`)

**Status: Not started.** Tier 3.

### 14.1 Objective

Connect the money to the operations. QuickBooks owns the ledger; Attune adds the operational context QuickBooks cannot see — which clinician, which payer, which service line, which claim is stuck.

### 14.2 What QuickBooks does not know

[Certain] QuickBooks sees a deposit. It does not see that the deposit is short because four claims were denied for a missing encounter form, or that the SAP line has better margin per hour than the MA line. That gap is the module.

| Question | QuickBooks | SimplePractice | Attune |
|---|---|---|---|
| What came in? | ✅ | partial | — |
| What was billed? | — | ✅ | — |
| What's denied and why? | — | ✅ (detail) | **rollup + pattern** |
| Margin by service line? | partial | — | **✅** |
| Revenue per clinician hour, including admin time? | — | — | **✅** (needs `OPS-6`) |
| Which denials trace to which compliance failure? | — | — | **✅** (needs `OPS-2`) |

### 14.3 In scope

- **QuickBooks Online API integration** — read-only in v1: accounts, deposits, expenses, P&L. [Certain] Read-only is the right v1; a write integration into the books is a category of risk with no matching benefit.
- **Service-line P&L**: MA outpatient, commercial, private pay, DOT SAP. Each has different economics and they are currently invisible to each other.
- **Denial register** from the SP billing report — reason code, payer, clinician, dollar value, age.
- **Denial-to-compliance mapping** — the connection that makes this worth building. A denial for a missing treatment plan is Hard Gate #2 failing. Feed that back into `OPS-2` audit sampling.
- **Claims aging** against the 365-day filing bar (`OPS-2` o12), with dollar value attached so it is prioritized by exposure, not by age alone.
- **Revenue per clinician**, and once `OPS-6` exists, revenue per clinician *hour* including admin time.
- **SAP line tracking** — cash/employer-pay, 80000-series codes, separate from all payer logic.

### 14.4 Out of scope

- Bookkeeping, reconciliation, journal entries, invoicing, payroll. QuickBooks and the accountant own all of it.
- Writing to QuickBooks in v1.
- Any client-level financial detail beyond what the claim requires.

### 14.5 Data model

| Entity | Key fields |
|---|---|
| `Denial` | id, claim_ref, payer, clinician_id, service_date, reason_code, amount, age_days, root_cause, resolved_at |
| `ServiceLine` | id, name, revenue_ytd, direct_cost_ytd, sessions, hours |
| `FinancialSnapshot` | id, period, source (qbo \| manual), revenue, expenses, ar_balance, captured_at |

### 14.6 The one number this module exists to produce

[Likely] **Net revenue per clinician hour, by service line, including administrative time.** No other system in the stack can compute it, and it answers the questions that actually drive the business: whether MA is worth the compliance overhead, whether SAP should be scaled, and whether a clinician is under-supported rather than under-performing.

That number requires `OPS-6` (time), `FIN-1` (revenue), and the SP session report together — which is the argument for the shared record spine in §3.2.

### 14.7 Open items

- **B-31** — QuickBooks Online API access and app registration.
- **B-32** — Chart of accounts structure: do service lines exist as classes in QBO today, or does Attune derive them?

---

## 15. The four hard gates

[Certain] Everything in the workflow serves these. If only four things are enforced:

1. **Eligibility verified before every service.** Denials are upheld for failure to check. With no authorization step in routine outpatient, this is the *only* pre-service gate. EVS printout is required evidence in an administrative appeal.
2. **A valid, signed treatment plan covering the date of service.** No plan, no claim. An expired plan makes every subsequent session unbillable.
3. **A signed encounter form for the encounter.** A missing form voids an otherwise perfect note.
4. **The adjunct screen.** No concurrent BH service making this outpatient a duplication. Asked at intake, re-asked at every plan review.

[Certain] Each can be lost silently, and each is recoverable only inside the **60-day administrative appeal window.** That window is the reason these are gates and not tasks.

**Also a hard gate at Phase 0:** confirm PerformCare membership **and county of residence** — rates vary by member county.

---

## 16. Document conflicts requiring resolution

Three live contradictions across current documents. None is cosmetic.

| # | Conflict | Documents | Resolution |
|---|---|---|---|
| X-1 | **Attune's PHI status.** Workflow says "No — case codes only." Master Scope §1.1 says Attune holds PHI. | MA Client Workflow vs. this document | **B-10.** Note the workflow is written *end to end* on the no-PHI assumption — case code at Phase 0, case codes only in the quarterly audit batch, case code at retention. Dropping the constraint means rewriting that document, not annotating it. |
| X-2 | **Where intake is first logged.** Workflow Phase 0 logs the referral in SimplePractice; OPS-3 (§5.3) has Attune generating the intake number *before* SimplePractice. | MA Client Workflow vs. OPS-3 | Decide, then correct the losing document. OPS-3's sequencing exists specifically to make the access clock machine-recorded; Phase 0 as written leaves it verbal. |
| X-3 | **Authorization burn-down.** Phase 2 eliminates authorization tracking entirely — no session cap, no burn-down. Phase 4 Weekly still lists "Authorization burn-down review across active cases." | MA Client Workflow, internal | [Certain] Stale row predating the CM-MS-003 finding. Delete it and replace with the adjunct re-screen (o13). |

---

> **THESE ARE OBLIGATIONS, NOT BUILD TICKETS.**
> Nothing here is satisfied by writing software. OPS-2 tracks them; people complete them. Owner and date columns are to be filled by Evan.

### A.1 Framing

[Certain] Two applications, two agencies, **run in parallel, not serially**:

| Track | Agency | What it gets you |
|---|---|---|
| **PROMISe enrollment** | DHS / OMAP | State MA enrollment + 13-digit PPID. Prerequisite for *payment*. |
| **In-plan expansion** | PerformCare (+ CABHC oversight) | Network admission + credentialing. Prerequisite for *being in network*. |

[Certain] PerformCare does **not** submit MA enrollment on the practice's behalf. Their enrollment assistance covers only OMHSAS supplemental service types (ICM, RC, BCM, FBMH, crisis) that Heart for Change does not provide.

[Certain] The in-plan application accepts a pending MA application ("If no MA number, has application been submitted?"). There is no reason to serialize.

> **⚑ SEQUENCING FINDING — verify before relying on A.1**
> [Certain] Supplemental 11/112 enrollment requires **PerformCare sponsorship before PROMISe** — the reverse of the assumed sequence. Confirm whether this applies to the standard MH-OP group enrollment or only to supplemental service types, because it inverts the filing order if it applies. Logged as **A-26**.

### A.2 Documentation prerequisites

| ID | Item | Status | Owner | Notes |
|---|---|---|---|---|
| A-18 | Obtain EIN letter (LTR 147C) | Not started | Melissa | [Certain] CP 575 is never reissued. PA PROMISe accepts **either CP-575 or LTR 147C** — interchangeable. Business & Specialty Tax Line 1-800-829-4933, Mon–Fri 7am–7pm local; press 1, 1, 3. Call 7–10am or 3–7pm; avoid Mondays. Ask for "Letter 147C, EIN Previously Assigned, faxed on this call." Fax same/next business day; mail 7–10 business days ([Likely] 4–6 weeks in practice). |
| A-19 | Verify IRS address of record matches current address | Not started | Melissa | [Certain] IRS releases only to the address of record. If the address changed since EIN issuance, file **Form 8822-B first** or the letter goes to a dead address. |
| A-20 | Verify legal name and address match between application and IRS record | Not started | Evan | [Likely] The most common PROMISe rejection is a name/address mismatch against the IRS record. |
| A-21 | File Form 2848 naming Evan for Heart for Change | Not started | Melissa | [Certain] Only a sole proprietor, partner, corporate officer, trustee, or a 2848 POA holder can request IRS documents. One-time fix removing the "Melissa makes every IRS call" bottleneck permanently. |

### A.3 Corporate compliance infrastructure

Marked **REQUIRED** on the in-network application. [Certain] None exist today. Build before filing.

| ID | Item | Status | Owner | Notes |
|---|---|---|---|---|
| A-1 | Named Corporate Compliance Officer | Not started | | Appointment must be documented. See §4.9 — do not name the person who owns billing oversight. |
| A-2 | Adopted Code of Conduct | Not started | | Drafted ≠ adopted; needs adoption date |
| A-3 | Written Corporate Compliance Plan | Not started | | Drives A-1/A-2 scope and OPS-2's final data model |
| A-22 | Written delegation of authority (Melissa clinical, Evan operational) | Not started | | Governance artifact |
| A-23 | Quarterly compliance committee **with minutes** | Not started | | [Likely] Minutes are the artifact auditors ask for and the one nobody has. Two people is fine. |
| A-24 | Confirm SP vendor turnaround for HIPAA Audit Log production | Not started | | See §4.6. Find out before a 30-day audit window, not during one. |

### A.4 Application content & eligibility

| ID | Item | Status | Owner | Notes |
|---|---|---|---|---|
| A-4 | Verify no service location is a home office | Not started | | [Certain] Explicit disqualifier, re-verified on application. A home-office site is dead on arrival. |
| A-5 | Write the needs / market analysis narrative | Not started | | [Certain] **Highest-probability failure point.** CABHC (Lancaster) can veto independently of PerformCare. Lancaster MH-OP is not an obvious access gap. Denial = ~1-year wait. Write it around differentiation, not "we want to accept Medicaid." |
| A-6 | Evaluate psychologist/psychiatrist attestation waiver | Not started | | [Likely] In-plan process is *waived* for LSW/LCSW/LPC/LMFT under psychiatrist or psychologist attestation — attestation + resume to the Lancaster AE, no expansion application, no county veto. If available, **this may delete A-5 entirely.** Confirm with Julie Merring / Jen Temple. |
| A-25 | Obtain emergent and urgent access-standard windows | Not started | | Blocks OPS-3 go-live. Routine (7 days) is known. |
| A-26 | Confirm whether PerformCare sponsorship precedes PROMISe for this enrollment type | Not started | | Inverts filing order if it applies. See §A.1. |

### A.5 PROMISe — CHIP closure and reapplication

[Certain] OMAP confirmed the CHIP-only enrollment must be closed and reapplied as combined MA + CHIP. [Certain] The portal shows no Terminate Enrollment link for CHIP-only accounts — expected, not a defect.

**Reference data:** Provider number `103727546-0001` · Entity Type: Group · Provider Type 11 · Melissa NPI `1982245940` · Address: 200 Willow Valley Sq, Suite 210

| ID | Item | Status | Owner | Notes |
|---|---|---|---|---|
| A-7 | Extract everything from the portal **before** closure | Not started | | [Certain] Portal access ends with the enrollment. CHIP-only accounts are already blocked from Reports/Trade Files. |
| A-8 | Quantify CHIP revenue exposure in the gap | Not started | | Near-zero = non-event. Real pediatric volume = voluntary go-dark of unknown length. |
| A-9 | Call CHIP MCO reps **before** closing | Not started | | [Likely] Commercial CHIP contracts (Capital Blue, Highmark, UPMC) sit downstream of PROMISe. Expect termination feeds and network drops. Re-credentialing is its own multi-month process OMAP will not mention. |
| A-10 | Get the verbal OMAP instruction in writing | Not started | | [Certain] Rep name, call reference number, date, written confirmation. "Someone on the phone told me" is worth nothing if this creates a duplicate entity record. |
| A-11 | Submit written disenrollment request | Not started | | `RA-ProvApp@pa.gov` · fax `717-265-8284` · PO Box 8045, Harrisburg PA 17105-8045. Include: entity name, provider number, FEIN, program type, requested effective date, authorized signature. |
| A-12 | Confirm transaction type for reapplication | Not started | | [Certain] The manual routes closures under two years to **Reactivation**, not New Application. Confirm which the system will accept or you are blocked on screen one. |
| A-13 | Confirm effective date / backdating policy | Not started | | Backdated to receipt or approval? That number *is* the coverage gap. |
| A-14 | Confirm whether the new application can be filed before closure finalizes | Not started | | Determines whether the gap is days or months. |
| A-15 | Confirm whether closure resets the revalidation clock | Not started | | |
| A-16 | Confirm MPI continuity | Not started | | [Likely] A new 9-digit MPI means re-registering the portal account, redoing EFT/ERA, and correcting the MA number downstream. |
| A-17 | Confirm whether specialty selections carry forward or are re-chosen | Not started | | If from scratch, a clean opportunity to get 112/116/117 right with no correction transaction later. |

### A.6 Specialty codes

[Certain] Specialty codes are **attestations of licensure or approval**, not a menu of services you would like to offer. A false attestation surfaces later as recoupment, not as a rejection letter.

| Code | Decision | Basis |
|---|---|---|
| 112, 116, 117 | **Keep** — already selected | Aligned to current licensure |
| 128 — D&A Intensive Outpatient (ASAM 2.1) | **Do not select** | [Certain] Requires DDAP license under 28 Pa. Code Ch. 704/709 |
| 115 — Family Based Mental Health | **Do not select** | [Certain] Requires OMHSAS approval |
| 122 | **Verify before selecting** | [Certain] Requires a licensed LMFT on staff |

### A.7 Operational findings — PerformCare

| Finding | Confidence | Implication |
|---|---|---|
| Routine outpatient MH requires **no prior authorization**; registration eliminated April 2012 per Policy CM-MS-003 | [Certain] | Removes an assumed workflow step. Only authorization risk is concurrent services triggering an "adjunct" classification. |
| **LSW and LAPC cannot bill MA under LCSW or LPC supervision** — only a licensed psychologist or psychiatrist may supervise for PerformCare billing | [Certain] | Confirmed against January 2025 attestation forms. Constrains hiring. |
| **LAPC is not listed on either supervision form** (PA created the credential March 2024) | [Certain] | Requires direct Account Executive confirmation before hiring an LAPC into a billable role. |
| Balance-billing MA members is prohibited — **including no-show fees** | [Certain] | Needs a written practice policy. |
| 42 CFR Part 2 procedures required if any SUD treatment occurs | [Certain] | Directly relevant given the DOT SAP line. |
| Record retention runs longer than clinical norms | [Certain] | Four years; drives OPS-4's persistence requirement. |
| Claims filing bar: 365 days | [Certain] | Seeded as obligation o12. |

**Staffing:** Heart for Change is adding two to three experienced LPC/LCSW clinicians with partial existing caseloads on hourly arrangements, which resolves the enrollment-lag risk. One candidate has 500 hours remaining toward LCSW licensure — track against the supervision constraint above.

**Panel management:** [Likely] Cap MA acceptance at a defined panel size for the first two quarters. Limits clawback exposure while documentation habits stabilize, and produces clean data on actual reimbursement per session before scaling.

### A.8 Boundary note — SAP line

[Certain] None of Appendix A touches the DOT SAP service line. SAP evaluations are employment-compliance services under 49 CFR Part 40 — not medically necessary treatment, not Medicaid-covered. DOT-mandated education generally is not either. Specialty code selection has **zero** bearing on the SAP line.

[Certain] If the rationale for Medicaid enrollment is "support the SAP treatment lane," that logic does not hold. If it is "broaden the core practice," it stands on its own.

### A.9 Key contacts

| Contact | Role | Reach |
|---|---|---|
| Julie Merring | PerformCare Lancaster Account Executive | `jmerring@performcare.org` |
| Jen Temple | PerformCare | |
| OMAP Provider Enrollment | Enrollment policy | 1-800-537-8862, **option 1** |
| IRS Business & Specialty Tax Line | EIN / 147C | 1-800-829-4933 |

[Certain] Do **not** use 1-800-433-4459 (OMHSAS behavioral health / supplemental service types) or 1-800-248-2152 (Gainwell Provider Assistance Center — claims and portal access, not enrollment policy).

### A.10 Timeline

[Guessing] 6–10 months to first billable Medicaid claim: 45-day in-plan review, plus monthly credentialing committee, plus PROMISe processing, plus the closure/reapplication cycle. Treat shorter estimates as optimistic.

---

## Appendix B — Open decisions log

| # | Decision needed | Owner | Blocking what |
|---|---|---|---|
| B-1 | Confirm the definition/boundary of "Attune" | Evan | Module register accuracy |
| B-2 | Named owner/maintainer for each module's data | Evan | All modules |
| B-3 | ~~Hosting platform fit for PII~~ — superseded by §1.1 | — | Closed |
| B-4 | Does back-office admin see bonus $ / comp? | Melissa | OPS-1 RBAC config |
| B-5 | SimplePractice CSV/API export availability | Evan | OPS-1 v2 |
| B-6 | Payroll handoff system + format | Evan | OPS-1 export spec |
| B-7 | Who is named Compliance Officer (A-1) | Melissa / Evan | A-2, A-3, entire MA filing |
| B-8 | Attestation-waiver path vs. group in-plan route (A-6) | Evan | May delete A-5 entirely |
| B-9 | Proceed with CHIP closure now, or wait? (A-8, A-9) | Evan | A-11 and everything downstream |
| B-10 | **Case codes: keep as discipline, or drop under the PHI reversal?** | Evan | OPS-2 and OPS-3 schema. Highest-cost decision to reverse later. |
| B-11 | Phone platform: Google Voice Standard vs. Quo | Evan | OPS-4 entirely; Grasshopper BAA gap persists until resolved |
| B-12 | Contracted developer vs. documented handoff path for Attune | Evan | Single-maintainer risk on a system holding audit evidence |
| B-13 | Which SimplePractice plan is active, and what is enabled | Evan | Confirms which OPS-2 gaps are real vs. already covered |
| B-14 | Compliance-officer / billing-oversight segregation (§4.9) | Melissa / Evan | A-1, OPS-2 RBAC |
| B-15 | ~~Hosting + database stack~~ — **RESOLVED: Neon Scale (Postgres) + Fly.io (app hosting)** | Evan | Closed. BAA execution tracked in Appendix E. |
| B-16 | **Deepgram BAA execution** — must cover audio, transcripts, and derived data | Evan | CLY-1 go-live. Sales-gated; start early. |
| B-17 | Deepgram deployment: cloud vs. self-hosted vs. VPC | Evan | CLY-1 architecture; interacts with B-15 |
| B-18 | Discoverability posture on recorded supervision | Melissa + counsel | CLY-1 Phase 1 go-live |
| B-19 | Care Aide transcript-retention setting (clinician / client / session level) | Evan | Care Aide enablement; 42 CFR Part 2 posture |
| B-20 | Review SimplePractice AI consent form against PA all-party consent and 42 CFR Part 2 | Melissa + counsel | Care Aide enablement |
| B-21 | What does SimplePractice's built-in eligibility check already cover for PerformCare? | Evan | OPS-5 v2 scope — may eliminate most of it |
| B-22 | Confirm Availity payer coverage for PerformCare / PA HealthChoices BH | Evan | OPS-5 adapter selection |
| B-23 | Is PROMISe certification for direct EVS access worth it vs. aggregator or PES software? | Evan | OPS-5 v3 |
| B-24 | Exempt vs. non-exempt classification per employee | Evan | OPS-6 — determines whether overtime calculation is in scope |
| B-25 | Whether AI may rank or score candidates (currently: no) | Evan + counsel | OPS-7 — automated employment decision tool exposure |
| B-26 | Confirm whether Indeed / ZipRecruiter offer usable application APIs at our tier | Evan | OPS-7 ingest design |
| B-27 | Applicant data retention schedule (EEOC floor is 1 year) | Evan | OPS-7 |
| B-28 | How non-BAA model providers are walled from PHI modules at the application layer | Evan | OPS-8 architecture |
| B-29 | Which model providers to support in OPS-8, and under what terms | Evan | OPS-8 |
| B-30 | Marketing approval workflow — who signs off on clinical claims | Melissa | OPS-8 publication gate |
| B-31 | QuickBooks Online API access + app registration | Evan | FIN-1 |
| B-32 | Chart of accounts: are service lines QBO classes, or derived in Attune? | Evan | FIN-1 service-line P&L |

---

## Appendix C — SimplePractice gap analysis

The source list from which OPS-2 derives. [Certain] All fourteen are satisfiable without client names in Attune; three require discipline, and one is deliberately excluded.

### C.1 What SimplePractice genuinely handles

- Note templates force-structured to capture medical-necessity elements, service duration, and interventions tied to treatment-plan goals
- Treatment plans with measurable objectives and review dates (Wiley Planners add-on for scaffolding)
- Auto-scored PHQ-9 / GAD-7 — MCOs increasingly want outcomes data at recredentialing
- E-signed intake packet: NPP, consent, telehealth consent, ROIs, financial agreement
- **Real-time eligibility verification** — non-negotiable for MA, where eligibility flips monthly
- Claims/ERA, role-based permissions, activity/audit log, supervisor co-signature workflow, BAA on request

### C.2 What it does not fill — the build list

| Gap | Why it matters | PHI-free? |
|---|---|---|
| Monthly exclusion screening (OIG LEIE, SAM.gov, PA precluded list) — every employee, contractor, vendor | [Certain] Paying an excluded person makes every associated claim false. Must be monthly and documented. | Yes — staff/vendor names, not clients |
| HIPAA Security Risk Analysis + security policies | SP's compliance covers their platform, not your devices, network, or workforce | Yes — documents |
| Ownership & control disclosure (42 CFR 455.104), kept current | Required at enrollment, revalidation, and on change | Yes |
| License/credential expiration + 5-year revalidation tracking | Lapse = retroactive claim denial | Yes |
| BAA inventory for every PHI-touching vendor | | Yes |
| Record retention schedule per MA/MCO terms | Longer than clinical norms | Yes |
| Training log and attestations | | Yes |
| Compliance committee minutes | | Yes, if cases discussed by code |
| 42 CFR Part 2 procedures | Relevant given the DOT/SAP line | Yes — policy |
| Written prohibition on balance-billing MA members | [Certain] Includes no-show fees | Yes — policy |
| **Internal chart audit program** (5 charts/clinician/quarter, scored) | Element 6. Auditors ask for results, not the policy | Yes, **with a case-code convention** |
| **Grievance/complaint log with MCO timelines** | | Yes — **code + category + dates; narrative stays in SP** |
| **Coordination-of-care documentation with PCP** | Commonly audited, commonly missing | **Do it in the chart.** Attune reports aggregate only |
| **Overpayment / self-disclosure log** | | Yes — claim number, not client name |

### C.3 The crosswalk rule

[Certain] Under the original no-PHI design, the case-code crosswalk lived in SimplePractice or a separate secure store and **never** in Attune. That single rule held the architecture together, and it is the one a well-meaning developer breaks first.

Under the PHI reversal (§2.1), the rule's status is **pending decision B-10**. Until B-10 resolves, hold the rule and write it into the repo as a scope statement.

---

## Appendix D — Related artifacts

| Document | Purpose | Status |
|---|---|---|
| HFC PerformCare Documentation Compliance Spec | Progress note required fields, treatment plan requirements, encounter form requirements, intake content, procedural controls. Built from the Program Integrity/SIU documentation training. | Referenced, not in hand |
| **HFC MA Client Workflow** | Intake through discharge with system ownership and handoff types. Phases 0–5, cyclical maintenance, crossing budget, four hard gates. | **In hand.** Carries conflicts X-1, X-2, X-3 (§8). |
| Plain-English enrollment guide | Written for the admin/biller. | Referenced, not in hand |
| `attune_compliance_registry.jsx` | OPS-2 prototype, 953 lines. Panels: Due now · Intake log · Clinicians · Screenings · Audits · Events. Storage key `hfc-compliance-registry-v1`. | **In hand.** Model validator only — environment-scoped storage, not production. |

**Prototype fidelity note:** [Certain] The prototype's data shape is `{ clinicians, screenings, obligations, audits, events, intakes }`. The append-only audit log with as-of reconstruction is **not** in it and cannot be — that is a database design. It is the first thing to add in Cursor and the hardest to retrofit.

### D.1 Tool knowledge

[Certain] The PerformCare provider manual PDF truncates at approximately page 35 regardless of requested token limit — **Chapter XI and Appendices S and T are not reachable by direct PDF fetch.** Workaround: PerformCare's policy library hosts individual policy PDFs (e.g., CM-MS-003 for authorization procedures) that fetch cleanly.

---

## Appendix E — BAA register

[Certain] Without a signed BAA, sending PHI to a vendor is not permitted under HIPAA regardless of how secure the vendor is. Every row below is a hard prerequisite, not a nice-to-have. Tracked as OPS-2 obligation o9 (annual review).

### E.1 Register

| Vendor | Role | BAA path | Status | Owner |
|---|---|---|---|---|
| **Google Workspace** | Mail, Calendar, Drive, Vault, Voice | Accept in Admin console | | Evan |
| **SimplePractice** | PMS + Care Aide add-on | Existing SP BAA; **separate AI Addendum to ToS** applies to Care Aide | | Evan |
| **Deepgram** | Speech-to-text (CLY-1) | Sales/enterprise agreement — request via account team | | Evan |
| **Anthropic** | Claude API — LLM processing | Accept in Console; requires HIPAA-enabled organization | | Evan |
| **Neon** | Postgres (Scale plan) | Self-serve: enable HIPAA at org level, accept BAA, then per project | | Evan |
| **Fly.io** | Application hosting | Compliance Package incl. HIPAA/BAA docs; pre-signed, activates on countersignature | | Evan |

### E.2 Coverage traps — read before executing

**Anthropic — the sharpest one.** [Certain] The BAA covers only the organization that accepted it, and **excludes Workbench, Claude Console, Cowork, and beta features.** Console and Workbench being outside coverage is the trap that matters here: prompt iteration against real transcripts in Console is a HIPAA violation even with a signed API BAA. [Certain] Claude Code is covered **only with zero data retention enabled, on qualified accounts** — enabling HIPAA readiness alone does not bring it under the BAA. Note also that Covered Models require 30-day retention and are unavailable with ZDR, so the two configurations are mutually exclusive.

> **⚑ BUILD RULE**
> Prompt development uses synthetic data only. Real transcripts and real client data go through the API, from the application, under the HIPAA-enabled organization — never through Console, Workbench, or a personal Claude plan. Write this into the repo scope statement alongside the case-code rule (B-10).

**Neon.** [Certain] Free and Launch projects are **not** HIPAA compliant and must not hold PHI — Scale is the floor. Enabling HIPAA is org-level first, then per project; **once enabled on a project it cannot be disabled**, and enabling restarts all computes. [Certain] HIPAA support is currently at no additional cost on Scale, with a **15% invoice surcharge** coming once billing begins — budget for it.

> **⚑ BRANCHING IS THE RISK**
> Neon's branching is its best feature and the most likely PHI leak in this stack. A dev branch off production is a full copy of PHI in a non-production environment. [Certain] Neon recommends anonymization in non-production branches. Make that a hard rule, not a recommendation, and check Neon's subprocessor list as part of o9.

**Fly.io.** [Certain] BAA comes via the Compliance Package, requested from the dashboard; it is pre-signed by Fly and activates on your signature. [Likely] Priced around $99/month as a compliance add-on. Required architecture: **production must be a separate Fly organization from test and staging**, since organizations are the access-control boundary.

**Deepgram.** Sales-gated, not self-serve. [Certain] The BAA must explicitly name **audio recordings, transcripts, and derived data** — see §7.3.

**Google Workspace.** The BAA covers only Google's designated HIPAA-included services. Confirm Gmail, Calendar, Drive, and Vault are all in scope for your edition before routing client-notification email or calendar data into Attune.

### E.3 Vendors not yet on the list — check before they surprise you

| Candidate | Why it becomes a business associate |
|---|---|
| **Error monitoring** (Sentry, etc.) | Stack traces carry PHI in variables. The most commonly missed BAA in any healthcare build. |
| **Cursor** | If the codebase contains PHI in test fixtures, logs, or seed data, the AI features index it. Use synthetic fixtures only. |
| **Transactional email** (Resend, Postmark, etc.) | Any Attune-generated notification naming a client. |
| **Backup / object storage** | Wherever transcripts and pre-amendment note archives land. |
| **Availity** | If the API path in Appendix F is ever built. Portal-only use is under the payer relationship, not yours. |


---

**Prepared by:** Evan, COO
**Status:** Draft for review
