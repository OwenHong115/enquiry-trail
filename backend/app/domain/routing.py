"""Routing evaluation and config hash (S1 stub; behaviour arrives in Green)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoutingDecision:
    """Outcome of one routing evaluation."""

    rule_id: str
    owner: str
    review: bool
    reason: str


def evaluate_routing(
    postcode: str | None, service_type: str, config: dict[str, Any]
) -> RoutingDecision:
    """Return the first matching rule decision for the enquiry fields.

    Contract: evaluation is first-match over the ordered rules.
    """
    raise NotImplementedError


def routing_config_hash(config: dict[str, Any]) -> str:
    """Return the lowercase hex SHA256 of the canonical routing bytes.

    Contract: canonical serialization on the entire routing object;
    comments excluded, unknown keys rejected, array order preserved.
    """
    raise NotImplementedError
