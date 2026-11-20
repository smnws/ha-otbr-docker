from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import healthcheck


class _DualStackHTTPServer(HTTPServer):
    address_family = socket.AF_INET6


def _start_rest_server(
    *,
    status: int = 200,
    body: bytes = b'"leader"',
    ipv6: bool = False,
) -> tuple[HTTPServer, str, int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/node/state":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    if ipv6:
        server: HTTPServer = _DualStackHTTPServer(("::1", 0), Handler)
        host = "::1"
    else:
        server = HTTPServer(("127.0.0.1", 0), Handler)
        host = "127.0.0.1"
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port


def _make_unix_socket(sock_dir: Path, thread_if: str = "wpan0") -> socket.socket:
    sock_path = sock_dir / f"openthread-{thread_if}.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock_path))
    s.listen(1)
    return s


def test_healthcheck_passes_for_ipv4_and_valid_states(tmp_path: Path) -> None:
    server, host, port = _start_rest_server(status=200, body=b'"disabled"')
    unix_sock = _make_unix_socket(tmp_path)
    try:
        cfg = tmp_path / "config.env"
        cfg.write_text(
            f"OTBR_THREAD_IF=wpan0\nOTBR_REST_CHECK_HOST={host}\nOTBR_REST_LISTEN_PORT={port}\n",
            encoding="utf-8",
        )
        ok, detail = healthcheck.check_health(config_path=cfg, socket_dir=tmp_path)
        assert ok is True
        assert detail == "disabled"
    finally:
        server.shutdown()
        server.server_close()
        unix_sock.close()


def test_healthcheck_passes_for_ipv6_loopback(tmp_path: Path) -> None:
    try:
        server, host, port = _start_rest_server(status=200, body=b'"router"', ipv6=True)
    except OSError:
        return
    unix_sock = _make_unix_socket(tmp_path)
    try:
        cfg = tmp_path / "config.env"
        cfg.write_text(
            f"OTBR_THREAD_IF=wpan0\nOTBR_REST_CHECK_HOST={host}\nOTBR_REST_LISTEN_PORT={port}\n",
            encoding="utf-8",
        )
        ok, detail = healthcheck.check_health(config_path=cfg, socket_dir=tmp_path)
        assert ok is True
        assert detail == "router"
    finally:
        server.shutdown()
        server.server_close()
        unix_sock.close()


def test_healthcheck_fails_when_socket_missing_or_not_socket(tmp_path: Path) -> None:
    cfg = tmp_path / "config.env"
    cfg.write_text(
        "OTBR_THREAD_IF=wpan0\nOTBR_REST_CHECK_HOST=127.0.0.1\nOTBR_REST_LISTEN_PORT=8081\n",
        encoding="utf-8",
    )
    ok, detail = healthcheck.check_health(config_path=cfg, socket_dir=tmp_path)
    assert ok is False
    assert "Control socket missing" in detail

    (tmp_path / "openthread-wpan0.sock").write_text("not a socket", encoding="utf-8")
    ok2, detail2 = healthcheck.check_health(config_path=cfg, socket_dir=tmp_path)
    assert ok2 is False
    assert "not a socket" in detail2


def test_healthcheck_fails_on_http_error_or_unexpected_state(tmp_path: Path) -> None:
    unix_sock = _make_unix_socket(tmp_path)
    try:
        for status, body, expected_msg in (
            (500, b'"internal error"', "HTTP 500"),
            (200, b"not-json", "invalid JSON"),
            (200, b'"unknown"', "unexpected state"),
        ):
            server, host, port = _start_rest_server(status=status, body=body)
            try:
                cfg = tmp_path / "config.env"
                cfg.write_text(
                    f"OTBR_THREAD_IF=wpan0\nOTBR_REST_CHECK_HOST={host}\nOTBR_REST_LISTEN_PORT={port}\n",
                    encoding="utf-8",
                )
                ok, detail = healthcheck.check_health(config_path=cfg, socket_dir=tmp_path)
                assert ok is False
                assert expected_msg in detail
            finally:
                server.shutdown()
                server.server_close()
    finally:
        unix_sock.close()
