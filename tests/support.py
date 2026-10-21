"""Test helpers for API integration tests."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from vr_common.db import DEFAULT_TENANT
from vr_common.models import Base, User
from vr_common.security import hash_password

_engine = None
SessionLocal: sessionmaker[Session] | None = None


def init_sqlite() -> None:
    global _engine, SessionLocal
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(_engine)
    SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def seed_user(
    username: str,
    password: str,
    role: str,
    must_change_password: bool = False,
    tenant_id: str = DEFAULT_TENANT,
) -> User:
    if SessionLocal is None:
        raise RuntimeError("call init_sqlite() before seed_user()")
    db = SessionLocal()
    try:
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            must_change_password=must_change_password,
            tenant_id=tenant_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def get_test_session() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise RuntimeError("call init_sqlite() before get_test_session()")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
