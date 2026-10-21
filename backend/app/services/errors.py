"""Shared service-layer exceptions."""

from __future__ import annotations


class VersionConflict(Exception):
    """The row version did not match the caller's expectation."""
