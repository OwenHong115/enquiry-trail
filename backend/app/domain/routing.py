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


def _condition_matches(
    condition: dict[str, Any], fields: dict[str, Any]
) -> bool:
    field = condition["field"]
    if field not in fields:
        raise ValueError(f"unknown routing field: {field!r}")
    actual = fields[field]
    operator = condition["op"]
    if operator == "is_null":
        return actual is None
    if operator == "eq":
        return actual == condition["value"]
    if operator == "in":
        return actual in condition["value"]
    raise ValueError(f"unknown routing operator: {operator!r}")


def _rule_matches(when: dict[str, Any], fields: dict[str, Any]) -> bool:
    if "always" in when:
        return bool(when["always"])
    if "any" in when:
        return any(_condition_matches(c, fields) for c in when["any"])
    if "all" in when:
        return all(_condition_matches(c, fields) for c in when["all"])
    raise ValueError(f"unsupported routing condition shape: {sorted(when)!r}")


def evaluate_routing(
    postcode: str | None, service_type: str, config: dict[str, Any]
) -> RoutingDecision:
    """Return the first matching rule decision for the enquiry fields.

    Contract: evaluation is first-match over the ordered rules. A rule
    that cannot be evaluated (unknown operator or unknown field) is a
    configuration error and raises ``ValueError``, never a silent
    non-match.
    """
    fields: dict[str, Any] = {"postcode": postcode, "service_type": service_type}
    for rule in config["rules"]:
        if _rule_matches(rule["when"], fields):
            return RoutingDecision(
                rule_id=rule["id"],
                owner=rule["owner"],
                review=rule["review"],
                reason=rule["reason"],
            )
    raise ValueError("no routing rule matched the enquiry fields")


def routing_config_hash(config: dict[str, Any]) -> str:
    """Return the lowercase hex SHA256 of the canonical routing bytes.

    Contract: canonical serialization on the entire routing object;
    comments excluded, unknown keys rejected, array order preserved.
    """
    raise NotImplementedError
