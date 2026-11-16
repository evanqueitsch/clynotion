"""UI check: correcting a field after a validation error saves the offer."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_resubmit_after_validation_error_reads_the_email() -> None:
    if not Path("/tmp/jsdom-run/node_modules/jsdom").exists():
        subprocess.run(
            ["npm", "install", "--prefix", "/tmp/jsdom-run", "jsdom@25"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    env = os.environ.copy()
    env["JSDOM_ROOT"] = "/tmp/jsdom-run"
    completed = subprocess.run(
        ["node", "tests/hiring_form_ui.mjs"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        cwd=Path(".").resolve(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout
