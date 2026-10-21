"""Shared display formatting for API responses."""

from __future__ import annotations


def format_display_no(display_no: int) -> str:
    """Format an enquiry display number, e.g. ENQ-000001."""
    return f"ENQ-{display_no:06d}"
