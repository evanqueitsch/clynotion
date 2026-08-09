"""FastAPI entrypoint — Attune shell + Clynotion capture; MOCK providers by default."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.audit import AuditAction, AuditReason, audit_log
from app.auth import (
    COOKIE_NAME,
    CurrentUser,
    User,
    auth_mode,
    issue_token,
    jwt_signing_secret,
    password_login_enabled,
    user_from_google,
    user_store,
)
from app.clinicians import clinician_store
from app.config import load_env_local, validate_startup_secrets
from app.google_oauth import (
    GoogleOAuthError,
    authorization_url,
    exchange_code,
    google_configured,
    make_state,
    oauth_public_diagnostics,
    probe_token_endpoint,
    verify_state,
)
from app.pipeline import (
    SpeakerMapIncompleteError,
    draft_from_audio,
    draft_from_transcript,
    finalize_session,
)
from app.providers import provider_modes
from app.schemas import (
    ClinicianOut,
    DraftResponse,
    FinalizeResponse,
    PresentClinicianOut,
    PresentMember,
    SupervisionOverrides,
    VoiceCheckinResponse,
    WorkspaceIncludeBody,
    WorkspaceUserOut,
)
from app.store import Session, store
from app.voice_id import get_voice_id_provider
from app.voice_match import verify_checkin
from app.workspace_directory import (
    WorkspaceDirectoryError,
    clear_directory_tokens,
    directory_authorization_url,
    directory_connection_status,
    directory_mode,
    exchange_directory_code,
    list_directory_users,
    save_directory_tokens,
)

load_env_local()
validate_startup_secrets()

_STATIC = Path(__file__).resolve().parent / "static"

APP_VERSION = "0.5.0"

app = FastAPI(
    title="Attune — Clynotion",
    description="Clinical supervision capture and notes (Phase 1 / 1b)",
    version=APP_VERSION,
)

_UPLOAD_ROOT = Path(tempfile.gettempdir()) / "clynotion_uploads"
_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

CaptureMode = Literal["session_surface", "meeting_bot"]


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthConfigResponse(BaseModel):
    auth: str
    password_login: bool
    google_login_url: Optional[str] = None
    redirect_uri: Optional[str] = None
    allowed_domains: list[str] = Field(default_factory=list)
    client_secret_set: bool = False
    client_id_suffix: str = ""


class MeResponse(BaseModel):
    user_id: str
    username: str
    practice_id: str
    email: str = ""


_OAUTH_STATE_COOKIE = "attune_oauth_state"
_DIRECTORY_STATE_COOKIE = "attune_directory_oauth_state"
_SESSION_TTL_SEC = 60 * 60 * 12


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_SESSION_TTL_SEC,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


class DraftJsonBody(BaseModel):
    transcript: str = Field(min_length=1)
    present: list[PresentMember] = Field(default_factory=list)
    capture_mode: CaptureMode = "session_surface"


class FinalizeBody(BaseModel):
    overrides: SupervisionOverrides = Field(default_factory=SupervisionOverrides)


class SessionReadResponse(BaseModel):
    session_id: str
    practice_id: str
    modality: str
    capture_mode: str
    finalized: bool
    fields: Any
    note: str
    unmapped_speakers: list[str] = Field(default_factory=list)
    present: list[PresentClinicianOut] = Field(default_factory=list)


class NoteResponse(BaseModel):
    session_id: str
    note: str


class MeetingBotStatus(BaseModel):
    available: bool
    detail: str


def _session_for_caller(session_id: str, user: User) -> Session:
    session = store.get(session_id)
    if session is None or session.practice_id != user.practice_id:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _resolve_present(
    user: User, present: list[PresentMember]
) -> list:
    if not present:
        return []
    try:
        return clinician_store.resolve_present(
            user.practice_id,
            [(p.clinician_id, p.role) for p in present],
        )
    except KeyError as e:
        raise HTTPException(
            status_code=404,
            detail=f"clinician not found in practice: {e.args[0]}",
        ) from e


def _draft_response(session: Session) -> DraftResponse:
    modes = provider_modes()
    used_vignette = modes["asr"] == "mock" or modes["llm"] == "mock"
    assignments = getattr(session, "voice_assignments", None) or []
    return DraftResponse(
        session_id=session.session_id,
        modality=session.modality,
        capture_mode=session.capture_mode,
        fields=session.fields,
        note=session.note,
        audio_path=session.audio_path,
        unmapped_speakers=session.fields.unmapped_speaker_labels(),
        asr_provider=modes["asr"],
        llm_provider=modes["llm"],
        used_vignette=used_vignette,
        present=session.present,
        roster_names=[p.display_name for p in session.present],
        voice_assignments=list(assignments),
    )


def _parse_present_form(present_json: Optional[str]) -> list[PresentMember]:
    if not present_json or not present_json.strip():
        return []
    try:
        raw = json.loads(present_json)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="present_json must be JSON") from e
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="present_json must be a list")
    return [PresentMember.model_validate(item) for item in raw]


@app.get("/health")
def health() -> dict[str, str]:
    modes = provider_modes()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "auth": auth_mode(),
        "product": "attune",
        "tool": "clynotion",
        "modality": "supervision",
        "asr": modes["asr"],
        "llm": modes["llm"],
    }


@app.get("/auth/config", response_model=AuthConfigResponse)
def auth_config() -> AuthConfigResponse:
    mode = auth_mode()
    diag = oauth_public_diagnostics() if mode == "google" else {}
    return AuthConfigResponse(
        auth=mode,
        password_login=password_login_enabled(),
        google_login_url="/auth/google/start" if mode == "google" else None,
        redirect_uri=str(diag.get("redirect_uri") or "") or None,
        allowed_domains=list(diag.get("allowed_domains") or []),
        client_secret_set=bool(diag.get("client_secret_set")),
        client_id_suffix=str(diag.get("client_id_suffix") or ""),
    )


@app.get("/auth/google/probe")
def google_probe() -> dict[str, object]:
    """Safe credential/redirect check (uses an invalid code on purpose)."""
    if auth_mode() != "google" or not google_configured():
        raise HTTPException(status_code=404, detail="Google auth not enabled")
    return probe_token_endpoint()


@app.get("/auth/me", response_model=MeResponse)
def auth_me(user: CurrentUser) -> MeResponse:
    return MeResponse(
        user_id=user.user_id,
        username=user.username,
        practice_id=user.practice_id,
        email=user.email,
    )


@app.post("/auth/logout")
def auth_logout() -> Response:
    response = Response(content='{"ok":true}', media_type="application/json")
    _clear_session_cookie(response)
    return response


@app.post("/auth/token", response_model=TokenResponse)
def login(body: TokenRequest, response: Response) -> TokenResponse:
    if not password_login_enabled():
        raise HTTPException(
            status_code=403,
            detail="password login disabled; use Google Workspace SSO",
        )
    user = user_store.authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = issue_token(user)
    # Dev convenience: also set cookie (Secure may be ignored on http://localhost by browsers).
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=_SESSION_TTL_SEC,
        path="/",
    )
    return TokenResponse(access_token=token)


@app.get("/auth/google/start")
def google_start() -> RedirectResponse:
    if auth_mode() != "google" or not google_configured():
        raise HTTPException(status_code=404, detail="Google auth not enabled")
    state = make_state(jwt_signing_secret())
    response = RedirectResponse(authorization_url(state), status_code=302)
    response.set_cookie(
        key=_OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@app.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> RedirectResponse:
    if auth_mode() != "google" or not google_configured():
        raise HTTPException(status_code=404, detail="Google auth not enabled")
    if error:
        return RedirectResponse("/?auth_error=google_denied", status_code=302)
    if not code or not state:
        return RedirectResponse("/?auth_error=google_missing", status_code=302)

    cookie_state = request.cookies.get(_OAUTH_STATE_COOKIE, "")
    if not cookie_state or cookie_state != state:
        return RedirectResponse("/?auth_error=google_state", status_code=302)
    if not verify_state(state, jwt_signing_secret()):
        return RedirectResponse("/?auth_error=google_state", status_code=302)

    try:
        identity = exchange_code(code)
    except PermissionError as e:
        msg = str(e)
        if "unverified" in msg:
            return RedirectResponse("/?auth_error=google_unverified", status_code=302)
        return RedirectResponse("/?auth_error=google_domain", status_code=302)
    except GoogleOAuthError as e:
        print("google_oauth_error", {"code": e.code, "detail": e.detail}, flush=True)
        return RedirectResponse(f"/?auth_error={e.code}", status_code=302)
    except Exception as e:
        print("google_oauth_unexpected", {"type": type(e).__name__}, flush=True)
        return RedirectResponse("/?auth_error=google_exchange", status_code=302)

    user = user_from_google(
        sub=identity.sub,
        email=identity.email,
        name=identity.name,
    )
    token = issue_token(user)
    response = RedirectResponse("/", status_code=302)
    _set_session_cookie(response, token)
    response.delete_cookie(key=_OAUTH_STATE_COOKIE, path="/")
    return response


@app.get("/clinicians", response_model=list[ClinicianOut])
def list_clinicians(user: CurrentUser) -> list[ClinicianOut]:
    return [
        ClinicianOut.model_validate(c.to_public_dict())
        for c in clinician_store.list_for_practice(user.practice_id)
    ]


@app.get("/workspace/status")
def workspace_status(user: CurrentUser) -> dict[str, Any]:
    return directory_connection_status(user.practice_id)


@app.get("/auth/google/directory/start")
def google_directory_start(user: CurrentUser) -> RedirectResponse:
    if auth_mode() != "google" or not google_configured():
        if directory_mode() != "mock":
            raise HTTPException(status_code=404, detail="Google auth not enabled")
    if directory_mode() == "off":
        raise HTTPException(status_code=404, detail="Workspace directory disabled")
    if directory_mode() == "mock":
        return RedirectResponse("/?directory=mock_ready", status_code=302)
    state = make_state(jwt_signing_secret())
    response = RedirectResponse(directory_authorization_url(state), status_code=302)
    response.set_cookie(
        key=_DIRECTORY_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@app.get("/auth/google/directory/callback")
def google_directory_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> RedirectResponse:
    if error:
        return RedirectResponse("/?directory_error=denied", status_code=302)
    if not code or not state:
        return RedirectResponse("/?directory_error=missing", status_code=302)
    cookie_state = request.cookies.get(_DIRECTORY_STATE_COOKIE, "")
    if not cookie_state or cookie_state != state:
        return RedirectResponse("/?directory_error=state", status_code=302)
    if not verify_state(state, jwt_signing_secret()):
        return RedirectResponse("/?directory_error=state", status_code=302)

    # Practice comes from the signed-in session cookie.
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/?directory_error=session", status_code=302)
    try:
        user = decode_token_safe(token)
    except Exception:
        return RedirectResponse("/?directory_error=session", status_code=302)

    try:
        tokens = exchange_directory_code(code)
        save_directory_tokens(
            user.practice_id,
            refresh_token=tokens.get("refresh_token") or "",
            access_token=tokens.get("access_token") or "",
            email=tokens.get("email") or "",
        )
    except WorkspaceDirectoryError:
        return RedirectResponse("/?directory_error=exchange", status_code=302)
    except Exception:
        return RedirectResponse("/?directory_error=exchange", status_code=302)

    response = RedirectResponse("/?directory=connected", status_code=302)
    response.delete_cookie(key=_DIRECTORY_STATE_COOKIE, path="/")
    return response


def decode_token_safe(token: str) -> User:
    from app.auth import decode_token

    return decode_token(token)


@app.get("/workspace/users", response_model=list[WorkspaceUserOut])
def workspace_users(user: CurrentUser) -> list[WorkspaceUserOut]:
    try:
        users = list_directory_users(user.practice_id)
    except WorkspaceDirectoryError as e:
        raise HTTPException(status_code=400, detail=e.code) from e
    existing = {
        c.google_id: c
        for c in clinician_store.list_all_for_practice(user.practice_id)
        if c.google_id
    }
    return [
        WorkspaceUserOut(
            google_id=u.google_id,
            email=u.email,
            display_name=u.display_name,
            suspended=u.suspended,
            org_unit=u.org_unit,
            already_included=bool(
                existing.get(u.google_id) and existing[u.google_id].included
            ),
        )
        for u in users
        if not u.suspended
    ]


@app.post("/workspace/include", response_model=list[ClinicianOut])
def workspace_include(user: CurrentUser, body: WorkspaceIncludeBody) -> list[ClinicianOut]:
    if body.clear_seed_roster:
        clinician_store.clear_seed_clinicians(user.practice_id)
    out: list[ClinicianOut] = []
    for member in body.members:
        clin = clinician_store.upsert_workspace_user(
            user.practice_id,
            google_id=member.google_id,
            email=member.email,
            display_name=member.display_name or member.email,
            default_role=member.default_role,
            included=True,
        )
        out.append(ClinicianOut.model_validate(clin.to_public_dict()))
    return out


@app.post("/workspace/exclude/{clinician_id}", response_model=ClinicianOut)
def workspace_exclude(clinician_id: str, user: CurrentUser) -> ClinicianOut:
    try:
        clin = clinician_store.set_included(
            user.practice_id, clinician_id, included=False
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail="clinician not found") from e
    return ClinicianOut.model_validate(clin.to_public_dict())


@app.delete("/workspace/connection")
def workspace_disconnect(user: CurrentUser) -> dict[str, bool]:
    clear_directory_tokens(user.practice_id)
    return {"ok": True}


@app.post("/clinicians/{clinician_id}/voice-enroll", response_model=ClinicianOut)
async def enroll_voice(
    clinician_id: str,
    user: CurrentUser,
    audio: UploadFile = File(...),
) -> ClinicianOut:
    """Accept a voice sample, store local embedding, delete sample immediately."""
    clin = clinician_store.get(user.practice_id, clinician_id)
    if clin is None:
        raise HTTPException(status_code=404, detail="clinician not found")
    dest = _UPLOAD_ROOT / f"enroll_{user.practice_id}_{clinician_id}_{audio.filename or 'sample.bin'}"
    try:
        with dest.open("wb") as f:
            shutil.copyfileobj(audio.file, f)
        size = dest.stat().st_size
        embedding = get_voice_id_provider().embed_file(str(dest))
        updated = clinician_store.enroll_voice(
            user.practice_id,
            clinician_id,
            sample_bytes=size,
            embedding=embedding,
        )
        audit_log.audit(
            AuditAction.VOICE_PROFILE_ENROLLED,
            f"clinician:{clinician_id}",
            reason=AuditReason.ENROLLMENT_STUB,
        )
        return ClinicianOut.model_validate(updated.to_public_dict())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=_safe_error_detail(e)) from e
    finally:
        if dest.is_file():
            dest.unlink()


@app.post("/clinicians/{clinician_id}/voice-checkin", response_model=VoiceCheckinResponse)
async def voice_checkin(
    clinician_id: str,
    user: CurrentUser,
    audio: UploadFile = File(...),
    present_json: Optional[str] = Form(default=None),
) -> VoiceCheckinResponse:
    """Verify a short check-in clip against the enrolled voice profile (local match)."""
    clin = clinician_store.get(user.practice_id, clinician_id)
    if clin is None:
        raise HTTPException(status_code=404, detail="clinician not found")
    present_ids: list[str] | None = None
    if present_json:
        members = _parse_present_form(present_json)
        present_ids = [m.clinician_id for m in members] or None
    dest = _UPLOAD_ROOT / f"checkin_{user.practice_id}_{clinician_id}_{audio.filename or 'sample.bin'}"
    try:
        with dest.open("wb") as f:
            shutil.copyfileobj(audio.file, f)
        result = verify_checkin(
            practice_id=user.practice_id,
            clinician_id=clinician_id,
            audio_path=str(dest),
            present_ids=present_ids,
        )
        return VoiceCheckinResponse.model_validate(result)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="clinician not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=_safe_error_detail(e)) from e
    finally:
        if dest.is_file():
            dest.unlink()


@app.delete("/clinicians/{clinician_id}/voice-enroll", response_model=ClinicianOut)
def clear_voice_enroll(clinician_id: str, user: CurrentUser) -> ClinicianOut:
    try:
        updated = clinician_store.clear_enrollment(user.practice_id, clinician_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="clinician not found") from e
    return ClinicianOut.model_validate(updated.to_public_dict())


@app.get("/capture/meeting-bot", response_model=MeetingBotStatus)
def meeting_bot_status(user: CurrentUser) -> MeetingBotStatus:
    _ = user
    return MeetingBotStatus(
        available=False,
        detail=(
            "Meeting bot (Zoom/Teams) is planned for a later phase and requires "
            "signed BAAs before any real session audio. Use session-surface capture for now."
        ),
    )


def _safe_error_detail(exc: BaseException) -> str:
    parts: list[str] = [type(exc).__name__]
    status = getattr(exc, "status_code", None)
    if status is not None:
        parts.append(f"status={status}")
    body = getattr(exc, "body", None)
    text = str(body) if body is not None else str(exc)
    lowered = text.lower()
    for needle in ("api_key", "authorization", "bearer ", "sk-ant-", "sk-"):
        if needle in lowered:
            text = "(redacted vendor error — check API key / account)"
            break
    text = " ".join(text.split())
    if len(text) > 280:
        text = text[:277] + "..."
    if text and text != type(exc).__name__:
        parts.append(text)
    modes = provider_modes()
    parts.append(f"asr={modes['asr']} llm={modes['llm']}")
    return " | ".join(parts)


def _create_draft(
    user: User,
    transcript: Optional[str],
    audio: Optional[UploadFile],
    *,
    present: list[PresentMember],
    capture_mode: CaptureMode,
) -> DraftResponse:
    if capture_mode == "meeting_bot":
        raise HTTPException(
            status_code=503,
            detail=(
                "Meeting bot capture is not enabled yet (BAA required). "
                "Use capture_mode=session_surface."
            ),
        )
    resolved = _resolve_present(user, present)
    try:
        if audio is not None and audio.filename:
            dest = _UPLOAD_ROOT / audio.filename
            stem, suffix = dest.stem, dest.suffix or ".webm"
            n = 0
            while dest.exists():
                n += 1
                dest = _UPLOAD_ROOT / f"{stem}_{n}{suffix}"
            with dest.open("wb") as f:
                shutil.copyfileobj(audio.file, f)
            if transcript:
                dest.with_suffix(".txt").write_text(transcript, encoding="utf-8")
            session = draft_from_audio(
                str(dest),
                practice_id=user.practice_id,
                present=resolved,
                capture_mode=capture_mode,
            )
        elif transcript:
            session = draft_from_transcript(
                transcript,
                practice_id=user.practice_id,
                present=resolved,
                capture_mode=capture_mode,
            )
        else:
            raise HTTPException(status_code=400, detail="Provide transcript and/or audio")
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=_safe_error_detail(e)) from e
    return _draft_response(session)


@app.post("/sessions/draft", response_model=DraftResponse)
async def create_session_draft(
    user: CurrentUser,
    transcript: Optional[str] = Form(default=None),
    audio: Optional[UploadFile] = File(default=None),
    present_json: Optional[str] = Form(default=None),
    capture_mode: CaptureMode = Form(default="session_surface"),
) -> DraftResponse:
    present = _parse_present_form(present_json)
    return _create_draft(
        user, transcript, audio, present=present, capture_mode=capture_mode
    )


@app.post("/sessions/draft/json", response_model=DraftResponse)
def create_session_draft_json(user: CurrentUser, body: DraftJsonBody) -> DraftResponse:
    return _create_draft(
        user,
        body.transcript,
        None,
        present=body.present,
        capture_mode=body.capture_mode,
    )


@app.get("/sessions/{session_id}", response_model=SessionReadResponse)
def read_session(session_id: str, user: CurrentUser) -> SessionReadResponse:
    session = _session_for_caller(session_id, user)
    return SessionReadResponse(
        session_id=session.session_id,
        practice_id=session.practice_id,
        modality=session.modality,
        capture_mode=session.capture_mode,
        finalized=session.finalized,
        fields=session.fields,
        note=session.note,
        unmapped_speakers=session.fields.unmapped_speaker_labels(),
        present=session.present,
    )


@app.get("/sessions/{session_id}/note", response_model=NoteResponse)
def read_note(session_id: str, user: CurrentUser) -> NoteResponse:
    session = _session_for_caller(session_id, user)
    return NoteResponse(session_id=session.session_id, note=session.note)


@app.post("/sessions/{session_id}/draft", response_model=DraftResponse)
def refresh_session_draft(session_id: str, user: CurrentUser) -> DraftResponse:
    session = _session_for_caller(session_id, user)
    if session.finalized:
        raise HTTPException(status_code=409, detail="session already finalized")
    return _draft_response(session)


@app.post("/sessions/{session_id}/finalize", response_model=FinalizeResponse)
def finalize_owned_session(
    session_id: str,
    user: CurrentUser,
    body: FinalizeBody | None = None,
) -> FinalizeResponse:
    _session_for_caller(session_id, user)
    overrides = (body.overrides if body else None) or SupervisionOverrides()
    try:
        session = finalize_session(session_id, overrides)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="session not found") from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SpeakerMapIncompleteError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "speaker_map_incomplete", "unmapped": e.unmapped},
        ) from e
    except Exception as e:
        raise HTTPException(status_code=422, detail=_safe_error_detail(e)) from e
    return FinalizeResponse(
        session_id=session.session_id,
        modality=session.modality,
        fields=session.fields,
        note=session.note,
        audio_deleted=bool(getattr(session, "_audio_deleted", True)),
    )


@app.post("/draft", response_model=DraftResponse)
async def create_draft_legacy(
    user: CurrentUser,
    transcript: Optional[str] = Form(default=None),
    audio: Optional[UploadFile] = File(default=None),
    present_json: Optional[str] = Form(default=None),
    capture_mode: CaptureMode = Form(default="session_surface"),
) -> DraftResponse:
    present = _parse_present_form(present_json)
    return _create_draft(
        user, transcript, audio, present=present, capture_mode=capture_mode
    )


@app.post("/draft/json", response_model=DraftResponse)
def create_draft_json_legacy(user: CurrentUser, body: DraftJsonBody) -> DraftResponse:
    return _create_draft(
        user,
        body.transcript,
        None,
        present=body.present,
        capture_mode=body.capture_mode,
    )


@app.post("/finalize", response_model=FinalizeResponse)
def finalize_legacy(user: CurrentUser, body: dict[str, Any]) -> FinalizeResponse:
    session_id = body.get("session_id")
    if not session_id or not isinstance(session_id, str):
        raise HTTPException(status_code=422, detail="session_id required")
    overrides_raw = body.get("overrides") or {}
    overrides = SupervisionOverrides.model_validate(overrides_raw)
    return finalize_owned_session(session_id, user, FinalizeBody(overrides=overrides))


@app.get("/debug/sessions_count")
def sessions_count(user: CurrentUser) -> dict[str, Any]:
    n = sum(1 for s in store._sessions.values() if s.practice_id == user.practice_id)
    return {"count": n}


@app.get("/")
def clynotion_ui() -> FileResponse:
    index = _STATIC / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index)


if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
