"""Inbound ESL transport: sole production ``connect`` for FreeSWITCH events."""

from __future__ import annotations

import os
import socket
from typing import Any

from vr_engine.main import EngineCtx, handle_event

# Design §4 / §7 event names the engine must see (plain + CUSTOM subclass).
ESL_EVENT_NAMES = (
    "CHANNEL_PARK",
    "CHANNEL_ANSWER",
    "CHANNEL_HANGUP_COMPLETE",
    "BACKGROUND_JOB",
    "HEARTBEAT",
    "PLAYBACK_START",
    "CUSTOM mod_audio_stream::play",
)


def connect(host: str, port: int | str, password: str) -> socket.socket:
    """Inbound ESL: TCP connect, ``auth``, then ``event plain`` for design events."""
    sock = socket.create_connection((host, int(port)))
    _recv_until_blank(sock)
    _send_esl(sock, f"auth {password}")
    _recv_until_blank(sock)
    _send_esl(sock, "event plain " + " ".join(ESL_EVENT_NAMES))
    _recv_until_blank(sock)
    return sock


def _send_esl(sock: socket.socket, line: str) -> None:
    sock.sendall((line + "\n\n").encode("utf-8"))


def _recv_until_blank(sock: socket.socket, bufsize: int = 4096) -> bytes:
    chunks: list[bytes] = []
    data = b""
    while b"\n\n" not in data:
        chunk = sock.recv(bufsize)
        if not chunk:
            break
        chunks.append(chunk)
        data = b"".join(chunks)
    return data


def parse_plain_event(raw: bytes | str) -> dict[str, str]:
    """Parse one ESL plain event body into a header dict."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    headers: dict[str, str] = {}
    for line in text.splitlines():
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip()] = value.strip()
    return headers


def read_loop(ctx: EngineCtx, transport: Any | None = None) -> None:
    """Read ESL events from ``transport`` (or ``ctx.transport``); only ``handle_event`` decides."""
    transport = transport if transport is not None else ctx.transport
    if transport is None:
        raise RuntimeError("ESL transport is not connected")
    while True:
        raw = transport.recv(65536)
        if not raw:
            break
        event = parse_plain_event(raw)
        if not event.get("Event-Name"):
            continue
        cmds = handle_event(event, ctx)
        for cmd in cmds:
            line = f"bgapi {cmd.api} {cmd.args}".strip()
            transport.sendall((line + "\n\n").encode("utf-8"))


def main() -> None:
    host = os.environ.get("FS_HOST", "127.0.0.1")
    port = os.environ.get("FS_ESL_PORT", "8021")
    password = os.environ.get("FS_ESL_PASSWORD", "ClueCon")
    sock = connect(host, port, password)
    ctx = EngineCtx(entries={}, numbers={})
    ctx.transport = sock
    read_loop(ctx)


if __name__ == "__main__":
    main()
