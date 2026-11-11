"""Copy an encrypted file before a migration writes it. Never logs file contents."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

_done: set[str] = set()
_names: list[str] = []


def backup_names() -> list[str]:
    return list(_names)


def backup_once(path: Path) -> str:
    """Copy ``path`` once per process. Returns the backup filename, or empty when there is no file."""
    key = str(path.resolve()) if path.exists() else str(path)
    if key in _done or not path.is_file():
        return ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = path.with_name(f"{path.name}.bak-0.18.0-{stamp}")
    shutil.copy2(path, dest)
    _done.add(key)
    _names.append(dest.name)
    return dest.name
