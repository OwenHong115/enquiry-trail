"""Shared routing config transcription for tests.

Transcribed by hand from the routing contract (plan package v3.0,
version routing-v1): rule table, owner codes, first-match evaluation.
The expected hash is transcribed from the canonical vectors artifact
(routing_sha256). The plan package is never loaded at test time.
"""

from __future__ import annotations

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
