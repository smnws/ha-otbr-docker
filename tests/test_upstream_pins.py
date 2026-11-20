from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import upstream_pins  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

SAMPLE_LOCAL_DOCKERFILE = """\
# BEGIN UPSTREAM-PINS
ARG BUILD_FROM=old
ARG OTBR_BETA_VERSION=old
ARG OTBR_VERSION=old
ARG SERIALX_VERSION=old
ARG UNIVERSAL_SILABS_FLASHER_VERSION=old
# END UPSTREAM-PINS

FROM ${BUILD_FROM} AS otbr-builder-base
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git wget iproute2 iputils-ping libreadline-dev libncurses-dev \\
    build-essential ninja-build cmake iptables libjsoncpp-dev \\
    libnetfilter-queue-dev nodejs npm libprotobuf-dev protobuf-compiler \\
    && rm -rf /var/lib/apt/lists/*

FROM otbr-builder-base AS otbr-beta-builder
ARG OTBR_BETA_VERSION

FROM otbr-builder-base AS otbr-stable-builder
ARG OTBR_VERSION

FROM ${BUILD_FROM} AS runtime
ARG SERIALX_VERSION
ARG UNIVERSAL_SILABS_FLASHER_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \\
    python3 python3-pip netcat-openbsd socat iproute2 iputils-ping \\
    ipset iptables libreadline8 libncurses6 libprotobuf-lite32 libjsoncpp26 \\
    && pip install --break-system-packages --no-cache-dir \\
    "universal-silabs-flasher==${UNIVERSAL_SILABS_FLASHER_VERSION}" \\
    "serialx==${SERIALX_VERSION}" \\
    && rm -rf /var/lib/apt/lists/*
"""

SAMPLE_BUILD_OTBR_SH = """\
#!/usr/bin/env bash
set -euo pipefail
install_prefix="$1"
cmake -GNinja \\
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \\
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \\
    .. \\
    -DBUILD_TESTING=OFF \\
    -DCMAKE_INSTALL_PREFIX="${install_prefix}" \\
    -DOTBR_FEATURE_FLAGS=ON \\
    -DOTBR_MDNS=openthread \\
    -DOTBR_VERSION= \\
    -DOT_PACKAGE_VERSION= \\
    -DOTBR_DBUS=OFF \\
    -DOT_POSIX_RCP_HDLC_BUS=ON \\
    -DOTBR_VENDOR_NAME="Home Assistant" \\
    -DOTBR_PRODUCT_NAME="OpenThread Border Router" \\
    -DOTBR_WEB=ON \\
    -DOTBR_BORDER_ROUTING=ON \\
    -DOTBR_REST=ON \\
    -DOTBR_BACKBONE_ROUTER=ON \\
    -DOTBR_TREL=ON \\
    -DOTBR_NAT64=ON \\
    -DOT_POSIX_NAT64_CIDR="192.168.255.0/24" \\
    -DOTBR_DNS_UPSTREAM_QUERY=ON \\
    -DOT_CHANNEL_MONITOR=ON \\
    -DOT_COAP=OFF \\
    -DOT_COAPS=OFF \\
    -DOT_THREAD_VERSION=1.4 \\
    -DOT_PROJECT_CONFIG="/usr/src/ot-br-posix/third_party/openthread/repo/openthread-core-ha-config-posix.h" \\
    -DOT_RCP_RESTORATION_MAX_COUNT=2
"""


@pytest.fixture()
def sandbox_repo(tmp_path: Path) -> Path:
    upstream_src = REPO_ROOT / "upstream" / "openthread_border_router"
    upstream_dst = tmp_path / "upstream" / "openthread_border_router"
    upstream_dst.mkdir(parents=True)
    shutil.copy(upstream_src / "Dockerfile", upstream_dst / "Dockerfile")
    shutil.copytree(upstream_src / "rootfs", upstream_dst / "rootfs")

    (tmp_path / "Dockerfile").write_text(SAMPLE_LOCAL_DOCKERFILE, encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "build-otbr.sh").write_text(SAMPLE_BUILD_OTBR_SH, encoding="utf-8")
    (tmp_path / "patches" / "ot-br-posix").mkdir(parents=True)
    (tmp_path / "patches" / "openthread").mkdir(parents=True)
    assert upstream_pins.main(["--repo-root", str(tmp_path), "sync"]) == 0
    assert upstream_pins.main(["--repo-root", str(tmp_path), "accept-rootfs"]) == 0
    return tmp_path


def test_sync_and_check_pass_on_aligned_repo(sandbox_repo: Path) -> None:
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 0
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "sync"]) == 0


def test_check_fails_on_pin_mismatch(sandbox_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    df = sandbox_repo / "Dockerfile"
    df.write_text(
        df.read_text(encoding="utf-8").replace("SERIALX_VERSION=1.8.0", "SERIALX_VERSION=9.9.9"),
        encoding="utf-8",
    )
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 1
    assert "ARG SERIALX_VERSION mismatch" in capsys.readouterr().err


def test_check_fails_on_builder_apt_drift(
    sandbox_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    df = sandbox_repo / "Dockerfile"
    df.write_text(df.read_text(encoding="utf-8").replace("protobuf-compiler ", ""), encoding="utf-8")
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 1
    err = capsys.readouterr().err
    assert "Builder apt packages mismatch" in err
    assert "protobuf-compiler" in err


def test_check_fails_on_runtime_apt_and_pip_drift(
    sandbox_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    df = sandbox_repo / "Dockerfile"
    text = df.read_text(encoding="utf-8").replace("ipset ", "").replace("serialx", "otherpkg")
    df.write_text(text, encoding="utf-8")
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 1
    err = capsys.readouterr().err
    assert "Runtime apt packages mismatch" in err
    assert "ipset" in err
    assert "Runtime pip packages mismatch" in err
    assert "serialx" in err


def test_check_fails_on_cmake_flag_drift(
    sandbox_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_sh = sandbox_repo / "build" / "build-otbr.sh"
    build_sh.write_text(
        build_sh.read_text(encoding="utf-8").replace("-DOTBR_NAT64=ON", "-DOTBR_NAT64=OFF"),
        encoding="utf-8",
    )
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 1
    err = capsys.readouterr().err
    assert "CMake -D flags mismatch" in err
    assert "-DOTBR_NAT64=ON" in err


def test_check_detects_rootfs_drift_and_ignores_migrate_script(
    sandbox_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rootfs = sandbox_repo / "upstream" / "openthread_border_router" / "rootfs"
    migrate = rootfs / "usr" / "local" / "bin" / "migrate_otbr_settings.py"
    migrate.write_text("# modified directly copied file\n", encoding="utf-8")
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 0

    (rootfs / "etc" / "s6-overlay" / "scripts" / "banner.sh").write_text("changed\n", encoding="utf-8")
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 1
    err = capsys.readouterr().err
    assert "modified upstream: etc/s6-overlay/scripts/banner.sh" in err

    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "accept-rootfs"]) == 0
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 0


def test_patch_check_skips_when_no_patches_present(
    sandbox_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "patch-check"]) == 0
    assert "patch-check skipped cleanly" in capsys.readouterr().out
