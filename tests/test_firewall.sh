#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=rootfs/usr/local/lib/otbr/common.sh
source "${repo_root}/rootfs/usr/local/lib/otbr/common.sh"
# shellcheck source=rootfs/usr/local/lib/otbr/firewall.sh
source "${repo_root}/rootfs/usr/local/lib/otbr/firewall.sh"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

state_dir="${tmp_dir}/state"
mkdir -p "${state_dir}/ipsets" "${state_dir}/ip6_chains" "${state_dir}/ip4_chains"
: > "${state_dir}/ip6_jumps"
: > "${state_dir}/ip4_jumps"
: > "${state_dir}/mangle"
: > "${state_dir}/nat"
: > "${tmp_dir}/calls.log"

cat > "${tmp_dir}/ipset" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "ipset $*" >> "${MOCK_LOG}"
if [[ "${MOCK_IPSET_FAIL_DESTROY:-0}" == "1" && "$1" == "destroy" ]]; then
    exit 1
fi
case "$1" in
    create)
        touch "${MOCK_STATE}/ipsets/$3"
        ;;
    flush)
        [[ -f "${MOCK_STATE}/ipsets/$2" ]]
        : > "${MOCK_STATE}/ipsets/$2"
        ;;
    list)
        [[ -f "${MOCK_STATE}/ipsets/$3" ]]
        ;;
    destroy)
        rm -f "${MOCK_STATE}/ipsets/$2"
        ;;
    *)
        echo "Unsupported ipset command: $*" >&2
        exit 98
        ;;
esac
EOF

make_iptables_stub() {
    local name="$1"
    local chain_dir="$2"
    local jump_file="$3"
    cat > "${tmp_dir}/${name}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
echo "${name} \$*" >> "\${MOCK_LOG}"
if [[ "\${1:-}" != "-w" ]]; then
    echo "Missing -w flag on ${name}: \$*" >&2
    exit 99
fi
shift
table="filter"
if [[ "\${1:-}" == "-t" ]]; then
    table="\$2"
    shift 2
fi
op="\${1:-}"
shift
case "\${op}" in
    -N)
        if [[ -f "\${MOCK_STATE}/${chain_dir}/\$1" ]]; then
            echo "Chain already exists: \$1" >&2
            exit 1
        fi
        : > "\${MOCK_STATE}/${chain_dir}/\$1"
        ;;
    -L)
        [[ -f "\${MOCK_STATE}/${chain_dir}/\$1" ]]
        ;;
    -F)
        : > "\${MOCK_STATE}/${chain_dir}/\$1"
        ;;
    -X)
        rm -f "\${MOCK_STATE}/${chain_dir}/\$1"
        ;;
    -A|-I)
        if [[ "\${table}" == "mangle" ]]; then
            echo "\$*" >> "\${MOCK_STATE}/mangle"
        elif [[ "\${table}" == "nat" ]]; then
            echo "\$*" >> "\${MOCK_STATE}/nat"
        elif [[ "\$1" == "FORWARD" ]]; then
            shift
            if [[ "\${1:-}" =~ ^[0-9]+$ ]]; then shift; fi
            echo "\$*" >> "\${MOCK_STATE}/${jump_file}"
        else
            chain="\$1"; shift
            echo "\$*" >> "\${MOCK_STATE}/${chain_dir}/\${chain}"
        fi
        ;;
    -C)
        if [[ "\${table}" == "mangle" ]]; then
            grep -Fxq -- "\$*" "\${MOCK_STATE}/mangle"
        elif [[ "\${table}" == "nat" ]]; then
            grep -Fxq -- "\$*" "\${MOCK_STATE}/nat"
        else
            shift
            grep -Fxq -- "\$*" "\${MOCK_STATE}/${jump_file}"
        fi
        ;;
    -D)
        if [[ "\${table}" == "mangle" ]]; then
            grep -Fxv -- "\$*" "\${MOCK_STATE}/mangle" > "\${MOCK_STATE}/mangle.tmp" || true
            mv "\${MOCK_STATE}/mangle.tmp" "\${MOCK_STATE}/mangle"
        elif [[ "\${table}" == "nat" ]]; then
            grep -Fxv -- "\$*" "\${MOCK_STATE}/nat" > "\${MOCK_STATE}/nat.tmp" || true
            mv "\${MOCK_STATE}/nat.tmp" "\${MOCK_STATE}/nat"
        else
            shift
            grep -Fxv -- "\$*" "\${MOCK_STATE}/${jump_file}" > "\${MOCK_STATE}/${jump_file}.tmp" || true
            mv "\${MOCK_STATE}/${jump_file}.tmp" "\${MOCK_STATE}/${jump_file}"
        fi
        ;;
esac
EOF
    chmod +x "${tmp_dir}/${name}"
}

make_iptables_stub "ip6tables" "ip6_chains" "ip6_jumps"
make_iptables_stub "iptables" "ip4_chains" "ip4_jumps"
chmod +x "${tmp_dir}/ipset"

export MOCK_STATE="${state_dir}"
export MOCK_LOG="${tmp_dir}/calls.log"
export IPSET_BIN="${tmp_dir}/ipset"
export IP6TABLES_BIN="${tmp_dir}/ip6tables"
export IPTABLES_BIN="${tmp_dir}/iptables"
export OTBR_IPSET_RETRY_DELAY="0.001"

export OTBR_THREAD_IF="wpan0"
export OTBR_BACKBONE_IF="eth0"
export OTBR_FIREWALL="1"
export OTBR_NAT64="0"

echo "stale-entry" > "${state_dir}/ipsets/otbr-ingress-deny-src"
MOCK_IPSET_FAIL_DESTROY=1 otbr_firewall_apply 2> "${tmp_dir}/apply_warn.log"
grep -q "Timed out destroying ipset otbr-ingress-deny-src" "${tmp_dir}/apply_warn.log"
test ! -s "${state_dir}/ipsets/otbr-ingress-deny-src"

for s in otbr-ingress-deny-src otbr-ingress-deny-src-swap otbr-ingress-allow-dst otbr-ingress-allow-dst-swap; do
    test -f "${state_dir}/ipsets/${s}"
    grep -Fxq "ipset flush ${s}" "${tmp_dir}/calls.log"
done
test "$(wc -l < "${state_dir}/ip6_chains/OTBR_FORWARD_INGRESS")" -eq 5
grep -Fq -- "-m set --match-set otbr-ingress-deny-src src -j DROP" "${state_dir}/ip6_chains/OTBR_FORWARD_INGRESS"
grep -Fq -- "-m set --match-set otbr-ingress-allow-dst dst -j ACCEPT" "${state_dir}/ip6_chains/OTBR_FORWARD_INGRESS"
test "$(wc -l < "${state_dir}/ip6_chains/OTBR_FORWARD_EGRESS")" -eq 1
test "$(wc -l < "${state_dir}/ip6_jumps")" -eq 2
test ! -f "${state_dir}/ip4_chains/OTBR_FORWARD_NAT64"

otbr_firewall_apply
test "$(wc -l < "${state_dir}/ip6_jumps")" -eq 2
test "$(wc -l < "${state_dir}/ip6_chains/OTBR_FORWARD_INGRESS")" -eq 5

export OTBR_FIREWALL="0"
export OTBR_NAT64="1"
otbr_firewall_apply
test "$(wc -l < "${state_dir}/ip6_chains/OTBR_FORWARD_INGRESS")" -eq 1
test -f "${state_dir}/ip4_chains/OTBR_FORWARD_NAT64"
test "$(wc -l < "${state_dir}/ip4_chains/OTBR_FORWARD_NAT64")" -eq 2
grep -Fq -- "-m conntrack --ctstate ESTABLISHED,RELATED -i eth0 -o wpan0 -j ACCEPT" "${state_dir}/ip4_chains/OTBR_FORWARD_NAT64"
test "$(wc -l < "${state_dir}/mangle")" -eq 1
test "$(wc -l < "${state_dir}/nat")" -eq 1
grep -Fq -- "-m mark --mark 0x1001 -j MASQUERADE" "${state_dir}/nat"

otbr_firewall_teardown
otbr_firewall_teardown
test "$(wc -l < "${state_dir}/ip6_jumps")" -eq 0
test "$(wc -l < "${state_dir}/ip4_jumps")" -eq 0
test "$(wc -l < "${state_dir}/mangle")" -eq 0
test "$(wc -l < "${state_dir}/nat")" -eq 0
test ! -f "${state_dir}/ip6_chains/OTBR_FORWARD_INGRESS"
test ! -f "${state_dir}/ip4_chains/OTBR_FORWARD_NAT64"

touch "${state_dir}/ipsets/otbr-ingress-deny-src"
MOCK_IPSET_FAIL_DESTROY=1 otbr_firewall_teardown 2> "${tmp_dir}/warn.log"
grep -q "Timed out destroying ipset otbr-ingress-deny-src" "${tmp_dir}/warn.log"

echo "test_firewall.sh PASSED"
