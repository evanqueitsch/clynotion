"""Remember a decrypted file until its modification time changes.

Each read parses a new object. A caller can edit that copy without changing
the cached text. The cache stays in this process and is not written back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional


class DecryptedFileCache:
    def __init__(self) -> None:
        self._stamp: Optional[int] = None
        self._text: Optional[str] = None

    def clear(self) -> None:
        self._stamp = None
        self._text = None

    def read_json(self, path: Path, decrypt: Callable[[bytes], str]) -> Optional[dict[str, Any]]:
        if not path.is_file():
            return None
        stamp = path.stat().st_mtime_ns
        if self._stamp != stamp or self._text is None:
            self._text = decrypt(path.read_bytes())
            self._stamp = stamp
        payload = json.loads(self._text or "{}")
        if not isinstance(payload, dict):
            return None
        return payload

    def remember(self, path: Path, payload: dict[str, Any]) -> None:
        self._text = json.dumps(payload)
        try:
            self._stamp = path.stat().st_mtime_ns
        except OSError:
            self.clear()
