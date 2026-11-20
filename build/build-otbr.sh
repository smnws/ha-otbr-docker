#!/usr/bin/env bash
set -euo pipefail

install_prefix="${1:?Usage: build-otbr.sh <install_prefix>}"

cp /usr/src/openthread-core-ha-config-posix.h /usr/src/ot-br-posix/third_party/openthread/repo/
mkdir -p /usr/src/ot-br-posix/build
cd /usr/src/ot-br-posix/build

cmake \
    -GNinja \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    .. \
    -DBUILD_TESTING=OFF \
    -DCMAKE_INSTALL_PREFIX="${install_prefix}" \
    -DOTBR_FEATURE_FLAGS=ON \
    -DOTBR_MDNS=openthread \
    -DOTBR_VERSION= \
    -DOT_PACKAGE_VERSION= \
    -DOTBR_DBUS=OFF \
    -DOT_POSIX_RCP_HDLC_BUS=ON \
    -DOTBR_VENDOR_NAME="Home Assistant" \
    -DOTBR_PRODUCT_NAME="OpenThread Border Router" \
    -DOTBR_WEB=ON \
    -DOTBR_BORDER_ROUTING=ON \
    -DOTBR_REST=ON \
    -DOTBR_BACKBONE_ROUTER=ON \
    -DOTBR_TREL=ON \
    -DOTBR_NAT64=ON \
    -DOT_POSIX_NAT64_CIDR="192.168.255.0/24" \
    -DOTBR_DNS_UPSTREAM_QUERY=ON \
    -DOT_CHANNEL_MONITOR=ON \
    -DOT_COAP=OFF \
    -DOT_COAPS=OFF \
    -DOT_THREAD_VERSION=1.4 \
    -DOT_PROJECT_CONFIG="/usr/src/ot-br-posix/third_party/openthread/repo/openthread-core-ha-config-posix.h" \
    -DOT_RCP_RESTORATION_MAX_COUNT=2

ninja
ninja install
