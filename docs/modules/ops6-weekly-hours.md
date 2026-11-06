# Weekly hours collector

Legacy alias: OPS-6. This is the behavior that shipped. It replaces the older “not started / daily categories” note in `docs/master-scope.md` §11 for this phase. Daily category ratios, session hours, payroll export, PTO, and clock-in/out are not part of this slice.

The app stores employee hours only. It does not store client names, session notes, or clinical records. This document does not make a legal, FLSA, or BAA claim.

## What staff do

Hourly staff are the people an HR admin or Melissa checks on the settings screen. The checklist is the current practice roster (people imported from Workspace who have an email). A later hourly/salaried flag on the roster can replace that checklist; the stored participant list is just emails.

Each person enters **one total for the pay week**, as whole hours plus minutes (0–59). The stored shape is a list of `{category, minutes}` lines. The only category today is `total` (“Weekly total”). Adding client vs supervision later means allowing more category ids and a screen; raw minutes already live on lines, and payable rounding stays a pure function of those minutes. v1 rounds the **sum** of the lines. Per-category rounding would be a separate decision.

A blank week is **missing**. An explicit `0` hours and `0` minutes is **submitted**. Missing is never treated as zero in the admin view, the CSV, the summary, or the Friday reminder.

## Pay week and clock

The pay week is Saturday 00:00 through Friday, paid that Friday. Every schedule instant is `America/New_York`, including daylight-saving transitions.

The default employee cutoff and the summary are the same moment: **Friday 11:30 AM ET**. Reminders default to:

- Thursday 9:00 AM ET, subject `Weekly hours are due tomorrow`, to every active selected person
- Friday 8:00 AM ET, subject `Enter your weekly hours today!`, only to selected people who have not submitted (including people who saved an explicit zero — they are not reminded)

Both messages include the Saturday–Friday dates, the deadline, this sentence, and `https://clynotion.com/#time`:

> Include the hours you expect to work Friday afternoon. The cutoff is before the end of Friday, so enter expected Friday afternoon hours before the deadline. Discrepancies after cutoff go to Melissa for correction.

The same sentence is the entry helper in the app. Day and time are editable. The two subject lines are not. Keep the first reminder on the day before cutoff and the day-of reminder on cutoff day before the cutoff; the settings screen warns when that stops being true.

Spring-forward gaps and fall-back overlap hours follow Python `zoneinfo` (`fold=0`, the first occurrence). The default morning times are not in those hours. A configured time that lands in the spring-forward gap is evaluated at the instant zoneinfo returns for that label.

## Cutoff and who may edit

The server locks the week when the clock reaches the cutoff, on any read, save, settings save, or tick. A late scheduler does not leave the week open.

Before cutoff, only that employee can save or update their own row, and only if they are an active selected person. They can read their own history. They cannot read anyone else’s row.

After cutoff, **only `msmith@heart4change.org`** can enter a missing row or change one, and only for people who were on the roster snapshot taken when the week locked. Generic HR admins, including Evan, do not get that right. Melissa does not get the rest of HR (onboarding stays HR-admin only).

Changing the summary recipient does not change who may correct hours. The corrector address is not a setting.

A settings save locks any week whose cutoff has already passed **under the previous settings**, then applies the new settings. A later cutoff time does not reopen a locked week. The locked week keeps the participant names, the summary recipient, and the deadline it closed with. Later roster or settings edits do not rewrite that week.

Melissa’s create/update writes an audit row: before lines and minutes (null when she is filling a missing row), after lines and minutes, actor id and email, timestamp, and `late: true`. Her correction does not send another email.

## Rounding

Raw minutes are stored exactly. Payable minutes are `15 * ceil(raw / 15)`, implemented as `((raw + 14) // 15) * 15`.

| Raw | Payable | Decimal hours |
|---|---|---|
| 0:00 | 0:00 | 0.00 |
| 5:00 | 5:00 | 5.00 |
| 5:01 | 5:15 | 5.25 |
| 5:14 | 5:15 | 5.25 |
| 5:15 | 5:15 | 5.25 |
| 5:16 | 5:30 | 5.50 |

Decimal hours are the payable minutes divided by 60, two places.

**Cap:** a week cannot exceed 168:00 (10,080 minutes). That is every minute in seven days, so a typo cannot store an impossible week. It is not an 80-hour policy cap. 80 hours and any larger total through 168:00 are accepted.

## Admin output

HR admins and Melissa can open the week, sorted by name: employee, raw total, rounded payable total, decimal hours, and the missing list. CSV download uses the same rows. Missing cells are blank, not `0`. Names that start with `=`, `+`, `-`, `@`, or a tab are prefixed with a quote so a spreadsheet does not treat them as formulas.

The summary email goes to the recipient snapshotted at lock (default `msmith@heart4change.org`), with the same figures, the missing list, the CSV attached, and `https://clynotion.com/#time-admin?week=YYYY-MM-DD`.

Preview and “Preview reminders” render that copy and return `sent: false`. They do not call the mailer.

## API

All practice ids come from the signed-in user. Another practice’s ids 404.

| Method | Path | Who |
|---|---|---|
| GET | `/hr/time/me` | Any signed-in user. Own week, own history, helper text. |
| POST | `/hr/time/entries` | Own row before cutoff. Melissa, after cutoff, may pass `for_email` for someone on the locked roster. |
| PATCH | `/hr/time/entries/{id}` | Same rules for an existing row. |
| GET | `/hr/time/week?week_ending=` | HR admin or Melissa. |
| GET | `/hr/time/week.csv?week_ending=` | HR admin or Melissa. |
| GET/PUT | `/hr/time/settings` | HR admin or Melissa. |
| POST | `/hr/time/settings/preview?week_ending=` | HR admin or Melissa. Does not send. |
| POST | `/hr/time/settings/test-reminder` | HR admin or Melissa. Body `{slot: "first"\|"due", week_ending}`. Does not send. |
| POST | `/internal/time/tick` | `Authorization: Bearer $CRON_SECRET` or `X-Cron-Secret`. No user session. |

`week_ending` is the Friday (`YYYY-MM-DD`).

## Who sees it

Nobody is enabled until an HR admin or Melissa checks them on Weekly hours settings and saves. The home tile follows that choice:

- Admins and Melissa always get **Weekly hours settings**, including the ability to enable themselves.
- A selected person gets **Enter weekly hours**.
- Everyone else sees the tool as not enabled and gets no entry link. The API also rejects their save.

The settings screen lists each roster person as Enabled or Not enabled and states that mail is off.

## Storage

`ATTUNE_HR_TIME_PERSISTENCE=file` writes one Fernet blob (the same `ATTUNE_DATA_ENCRYPTION_KEY` as the rest of the app) to `ATTUNE_HR_TIME_DATA_PATH`. The production image sets that to `/data/hr_time.enc` on the existing `attune_data` volume and sets `TIME_MAIL_MODE=off`. Real mode refuses to boot if hours would be stored only in memory, or if an existing hours file cannot be decrypted. It does not create or replace the encryption key.

Fly runs one app process on one machine (`min_machines_running = 1`, the deploy workflow scales count to 1) with the volume mounted at `/data`. `flock` on that file is valid for that topology. A second machine is not part of this deploy.

Writes take an exclusive `flock`, reload from disk, then replace the file via a temp file in the same directory, `fsync`, and `os.replace`. A failed replace leaves the previous file in place. If the file cannot be decrypted, the process refuses the request and does not overwrite it.

That lock is for multiple processes on **one machine**. The current Fly app is one machine with the `attune_data` volume. It is not a multi-host lock.

Dev and tests stay on `memory` and do not need a key. A memory store will not survive a restart; production file mode will, as long as the encryption key stays the same.

The global supervision audit log is not given hour totals. The edit trail lives in this encrypted file.

## Mail and the tick

Default `TIME_MAIL_MODE` is `off`. The tick still applies cutoffs and sends nothing, and it does not mark the week’s messages as sent. Turning Gmail on later can still send a message whose window is open.

Gmail is optional and send-only: a service account with domain-wide delegation impersonates `TIME_MAIL_SENDER` (intended `operations@heart4change.org`) with scope `https://www.googleapis.com/auth/gmail.send`. Tokens stay in process memory. Secrets are not written to logs, API responses, or the UI. The settings screen shows only whether a service-account value is present, the sender address, and whether the cron secret is set.

The checked-in Worker (`deploy/time-tick-worker`) cron is `* * * * *` (every minute). Due times are compared to the configured minute in Eastern Time. A 15-minute poll would miss any other minute, so that cadence is not used. The UI accepts any `HH:MM`.

Each practice/week/kind/recipient has one delivery row:

1. The tick locks due weeks, then **claims** a row (new token, 120-second lease) and flushes **before** calling Gmail.
2. A second tick sees the claim and does not send.
3. A definite Gmail rejection (`4xx`/`5xx`, connection refused before the send left) is `failed` and may retry up to 3 times, after 5, 15, then 45 minutes.
4. A read timeout or a dropped response after the send was attempted is `uncertain` and is **not** retried. The message may or may not have been accepted.
5. A config or token failure (`sa_missing`, token HTTP errors, and similar) is retried on the 5-minute backoff and does **not** use up the 3-attempt budget, so fixing the key still sends. The tick does not claim at all when the sender is obviously not configured.
6. If the process dies after a claim and before the result is saved, the lease expiry does **not** send again. The row becomes `uncertain` with `claim_expired_unconfirmed` and stays visible on the admin week. An operator has to resolve it later; this build has no resend button.
7. Reminders are not sent after cutoff. A failed reminder whose window has closed is cancelled. The summary stays due for the current week and the previous week (not older), and only if someone was on the list. An empty participant list does not send a summary.
8. A Friday reminder claimed for someone who submits before the send is cancelled instead of sent. The summary body is rebuilt at send time so a last-second save is included.

This is not exactly-once delivery. Network loss, a crash between accept and disk, and provider behavior can still duplicate or drop a message. The product does not promise inbox delivery, Worker uptime, or a free Cloudflare cron.

Melissa’s own edits never enqueue mail.

## What works with no external setup

With dev login (`alice` / `alice-pass`) and no Google, Gmail, or cron secret:

- Settings, the employee form, history, the admin week, CSV, preview, and reminder preview all work.
- Cutoff, Melissa-only correction, rounding, and practice isolation are enforced.
- Nothing is emailed. `/internal/time/tick` returns 503 until `CRON_SECRET` is set, and even then sends nothing until `TIME_MAIL_MODE=gmail`.

Synthetic roster fixtures exist only in tests. A real dev roster starts empty until Workspace import (mock directory in dev auth).

## Operator setup

Do this only when you intend to send mail. Do not put the secret values in git, logs, or screenshots.

| Name | Required? | Purpose | Where it comes from |
|---|---|---|---|
| `GOOGLE_ALLOWED_DOMAINS` | Required for Workspace sign-in (existing) | Restricts Google sign-in. For Heart for Change set `heart4change.org`. | Your domain. Already read by `app/google_oauth.py`. An empty list allows nobody. |
| `ATTUNE_HR_ADMIN_EMAILS` | Optional | Extra HR admins (Evan) if they are not roster role `admin`. Does **not** grant post-cutoff edits. | Your own list. |
| `ATTUNE_HR_TIME_PERSISTENCE` | Production: set `file` | Keeps hours across restarts. | — |
| `ATTUNE_HR_TIME_DATA_PATH` | Optional | Encrypted file path. On Fly, a path under `/data`. | — |
| `ATTUNE_DATA_ENCRYPTION_KEY` | Required in real mode (existing) | Encrypts this file. | `python -c "from app.crypto import generate_key; print(generate_key())"` — store it in the host secret manager. Do not commit it. Losing it makes the file unreadable. |
| `TIME_MAIL_MODE` | Optional, default `off` | `gmail` is the only mode that sends. | — |
| `TIME_MAIL_SENDER` | Required only for `gmail` | Mailbox the service account impersonates. Use `operations@heart4change.org`. | Google Admin → Directory → Users. The address must be a real Workspace user. |
| `GOOGLE_SA_JSON` | Required only for `gmail` | Service-account JSON, either a file path or the JSON string. | Google Cloud Console → IAM → Service accounts → Keys → Add key → JSON. Enable the Gmail API on that project. In Google Admin → Security → Access and data control → API controls → Domain-wide delegation, authorize the service account’s client id for `https://www.googleapis.com/auth/gmail.send` only. |
| `CRON_SECRET` | Required only to enable the tick | Shared bearer secret for `POST /internal/time/tick`. | Generate it yourself, for example `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Set the same value on the app and on the Worker. It is not a vendor key. |

Fly already runs one machine and mounts `/data`. This change does not alter `fly.toml` or deploy anything.

### Worker

`deploy/time-tick-worker` is a Cloudflare Worker with a one-minute cron. It POSTs to the app and throws if the HTTP status is not OK, so Cloudflare retries that invocation. It does not store hours.

```bash
# From deploy/time-tick-worker, after `npx wrangler login` (Cloudflare dashboard → Workers).
# Confirm the account’s Workers plan allows a one-minute cron. This repo does not promise that the free plan does.
npx wrangler secret put CRON_SECRET
# Set CLYNOTION_TICK_URL in wrangler.toml or the dashboard to
# https://clynotion.com/internal/time/tick
npx wrangler deploy
```

`CLYNOTION_TICK_URL` is the public app origin plus `/internal/time/tick`. Fly’s `auto_start_machines` wakes the app when that request arrives. A cold start after 11:30 still locks, because the lock uses the clock on the request, not the cron’s schedule time.

## Verification

Without mail:

1. `python3 run_smoke_test.py` and `python3 -m pytest -q`.
2. Sign in, import a synthetic roster, open **Time entry**.
3. Settings: select staff, save, **Preview summary** and **Preview reminders**. The page must say they were not sent.
4. As a selected user, save hours and minutes, including `0` / `0`. Confirm the admin week shows raw, payable, decimal, and a blank missing row for anyone who did not save.
5. `curl -sS -X POST https://<host>/internal/time/tick` with no secret returns 503 and sends nothing.

With mail, from a trusted shell (do not paste the secret into chat or commit it):

```bash
curl -sS -X POST "$CLYNOTION_TICK_URL" \
  -H "Authorization: Bearer $CRON_SECRET"
```

Expect JSON counts (`sent`, `failed`, `uncertain`, `not_ready`). Then look at the operations mailbox and one staff mailbox. One success does not prove the next tick will arrive. If a row’s status is `uncertain`, do not click a retry that is not in this build; assume it may already have been delivered.

After Friday 11:30 ET, a non-Melissa admin’s save must be rejected, and Melissa’s save must show a late audit row and must not send a second summary.

## Rollback

Redeploy the previous image (the GitHub Action on `main`, or `flyctl deploy` of the last good release). Do not delete the Fly volume, `/data/hr_time.enc`, or any other `/data/*.enc` file. Do not rotate or unset `ATTUNE_DATA_ENCRYPTION_KEY`. Leaving the hours file in place is safe: the older image ignores it, and a later hours release can still read it with the same key.

The Cloudflare tick worker is not part of this deploy. Do not `wrangler deploy` it while mail is off.

## Limits

- One category (`total`) on screen. Lines are ready for more categories; payable time is the rounded sum.
- Corrector is fixed as `msmith@heart4change.org` inside her practice. It is not configurable.
- Summary catch-up is the current week and the previous week only, and only weeks on or after the week settings were first saved.
- Participant snapshot is whoever was selected and still on the included roster at the moment the cutoff was applied. Run the minute tick so that moment is the cutoff, not a later settings edit. Settings saves themselves lock due weeks before applying the edit.
- File locking assumes one app machine. Uncertain Gmail results are not retried. A crash after Gmail accepts a message and before the sent flag is saved can duplicate that message when the lease expires.
- No payroll file is pushed to QuickBooks or any other pay system. The CSV is a download and an email attachment.
