"""The real Send button handler must show Gmail's rejection beside the button."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip("playwright.sync_api")

VIEW = {
    "banner": "TEST",
    "sender_note": "Verified sending mailbox.",
    "email": "operations@heart4change.org",
    "summary_recipient": "msmith@heart4change.org",
    "summary_note": "Not sent.",
    "mailbox": {
        "connected": True,
        "sender_email": "operations@heart4change.org",
        "status_detail": "Verified From address: operations@heart4change.org. Scheduled payroll mail stays off.",
        "scheduled_mail": "off",
    },
    "scheduled_mail": "off",
    "thursday": {
        "to": "operations@heart4change.org",
        "subject": "[TEST] Weekly hours are due tomorrow",
        "body": "Thursday body",
        "sent": False,
        "delivery": {
            "status": "failed",
            "last_error": "gmail_http_403",
            "gmail_message_id": "",
            "can_send": True,
        },
    },
    "friday": {
        "to": "operations@heart4change.org",
        "subject": "[TEST] Enter your weekly hours today!",
        "body": "Friday body",
        "sent": False,
        "delivery": {
            "status": "failed",
            "last_error": "gmail_http_403",
            "gmail_message_id": "",
            "can_send": True,
        },
    },
    "entry": {"hours": 5, "minutes": 10},
    "summary": {
        "to": "msmith@heart4change.org",
        "subject": "[TEST] Weekly hours summary",
        "body": "summary",
        "csv": "employee,email\n",
        "delivery": {"status": "", "last_error": "", "gmail_message_id": "", "can_send": True},
    },
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_reload_shows_rejected_sends_and_click_stays_beside_the_button(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd="/workspace",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    posts: list[str] = []
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
            browser = playwright.chromium.launch(
                channel="chrome",
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page()

            def fulfill_time(route) -> None:
                request = route.request
                if request.method == "POST":
                    posts.append(request.post_data or "")
                    body = dict(VIEW)
                    kind = "summary" if "summary" in (request.post_data or "") else "thursday"
                    if kind == "summary":
                        summary = dict(VIEW["summary"])
                        summary["delivery"] = {
                            "status": "uncertain",
                            "last_error": "gmail_uncertain",
                            "gmail_message_id": "",
                            "can_send": False,
                        }
                        body["summary"] = summary
                        body["send_result"] = {
                            "kind": "summary",
                            "status": "uncertain",
                            "last_error": "gmail_uncertain",
                            "gmail_message_id": "",
                            "duplicate": False,
                        }
                    else:
                        thursday = dict(VIEW["thursday"])
                        thursday["delivery"] = {
                            "status": "failed",
                            "last_error": "gmail_api_disabled",
                            "gmail_message_id": "",
                            "can_send": True,
                        }
                        body["thursday"] = thursday
                        body["send_result"] = {
                            "kind": "thursday",
                            "status": "failed",
                            "last_error": "gmail_api_disabled",
                            "gmail_message_id": "",
                            "duplicate": False,
                        }
                    route.fulfill(status=200, content_type="application/json", json=body)
                    return
                route.fulfill(status=200, content_type="application/json", json=VIEW)

            page.route("**/auth/config", lambda route: route.fulfill(
                json={"auth": "dev", "password_login": True, "google_login_url": None, "allowed_domains": []}
            ))
            page.route("**/auth/me", lambda route: route.fulfill(
                json={"user_id": "user-alice", "username": "alice", "practice_id": "practice-a", "email": "operations@heart4change.org"}
            ))
            page.route("**/hr/time/self-test**", fulfill_time)
            page.goto(f"http://127.0.0.1:{port}/#time-self-test", wait_until="networkidle")
            thursday = page.locator("#time-self-test-thursday-status")
            friday = page.locator("#time-self-test-friday-status")
            thursday.wait_for(state="visible")
            assert "did not accept" in thursday.inner_text()
            assert "610340555869" in thursday.inner_text()
            assert "did not accept" in friday.inner_text()
            page.screenshot(path="/opt/cursor/artifacts/self-test-send-status.png")
            page.click("#btn-send-thursday")
            page.wait_for_timeout(300)
            assert posts and "thursday" in posts[-1]
            assert "summary" not in "".join(posts)
            assert "gmail_api_disabled" in thursday.inner_text() or "610340555869" in thursday.inner_text()
            assert "Not sent" in thursday.inner_text()
            page.screenshot(path="/opt/cursor/artifacts/self-test-send-clicked.png")
            page.click("#btn-send-summary")
            page.wait_for_timeout(300)
            summary_text = page.locator("#time-self-test-summary-status").inner_text()
            general = page.locator("#time-self-test-status").inner_text()
            assert summary_text.count("will not be sent again") == 1
            assert "will not be sent again" not in general
            assert "summary" in posts[-1]
            page.screenshot(path="/opt/cursor/artifacts/self-test-summary-uncertain.png")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
