"""Candidate portal pages. The token is exchanged for a cookie and never logged."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.hiring.engine import HiringError, hiring

router = APIRouter()
COOKIE = "clynotion_join"
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="referrer" content="no-referrer" />
  <title>Heart for Change</title>
  <style>
    body { font-family: Georgia, serif; background: #f4f7f5; color: #1a2421; margin: 0; }
    main { max-width: 40rem; margin: 2rem auto; background: #fff; padding: 1.5rem; border-radius: 12px; }
    button, input, select { font: inherit; padding: 0.5rem 0.7rem; }
    .error { color: #8b2e2e; }
    iframe { width: 100%; height: 70vh; border: 1px solid #ccc; }
    a { color: #0f6e56; }
  </style>
</head>
<body>
<main>
  <h1>Heart for Change</h1>
  <p id="msg"></p>
  <form id="code-form" hidden>
    <label>Sign-in code <input id="code" inputmode="numeric" autocomplete="one-time-code" /></label>
    <button type="submit">Continue</button>
    <button type="button" id="resend">Email me a code</button>
  </form>
  <div id="offer" hidden>
    <p id="letter-meta"></p>
    <iframe id="letter" title="Offer letter" src="/join/letter"></iframe>
    <form id="accept-form">
      <label>Type your full legal name to accept <input id="typed" autocomplete="name" /></label>
      <button type="submit">Accept</button>
    </form>
    <form id="decline-form">
      <label>Decline
        <select id="reason">
          <option value="">No reason given</option>
          <option value="accepted_another">Accepted another offer</option>
          <option value="pay">Pay</option>
          <option value="schedule">Schedule</option>
          <option value="location">Location</option>
          <option value="other">Other</option>
        </select>
      </label>
      <button type="submit">Decline</button>
    </form>
  </div>
  <div id="packet" hidden></div>
  <p>Lists of acceptable I-9 documents: <a href="https://www.uscis.gov/i-9-central/form-i-9-acceptable-documents" rel="noreferrer">USCIS</a>. Bring the documents you choose. Heart for Change will not tell you which ones.</p>
</main>
<script>
async function api(path, body) {
  const res = await fetch(path, { method: body ? "POST" : "GET", headers: body ? {"Content-Type": "application/json"} : {}, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong");
  return data;
}
function say(text) { document.getElementById("msg").textContent = text; }
async function load() {
  try {
    const state = await api("/join/session");
    if (!state.verified) {
      document.getElementById("code-form").hidden = false;
      say("Enter the code sent to your email.");
      return;
    }
    if (state.kind === "offer") {
      document.getElementById("offer").hidden = false;
      document.getElementById("letter-meta").textContent = state.position + " — respond by " + state.deadline;
      say("Read the letter, then accept or decline.");
      return;
    }
    const packet = document.getElementById("packet");
    packet.hidden = false;
    say("Your onboarding checklist");
    (state.items || []).forEach((item) => {
      const p = document.createElement("p");
      p.textContent = item.title + " — " + item.status;
      packet.appendChild(p);
    });
  } catch (error) {
    say(error.message);
  }
}
document.getElementById("resend").onclick = async () => {
  try { await api("/join/code", {}); say("A code was sent."); }
  catch (error) { say(error.message); }
};
document.getElementById("code-form").onsubmit = async (event) => {
  event.preventDefault();
  try { await api("/join/verify", { code: document.getElementById("code").value }); location.reload(); }
  catch (error) { say(error.message); }
};
document.getElementById("accept-form").onsubmit = async (event) => {
  event.preventDefault();
  try { await api("/join/accept", { typed_name: document.getElementById("typed").value }); say("Accepted. Your onboarding checklist is ready."); location.reload(); }
  catch (error) { say(error.message); }
};
document.getElementById("decline-form").onsubmit = async (event) => {
  event.preventDefault();
  try { await api("/join/decline", { reason: document.getElementById("reason").value, note: "" }); say("The offer was declined."); }
  catch (error) { say(error.message); }
};
load();
</script>
</body>
</html>
"""


def _headers(response: Response) -> None:
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _fail(exc: HiringError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.detail)


def _secret(request: Request) -> str:
    return request.cookies.get(COOKIE, "")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:80]
    if request.client:
        return request.client.host
    return ""


class CodeBody(BaseModel):
    code: str = ""


class AcceptBody(BaseModel):
    typed_name: str = Field(min_length=1, max_length=200)


class DeclineBody(BaseModel):
    reason: str = ""
    note: str = ""


@router.get("/join/{token}")
def exchange(token: str, request: Request) -> RedirectResponse:
    try:
        hiring.rate_limit(_client_ip(request) or "join", limit=30, window=60)
        secret = hiring.open_token(token, _client_ip(request), request.headers.get("user-agent", ""))
    except HiringError as exc:
        _fail(exc)
    response = RedirectResponse("/join", status_code=303)
    response.set_cookie(COOKIE, secret, httponly=True, secure=True, samesite="strict", max_age=900, path="/join")
    _headers(response)
    return response


@router.get("/join", response_class=HTMLResponse)
def portal_page() -> HTMLResponse:
    response = HTMLResponse(_PAGE)
    _headers(response)
    return response


@router.get("/join/session")
def portal_session(request: Request) -> dict:
    try:
        return hiring.portal_state(_secret(request))
    except HiringError as exc:
        _fail(exc)


@router.post("/join/code")
def portal_code(request: Request) -> dict:
    try:
        hiring.rate_limit(_client_ip(request) or "code", limit=10, window=600)
        hiring.request_code(_secret(request), str(request.base_url))
    except HiringError as exc:
        _fail(exc)
    return {"ok": True}


@router.post("/join/verify")
def portal_verify(body: CodeBody, request: Request) -> dict:
    try:
        hiring.verify_code(_secret(request), body.code)
    except HiringError as exc:
        _fail(exc)
    return {"ok": True}


@router.post("/join/accept")
def portal_accept(body: AcceptBody, request: Request) -> dict:
    try:
        return hiring.portal_accept(_secret(request), body.typed_name, _client_ip(request), request.headers.get("user-agent", ""))
    except HiringError as exc:
        _fail(exc)


@router.post("/join/decline")
def portal_decline(body: DeclineBody, request: Request) -> dict:
    try:
        hiring.portal_decline(_secret(request), body.reason, body.note, _client_ip(request))
    except HiringError as exc:
        _fail(exc)
    return {"ok": True}


@router.get("/join/letter")
def portal_letter(request: Request) -> Response:
    try:
        data, name = hiring.portal_pdf(_secret(request))
    except HiringError as exc:
        _fail(exc)
    response = Response(content=data, media_type="application/pdf")
    response.headers["Content-Disposition"] = f'inline; filename="{name}"'
    _headers(response)
    return response
