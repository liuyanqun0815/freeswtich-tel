"""POST /fs/xml — mod_xml_curl backend."""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import Generator
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from vr_common import sipcred, xmlbuild
from vr_common.db import DEFAULT_TENANT, get_database_url
from vr_common.models import ConfigRelease, Entry, Extension, PhoneNumber, User

logger = logging.getLogger("vr.config")

app = FastAPI(title="vr-config")
security = HTTPBasic(auto_error=False)

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


def _require_basic(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    if credentials is None:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    expected_user = os.environ.get("XML_CURL_USER", "fs")
    expected_password = os.environ.get("XML_CURL_PASSWORD", "")
    user_ok = secrets.compare_digest(credentials.username, expected_user)
    pass_ok = secrets.compare_digest(credentials.password, expected_password)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})


def _tenant_from_domain(domain: str) -> str:
    if domain.endswith(".vr.local"):
        return domain.removesuffix(".vr.local")
    return os.environ.get("DEFAULT_TENANT", DEFAULT_TENANT)


def _park_timeout_seconds() -> int:
    return int(os.environ.get("RING_TIMEOUT_SECONDS", "20"))


def _lookup_extension(
    db: Session, tenant_id: str, ext_number: str
) -> Extension | None:
    stmt = (
        select(Extension)
        .where(
            Extension.tenant_id == tenant_id,
            Extension.ext_number == ext_number,
            Extension.enabled.is_(True),
        )
        .limit(1)
    )
    return db.scalars(stmt).first()


def _agent_id_for_extension(db: Session, extension_id: int) -> str:
    stmt = select(User).where(User.extension_id == extension_id).limit(1)
    user = db.scalars(stmt).first()
    return str(user.id) if user else ""


def _destination_number(form: Any) -> str | None:
    for key in ("destination_number", "Caller-Destination-Number"):
        raw = form.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _lookup_entry_for_number(
    db: Session, tenant_id: str, number: str
) -> tuple[PhoneNumber | None, Entry | None]:
    phone_stmt = (
        select(PhoneNumber)
        .where(
            PhoneNumber.tenant_id == tenant_id,
            PhoneNumber.number == number,
            PhoneNumber.enabled.is_(True),
        )
        .limit(1)
    )
    phone = db.scalars(phone_stmt).first()
    if phone is None:
        return None, None
    entry_stmt = (
        select(Entry)
        .where(
            Entry.tenant_id == tenant_id,
            Entry.code == number,
            Entry.enabled.is_(True),
        )
        .limit(1)
    )
    entry = db.scalars(entry_stmt).first()
    return phone, entry


def _vr_entry_for_dialplan(db: Session, tenant_id: str, destination: str | None) -> str:
    if not destination:
        return "did:unknown"
    phone, entry = _lookup_entry_for_number(db, tenant_id, destination)
    if phone is None:
        return "did:unknown"
    if entry is not None:
        return f"{entry.kind}:{entry.target}"
    return f"did:{phone.number}"


def _applied_payloads(db: Session, tenant_id: str) -> list[dict[str, Any]]:
    stmt = (
        select(ConfigRelease)
        .where(
            ConfigRelease.tenant_id == tenant_id,
            ConfigRelease.status == "applied",
        )
        .order_by(ConfigRelease.id.desc())
    )
    payloads: list[dict[str, Any]] = []
    for release in db.scalars(stmt).all():
        try:
            payload = json.loads(release.snapshot_json)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _latest_list(payloads: list[dict[str, Any]], key: str) -> list[Any]:
    for payload in payloads:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _published_gateways(db: Session, tenant_id: str) -> list[dict[str, Any]]:
    gateways = _latest_list(_applied_payloads(db, tenant_id), "gateways")
    return [g for g in gateways if isinstance(g, dict)]


def _published_acl(db: Session, tenant_id: str) -> list[str]:
    nodes = _latest_list(_applied_payloads(db, tenant_id), "acl")
    return [str(node) for node in nodes]


def _xml_response(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


@app.post("/fs/xml")
async def fs_xml(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_require_basic),
) -> Response:
    form = await request.form()
    section = str(form.get("section") or "")
    user = str(form.get("user") or "")
    domain = str(form.get("domain") or "")
    key_value = str(form.get("key_value") or "")
    # hostname reserved for future fs-node routing
    _hostname = str(form.get("hostname") or "")

    tenant_id = os.environ.get("DEFAULT_TENANT", DEFAULT_TENANT)

    if section == "directory":
        tenant_id = _tenant_from_domain(domain)
        extension = _lookup_extension(db, tenant_id, user)
        if extension is None:
            logger.info("directory user=%s domain=%s result=extension-missing", user, domain)
            return _xml_response(xmlbuild.not_found_xml())
        cred = sipcred.get_cred(sipcred.get_store(), domain, user)
        if cred is None:
            logger.info("directory user=%s domain=%s result=no-sip-cred", user, domain)
            return _xml_response(xmlbuild.not_found_xml())
        a1 = str(cred.get("a1") or "")
        if not a1:
            logger.info("directory user=%s domain=%s result=empty-a1", user, domain)
            return _xml_response(xmlbuild.not_found_xml())
        agent_id = str(cred.get("agent_id") or "") or _agent_id_for_extension(db, extension.id)
        logger.info("directory user=%s domain=%s result=ok", user, domain)
        return _xml_response(xmlbuild.directory_xml(domain, user, a1, agent_id))

    if section == "dialplan":
        destination = _destination_number(form)
        entry = _vr_entry_for_dialplan(db, tenant_id, destination)
        body = xmlbuild.park_dialplan(tenant_id, entry, _park_timeout_seconds())
        return _xml_response(body)

    if section == "configuration" and key_value == "sofia.conf":
        gateways = _published_gateways(db, tenant_id)
        return _xml_response(xmlbuild.gateway_xml(gateways))

    if section == "configuration" and key_value == "acl.conf":
        return _xml_response(xmlbuild.acl_xml(_published_acl(db, tenant_id)))

    logger.info(
        "xml_curl section=%s user=%s domain=%s key=%s result=not-found",
        section,
        user,
        domain,
        key_value,
    )
    return _xml_response(xmlbuild.not_found_xml())
