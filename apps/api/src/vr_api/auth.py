"""Authentication dependencies and routes."""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from vr_common import sipcred
from vr_common.db import get_database_url
from vr_common.models import Extension, User
from vr_common.registration import registration_view
from vr_common.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger("vr.api.auth")

_engine = None
SessionLocal: sessionmaker[Session] | None = None

bearer_scheme = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/auth", tags=["auth"])


def _ensure_session_factory() -> sessionmaker[Session]:
    global _engine, SessionLocal
    if SessionLocal is None:
        _engine = create_engine(get_database_url())
        SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return SessionLocal


def get_db() -> Generator[Session, None, None]:
    factory = _ensure_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    must_change_password: bool
    extension: str | None = None
    sip_password: str | None = None
    domain: str | None = None
    wss_url: str | None = None
    ice_servers: list[dict[str, str]] | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ChangePasswordResponse(BaseModel):
    must_change_password: bool


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str


class MeResponse(BaseModel):
    username: str
    role: str
    extension: str | None = None
    domain: str | None = None
    wss_url: str | None = None
    registered: bool = False
    contact: str = ""


def _load_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    if payload.get("typ") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user = _load_user(db, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_password_changed(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password_change_required",
        )
    return user


def require_admin(user: Annotated[User, Depends(require_password_changed)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


def _sip_fields_for_user(db: Session, user: User) -> dict[str, Any]:
    if user.extension_id is None:
        return {}
    extension = db.get(Extension, user.extension_id)
    if extension is None or not extension.enabled:
        return {}
    domain = sipcred.domain_for_tenant(user.tenant_id)
    password = sipcred.issue_and_store(
        sipcred.get_store(),
        domain=domain,
        ext=extension.ext_number,
        agent_id=str(user.id),
    )
    return {
        "extension": extension.ext_number,
        "sip_password": password,
        "domain": domain,
        "wss_url": sipcred.wss_url(),
        "ice_servers": sipcred.ice_servers(),
    }


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> LoginResponse:
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        logger.info("login rejected username=%s", body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    fields = _sip_fields_for_user(db, user)
    logger.info(
        "login user=%s extension=%s domain=%s wss=%s sip_cred=%s",
        user.username,
        fields.get("extension") or "-",
        fields.get("domain") or "-",
        fields.get("wss_url") or "-",
        "issued" if fields.get("sip_password") else "none",
    )
    return LoginResponse(
        access_token=create_access_token(user.id, user.role, user.tenant_id),
        refresh_token=create_refresh_token(user.id),
        must_change_password=user.must_change_password,
        **fields,
    )


@router.get("/me", response_model=MeResponse)
def me(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_password_changed)],
) -> MeResponse:
    extension_number = None
    registered = False
    contact = ""
    if user.extension_id is not None:
        extension = db.get(Extension, user.extension_id)
        if extension is not None and extension.enabled:
            extension_number = extension.ext_number
            try:
                view = registration_view(extension_number)
            except Exception:
                logger.exception("auth/me registration lookup failed extension=%s", extension_number)
                view = {}
            registered = bool(view.get("registered"))
            contact = str(view.get("contact") or "")
    logger.info(
        "auth/me user=%s extension=%s registered=%s contact=%s",
        user.username,
        extension_number or "-",
        registered,
        contact or "-",
    )
    return MeResponse(
        username=user.username,
        role=user.role,
        extension=extension_number,
        domain=sipcred.domain_for_tenant(user.tenant_id) if extension_number else None,
        wss_url=sipcred.wss_url() if extension_number else None,
        registered=registered,
        contact=contact,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    if user.extension_id is not None:
        extension = db.get(Extension, user.extension_id)
        if extension is not None:
            sipcred.delete_cred(
                sipcred.get_store(),
                sipcred.domain_for_tenant(user.tenant_id),
                extension.ext_number,
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(
    body: ChangePasswordRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ChangePasswordResponse:
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    db.add(user)
    db.commit()
    db.refresh(user)
    return ChangePasswordResponse(must_change_password=user.must_change_password)


@router.post("/refresh", response_model=RefreshResponse)
def refresh(body: RefreshRequest, db: Annotated[Session, Depends(get_db)]) -> RefreshResponse:
    try:
        payload = decode_token(body.refresh_token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    if payload.get("typ") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user = _load_user(db, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return RefreshResponse(
        access_token=create_access_token(user.id, user.role, user.tenant_id),
        refresh_token=create_refresh_token(user.id),
    )
