"""Engine context load + vr_entry park handoff."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vr_common.db import DEFAULT_TENANT
from vr_common.inbound import EntryView
from vr_common.models import Base, Entry, PhoneNumber
from vr_common.commands import FsCommand
from vr_engine.main import EngineCtx, handle_event, load_engine_ctx, park_decision_reason


def _memory_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_load_engine_ctx_maps_phone_status_and_entry_by_did():
    db = _memory_session()
    try:
        db.add(PhoneNumber(tenant_id=DEFAULT_TENANT, number="4008001234", enabled=True))
        db.add(PhoneNumber(tenant_id=DEFAULT_TENANT, number="4008009999", enabled=False))
        db.add(
            Entry(
                tenant_id=DEFAULT_TENANT,
                code="4008001234",
                kind="robot",
                target="scene-1",
                enabled=True,
            )
        )
        db.commit()

        ctx = load_engine_ctx(db)

        assert ctx.numbers["4008001234"] == "enabled"
        assert ctx.numbers["4008009999"] == "disabled"
        assert ctx.entries["4008001234"] == EntryView(kind="robot", target="scene-1")
        assert "4008009999" not in ctx.entries
    finally:
        db.close()


def test_park_with_variable_vr_entry_routes_when_ctx_entries_empty():
    ctx = EngineCtx(entries={}, numbers={})
    cmds = handle_event(
        {
            "Event-Name": "CHANNEL_PARK",
            "Unique-ID": "u1",
            "Caller-Direction": "inbound",
            "Caller-Destination-Number": "10086",
            "variable_vr_call_id": "c1",
            "variable_vr_entry": "robot:scene-1",
        },
        ctx,
    )
    apis = [c.api for c in cmds]
    assert "uuid_kill" not in apis
    assert apis[0] == "uuid_answer"
    assert "uuid_audio_stream" in apis


def test_park_decision_reason_names_unparsed_entry():
    assert (
        park_decision_reason(
            "extension%3A1001",
            None,
            "disabled",
            [FsCommand("uuid_kill", "u1")],
        )
        == "vr-entry-unparsed"
    )
    assert (
        park_decision_reason(
            "extension:1001",
            EntryView(kind="extension", target="1001"),
            "enabled",
            [FsCommand("uuid_transfer", "u1 bridge:user/1001@t001.vr.local inline")],
        )
        == "transfer"
    )
