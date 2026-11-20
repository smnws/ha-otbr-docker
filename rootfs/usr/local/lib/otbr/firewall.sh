# shellcheck shell=bash

_OTBR_FORWARD_INGRESS_CHAIN="OTBR_FORWARD_INGRESS"
_OTBR_FORWARD_EGRESS_CHAIN="OTBR_FORWARD_EGRESS"
_OTBR_FORWARD_NAT64_CHAIN="OTBR_FORWARD_NAT64"
_OTBR_FW_MARK="0x1001"
_OTBR_IPSETS=(
    "otbr-ingress-deny-src"
    "otbr-ingress-deny-src-swap"
    "otbr-ingress-allow-dst"
    "otbr-ingress-allow-dst-swap"
)

_ipset() {
    "${IPSET_BIN:-ipset}" "$@"
}

_ip6tables() {
    "${IP6TABLES_BIN:-ip6tables}" -w "$@"
}

_iptables() {
    "${IPTABLES_BIN:-iptables}" -w "$@"
}

_ipset_destroy_if_exist() {
    local set_name="$1"
    local attempt
    for (( attempt = 0; attempt < 20; attempt++ )); do
        if ! _ipset list -n "${set_name}" >/dev/null 2>&1; then
            return 0
        fi
        if _ipset destroy "${set_name}" >/dev/null 2>&1; then
            return 0
        fi
        sleep "${OTBR_IPSET_RETRY_DELAY:-0.1}"
    done
    if _ipset list -n "${set_name}" >/dev/null 2>&1; then
        log_warning "Timed out destroying ipset ${set_name}; continuing teardown."
    fi
}

otbr_firewall_teardown() {
    local thread_if="${OTBR_THREAD_IF:-wpan0}"

    while _ip6tables -C FORWARD -o "${thread_if}" -j "${_OTBR_FORWARD_INGRESS_CHAIN}" 2>/dev/null; do
        _ip6tables -D FORWARD -o "${thread_if}" -j "${_OTBR_FORWARD_INGRESS_CHAIN}" || break
    done

    if _ip6tables -L "${_OTBR_FORWARD_INGRESS_CHAIN}" -n >/dev/null 2>&1; then
        _ip6tables -F "${_OTBR_FORWARD_INGRESS_CHAIN}"
        _ip6tables -X "${_OTBR_FORWARD_INGRESS_CHAIN}"
    fi

    local set_name
    for set_name in "${_OTBR_IPSETS[@]}"; do
        _ipset_destroy_if_exist "${set_name}"
    done

    while _ip6tables -C FORWARD -i "${thread_if}" -j "${_OTBR_FORWARD_EGRESS_CHAIN}" 2>/dev/null; do
        _ip6tables -D FORWARD -i "${thread_if}" -j "${_OTBR_FORWARD_EGRESS_CHAIN}" || break
    done

    if _ip6tables -L "${_OTBR_FORWARD_EGRESS_CHAIN}" -n >/dev/null 2>&1; then
        _ip6tables -F "${_OTBR_FORWARD_EGRESS_CHAIN}"
        _ip6tables -X "${_OTBR_FORWARD_EGRESS_CHAIN}"
    fi

    while _iptables -C FORWARD -j "${_OTBR_FORWARD_NAT64_CHAIN}" 2>/dev/null; do
        _iptables -D FORWARD -j "${_OTBR_FORWARD_NAT64_CHAIN}" || break
    done

    if _iptables -L "${_OTBR_FORWARD_NAT64_CHAIN}" -n >/dev/null 2>&1; then
        _iptables -F "${_OTBR_FORWARD_NAT64_CHAIN}"
        _iptables -X "${_OTBR_FORWARD_NAT64_CHAIN}"
    fi

    while _iptables -t mangle -C PREROUTING -i "${thread_if}" -j MARK --set-mark "${_OTBR_FW_MARK}" 2>/dev/null; do
        _iptables -t mangle -D PREROUTING -i "${thread_if}" -j MARK --set-mark "${_OTBR_FW_MARK}" || break
    done

    while _iptables -t nat -C POSTROUTING -m mark --mark "${_OTBR_FW_MARK}" -j MASQUERADE 2>/dev/null; do
        _iptables -t nat -D POSTROUTING -m mark --mark "${_OTBR_FW_MARK}" -j MASQUERADE || break
    done
}

otbr_firewall_apply() {
    local thread_if="${OTBR_THREAD_IF:-wpan0}"
    local backbone_if="${OTBR_BACKBONE_IF:?OTBR_BACKBONE_IF is required}"
    local firewall="${OTBR_FIREWALL:-1}"
    local nat64="${OTBR_NAT64:-0}"

    # Always clean stale chains/jumps left by an earlier SIGKILL before creating.
    otbr_firewall_teardown

    local set_name
    for set_name in "${_OTBR_IPSETS[@]}"; do
        _ipset create -exist "${set_name}" hash:net family inet6
    done

    _ip6tables -N "${_OTBR_FORWARD_INGRESS_CHAIN}"
    _ip6tables -I FORWARD 1 -o "${thread_if}" -j "${_OTBR_FORWARD_INGRESS_CHAIN}"

    _ip6tables -N "${_OTBR_FORWARD_EGRESS_CHAIN}"
    _ip6tables -I FORWARD 2 -i "${thread_if}" -j "${_OTBR_FORWARD_EGRESS_CHAIN}"

    if [[ "${firewall}" == "1" ]]; then
        log_info "Setting up OTBR firewall..."
        _ip6tables -A "${_OTBR_FORWARD_INGRESS_CHAIN}" -m pkttype --pkt-type unicast -i "${thread_if}" -j DROP
        _ip6tables -A "${_OTBR_FORWARD_INGRESS_CHAIN}" -m set --match-set otbr-ingress-deny-src src -j DROP
        _ip6tables -A "${_OTBR_FORWARD_INGRESS_CHAIN}" -m set --match-set otbr-ingress-allow-dst dst -j ACCEPT
        _ip6tables -A "${_OTBR_FORWARD_INGRESS_CHAIN}" -m pkttype --pkt-type unicast -j DROP
        _ip6tables -A "${_OTBR_FORWARD_INGRESS_CHAIN}" -j ACCEPT

        _ip6tables -A "${_OTBR_FORWARD_EGRESS_CHAIN}" -j ACCEPT
    else
        log_info "OTBR firewall disabled; allowing Thread forwarding."
        _ip6tables -A "${_OTBR_FORWARD_INGRESS_CHAIN}" -j ACCEPT
        _ip6tables -A "${_OTBR_FORWARD_EGRESS_CHAIN}" -j ACCEPT
    fi

    if [[ "${nat64}" == "1" ]]; then
        log_info "Setting up NAT64 iptables rules..."
        _iptables -t mangle -A PREROUTING -i "${thread_if}" -j MARK --set-mark "${_OTBR_FW_MARK}"
        _iptables -t nat -A POSTROUTING -m mark --mark "${_OTBR_FW_MARK}" -j MASQUERADE

        _iptables -N "${_OTBR_FORWARD_NAT64_CHAIN}"
        _iptables -I FORWARD 1 -j "${_OTBR_FORWARD_NAT64_CHAIN}"

        _iptables -A "${_OTBR_FORWARD_NAT64_CHAIN}" -m mark --mark "${_OTBR_FW_MARK}" -o "${backbone_if}" -j ACCEPT
        _iptables -A "${_OTBR_FORWARD_NAT64_CHAIN}" -m conntrack --ctstate ESTABLISHED,RELATED -i "${backbone_if}" -o "${thread_if}" -j ACCEPT
    fi
}
