"""Read a resume into offer fields.

Production calls Vertex Gemini on the Google Cloud project. Tests and mock
mode stay on this machine. Values that are not in the resume text are dropped,
and nothing from the resume is written to logs.
"""

from __future__ import annotations

import json
import os
import re
import time
import zipfile
from io import BytesIO
from typing import Any, Callable
from xml.etree import ElementTree

import httpx

MAX_RESUME_BYTES = 10 * 1024 * 1024
FIELD_NAMES = (
    "candidate_full_name",
    "candidate_first_name",
    "personal_email",
    "phone",
    "candidate_credentials",
    "position_title",
)
_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE = re.compile(r"(?:\+?1[\s.\-]*)?(?:\(?\d{3}\)?[\s.\-]*)\d{3}[\s.\-]*\d{4}")
_LABELS = (
    ("full legal name", "candidate_full_name"),
    ("legal name", "candidate_full_name"),
    ("first name", "candidate_first_name"),
    ("personal email", "personal_email"),
    ("email", "personal_email"),
    ("phone", "phone"),
    ("telephone", "phone"),
    ("credentials", "candidate_credentials"),
    ("credential", "candidate_credentials"),
    ("degree", "candidate_credentials"),
    ("position", "position_title"),
    ("title", "position_title"),
)
_PROMPT = """Extract only facts that are written in this resume. Return a JSON object with these keys:
candidate_full_name, candidate_first_name, personal_email, phone, candidate_credentials, position_title.
Use an empty string for anything that is not explicitly present. Do not guess, complete, or combine fragments.
candidate_credentials is the degree or license letters. position_title is the role they are seeking or hold, if stated.

Resume:
"""


class ResumeReadError(ValueError):
    """Safe to show to Admin. The message never includes resume text."""


def resume_mode() -> str:
    explicit = (os.environ.get("ATTUNE_RESUME_LLM") or "").strip().lower()
    if explicit in {"anthropic", "openai", "claude"}:
        raise ResumeReadError("Resume reading is not available.")
    if explicit:
        return explicit
    from app.config import is_real_mode

    return "gemini" if is_real_mode() else "mock"


def resume_mime(data: bytes, filename: str = "") -> str:
    kind = _kind(data, filename)
    if kind == "pdf":
        return "application/pdf"
    if kind == "docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "text/plain"


def read_resume(data: bytes, filename: str = "") -> dict[str, str]:
    if len(data) > MAX_RESUME_BYTES:
        raise ResumeReadError("That resume is larger than 10 MB.")
    text = _text_from(data, filename)
    if not text.strip():
        raise ResumeReadError("That resume has no readable text.")
    mode = resume_mode()
    if mode == "mock":
        proposed = _labeled_fields(text)
    elif mode == "gemini":
        proposed = _gemini_fields(text)
    else:
        raise ResumeReadError("Resume reading is not available.")
    return ground_fields(text, proposed)


def ground_fields(text: str, proposed: dict[str, Any]) -> dict[str, str]:
    """Drop anything the model added that is not written in the resume."""
    found: dict[str, str] = {}
    for name in FIELD_NAMES:
        value = _clean(str((proposed or {}).get(name) or ""))
        if name == "personal_email":
            match = _EMAIL.fullmatch(value)
            value = match.group(0) if match and _present(text, match.group(0)) else ""
        elif name == "phone":
            value = value if _phone_present(text, value) else ""
        elif not _present(text, value):
            value = ""
        found[name] = value
    if not found["personal_email"]:
        emails = _EMAIL.findall(text)
        if len(emails) == 1:
            found["personal_email"] = emails[0]
    if not found["phone"]:
        phones = _PHONE.findall(text)
        if len(phones) == 1:
            found["phone"] = _clean(phones[0])
    return found


def _clean(value: str) -> str:
    text = " ".join(value.replace("\x00", " ").split())
    return text[:120]


def _present(text: str, value: str) -> bool:
    if not value:
        return False
    hay = " ".join(text.split()).casefold()
    needle = " ".join(value.split()).casefold()
    return needle in hay


def _phone_present(text: str, value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 10 or len(digits) > 15:
        return False
    return digits in re.sub(r"\D", "", text)


def _labeled_fields(text: str) -> dict[str, str]:
    found = {name: "" for name in FIELD_NAMES}
    for raw in text.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        label, _, rest = line.partition(":")
        key = label.strip().casefold()
        value = _clean(rest)
        if not value:
            continue
        for prefix, field in _LABELS:
            if key == prefix and not found[field]:
                found[field] = value
                break
    return found


def _text_from(data: bytes, filename: str) -> str:
    kind = _kind(data, filename)
    if kind == "pdf":
        return _pdf_text(data)
    if kind == "docx":
        return _docx_text(data)
    return _txt_text(data)


def _kind(data: bytes, filename: str) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK") and _zip_has(data, "word/document.xml"):
        return "docx"
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "txt" or _looks_textual(data):
        if b"\x00" in data:
            raise ResumeReadError("Upload a PDF, DOCX, or TXT resume.")
        return "txt"
    raise ResumeReadError("Upload a PDF, DOCX, or TXT resume.")


def _zip_has(data: bytes, name: str) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            return name in package.namelist()
    except zipfile.BadZipFile:
        return False


def _looks_textual(data: bytes) -> bool:
    sample = data[:4000]
    if not sample or b"\x00" in sample:
        return False
    textish = sum(32 <= byte < 127 or byte in (9, 10, 13) for byte in sample)
    return textish / len(sample) > 0.9


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        raise ResumeReadError("That resume could not be read.") from exc
    return "\n".join(parts)


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            xml = package.read("word/document.xml")
        root = ElementTree.fromstring(xml)
    except Exception as exc:
        raise ResumeReadError("That resume could not be read.") from exc
    texts = [node.text or "" for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
    return " ".join(part for part in texts if part)


def _txt_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResumeReadError("That resume could not be read.") from exc


def _gemini_fields(text: str) -> dict[str, str]:
    try:
        raw = _gemini_complete(text)
    except ResumeReadError:
        raise
    except Exception as exc:
        raise ResumeReadError("The resume could not be read.") from exc
    try:
        parsed = _json_object(raw)
    except ValueError as exc:
        raise ResumeReadError("The resume could not be read.") from exc
    if not isinstance(parsed, dict):
        raise ResumeReadError("The resume could not be read.")
    return {name: str(parsed.get(name) or "") for name in FIELD_NAMES}


def _json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("not an object")
    return parsed


def _gemini_complete(text: str) -> str:
    token = _gemini_token()
    project, location, model = _gemini_target()
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
        f"/locations/{location}/publishers/google/models/{model}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": _PROMPT + text[:20000]}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 400, "responseMimeType": "application/json"},
    }
    try:
        response = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise ResumeReadError("The resume could not be read.") from exc
    if response.status_code >= 400:
        raise ResumeReadError("The resume could not be read.")
    try:
        payload = response.json()
        return str(payload["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as exc:
        raise ResumeReadError("The resume could not be read.") from exc


def _gemini_target() -> tuple[str, str, str]:
    account = _service_account()
    project = (os.environ.get("GOOGLE_CLOUD_PROJECT") or str(account.get("project_id") or "")).strip()
    if not project:
        raise ResumeReadError("Resume reading is not configured.")
    location = (os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-east4").strip() or "us-east4"
    model = (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash-001").strip() or "gemini-2.0-flash-001"
    return project, location, model


def _service_account() -> dict[str, Any]:
    import json
    from pathlib import Path

    raw = (os.environ.get("GOOGLE_SA_JSON") or "").strip()
    if not raw:
        raise ResumeReadError("Resume reading is not configured.")
    try:
        data = json.loads(raw) if raw.lstrip().startswith("{") else json.loads(Path(raw).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ResumeReadError("Resume reading is not configured.") from exc
    if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
        raise ResumeReadError("Resume reading is not configured.")
    return data


def _gemini_token() -> str:
    import jwt

    account = _service_account()
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": str(account["client_email"]),
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        },
        str(account["private_key"]),
        algorithm="RS256",
    )
    try:
        response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise ResumeReadError("The resume could not be read.") from exc
    if response.status_code >= 400:
        raise ResumeReadError("The resume could not be read.")
    token = str((response.json() or {}).get("access_token") or "")
    if not token:
        raise ResumeReadError("The resume could not be read.")
    return token


def set_reader_for_tests(reader: Callable[[bytes, str], dict[str, str]] | None) -> None:
    global _reader_override
    _reader_override = reader


_reader_override: Callable[[bytes, str], dict[str, str]] | None = None


def read_resume_for_upload(data: bytes, filename: str = "") -> dict[str, str]:
    if _reader_override is not None:
        return _reader_override(data, filename)
    return read_resume(data, filename)
