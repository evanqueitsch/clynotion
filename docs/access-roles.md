# Access roles

Roles are stored per practice in the encrypted file `ATTUNE_ACCESS_DATA_PATH`
(production: `/data/access_roles.enc`). Permissions are generated from the
catalog: each business unit is `unit:{id}` and each tool is `tool:{id}`.

Seeded roles are Intake, Billing, Clinician, Clinical Supervisor, and Admin.
The first four start with only `tool:time_entry`. Admin has every grantable
permission, including role management and the payroll dashboard. Admin does
not include post-cutoff correction.

Break-glass owners are the dev user `alice`, `ATTUNE_HR_ADMIN_EMAILS`,
`ATTUNE_OWNER_EMAILS`, and the verified owner mailbox
`operations@heart4change.org`. A roster `default_role` of admin does not
grant this. The live app has no owner-email env list. The shared mailbox
`hr@heart4change.org` is a roster admin and does not receive the Admin role.
Supervisor and supervisee roster roles are not copied onto the new roles.
Someone with no assignment sees a signed-in empty state and cannot call
product APIs.

The first time the practice-hfc role file is created, the owner mailbox is
recorded as Admin. Clearing that assignment later does not remove break-glass.

`capability:payroll_correct` is not a role grant. It is attached only to
`msmith@heart4change.org`. That account also receives `tool:time_entry` and
`capability:payroll_dashboard`, so Melissa can open the payroll week without
being an Admin.

Weekly hours enablement stays a separate checklist. A role with Time Entry
does not add someone to that list.

Scheduled reminder mail is unchanged by this and stays off until an Admin
turns on the settings checkbox. No worker is deployed with this change.

## Reset proposal (not implemented)

A read-only inventory on 11 Nov 2026 found these files on the attached volume
and nothing else: `clinicians.enc`, `due.enc`, `hr_time.enc`, and
`workspace_tokens.enc`. Sessions, ingest, intake, eligibility, credentials,
onboarding, and the role file are not on the volume. The audit log is not
stored on disk. The encryption key and the operations mailbox grant were not
modified.

An owner-only action, not built here, could copy one chosen file to a
timestamped backup on the same volume and then replace that file with an
empty encrypted document. It would be scoped per store. It must not rotate
`ATTUNE_DATA_ENCRYPTION_KEY`, detach the volume, or rewrite
`workspace_tokens.enc`. Clearing `hr_time.enc` would drop the one enabled
participant, the locked 6 Nov 2026 week, and the completed self-test record.
Clearing `due.enc` would drop the seeded home clocks (43 open, 12 done).
Clearing `clinicians.enc` would drop the five Workspace roster people.
None of that has been run.
