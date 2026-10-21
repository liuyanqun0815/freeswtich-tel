"""Agent business WebSocket service."""

from __future__ import annotations

import json
import os
from collections.abc import Generator

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from jwt.exceptions import PyJWTError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vr_common.agent import AGENT_WS_MESSAGE_TYPES, state_for_agent_message
from vr_common.db import DEFAULT_TENANT, get_database_url
from vr_common.models import AgentStateLog
from vr_common.security import decode_token

app = FastAPI(title="vr-push")

ALLOWED_AGENT_MESSAGE_TYPES = AGENT_WS_MESSAGE_TYPES

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _sync_database_url() -> str:
    url = get_database_url()
    if url.startswith("postgresql+asyncpg"):
        return url.replace("postgresql+asyncpg", "postgresql+psycopg2", 1)
    return url


def _session_factory() -> sessionmaker[Session]:
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = create_engine(_sync_database_url(), pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    session = _session_factory()()
    try:
        yield session
    finally:
        session.close()


def _log_agent_state(db: Session, user_id: int, tenant_id: str, state: str) -> None:
    db.add(
        AgentStateLog(
            user_id=user_id,
            state=state,
            tenant_id=tenant_id,
        )
    )
    db.commit()


def _authenticate(first_message: dict) -> tuple[int, str] | None:
    if first_message.get("type") != "auth":
        return None
    token = first_message.get("token")
    if not isinstance(token, str) or not token:
        return None
    try:
        payload = decode_token(token)
    except PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    tenant_id = str(payload.get("tenant_id") or os.environ.get("DEFAULT_TENANT", DEFAULT_TENANT))
    return int(sub), tenant_id


@app.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket, db: Session = Depends(get_db)) -> None:
    await websocket.accept()
    user_id: int | None = None
    tenant_id = os.environ.get("DEFAULT_TENANT", DEFAULT_TENANT)
    try:
        first_raw = await websocket.receive_text()
        first_message = json.loads(first_raw)
        auth = _authenticate(first_message)
        if auth is None:
            await websocket.close(code=4401)
            return
        user_id, tenant_id = auth

        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)
            msg_type = message.get("type")
            if msg_type not in ALLOWED_AGENT_MESSAGE_TYPES:
                continue
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            new_state = state_for_agent_message(msg_type)
            if new_state is not None:
                _log_agent_state(db, user_id, tenant_id, new_state)
                await websocket.send_json({"type": "state", "state": new_state})
    except WebSocketDisconnect:
        pass
