#!/usr/bin/with-contenv bash
# shellcheck shell=bash
set -euo pipefail

# shellcheck source=rootfs/usr/local/lib/otbr/common.sh
source /usr/local/lib/otbr/common.sh
load_otbr_config

# ot-ctl exits 0 even on CLI errors ("Error <n>: ..."); detect them by checking
# for the trailing "Done" line.
otctl() {
    local output
    if ! output="$(ot-ctl "$@" 2>&1)"; then
        log_error "ot-ctl $* failed: ${output}"
        return 1
    fi

    if [[ "${output//[[:space:]]/}" != *Done ]]; then
        log_warning "ot-ctl $* failed: ${output}"
    fi
}

otctl trel enable

if [[ "${OTBR_NAT64}" == "1" ]]; then
    log_info "Enabling NAT64."
    otctl nat64 enable
    otctl dns server upstream enable
fi

short_host="$(hostname)"
short_host="${short_host%%.*}"
mdns_localhostname="${short_host:0:58}-otbr"
log_info "Setting OpenThread mDNS local hostname to ${mdns_localhostname}."
otctl mdns localhostname "${mdns_localhostname}"
otctl mdns enable

# Keep TX power controlled to avoid asymmetric link quality with other Thread routers.
otctl txpower "${OTBR_TX_POWER:-6}"
