"""Place a verification call through the engine ESL job queue."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from vr_api.auth import require_password_changed
from vr_common.esljobs import enqueue_command
from vr_common.models import User
from vr_common.registration import is_registered, list_registrations
from vr_common.verify_call import hangup_command, load_call, originate_extension_call, save_call

router = APIRouter(tags=["calls"])


class DialIn(BaseModel):
    caller: str = Field(min_length=2, max_length=32)
    callee: str = Field(min_length=2, max_length=32)


def _queue(cmd: str) -> None:
    try:
        enqueue_command(cmd)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="引擎队列不可用") from exc


@router.get("/registrations")
def registrations(
    _: Annotated[User, Depends(require_password_changed)],
) -> dict:
    try:
        return {"items": list_registrations()}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="注册状态不可用") from exc


@router.post("/calls", status_code=status.HTTP_201_CREATED)
def dial_call(
    body: DialIn,
    _: Annotated[User, Depends(require_password_changed)],
) -> dict:
    if body.caller == body.callee:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="主叫和被叫不能相同")
    try:
        missing = [ext for ext in (body.caller, body.callee) if not is_registered(ext)]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="注册状态不可用") from exc
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"分机未注册: {', '.join(missing)}",
        )
    call_id = str(uuid.uuid4())
    command = originate_extension_call(body.caller, body.callee, call_id)
    row = {
        "call_id": call_id,
        "caller": body.caller,
        "callee": body.callee,
        "state": "queued",
        "uuids": [],
        "detail": "",
        "command": command,
    }
    try:
        save_call(row)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="通话状态不可用") from exc
    _queue(command)
    return row


@router.get("/calls/{call_id}")
def get_call(
    call_id: str,
    _: Annotated[User, Depends(require_password_changed)],
) -> dict:
    try:
        row = load_call(call_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="通话状态不可用") from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通话不存在")
    return row


@router.post("/calls/{call_id}/hangup")
def hangup_call(
    call_id: str,
    _: Annotated[User, Depends(require_password_changed)],
) -> dict:
    try:
        row = load_call(call_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="通话状态不可用") from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通话不存在")
    uuids = [str(item) for item in row.get("uuids") or [] if item]
    if not uuids:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="通道尚未建立")
    for uid in uuids:
        _queue(hangup_command(uid))
    row["detail"] = "hangup_requested"
    try:
        save_call(row)
    except Exception:
        pass
    return row
