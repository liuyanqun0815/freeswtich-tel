"""SQLAlchemy ORM models for the smart phone platform."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from vr_common.db import DEFAULT_TENANT


class Base(DeclarativeBase):
    pass


class TenantScoped:
    tenant_id: Mapped[str] = mapped_column(String(32), default=DEFAULT_TENANT, index=True)


class User(Base, TenantScoped):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extension_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("extension.id"), nullable=True
    )


class Extension(Base, TenantScoped):
    __tablename__ = "extension"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ext_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Gateway(Base, TenantScoped):
    __tablename__ = "gateway"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    profile: Mapped[str] = mapped_column(String(64), nullable=False, default="external")
    register: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    password_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    realm: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    proxy: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PhoneNumber(Base, TenantScoped):
    __tablename__ = "phone_number"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Schedule(Base, TenantScoped):
    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    windows_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class Entry(Base, TenantScoped):
    __tablename__ = "entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    schedule_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("schedule.id"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class IvrFlow(Base, TenantScoped):
    __tablename__ = "ivr_flow"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    draft_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class IvrPublish(Base, TenantScoped):
    __tablename__ = "ivr_publish"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flow_id: Mapped[int] = mapped_column(Integer, ForeignKey("ivr_flow.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SkillGroup(Base, TenantScoped):
    __tablename__ = "skill_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    overflow_target: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ring_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    members_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class AgentStateLog(Base, TenantScoped):
    __tablename__ = "agent_state_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DialTask(Base, TenantScoped):
    __tablename__ = "dial_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    window_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    max_success_per_number_per_day: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    gateway_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("gateway.id"), nullable=True
    )
    scene_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ai_scene.id"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DialContact(Base, TenantScoped):
    __tablename__ = "dial_contact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("dial_task.id"), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    block_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Blacklist(Base, TenantScoped):
    __tablename__ = "blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AiScene(Base, TenantScoped):
    __tablename__ = "ai_scene"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    ws_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    fallback: Mapped[str] = mapped_column(String(16), nullable=False, default="transfer")
    opening: Mapped[str] = mapped_column(Text, nullable=False, default="")


class FsNode(Base, TenantScoped):
    __tablename__ = "fs_node"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    esl_host: Mapped[str] = mapped_column(String(255), nullable=False)
    esl_port: Mapped[int] = mapped_column(Integer, nullable=False, default=8021)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ConfigRelease(Base, TenantScoped):
    __tablename__ = "config_release"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base, TenantScoped):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CallRecord(Base, TenantScoped):
    __tablename__ = "call_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vr_call_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    caller: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    callee: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    direction: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    agent_ext: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    billsec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    queue_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    robot_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agent_billsec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hangup_cause: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    record_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    restrict_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class CallLeg(Base, TenantScoped):
    __tablename__ = "call_leg"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vr_call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    leg_uuid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    billsec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class CallEvent(Base, TenantScoped):
    __tablename__ = "call_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vr_call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AiTurn(Base, TenantScoped):
    __tablename__ = "ai_turn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vr_call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
