"""FastAPI application entrypoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vr_api.auth import get_db, require_admin, router as auth_router
from vr_common.models import Gateway, User

app = FastAPI(title="VR API")
app.include_router(auth_router)


class GatewayCreate(BaseModel):
    name: str


class GatewayOut(BaseModel):
    id: int
    name: str


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
