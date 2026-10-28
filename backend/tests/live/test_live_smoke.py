"""Live CRM smoke tests (opt-in only; see conftest.py for the gate).

These tests use the real HttpCrmAdapter against the configured real
endpoint. They are never mocked and never run against the simulator as
connected evidence. In this mock-labelled delivery only the
loud-failure path (missing prerequisites) is executed; the happy path
below runs only when an operator supplies a dedicated test account.

Connected mode is UNVERIFIED: passing or failing here is recorded by
the operator outside this suite, never as an acceptance sign-off.
"""

from __future__ import annotations

import uuid

import pytest

from app.ports.crm import (
    CallContext,
    ContactFieldPatch,
    CreateContact,
    FoundContact,
    NotFound,
)

pytestmark = pytest.mark.live


def _context() -> CallContext:
    return CallContext(
        scenario_run_id=None,
        job_id=uuid.uuid4(),
        cycle_no=1,
        attempt_no=1,
        external_operation_id=uuid.uuid4(),
        deadline_seconds=15.0,
    )


def test_live_create_read_patch_and_repeat_create(
    live_adapter, live_marker: str  # type: ignore[no-untyped-def]
) -> None:
    """Create, read back, optionally patch, and repeat the create.

    The repeat create exercises the provider's duplicate handling: the
    adapter reads back by email on a definite conflict and must return
    the same remote id. That returned remote id is the value a CRM sync
    would store on the crm link (``crm_links.remote_id``); asserting it
    matches the read-by-id result verifies the link target without any
    mock.
    """
    adapter = live_adapter()
    email = f"enquirytrail-live-{live_marker}-{uuid.uuid4().hex[:12]}@example.com"
    context = _context()

    created = adapter.create_contact(
        CreateContact(email=email, firstname="Live", lastname="Smoke"),
        context,
    )
    assert isinstance(created, FoundContact)
    assert created.remote_id
    # The remote id that would be stored on the crm link.
    link_remote_id = created.remote_id

    by_email = adapter.get_contact_by_email(email, context)
    assert isinstance(by_email, FoundContact)
    assert by_email.remote_id == link_remote_id

    by_id = adapter.get_contact_by_id(link_remote_id, context)
    assert isinstance(by_id, FoundContact)
    assert by_id.remote_id == link_remote_id
    assert by_id.email == email

    # Optional patch: only when the provider probe says patch is
    # supported. An unsupported patch is reported by the probe result,
    # not hidden by a fallback.
    probe = adapter.probe(context)
    assert probe.ok, "provider probe failed for the configured endpoint"
    if probe.supports_patch:
        patched = adapter.patch_contact(
            link_remote_id,
            ContactFieldPatch(firstname="LiveUpdated"),
            context,
        )
        assert patched.remote_id == link_remote_id
        assert patched.firstname == "LiveUpdated"

    # Repeat enquiry for the same contact: a second create for the same
    # email must resolve to the same remote contact, never a duplicate.
    repeated = adapter.create_contact(
        CreateContact(email=email, firstname="Live", lastname="Smoke"),
        context,
    )
    assert isinstance(repeated, FoundContact)
    assert repeated.remote_id == link_remote_id


def test_live_unknown_contact_is_not_found(live_adapter) -> None:  # type: ignore[no-untyped-def]
    """A lookup for an absent contact returns NotFound, not an error."""
    email = f"enquirytrail-absent-{uuid.uuid4().hex[:12]}@example.com"
    adapter = live_adapter()
    result = adapter.get_contact_by_email(email, _context())
    assert isinstance(result, NotFound)

