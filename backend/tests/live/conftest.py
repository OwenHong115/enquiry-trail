"""Live-test gate: opt-in only, loud failure, never a skip.

English-only note for operators: this suite talks to a real provider
endpoint. It is NOT part of the default unit or integration runs and
simulator results are never connected evidence. Connected mode stays
UNVERIFIED in this mock-labelled delivery.

Gate mechanism:
- Plain ``pytest`` uses ``testpaths = ["tests/unit"]`` in pyproject.toml,
  so this directory is not collected by default.
- ``pytest tests/integration`` collects only that directory, so live
  tests never run as ordinary integration tests.
- Running this directory explicitly still requires opt-in: either the
  ``--run-live`` flag or the ``RUN_LIVE=1`` environment switch.
- When explicitly opted in (or explicitly targeted), missing
  prerequisites are a hard failure via ``pytest.fail`` naming every
  missing item. They are never ``pytest.skip``.
"""

from __future__ import annotations

import os

import pytest

# Exact loud-failure prefix. Tests and operators rely on this wording
# to distinguish a missing-prerequisite failure from a provider failure.
LOUD_PREFIX = "LIVE TEST PREREQUISITES MISSING"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run live provider tests (requires CRM_MODE=http, a real "
        "CRM_BASE_URL and CRM_TOKEN, and LIVE_TEST_ACCOUNT_MARKER).",
    )


def _opted_in(config: pytest.Config) -> bool:
    return bool(config.getoption("--run-live")) or os.environ.get("RUN_LIVE") == "1"


def _is_live_item(item: pytest.Item) -> bool:
    return "tests/live" in item.path.as_posix() or "tests\\live" in str(item.path)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    # Plain runs that sweep a wider tree (for example ``pytest tests/``)
    # must not collect live tests as ordinary tests: deselect them
    # unless the operator opted in or explicitly targeted this directory.
    if _opted_in(config):
        return
    explicitly_targeted = any("live" in str(arg) for arg in config.args)
    if explicitly_targeted:
        # Keep the items: the prerequisite fixture below fails loudly.
        return
    live_items = [item for item in items if _is_live_item(item)]
    if live_items:
        items[:] = [item for item in items if item not in live_items]
        config.hook.pytest_deselected(items=live_items)


def missing_prerequisites() -> list[str]:
    """Return every missing live prerequisite, in a stable order."""
    missing: list[str] = []
    crm_mode = os.environ.get("CRM_MODE", "")
    if crm_mode != "http":
        missing.append(f"CRM_MODE must be 'http' (got {crm_mode!r})")
    base_url = os.environ.get("CRM_BASE_URL", "").strip()
    if not base_url:
        missing.append("CRM_BASE_URL is not set")
    elif "provider-sim" in base_url or base_url.rstrip("/").endswith(":8091"):
        missing.append(
            "CRM_BASE_URL must be a real provider URL, not the simulator"
        )
    token = os.environ.get("CRM_TOKEN", "").strip()
    if not token:
        missing.append("CRM_TOKEN is not set")
    elif token == "sim-token":
        missing.append("CRM_TOKEN must be a real provider token, not 'sim-token'")
    if not os.environ.get("LIVE_TEST_ACCOUNT_MARKER", "").strip():
        missing.append("LIVE_TEST_ACCOUNT_MARKER is not set")
    return missing


def require_live_prerequisites() -> None:
    """Fail loudly (never skip) when any prerequisite is missing.

    Called from inside each test body so pytest reports a FAILED test,
    not a setup error or a skip.
    """
    missing = missing_prerequisites()
    if missing:
        pytest.fail(
            f"{LOUD_PREFIX}: " + "; ".join(missing) + ". "
            "Set CRM_MODE=http with a real CRM_BASE_URL and CRM_TOKEN, "
            "plus LIVE_TEST_ACCOUNT_MARKER naming a dedicated test "
            "account, then re-run with --run-live. Connected mode is "
            "UNVERIFIED in this mock-labelled delivery; simulator "
            "results are not connected evidence.",
            pytrace=False,
        )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live: live provider tests (opt-in via --run-live)"
    )


@pytest.fixture()
def live_adapter():  # type: ignore[no-untyped-def]
    """Factory for a real HttpCrmAdapter; call it inside the test body.

    Returning a factory (not the adapter itself) keeps the
    prerequisite check inside the test body, so a missing prerequisite
    is reported as a FAILED test. The adapter is real, never mocked.
    """
    from app.adapters.crm_http import HttpCrmAdapter

    adapters: list[HttpCrmAdapter] = []

    def _make() -> HttpCrmAdapter:
        require_live_prerequisites()
        adapter = HttpCrmAdapter(
            os.environ["CRM_BASE_URL"], os.environ["CRM_TOKEN"]
        )
        adapters.append(adapter)
        return adapter

    try:
        yield _make
    finally:
        for adapter in adapters:
            adapter.close()


@pytest.fixture(scope="session")
def live_marker() -> str:
    """Sanitised dedicated-test-account marker for generated emails."""
    raw = os.environ.get("LIVE_TEST_ACCOUNT_MARKER", "unmarked")
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in raw.lower()).strip("-")
    return cleaned or "unmarked"
