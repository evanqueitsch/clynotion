# Access roles

Roles are stored per practice in the encrypted file `ATTUNE_ACCESS_DATA_PATH`
(production: `/data/access_roles.enc`). Permissions are generated from the
catalog: each business unit is `unit:{id}` and each tool is `tool:{id}`.

Seeded roles are Intake, Billing, Clinician, Clinical Supervisor, and Admin.
The first four start with only `tool:time_entry`. Admin has every grantable
permission, including role management and the payroll dashboard. Admin does
not include post-cutoff correction.

Break-glass owners are the dev user `alice`, `ATTUNE_HR_ADMIN_EMAILS`, and
`ATTUNE_OWNER_EMAILS`. A roster `default_role` of admin does not grant this.
Someone with no assignment sees a signed-in empty state and cannot call
product APIs.

`capability:payroll_correct` is not a role grant. It is attached only to
`msmith@heart4change.org`. That account also receives `tool:time_entry` and
`capability:payroll_dashboard`, so Melissa can open the payroll week without
being an Admin.

Weekly hours enablement stays a separate checklist. A role with Time Entry
does not add someone to that list.

Scheduled reminder mail is unchanged by this and stays off until an Admin
turns on the settings checkbox. No worker is deployed with this change.

## Reset proposal (not implemented)

An owner-only action could copy selected encrypted files to a timestamped
backup on the existing volume, then replace only the chosen stores with empty
encrypted documents. It must never rotate `ATTUNE_DATA_ENCRYPTION_KEY`, detach
the volume, or clear the Gmail send grant inside `workspace_tokens.enc`.
The action does not exist yet and must not be run until the inventory is
confirmed.
