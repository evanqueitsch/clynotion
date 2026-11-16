"""Headless browser: a corrected offer save clears the error and does not duplicate."""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(port: int) -> None:
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + 20
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last = str(exc)
        time.sleep(0.2)
    raise AssertionError("server did not start: " + last)


def test_playwright_resave_after_validation_error_keeps_one_offer() -> None:
    pw_root = Path("/tmp/pw-run")
    if not (pw_root / "node_modules" / "playwright").exists():
        subprocess.run(
            ["npm", "install", "--prefix", str(pw_root), "playwright@1.49.1"],
            check=True,
            timeout=180,
        )
        subprocess.run(
            [str(pw_root / "node_modules" / ".bin" / "playwright"), "install", "chromium"],
            check=True,
            timeout=180,
        )
    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "ATTUNE_MODE": "mock",
            "ATTUNE_AUTH": "dev",
            "ATTUNE_ASR": "mock",
            "ATTUNE_LLM": "mock",
            "ATTUNE_ONBOARDING_PERSISTENCE": "memory",
            "ATTUNE_OFFER_PDF": "stub",
            "ATTUNE_HIRING_SCHEDULER": "off",
            "ATTUNE_CLINICIAN_PERSISTENCE": "memory",
            "ATTUNE_PERSISTENCE": "memory",
            "ATTUNE_ACCESS_PERSISTENCE": "memory",
            "FILE_STORE_BACKEND": "local",
            "TIME_MAIL_MODE": "off",
            "ATTUNE_ONBOARDING_FILES_PATH": tempfile.mkdtemp(prefix="clynotion-hiring-browser-"),
        }
    )
    env.pop("GOOGLE_CLIENT_ID", None)
    env.pop("GOOGLE_CLIENT_SECRET", None)
    server = subprocess.Popen(
        ["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_health(port)
        env["PLAYWRIGHT_ROOT"] = str(pw_root)
        env["APP_BASE"] = f"http://127.0.0.1:{port}"
        completed = subprocess.run(
            ["node", "tests/hiring_save_browser.mjs"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=Path(".").resolve(),
        )
        output = completed.stdout + completed.stderr
        assert completed.returncode == 0, output
        assert "PASS" in completed.stdout
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
