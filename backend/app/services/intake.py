"""Intake submission: persist one enquiry as a single atomic unit."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.orm import Session

from app.db import intake_repo, job_repo
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


@dataclass(frozen=True)
class IntakeResult:
    """Public outcome of one intake submission."""

    enquiry_id: uuid.UUID
    receipt_id: uuid.UUID | None = None
    deduplication: str = "new"
    replayed: bool = False
    display_no: int | None = None


class IdempotencyConflict(Exception):
    """The (source, event_key) was already used with a different payload."""


class IntakeUnavailableError(Exception):
    """The intake transaction failed at the database layer.

    The transaction rolled back atomically, so the caller may retry
    with the same Idempotency-Key. Programming and configuration
    errors (for example the routing-owner ValueError) never become
    this error; they propagate unchanged.
    """


def _is_retryable_db_failure(err: DBAPIError) -> bool:
    # Only transient database failures are retryable 503s:
    # OperationalError (database operational/connection failure) and
    # InterfaceError (driver interface/connection failure), plus any
    # DBAPIError that SQLAlchemy flagged connection_invalidated.
    # Permanent errors such as ProgrammingError and non-receipt
    # IntegrityError are not flagged and must propagate as 500.
    return isinstance(err, (OperationalError, InterfaceError)) or bool(
        err.connection_invalidated
    )


class _ReceiptUniqueViolation(Exception):
    """A receipt insert lost a race the advisory locks did not stop.

    Carries the attempted payload hash so the public entry point
    can, after this transaction has rolled back, re-read the
    winning receipt and resolve to a replay or a conflict.
    """

    def __init__(self, payload_hash: str) -> None:
        super().__init__("receipt insert hit a unique violation")
        self.payload_hash = payload_hash

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


def _fold_name(value: Any) -> str:
    """Fold a name for duplicate comparison: NFC, trimmed, case-folded."""
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", value).strip().casefold()


def _content_lock_key(source: str, content_fingerprint: str) -> int:
    """Contract lock key: signed first 64 bits of the SHA256 digest."""
    digest = hashlib.sha256(
        source.encode("utf-8") + b"\x00" + content_fingerprint.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


CONTENT_WINDOW_SECONDS = 600


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


def _submit_intake_once(
    session: Session,
    payload: dict[str, Any],
    *,
    source: str,
    event_key: uuid.UUID,
    clock: BusinessClock,
    routing_config: dict[str, Any],
) -> IntakeResult:
    """Persist one intake: receipt, contact, enquiry and follow-up task.

    Everything below commits together in one transaction; any
    exception rolls the whole intake back and re-raises.
    """
    received_at = clock.now_utc()
    with session.begin():
        email = normalize_email(payload["email"])
        phone = normalize_phone(payload.get("phone"))

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

        # Fixed lock order on every path: content -> event key ->
        # email. Every intake acquires in this order only, so a
        # transaction never waits on a lock class it has already
        # passed and no wait cycle can form. Each lock is held to
        # commit, so a concurrent twin always observes the winner's
        # rows instead of racing them.
        # 1. Serialise identical content before any lookup or write.
        intake_repo.take_content_lock(
            session, _content_lock_key(source, content_fingerprint)
        )
        # 2. Serialise one (source, event_key) before the receipt
        # lookup, so same-key races resolve as replay/conflict.
        intake_repo.take_event_key_lock(session, source, event_key)

        existing = intake_repo.get_receipt_by_key(session, source, event_key)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise IdempotencyConflict(
                    f"event_key {event_key} was used with a different payload"
                )
            # Replay: return the original enquiry, write nothing.
            return IntakeResult(
                enquiry_id=existing.enquiry_id,
                receipt_id=existing.id,
                deduplication=existing.deduplication,
                replayed=True,
                display_no=intake_repo.get_display_no(
                    session, existing.enquiry_id
                ),
            )

        anchor = intake_repo.get_canonical_anchor(
            session, source, content_fingerprint
        )
        if anchor is not None and (received_at - anchor.received_at) < timedelta(
            seconds=CONTENT_WINDOW_SECONDS
        ):
            try:
                # Canonical duplicate: the contact data is identical
                # by fingerprint definition, so this update is a
                # no-op safeguard; it also rearms a lagging sync job.
                intake_repo.take_email_lock(session, email)
                dup_contact_id, _dup_version, _dup_inserted = (
                    intake_repo.insert_contact(
                        session,
                        email_norm=email,
                        first_name=canonical_payload["first_name"],
                        last_name=canonical_payload["last_name"],
                        phone_e164=phone,
                    )
                )
                job_repo.rearm_sync_job_for_contact(
                    session,
                    contact_id=dup_contact_id,
                    next_attempt_at=received_at,
                )
                duplicate_id = intake_repo.insert_duplicate_receipt(
                    session,
                    source=source,
                    event_key=event_key,
                    payload_hash=payload_hash,
                    content_fingerprint=content_fingerprint,
                    payload_json=canonical_payload,
                    enquiry_id=anchor.enquiry_id,
                    received_at=received_at,
                )
            except IntegrityError as err:
                raise _ReceiptUniqueViolation(payload_hash) from err
            intake_repo.insert_content_duplicate_event(
                session,
                enquiry_id=anchor.enquiry_id,
                receipt_id=duplicate_id,
                canonical_enquiry_id=anchor.enquiry_id,
            )
            return IntakeResult(
                enquiry_id=anchor.enquiry_id,
                receipt_id=duplicate_id,
                deduplication="content_duplicate",
                replayed=False,
                display_no=intake_repo.get_display_no(
                    session, anchor.enquiry_id
                ),
            )

        decision = evaluate_routing(
            payload.get("postcode"), payload["service_type"], routing_config
        )
        owner_id = intake_repo.get_owner_id(session, decision.owner)
        if owner_id is None:
            # Reference data is seeded outside the intake path; a
            # missing owner is a configuration error, never something
            # to paper over by creating the row here.
            raise ValueError(f"routing owner is not seeded: {decision.owner!r}")

        # 3. Serialise one email before the contact write. The
        # write itself is a single atomic INSERT ... ON CONFLICT
        # DO NOTHING, so there is no read-then-insert window even
        # for a writer that ignores the advisory lock; a returning
        # customer's row is updated under this lock when new valid
        # phone/name data arrives, advancing its version (A32).
        intake_repo.take_email_lock(session, email)
        contact_id, contact_version, contact_inserted = (
            intake_repo.insert_contact(
                session,
                email_norm=email,
                first_name=canonical_payload["first_name"],
                last_name=canonical_payload["last_name"],
                phone_e164=phone,
            )
        )

        matched_contact_id: uuid.UUID | None = None
        match_reason: str | None = None
        if contact_inserted:
            if phone is not None:
                matched_contact_id = intake_repo.find_contact_by_phone(
                    session, phone, exclude_id=contact_id
                )
                if matched_contact_id is not None:
                    match_reason = "same_phone"
            if matched_contact_id is None:
                matched_contact_id = intake_repo.find_contact_by_name(
                    session,
                    _fold_name(canonical_payload["first_name"]),
                    _fold_name(canonical_payload["last_name"]),
                    exclude_id=contact_id,
                )
                if matched_contact_id is not None:
                    match_reason = "same_name"

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
        try:
            receipt_id = intake_repo.insert_receipt(
                session,
                source=source,
                event_key=event_key,
                payload_hash=payload_hash,
                content_fingerprint=content_fingerprint,
                payload_json=canonical_payload,
                enquiry_id=enquiry_id,
                received_at=received_at,
            )
        except IntegrityError as err:
            raise _ReceiptUniqueViolation(payload_hash) from err
        intake_repo.insert_initial_job(
            session, enquiry_id=enquiry_id, next_attempt_at=received_at
        )
        intake_repo.insert_received_event(
            session, enquiry_id=enquiry_id, source=source
        )
        if matched_contact_id is not None and match_reason is not None:
            intake_repo.insert_possible_duplicate_event(
                session,
                enquiry_id=enquiry_id,
                matched_contact_id=matched_contact_id,
                match_reason=match_reason,
            )
        create_follow_up_task(
            session,
            enquiry_id,
            owner_id=owner_id,
            due_at=received_at + timedelta(minutes=FOLLOWUP_DUE_MINUTES),
        )
        display_no = intake_repo.get_display_no(session, enquiry_id)
    return IntakeResult(
        enquiry_id=enquiry_id,
        receipt_id=receipt_id,
        deduplication="new",
        replayed=False,
        display_no=display_no,
    )


def submit_intake(
    session: Session,
    payload: dict[str, Any],
    *,
    source: str,
    event_key: uuid.UUID,
    clock: BusinessClock,
    routing_config: dict[str, Any],
) -> IntakeResult:
    """Persist one intake, resolving a lost receipt race if needed.

    Defence in depth: the advisory locks should make a receipt
    unique violation unreachable, but if one still escapes the
    single intake transaction (which rolls back atomically), the
    winning receipt is re-read in a fresh transaction. A matching
    payload hash is a replay of the winner's enquiry; anything
    else is the domain conflict the API maps to 409. No
    IntegrityError escapes this entry point from a receipt insert.
    """
    try:
        return _submit_intake_once(
            session,
            payload,
            source=source,
            event_key=event_key,
            clock=clock,
            routing_config=routing_config,
        )
    except DBAPIError as err:
        if not _is_retryable_db_failure(err):
            raise
        raise IntakeUnavailableError(
            "intake database transaction failed"
        ) from err
    except _ReceiptUniqueViolation as violation:
        try:
            with session.begin():
                existing = intake_repo.get_receipt_by_key(
                    session, source, event_key
                )
                if existing is None or (
                    existing.payload_hash != violation.payload_hash
                ):
                    raise IdempotencyConflict(
                        f"event_key {event_key} was used with a different payload"
                    )
                return IntakeResult(
                    enquiry_id=existing.enquiry_id,
                    receipt_id=existing.id,
                    deduplication=existing.deduplication,
                    replayed=True,
                    display_no=intake_repo.get_display_no(
                        session, existing.enquiry_id
                    ),
                )
        except DBAPIError as err:
            if not _is_retryable_db_failure(err):
                raise
            raise IntakeUnavailableError(
                "intake database transaction failed"
            ) from err
