"""Hard total-deadline tests against a local trickle server.

The trickle server sends a valid JSON body in 0.4s chunks; per-read
timeouts alone would never fire. The adapter must abort at the
monotonic deadline (deadline + 0.5s) with the expected ProviderError.
A normal slow single response within its per-read timeout is kept.
"""

from __future__ import annotations

import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.adapters.crm_http import HttpCrmAdapter
from app.ports.crm import CallContext, CreateContact, ProviderError

BODY = b'{"id":"sim-1","email":"a@b.c","firstname":"A","lastname":null,"phone":null}'


class _TrickleHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _trickle(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        # 0.4s chunks: ~10 chunks for this body, far past a 2s budget
        # if only completion is checked, chunk gaps under any read timeout.
        chunk = 8
        for i in range(0, len(BODY), chunk):
            try:
                self.wfile.write(BODY[i:i + chunk])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(0.4)

    do_GET = _trickle
    do_POST = _trickle


class _SlowHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        time.sleep(0.6)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _ctx(deadline: float) -> CallContext:
    return CallContext(None, uuid.uuid4(), 1, 1, uuid.uuid4(), deadline)


def test_trickle_body_read_aborts_at_total_deadline():
    server, base = _serve(_TrickleHandler)
    adapter = HttpCrmAdapter(base, "t")
    try:
        start = time.monotonic()
        with pytest.raises(ProviderError) as exc:
            adapter.get_contact_by_email("a@b.c", _ctx(2.0))
        elapsed = time.monotonic() - start
        assert exc.value.code == "unavailable"
        assert elapsed <= 2.0 + 0.5, elapsed
    finally:
        adapter.close()
        server.shutdown()


def test_trickle_body_write_aborts_at_total_deadline():
    server, base = _serve(_TrickleHandler)
    adapter = HttpCrmAdapter(base, "t")
    try:
        start = time.monotonic()
        with pytest.raises(ProviderError) as exc:
            adapter.create_contact(CreateContact(email="a@b.c", firstname="A"), _ctx(2.0))
        elapsed = time.monotonic() - start
        assert exc.value.code == "result_unknown"
        assert elapsed <= 2.0 + 0.5, elapsed
    finally:
        adapter.close()
        server.shutdown()


def test_slow_single_response_within_deadline_succeeds():
    server, base = _serve(_SlowHandler)
    adapter = HttpCrmAdapter(base, "t")
    try:
        found = adapter.get_contact_by_email("a@b.c", _ctx(5.0))
        assert found.remote_id == "sim-1"
    finally:
        adapter.close()
        server.shutdown()


def test_invalid_base_url_rejected():
    with pytest.raises(ValueError):
        HttpCrmAdapter("notaurl", "t")
    with pytest.raises(ValueError):
        HttpCrmAdapter("ftp://127.0.0.1:1", "t")


def test_invalid_url_maps_to_provider_error():
    # A non-numeric port passes urlparse host validation; httpx raises
    # InvalidURL at request time and it must not escape as-is.
    adapter = HttpCrmAdapter("http://127.0.0.1:abc", "t")
    try:
        with pytest.raises(ProviderError) as exc:
            adapter.get_contact_by_email("a@b.c", _ctx(2.0))
        assert exc.value.code == "invalid_response"
    finally:
        adapter.close()
