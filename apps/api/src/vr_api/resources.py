"""Admin CRUD for tenant-scoped resources (fixed tenant t001)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
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
    PhoneNumber,
    SkillGroup,
    User,
)
from vr_common.security import hash_password

router = APIRouter(tags=["resources"])

_ALLOWED_FALLBACKS = frozenset({"transfer", "hangup"})


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
    members_json: str = "[]"
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
    return {
        "items": [
            _row_dict(r, "ext_number", "display_name", "enabled") for r in rows
        ]
    }


@router.post("/extensions")
def create_extension(
    body: ExtensionIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    row = Extension(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(row, "ext_number", "display_name", "enabled")


# ---- gateways ----


@router.get("/gateways")
def list_gateways(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(Gateway).where(Gateway.tenant_id == DEFAULT_TENANT)).all()
    return {
        "items": [
            _row_dict(r, "name", "profile", "register", "enabled", "published")
            for r in rows
        ]
    }


@router.post("/gateways", status_code=status.HTTP_201_CREATED)
def create_gateway_resource(
    body: GatewayIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump(by_alias=False))
    data["register"] = data.pop("register_gw")
    row = Gateway(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(row, "name", "profile", "register", "enabled", "published")


# ---- numbers ----


@router.get("/numbers")
def list_numbers(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(
        select(PhoneNumber).where(PhoneNumber.tenant_id == DEFAULT_TENANT)
    ).all()
    return {"items": [_row_dict(r, "number", "label", "enabled") for r in rows]}


@router.post("/numbers")
def create_number(
    body: NumberIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    row = PhoneNumber(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(row, "number", "label", "enabled")


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


@router.get("/skill-groups")
def list_skill_groups(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(
        select(SkillGroup).where(SkillGroup.tenant_id == DEFAULT_TENANT)
    ).all()
    return {
        "items": [
            _row_dict(
                r, "name", "overflow_target", "ring_timeout_seconds", "members_json"
            )
            for r in rows
        ]
    }


@router.post("/skill-groups")
def create_skill_group(
    body: SkillGroupIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    row = SkillGroup(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(
        row, "name", "overflow_target", "ring_timeout_seconds", "members_json"
    )


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


@router.get("/users")
def list_users(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(User).where(User.tenant_id == DEFAULT_TENANT)).all()
    return {
        "items": [
            {
                "id": r.id,
                "username": r.username,
                "role": r.role,
                "must_change_password": r.must_change_password,
                "extension_id": r.extension_id,
                "tenant_id": r.tenant_id,
            }
            for r in rows
        ]
    }


@router.post("/users")
def create_user(
    body: UserIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    data = _force_tenant(body.model_dump())
    password = data.pop("password")
    row = User(
        username=data["username"],
        password_hash=hash_password(password),
        role=data["role"],
        must_change_password=data["must_change_password"],
        extension_id=data.get("extension_id"),
        tenant_id=DEFAULT_TENANT,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "username": row.username,
        "role": row.role,
        "must_change_password": row.must_change_password,
        "extension_id": row.extension_id,
        "tenant_id": row.tenant_id,
    }
