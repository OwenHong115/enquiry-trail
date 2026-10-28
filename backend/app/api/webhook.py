"""Signed webhook intake (P3c, A27).

Route shape chosen for this build (not specified in the repo):
``POST /api/v1/webhooks/intake`` with headers
``X-Webhook-Timestamp`` (Unix seconds), ``X-Webhook-Signature``
(``sha256=<hex>``) and ``Idempotency-Key`` (UUID, the intake event
key). The signature is ``HMAC-SHA256(WEBHOOK_SECRET, "<timestamp>."
+ raw_body)``. A delivery is fresh only within
``WEBHOOK_REPLAY_WINDOW_SECONDS`` of the database clock, and each
signature may be delivered only once: a replay is rejected before
intake, independently of intake idempotency, so a replay under a
fresh Idempotency-Key still creates nothing, while a fresh
signature under a used Idempotency-Key is an ordinary intake
replay. No session or CSRF token is required; the signature is
the credential. Accepted deliveries reuse the intake service with
source ``webhook``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.display import format_display_no
from app.api.errors import ApiError
from app.api.intake import IntakeDataIn, IntakeReceiptOut
from app.db.session import get_session
from app.domain.normalization import normalize_phone
from app.security import ratelimit
from app.services.intake import IdempotencyConflict, submit_intake

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def build_signature(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def verify_signature(
    secret: str, timestamp: int, body: bytes, supplied: str | None
) -> bool:
    if not secret or not supplied:
        return False
    return hmac.compare_digest(build_signature(secret, timestamp, body), supplied)


@router.post("/intake", status_code=202, response_model=IntakeReceiptOut)
async def webhook_intake(
    request: Request,
    session: Session = Depends(get_session),
    timestamp: str | None = Header(default=None, alias="X-Webhook-Timestamp"),
    signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
    idempotency_key: uuid.UUID = Header(..., alias="Idempotency-Key"),
):
    settings = request.app.state.settings
    body = await request.body()

    try:
        ts = int(timestamp) if timestamp is not None else None
    except ValueError:
        ts = None
    if ts is None or not verify_signature(settings.webhook_secret, ts, body, signature):
        raise ApiError(401, "webhook_signature_invalid", "Webhook signature is invalid")

    now = ratelimit.database_now(session)
    delivered_at = datetime.fromtimestamp(ts, tz=timezone.utc)
    if abs((now - delivered_at).total_seconds()) > settings.webhook_replay_window_seconds:
        raise ApiError(401, "webhook_signature_invalid", "Webhook signature is invalid")

    # Replay check, separate from intake idempotency: the signature
    # itself may be delivered only once, whatever event key it carries.
    assert signature is not None
    if not ratelimit.claim_webhook_signature(
        session,
        signature=signature,
        timestamp=ts,
        replay_window_seconds=settings.webhook_replay_window_seconds,
    ):
        raise ApiError(409, "webhook_replay", "Webhook delivery was already accepted")

    try:
        payload = IntakeDataIn.model_validate(json.loads(body))
    except (ValueError, ValidationError) as err:
        field_errors = []
        if isinstance(err, ValidationError):
            field_errors = [
                {
                    "path": ".".join(str(part) for part in error["loc"]),
                    "message": error["msg"],
                }
                for error in err.errors()
            ]
        raise ApiError(
            422, "validation_failed", "Request validation failed",
            field_errors=field_errors,
        ) from err

    try:
        normalize_phone(payload.phone)
    except ValueError as err:
        raise ApiError(
            422, "validation_failed", "Request validation failed",
            field_errors=[{"path": "phone", "message": str(err)}],
        ) from err

    try:
        result = submit_intake(
            session,
            payload.model_dump(),
            source="webhook",
            event_key=idempotency_key,
            clock=request.app.state.clock,
            routing_config=request.app.state.routing_config,
        )
    except IdempotencyConflict as err:
        raise ApiError(
            409, "idempotency_conflict", "Idempotency key conflict"
        ) from err

    return IntakeReceiptOut(
        receipt_id=result.receipt_id,
        enquiry_id=result.enquiry_id,
        display_no=format_display_no(result.display_no),
        accepted=True,
        replayed=result.replayed,
        deduplication=result.deduplication,  # type: ignore[arg-type]
        status_url=f"/api/v1/enquiries/{result.enquiry_id}",
    )
