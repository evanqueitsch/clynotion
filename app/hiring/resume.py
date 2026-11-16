"""Read a resume into offer fields.

Production calls Vertex Gemini on the Google Cloud project. Tests and mock
mode stay on this machine. Values that are not in the resume text are dropped,
and nothing from the resume is written to logs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import zipfile
from io import BytesIO
from typing import Any, Callable
from xml.etree import ElementTree

import httpx

logger = logging.getLogger("app.hiring.resume")

MAX_RESUME_BYTES = 10 * 1024 * 1024
DEFAULT_MODEL = "gemini-2.5-flash"
FLASH_VARIANTS = ("gemini-3-flash", "gemini-3.1-flash", "gemini-3-flash-001", "gemini-2.5-flash")
VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_MODEL_CACHE_SECONDS = 3600
_model_cache: tuple[float, str] | None = None
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

    def __init__(
        self,
        message: str,
        *,
        stage: str = "",
        http_status: int = 0,
        google_status: str = "",
        reason: str = "",
        model: str = "",
        location: str = "",
        attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.http_status = http_status
        self.google_status = google_status
        self.reason = reason
        self.model = model
        self.location = location
        self.attempts = list(attempts or [])


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


def resume_secret() -> str:
    """GOOGLE_SA_JSON wins. GOOGLE_DRIVE_SA_JSON is the fallback so one key can read resumes."""
    return (os.environ.get("GOOGLE_SA_JSON") or os.environ.get("GOOGLE_DRIVE_SA_JSON") or "").strip()


def resume_configured() -> bool:
    """True only when a service-account key and a Cloud project are present. No network."""
    raw = resume_secret()
    if not raw:
        return False
    try:
        data = json.loads(raw) if raw.lstrip().startswith("{") else json.loads(
            __import__("pathlib").Path(raw).read_text(encoding="utf-8")
        )
    except Exception:
        return False
    if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
        return False
    project = (os.environ.get("GOOGLE_CLOUD_PROJECT") or str(data.get("project_id") or "")).strip()
    return bool(project)


def read_resume(data: bytes, filename: str = "") -> dict[str, str]:
    mode = resume_mode()
    if mode == "gemini" and not resume_configured():
        raise ResumeReadError("Resume reading is not configured.")
    if len(data) > MAX_RESUME_BYTES:
        raise ResumeReadError("That resume is larger than 10 MB.")
    text = _text_from(data, filename)
    if not text.strip():
        raise ResumeReadError("That resume has no readable text.")
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
    if suffix in {"txt", "text"} or (not suffix and _looks_textual(data)):
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
        _log_failure(stage="text_extraction", exc=exc)
        raise ResumeReadError("That resume could not be read.", stage="text_extraction") from exc
    return "\n".join(parts)


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            xml = package.read("word/document.xml")
        root = ElementTree.fromstring(xml)
    except Exception as exc:
        _log_failure(stage="text_extraction", exc=exc)
        raise ResumeReadError("That resume could not be read.", stage="text_extraction") from exc
    texts: list[str] = []
    paragraph = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
    run_text = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    for node in root.iter(paragraph):
        line = "".join(item.text or "" for item in node.iter(run_text))
        if line.strip():
            texts.append(line.strip())
    return "\n".join(texts)


def _txt_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResumeReadError("That resume could not be read.") from exc


def _gemini_fields(text: str) -> dict[str, str]:
    try:
        raw, _model, _location, _attempts = _gemini_complete(_PROMPT + text)
    except ResumeReadError:
        raise
    except Exception as exc:
        _log_failure(stage="vertex", exc=exc)
        raise ResumeReadError("The resume could not be read.", stage="vertex") from exc
    try:
        parsed = _json_object(raw)
    except ValueError as exc:
        _log_failure(stage="vertex_response", exc=exc)
        raise ResumeReadError("The resume could not be read.", stage="vertex_response") from exc
    if not isinstance(parsed, dict):
        _log_failure(stage="vertex_response")
        raise ResumeReadError("The resume could not be read.", stage="vertex_response")
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


def _gemini_complete(text: str) -> tuple[str, str, str, list[dict[str, Any]]]:
    token = _gemini_token()
    project = _gemini_project()
    attempts: list[dict[str, Any]] = []
    last_error: ResumeReadError | None = None
    for location, model in _model_attempts(token):
        try:
            reply = _generate(project, location, model, token, text)
        except ResumeReadError as exc:
            attempts.append(
                {"model": model, "location": location, "status": exc.http_status or exc.google_status or "error"}
            )
            exc.attempts = list(attempts)
            last_error = exc
            if exc.google_status == "NOT_FOUND" or exc.http_status == 404:
                continue
            raise
        attempts.append({"model": model, "location": location, "status": 200})
        return reply, model, location, attempts
    if last_error is not None:
        last_error.attempts = attempts
        raise last_error
    raise ResumeReadError("The resume could not be read.", stage="vertex", attempts=attempts)


def clear_model_cache() -> None:
    global _model_cache
    _model_cache = None


def _model_attempts(token: str) -> list[tuple[str, str]]:
    models: list[str] = []
    override = (os.environ.get("GEMINI_MODEL") or "").strip()
    if override:
        models.append(override)
    newest = _newest_flash_model(token)
    if newest and newest not in models:
        models.append(newest)
    for name in FLASH_VARIANTS:
        if name not in models:
            models.append(name)
    locations = ["global"]
    regional = (os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-east4").strip() or "us-east4"
    if regional not in locations:
        locations.append(regional)
    if "us-central1" not in locations:
        locations.append("us-central1")
    attempts: list[tuple[str, str]] = []
    for place in locations:
        for model in models:
            item = (place, model)
            if item not in attempts:
                attempts.append(item)
    return attempts


def _newest_flash_model(token: str) -> str:
    global _model_cache
    now = time.time()
    if _model_cache and now - _model_cache[0] < _MODEL_CACHE_SECONDS:
        return _model_cache[1]
    try:
        catalog = _list_publisher_models(token)
    except Exception as exc:
        _log_failure(stage="model_list", exc=exc)
        return ""
    picked = _choose_ga_flash(catalog)
    if picked:
        _model_cache = (now, picked)
    return picked


def _list_publisher_models(token: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    page = ""
    for _ in range(5):
        params: dict[str, str] = {"pageSize": "100"}
        if page:
            params["pageToken"] = page
        response = httpx.get(
            "https://aiplatform.googleapis.com/v1/publishers/google/models",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15.0,
        )
        if response.status_code >= 400:
            info = _google_error(response)
            _log_failure(stage="model_list", **info)
            break
        body = response.json() if response.content else {}
        if not isinstance(body, dict):
            break
        batch = body.get("publisherModels") or body.get("models") or []
        if isinstance(batch, list):
            found.extend(item for item in batch if isinstance(item, dict))
        page = str(body.get("nextPageToken") or "")
        if not page:
            break
    return found


def _choose_ga_flash(catalog: list[dict[str, Any]]) -> str:
    ranked: list[tuple[tuple[int, int], str]] = []
    for item in catalog:
        model_id = str(item.get("name") or "").rsplit("/", 1)[-1]
        if not _ga_flash(item, model_id):
            continue
        ranked.append((_version_key(model_id), model_id))
    if not ranked:
        return ""
    ranked.sort(reverse=True)
    return ranked[0][1]


def _ga_flash(item: dict[str, Any], model_id: str) -> bool:
    stage = str(item.get("launchStage") or "").upper()
    if stage not in {"GA", "LAUNCH_STAGE_GA"}:
        return False
    if not re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash(?:@\d+)?", model_id):
        return False
    return "preview" not in model_id and "exp" not in model_id


def _version_key(model_id: str) -> tuple[int, int]:
    match = re.search(r"gemini-(\d+)(?:\.(\d+))?", model_id)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2) or 0))


def _generate(project: str, location: str, model: str, token: str, text: str) -> str:
    host = "https://aiplatform.googleapis.com" if location == "global" else f"https://{location}-aiplatform.googleapis.com"
    url = (
        f"{host}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": text[:20000]}]}],
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
        _log_failure(stage="vertex", model=model, location=location, exc=exc)
        raise ResumeReadError("The resume could not be read.", stage="vertex", model=model, location=location) from exc
    if response.status_code >= 400:
        info = _google_error(response)
        _log_failure(stage="vertex", model=model, location=location, **info)
        raise ResumeReadError(
            "The resume could not be read.",
            stage="vertex",
            http_status=int(info["http_status"] or 0),
            google_status=str(info["google_status"]),
            reason=str(info["reason"]),
            model=model,
            location=location,
        )
    try:
        payload = response.json()
        return str(payload["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as exc:
        _log_failure(stage="vertex_response", model=model, location=location, exc=exc)
        raise ResumeReadError(
            "The resume could not be read.",
            stage="vertex_response",
            model=model,
            location=location,
        ) from exc


def _gemini_project() -> str:
    account = _service_account()
    project = (os.environ.get("GOOGLE_CLOUD_PROJECT") or str(account.get("project_id") or "")).strip()
    if not project:
        raise ResumeReadError("Resume reading is not configured.", stage="not_configured")
    return project


def _gemini_target() -> tuple[str, str, str]:
    return _gemini_project(), "global", (os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _service_account() -> dict[str, Any]:
    import json
    from pathlib import Path

    raw = resume_secret()
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
            "scope": VERTEX_SCOPE,
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
        _log_failure(stage="token", exc=exc)
        raise ResumeReadError("The resume could not be read.", stage="token") from exc
    if response.status_code >= 400:
        info = _google_error(response)
        _log_failure(stage="token", **info)
        raise ResumeReadError(
            "The resume could not be read.",
            stage="token",
            http_status=int(info["http_status"] or 0),
            google_status=str(info["google_status"]),
            reason=str(info["reason"]),
        )
    token = str((response.json() or {}).get("access_token") or "")
    if not token:
        _log_failure(stage="token", http_status=response.status_code)
        raise ResumeReadError("The resume could not be read.", stage="token", http_status=response.status_code)
    return token


def _google_error(response: Any) -> dict[str, Any]:
    http_status = int(getattr(response, "status_code", 0) or 0)
    try:
        body = response.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return {"http_status": http_status, "google_status": "", "reason": "", "message": ""}
    error = body.get("error")
    if isinstance(error, str):
        return {
            "http_status": http_status,
            "google_status": error,
            "reason": error,
            "message": str(body.get("error_description") or ""),
        }
    if not isinstance(error, dict):
        return {"http_status": http_status, "google_status": "", "reason": "", "message": ""}
    reasons: list[str] = []
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reasons.append(str(detail["reason"]))
    return {
        "http_status": http_status,
        "google_status": str(error.get("status") or ""),
        "reason": ",".join(reasons),
        "message": str(error.get("message") or ""),
    }


def _sanitize(value: str) -> str:
    text = " ".join(str(value or "").split())
    text = _EMAIL.sub("[email]", text)
    return text[:180]


def _log_failure(
    *,
    stage: str,
    http_status: int = 0,
    google_status: str = "",
    reason: str = "",
    message: str = "",
    model: str = "",
    location: str = "",
    exc: BaseException | None = None,
) -> None:
    logger.warning(
        "resume_read_failed stage=%s http_status=%s google_status=%s reason=%s model=%s location=%s exception=%s detail=%s",
        stage,
        http_status or "",
        google_status,
        reason,
        model,
        location,
        type(exc).__name__ if exc else "",
        _sanitize(message),
    )


def reader_health() -> dict[str, Any]:
    """A tiny non-PHI check. Mock mode does not call Vertex."""
    mode = resume_mode()
    pypdf_ok = _pypdf_installed()
    if mode == "mock":
        return {"ok": True, "mode": "mock", "model": "", "chosen_model": "", "location": "", "attempts": [], "scope": VERTEX_SCOPE, "pypdf": pypdf_ok}
    if not resume_configured():
        return {
            "ok": False,
            "mode": mode,
            "stage": "not_configured",
            "error": "Resume reading is not configured.",
            "scope": VERTEX_SCOPE,
            "pypdf": pypdf_ok,
        }
    try:
        _reply, model, location, attempts = _gemini_complete("Reply with the single word ok.")
    except ResumeReadError as exc:
        return {
            "ok": False,
            "mode": "gemini",
            "stage": exc.stage,
            "http_status": exc.http_status,
            "google_status": exc.google_status,
            "reason": exc.reason,
            "model": exc.model,
            "chosen_model": exc.model,
            "location": exc.location,
            "attempts": exc.attempts,
            "error": "The resume reader could not reach Vertex.",
            "scope": VERTEX_SCOPE,
            "pypdf": pypdf_ok,
        }
    return {
        "ok": True,
        "mode": "gemini",
        "model": model,
        "chosen_model": model,
        "location": location,
        "attempts": attempts,
        "scope": VERTEX_SCOPE,
        "pypdf": pypdf_ok,
    }


def _pypdf_installed() -> bool:
    try:
        import pypdf
    except Exception:
        return False
    return bool(getattr(pypdf, "__version__", "ok"))


def set_reader_for_tests(reader: Callable[[bytes, str], dict[str, str]] | None) -> None:
    global _reader_override
    _reader_override = reader


_reader_override: Callable[[bytes, str], dict[str, str]] | None = None


def read_resume_for_upload(data: bytes, filename: str = "") -> dict[str, str]:
    if _reader_override is not None:
        return _reader_override(data, filename)
    return read_resume(data, filename)
