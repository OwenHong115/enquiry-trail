"""A09 routing precedence — Red tests for S1.

Source contracts (transcribed by hand; plan package v3.0):
- The rule table, owner codes, evaluation mode ("first-match") and
  the unknown-field policy are transcribed from the routing contract
  (version routing-v1) into ROUTING_CONFIG below. The plan fixture
  file itself is never loaded at test time.
- The expected config hash is transcribed from the canonical vectors
  artifact (routing_sha256). The hash contract states: input is the
  parsed routing data model with comments excluded and unknown keys
  rejected; bytes are the canonical serialization (lexicographic key
  order, comma/colon separators without spaces, ensure_ascii=false,
  no floats, no trailing newline) over the entire routing object
  with array order preserved; digest is SHA256 lowercase hex.
"""

from __future__ import annotations

import copy

import pytest

from app.domain.routing import evaluate_routing, routing_config_hash

# Transcribed from the routing contract (routing-v1). Owner display
# names are included because the hash covers the entire object.
ROUTING_CONFIG: dict = {
    "version": "routing-v1",
    "owners": [
        {"code": "intake", "display_name": "Intake Desk"},
        {"code": "maya", "display_name": "Maya Chen"},
        {"code": "oliver", "display_name": "Oliver Reed"},
    ],
    "rules": [
        {
            "id": "R01",
            "when": {
                "any": [
                    {"field": "postcode", "op": "is_null"},
                    {"field": "service_type", "op": "eq", "value": "unsure"},
                ]
            },
            "owner": "intake",
            "review": True,
            "reason": "missing_routing_information",
        },
        {
            "id": "R02",
            "when": {
                "all": [
                    {
                        "field": "postcode",
                        "op": "in",
                        "value": ["2150", "2151", "2152", "2153", "2154", "2155"],
                    },
                    {
                        "field": "service_type",
                        "op": "in",
                        "value": ["bathroom", "kitchen"],
                    },
                ]
            },
            "owner": "maya",
            "review": False,
            "reason": "west_renovation",
        },
        {
            "id": "R03",
            "when": {
                "all": [
                    {
                        "field": "postcode",
                        "op": "in",
                        "value": ["2000", "2007", "2008", "2009"],
                    },
                    {
                        "field": "service_type",
                        "op": "in",
                        "value": ["bathroom", "kitchen", "general"],
                    },
                ]
            },
            "owner": "oliver",
            "review": False,
            "reason": "inner_city",
        },
        {
            "id": "R04",
            "when": {
                "all": [
                    {
                        "field": "postcode",
                        "op": "in",
                        "value": ["2150", "2151", "2152", "2153", "2154", "2155"],
                    },
                    {"field": "service_type", "op": "eq", "value": "general"},
                ]
            },
            "owner": "intake",
            "review": False,
            "reason": "general_enquiry",
        },
        {
            "id": "R05",
            "when": {"always": True},
            "owner": "intake",
            "review": True,
            "reason": "outside_configured_area",
        },
    ],
    "evaluation": "first-match",
    "unknown_field": "configuration_error",
    "operators": ["is_null", "eq", "in"],
    "missing_review_reasons": {
        "postcode": "missing_postcode",
        "service_type": "unsure_service",
    },
}

# Transcribed from the canonical vectors artifact (routing_sha256):
# SHA256 over the canonical serialization of the object above.
EXPECTED_ROUTING_SHA256 = (
    "ecc3a7055611989d660e51b92abc5dff67b3735cd510d16871c9fc4c0dbe6ca0"
)


@pytest.mark.parametrize(
    ("postcode", "service_type", "rule_id", "owner", "review", "reason"),
    [
        # R01: null postcode needs review whatever the service is.
        (None, "bathroom", "R01", "intake", True, "missing_routing_information"),
        (None, "kitchen", "R01", "intake", True, "missing_routing_information"),
        # R01: unsure service needs review even with a routable
        # postcode; R01 is evaluated before R02/R03.
        ("2150", "unsure", "R01", "intake", True, "missing_routing_information"),
        ("2000", "unsure", "R01", "intake", True, "missing_routing_information"),
        # R02: west postcodes with bathroom/kitchen go to Maya. The
        # 2150 kitchen case also sits in the R04 postcode set, but
        # first-match evaluation decides for R02, not R04/intake.
        ("2150", "kitchen", "R02", "maya", False, "west_renovation"),
        ("2150", "bathroom", "R02", "maya", False, "west_renovation"),
        ("2155", "kitchen", "R02", "maya", False, "west_renovation"),
        # R03: inner-city postcodes go to Oliver, general included.
        ("2000", "bathroom", "R03", "oliver", False, "inner_city"),
        ("2009", "general", "R03", "oliver", False, "inner_city"),
        # R04: west postcode with general service stays with intake
        # and needs no review — this is what R02 must not swallow.
        ("2150", "general", "R04", "intake", False, "general_enquiry"),
        ("2152", "general", "R04", "intake", False, "general_enquiry"),
        # R05 fallback: anything else lands with intake for review.
        ("3000", "kitchen", "R05", "intake", True, "outside_configured_area"),
        # Just outside the west list still falls through to R05.
        ("2156", "bathroom", "R05", "intake", True, "outside_configured_area"),
    ],
)
def test_routing_first_match_table(
    postcode: str | None,
    service_type: str,
    rule_id: str,
    owner: str,
    review: bool,
    reason: str,
) -> None:
    decision = evaluate_routing(postcode, service_type, ROUTING_CONFIG)
    # Contract: assert the public decision — rule, owner code, review
    # flag and reason — for the first matching rule only.
    assert decision.rule_id == rule_id
    assert decision.owner == owner
    assert decision.review is review
    assert decision.reason == reason


def test_routing_config_hash_matches_authority() -> None:
    # Contract: hash agrees with the authority value transcribed
    # above (canonical serialization, SHA256 lowercase hex).
    assert routing_config_hash(ROUTING_CONFIG) == EXPECTED_ROUTING_SHA256


def test_routing_config_hash_ignores_key_insertion_order() -> None:
    # Contract: lexicographic key order — the same object written
    # with keys in a different order hashes identically.
    shuffled = {
        "rules": copy.deepcopy(ROUTING_CONFIG["rules"]),
        "version": ROUTING_CONFIG["version"],
        "missing_review_reasons": dict(
            reversed(list(ROUTING_CONFIG["missing_review_reasons"].items()))
        ),
        "owners": copy.deepcopy(ROUTING_CONFIG["owners"]),
        "operators": list(ROUTING_CONFIG["operators"]),
        "unknown_field": ROUTING_CONFIG["unknown_field"],
        "evaluation": ROUTING_CONFIG["evaluation"],
    }
    assert routing_config_hash(shuffled) == EXPECTED_ROUTING_SHA256


def test_routing_config_hash_preserves_rule_order() -> None:
    # Contract: array order preserved — reordering rules must change
    # the bytes and therefore the digest.
    reordered = copy.deepcopy(ROUTING_CONFIG)
    reordered["rules"] = list(reversed(reordered["rules"]))
    assert routing_config_hash(reordered) != EXPECTED_ROUTING_SHA256


def test_routing_config_hash_rejects_unknown_keys() -> None:
    # Contract: "unknown keys rejected" for the routing hash input.
    broken = copy.deepcopy(ROUTING_CONFIG)
    broken["surprise_key"] = "nope"
    with pytest.raises(ValueError):
        routing_config_hash(broken)
