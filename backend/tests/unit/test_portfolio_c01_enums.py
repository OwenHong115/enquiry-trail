"""Portfolio C01: published enum sets in the exported OpenAPI schema.

The expected sets are the portfolio contract table in
docs/development-plan.md (Portfolio contract completion, 2026-11-04).
Set equality detects a missing, extra or renamed value. The schema
is exported offline from the FastAPI app with mock settings: no
database connection and no provider calls.
"""

from __future__ import annotations

from app.bootstrap import create_app
from app.settings import Settings

EXPECTED_PROCESSING_SUMMARY = {
    "processing",
    "ready",
    "waiting_retry",
    "blocked",
    "reconciling",
    "failed",
}
EXPECTED_ATTENTION = {
    "none",
    "review_required",
    "retry_scheduled",
    "notification_uncertain",
    "integration_blocked",
    "needs_intervention",
}
EXPECTED_CRM_STATUS = {
    "pending",
    "syncing",
    "synced",
    "waiting_retry",
    "reconciling",
    "blocked",
    "failed",
}


def _exported_schema() -> dict:
    settings = Settings(
        database_url="postgresql+psycopg://offline:offline@127.0.0.1:1/offline",
        session_secret="offline-export-secret",
    )
    return create_app(settings).openapi()


def _resolve(schema: dict, node: dict) -> dict:
    while "$ref" in node:
        prefix = "#/components/schemas/"
        ref = node["$ref"]
        assert ref.startswith(prefix), ref
        node = schema["components"]["schemas"][ref[len(prefix):]]
    return node


def _enum_of(schema: dict, node: dict) -> set:
    resolved = _resolve(schema, node)
    assert "enum" in resolved, resolved
    return set(resolved["enum"])


def test_detail_enums_match_portfolio_table() -> None:
    schema = _exported_schema()
    operation = schema["paths"]["/api/v1/enquiries/{enquiry_id}"]["get"]
    detail_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]
    detail = _resolve(schema, detail_ref)
    assert _enum_of(schema, detail["properties"]["processing_summary"]) == (
        EXPECTED_PROCESSING_SUMMARY
    )
    assert _enum_of(schema, detail["properties"]["attention"]) == EXPECTED_ATTENTION
    crm = _resolve(schema, detail["properties"]["crm"])
    assert _enum_of(schema, crm["properties"]["status"]) == EXPECTED_CRM_STATUS


def test_list_enums_match_detail_enums() -> None:
    schema = _exported_schema()
    item = schema["components"]["schemas"]["EnquiryListItemOut"]
    assert _enum_of(schema, item["properties"]["processing_summary"]) == (
        EXPECTED_PROCESSING_SUMMARY
    )
    assert _enum_of(schema, item["properties"]["attention"]) == EXPECTED_ATTENTION
    assert _enum_of(schema, item["properties"]["crm_status"]) == EXPECTED_CRM_STATUS
