"""One controlled clear of /data/due.enc. Backup first. No other files."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DUE_PATH = Path("/data/due.enc")
DATA_DIR = Path("/data")


def emit(payload: dict) -> None:
    print("DUE_RESET " + json.dumps(payload, sort_keys=True), flush=True)


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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(DATA_DIR.iterdir()):
        if path.is_file():
            out[path.name] = sha256(path.read_bytes())
    return out


def load_due(path: Path) -> list:
    from app.crypto import decrypt_utf8

    wrapper = json.loads(path.read_text(encoding="utf-8"))
    blob = base64.b64decode(str(wrapper["blob"]))
    rows = json.loads(decrypt_utf8(blob))
    if not isinstance(rows, list):
        raise SystemExit("due store is not a list")
    return rows


def write_empty(path: Path) -> None:
    from app.crypto import encrypt_utf8

    blob = encrypt_utf8(json.dumps([]))
    payload = json.dumps(
        {"version": 1, "blob": base64.b64encode(blob).decode("ascii")}
    )
    tmp = path.with_name("due.enc.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dir_fd = os.open(DATA_DIR, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def summarize(rows: list) -> dict:
    return {
        "count": len(rows),
        "practices": sorted({str(row.get("practice_id")) for row in rows if isinstance(row, dict)}),
        "by_status": dict(Counter(str(row.get("status")) for row in rows if isinstance(row, dict))),
        "by_source": dict(Counter(str(row.get("source")) for row in rows if isinstance(row, dict))),
    }


def freeze(pids: list[int]) -> None:
    for pid in pids:
        os.kill(pid, signal.SIGSTOP)


def kill(pids: list[int]) -> None:
    for pid in pids:
        os.kill(pid, signal.SIGKILL)


def clear() -> None:
    pids = adopt_uvicorn_env()
    freeze(pids)
    try:
        if not DUE_PATH.is_file():
            raise SystemExit("due.enc missing")
        original = DUE_PATH.read_bytes()
        before_rows = load_due(DUE_PATH)
        summary = summarize(before_rows)
        practices = summary["practices"]
        if practices != ["practice-hfc"]:
            raise SystemExit("refusing to clear: practices are " + ",".join(practices))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = DATA_DIR / f"due.enc.bak-{stamp}"
        if backup.exists():
            raise SystemExit("backup path already exists")
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, original)
            os.fsync(fd)
        finally:
            os.close(fd)
        read_back = backup.read_bytes()
        if read_back != original or len(read_back) != len(original):
            raise SystemExit("backup read-back mismatch")
        backup_rows = load_due(backup)
        if backup_rows != before_rows:
            raise SystemExit("backup plaintext mismatch")
        before_hashes = file_hashes()
        write_empty(DUE_PATH)
        after_rows = load_due(DUE_PATH)
        if after_rows != []:
            raise SystemExit("empty store did not read back as an empty list")
        after_hashes = file_hashes()
        changed = sorted(
            name
            for name in set(before_hashes) | set(after_hashes)
            if before_hashes.get(name) != after_hashes.get(name)
            and name not in {DUE_PATH.name, backup.name}
        )
        if changed:
            raise SystemExit("unexpected file changes: " + ",".join(changed))
        emit(
            {
                "action": "cleared",
                "backup": str(backup),
                "original_bytes": len(original),
                "backup_bytes": len(read_back),
                "bytes_match": True,
                "plaintext_match": True,
                "before": summary,
                "after_count": 0,
                "backup_sha256": sha256(read_back),
                "other_files_unchanged": True,
            }
        )
    except BaseException:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass
        raise
    kill(pids)


def verify() -> None:
    adopt_uvicorn_env()
    backups = sorted(DATA_DIR.glob("due.enc.bak-*"))
    rows = load_due(DUE_PATH) if DUE_PATH.is_file() else None
    payload = {
        "action": "verify",
        "due_count": None if rows is None else len(rows),
        "due_bytes": DUE_PATH.stat().st_size if DUE_PATH.is_file() else 0,
        "backups": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())}
            for path in backups
        ],
        "files": sorted(path.name for path in DATA_DIR.iterdir() if path.is_file()),
    }
    emit(payload)


if __name__ == "__main__":
    mode = (os.environ.get("DUE_RESET_MODE") or "verify").strip().lower()
    if mode == "clear":
        clear()
    elif mode == "verify":
        verify()
    else:
        raise SystemExit("unknown mode")
