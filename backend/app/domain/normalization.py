"""Email and phone normalization."""

from __future__ import annotations

import phonenumbers
from phonenumbers import PhoneNumberFormat


def normalize_email(raw: str) -> str:
    """Return the canonical email for ``raw``.

    Contract: strip + lower; preserve dots/+tag; this is demo identity
    policy.
    """
    return raw.strip().lower()


def normalize_phone(raw: str | None) -> str | None:
    """Return the E.164 form of ``raw`` for an AU number, or ``None``.

    Contract: phonenumbers AU default -> E.164; invalid nonempty rejected
    (raises ``ValueError`` at the domain layer).
    """
    if raw is None:
        return None
    try:
        number = phonenumbers.parse(raw, "AU")
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"invalid AU phone number: {raw!r}") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValueError(f"invalid AU phone number: {raw!r}")
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)
