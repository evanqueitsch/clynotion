# Clynotion — Module Scope: OPS-2 · Onboarding & Compliance Tracking

**Module type:** Internal admin (HR/Operations)
**Access tier:** Owner + back-office admin only. Clinicians and the public matcher/analytics have NO access. Sits in the same access-walled zone as OPS-1; fully separate from client/PHI stores and the anonymous visitor-analytics store.
**Status:** Scoped 2026-08-18. Replaces the standalone onboarding spreadsheet (`H4C_Onboarding_Tracker.xlsx`).
**Relationship to OPS-1:** Shares the `Employee` entity with OPS-1 (PTO/Productivity/Bonus). Do NOT duplicate employee records — OPS-2 attaches onboarding/compliance data to the same employee.

---

## 1. Purpose

Track each new hire's onboarding progress and each employee's ongoing compliance status in one admin view, replacing the spreadsheet nobody remembers to update. Encodes the onboarding process built 8/18/26 (see the Digital Onboarding Playbook): packet e-signed via Acrobat, W-4 + direct deposit via QuickBooks Workforce, I-9 verified in person, PA clearances, insurance credentialing, and — for LSW hires — a clinical supervision agreement plus supervised-hours logging.

## 2. Design principles

- **PII minimization (hard rule).** Store *status + dates + short notes only.* Do NOT store SSNs, bank details, clearance document contents, or DOB. Source documents live in Google Drive and payroll data lives in QuickBooks — Clynotion holds status flags and, at most, a link to the Drive folder.
- **v1 = manual entry.** Admin updates statuses. No dependency on SimplePractice or third-party APIs to ship. QuickBooks/Drive are referenced by status + link, not integrated in v1.
- **Conditional by license type.** LSW hires surface supervision fields (agreement + hours log); LCSW hires do not.
- **Compliance is ongoing, not one-time.** Clearances and licenses expire; the module tracks renewal dates and surfaces alerts, so it stays useful after onboarding completes.

## 3. Data model

**Employee** *(shared with OPS-1 — reference, do not duplicate)*
- Add: `licenseType` (LCSW | LSW | LPC | admin | other), `licenseNumber`, `licenseExpiry`, `supervisorId` (nullable), `startDate`, `driveFolderUrl`.

**OnboardingRecord** *(one per hire)*
- `employeeId`
- Step statuses, each enum `NotStarted | InProgress | Complete | NA`:
  `offerSigned`, `employeeInfoForm`, `hipaaAgreement`, `i9Section1`, `i9Section2Verified`, `w4_quickbooks`, `directDeposit_quickbooks`, `credentialingApp`, `clearanceChildAbuse_act151`, `clearanceCriminal_act34`, `clearanceFbiFingerprint_act114`, `driveFolderCreated`, `welcomeEmailSent`, `supervisionAgreement` *(LSW only)*
- `notes` (short free text), `lastUpdatedBy`, `lastUpdatedAt`

**ComplianceItem** *(renewable, one per tracked credential/clearance per employee)*
- `employeeId`, `type` (Act151 | Act34 | Act114 | License | Other), `issueDate`, `expiryDate`, `renewalIntervalMonths` (default 60 for PA clearances), `documentDriveUrl` (link only)

**SupervisionLog** *(LSW only)*
- `superviseeId`, `supervisorId`, `date`, `durationMinutes`, `format` (InPerson | Telehealth), `notes`, `signedByBoth` (bool)

## 4. Computed / derived

- `percentComplete` per hire = (Complete + NA steps) ÷ applicable steps. Supervision step is applicable only for LSW.
- `overallStatus` = NotStarted | InProgress | Complete.
- `i9Section2Overdue` = true if `i9Section2Verified` ≠ Complete AND today > startDate + 3 business days.
- `expiringSoon` list = any ComplianceItem or license within N days of expiry (N configurable, default 60).
- `supervisionHoursToDate` = sum(SupervisionLog.durationMinutes) per supervisee, with progress toward the PA LCSW requirement (target value configurable — confirm current PA Board number).

## 5. Business rules

- LSW `licenseType` → `supervisionAgreement` step and SupervisionLog become required/visible; LCSW → hidden/NA.
- `w4_quickbooks` and `directDeposit_quickbooks` are **status-only mirrors** of QuickBooks Workforce completion — Clynotion never stores the underlying SSN/bank data.
- PA clearances auto-set `expiryDate` = `issueDate` + 60 months on entry.
- Completing all applicable steps flips `overallStatus` to Complete and timestamps it.

## 6. Access & security

- Role-gated to Owner + Back-office admin. No clinician or public access.
- Stored in the employee-PII zone (same wall as OPS-1), separate from client/PHI and from anonymous analytics.
- Store links to Google Drive folders/documents, never the documents or their sensitive contents.
- All writes via server-side validated ingestion (mirror the analytics/OPS-1 pattern); no direct client writes to sensitive tables.

## 7. Views (UI)

- **Roster view:** one row per hire — name, title, license type, start date, % complete, overall status, overdue/expiry badges. (Mirrors the delivered spreadsheet.)
- **Hire detail:** all step statuses with dropdowns + dates + notes; conditional supervision panel for LSWs; link to Drive folder.
- **Compliance dashboard:** upcoming clearance/license expirations and overdue I-9 Section 2 across all staff.
- **Supervision log (LSW):** entries + running hours toward LCSW.

## 8. Integrations (post-v1, optional)

- Google Drive: store + open the per-employee folder link.
- QuickBooks: status reference only (no data pull required for v1).
- Notifications: email/in-app alerts for expiring clearances/licenses and overdue I-9 Section 2.

## 9. Acceptance criteria

- Reproduces the standalone tracker's per-hire status view and `percentComplete` exactly.
- Supervision fields appear only for LSW hires.
- I-9 Section 2 overdue flag and clearance/license expiry alerts compute correctly.
- No SSN, bank, DOB, or document contents are ever persisted.

## 10. Migration

- Seed with current hires: Alex Mistovich (LCSW), Nathan Sterry (LSW), Kayleigh (LSW). Import statuses from `H4C_Onboarding_Tracker.xlsx`.

## 11. Open questions

- Do expiry/overdue alerts email the admin, or in-app only (v1)?
- Who owns status updates day-to-day (Evan, Melissa, future admin)?
- Confirm the current PA State Board supervised-hours target for the SupervisionLog progress metric.
- Track license expiry for all staff or new hires only?
- Store Drive folder links in Clynotion (recommended: yes, link only)?
