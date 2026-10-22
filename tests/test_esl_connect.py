import socket
import threading

import pytest

from vr_engine.reader import EslError, connect


def _serve(handler) -> int:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def run() -> None:
        conn, _addr = server.accept()
        try:
            handler(conn)
        finally:
            conn.close()
            server.close()

    threading.Thread(target=run, daemon=True).start()
    return port


def test_invalid_password_reports_esl_error() -> None:
    def handler(conn: socket.socket) -> None:
        conn.sendall(b"Content-Type: auth/request\n\n")
        conn.recv(1024)
        conn.sendall(b"Content-Type: command/reply\nReply-Text: -ERR invalid\n\n")

    port = _serve(handler)
    with pytest.raises(EslError, match="FS_ESL_PASSWORD") as caught:
        connect("127.0.0.1", port, "wrong")
    assert "Broken pipe" not in str(caught.value)


def test_rude_rejection_names_the_acl() -> None:
    def handler(conn: socket.socket) -> None:
        conn.sendall(b"Content-Type: text/rude-rejection\n\n")

    port = _serve(handler)
    with pytest.raises(EslError, match="apply-inbound-acl"):
        connect("127.0.0.1", port, "secret")


def test_closed_during_auth_is_not_broken_pipe() -> None:
    def handler(conn: socket.socket) -> None:
        conn.sendall(b"Content-Type: auth/request\n\n")
        conn.recv(1024)

    port = _serve(handler)
    with pytest.raises(EslError, match="等待认证结果"):
        connect("127.0.0.1", port, "secret")


def test_successful_handshake_subscribes_events() -> None:
    seen: dict[str, bytes] = {}

    def handler(conn: socket.socket) -> None:
        conn.sendall(b"Content-Type: auth/request\n\n")
        seen["auth"] = conn.recv(1024)
        conn.sendall(b"Content-Type: command/reply\nReply-Text: +OK accepted\n\n")
        seen["event"] = conn.recv(4096)
        conn.sendall(b"Content-Type: command/reply\nReply-Text: +OK event listener enabled plain\n\n")
        conn.recv(64)

    port = _serve(handler)
    sock = connect("127.0.0.1", port, "secret")
    sock.close()
    assert b"auth secret" in seen["auth"]
    assert b"event plain" in seen["event"]
    assert b"CUSTOM sofia::register" in seen["event"]
