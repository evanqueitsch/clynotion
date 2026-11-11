"""Live-API round trip for one reversible role. Prints statuses only."""

from __future__ import annotations

import json
import urllib.request

from app.access.store import AccessStore
from app.auth import User, issue_token

TARGET = "intake@heart4change.org"
OWNER = "operations@heart4change.org"


def call(token: str, method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8080" + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def person(staff: list, email: str) -> dict:
    return next(row for row in staff if row["email"] == email)


def main() -> None:
    token = issue_token(User("verify-roles", "Evan Queitsch", "practice-hfc", OWNER))
    staff = call(token, "GET", "/access/staff")
    before = person(staff, TARGET)
    owner = person(staff, OWNER)
    print(f"BEFORE target={before['role_id']!r} owner={owner['role_id']!r}", flush=True)
    if before["role_id"] not in ("", "intake"):
        raise SystemExit("refusing to change a role that is already set")
    if owner["role_id"] != "admin":
        raise SystemExit("owner assignment is not admin")
    call(token, "PUT", "/access/assignments", {"email": TARGET, "role_id": "intake"})
    after = person(call(token, "GET", "/access/staff"), TARGET)
    disk = AccessStore().role_id_for("practice-hfc", TARGET)
    print(f"AFTER_API target={after['role_id']!r} AFTER_DISK target={disk!r}", flush=True)
    if after["role_id"] != "intake" or disk != "intake":
        raise SystemExit("assignment did not persist")
    call(token, "PUT", "/access/assignments", {"email": TARGET, "role_id": ""})
    cleared = person(call(token, "GET", "/access/staff"), TARGET)
    owner_after = person(call(token, "GET", "/access/staff"), OWNER)
    print(f"CLEARED target={cleared['role_id']!r} owner={owner_after['role_id']!r}", flush=True)
    if cleared["role_id"] or owner_after["role_id"] != "admin":
        raise SystemExit("cleanup failed")


if __name__ == "__main__":
    main()
