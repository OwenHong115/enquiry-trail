"""Portfolio D07 follow-up: scenario responses in the OpenAPI schema.

The demo page consumes the scenario list and create responses.
Both must be published (not an empty schema), the list items must
keep omitting the faults field, and the create response keeps the
full run shape including faults. Exported offline, as in C01.
"""

from __future__ import annotations

from app.bootstrap import create_app
from app.settings import Settings


def _exported_schema() -> dict:
    settings = Settings(
        database_url="postgresql+psycopg://offline:offline@127.0.0.1:1/offline",
        session_secret="offline-export-secret",
    )
    return create_app(settings).openapi()


def _response_schema(schema: dict, method: str, status: str) -> dict:
    operation = schema["paths"]["/api/v1/scenarios"][method]
    return operation["responses"][status]["content"]["application/json"]["schema"]


def test_scenario_list_response_is_published_without_faults() -> None:
    schema = _exported_schema()
    assert _response_schema(schema, "get", "200") == {
        "$ref": "#/components/schemas/ScenarioListOut"
    }
    summary = schema["components"]["schemas"]["ScenarioRunSummaryOut"]
    assert "faults" not in summary["properties"]
    assert set(summary["properties"]) == {
        "id",
        "scenario_id",
        "status",
        "fault_triggered",
        "assertion_results",
        "created_at",
        "finished_at",
    }


def test_scenario_create_response_is_the_full_run() -> None:
    schema = _exported_schema()
    assert _response_schema(schema, "post", "201") == {
        "$ref": "#/components/schemas/ScenarioRunOut"
    }
    run = schema["components"]["schemas"]["ScenarioRunOut"]
    assert "faults" in run["properties"]
