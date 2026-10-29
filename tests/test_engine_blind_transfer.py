import json

from vr_common.commands import FsCommand
from vr_common.esljobs import MemoryJobQueue, enqueue_blind_transfer, enqueue_command, set_job_queue
from vr_engine.main import EngineCtx, drain_flush_commands


class FakeTransport:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _size: int) -> bytes:
        if not hasattr(self, "_done"):
            self._done = True
            header = f"Content-Type: api/response\nContent-Length: {len(self._body)}\n\n".encode()
            return header + self._body
        return b""


def test_drain_flush_commands_resolves_blind_transfer_job(monkeypatch):
    monkeypatch.setenv("DEFAULT_TENANT", "t001")
    queue = MemoryJobQueue()
    set_job_queue(queue)
    enqueue_command("sofia profile internal flush_inbound_reg 1000")
    enqueue_blind_transfer("1001", "1002")

    channels = {
        "rows": [
            {"uuid": "leg-1", "cid_num": "1001", "dest": "4001", "callstate": "ACTIVE"},
        ]
    }
    ctx = EngineCtx(entries={}, numbers={}, transport=FakeTransport(json.dumps(channels)))
    cmds = drain_flush_commands(ctx)
    assert cmds[0] == FsCommand("sofia", "profile internal flush_inbound_reg 1000")
    assert cmds[1].api == "uuid_transfer"
    assert cmds[1].args.startswith("leg-1 bridge:${sofia_contact(1002@")
