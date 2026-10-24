"""Shared service-layer exceptions."""

from __future__ import annotations


class VersionConflict(Exception):
    """The row version did not match the caller's expectation."""


class EnquiryNotFound(ValueError):
    """No enquiry exists with the given id.

    Subclasses ValueError so existing service callers that treat a
    missing enquiry as a value error keep working, while the API
    layer can map this exact type to 404 without broad-catching
    ValueError (a configuration failure must stay a 500).
    """


class TaskNotFound(ValueError):
    """No follow-up task exists with the given id (see EnquiryNotFound)."""


class JobNotFound(ValueError):
    """No job exists with the given id (see EnquiryNotFound)."""
