"""Routing evaluation and config hash (S1 stub; behaviour arrives in Green)."""

from __future__ import annotations

import hashlib
import json
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


_TOP_LEVEL_KEYS = frozenset(
    {
        "version",
        "owners",
        "rules",
        "evaluation",
        "unknown_field",
        "operators",
        "missing_review_reasons",
    }
)
_OWNER_KEYS = frozenset({"code", "display_name"})
_RULE_KEYS = frozenset({"id", "when", "owner", "review", "reason"})
_WHEN_KEYS = frozenset({"any", "all", "always"})
_CONDITION_KEYS = frozenset({"field", "op", "value"})
_ROUTING_FIELDS = frozenset({"postcode", "service_type"})


def _reject_unknown_keys(
    mapping: dict[str, Any], allowed: frozenset[str], where: str
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"unknown routing config keys in {where}: {unknown}")


def _reject_unknown_config_keys(config: dict[str, Any]) -> None:
    _reject_unknown_keys(config, _TOP_LEVEL_KEYS, "top level")
    for owner in config.get("owners", []):
        _reject_unknown_keys(owner, _OWNER_KEYS, "owners[]")
    for rule in config.get("rules", []):
        _reject_unknown_keys(rule, _RULE_KEYS, "rules[]")
        when = rule["when"]
        _reject_unknown_keys(when, _WHEN_KEYS, "rules[].when")
        for group in ("any", "all"):
            for condition in when.get(group, []):
                _reject_unknown_keys(condition, _CONDITION_KEYS, "condition")
    _reject_unknown_keys(
        config.get("missing_review_reasons", {}),
        _ROUTING_FIELDS,
        "missing_review_reasons",
    )


def routing_config_hash(config: dict[str, Any]) -> str:
    """Return the lowercase hex SHA256 of the canonical routing bytes.

    Contract: canonical serialization on the entire routing object;
    comments excluded, unknown keys rejected, array order preserved.

    Strictness choice: the contract does not fix the depth of
    "unknown keys rejected", so this rejects unknown keys at every
    fixed-schema level — the top level, each ``owners[]`` and
    ``rules[]`` entry, each ``when`` and condition, and the
    ``missing_review_reasons`` keys (limited to the known routing
    fields). List contents such as postcodes or operator names are
    data values, not keys, and are never rejected.
    """
    _reject_unknown_config_keys(config)
    canonical = json.dumps(
        config, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
