"""Inbound ESL transport: sole production ``connect`` for FreeSWITCH events."""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any
from urllib.parse import unquote

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vr_common.db import get_database_url
from vr_engine.main import EngineCtx, handle_event, load_engine_ctx, run_maintenance

# Design §4 / §7 event names the engine must see (plain + CUSTOM subclass).
ESL_EVENT_NAMES = (
    "CHANNEL_PARK",
    "CHANNEL_ANSWER",
    "CHANNEL_HANGUP_COMPLETE",
    "BACKGROUND_JOB",
    "HEARTBEAT",
    "PLAYBACK_START",
    "DTMF",
    "CUSTOM sofia::register",
    "CUSTOM sofia::unregister",
    "CUSTOM sofia::expire",
    "CUSTOM mod_audio_stream::play",
)

logger = logging.getLogger("vr.engine.esl")

_SessionLocal: sessionmaker[Session] | None = None
_ESL_TIMEOUT_SECONDS = 10


class EslError(RuntimeError):
    """FreeSWITCH closed or rejected the inbound ESL handshake."""


def _sync_database_url() -> str:
    url = get_database_url()
    if url.startswith("postgresql+asyncpg"):
        return url.replace("postgresql+asyncpg", "postgresql+psycopg2", 1)
    return url


def _session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        engine = create_engine(_sync_database_url(), pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return _SessionLocal


def connect(host: str, port: int | str, password: str) -> socket.socket:
    """Inbound ESL: TCP connect, ``auth``, then ``event plain`` for design events."""
    try:
        sock = socket.create_connection((host, int(port)), timeout=_ESL_TIMEOUT_SECONDS)
    except OSError as exc:
        raise EslError(f"无法连接 FreeSWITCH ESL {host}:{port}：{exc}") from exc
    sock.settimeout(_ESL_TIMEOUT_SECONDS)
    try:
        banner = _recv_until_blank(sock, "等待认证请求")
        _require_auth_request(banner)
        _send_esl(sock, f"auth {password}")
        auth_reply = _recv_until_blank(sock, "等待认证结果")
        _require_ok(auth_reply, "ESL 认证")
        _send_esl(sock, "event plain " + " ".join(ESL_EVENT_NAMES))
        event_reply = _recv_until_blank(sock, "等待事件订阅结果")
        _require_ok(event_reply, "事件订阅")
    except EslError:
        sock.close()
        raise
    except (TimeoutError, socket.timeout) as exc:
        sock.close()
        raise EslError(
            f"等待 FreeSWITCH ESL 响应超时（{host}:{port}）。"
            "端口能连通，但没有完成认证。请确认 event_socket 正在监听，并且 ACL 没有丢弃引擎的连接。"
        ) from exc
    except (BrokenPipeError, ConnectionError, OSError) as exc:
        sock.close()
        raise EslError(
            f"FreeSWITCH 在认证后关闭了连接（{host}:{port}）。"
            "请核对 FS_ESL_PASSWORD 是否与 event_socket.conf.xml 的 password 一致，"
            "并放行引擎容器访问 8021 的地址。"
        ) from exc
    sock.settimeout(None)
    return sock


def _send_esl(sock: socket.socket, line: str) -> None:
    sock.sendall((line + "\n\n").encode("utf-8"))


def _recv_until_blank(sock: socket.socket, step: str, bufsize: int = 4096) -> bytes:
    chunks: list[bytes] = []
    data = b""
    while b"\n\n" not in data:
        chunk = sock.recv(bufsize)
        if not chunk:
            break
        chunks.append(chunk)
        data = b"".join(chunks)
    if b"\n\n" not in data:
        if data:
            raise EslError(f"{step}：FreeSWITCH 关闭了连接，已收到：{_reply_text(data)}")
        raise EslError(
            f"{step}：FreeSWITCH 接受连接后立即关闭。"
            "请检查 event_socket 的 apply-inbound-acl（默认 loopback.auto 只允许本机），"
            "并确认 8021 上是 mod_event_socket。"
        )
    return data


def _reply_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    kept: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        if lowered.startswith("content-type:") or lowered.startswith("reply-text:"):
            kept.append(line.strip())
    return "；".join(kept) if kept else "无响应"


def _require_auth_request(raw: bytes) -> None:
    text = raw.decode("utf-8", errors="replace").lower()
    if "rude-rejection" in text:
        raise EslError(
            "FreeSWITCH 拒绝了引擎地址（text/rude-rejection）。"
            "请修改 event_socket.conf.xml 的 apply-inbound-acl，放行引擎容器所在网段，不要只保留 loopback.auto。"
        )
    if "auth/request" not in text:
        raise EslError(f"未收到 ESL auth/request：{_reply_text(raw)}")


def _require_ok(raw: bytes, step: str) -> None:
    text = raw.decode("utf-8", errors="replace")
    if "rude-rejection" in text.lower():
        _require_auth_request(raw)
    if "+OK" in text:
        return
    detail = _reply_text(raw)
    if "-ERR" in text and "invalid" in text.lower():
        raise EslError(
            f"{step}失败：{detail}。请核对 FS_ESL_PASSWORD 与 event_socket.conf.xml 的 password。"
        )
    raise EslError(f"{step}失败：{detail}")


def parse_plain_event(raw: bytes | str) -> dict[str, str]:
    """Parse one ESL plain event body into a header dict."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    header_text, sep, body = text.partition("\n\n")
    headers: dict[str, str] = {}
    for line in header_text.splitlines():
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip()] = unquote(value.strip())
    if sep and body.strip():
        headers["_body"] = body.strip()
    return headers


def read_loop(ctx: EngineCtx, transport: Any | None = None) -> None:
    """Read ESL events from ``transport`` (or ``ctx.transport``); only ``handle_event`` decides."""
    transport = transport if transport is not None else ctx.transport
    if transport is None:
        raise RuntimeError("ESL transport is not connected")
    ctx.transport = transport
    for cmd in run_maintenance(ctx):
        line = f"bgapi {cmd.api} {cmd.args}".strip()
        transport.sendall((line + "\n\n").encode("utf-8"))
    while True:
        raw = transport.recv(65536)
        if not raw:
            break
        event = parse_plain_event(raw)
        event_name = event.get("Event-Name")
        if not event_name:
            reply = event.get("Reply-Text") or event.get("_body") or ""
            content_type = event.get("Content-Type") or ""
            reply_log = logger.error if "-ERR" in reply else logger.info
            reply_log(
                "esl reply content_type=%s reply=%s",
                content_type or "-",
                reply.replace("\n", " ")[:500] or "-",
            )
            continue
        if event_name == "BACKGROUND_JOB":
            body = (event.get("_body") or "").replace("\n", " ")
            job_log = logger.error if "-ERR" in body else logger.info
            job_log(
                "esl job uuid=%s command=%s result=%s",
                event.get("Job-UUID") or "-",
                event.get("Job-Command") or "-",
                body[:500] or "-",
            )
        cmds = handle_event(event, ctx)
        for cmd in cmds:
            line = f"bgapi {cmd.api} {cmd.args}".strip()
            logger.info("esl send %s", line)
            transport.sendall((line + "\n\n").encode("utf-8"))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("FS_HOST", "127.0.0.1")
    port = os.environ.get("FS_ESL_PORT", "8021")
    password = os.environ.get("FS_ESL_PASSWORD", "ClueCon")
    while True:
        try:
            sock = connect(host, port, password)
            break
        except EslError as exc:
            logger.error("%s", exc)
            time.sleep(5)
    db = _session_factory()()
    ctx = load_engine_ctx(db)
    ctx.transport = sock
    read_loop(ctx)


if __name__ == "__main__":
    main()
