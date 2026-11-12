"""Server-side permission checks for HTTP routes."""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from app.access.permissions import MANAGE_ROLES, PAYROLL_DASHBOARD, TIME_ENTRY
from app.access.resolve import has_permission, is_owner
from app.auth import COOKIE_NAME, decode_token

_PUBLIC_PREFIXES = (
    "/health",
    "/auth/",
    "/static/",
    "/internal/time/tick",
)


def required_permission(path: str) -> str | None:
    """Return the permission required for ``path``, or None if any signed-in user may call it."""
    if path.startswith("/hr/time/settings") or path.startswith("/hr/time/self-test"):
        return MANAGE_ROLES
    if path.startswith("/hr/time/directory") or path.startswith("/hr/time/gmail-send"):
        return MANAGE_ROLES
    if path.startswith("/hr/time/week"):
        return PAYROLL_DASHBOARD
    if path.startswith("/hr/time"):
        return TIME_ENTRY
    if path.startswith("/access/roles") or path.startswith("/access/assignments") or path.startswith("/access/staff"):
        return MANAGE_ROLES
    if path.startswith("/access/me"):
        return None
    if path.startswith("/credentialing"):
        return "tool:credentialing"
    if path.startswith("/employee/me"):
        return "tool:employee_view"
    if path.startswith("/employee"):
        return "capability:hr_records_view"
    if path.startswith("/billing"):
        return "tool:billing"
    if path.startswith("/quickbooks"):
        return "capability:quickbooks_money"
    if path.startswith("/hr/"):
        return "tool:onboarding_compliance"
    if path.startswith("/sessions") or path.startswith("/draft") or path.startswith("/finalize") or path.startswith("/clinicians") or path.startswith("/capture"):
        return "tool:supervision_notes"
    if path.startswith("/workspace"):
        return MANAGE_ROLES
    if path.startswith("/comply") or path.startswith("/due"):
        return "tool:compliance_registry"
    if path.startswith("/ingest"):
        return "tool:sp_ingest"
    if path.startswith("/intake"):
        return "tool:intake_log"
    if path.startswith("/eligibility"):
        return "tool:eligibility"
    if path.startswith("/debug"):
        return MANAGE_ROLES
    if path.startswith("/catalog"):
        return None
    if path in ("/home", "/practice") or path.startswith("/attune"):
        return None
    return MANAGE_ROLES


def _user_from_request(request: Request):
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    try:
        return decode_token(token)
    except HTTPException:
        return None


async def access_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path == "/" or any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
        return await call_next(request)
    needed = required_permission(path)
    if needed is None and path not in ("/home", "/catalog", "/practice") and not path.startswith("/attune"):
        return await call_next(request)
    user = _user_from_request(request)
    if user is None:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    if needed and not (is_owner(user) or has_permission(user, needed)):
        return JSONResponse({"detail": "You do not have access to that."}, status_code=403)
    return await call_next(request)
