"""Pick which extension to bridge for IVR transfer actions."""

from __future__ import annotations

from collections.abc import Callable


def pick_bridge_extension(
    preferred: str | None,
    members: list[str],
    *,
    registered: Callable[[str], bool],
) -> str | None:
    """Prefer the configured extension; if offline, first registered skill-group member."""
    preferred_ext = (preferred or "").strip()
    if preferred_ext and registered(preferred_ext):
        return preferred_ext
    for raw in members:
        ext = str(raw or "").strip()
        if ext and registered(ext):
            return ext
    return None
