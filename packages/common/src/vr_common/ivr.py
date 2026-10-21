"""IVR tree validate / publish / step helpers."""

from __future__ import annotations

import copy
from typing import Any


def validate(flow: dict) -> list[str]:
    """Return validation error codes for an IVR draft tree."""
    errors: list[str] = []
    _validate_node(flow, errors)
    return errors


def _validate_node(node: dict, errors: list[str]) -> None:
    children = node.get("children") or []
    seen: set[str] = set()
    for child in children:
        digit = child.get("digit")
        if digit is not None:
            if digit in seen:
                if "duplicate_digit" not in errors:
                    errors.append("duplicate_digit")
            else:
                seen.add(digit)
        _validate_node(child, errors)


def publish(draft: dict, previous_version: int) -> dict:
    """Create an immutable versioned snapshot from a draft."""
    return {
        "version": previous_version + 1,
        "snapshot": copy.deepcopy(draft),
    }


def _find_node(tree: dict, node_id: str) -> dict | None:
    if tree.get("id") == node_id:
        return tree
    for child in tree.get("children") or []:
        found = _find_node(child, node_id)
        if found is not None:
            return found
    return None


def _parent_id_of(tree: dict, node_id: str, parent_id: str | None = None) -> str | None:
    if tree.get("id") == node_id:
        return parent_id
    for child in tree.get("children") or []:
        if child.get("id") == node_id:
            return tree.get("id")
        found = _parent_id_of(child, node_id, tree.get("id"))
        if found is not None:
            return found
    return None


def step(
    snapshot: dict,
    node_id: str,
    digit: str | None,
    *,
    buffer: str = "",
) -> dict[str, Any]:
    """Advance one IVR step against a locked published snapshot."""
    node = _find_node(snapshot, node_id)
    if node is None:
        return {"action": "error", "reason": "unknown_node"}

    if digit == "*":
        return {"action": "replay", "node_id": node_id}

    kind = node.get("kind")
    if kind == "collect":
        terminators = node.get("terminators") or []
        if digit == "#" and "#" in terminators:
            if buffer == "":
                parent = node.get("parent_id") or _parent_id_of(snapshot, node_id)
                return {"action": "parent", "node_id": parent}
            return {"action": "complete", "node_id": node_id, "buffer": buffer}

    if digit is not None:
        for child in node.get("children") or []:
            if child.get("digit") == digit:
                result = dict(child)
                result.setdefault("action", "goto")
                result.setdefault("node_id", child.get("id"))
                return result

    return {"action": "noop", "node_id": node_id}
