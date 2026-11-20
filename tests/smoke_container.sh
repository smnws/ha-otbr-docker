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

if docker run --rm "${docker_args[@]}" "${image}" >/dev/null 2>&1; then
    echo "Expected container to fail fast without DEVICE or NETWORK_DEVICE" >&2
    exit 1
fi

sw_err="$(docker run --rm "${docker_args[@]}" \
    -e DEVICE=/dev/null \
    -e BACKBONE_IF=lo \
    -e FLOW_CONTROL=software \
    "${image}" 2>&1 || true)"
grep -q "ERROR:" <<< "${sw_err}"
grep -q "patches/openthread/" <<< "${sw_err}"

docker run --rm "${docker_args[@]}" --entrypoint /bin/bash \
    -e DEVICE=/dev/null \
    -e BACKBONE_IF=lo \
    -e BETA=0 \
    "${image}" -c '
        set -euo pipefail
        /usr/local/lib/otbr/stage2_init.py
        test "$(readlink /usr/sbin/otbr-agent)" = "/opt/otbr-stable/sbin/otbr-agent"
        grep -Fxq "channel=stable" /opt/otbr-stable/BUILD_INFO
        /usr/sbin/otbr-agent --version
        /usr/sbin/otbr-agent --help | grep -q -- "--rest-listen-port"
        python3 /usr/local/bin/migrate_otbr_settings.py --help >/dev/null
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
        /usr/sbin/otbr-agent --version
        /usr/sbin/otbr-agent --help | grep -q -- "--rest-listen-port"
    '

docker run --rm "${docker_args[@]}" --entrypoint /bin/bash \
    "${image}" -c '
        set -euo pipefail
        env -i PATH="$PATH" BACKBONE_IF=lo NETWORK_DEVICE="127.0.0.1:6638" WEB_PORT=8080 \
            /usr/local/lib/otbr/stage2_init.py
        test -f /etc/s6-overlay/user-bundles.d/user/contents.d/otbr-web
        test -f /etc/s6-overlay/user-bundles.d/user/contents.d/socat-otbr-tcp
        test -f /etc/s6-overlay/s6-rc.d/otbr-agent/dependencies.d/socat-otbr-tcp
        grep -Fxq "OTBR_WEB_PORT=8080" /run/otbr/config.env
        grep -Fxq "OTBR_DEVICE=/tmp/ttyOTBR" /run/otbr/config.env

        env -i PATH="$PATH" BACKBONE_IF=lo DEVICE=/dev/null \
            /usr/local/lib/otbr/stage2_init.py
        test ! -e /etc/s6-overlay/user-bundles.d/user/contents.d/otbr-web
        test ! -e /etc/s6-overlay/user-bundles.d/user/contents.d/socat-otbr-tcp
        test ! -e /etc/s6-overlay/s6-rc.d/otbr-agent/dependencies.d/socat-otbr-tcp
    '

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

dual_err="$(docker run --rm "${docker_args[@]}" \
    -v "${tmp_dir}/vol_data_thread:/data/thread" \
    -v "${tmp_dir}/vol_var_lib:/var/lib/thread" \
    -e DEVICE=/dev/null -e BACKBONE_IF=lo \
    "${image}" 2>&1 || true)"
grep -q "Both /var/lib/thread and /data/thread are mounted" <<< "${dual_err}"

echo "smoke_container.sh PASSED for ${image}"
