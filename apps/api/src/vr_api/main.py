"""FastAPI application entrypoint."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vr_api.auth import get_db, require_admin, require_password_changed, router as auth_router
from vr_common.cdr import build_cdr_summary, call_record_to_item, parse_instant
from vr_common.db import DEFAULT_TENANT
from vr_common.ivr import publish as ivr_publish, validate as ivr_validate
from vr_common.models import CallRecord, Gateway, IvrFlow, IvrPublish, User

app = FastAPI(title="VR API")
app.include_router(auth_router)


class GatewayCreate(BaseModel):
    name: str


class GatewayOut(BaseModel):
    id: int
    name: str


class IvrPublishOut(BaseModel):
    flow_id: int
    version: int


@app.post("/gateways", response_model=GatewayOut, status_code=status.HTTP_201_CREATED)
def create_gateway(
    body: GatewayCreate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> GatewayOut:
    gateway = Gateway(name=body.name)
    db.add(gateway)
    db.commit()
    db.refresh(gateway)
    return GatewayOut(id=gateway.id, name=gateway.name)


@app.post("/ivr/{flow_id}/publish", response_model=IvrPublishOut)
def publish_ivr(
    flow_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> IvrPublishOut:
    flow = db.get(IvrFlow, flow_id)
    if flow is None or flow.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IVR not found")
    draft = json.loads(flow.draft_json or "{}")
    errors = ivr_validate(draft)
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=errors)

    previous = db.scalar(
        select(func.max(IvrPublish.version)).where(
            IvrPublish.flow_id == flow_id,
            IvrPublish.tenant_id == DEFAULT_TENANT,
        )
    )
    published = ivr_publish(draft, int(previous or 0))
    row = IvrPublish(
        flow_id=flow_id,
        version=published["version"],
        snapshot_json=json.dumps(published["snapshot"], ensure_ascii=False),
        applied_at=datetime.now(timezone.utc),
        tenant_id=DEFAULT_TENANT,
    )
    db.add(row)
    db.commit()
    return IvrPublishOut(flow_id=flow_id, version=published["version"])


@app.get("/cdr")
def list_cdr(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_password_changed)],
    call_id: Annotated[str | None, Query()] = None,
    caller: Annotated[str | None, Query()] = None,
    callee: Annotated[str | None, Query()] = None,
    agent: Annotated[str | None, Query()] = None,
    direction: Annotated[str | None, Query()] = None,
    answered: Annotated[bool | None, Query()] = None,
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
) -> dict:
    stmt = select(CallRecord).where(CallRecord.tenant_id == DEFAULT_TENANT)
    if call_id:
        stmt = stmt.where(CallRecord.vr_call_id == call_id)
    if caller:
        stmt = stmt.where(CallRecord.caller == caller)
    if callee:
        stmt = stmt.where(CallRecord.callee == callee)
    if agent:
        stmt = stmt.where(CallRecord.agent_ext == agent)
    if direction:
        stmt = stmt.where(CallRecord.direction == direction)
    if answered is True:
        stmt = stmt.where(CallRecord.answered_at.is_not(None))
    elif answered is False:
        stmt = stmt.where(CallRecord.answered_at.is_(None))
    start_at = parse_instant(start)
    end_at = parse_instant(end)
    if start_at is not None:
        stmt = stmt.where(CallRecord.started_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(CallRecord.started_at <= end_at)

    records = list(db.scalars(stmt.order_by(CallRecord.started_at.desc())).all())
    return {
        "items": [call_record_to_item(r) for r in records],
        "summary": build_cdr_summary(records),
    }
