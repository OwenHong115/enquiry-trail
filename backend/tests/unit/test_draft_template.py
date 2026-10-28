"""Unit tests for deterministic server-side template drafts (T4).

The template is a pure function of the enquiry facts: the same
contact name and service type must always produce the same English
subject and body, inside the persisted column limits
(subject varchar(200), body varchar(3000)).
"""

from __future__ import annotations

from app.services.drafts import build_template


def test_template_is_deterministic_and_personalised() -> None:
    first = build_template(first_name="Alex", service_type="bathroom")
    second = build_template(first_name="Alex", service_type="bathroom")

    assert first == second
    subject, body = first
    assert "Alex" in body
    assert "bathroom" in subject.lower() or "bathroom" in body.lower()


def test_template_stays_within_column_limits() -> None:
    subject, body = build_template(first_name="Alex", service_type="kitchen")

    assert 1 <= len(subject) <= 200
    assert 1 <= len(body) <= 3000


def test_template_varies_with_service_type() -> None:
    bathroom = build_template(first_name="Alex", service_type="bathroom")
    kitchen = build_template(first_name="Alex", service_type="kitchen")

    assert bathroom != kitchen
