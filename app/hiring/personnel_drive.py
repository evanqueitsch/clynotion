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

    def rename(self, file_id: str, name: str) -> None: ...

    def ensure_child(self, parent_id: str, name: str) -> str: ...

    def trash(self, file_id: str) -> None: ...

    def move(self, file_id: str, folder_id: str, name: str = "") -> None: ...


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "2nd", "3rd", "4th"}


def _title_token(token: str) -> str:
    letters = [char for char in token if char.isalpha()]
    if letters and (all(char.isupper() for char in letters) or all(char.islower() for char in letters)):
        return token.title()
    return token


def _suffix_label(token: str) -> str:
    key = token.strip(",").casefold().rstrip(".")
    if key in {"jr", "sr"}:
        return key.title() + "."
    if key in {"ii", "iii", "iv", "v"}:
        return key.upper()
    return token.strip(",")


def folder_name(full_name: str) -> str:
    """Last, First. Title-cased. Middle names and initials are dropped.

    A suffix such as Jr. or III stays with the last name.
    """
    cleaned = (full_name or "").replace("/", " ")
    raw = [part.strip(",") for part in re.split(r"\s+", cleaned.strip()) if part.strip(",")]
    suffixes: list[str] = []
    while raw:
        key = raw[-1].casefold().rstrip(".")
        if key in _SUFFIXES:
            suffixes.insert(0, _suffix_label(raw[-1]))
            raw.pop()
            continue
        break
    names = [part for part in raw if not re.fullmatch(r"[A-Za-z]\.?", part)]
    if not names:
        return "Candidate"
    first = _title_token(names[0])
    if len(names) == 1 and not suffixes:
        return first[:180]
    last = _title_token(names[-1])
    if suffixes:
        last = f"{last} {' '.join(suffixes)}"
    if len(names) == 1:
        return last[:180]
    return f"{last}, {first}"[:180]


def folder_link(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def _file_stem(full_name: str) -> str:
    stem = folder_name(full_name).replace(",", "")
    stem = re.sub(r"\s+", "_", stem.strip())
    return re.sub(r"[^A-Za-z0-9._-]", "", stem).strip("._") or "Candidate"


def resume_filename(full_name: str, mime: str) -> str:
    """Last_First_resume.pdf, from the same Last, First order as the folder."""
    extension = {"application/pdf": "pdf", "text/plain": "txt"}.get(mime, "docx")
    return f"{_file_stem(full_name)}_resume.{extension}"


def signed_offer_filename(full_name: str) -> str:
    """Last_First_offer-signed.pdf, in the same order as the personnel folder."""
    return f"{_file_stem(full_name)}_offer-signed.pdf"


def offer_letter_filename(full_name: str) -> str:
    """Last_First_offer-letter.pdf, in the same order as the personnel folder."""
    return f"{_file_stem(full_name)}_offer-letter.pdf"


ONBOARDING_FOLDERS = ("01 Onboarding", "02 Payroll & Tax", "03 Credentialing", "04 Clearances")


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

    def rename(self, file_id: str, name: str) -> None:
        response = self._request(
            "PATCH",
            f"{_FILES}/{file_id}",
            params={"supportsAllDrives": "true", "fields": "id"},
            json={"name": name},
        )
        if getattr(response, "status_code", 500) not in {200, 201}:
            raise StorageError("drive_rename_failed")

    def ensure_child(self, parent_id: str, name: str) -> str:
        found = self._find_folder(name, parent_id)
        if found:
            return found
        return self._create_folder(name, parent_id)

    def move(self, file_id: str, folder_id: str, name: str = "") -> None:
        current = self._request(
            "GET",
            f"{_FILES}/{file_id}",
            params={"supportsAllDrives": "true", "fields": "parents"},
        )
        if getattr(current, "status_code", 500) != 200:
            raise StorageError("drive_move_failed")
        parents = [str(item) for item in (current.json().get("parents") or []) if item and item != folder_id]
        body: dict[str, str] = {"name": name} if name else {}
        response = self._request(
            "PATCH",
            f"{_FILES}/{file_id}",
            params={
                "supportsAllDrives": "true",
                "addParents": folder_id,
                "removeParents": ",".join(parents),
                "fields": "id",
            },
            json=body,
        )
        if getattr(response, "status_code", 500) not in {200, 201}:
            raise StorageError("drive_move_failed")

    def trash(self, file_id: str) -> None:
        response = self._request(
            "PATCH",
            f"{_FILES}/{file_id}",
            params={"supportsAllDrives": "true", "fields": "id,trashed"},
            json={"trashed": True},
        )
        if getattr(response, "status_code", 500) not in {200, 201}:
            raise StorageError("drive_trash_failed")

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

    def _create_folder(self, name: str, parent_id: str = "") -> str:
        response = self._request(
            "POST",
            _FILES,
            params={"supportsAllDrives": "true", "fields": "id"},
            json={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id or self._drive_id],
            },
        )
        if getattr(response, "status_code", 500) not in {200, 201}:
            raise StorageError("drive_folder_failed")
        return str(response.json()["id"])

    def _find_folder(self, name: str, parent_id: str = "") -> str:
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        parent = parent_id or self._drive_id
        query = (
            f"name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' "
            f"and '{parent}' in parents and trashed = false"
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
