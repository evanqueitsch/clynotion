"""The Roles screen must keep a saved dropdown after reload."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_role_dropdown_survives_reload(tmp_path) -> None:
    from cryptography.fernet import Fernet
    from playwright.sync_api import sync_playwright

    port = _free_port()
    env = {
        "ATTUNE_MODE": "mock",
        "ATTUNE_AUTH": "dev",
        "ATTUNE_ACCESS_PERSISTENCE": "file",
        "ATTUNE_ACCESS_DATA_PATH": str(tmp_path / "roles.enc"),
        "ATTUNE_DATA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ATTUNE_HR_TIME_PERSISTENCE": "memory",
        "ATTUNE_CLINICIAN_PERSISTENCE": "memory",
        "TIME_MAIL_MODE": "off",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd="/agent/repos/clynotion",
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("local server did not start")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.fill("#username", "alice")
            page.fill("#password", "alice-pass")
            page.click("#btn-login")
            page.wait_for_selector("#btn-top-roles")
            page.click("#btn-top-roles")
            page.wait_for_selector("#role-assignments select")
            save = page.locator("#btn-save-assignments")
            assert save.is_disabled()
            select = page.locator("#role-assignments select").first
            select.select_option("intake")
            assert save.is_enabled()
            save.click()
            page.wait_for_selector("#access-status.ok")
            assert "Saved" in page.locator("#access-status").inner_text()
            page.reload(wait_until="networkidle")
            page.wait_for_selector("#role-assignments select")
            assert page.locator("#role-assignments select").first.input_value() == "intake"
            page.screenshot(path="/opt/cursor/artifacts/role-saved-after-reload.png", full_page=True)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
