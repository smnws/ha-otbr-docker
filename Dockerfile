# BEGIN UPSTREAM-PINS
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie-2026.11.0@sha256:d996c8f8ef0096a1ac3756766149b31b8171afc663068cc192f2bcab20f1b8c9
ARG OTBR_BETA_VERSION=76fb21d666a41050b43f8eaabc30e71d9d75d9fd
ARG OTBR_VERSION=cb4bdae242b22287cded07ae3aa10347c2d16d9b
ARG SERIALX_VERSION=1.8.0
ARG UNIVERSAL_SILABS_FLASHER_VERSION=0.1.3
# END UPSTREAM-PINS

FROM ${BUILD_FROM} AS otbr-builder-base
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV BORDER_ROUTING=1
ENV BACKBONE_ROUTER=1
ENV PLATFORM=debian
ENV RELEASE=1
ENV WEB_GUI=1
ENV REST_API=1
ENV DOCKER=1

WORKDIR /usr/src
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        wget \
        iproute2 \
        iputils-ping \
        libreadline-dev \
        libncurses-dev \
        build-essential \
        ninja-build \
        cmake \
        iptables \
        libjsoncpp-dev \
        libnetfilter-queue-dev \
        nodejs \
        npm \
        libprotobuf-dev \
        protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY upstream/openthread_border_router/openthread-core-ha-config-posix.h /usr/src/
COPY build/fetch-otbr.sh /usr/local/bin/

FROM otbr-builder-base AS otbr-beta-builder
ARG OTBR_BETA_VERSION

RUN /usr/local/bin/fetch-otbr.sh "${OTBR_BETA_VERSION}"

COPY build/apply-patches.sh build/build-otbr.sh /usr/local/bin/
COPY patches/ /usr/src/patches/

RUN /usr/local/bin/apply-patches.sh beta /opt/otbr-beta "${OTBR_BETA_VERSION}" \
    && /usr/local/bin/build-otbr.sh /opt/otbr-beta

FROM otbr-builder-base AS otbr-stable-builder
ARG OTBR_VERSION

RUN /usr/local/bin/fetch-otbr.sh "${OTBR_VERSION}"

COPY build/apply-patches.sh build/build-otbr.sh /usr/local/bin/
COPY patches/ /usr/src/patches/

RUN /usr/local/bin/apply-patches.sh stable /opt/otbr-stable "${OTBR_VERSION}" \
    && /usr/local/bin/build-otbr.sh /opt/otbr-stable

FROM ${BUILD_FROM} AS runtime
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG SERIALX_VERSION
ARG UNIVERSAL_SILABS_FLASHER_VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        netcat-openbsd \
        socat \
        iproute2 \
        iputils-ping \
        ipset \
        iptables \
        libreadline8 \
        libncurses6 \
        libprotobuf-lite32 \
        libjsoncpp26 \
    && pip install --break-system-packages --no-cache-dir \
        "universal-silabs-flasher==${UNIVERSAL_SILABS_FLASHER_VERSION}" \
        "serialx==${SERIALX_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/iproute2 \
    && echo "88 openthread" >> /etc/iproute2/rt_tables

COPY --from=otbr-beta-builder /opt/otbr-beta /opt/otbr-beta
COPY --from=otbr-stable-builder /opt/otbr-stable /opt/otbr-stable
COPY upstream/openthread_border_router/rootfs/usr/local/bin/migrate_otbr_settings.py /usr/local/bin/migrate_otbr_settings.py
COPY rootfs /

ENV LOG_FORMAT="{LEVEL}: {MESSAGE}" \
    S6_STAGE2_HOOK=/etc/s6-overlay/scripts/stage2-init.sh \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/etc/s6-overlay/s6-rc.d/otbr-agent/data/check"]

LABEL org.opencontainers.image.title="OpenThread Border Router (Standalone)" \
    org.opencontainers.image.description="Standalone Docker image built from home-assistant/app-openthread-border-router" \
    org.opencontainers.image.source="https://github.com/smnws/otbr-ha" \
    org.opencontainers.image.licenses="Apache-2.0"
