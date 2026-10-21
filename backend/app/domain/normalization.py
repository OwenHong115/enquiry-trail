"""Email and phone normalization (S1 stub; behaviour arrives in Green)."""

from __future__ import annotations


def normalize_email(raw: str) -> str:
    """Return the canonical email for ``raw``.

    Contract: strip + lower; preserve dots/+tag; this is demo identity
    policy.
    """
    raise NotImplementedError


def normalize_phone(raw: str | None) -> str | None:
    """Return the E.164 form of ``raw`` for an AU number, or ``None``.

    Contract: phonenumbers AU default -> E.164; invalid nonempty rejected
    (raises ``ValueError`` at the domain layer).
    """
    raise NotImplementedError
