"""Provider-sim test diagnostic: exact-email contact lookup.

Spins the real provider-sim (stdlib HTTP, no database) on a free port,
creates contacts through its authenticated create route, then reads
them back through the unauthenticated __test__ diagnostic the A26
compose recovery script uses to prove remote uniqueness per target.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
TOKEN = "test-sim-token"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def sim():
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, SIM_TOKEN=TOKEN, PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "provider-sim" / "app.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/__test__/counts", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("provider-sim did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _create(sim: str, email: str, firstname: str) -> dict:
    response = httpx.post(
        f"{sim}/contacts",
        json={"email": email, "firstname": firstname, "lastname": "Target"},
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=5,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_test_contacts_returns_only_exact_email_matches(sim):
    httpx.post(f"{sim}/__test__/reset", timeout=5)
    target = _create(sim, "target@example.com", "Target")
    _create(sim, "other@example.com", "Other")
    _create(sim, "target@example.com.au", "Near")

    # No Authorization header: same unauthenticated __test__ namespace
    # style as /__test__/counts.
    response = httpx.get(
        f"{sim}/__test__/contacts",
        params={"email": "target@example.com"},
        timeout=5,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1, body
    assert body["contacts"] == [target], body


def test_test_contacts_unknown_email_is_empty_not_an_error(sim):
    response = httpx.get(
        f"{sim}/__test__/contacts",
        params={"email": "nobody@example.com"},
        timeout=5,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"contacts": [], "count": 0}


def test_test_contacts_missing_email_is_validation_failed(sim):
    response = httpx.get(f"{sim}/__test__/contacts", timeout=5)
    assert response.status_code == 422, response.text
    assert response.json() == {"error": "validation_failed"}
