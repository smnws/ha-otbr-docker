#!/usr/bin/env bash
set -euo pipefail

image="${1:?Usage: smoke_container.sh <image> [platform]}"
platform="${2:-}"

docker_args=()
if [[ -n "${platform}" ]]; then
    docker_args+=(--platform "${platform}")
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

mkdir -p "${tmp_dir}/vol_data_thread" "${tmp_dir}/vol_data" "${tmp_dir}/vol_var_lib"

run_expect_fail() {
    local out_var="$1"
    shift
    local rc=0
    local output
    output="$(timeout 30 docker run --rm "${docker_args[@]}" "$@" 2>&1)" || rc=$?
    if [[ "${rc}" -eq 0 ]]; then
        echo "Expected container to fail fast, but it exited 0: $*" >&2
        exit 1
    fi
    if [[ "${rc}" -eq 124 ]]; then
        echo "Container timed out after 30s instead of failing fast: $*" >&2
        exit 1
    fi
    printf -v "${out_var}" '%s' "${output}"
}

missing_err=""
run_expect_fail missing_err "${image}"
grep -q "ERROR:" <<< "${missing_err}"

bad_fc_err=""
run_expect_fail bad_fc_err \
    -e DEVICE=/dev/null \
    -e BACKBONE_IF=lo \
    -e FLOW_CONTROL=bogus \
    "${image}"
grep -q "ERROR:" <<< "${bad_fc_err}"
grep -q "Invalid FLOW_CONTROL" <<< "${bad_fc_err}"

docker run --rm "${docker_args[@]}" --entrypoint /bin/bash \
    -e DEVICE=/dev/null \
    -e BACKBONE_IF=lo \
    -e BETA=0 \
    "${image}" -c '
        set -euo pipefail
        /usr/local/lib/otbr/stage2_init.py
        test "$(readlink /usr/sbin/otbr-agent)" = "/opt/otbr-stable/sbin/otbr-agent"
        grep -Fxq "channel=stable" /opt/otbr-stable/BUILD_INFO
        grep -Eq "^otbr_commit=[0-9a-f]{40}$" /opt/otbr-stable/BUILD_INFO
        grep -Eq "^patch=openthread/0001-posix-hdlc-software-flow-control(@[a-z0-9-]+)?\.patch sha256=[0-9a-f]{64}$" /opt/otbr-stable/BUILD_INFO
        if command -v pip3 >/dev/null 2>&1 || command -v pip >/dev/null 2>&1; then
            echo "Expected python3-pip to be purged from runtime image" >&2
            exit 1
        fi
        python3 -c '\''import sys
for p in ("/opt/otbr-stable/sbin/otbr-agent", "/opt/otbr-beta/sbin/otbr-agent"):
    if b".symtab" in open(p, "rb").read():
        sys.exit(f"Expected stripped binary, found .symtab in {p}")
'\''
        /usr/sbin/otbr-agent --version
        help_out="$(/usr/sbin/otbr-agent --help 2>&1)"
        grep -q -- "--rest-listen-port" <<< "${help_out}"
        grep -Fq "uart-flow-control[=hardware|software|none]" <<< "${help_out}"

        for bin in /usr/sbin/otbr-agent /usr/sbin/otbr-web /usr/sbin/ot-ctl; do
            if ldd "${bin}" | grep -q "not found"; then
                echo "Missing shared library for ${bin}:" >&2
                ldd "${bin}" >&2
                exit 1
            fi
        done

        for script in \
            /usr/local/lib/otbr/stage2_init.py \
            /usr/local/lib/otbr/healthcheck.py \
            /etc/s6-overlay/scripts/*.sh \
            /etc/s6-overlay/s6-rc.d/*/run \
            /etc/s6-overlay/s6-rc.d/*/finish \
            /etc/s6-overlay/s6-rc.d/*/data/check; do
            test -x "${script}"
        done

        migrate_help="$(python3 /usr/local/bin/migrate_otbr_settings.py --help 2>&1)"
        for flag in --adapter --baudrate --flow-control --data-dir; do
            grep -q -- "${flag}" <<< "${migrate_help}"
        done
    '

docker run --rm "${docker_args[@]}" --entrypoint /bin/bash \
    -e DEVICE=/dev/null \
    -e BACKBONE_IF=lo \
    -e BETA=1 \
    "${image}" -c '
        set -euo pipefail
        /usr/local/lib/otbr/stage2_init.py
        test "$(readlink /usr/sbin/otbr-agent)" = "/opt/otbr-beta/sbin/otbr-agent"
        grep -Fxq "channel=beta" /opt/otbr-beta/BUILD_INFO
        grep -Eq "^patch=openthread/0001-posix-hdlc-software-flow-control(@[a-z0-9-]+)?\.patch sha256=[0-9a-f]{64}$" /opt/otbr-beta/BUILD_INFO
        /usr/sbin/otbr-agent --version
        help_out="$(/usr/sbin/otbr-agent --help 2>&1)"
        grep -q -- "--rest-listen-port" <<< "${help_out}"
        grep -Fq "uart-flow-control[=hardware|software|none]" <<< "${help_out}"
        for bin in /usr/sbin/otbr-agent /usr/sbin/otbr-web /usr/sbin/ot-ctl; do
            if ldd "${bin}" | grep -q "not found"; then
                echo "Missing shared library for ${bin}:" >&2
                ldd "${bin}" >&2
                exit 1
            fi
        done
    '

docker run --rm "${docker_args[@]}" --entrypoint /bin/bash \
    "${image}" -c '
        set -euo pipefail
        env -i PATH="$PATH" BACKBONE_IF=lo NETWORK_DEVICE="127.0.0.1:6638" \
            WEB_LISTEN_ADDRESS=127.0.0.1 WEB_PORT=8080 \
            /usr/local/lib/otbr/stage2_init.py
        test -f /etc/s6-overlay/user-bundles.d/user/contents.d/otbr-web
        test -f /etc/s6-overlay/user-bundles.d/user/contents.d/socat-otbr-tcp
        test -f /etc/s6-overlay/s6-rc.d/otbr-agent/dependencies.d/socat-otbr-tcp
        grep -Fxq "OTBR_WEB_LISTEN_ADDRESS=127.0.0.1" /run/otbr/config.env
        grep -Fxq "OTBR_WEB_PORT=8080" /run/otbr/config.env
        grep -Fxq "OTBR_DEVICE=/tmp/ttyOTBR" /run/otbr/config.env
        grep -Fxq "OTBR_FLOW_CONTROL=none" /run/otbr/config.env

        env -i PATH="$PATH" BACKBONE_IF=lo DEVICE=/dev/null \
            /usr/local/lib/otbr/stage2_init.py
        test ! -e /etc/s6-overlay/user-bundles.d/user/contents.d/otbr-web
        test ! -e /etc/s6-overlay/user-bundles.d/user/contents.d/socat-otbr-tcp
        test ! -e /etc/s6-overlay/s6-rc.d/otbr-agent/dependencies.d/socat-otbr-tcp
        grep -Fxq "OTBR_FLOW_CONTROL=software" /run/otbr/config.env
        grep -Fxq "OTBR_UART_FLOW_PARAM='\''&uart-flow-control=software&uart-init-deassert'\''" /run/otbr/config.env
        grep -Fxq "OTBR_TX_POWER=6" /run/otbr/config.env

        env -i PATH="$PATH" BACKBONE_IF=lo DEVICE=/dev/null TX_POWER=7 \
            /usr/local/lib/otbr/stage2_init.py
        grep -Fxq "OTBR_TX_POWER=7" /run/otbr/config.env

        python3 - <<'\''HC_PY'\''
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

state_status = [200]

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"\"leader\""
        self.send_response(state_status[0])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format, *args):
        return

srv = HTTPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()

sock_path = Path("/run/openthread-wpan0.sock")
sock_path.unlink(missing_ok=True)
us = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
us.bind(str(sock_path))
us.listen(1)

Path("/run/otbr/config.env").write_text(
    f"OTBR_THREAD_IF=wpan0\nOTBR_REST_CHECK_HOST=127.0.0.1\nOTBR_REST_LISTEN_PORT={port}\n",
    encoding="utf-8",
)
try:
    ok_res = subprocess.run(["/usr/local/lib/otbr/healthcheck.py"], capture_output=True, text=True)
    assert ok_res.returncode == 0, f"healthcheck expected 0, got {ok_res.returncode}: {ok_res.stderr}"
    state_status[0] = 500
    bad_res = subprocess.run(["/usr/local/lib/otbr/healthcheck.py"], capture_output=True, text=True)
    assert bad_res.returncode == 1, f"healthcheck expected 1 on HTTP 500, got {bad_res.returncode}"
finally:
    srv.shutdown()
    srv.server_close()
    us.close()
    sock_path.unlink(missing_ok=True)
HC_PY

        net_sw_err="$(
            env -i PATH="$PATH" BACKBONE_IF=lo NETWORK_DEVICE="127.0.0.1:6638" FLOW_CONTROL=software \
                /usr/local/lib/otbr/stage2_init.py 2>&1 >/dev/null
        )"
        grep -q "WARNING:.*FLOW_CONTROL=software with NETWORK_DEVICE" <<< "${net_sw_err}"
        grep -Fxq "OTBR_FLOW_CONTROL=software" /run/otbr/config.env
        grep -Fxq "OTBR_UART_FLOW_PARAM='\''&uart-flow-control=software&uart-init-deassert'\''" /run/otbr/config.env
    '

docker run --rm -i "${docker_args[@]}" --entrypoint python3 "${image}" - <<'PY'
import os
import pty
import select
import subprocess
import termios

AGENTS = (
    "/opt/otbr-stable/sbin/otbr-agent",
    "/opt/otbr-beta/sbin/otbr-agent",
)

POSITIVE_CASES = (
    ("&uart-flow-control=software&uart-init-deassert", True, True, False, True),
    ("&uart-flow-control", False, False, True, False),
    ("&uart-flow-control=hardware", False, False, True, False),
    ("&uart-init-deassert", False, False, False, False),
    ("&uart-flow-control=none&uart-init-deassert", False, False, False, False),
)

NEGATIVE_CASES = (
    "&uart-flow-control=bogus",
    "&uart-flow-control=hardware&uart-flow-control=software",
    "&uart-flow-control&uart-flow-control=software",
)


def dirty_slave(slave: int, expect_ixon: bool, expect_ixoff: bool, expect_crtscts: bool) -> None:
    attrs = termios.tcgetattr(slave)
    iflag = attrs[0] | termios.IXANY
    iflag = (iflag & ~termios.IXON) if expect_ixon else (iflag | termios.IXON)
    iflag = (iflag & ~termios.IXOFF) if expect_ixoff else (iflag | termios.IXOFF)
    attrs[0] = iflag
    attrs[2] = (attrs[2] & ~termios.CRTSCTS) if expect_crtscts else (attrs[2] | termios.CRTSCTS)
    attrs[4] = termios.B9600
    attrs[5] = termios.B9600
    attrs[6][termios.VSTART] = b"\x01"
    attrs[6][termios.VSTOP] = b"\x02"
    termios.tcsetattr(slave, termios.TCSANOW, attrs)


for agent in AGENTS:
    for param, expect_ixon, expect_ixoff, expect_crtscts, check_xonxoff_cc in POSITIVE_CASES:
        master, slave = pty.openpty()
        proc = None
        try:
            dirty_slave(slave, expect_ixon, expect_ixoff, expect_crtscts)
            slave_path = os.ttyname(slave)
            url = f"spinel+hdlc+uart://{slave_path}?uart-baudrate=460800{param}"
            proc = subprocess.Popen(
                [agent, "-I", "wpan0", "-B", "lo", "-d7", "-v", url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            ready, _, _ = select.select([master], [], [], 5.0)
            if not ready:
                rc = proc.poll()
                proc.kill()
                out, err = proc.communicate()
                raise AssertionError(
                    f"{agent} ({param}) produced no Spinel TX within 5s (rc={rc}): "
                    f"stderr={err.decode(errors='replace')!r} stdout={out.decode(errors='replace')!r}"
                )
            tx = os.read(master, 4096)
            assert len(tx) > 0, f"{agent} ({param}) wrote empty Spinel TX"
            attrs = termios.tcgetattr(slave)
            actual = {
                "IXON": bool(attrs[0] & termios.IXON),
                "IXOFF": bool(attrs[0] & termios.IXOFF),
                "IXANY": bool(attrs[0] & termios.IXANY),
                "CRTSCTS": bool(attrs[2] & termios.CRTSCTS),
                "ispeed": attrs[4],
                "ospeed": attrs[5],
                "VSTART": attrs[6][termios.VSTART],
                "VSTOP": attrs[6][termios.VSTOP],
            }
            assert actual["IXON"] is expect_ixon, f"{agent} ({param}) IXON mismatch: {actual}"
            assert actual["IXOFF"] is expect_ixoff, f"{agent} ({param}) IXOFF mismatch: {actual}"
            assert actual["IXANY"] is False, f"{agent} ({param}) IXANY remained set: {actual}"
            assert actual["CRTSCTS"] is expect_crtscts, f"{agent} ({param}) CRTSCTS mismatch: {actual}"
            assert actual["ispeed"] == termios.B460800, f"{agent} ({param}) ispeed mismatch: {actual}"
            assert actual["ospeed"] == termios.B460800, f"{agent} ({param}) ospeed mismatch: {actual}"
            if check_xonxoff_cc:
                assert actual["VSTART"] == b"\x11", f"{agent} ({param}) VSTART mismatch: {actual}"
                assert actual["VSTOP"] == b"\x13", f"{agent} ({param}) VSTOP mismatch: {actual}"
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            os.close(master)
            os.close(slave)

    for param in NEGATIVE_CASES:
        master, slave = pty.openpty()
        proc = None
        try:
            slave_path = os.ttyname(slave)
            url = f"spinel+hdlc+uart://{slave_path}?uart-baudrate=460800{param}"
            proc = subprocess.Popen(
                [agent, "-I", "wpan0", "-B", "lo", "-d7", "-v", url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            rc = proc.wait(timeout=5.0)
            assert rc == 2, f"{agent} ({param}) expected exit code 2, got {rc}"
            ready, _, _ = select.select([master], [], [], 0.0)
            assert not ready, f"{agent} ({param}) unexpectedly wrote to PTY on invalid args"
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)
            os.close(slave)

print("Real-PTY termios smoke checks PASSED for stable and beta.")
PY

docker run --rm "${docker_args[@]}" --entrypoint /bin/bash \
    -v "${tmp_dir}/vol_data_thread:/data/thread" \
    -e DEVICE=/dev/null -e BACKBONE_IF=lo \
    "${image}" -c '
        set -euo pipefail
        /usr/local/lib/otbr/stage2_init.py
        /usr/local/lib/otbr/stage2_init.py
        test -L /var/lib/thread
        test "$(readlink /var/lib/thread)" = "/data/thread"
        test ! -e /data/thread/thread
        grep -Fxq "OTBR_DATA_DIR=/data/thread" /run/otbr/config.env
    '

docker run --rm "${docker_args[@]}" --entrypoint /bin/bash \
    -v "${tmp_dir}/vol_data:/data" \
    -e DEVICE=/dev/null -e BACKBONE_IF=lo \
    "${image}" -c '
        set -euo pipefail
        /usr/local/lib/otbr/stage2_init.py
        /usr/local/lib/otbr/stage2_init.py
        test -L /var/lib/thread
        test "$(readlink /var/lib/thread)" = "/data/thread"
        test -d /data/thread
        grep -Fxq "OTBR_DATA_DIR=/data/thread" /run/otbr/config.env
    '

docker run --rm "${docker_args[@]}" --entrypoint /bin/bash \
    -v "${tmp_dir}/vol_var_lib:/var/lib/thread" \
    -e DEVICE=/dev/null -e BACKBONE_IF=lo \
    "${image}" -c '
        set -euo pipefail
        /usr/local/lib/otbr/stage2_init.py
        /usr/local/lib/otbr/stage2_init.py
        test -d /var/lib/thread
        test ! -L /var/lib/thread
        grep -Fxq "OTBR_DATA_DIR=/var/lib/thread" /run/otbr/config.env
    '

dual_err=""
run_expect_fail dual_err \
    -v "${tmp_dir}/vol_data_thread:/data/thread" \
    -v "${tmp_dir}/vol_var_lib:/var/lib/thread" \
    -e DEVICE=/dev/null -e BACKBONE_IF=lo \
    "${image}"
grep -q "Both /var/lib/thread and /data" <<< "${dual_err}"

echo "smoke_container.sh PASSED for ${image}"
