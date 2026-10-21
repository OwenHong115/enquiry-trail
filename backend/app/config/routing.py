"""Production routing config (routing-v1).

This is an app-owned transcription of the routing rules, guarded at
startup by the authority hash below: create_app recomputes
routing_config_hash(ROUTING_CONFIG) and refuses to boot on mismatch,
so a hand edit here fails fast instead of routing enquiries wrongly.
It is not a copy of any plan-package file and the plan package is
never read at runtime.
"""

from __future__ import annotations

EXPECTED_ROUTING_SHA256 = (
    "ecc3a7055611989d660e51b92abc5dff67b3735cd510d16871c9fc4c0dbe6ca0"
)

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
