"""Weekly hours HTTP API. Practice id always comes from the signed-in user."""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth import CurrentUser
from app.hr.time_entry import (
    NotFoundError,
    TimeAuthzError,
    TimeStoreError,
    time_store,
)
from app.hr.time_mail import ConnectedMailboxMailer, build_mailer
from app.hr.time_schedule import run_tick

router = APIRouter(tags=["weekly-hours"])


class HoursBody(BaseModel):
    week_ending: str
    hours: int = Field(ge=0, le=168)
    minutes: int = Field(ge=0, le=59)
    for_email: str = ""


class HoursPatch(BaseModel):
    hours: int = Field(ge=0, le=168)
    minutes: int = Field(ge=0, le=59)


class SettingsBody(BaseModel):
    participant_emails: list[str] = Field(default_factory=list)
    reminder_first_weekday: int = Field(ge=0, le=6)
    reminder_first_time: str
    reminder_due_weekday: int = Field(ge=0, le=6)
    reminder_due_time: str
    summary_weekday: int = Field(ge=0, le=6)
    summary_time: str
    summary_recipient: str
    automation_enabled: bool = False


class ReminderPreviewBody(BaseModel):
    slot: str
    week_ending: str = ""


class SelfTestBody(BaseModel):
    hours: int = Field(ge=0, le=168)
    minutes: int = Field(ge=0, le=59)


class SelfTestSendBody(BaseModel):
    kind: str


def _call(fn):
    try:
        return fn()
    except TimeAuthzError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Not found") from None
    except TimeStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/hr/time/me")
def time_me(user: CurrentUser) -> dict:
    return _call(lambda: time_store.me_view(user))


@router.get("/hr/time/week")
def time_week(user: CurrentUser, week_ending: str, v: Optional[int] = None) -> dict:
    return _call(lambda: time_store.week_view(user, week_ending, version=v))


@router.get("/hr/time/week.csv")
def time_week_csv(user: CurrentUser, week_ending: str, v: Optional[int] = None) -> Response:
    text = _call(lambda: time_store.week_csv(user, week_ending, version=v))
    filename = f"weekly-hours-{week_ending[:10]}.csv"
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/hr/time/entries")
def time_save_entry(user: CurrentUser, body: HoursBody) -> dict:
    return _call(
        lambda: time_store.save_hours(
            user,
            week_ending=body.week_ending,
            hours=body.hours,
            minutes=body.minutes,
            for_email=body.for_email,
        )
    )


@router.patch("/hr/time/entries/{submission_id}")
def time_patch_entry(user: CurrentUser, submission_id: str, body: HoursPatch) -> dict:
    return _call(
        lambda: time_store.patch_hours(
            user,
            submission_id,
            hours=body.hours,
            minutes=body.minutes,
        )
    )


@router.get("/hr/time/settings")
def time_settings_get(user: CurrentUser) -> dict:
    return _call(lambda: time_store.settings_view(user))


@router.put("/hr/time/settings")
def time_settings_put(user: CurrentUser, body: SettingsBody) -> dict:
    return _call(lambda: time_store.save_settings(user, body.model_dump()))


@router.post("/hr/time/settings/preview")
def time_settings_preview(user: CurrentUser, week_ending: str) -> dict:
    return _call(lambda: time_store.preview_summary(user, week_ending))


@router.post("/hr/time/directory/refresh")
def time_directory_refresh(user: CurrentUser) -> dict:
    """Reload the Workspace staff list. Does not enable anyone and does not send mail."""
    return _call(lambda: time_store.refresh_directory(user))


@router.get("/hr/time/self-test")
def time_self_test(user: CurrentUser) -> dict:
    """Labelled previews and optional self-entered hours. Never sends mail."""
    return _call(lambda: time_store.self_test_view(user))


@router.put("/hr/time/self-test")
def time_self_test_save(user: CurrentUser, body: SelfTestBody) -> dict:
    return _call(
        lambda: time_store.save_self_test_hours(
            user, hours=body.hours, minutes=body.minutes
        )
    )


@router.post("/hr/time/self-test/send")
def time_self_test_send(user: CurrentUser, body: SelfTestSendBody) -> dict:
    """Send one labelled self-test message. Does not run the payroll scheduler."""
    from app.hr.gmail_oauth import deliver_self_test_message

    result = _call(
        lambda: time_store.send_self_test(
            user,
            body.kind,
            lambda message: deliver_self_test_message(user.practice_id, message),
        )
    )
    send = result.get("send_result") if isinstance(result, dict) else {}
    send = send if isinstance(send, dict) else {}
    print(
        "self_test_send",
        {
            "kind": send.get("kind") or body.kind,
            "status": send.get("status") or "",
            "error": send.get("last_error") or "",
            "gmail_message_id": send.get("gmail_message_id") or "",
            "duplicate": bool(send.get("duplicate")),
        },
        flush=True,
    )
    return result


@router.post("/hr/time/settings/test-reminder")
def time_settings_test_reminder(user: CurrentUser, body: ReminderPreviewBody) -> dict:
    """Render reminder copy for the selected staff. This never sends mail."""
    return _call(
        lambda: time_store.preview_reminder(
            user, slot=body.slot, week_ending=body.week_ending
        )
    )


def _require_cron(request: Request) -> None:
    secret = (os.environ.get("CRON_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="cron_not_configured")
    header = request.headers.get("authorization", "")
    provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not provided:
        provided = (request.headers.get("x-cron-secret") or "").strip()
    if not provided or not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/internal/time/tick")
def time_tick(request: Request) -> dict:
    _require_cron(request)
    try:
        # Scheduled payroll mail uses the connected mailbox only when a practice
        # turns automation on. TIME_MAIL_MODE is not consulted, so this tick
        # cannot wake the old service-account jobs or send catch-up mail.
        result = run_tick(mailer=ConnectedMailboxMailer())
    except TimeStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        print(f"time_tick_error {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=500, detail="tick_failed") from exc
    printable = " ".join(f"{key}={value}" for key, value in sorted(result.items()))
    print(f"time_tick {printable}", flush=True)
    try:
        from app.denials.store import denials_store

        denials_store.purge_if_due()
    except Exception:
        pass
    return result
