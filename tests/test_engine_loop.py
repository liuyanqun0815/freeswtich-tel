from vr_engine.main import handle_event, EngineCtx


def test_park_without_entry_emits_hangup():
    from vr_engine.main import retry_or_kill

    ctx = EngineCtx(entries={}, numbers={"10086": "disabled"})
    cmds = handle_event(
        {
            "Event-Name": "CHANNEL_PARK",
            "Unique-ID": "u1",
            "Caller-Direction": "inbound",
            "Caller-Destination-Number": "10086",
            "variable_vr_call_id": "c1",
        },
        ctx,
    )
    assert cmds[-1].api == "uuid_kill"
    assert retry_or_kill(0) == "retry"
    assert retry_or_kill(1) == "kill"
