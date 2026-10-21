"""Intake submission: persist one enquiry as a single atomic unit."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import intake_repo
from app.domain.normalization import normalize_email, normalize_phone
from app.domain.routing import RoutingDecision, evaluate_routing, routing_config_hash
from app.ports.clock import BusinessClock

# Referenced by the clock contract for follow_up_tasks.due_at but not
# yet defined in the contract package. One calendar day: a renovation
# lead is followed up the next day, which keeps the demo timeline
# readable. The operator will record the settled value in the
# contract; changing it is a one-line edit here.
FOLLOWUP_DUE_MINUTES = 1440

TASK_TITLE = "Initial follow-up"

_PAYLOAD_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "postcode",
    "service_type",
    "message",
    "preferred_contact",
    "acknowledge_demo",
)
_FINGERPRINT_FIELDS = tuple(f for f in _PAYLOAD_FIELDS if f != "acknowledge_demo")


def _nfc(value: Any) -> Any:
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else value


def _canonical_hash(obj: dict[str, Any]) -> str:
    canonical = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _review_reason_codes(
    payload: dict[str, Any], decision: RoutingDecision, config: dict[str, Any]
) -> list[str]:
    if not decision.review:
        return []
    mapping: dict[str, str] = config.get("missing_review_reasons", {})
    codes: list[str] = []
    if payload.get("postcode") is None and "postcode" in mapping:
        codes.append(mapping["postcode"])
    if payload.get("service_type") == "unsure" and "service_type" in mapping:
        codes.append(mapping["service_type"])
    return codes or ["outside_configured_area"]


def create_follow_up_task(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    owner_id: uuid.UUID,
    due_at: datetime,
) -> None:
    """Insert the initial follow-up task for an enquiry (barrier seam)."""
    intake_repo.insert_follow_up_task(
        session,
        enquiry_id=enquiry_id,
        owner_id=owner_id,
        due_at=due_at,
        title=TASK_TITLE,
    )


def submit_intake(
    session: Session,
    payload: dict[str, Any],
    *,
    source: str,
    event_key: uuid.UUID,
    clock: BusinessClock,
    routing_config: dict[str, Any],
) -> None:
    """Persist one intake: receipt, contact, enquiry and follow-up task.

    Everything below commits together in one transaction; any
    exception rolls the whole intake back and re-raises.
    """
    received_at = clock.now_utc()
    with session.begin():
        email = normalize_email(payload["email"])
        phone = normalize_phone(payload.get("phone"))
        decision = evaluate_routing(
            payload.get("postcode"), payload["service_type"], routing_config
        )
        owner_id = intake_repo.get_owner_id(session, decision.owner)
        if owner_id is None:
            # Reference data is seeded outside the intake path; a
            # missing owner is a configuration error, never something
            # to paper over by creating the row here.
            raise ValueError(f"routing owner is not seeded: {decision.owner!r}")

        canonical_payload: dict[str, Any] = {
            "first_name": _nfc(payload["first_name"]),
            "last_name": _nfc(payload.get("last_name")),
            "email": email,
            "phone": phone,
            "postcode": payload.get("postcode"),
            "service_type": payload["service_type"],
            "message": _nfc(payload["message"]),
            "preferred_contact": payload["preferred_contact"],
            "acknowledge_demo": payload["acknowledge_demo"],
        }
        fingerprint_payload = {
            field: canonical_payload[field] for field in _FINGERPRINT_FIELDS
        }
        # Fingerprint only: collapse all whitespace runs to one space.
        fingerprint_payload["message"] = " ".join(
            str(canonical_payload["message"]).split()
        )
        payload_hash = _canonical_hash(
            {field: canonical_payload[field] for field in _PAYLOAD_FIELDS}
        )
        content_fingerprint = _canonical_hash(fingerprint_payload)

        contact_id, contact_version = intake_repo.insert_contact(
            session,
            email_norm=email,
            first_name=canonical_payload["first_name"],
            last_name=canonical_payload["last_name"],
            phone_e164=phone,
        )
        enquiry_id = intake_repo.insert_enquiry(
            session,
            contact_id=contact_id,
            required_contact_version=contact_version,
            source=source,
            service_type=canonical_payload["service_type"],
            postcode=canonical_payload["postcode"],
            message=canonical_payload["message"],
            preferred_contact=canonical_payload["preferred_contact"],
            owner_id=owner_id,
            routing_reason=decision.reason,
            rules_version=routing_config["version"],
            rules_hash=routing_config_hash(routing_config),
            requires_review=decision.review,
            review_reason_codes=_review_reason_codes(
                payload, decision, routing_config
            ),
            received_at=received_at,
        )
        intake_repo.insert_receipt(
            session,
            source=source,
            event_key=event_key,
            payload_hash=payload_hash,
            content_fingerprint=content_fingerprint,
            payload_json=canonical_payload,
            enquiry_id=enquiry_id,
            received_at=received_at,
        )
        intake_repo.insert_initial_job(
            session, enquiry_id=enquiry_id, next_attempt_at=received_at
        )
        intake_repo.insert_received_event(
            session, enquiry_id=enquiry_id, source=source
        )
        create_follow_up_task(
            session,
            enquiry_id,
            owner_id=owner_id,
            due_at=received_at + timedelta(minutes=FOLLOWUP_DUE_MINUTES),
        )
