"""Due-engine encrypted file persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.platform.due import DueEngine
from app.platform.due_persist import FileDuePersistence


def test_due_file_persistence_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "due.enc"
    monkeypatch.setenv("ATTUNE_DUE_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_DUE_DATA_PATH", str(path))

    engine = DueEngine(persistence=FileDuePersistence(path))
    engine.upsert(
        practice_id="practice-a",
        domain="comply",
        source="ops2",
        title="o1: Exclusion screening",
        owner_user_id="user-alice",
        due_at="2026-08-20T00:00:00+00:00",
        href="/#comply",
        source_ref="o1",
    )
    assert path.is_file()
    raw = path.read_text(encoding="utf-8")
    assert "Exclusion" not in raw  # ciphertext wrapper only
    assert "blob" in raw

    engine2 = DueEngine(persistence=FileDuePersistence(path))
    open_items = engine2.list_open("practice-a")
    assert len(open_items) == 1
    assert open_items[0].source_ref == "o1"
