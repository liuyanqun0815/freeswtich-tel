"""Load one AI scene and its provider configs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from vr_ai.session import SceneConfig
from vr_common.db import DEFAULT_TENANT
from vr_common.models import AiScene


@dataclass(frozen=True)
class SceneBundle:
    scene: SceneConfig
    asr: dict[str, Any]
    tts: dict[str, Any]
    llm: dict[str, Any]


def _config(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_scene(db: Session, scene_id: str) -> SceneBundle | None:
    if not scene_id.isdigit():
        return None
    row = db.get(AiScene, int(scene_id))
    if row is None or row.tenant_id != DEFAULT_TENANT:
        return None
    scene = SceneConfig(
        scene_id=scene_id,
        opening=row.opening or "",
        system_prompt=row.system_prompt or "",
        fallback=row.fallback or "transfer",
        transfer_skill_group_id=row.transfer_skill_group_id,
    )
    return SceneBundle(scene=scene, asr=_config(row.asr_json), tts=_config(row.tts_json), llm=_config(row.llm_json))
