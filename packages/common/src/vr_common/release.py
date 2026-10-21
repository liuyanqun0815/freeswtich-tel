"""Gateway/ACL publish plans, REGED check, and rollback helpers."""

from __future__ import annotations

from typing import Any


def plan_apply(kind: str, name: str) -> list[str]:
    """ESL command plan for gateway/ACL publish (engine executes; API does not)."""
    if kind == "gateway_update":
        return [
            f"sofia profile external killgw {name}",
            "sofia profile external rescan",
        ]
    if kind == "gateway_create":
        return ["sofia profile external rescan"]
    if kind == "acl":
        return ["reloadacl"]
    raise ValueError(f"unsupported release kind: {kind}")


def on_extension_saved(ext: str, cacheable: bool) -> list[str]:
    """Flush directory cache after extension save when cacheable is enabled."""
    if cacheable:
        return [f"xml_flush_cache {ext}"]
    return []


def publish_ok(reged: bool, needs_register: bool) -> bool:
    """REGED required only when the gateway registers outbound."""
    if needs_register:
        return bool(reged)
    return True


def rollback(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most recent applied gateway/ACL snapshot entry."""
    applied = [row for row in history if row.get("status") == "applied"]
    if not applied:
        raise ValueError("no applied snapshot to roll back to")
    return applied[-1]
