"""Drop regenerated home seeds. Keep rows that are not catalog placeholders."""

from __future__ import annotations

import base64
import json
import os
import signal
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DUE = Path("/data/due.enc")
CRED = Path("/data/credentials.enc")
DATA = Path("/data")


def emit(payload: dict) -> None:
    print("HOME_CLOCKS " + json.dumps(payload, sort_keys=True), flush=True)


def adopt_uvicorn_env() -> list[int]:
    pids: list[int] = []
    adopted = False
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            continue
        if b"uvicorn" not in cmd or b"app.main:app" not in cmd:
            continue
        pids.append(int(pid))
        if adopted:
            continue
        for item in raw.split(b"\0"):
            if b"=" not in item:
                continue
            key, value = item.split(b"=", 1)
            os.environ[key.decode()] = value.decode()
        adopted = True
    if not pids:
        raise SystemExit("uvicorn process not found")
    return pids


def load_due() -> list:
    from app.crypto import decrypt_utf8

    wrapper = json.loads(DUE.read_text(encoding="utf-8"))
    rows = json.loads(decrypt_utf8(base64.b64decode(str(wrapper["blob"]))))
    if not isinstance(rows, list):
        raise SystemExit("due store is not a list")
    return rows


def recorded_credential_refs() -> set[str]:
    if not CRED.is_file():
        return set()
    from app.crypto import decrypt_utf8

    try:
        wrapper = json.loads(CRED.read_text(encoding="utf-8"))
        payload = json.loads(decrypt_utf8(base64.b64decode(str(wrapper.get("blob") or ""))))
    except (ValueError, json.JSONDecodeError, OSError, KeyError):
        return set()
    practices = payload.get("practices") if isinstance(payload, dict) else None
    refs: set[str] = set()
    if not isinstance(practices, dict):
        return refs
    for bucket in practices.values():
        if not isinstance(bucket, dict):
            continue
        for clinician_id, clocks in bucket.items():
            if not isinstance(clocks, dict):
                continue
            for clock_id, completed in clocks.items():
                if completed:
                    refs.add(f"{clinician_id}:{clock_id}")
    return refs


def keep_row(row: dict, recorded: set[str]) -> bool:
    source = str(row.get("source") or "")
    ref = str(row.get("source_ref") or "")
    if source in ("compliance_registry", "ops2"):
        return False
    if source == "ingest" and ref.startswith("stale:"):
        return False
    if source == "credential_clocks":
        return ref in recorded
    return True


def write_due(rows: list) -> None:
    from app.crypto import encrypt_utf8

    blob = encrypt_utf8(json.dumps(rows))
    payload = json.dumps({"version": 1, "blob": base64.b64encode(blob).decode("ascii")})
    tmp = DUE.with_name("due.enc.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, DUE)
    dir_fd = os.open(DATA, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def counts(rows: list) -> dict:
    return {
        "count": len(rows),
        "by_source": dict(Counter(str(row.get("source")) for row in rows if isinstance(row, dict))),
        "by_status": dict(Counter(str(row.get("status")) for row in rows if isinstance(row, dict))),
    }


def main() -> None:
    pids = adopt_uvicorn_env()
    for pid in pids:
        os.kill(pid, signal.SIGSTOP)
    try:
        original = DUE.read_bytes()
        rows = load_due()
        recorded = recorded_credential_refs()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = DATA / f"due.enc.bak-{stamp}"
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, original)
            os.fsync(fd)
        finally:
            os.close(fd)
        if backup.read_bytes() != original:
            raise SystemExit("backup mismatch")
        kept = [row for row in rows if isinstance(row, dict) and keep_row(row, recorded)]
        write_due(kept)
        after = load_due()
        if len(after) != len(kept):
            raise SystemExit("read-back count mismatch")
        emit(
            {
                "backup": str(backup),
                "backup_bytes": len(original),
                "bytes_match": True,
                "recorded_credential_dates": len(recorded),
                "before": counts(rows),
                "after": counts(after),
                "kept_sources": sorted({str(row.get("source")) for row in after}),
            }
        )
    except BaseException:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass
        raise
    for pid in pids:
        os.kill(pid, signal.SIGKILL)


if __name__ == "__main__":
    main()
