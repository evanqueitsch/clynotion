"""File storage for onboarding documents.

Default backend is Fernet files under /data/onboarding_files. A Drive backend
is used only when FILE_STORE_BACKEND=drive and its secrets are present.
Names are hire id and document type. No person names.

Credential providers are pluggable. The JSON-key provider reads
GOOGLE_DRIVE_SA_JSON. Keyless Workload Identity is selected with
GOOGLE_DRIVE_AUTH=wif and is not used unless that configuration exists.
This module does not create credentials or request new OAuth scopes.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

import httpx
from cryptography.fernet import Fernet

from app.crypto import decrypt_utf8, encrypt_utf8

ENV_FILES_PATH = "ATTUNE_ONBOARDING_FILES_PATH"
ENV_FILES_KEY = "ONBOARDING_FILES_KEY"
ENV_BACKEND = "FILE_STORE_BACKEND"
DEFAULT_FILES_PATH = ".attune_data/onboarding_files"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
TOKEN_URL = "https://oauth2.googleapis.com/token"

PERSONNEL_FOLDERS = ("01-onboarding", "02-payroll-tax", "03-credentialing", "04-clearances")


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageKey:
    area: str
    hire_id: str
    subfolder: str
    doc_type: str
    version: int

    def __post_init__(self) -> None:
        if self.area not in {"personnel", "i9"}:
            raise StorageError("unknown storage area")
        if not self.hire_id or "/" in self.hire_id or ".." in self.hire_id:
            raise StorageError("bad hire id")
        for part in (self.subfolder, self.doc_type):
            if not part or "/" in part or ".." in part:
                raise StorageError("bad storage name")


@dataclass
class StoredRef:
    backend: str
    object_key: str
    sha256: str
    size: int
    mime: str
    drive_file_id: str = ""
    drive_revision_id: str = ""


@dataclass
class DeleteResult:
    trashed: bool
    permanent: bool


@dataclass
class VerifyResult:
    exists: bool
    sha_match: bool
    revision_match: bool
    moved: bool


@dataclass
class TokenProvider(Protocol):
    def access_token(self) -> str: ...


def files_root() -> Path:
    return Path((os.environ.get(ENV_FILES_PATH) or DEFAULT_FILES_PATH).strip())


def _file_fernet() -> tuple[Optional[Fernet], str]:
    """Prefer ONBOARDING_FILES_KEY. Fall back to the app key so letters can be
    stored before that secret exists. Set ONBOARDING_FILES_REQUIRE_DEDICATED=1
    to refuse the fallback.
    """
    raw = (os.environ.get(ENV_FILES_KEY) or "").strip()
    if raw:
        return Fernet(raw.encode("ascii")), "dedicated"
    if os.environ.get("ONBOARDING_FILES_REQUIRE_DEDICATED") == "1":
        raise StorageError("ONBOARDING_FILES_KEY is not set")
    return None, "app"


def _encrypt(blob: bytes) -> bytes:
    box, _source = _file_fernet()
    if box is None:
        return encrypt_utf8(blob.decode("latin-1")) if False else _encrypt_app(blob)
    return box.encrypt(blob)


def _encrypt_app(blob: bytes) -> bytes:
    import base64

    payload = base64.b64encode(blob).decode("ascii")
    return encrypt_utf8(payload)


def _decrypt(blob: bytes) -> bytes:
    import base64

    box, _source = _file_fernet()
    if box is None:
        return base64.b64decode(decrypt_utf8(blob))
    return box.decrypt(blob)


class LocalEncryptedStore:
    backend = "local"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or files_root()

    def _path_for(self, key: StorageKey, name: str) -> Path:
        return self.root / key.area / key.hire_id / key.subfolder / name

    def ensure_tree(self, hire_id: str) -> dict[str, Any]:
        folders: dict[str, Any] = {"personnel": {}, "i9": {}}
        for sub in PERSONNEL_FOLDERS:
            path = self.root / "personnel" / hire_id / sub
            path.mkdir(parents=True, exist_ok=True)
            folders["personnel"][sub] = str(path)
        i9 = self.root / "i9" / hire_id / "i9"
        i9.mkdir(parents=True, exist_ok=True)
        folders["i9"]["i9"] = str(i9)
        return folders

    def put(self, key: StorageKey, data: bytes, mime: str, meta: dict[str, Any] | None = None) -> StoredRef:
        _ = meta
        digest = hashlib.sha256(data).hexdigest()
        name = f"{key.doc_type}-v{key.version}-{uuid.uuid4().hex}.bin"
        path = self._path_for(key, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_encrypt(data))
        object_key = str(path.relative_to(self.root))
        return StoredRef(
            backend=self.backend,
            object_key=object_key,
            sha256=digest,
            size=len(data),
            mime=mime,
        )

    def _abs(self, ref: StoredRef) -> Path:
        path = (self.root / ref.object_key).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise StorageError("ref escaped the store")
        return path

    def get(self, ref: StoredRef) -> bytes:
        path = self._abs(ref)
        if not path.is_file():
            raise StorageError("file missing")
        return _decrypt(path.read_bytes())

    def delete(self, ref: StoredRef) -> DeleteResult:
        path = self._abs(ref)
        if not path.is_file():
            return DeleteResult(trashed=False, permanent=False)
        trash = path.with_name(path.name + ".trash")
        path.replace(trash)
        trash.unlink()
        return DeleteResult(trashed=True, permanent=True)

    def list(self, prefix: StorageKey) -> list[StoredRef]:
        folder = self.root / prefix.area / prefix.hire_id / prefix.subfolder
        if not folder.is_dir():
            return []
        found: list[StoredRef] = []
        for path in sorted(folder.glob(f"{prefix.doc_type}-v{prefix.version}-*.bin")):
            found.append(
                StoredRef(
                    backend=self.backend,
                    object_key=str(path.relative_to(self.root)),
                    sha256="",
                    size=path.stat().st_size,
                    mime="",
                )
            )
        return found

    def verify(self, ref: StoredRef) -> VerifyResult:
        path = self._abs(ref)
        if not path.is_file():
            return VerifyResult(False, False, True, True)
        try:
            data = _decrypt(path.read_bytes())
        except Exception:
            return VerifyResult(True, False, True, False)
        digest = hashlib.sha256(data).hexdigest()
        return VerifyResult(True, digest == ref.sha256, True, False)


class ServiceAccountJsonProvider:
    """OAuth token from a service-account JSON key. The key is never logged."""

    def __init__(self, info: dict[str, Any]) -> None:
        self._info = info
        self._token = ""
        self._exp = 0.0

    def access_token(self) -> str:
        now = time.time()
        if self._token and now < self._exp - 60:
            return self._token
        import jwt

        issued = int(now)
        claims = {
            "iss": self._info["client_email"],
            "scope": DRIVE_SCOPE,
            "aud": TOKEN_URL,
            "iat": issued,
            "exp": issued + 3600,
        }
        assertion = jwt.encode(claims, self._info["private_key"], algorithm="RS256")
        response = httpx.post(
            TOKEN_URL,
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            timeout=20,
        )
        if response.status_code != 200:
            raise StorageError("drive_token_failed")
        body = response.json()
        self._token = str(body.get("access_token") or "")
        self._exp = now + int(body.get("expires_in") or 3600)
        if not self._token:
            raise StorageError("drive_token_failed")
        return self._token


class WorkloadIdentityProvider:
    """Keyless auth: Fly OIDC exchanged for a Drive token.

    Used only when GOOGLE_DRIVE_AUTH=wif and the audience, service account,
    and OIDC token file are set. Does not change existing OAuth clients.
    """

    def __init__(self) -> None:
        self._audience = (os.environ.get("GOOGLE_DRIVE_WIF_AUDIENCE") or "").strip()
        self._service_account = (os.environ.get("GOOGLE_DRIVE_WIF_SERVICE_ACCOUNT") or "").strip()
        self._token_file = (os.environ.get("GOOGLE_DRIVE_OIDC_TOKEN_FILE") or "").strip()
        self._token = ""
        self._exp = 0.0

    def configured(self) -> bool:
        return bool(self._audience and self._service_account and self._token_file)

    def access_token(self) -> str:
        now = time.time()
        if self._token and now < self._exp - 60:
            return self._token
        oidc = Path(self._token_file).read_text(encoding="utf-8").strip()
        exchanged = httpx.post(
            "https://sts.googleapis.com/v1/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "audience": self._audience,
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "subject_token": oidc,
            },
            timeout=20,
        )
        if exchanged.status_code != 200:
            raise StorageError("drive_wif_failed")
        sts = str(exchanged.json().get("access_token") or "")
        minted = httpx.post(
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{self._service_account}:generateAccessToken",
            headers={"Authorization": f"Bearer {sts}"},
            json={"scope": [DRIVE_SCOPE], "lifetime": "3600s"},
            timeout=20,
        )
        if minted.status_code != 200:
            raise StorageError("drive_wif_failed")
        self._token = str(minted.json().get("accessToken") or "")
        self._exp = now + 3600
        if not self._token:
            raise StorageError("drive_wif_failed")
        return self._token


def drive_token_provider() -> Optional[TokenProvider]:
    mode = (os.environ.get("GOOGLE_DRIVE_AUTH") or "sa").strip().lower()
    if mode == "wif":
        provider = WorkloadIdentityProvider()
        return provider if provider.configured() else None
    raw = (os.environ.get("GOOGLE_DRIVE_SA_JSON") or "").strip()
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(info, dict) or not info.get("client_email") or not info.get("private_key"):
        return None
    return ServiceAccountJsonProvider(info)


class _Http(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class DriveStore:
    """Shared-drive files. supportsAllDrives on every call. No domain-wide delegation."""

    backend = "drive"

    def __init__(
        self,
        provider: TokenProvider,
        personnel_drive_id: str,
        i9_drive_id: str,
        *,
        client: _Http | None = None,
    ) -> None:
        self._provider = provider
        self._drives = {"personnel": personnel_drive_id, "i9": i9_drive_id}
        self._client = client or httpx.Client(timeout=30)

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        delay = 0.4
        last = None
        for _attempt in range(5):
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {self._provider.access_token()}"
            response = self._client.request(method, url, headers=headers, **kwargs)
            last = response
            status = getattr(response, "status_code", 200)
            if status not in {403, 429, 500, 502, 503, 504}:
                return response
            if status == 403:
                text = ""
                try:
                    text = response.text or ""
                except Exception:
                    text = ""
                if "rate" not in text.lower() and "quota" not in text.lower():
                    return response
            time.sleep(delay + random.random() * 0.2)
            delay *= 2
            kwargs = dict(kwargs)
        return last

    def _drive_id(self, area: str) -> str:
        return self._drives[area]

    def ensure_tree(self, hire_id: str) -> dict[str, Any]:
        folders: dict[str, Any] = {"personnel": {}, "i9": {}}
        personnel_root = self._mkdir("personnel", self._drive_id("personnel"), hire_id)
        for sub in PERSONNEL_FOLDERS:
            folders["personnel"][sub] = self._mkdir("personnel", personnel_root, sub)
        i9_root = self._mkdir("i9", self._drive_id("i9"), hire_id)
        folders["i9"]["i9"] = self._mkdir("i9", i9_root, "i9")
        return folders

    def _mkdir(self, area: str, parent: str, name: str) -> str:
        existing = self._find(area, parent, name, folder=True)
        if existing:
            return existing
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent],
        }
        response = self._request(
            "POST",
            "https://www.googleapis.com/drive/v3/files",
            params={"supportsAllDrives": "true"},
            json=body,
        )
        if response.status_code not in {200, 201}:
            raise StorageError("drive_folder_failed")
        return str(response.json()["id"])

    def _find(self, area: str, parent: str, name: str, *, folder: bool) -> str:
        mime = "application/vnd.google-apps.folder" if folder else ""
        query = f"name = '{name}' and '{parent}' in parents and trashed = false"
        if mime:
            query += f" and mimeType = '{mime}'"
        response = self._request(
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            params={
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "drive",
                "driveId": self._drive_id(area),
                "q": query,
                "fields": "files(id,name)",
            },
        )
        if response.status_code != 200:
            raise StorageError("drive_list_failed")
        files = response.json().get("files") or []
        if not files:
            return ""
        return str(files[0]["id"])

    def put(self, key: StorageKey, data: bytes, mime: str, meta: dict[str, Any] | None = None) -> StoredRef:
        parent = str((meta or {}).get("parent_id") or "")
        if not parent:
            raise StorageError("drive folder is missing")
        digest = hashlib.sha256(data).hexdigest()
        name = f"{key.doc_type}-v{key.version}-{uuid.uuid4().hex}"
        boundary = "clynotion" + uuid.uuid4().hex
        meta_json = json.dumps({"name": name, "parents": [parent]})
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta_json}\r\n"
            f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--".encode()
        response = self._request(
            "POST",
            "https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id,headRevisionId"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        )
        if response.status_code not in {200, 201}:
            raise StorageError("drive_put_failed")
        payload = response.json()
        return StoredRef(
            backend=self.backend,
            object_key=f"{key.area}/{key.hire_id}/{key.subfolder}/{name}",
            sha256=digest,
            size=len(data),
            mime=mime,
            drive_file_id=str(payload.get("id") or ""),
            drive_revision_id=str(payload.get("headRevisionId") or ""),
        )

    def get(self, ref: StoredRef) -> bytes:
        response = self._request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{ref.drive_file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
        )
        if response.status_code != 200:
            raise StorageError("drive_get_failed")
        return response.content

    def delete(self, ref: StoredRef) -> DeleteResult:
        trashed = self._request(
            "PATCH",
            f"https://www.googleapis.com/drive/v3/files/{ref.drive_file_id}",
            params={"supportsAllDrives": "true"},
            json={"trashed": True},
        )
        removed = self._request(
            "DELETE",
            f"https://www.googleapis.com/drive/v3/files/{ref.drive_file_id}",
            params={"supportsAllDrives": "true"},
        )
        return DeleteResult(
            trashed=getattr(trashed, "status_code", 500) in {200, 204},
            permanent=getattr(removed, "status_code", 500) in {200, 204},
        )

    def list(self, prefix: StorageKey) -> list[StoredRef]:
        return []

    def verify(self, ref: StoredRef) -> VerifyResult:
        response = self._request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{ref.drive_file_id}",
            params={"supportsAllDrives": "true", "fields": "id,trashed,headRevisionId,parents"},
        )
        if response.status_code == 404:
            return VerifyResult(False, False, False, True)
        if response.status_code != 200:
            raise StorageError("drive_verify_failed")
        meta = response.json()
        if meta.get("trashed"):
            return VerifyResult(False, False, False, True)
        try:
            data = self.get(ref)
        except StorageError:
            return VerifyResult(True, False, False, False)
        digest = hashlib.sha256(data).hexdigest()
        revision = str(meta.get("headRevisionId") or "")
        return VerifyResult(
            True,
            digest == ref.sha256,
            (not ref.drive_revision_id) or revision == ref.drive_revision_id,
            False,
        )


_store: Any = None


def build_file_store() -> Any:
    global _store
    backend = (os.environ.get(ENV_BACKEND) or "local").strip().lower()
    if backend == "drive":
        provider = drive_token_provider()
        personnel = (os.environ.get("DRIVE_PERSONNEL_SHARED_DRIVE_ID") or "").strip()
        i9 = (os.environ.get("DRIVE_I9_SHARED_DRIVE_ID") or "").strip()
        if provider and personnel and i9:
            _store = DriveStore(provider, personnel, i9)
            return _store
    _store = LocalEncryptedStore()
    return _store


def file_store() -> Any:
    global _store
    if _store is None or os.environ.get("ATTUNE_HIRING_STORE_RESET") == "1":
        return build_file_store()
    return _store


def reset_file_store() -> None:
    global _store
    _store = None


def cutover_plan(local: LocalEncryptedStore, drive: DriveStore, refs: list[StoredRef]) -> dict[str, int]:
    """Dry-run counts. Does not copy."""
    total = 0
    size = 0
    for ref in refs:
        if ref.backend != "local":
            continue
        try:
            data = local.get(ref)
        except StorageError:
            continue
        total += 1
        size += len(data)
    _ = drive
    return {"files": total, "bytes": size}
