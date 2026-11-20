#!/usr/bin/env python3
"""Docker HEALTHCHECK probing the OTBR control socket and REST /node/state API."""

from __future__ import annotations

import http.client
import json
import os
import shlex
import stat
import sys
from pathlib import Path

VALID_NODE_STATES = frozenset({"disabled", "detached", "child", "router", "leader"})


def load_config_env(path: Path) -> dict[str, str]:
    """Parse shlex-quoted KEY=VALUE entries from /run/otbr/config.env."""
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, raw_val = stripped.partition("=")
        if not sep:
            continue
        tokens = shlex.split(raw_val)
        env[key] = tokens[0] if tokens else ""
    return env


def check_health(
    *,
    config_path: Path = Path("/run/otbr/config.env"),
    socket_dir: Path = Path("/run"),
    timeout: float = 3.0,
) -> tuple[bool, str]:
    """Verify the OTBR control socket exists and REST GET /node/state responds 200."""
    try:
        cfg = load_config_env(config_path)
    except OSError as exc:
        return False, f"Failed to read {config_path}: {exc}"

    thread_if = cfg.get("OTBR_THREAD_IF", "wpan0")
    host = cfg.get("OTBR_REST_CHECK_HOST")
    port_raw = cfg.get("OTBR_REST_LISTEN_PORT")
    if not host or not port_raw:
        return False, "Missing OTBR_REST_CHECK_HOST or OTBR_REST_LISTEN_PORT in config.env"

    try:
        port = int(port_raw)
    except ValueError:
        return False, f"Invalid OTBR_REST_LISTEN_PORT: {port_raw!r}"

    sock_path = socket_dir / f"openthread-{thread_if}.sock"
    try:
        mode = sock_path.stat().st_mode
    except OSError as exc:
        return False, f"Control socket missing: {sock_path} ({exc})"
    if not stat.S_ISSOCK(mode):
        return False, f"Control socket path is not a socket: {sock_path}"

    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/node/state")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace").strip()
    except (OSError, http.client.HTTPException) as exc:
        return False, f"REST probe to {host}:{port}/node/state failed: {exc}"
    finally:
        if conn is not None:
            conn.close()

    if resp.status != 200:
        return False, f"REST /node/state returned HTTP {resp.status}: {body!r}"

    try:
        state = json.loads(body)
    except json.JSONDecodeError as exc:
        return False, f"REST /node/state returned invalid JSON ({exc}): {body!r}"

    if not isinstance(state, str) or state not in VALID_NODE_STATES:
        return False, f"REST /node/state returned unexpected state: {state!r}"

    return True, state


def main() -> int:
    config_path = Path(os.environ.get("OTBR_CONFIG_ENV", "/run/otbr/config.env"))
    ok, detail = check_health(config_path=config_path)
    if not ok:
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
