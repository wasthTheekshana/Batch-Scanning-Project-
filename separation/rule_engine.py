"""JSON DSL rule engine (HLD §8).

Rules live inside the project config. The Phase 2 visual builder generates
this same JSON, so the evaluator is a pure function of (rule_set, document).
Conditions support eq/neq/contains/not_contains/is_empty/is_not_empty/
gt/lt/gte/lte/matches_regex plus nested AND/OR/NOT compounds. Actions are
BaseRuleAction plugins from separation.actions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema

from separation.actions import ACTION_REGISTRY

_SCHEMA = json.loads(
    (Path(__file__).parent / "rule_schema.json").read_text(encoding="utf-8"))


class RuleSetError(Exception):
    """Raised when a rule set fails schema validation."""


def _get_field(document: dict, path: str) -> Any:
    """Resolve a dotted field path; missing segments yield None."""
    cur: Any = document
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _check_leaf(document: dict, cond: dict) -> bool:
    value = _get_field(document, cond["field"])
    op = cond["op"]
    expected = cond.get("value")

    if op == "is_empty":
        return value is None or value == "" or value == [] or value == {}
    if op == "is_not_empty":
        return not _check_leaf(document, {**cond, "op": "is_empty"})
    if op == "eq":
        return value == expected
    if op == "neq":
        return value != expected
    if op == "contains":
        return value is not None and str(expected) in str(value)
    if op == "not_contains":
        return value is None or str(expected) not in str(value)
    if op == "matches_regex":
        return value is not None and re.search(str(expected), str(value)) is not None
    if op in ("gt", "lt", "gte", "lte"):
        try:
            v, e = float(value), float(expected)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return {"gt": v > e, "lt": v < e, "gte": v >= e, "lte": v <= e}[op]
    raise RuleSetError(f"unknown operator: {op}")


def _evaluate_condition(document: dict, cond: dict) -> bool:
    if "operator" in cond:
        op = cond["operator"]
        results = (_evaluate_condition(document, c) for c in cond["checks"])
        if op == "AND":
            return all(results)
        if op == "OR":
            return any(results)
        if op == "NOT":
            return not any(results)
        raise RuleSetError(f"unknown compound operator: {op}")
    return _check_leaf(document, cond)


class RuleEngine:
    def __init__(self, rule_set: dict):
        try:
            jsonschema.validate(rule_set, _SCHEMA)
        except jsonschema.ValidationError as e:
            raise RuleSetError(f"invalid rule set: {e.message}") from e
        self.rule_set = rule_set

    def evaluate(self, document: dict) -> list[dict]:
        """Run every rule against the document, applying actions of rules
        whose condition matches. Returns the list of applied actions,
        each annotated with the rule id."""
        applied: list[dict] = []
        for rule in self.rule_set["rules"]:
            if _evaluate_condition(document, rule["condition"]):
                for action in rule["actions"]:
                    ACTION_REGISTRY[action["type"]].apply(
                        document, action.get("value"))
                    applied.append({**action, "rule_id": rule["id"]})
        return applied
