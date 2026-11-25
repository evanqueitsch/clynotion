"""Scheduled compliance jobs.

Both the monthly screening and the daily integrity check stay off until an
Admin enables them. The Drive copy stays disabled until a folder id is saved.
Nothing here sends mail.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.compliance.audit_chain import public_entry, verify_chain
from app.compliance.screening import monthly_window, run_screening
from app.compliance.store import compliance_store

EASTERN = ZoneInfo("America/New_York")
_FOLDER = re.compile(r"^[A-Za-z0-9_-]{10,120}$")


def run_scheduled(
    moment: Optional[datetime] = None,
    *,
    lists: Optional[dict] = None,
    uploader: Optional[Any] = None,
) -> dict[str, str]:
    """One pass for every practice that already has compliance settings.

    An empty file does nothing. Screening runs only when the monthly toggle
    is on and the clock is the first business day at 7:00 AM Eastern.
    """
    summary = {"screening": "off", "integrity": "off", "drive": "not_configured"}
    local = moment.astimezone(EASTERN) if moment else datetime.now(EASTERN)
    for practice_id in compliance_store.practices():
        settings = compliance_store.settings(practice_id)
        if settings.get("monthly_screening_enabled"):
            closed = _closed(practice_id)
            if monthly_window(local, closed, str(settings.get("last_monthly_key") or "")):
                run_screening(
                    practice_id,
                    "scheduler",
                    purpose="monthly",
                    confirm="SCREEN ROSTER",
                    lists=lists,
                    moment=local,
                )
                compliance_store.save_settings(
                    practice_id,
                    {"last_monthly_key": f"{local.year:04d}-{local.month:02d}"},
                )
                summary["screening"] = "ran"
            else:
                summary["screening"] = "waiting"
        if settings.get("integrity_check_enabled"):
            day = local.date().isoformat()
            if local.hour >= 7 and str(settings.get("last_integrity_day") or "") != day:
                verify_chain(practice_id)
                compliance_store.save_settings(practice_id, {"last_integrity_day": day})
                summary["integrity"] = "ran"
            else:
                summary["integrity"] = "waiting"
        folder = str(settings.get("drive_folder_id") or "").strip()
        if not folder:
            continue
        summary["drive"] = "configured"
        day = local.date().isoformat()
        if local.hour >= 7 and str(settings.get("last_drive_day") or "") != day:
            outcome = copy_audit_day(practice_id, day, folder, uploader=uploader)
            compliance_store.save_settings(practice_id, {"last_drive_day": day})
            summary["drive"] = "copied" if outcome.get("status") == "copied" else str(outcome.get("status") or "not_configured")
    return summary


def _closed(practice_id: str):
    from app.compliance.screening import _closed as closed_days

    return closed_days(practice_id)


def copy_audit_day(
    practice_id: str,
    day: str,
    folder_id: str,
    *,
    uploader: Optional[Any] = None,
) -> dict[str, str]:
    """Write one day's entries and the chain head to a Shared Drive folder.

    No folder means the job is not configured and no Drive call is made.
    """
    if not folder_id or not _FOLDER.match(folder_id):
        compliance_store.append_drive_copy(
            practice_id,
            {"at": datetime.now(EASTERN).isoformat(), "day": day, "status": "not_configured"},
        )
        return {"status": "not_configured"}
    entries = compliance_store.audit_entries(practice_id)
    day_rows = [public_entry(item) for item in entries if str(public_entry(item).get("ts_et") or "").startswith(day)]
    head = str(entries[-1].get("hash") or "") if entries else ""
    body = json.dumps({"day": day, "head_hash": head, "entries": day_rows}, sort_keys=True).encode("utf-8")
    sender = uploader or _drive_uploader()
    if sender is None:
        compliance_store.append_drive_copy(
            practice_id,
            {"at": datetime.now(EASTERN).isoformat(), "day": day, "status": "not_configured"},
        )
        return {"status": "not_configured"}
    try:
        file_id = sender(folder_id, f"clynotion-audit-{day}.json", body)
    except Exception:
        compliance_store.append_drive_copy(
            practice_id,
            {"at": datetime.now(EASTERN).isoformat(), "day": day, "status": "failed"},
        )
        return {"status": "failed"}
    compliance_store.append_drive_copy(
        practice_id,
        {
            "at": datetime.now(EASTERN).isoformat(),
            "day": day,
            "status": "copied",
            "file_id": str(file_id or ""),
            "head_hash": head,
            "count": len(day_rows),
        },
    )
    return {"status": "copied"}


def _drive_uploader():
    from app.hiring.storage import drive_token_provider

    provider = drive_token_provider()
    if provider is None:
        return None

    def send(folder_id: str, filename: str, data: bytes) -> str:
        import httpx

        token = provider.access_token()
        response = httpx.post(
            "https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType": "multipart", "supportsAllDrives": "true"},
            headers={"Authorization": f"Bearer {token}"},
            files={
                "metadata": (
                    "metadata",
                    json.dumps({"name": filename, "parents": [folder_id], "mimeType": "application/json"}),
                    "application/json",
                ),
                "file": (filename, data, "application/json"),
            },
            timeout=60,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError("drive_upload_failed")
        return str(response.json().get("id") or "")

    return send
