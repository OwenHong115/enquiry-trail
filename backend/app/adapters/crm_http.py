"""HTTP CRM adapter: the real CrmPort over httpx, no hidden retries.

Wire contract: docs/crm-http-contract.md. One HTTP request per port
call (create's definite-409 readback is a read, never a second write).
Every transport error is mapped to ProviderError; httpx exceptions
never leak. Logs carry operation/job ids, status and timing only.

Total deadline: each call runs on a worker thread joined with the
remaining monotonic budget. On expiry the in-flight owned client is
closed (interrupting the socket read, including a trickled body) and
the expected ProviderError is raised at deadline + a small grace, so
elapsed time is bounded by the deadline, not by response completion.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import quote, urlparse

import httpx

from app.logging import log_event
from app.ports.crm import (
    CallContext,
    CreateContact,
    ContactFieldPatch,
    FoundContact,
    NotFound,
    ProbeResult,
    ProviderError,
)

_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 5.0
_WRITE_TIMEOUT = 5.0
_POOL_TIMEOUT = 3.0
_ABORT_GRACE_SECONDS = 0.3


def validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("CRM_BASE_URL must be an http(s) URL with a host")
    return base_url.rstrip("/")


class HttpCrmAdapter:
    """CrmPort over HTTP. An injected `client` stays caller-owned; an
    adapter-owned client is per-request and always closed."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None) -> None:
        self._base_url = validate_base_url(base_url)
        if not token.strip():
            raise ValueError("CRM token must not be empty")
        self._token = token
        self._client = client
        self._owns_client = client is None
        self._inflight: set[httpx.Client] = set()
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------
    def close(self) -> None:
        """Close adapter-owned clients deterministically (in-flight)."""
        if self._client is not None and self._owns_client:
            self._client.close()
        with self._lock:
            inflight = list(self._inflight)
        for client in inflight:
            try:
                client.close()
            except Exception:
                pass

    def __enter__(self) -> "HttpCrmAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ---------------------------------------------------
    @staticmethod
    def _total(context: CallContext) -> float:
        return context.deadline_seconds if context.deadline_seconds > 0 else 15.0

    def _request(self, method: str, path: str, context: CallContext, *,
                 write: bool, json_body: dict | None = None,
                 _start: float | None = None, _total: float | None = None) -> httpx.Response:
        start = _start if _start is not None else time.monotonic()
        total = _total if _total is not None else self._total(context)
        remaining = total - (time.monotonic() - start)
        timeout_code = "result_unknown" if write else "unavailable"
        if remaining <= 0:
            self._log(context, method, timeout_code, start)
            raise ProviderError(timeout_code)
        timeout = httpx.Timeout(connect=min(_CONNECT_TIMEOUT, remaining),
                                read=min(_READ_TIMEOUT, remaining),
                                write=min(_WRITE_TIMEOUT, remaining),
                                pool=min(_POOL_TIMEOUT, remaining))
        holder: dict = {}

        client_ready = threading.Event()

        def run() -> None:
            client = self._client
            owned_here = False
            holder["client"] = client
            client_ready.set()
            if client is None:
                client = httpx.Client(timeout=timeout)
                owned_here = True
                holder["client"] = client
                client_ready.set()
                with self._lock:
                    self._inflight.add(client)
            try:
                holder["response"] = client.request(
                    method, f"{self._base_url}{path}", json=json_body,
                    headers={"Authorization": f"Bearer {self._token}"}, timeout=timeout,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                holder["error"] = ProviderError("connect_failed")
                holder["cause"] = exc
            except httpx.InvalidURL as exc:
                holder["error"] = ProviderError("invalid_response")
                holder["cause"] = exc
            except httpx.TimeoutException as exc:
                holder["error"] = ProviderError(timeout_code)
                holder["cause"] = exc
            except httpx.HTTPError as exc:
                holder["error"] = ProviderError(timeout_code)
                holder["cause"] = exc
            except Exception as exc:  # never leak a transport-layer error
                holder["error"] = ProviderError(timeout_code)
                holder["cause"] = exc
            finally:
                if owned_here:
                    with self._lock:
                        self._inflight.discard(client)  # type: ignore[arg-type]
                    try:
                        client.close()  # type: ignore[union-attr]
                    except Exception:
                        pass

        thread = threading.Thread(target=run, daemon=True, name="crm-http-call")
        thread.start()
        thread.join(remaining)
        if thread.is_alive():
            # Hard abort at the deadline: closing the owned client
            # interrupts the blocked socket read (headers or trickled
            # body); an injected client is caller-owned, so the daemon
            # thread is abandoned and its per-read timeout bounds it.
            if self._owns_client:
                # Close only this call's client, never a concurrent
                # call's: adapter.close() is for full shutdown.
                client_ready.wait(_ABORT_GRACE_SECONDS)
                current = holder.get("client")
                if current is not None:
                    try:
                        current.close()
                    except Exception:
                        pass
            thread.join(_ABORT_GRACE_SECONDS)
            self._log(context, method, timeout_code, start)
            raise ProviderError(timeout_code)
        if "error" in holder:
            error: ProviderError = holder["error"]
            self._log(context, method, error.code, start)
            raise error from holder.get("cause")
        response: httpx.Response = holder["response"]
        self._log(context, method, str(response.status_code), start)
        return response

    @staticmethod
    def _log(context: CallContext, method: str, status: str, start: float) -> None:
        log_event("crm.http", job_id=str(context.job_id),
                  operation_id=str(context.external_operation_id or ""),
                  method=method, status=status,
                  elapsed_ms=round((time.monotonic() - start) * 1000, 1))

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _raise_for_status(self, response: httpx.Response, *, write: bool,
                          notfound_is_error: bool) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 404 and notfound_is_error:
            raise ProviderError("not_found", http_status=404)
        if status in (400, 422):
            raise ProviderError("validation_failed", http_status=status)
        if status in (401, 403):
            raise ProviderError("auth_failed", http_status=status)
        if status == 409:
            raise ProviderError("conflict", http_status=409)
        if status == 429:
            raise ProviderError("rate_limited", http_status=429,
                                retry_after_seconds=self._retry_after(response))
        if status >= 500:
            raise ProviderError("result_unknown" if write else "unavailable",
                                http_status=status)
        raise ProviderError("invalid_response", http_status=status)

    @staticmethod
    def _parse_contact(response: httpx.Response) -> FoundContact:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("invalid_response") from exc
        if not isinstance(data, dict) or not data.get("id") or not data.get("email"):
            raise ProviderError("invalid_response")
        return FoundContact(remote_id=str(data["id"]), email=str(data["email"]),
                            firstname=data.get("firstname"), lastname=data.get("lastname"),
                            phone=data.get("phone"))

    def _get_by_email(self, email_norm: str, context: CallContext,
                      start: float, total: float) -> FoundContact | NotFound:
        response = self._request("GET", f"/contacts/by-email/{quote(email_norm, safe='')}",
                                 context, write=False, _start=start, _total=total)
        if response.status_code == 404:
            return NotFound(email=email_norm)
        self._raise_for_status(response, write=False, notfound_is_error=False)
        return self._parse_contact(response)

    # -- CrmPort ---------------------------------------------------
    def get_contact_by_email(self, email_norm: str, context: CallContext) -> FoundContact | NotFound:
        return self._get_by_email(email_norm, context, time.monotonic(), self._total(context))

    def get_contact_by_id(self, remote_id: str, context: CallContext) -> FoundContact | NotFound:
        response = self._request("GET", f"/contacts/{quote(remote_id, safe='')}",
                                 context, write=False)
        if response.status_code == 404:
            return NotFound(remote_id=remote_id)
        self._raise_for_status(response, write=False, notfound_is_error=False)
        return self._parse_contact(response)

    def create_contact(self, fields: CreateContact, context: CallContext) -> FoundContact:
        # One budget for the whole call: the 409 readback spends only
        # what remains, never a fresh deadline.
        start = time.monotonic()
        total = self._total(context)
        body: dict = {"email": fields.email, "firstname": fields.firstname}
        if fields.lastname is not None:
            body["lastname"] = fields.lastname
        if fields.phone is not None:
            body["phone"] = fields.phone
        response = self._request("POST", "/contacts", context, write=True,
                                 json_body=body, _start=start, _total=total)
        if response.status_code == 409:
            found = self._get_by_email(fields.email, context, start, total)
            if isinstance(found, FoundContact):
                return found
            raise ProviderError("conflict", http_status=409)
        self._raise_for_status(response, write=True, notfound_is_error=True)
        return self._parse_contact(response)

    def patch_contact(self, remote_id: str, fields: ContactFieldPatch, context: CallContext) -> FoundContact:
        body: dict = {}
        if fields.firstname is not None:
            body["firstname"] = fields.firstname
        if fields.lastname is not None:
            body["lastname"] = fields.lastname
        if fields.phone is not None:
            body["phone"] = fields.phone
        response = self._request("PATCH", f"/contacts/{quote(remote_id, safe='')}",
                                 context, write=True, json_body=body)
        self._raise_for_status(response, write=True, notfound_is_error=True)
        return self._parse_contact(response)

    def probe(self, context: CallContext) -> ProbeResult:
        try:
            response = self._request("GET", "/probe", context, write=False)
        except ProviderError:
            return ProbeResult(ok=False, supports_patch=False)
        if response.status_code != 200:
            return ProbeResult(ok=False, supports_patch=False)
        try:
            data = response.json()
        except ValueError:
            return ProbeResult(ok=False, supports_patch=False)
        if not isinstance(data, dict):
            return ProbeResult(ok=False, supports_patch=False)
        return ProbeResult(ok=bool(data.get("ok")), supports_patch=bool(data.get("supports_patch")))
