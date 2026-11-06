"""Minute-level weekly-hours tick.

The worker is expected to call POST /internal/time/tick every minute. Due
times are compared in America/New_York to the configured minute, so a 15-minute
poll would miss arbitrary minutes — that cadence is not used.

Locking does not depend on this tick. API reads and writes apply the cutoff
from the clock as well. A late tick still sends at most the notifications whose
window is still open (reminders stop at cutoff; the summary remains due).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.hr.time_entry import (
    DEFAULT_LEASE,
    DEFAULT_RETRY_BACKOFF,
    MAX_DELIVERY_ATTEMPTS,
    time_store,
    utcnow,
)
from app.hr.time_mail import MailResult, MailSender, OutboundMail

DEFAULT_BACKOFF = DEFAULT_RETRY_BACKOFF


def run_tick(
    *,
    mailer: Optional[MailSender],
    now: Optional[datetime] = None,
    backoff: tuple[timedelta, ...] = DEFAULT_BACKOFF,
    lease: timedelta = DEFAULT_LEASE,
    max_attempts: int = MAX_DELIVERY_ATTEMPTS,
) -> dict[str, int | str]:
    moment = now or utcnow()
    if mailer is None:
        locked = time_store.apply_due_locks(moment)
        return {
            "mail": "not_configured",
            "reason": "mail_off",
            "locked_weeks": locked,
            "claimed": 0,
            "sent": 0,
            "failed": 0,
            "uncertain": 0,
            "not_ready": 0,
            "stale": 0,
            "skipped": 0,
        }
    problem = mailer.check_ready()
    if problem:
        locked = time_store.apply_due_locks(moment)
        return {
            "mail": "not_ready",
            "reason": problem,
            "locked_weeks": locked,
            "claimed": 0,
            "sent": 0,
            "failed": 0,
            "uncertain": 0,
            "not_ready": 0,
            "stale": 0,
            "skipped": 0,
        }

    batch = time_store.claim_due_jobs(
        moment, lease=lease, backoff=backoff, max_attempts=max_attempts
    )
    sent = failed = uncertain = not_ready = stale = skipped = 0
    for job in batch.jobs:
        fresh = time_store.refresh_job(job.delivery_id, job.claim_token, moment)
        if fresh is None:
            skipped += 1
            continue
        try:
            result = mailer.send(
                OutboundMail(
                    to=fresh.to,
                    subject=fresh.subject,
                    text=fresh.body,
                    csv_filename=fresh.csv_filename,
                    csv_text=fresh.csv_text,
                    practice_id=fresh.practice_id,
                )
            )
        except Exception:
            # Unknown outcome: the provider may already have accepted the message.
            result = MailResult(status="uncertain", detail="mailer_exception")
        outcome = time_store.finish_delivery(
            fresh.delivery_id, fresh.claim_token, result.safe(), moment
        )
        if outcome == "sent":
            sent += 1
        elif outcome == "failed":
            failed += 1
        elif outcome == "uncertain":
            uncertain += 1
        elif outcome == "not_ready":
            not_ready += 1
        else:
            stale += 1
    return {
        "mail": "configured",
        "reason": "ok",
        "locked_weeks": batch.locked_weeks,
        "claimed": len(batch.jobs),
        "sent": sent,
        "failed": failed,
        "uncertain": uncertain,
        "not_ready": not_ready,
        "stale": stale,
        "skipped": skipped,
    }
