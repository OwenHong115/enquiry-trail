"""A07 normalization — Red tests for S1.

Source contract (transcribed by hand; plan package v3.0):
- Email/phone canonicalization rules and the "invalid nonempty
  rejected" clause live in the behavior contract's canonicalization
  section. The +tag and dot expectations below quote that clause:
  "strip + lower; preserve dots/+tag; this is demo identity policy".
- Phone rule quoted from the same section: "phonenumbers AU default
  -> E.164; invalid nonempty rejected".
- The canonical phone/email pair for the Alex fixture
  (+61412345678 / alex.demo@example.com) is transcribed from the
  canonical vectors artifact.

A07 also states "invalid rejected 422". That 422 is an API-layer
mapping; S1 asserts the domain-level rejection only. The 422 mapping
is proven later at the API slice. A07 is NOT marked passed here.
"""

from __future__ import annotations

import pytest

from app.domain.normalization import normalize_email, normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Whitespace trimmed and case folded; dots in the local part
        # survive (contract: strip + lower; preserve dots/+tag).
        ("  Alex.Demo@Example.COM  ", "alex.demo@example.com"),
        ("ALEX@EXAMPLE.COM", "alex@example.com"),
        ("alex.demo@example.com", "alex.demo@example.com"),
        # Dots are identity here, never stripped (same clause).
        ("a.l.e.x@Example.com", "a.l.e.x@example.com"),
    ],
)
def test_email_normalization_forms(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


def test_email_case_and_whitespace_variants_collapse_to_one_value() -> None:
    # Contract: one canonical value per address under strip + lower.
    assert normalize_email("  Alex.Demo@Example.COM ") == normalize_email(
        "alex.demo@example.com"
    )


def test_email_plus_tag_is_retained() -> None:
    # Contract: "+tag preserved" — the tag must survive lowercasing,
    # not be cut at the plus sign.
    assert (
        normalize_email("Alex.Demo+Kitchen@Example.COM")
        == "alex.demo+kitchen@example.com"
    )
    assert normalize_email("alex+tag@example.com") != normalize_email(
        "alex@example.com"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Already-E.164 AU mobile passes through unchanged; this pair
        # is the canonical vectors value for the Alex fixture.
        ("+61412345678", "+61412345678"),
        # AU trunk forms normalise to the same E.164 value
        # (contract: phonenumbers AU default -> E.164).
        ("0412345678", "+61412345678"),
        ("0412 345 678", "+61412345678"),
        ("+61 412 345 678", "+61412345678"),
        # AU landline with area code 02 -> +61 2 ...
        ("0298765432", "+61298765432"),
        ("(02) 9876 5432", "+61298765432"),
    ],
)
def test_phone_accepted_au_forms_normalize_to_e164(
    raw: str, expected: str
) -> None:
    assert normalize_phone(raw) == expected


def test_phone_input_forms_collapse_to_one_value() -> None:
    # Contract: every accepted AU spelling of one number yields the
    # single E.164 canonical value.
    assert normalize_phone("0412 345 678") == normalize_phone("+61412345678")


def test_phone_absent_is_none() -> None:
    # Contract: absent optional field becomes JSON null; phone is an
    # optional (nullable) intake field.
    assert normalize_phone(None) is None


@pytest.mark.parametrize(
    "raw",
    [
        # Non-numeric text can never be an AU number.
        "not-a-phone",
        # Too short to be a valid AU number.
        "12345",
        # Trunk prefix with too few digits.
        "0412 34",
        # Far beyond any valid AU length.
        "+614123456789012345",
    ],
)
def test_phone_invalid_nonempty_is_rejected(raw: str) -> None:
    # Contract: "invalid nonempty rejected" — asserted here as the
    # domain-level ValueError. The HTTP 422 mapping of this rejection
    # is proven later at the API slice (see module docstring).
    with pytest.raises(ValueError):
        normalize_phone(raw)
