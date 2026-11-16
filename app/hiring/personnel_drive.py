"""Personnel folder in the HR shared drive. Local files stay the copy of record.

This client talks only to HR_PERSONNEL_DRIVE_ID. It does not open the I-9 drive
or any other Drive. Missing secrets leave the caller on encrypted /data.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Optional, Protocol

import httpx

from app.hiring.storage import StorageError, drive_token_provider

ENV_DRIVE_ID = "HR_PERSONNEL_DRIVE_ID"
_FILES = "https://www.googleapis.com/drive/v3/files"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
_UNSET = object()
_override: Any = _UNSET


class DriveClient(Protocol):
    def ensure_folder(self, folder_id: str, name: str) -> tuple[str, str]: ...

    def upload(self, folder_id: str, filename: str, data: bytes, mime: str) -> str: ...


def folder_name(full_name: str) -> str:
    """Last, First. A single token is used as-is. Slashes are removed."""
    parts = [part for part in re.split(r"\s+", (full_name or "").replace("/", " ").strip()) if part]
    if len(parts) >= 2:
        label = f"{parts[-1]}, {' '.join(parts[:-1])}"
    elif parts:
        label = parts[0]
    else:
        label = "Candidate"
    return label[:180]


def folder_link(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def personnel_configured() -> bool:
    if _override is not _UNSET:
        return _override is not None
    return drive_token_provider() is not None and bool((os.environ.get(ENV_DRIVE_ID) or "").strip())


def personnel_client() -> Optional[DriveClient]:
    if _override is not _UNSET:
        return _override
    drive_id = (os.environ.get(ENV_DRIVE_ID) or "").strip()
    provider = drive_token_provider()
    if not provider or not drive_id:
        return None
    return PersonnelDrive(provider, drive_id)


def set_personnel_drive_for_tests(client: Optional[DriveClient]) -> None:
    global _override
    _override = client


def clear_personnel_drive_for_tests() -> None:
    global _override
    _override = _UNSET


class PersonnelDrive:
    """One shared drive. supportsAllDrives on every call. No domain-wide delegation."""

    def __init__(self, provider: Any, drive_id: str, *, client: Any | None = None) -> None:
        self._provider = provider
        self._drive_id = drive_id
        self._client = client or httpx.Client(timeout=30)

    def ensure_folder(self, folder_id: str, name: str) -> tuple[str, str]:
        if folder_id:
            return folder_id, folder_link(folder_id)
        found = self._find_folder(name)
        if found:
            return found, folder_link(found)
        created = self._create_folder(name)
        return created, folder_link(created)

    def upload(self, folder_id: str, filename: str, data: bytes, mime: str) -> str:
        boundary = "clynotion" + uuid.uuid4().hex
        meta = json.dumps({"name": filename, "parents": [folder_id]})
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--".encode()
        response = self._request(
            "POST",
            _UPLOAD,
            params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        )
        if getattr(response, "status_code", 500) not in {200, 201}:
            raise StorageError("drive_put_failed")
        return str(response.json().get("id") or "")

    def _create_folder(self, name: str) -> str:
        response = self._request(
            "POST",
            _FILES,
            params={"supportsAllDrives": "true", "fields": "id"},
            json={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [self._drive_id],
            },
        )
        if getattr(response, "status_code", 500) not in {200, 201}:
            raise StorageError("drive_folder_failed")
        return str(response.json()["id"])

    def _find_folder(self, name: str) -> str:
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' "
            f"and '{self._drive_id}' in parents and trashed = false"
        )
        response = self._request(
            "GET",
            _FILES,
            params={
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "drive",
                "driveId": self._drive_id,
                "q": query,
                "fields": "files(id)",
                "pageSize": "1",
            },
        )
        if getattr(response, "status_code", 500) != 200:
            raise StorageError("drive_list_failed")
        files = response.json().get("files") or []
        if not files:
            return ""
        return str(files[0].get("id") or "")

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._provider.access_token()}"
        return self._client.request(method, url, headers=headers, **kwargs)
