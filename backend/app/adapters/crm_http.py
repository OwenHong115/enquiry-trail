"""HTTP CRM adapter: the real CrmPort over httpx, no hidden retries.

Wire contract: docs/crm-http-contract.md. One HTTP request per port
call (create's definite-409 readback is a read, never a second write).
Every transport error is mapped to ProviderError; httpx exceptions
never leak. Logs carry operation/job ids, status and timing only.
"""

from __future__ import annotations

import time
from urllib.parse import quote

import httpx

from app.logging import log_event
from app.ports.crm import (
    CallContext,
    CreateContact,
    EmptyFieldPatch,
    FoundContact,
    NotFound,
    ProbeResult,
    ProviderError,
)

_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 5.0
_WRITE_TIMEOUT = 5.0
_POOL_TIMEOUT = 3.0


class HttpCrmAdapter:
    """CrmPort over HTTP. `client` is injectable for tests that still
    use a real socket transport (never a retrying transport)."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None) -> None:
        if not base_url.strip():
            raise ValueError("CRM base_url must not be empty")
        if not token.strip():
            raise ValueError("CRM token must not be empty")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT,
                                  write=_WRITE_TIMEOUT, pool=_POOL_TIMEOUT)
        )

    # -- internals -------------------------------------------------
    def _remaining(self, context: CallContext, start: float) -> float:
        deadline = context.deadline_seconds if context.deadline_seconds > 0 else 15.0
        return deadline - (time.monotonic() - start)

    def _request(self, method: str, path: str, context: CallContext, *,
                 write: bool, json_body: dict | None = None) -> httpx.Response:
        start = time.monotonic()
        remaining = self._remaining(context, start)
        if remaining <= 0:
            raise ProviderError("result_unknown" if write else "unavailable")
        timeout = httpx.Timeout(connect=min(_CONNECT_TIMEOUT, remaining),
                                read=min(_READ_TIMEOUT, remaining),
                                write=min(_WRITE_TIMEOUT, remaining),
                                pool=min(_POOL_TIMEOUT, remaining))
        try:
            response = self._client.request(
                method, f"{self._base_url}{path}", json=json_body,
                headers={"Authorization": f"Bearer {self._token}"}, timeout=timeout,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            self._log(context, method, "connect_failed", start)
            raise ProviderError("connect_failed") from exc
        except httpx.TimeoutException as exc:
            code = "result_unknown" if write else "unavailable"
            self._log(context, method, code, start)
            raise ProviderError(code) from exc
        except httpx.HTTPError as exc:
            # Reset / remote-protocol / read errors after possible send.
            code = "result_unknown" if write else "unavailable"
            self._log(context, method, code, start)
            raise ProviderError(code) from exc
        if time.monotonic() - start > (context.deadline_seconds or 15.0):
            code = "result_unknown" if write else "unavailable"
            self._log(context, method, code, start)
            raise ProviderError(code)
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

    # -- CrmPort ---------------------------------------------------
    def get_contact_by_email(self, email_norm: str, context: CallContext) -> FoundContact | NotFound:
        response = self._request("GET", f"/contacts/by-email/{quote(email_norm, safe='')}",
                                 context, write=False)
        if response.status_code == 404:
            return NotFound(email=email_norm)
        self._raise_for_status(response, write=False, notfound_is_error=False)
        return self._parse_contact(response)

    def get_contact_by_id(self, remote_id: str, context: CallContext) -> FoundContact | NotFound:
        response = self._request("GET", f"/contacts/{quote(remote_id, safe='')}",
                                 context, write=False)
        if response.status_code == 404:
            return NotFound(remote_id=remote_id)
        self._raise_for_status(response, write=False, notfound_is_error=False)
        return self._parse_contact(response)

    def create_contact(self, fields: CreateContact, context: CallContext) -> FoundContact:
        body: dict = {"email": fields.email, "firstname": fields.firstname}
        if fields.lastname is not None:
            body["lastname"] = fields.lastname
        if fields.phone is not None:
            body["phone"] = fields.phone
        response = self._request("POST", "/contacts", context, write=True, json_body=body)
        if response.status_code == 409:
            # Already exists remotely: read back by email and link.
            # Never a second create. A missing readback keeps conflict.
            found = self.get_contact_by_email(fields.email, context)
            if isinstance(found, FoundContact):
                return found
            raise ProviderError("conflict", http_status=409)
        self._raise_for_status(response, write=True, notfound_is_error=True)
        return self._parse_contact(response)

    def patch_contact(self, remote_id: str, fields: EmptyFieldPatch, context: CallContext) -> FoundContact:
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
