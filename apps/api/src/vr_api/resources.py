"""Admin CRUD for tenant-scoped resources (fixed tenant t001)."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vr_api.auth import get_db, require_admin
from vr_common.db import DEFAULT_TENANT
from vr_common.models import (
    AiScene,
    Blacklist,
    DialTask,
    Entry,
    Extension,
    FsNode,
    Gateway,
    IvrFlow,
    IvrPublish,
    PhoneNumber,
    SkillGroup,
    User,
)
from vr_common.esljobs import enqueue_directory_flush
from vr_common.registration import registration_view
from vr_common.security import hash_password

router = APIRouter(tags=["resources"])

_ALLOWED_FALLBACKS = frozenset({"transfer", "hangup"})
_AUDIO_EXTS = frozenset({".wav", ".mp3", ".gsm"})
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sound_dir() -> Path:
    raw = os.environ.get("VR_SOUND_DIR", "/usr/local/freeswitch/sounds/vr").strip()
    path = Path(raw or "/usr/local/freeswitch/sounds/vr")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _force_tenant(data: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(data)
    cleaned.pop("tenant_id", None)
    cleaned["tenant_id"] = DEFAULT_TENANT
    return cleaned


# ---- request / response models ----


class ExtensionIn(BaseModel):
    ext_number: str
    display_name: Optional[str] = None
    enabled: bool = True
    tenant_id: Optional[str] = None


class GatewayIn(BaseModel):
    name: str
    profile: str = "external"
    register_gw: bool = Field(default=True, alias="register")
    username: Optional[str] = None
    password_encrypted: Optional[str] = None
    realm: Optional[str] = None
    proxy: Optional[str] = None
    enabled: bool = True
    tenant_id: Optional[str] = None

    model_config = {"populate_by_name": True}


class NumberIn(BaseModel):
    number: str
    label: Optional[str] = None
    enabled: bool = True
    entry_kind: Optional[str] = None
    entry_target: Optional[str] = None
    tenant_id: Optional[str] = None


class EntryIn(BaseModel):
    code: str
    kind: str
    target: str
    schedule_id: Optional[int] = None
    enabled: bool = True
    tenant_id: Optional[str] = None


class SkillGroupIn(BaseModel):
    name: str
    overflow_target: Optional[str] = None
    ring_timeout_seconds: int = 20
    members: list[str] = Field(default_factory=list)
    members_json: Optional[str] = None
    tenant_id: Optional[str] = None


class IvrFlowIn(BaseModel):
    name: str
    draft: dict[str, Any] = Field(default_factory=dict)
    tenant_id: Optional[str] = None


class DialTaskIn(BaseModel):
    name: str
    window_json: str = "[]"
    max_success_per_number_per_day: int = 1
    concurrency: int = 1
    gateway_id: Optional[int] = None
    scene_id: Optional[int] = None
    enabled: bool = True
    tenant_id: Optional[str] = None


class BlacklistIn(BaseModel):
    phone: str
    reason: str
    effective_at: Optional[datetime] = None
    tenant_id: Optional[str] = None


class AiSceneIn(BaseModel):
    name: str
    ws_url: Optional[str] = None
    fallback: str = "transfer"
    opening: str = ""
    tenant_id: Optional[str] = None


class FsNodeIn(BaseModel):
    name: str
    esl_host: str
    esl_port: int = 8021
    enabled: bool = True
    tenant_id: Optional[str] = None


class UserIn(BaseModel):
    username: str
    password: str
    role: str = Field(..., pattern="^(admin|agent)$")
    must_change_password: bool = True
    extension_id: Optional[int] = None
    tenant_id: Optional[str] = None


class UserBindIn(BaseModel):
    extension_id: Optional[int] = None


def _row_dict(obj: Any, *fields: str) -> dict[str, Any]:
    data = {f: getattr(obj, f) for f in fields}
    if hasattr(obj, "tenant_id"):
        data["tenant_id"] = obj.tenant_id
    if hasattr(obj, "id"):
        data["id"] = obj.id
    return data


# ---- extensions ----


@router.get("/extensions")
def list_extensions(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(
        select(Extension).where(Extension.tenant_id == DEFAULT_TENANT)
    ).all()
    owners = {
        user.extension_id: user.username
        for user in db.scalars(
            select(User).where(
                User.tenant_id == DEFAULT_TENANT,
                User.extension_id.is_not(None),
            )
        ).all()
    }
    items = []
    for row in rows:
        item = _row_dict(row, "ext_number", "display_name", "enabled")
        try:
            reg = registration_view(row.ext_number)
        except Exception:
            reg = {"registered": False, "contact": ""}
        item["registered"] = reg["registered"]
        item["contact"] = reg["contact"]
        item["bound_username"] = owners.get(row.id) or ""
        items.append(item)
    return {"items": items}


def _request_directory_flush(ext_number: str) -> None:
    try:
        enqueue_directory_flush(ext_number)
    except Exception:
        return


def _extension_taken(db: Session, ext_number: str, exclude_id: int | None = None) -> bool:
    stmt = select(Extension).where(
        Extension.tenant_id == DEFAULT_TENANT,
        Extension.ext_number == ext_number,
    )
    row = db.scalars(stmt).first()
    return row is not None and row.id != exclude_id


@router.post("/extensions", status_code=status.HTTP_201_CREATED)
def create_extension(
    body: ExtensionIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    if _extension_taken(db, body.ext_number):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分机号已存在")
    data = _force_tenant(body.model_dump())
    row = Extension(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    _request_directory_flush(row.ext_number)
    return _row_dict(row, "ext_number", "display_name", "enabled")


@router.put("/extensions/{extension_id}")
def update_extension(
    extension_id: int,
    body: ExtensionIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = db.get(Extension, extension_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分机不存在")
    if _extension_taken(db, body.ext_number, exclude_id=row.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分机号已存在")
    row.ext_number = body.ext_number
    row.display_name = body.display_name
    row.enabled = body.enabled
    db.commit()
    db.refresh(row)
    _request_directory_flush(row.ext_number)
    return _row_dict(row, "ext_number", "display_name", "enabled")


@router.delete("/extensions/{extension_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_extension(
    extension_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> None:
    row = db.get(Extension, extension_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分机不存在")
    bound = db.scalar(select(User).where(User.extension_id == row.id))
    if bound is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分机已绑定用户")
    ext_number = row.ext_number
    db.delete(row)
    db.commit()
    _request_directory_flush(ext_number)


# ---- gateways ----


@router.get("/gateways")
def list_gateways(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(Gateway).where(Gateway.tenant_id == DEFAULT_TENANT)).all()
    return {"items": [_row_dict(row, *_GATEWAY_FIELDS) for row in rows]}


_GATEWAY_FIELDS = (
    "name",
    "profile",
    "register",
    "username",
    "realm",
    "proxy",
    "enabled",
    "published",
)


def _gateway_name_taken(db: Session, name: str, exclude_id: int | None = None) -> bool:
    row = db.scalars(
        select(Gateway).where(Gateway.tenant_id == DEFAULT_TENANT, Gateway.name == name)
    ).first()
    return row is not None and row.id != exclude_id


def _apply_gateway(row: Gateway, body: GatewayIn, *, keep_password: bool) -> None:
    row.name = body.name.strip()
    row.profile = body.profile.strip() or "external"
    row.register = body.register_gw
    row.username = body.username
    row.realm = body.realm
    row.proxy = body.proxy
    row.enabled = body.enabled
    if not keep_password:
        row.password_encrypted = body.password_encrypted
    row.published = False


@router.post("/gateways", status_code=status.HTTP_201_CREATED)
def create_gateway_resource(
    body: GatewayIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    if _gateway_name_taken(db, body.name.strip()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="线路名称已存在")
    row = Gateway(tenant_id=DEFAULT_TENANT, name=body.name.strip())
    _apply_gateway(row, body, keep_password=False)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="线路名称已存在") from None
    db.refresh(row)
    return _row_dict(row, *_GATEWAY_FIELDS)


@router.put("/gateways/{gateway_id}")
def update_gateway_resource(
    gateway_id: int,
    body: GatewayIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = db.get(Gateway, gateway_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="线路不存在")
    if _gateway_name_taken(db, body.name.strip(), exclude_id=row.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="线路名称已存在")
    _apply_gateway(row, body, keep_password=body.password_encrypted in {None, ""})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="线路名称已存在") from None
    db.refresh(row)
    return _row_dict(row, *_GATEWAY_FIELDS)


@router.delete("/gateways/{gateway_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_gateway_resource(
    gateway_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> None:
    row = db.get(Gateway, gateway_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="线路不存在")
    db.delete(row)
    db.commit()


# ---- numbers ----


def _number_item(row: PhoneNumber, entry: Entry | None) -> dict[str, Any]:
    item = _row_dict(row, "number", "label", "enabled")
    item["entry_kind"] = entry.kind if entry is not None else None
    item["entry_target"] = entry.target if entry is not None else None
    return item


def _number_taken(db: Session, number: str, exclude_id: int | None = None) -> bool:
    row = db.scalars(
        select(PhoneNumber).where(
            PhoneNumber.tenant_id == DEFAULT_TENANT,
            PhoneNumber.number == number,
        )
    ).first()
    return row is not None and row.id != exclude_id


def _sync_number_entry(
    db: Session, number: str, previous: str | None, body: NumberIn
) -> Entry | None:
    code = number.strip()
    old_code = previous if previous and previous != code else code
    entry = db.scalars(
        select(Entry).where(Entry.tenant_id == DEFAULT_TENANT, Entry.code == old_code)
    ).first()
    kind = (body.entry_kind or "").strip()
    target = (body.entry_target or "").strip()
    if not kind or not target:
        if entry is not None:
            db.delete(entry)
        return None
    if entry is None:
        entry = Entry(
            tenant_id=DEFAULT_TENANT,
            code=code,
            kind=kind,
            target=target,
            enabled=body.enabled,
        )
        db.add(entry)
    else:
        entry.code = code
        entry.kind = kind
        entry.target = target
        entry.enabled = body.enabled
    return entry


@router.get("/numbers")
def list_numbers(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(
        select(PhoneNumber).where(PhoneNumber.tenant_id == DEFAULT_TENANT)
    ).all()
    entries = {
        entry.code: entry
        for entry in db.scalars(select(Entry).where(Entry.tenant_id == DEFAULT_TENANT)).all()
    }
    return {"items": [_number_item(row, entries.get(row.number)) for row in rows]}


@router.post("/numbers")
def create_number(
    body: NumberIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    number = body.number.strip()
    if _number_taken(db, number):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="号码已存在")
    row = PhoneNumber(
        tenant_id=DEFAULT_TENANT,
        number=number,
        label=body.label,
        enabled=body.enabled,
    )
    db.add(row)
    entry = _sync_number_entry(db, number, None, body)
    db.commit()
    db.refresh(row)
    return _number_item(row, entry)


@router.put("/numbers/{number_id}")
def update_number(
    number_id: int,
    body: NumberIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = db.get(PhoneNumber, number_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="号码不存在")
    number = body.number.strip()
    if _number_taken(db, number, exclude_id=row.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="号码已存在")
    previous = row.number
    row.number = number
    row.label = body.label
    row.enabled = body.enabled
    entry = _sync_number_entry(db, number, previous, body)
    db.commit()
    db.refresh(row)
    return _number_item(row, None if entry is None else entry)


@router.delete("/numbers/{number_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_number(
    number_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> None:
    row = db.get(PhoneNumber, number_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="号码不存在")
    entry = db.scalars(
        select(Entry).where(Entry.tenant_id == DEFAULT_TENANT, Entry.code == row.number)
    ).first()
    if entry is not None:
        db.delete(entry)
    db.delete(row)
    db.commit()


# ---- entries ----


@router.get("/entries")
def list_entries(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(Entry).where(Entry.tenant_id == DEFAULT_TENANT)).all()
    return {
        "items": [
            _row_dict(r, "code", "kind", "target", "schedule_id", "enabled")
            for r in rows
        ]
    }


@router.post("/entries")
def create_entry(
    body: EntryIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    row = Entry(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(row, "code", "kind", "target", "schedule_id", "enabled")


# ---- skill groups ----


def _parse_members(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _members_json_from_body(body: SkillGroupIn) -> str:
    if body.members:
        return json.dumps([m.strip() for m in body.members if m.strip()], ensure_ascii=False)
    if body.members_json:
        return json.dumps(_parse_members(body.members_json), ensure_ascii=False)
    return "[]"


def _skill_group_out(row: SkillGroup) -> dict[str, Any]:
    item = _row_dict(row, "name", "overflow_target", "ring_timeout_seconds", "members_json")
    item["members"] = _parse_members(row.members_json)
    return item


@router.get("/skill-groups")
def list_skill_groups(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(
        select(SkillGroup).where(SkillGroup.tenant_id == DEFAULT_TENANT)
    ).all()
    return {"items": [_skill_group_out(r) for r in rows]}


@router.post("/skill-groups", status_code=status.HTTP_201_CREATED)
def create_skill_group(
    body: SkillGroupIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = SkillGroup(
        name=body.name.strip(),
        overflow_target=body.overflow_target,
        ring_timeout_seconds=body.ring_timeout_seconds,
        members_json=_members_json_from_body(body),
        tenant_id=DEFAULT_TENANT,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _skill_group_out(row)


@router.put("/skill-groups/{skill_group_id}")
def update_skill_group(
    skill_group_id: int,
    body: SkillGroupIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = db.get(SkillGroup, skill_group_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="技能组不存在")
    row.name = body.name.strip()
    row.overflow_target = body.overflow_target
    row.ring_timeout_seconds = body.ring_timeout_seconds
    row.members_json = _members_json_from_body(body)
    db.commit()
    db.refresh(row)
    return _skill_group_out(row)


@router.delete("/skill-groups/{skill_group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill_group(
    skill_group_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> None:
    row = db.get(SkillGroup, skill_group_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="技能组不存在")
    db.delete(row)
    db.commit()


# ---- IVR flows ----


def _ivr_latest_version(db: Session, flow_id: int) -> int:
    return int(
        db.scalar(
            select(func.max(IvrPublish.version)).where(
                IvrPublish.flow_id == flow_id,
                IvrPublish.tenant_id == DEFAULT_TENANT,
            )
        )
        or 0
    )


def _ivr_out(db: Session, row: IvrFlow) -> dict[str, Any]:
    try:
        draft = json.loads(row.draft_json or "{}")
    except json.JSONDecodeError:
        draft = {}
    if not isinstance(draft, dict):
        draft = {}
    return {
        "id": row.id,
        "name": row.name,
        "draft": draft,
        "version": _ivr_latest_version(db, row.id),
        "tenant_id": row.tenant_id,
    }


@router.get("/ivr")
def list_ivr_flows(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(IvrFlow).where(IvrFlow.tenant_id == DEFAULT_TENANT)).all()
    return {"items": [_ivr_out(db, row) for row in rows]}


@router.post("/ivr", status_code=status.HTTP_201_CREATED)
def create_ivr_flow(
    body: IvrFlowIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = IvrFlow(
        name=body.name.strip(),
        draft_json=json.dumps(body.draft or {}, ensure_ascii=False),
        tenant_id=DEFAULT_TENANT,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _ivr_out(db, row)


@router.put("/ivr/{flow_id}")
def update_ivr_flow(
    flow_id: int,
    body: IvrFlowIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = db.get(IvrFlow, flow_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IVR not found")
    row.name = body.name.strip()
    row.draft_json = json.dumps(body.draft or {}, ensure_ascii=False)
    db.commit()
    db.refresh(row)
    return _ivr_out(db, row)


@router.delete("/ivr/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ivr_flow(
    flow_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> None:
    row = db.get(IvrFlow, flow_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IVR not found")
    for pub in db.scalars(select(IvrPublish).where(IvrPublish.flow_id == flow_id)).all():
        db.delete(pub)
    db.delete(row)
    db.commit()


# ---- dial tasks ----


@router.get("/dial-tasks")
def list_dial_tasks(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(
        select(DialTask).where(DialTask.tenant_id == DEFAULT_TENANT)
    ).all()
    return {
        "items": [
            _row_dict(
                r,
                "name",
                "window_json",
                "max_success_per_number_per_day",
                "concurrency",
                "gateway_id",
                "scene_id",
                "enabled",
            )
            for r in rows
        ]
    }


@router.post("/dial-tasks")
def create_dial_task(
    body: DialTaskIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    row = DialTask(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(
        row,
        "name",
        "window_json",
        "max_success_per_number_per_day",
        "concurrency",
        "gateway_id",
        "scene_id",
        "enabled",
    )


# ---- blacklist ----


@router.get("/blacklist")
def list_blacklist(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(
        select(Blacklist).where(Blacklist.tenant_id == DEFAULT_TENANT)
    ).all()
    return {
        "items": [_row_dict(r, "phone", "reason", "effective_at") for r in rows]
    }


@router.post("/blacklist")
def create_blacklist(
    body: BlacklistIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    if data.get("effective_at") is None:
        data.pop("effective_at", None)
    row = Blacklist(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(row, "phone", "reason", "effective_at")


# ---- AI scenes ----


@router.get("/ai-scenes")
def list_ai_scenes(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(AiScene).where(AiScene.tenant_id == DEFAULT_TENANT)).all()
    return {
        "items": [_row_dict(r, "name", "ws_url", "fallback", "opening") for r in rows]
    }


@router.post("/ai-scenes")
def create_ai_scene(
    body: AiSceneIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    if body.fallback not in _ALLOWED_FALLBACKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fallback must be transfer or hangup",
        )
    data = _force_tenant(body.model_dump())
    row = AiScene(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(row, "name", "ws_url", "fallback", "opening")


# ---- FS nodes ----


@router.get("/fs-nodes")
def list_fs_nodes(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(FsNode).where(FsNode.tenant_id == DEFAULT_TENANT)).all()
    return {
        "items": [
            _row_dict(r, "name", "esl_host", "esl_port", "enabled") for r in rows
        ]
    }


@router.post("/fs-nodes")
def create_fs_node(
    body: FsNodeIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    row = FsNode(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(row, "name", "esl_host", "esl_port", "enabled")


# ---- users ----


def _user_view(db: Session, row: User) -> dict[str, Any]:
    ext_number = None
    if row.extension_id is not None:
        extension = db.get(Extension, row.extension_id)
        if extension is not None:
            ext_number = extension.ext_number
    return {
        "id": row.id,
        "username": row.username,
        "role": row.role,
        "must_change_password": row.must_change_password,
        "extension_id": row.extension_id,
        "ext_number": ext_number,
        "tenant_id": row.tenant_id,
    }


def _require_free_extension(
    db: Session, extension_id: int, exclude_user_id: int | None = None
) -> Extension:
    extension = db.get(Extension, extension_id)
    if extension is None or extension.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分机不存在")
    owner = db.scalars(select(User).where(User.extension_id == extension_id)).first()
    if owner is not None and owner.id != exclude_user_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分机已绑定其他用户")
    return extension


@router.get("/users")
def list_users(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(User).where(User.tenant_id == DEFAULT_TENANT)).all()
    return {"items": [_user_view(db, row) for row in rows]}


@router.post("/users")
def create_user(
    body: UserIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    extension_id = data.get("extension_id")
    if extension_id is not None:
        _require_free_extension(db, extension_id)
    password = data.pop("password")
    row = User(
        username=data["username"],
        password_hash=hash_password(password),
        role=data["role"],
        must_change_password=data["must_change_password"],
        extension_id=extension_id,
        tenant_id=DEFAULT_TENANT,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _user_view(db, row)


@router.put("/users/{user_id}")
def bind_user_extension(
    user_id: int,
    body: UserBindIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = db.get(User, user_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if body.extension_id is not None:
        _require_free_extension(db, body.extension_id, exclude_user_id=row.id)
    row.extension_id = body.extension_id
    db.add(row)
    db.commit()
    db.refresh(row)
    return _user_view(db, row)


@router.post("/media/upload")
async def upload_media(
    _: Annotated[User, Depends(require_admin)],
    file: UploadFile = File(...),
) -> dict:
    filename = (file.filename or "").strip()
    suffix = Path(filename).suffix.lower()
    if suffix not in _AUDIO_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 wav / mp3 / gsm 音频",
        )
    stem = _SAFE_NAME.sub("_", Path(filename).stem).strip("._") or "audio"
    out_name = f"{stem}-{uuid.uuid4().hex[:10]}{suffix}"
    dest = _sound_dir() / out_name
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="空文件")
    dest.write_bytes(content)
    return {"path": str(dest.resolve()), "filename": out_name}
