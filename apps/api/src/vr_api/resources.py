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
from vr_common.esljobs import enqueue_directory_flush
from vr_common.registration import registration_view
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
