"""Inbound ESL transport: sole production ``connect`` for FreeSWITCH events."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Any
from urllib.parse import unquote

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vr_common.db import get_database_url
from vr_common.redis_keys import AI_COMMANDS
from vr_engine.main import EngineCtx, handle_event, load_engine_ctx, run_maintenance

# Design §4 / §7 event names the engine must see (plain + CUSTOM subclass).
ESL_EVENT_NAMES = (
    "CHANNEL_PARK",
    "CHANNEL_ANSWER",
    "CHANNEL_CALLSTATE",
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

# ESL 读循环与 AI 指令线程共用：handle_event 改 ctx，写 ESL 也必须串行
_ENGINE_LOCK = threading.Lock()

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


def _header_dict(header_text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_text.splitlines():
        line = line.strip("\r")
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip()] = unquote(value.strip())
    return headers


def parse_plain_event(raw: bytes | str) -> dict[str, str]:
    """Parse one ESL plain event body into a header dict."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    header_text, sep, body = text.partition("\n\n")
    headers = _header_dict(header_text)
    if sep and body.strip():
        headers["_body"] = body.strip()
    return headers


def event_from_frame(outer: dict[str, str], body: bytes) -> dict[str, str]:
    """Turn one ESL frame into the event ``handle_event`` reads.

    ``event plain`` puts ``Event-Name`` in the body, after ``Content-Type`` and
    ``Content-Length``. Reading only the outer headers drops ``CHANNEL_PARK``.
    """
    if outer.get("Content-Type") == "text/event-plain":
        event = parse_plain_event(body)
        event.setdefault("Content-Type", "text/event-plain")
        return event
    if body:
        outer = dict(outer)
        outer["_body"] = body.decode("utf-8", errors="replace").strip()
    return outer


def pop_complete_frames(buf: bytes) -> tuple[list[dict[str, str]], bytes]:
    """Take every complete ESL frame out of ``buf`` and return the remainder."""
    events: list[dict[str, str]] = []
    while True:
        sep = buf.find(b"\n\n")
        if sep < 0:
            break
        outer = _header_dict(buf[:sep].decode("utf-8", errors="replace"))
        length_raw = outer.get("Content-Length")
        if length_raw is None:
            events.append(event_from_frame(outer, b""))
            buf = buf[sep + 2 :]
            continue
        try:
            length = int(length_raw)
        except ValueError:
            length = 0
        start = sep + 2
        if len(buf) < start + length:
            break
        body = buf[start : start + length]
        buf = buf[start + length :]
        events.append(event_from_frame(outer, body))
    return events, buf


def read_loop(ctx: EngineCtx, transport: Any | None = None) -> None:
    """Read ESL events from ``transport`` (or ``ctx.transport``); only ``handle_event`` decides."""
    transport = transport if transport is not None else ctx.transport
    if transport is None:
        raise RuntimeError("ESL transport is not connected")
    ctx.transport = transport
    with _ENGINE_LOCK:
        _send_commands(transport, run_maintenance(ctx))
    pending = b""
    while True:
        raw = transport.recv(65536)
        if not raw:
            break
        pending += raw
        frames, pending = pop_complete_frames(pending)
        for event in frames:
            _dispatch_event(ctx, transport, event)


def _dispatch_event(ctx: EngineCtx, transport: Any, event: dict[str, str]) -> None:
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
        return
    if event_name == "BACKGROUND_JOB":
        body = (event.get("_body") or "").replace("\n", " ")
        job_log = logger.error if "-ERR" in body else logger.info
        job_log(
            "esl job uuid=%s command=%s result=%s",
            event.get("Job-UUID") or "-",
            event.get("Job-Command") or "-",
            body[:500] or "-",
        )
    with _ENGINE_LOCK:
        _send_commands(transport, handle_event(event, ctx))


def _send_commands(transport: Any, cmds: list[Any]) -> None:
    for cmd in cmds:
        line = f"bgapi {cmd.api} {cmd.args}".strip()
        logger.info("esl send %s", line)
        transport.sendall((line + "\n\n").encode("utf-8"))


def ai_command_event(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "Event-Name": "VR_AI_CMD",
        "Unique-ID": str(data.get("uuid") or ""),
        "variable_vr_call_id": str(data.get("call_id") or ""),
        "type": str(data.get("type") or ""),
        "skill_group_id": data.get("skill_group_id"),
    }


def handle_ai_command(ctx: EngineCtx, transport: Any, raw: str) -> None:
    event = ai_command_event(raw)
    if event is None:
        logger.warning("ai command ignored raw=%s", raw[:200])
        return
    logger.info("ai command type=%s uuid=%s", event["type"], event["Unique-ID"] or "-")
    with _ENGINE_LOCK:
        try:
            _send_commands(transport, handle_event(event, ctx))
        except Exception:
            logger.exception("ai command failed raw=%s", raw[:200])
            if ctx.db is not None:
                ctx.db.rollback()


def ai_command_loop(ctx: EngineCtx, transport: Any, client: Any) -> None:
    while True:
        try:
            item = client.brpop(AI_COMMANDS, timeout=5)
        except Exception:
            logger.exception("ai command poll failed")
            time.sleep(1)
            continue
        if item is not None:
            handle_ai_command(ctx, transport, item[1])


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
    client = redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    threading.Thread(target=ai_command_loop, args=(ctx, sock, client), daemon=True, name="ai-commands").start()
    read_loop(ctx)


if __name__ == "__main__":
    main()
