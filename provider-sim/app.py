"""Independent CRM provider simulator (local-test only).

Standalone stdlib HTTP process (the "or equivalent" of FastAPI+uvicorn)
with in-memory storage. It does not import the EnquiryTrail application,
does not use PostgreSQL or the leadops schema, and must never be exposed
publicly: fault injection and test counters live here. Auth is a static
bearer token from SIM_TOKEN (default "sim-token" for local runs). Logs
never print tokens, emails, phones or request bodies.

Run:  SIM_TOKEN=sim-token python app.py   (PORT env, default 8091)
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

TOKEN = os.environ.get("SIM_TOKEN", "sim-token")
_lock = threading.Lock()
_contacts: dict[str, dict] = {}
_by_email: dict[str, str] = {}
_counts = {"lookup_email": 0, "lookup_id": 0, "create": 0, "patch": 0, "probe": 0}
_fault = {"mode": "none", "path": "any"}


def _take_fault(path: str) -> str:
    with _lock:
        if _fault["path"] in ("any", path):
            mode = _fault["mode"]
            _fault["mode"] = "none"
            _fault["path"] = "any"
            return mode
    return "none"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # no request lines/bodies in logs
        pass

    # -- helpers ---------------------------------------------------
    def _send(self, status: int, body, headers: dict | None = None, raw: bool = False):
        data = body if raw else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _auth_ok(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def _fault(self, mode: str, commit=None):
        """Apply a fault; returns True when a response was sent."""
        if mode == "none":
            return False
        if mode in ("http_503_before_write",):
            self._send(503, {"error": "unavailable"})
            return True
        if mode == "http_401_before_write":
            self._send(401, {"error": "auth_failed"})
            return True
        if mode == "http_429_before_write":
            self._send(429, {"error": "rate_limited"}, {"Retry-After": "7"})
            return True
        if mode == "drop_success_response":
            if commit is not None:
                commit()
            self._send(500, {"error": "internal_after_commit"})
            return True
        if mode == "malformed":
            self._send(200, b"{not-json", raw=True)
            return True
        if mode == "slow":
            time.sleep(float(os.environ.get("SIM_SLOW_SECONDS", "2")))
            return False
        if mode == "rate_limited":
            self._send(429, {"error": "rate_limited"}, {"Retry-After": "7"})
            return True
        if mode == "error_before_write":
            self._send(500, {"error": "internal"})
            return True
        if mode in ("error_after_commit", "timeout_after_commit", "disconnect_after_commit"):
            if commit is not None:
                commit()
            if mode == "error_after_commit":
                self._send(500, {"error": "internal_after_commit"})
            elif mode == "disconnect_after_commit":
                # Real reset: commit, then close without any response
                # so the client observes RemoteProtocolError.
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    self.connection.close()
                except OSError:
                    pass
            else:
                time.sleep(float(os.environ.get("SIM_SLOW_SECONDS", "2")))
                self._send(500, {"error": "after_commit_timeout"})
            return True
        return False

    # -- routes ------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/__test__/counts":
            with _lock:
                self._send(200, {"counts": dict(_counts), "contacts": len(_contacts)})
            return
        if path in ("/health", "/probe"):
            with _lock:
                _counts["probe"] += 1
            if not self._auth_ok():
                self._send(401, {"error": "unauthorized"})
                return
            if self._fault(_take_fault("probe")):
                return
            self._send(200, {"ok": True, "supports_patch": True})
            return
        if path.startswith("/contacts/by-email/"):
            with _lock:
                _counts["lookup_email"] += 1
            if not self._auth_ok():
                self._send(401, {"error": "unauthorized"})
                return
            if self._fault(_take_fault("lookup")):
                return
            email = unquote(path[len("/contacts/by-email/"):])
            cid = _by_email.get(email)
            if cid is None:
                self._send(404, {"error": "not_found"})
            else:
                self._send(200, _contacts[cid])
            return
        if path.startswith("/contacts/"):
            with _lock:
                _counts["lookup_id"] += 1
            if not self._auth_ok():
                self._send(401, {"error": "unauthorized"})
                return
            if self._fault(_take_fault("lookup")):
                return
            contact = _contacts.get(unquote(path[len("/contacts/"):]))
            if contact is None:
                self._send(404, {"error": "not_found"})
            else:
                self._send(200, contact)
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/__test__/reset":
            self._body()
            with _lock:
                _contacts.clear(); _by_email.clear()
                for key in _counts:
                    _counts[key] = 0
                _fault["mode"] = "none"; _fault["path"] = "any"
            self._send(200, {"ok": True})
            return
        if path == "/__test__/fault":
            body = self._body()
            with _lock:
                _fault["mode"] = str(body.get("mode", "none"))
                _fault["path"] = str(body.get("path", "any"))
            self._send(200, {"ok": True})
            return
        if path == "/contacts":
            with _lock:
                _counts["create"] += 1
            if not self._auth_ok():
                self._send(401, {"error": "unauthorized"})
                return
            body = self._body()
            if not body.get("email") or not body.get("firstname"):
                self._send(422, {"error": "validation_failed"})
                return
            with _lock:
                duplicate = body["email"] in _by_email
            if duplicate:
                self._send(409, {"error": "duplicate"})
                return
            def commit():
                with _lock:
                    existing = _by_email.get(body["email"])
                    if existing is not None:
                        return _contacts[existing]
                    cid = f"sim-{uuid.uuid4().hex[:12]}"
                    contact = {"id": cid, "email": body["email"],
                               "firstname": body.get("firstname"),
                               "lastname": body.get("lastname"), "phone": body.get("phone")}
                    _contacts[cid] = contact
                    _by_email[body["email"]] = cid
                    return contact
            mode = _take_fault("create")
            if mode in ("error_after_commit", "timeout_after_commit", "disconnect_after_commit"):
                created = {}
                def _commit():
                    created.update(commit())
                if self._fault(mode, commit=_commit):
                    return
            elif mode == "slow":
                self._fault(mode)
                self._send(201, commit())
                return
            elif self._fault(mode):
                return
            else:
                self._send(201, commit())
                return
            self._send(201, created)
            return
        self._send(404, {"error": "not_found"})

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/contacts/"):
            with _lock:
                _counts["patch"] += 1
            if not self._auth_ok():
                self._send(401, {"error": "unauthorized"})
                return
            if self._fault(_take_fault("patch")):
                return
            contact = _contacts.get(unquote(path[len("/contacts/"):]))
            if contact is None:
                self._send(404, {"error": "not_found"})
                return
            body = self._body()
            for key in ("firstname", "lastname", "phone"):
                if key in body:
                    contact[key] = body[key]
            self._send(200, contact)
            return
        self._send(404, {"error": "not_found"})


def main() -> None:
    port = int(os.environ.get("PORT", "8091"))
    # Local runs stay loopback-only; Compose sets SIM_HOST=0.0.0.0 so
    # the worker container can reach this (still unpublished) service.
    host = os.environ.get("SIM_HOST", "127.0.0.1")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
