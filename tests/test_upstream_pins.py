from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import upstream_pins

SYNTHETIC_UPSTREAM_DOCKERFILE = """\
ARG BUILD_FROM=ghcr.io/example/base-debian:trixie@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

FROM ${BUILD_FROM} AS otbr-beta-builder
ARG OTBR_BETA_VERSION=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
RUN --mount=type=cache,target=/var/cache/apt \\
    apt-get update \\
    && apt-get install -y -t trixie-backports --no-install-recommends \\
        cmake \\
        git \\
        protobuf-compiler \\
    && cmake -GNinja .. \\
        -DCMAKE_INSTALL_PREFIX=/opt/otbr-beta \\
        -DOTBR_NAT64=ON \\
        -DOTBR_WEB=ON

FROM ${BUILD_FROM} AS otbr-stable-builder
ARG OTBR_VERSION=cccccccccccccccccccccccccccccccccccccccc
RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        cmake \\
        git \\
        protobuf-compiler \\
    && cmake -GNinja .. \\
        -DCMAKE_INSTALL_PREFIX=/opt/otbr-stable \\
        -DOTBR_NAT64=ON \\
        -DOTBR_WEB=ON

FROM ${BUILD_FROM}
ARG SERIALX_VERSION=2.3.4
ARG UNIVERSAL_SILABS_FLASHER_VERSION=0.5.6
RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        ipset \\
        python3 \\
    && pip install --break-system-packages --no-cache-dir -i https://pypi.org/simple \\
        universal-silabs-flasher==${UNIVERSAL_SILABS_FLASHER_VERSION} \\
        serialx==${SERIALX_VERSION}
LABEL org.opencontainers.image.description="unmatched ' quote in non-RUN line is ignored"
"""

SYNTHETIC_LOCAL_DOCKERFILE = """\
# BEGIN UPSTREAM-PINS
ARG BUILD_FROM=old
ARG OTBR_BETA_VERSION=old
ARG OTBR_VERSION=old
ARG SERIALX_VERSION=old
ARG UNIVERSAL_SILABS_FLASHER_VERSION=old
# END UPSTREAM-PINS

FROM ${BUILD_FROM} AS otbr-builder-base
RUN apt-get update && apt-get install -y --no-install-recommends \\
    cmake git protobuf-compiler \\
    && rm -rf /var/lib/apt/lists/*

FROM otbr-builder-base AS otbr-beta-builder
ARG OTBR_BETA_VERSION

FROM otbr-builder-base AS otbr-stable-builder
ARG OTBR_VERSION

FROM ${BUILD_FROM} AS runtime
ARG SERIALX_VERSION
ARG UNIVERSAL_SILABS_FLASHER_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ipset python3 \\
    && pip install --break-system-packages --no-cache-dir \\
    "universal-silabs-flasher==${UNIVERSAL_SILABS_FLASHER_VERSION}" \\
    "serialx==${SERIALX_VERSION}" \\
    && rm -rf /var/lib/apt/lists/*
"""

SYNTHETIC_BUILD_OTBR_SH = """\
#!/usr/bin/env bash
set -euo pipefail
install_prefix="$1"
cmake -GNinja .. \\
    -DCMAKE_INSTALL_PREFIX="${install_prefix}" \\
    -DOTBR_NAT64=ON \\
    -DOTBR_WEB=ON
"""


@pytest.fixture()
def sandbox_repo(tmp_path: Path) -> Path:
    upstream_dst = tmp_path / "upstream" / "openthread_border_router"
    rootfs = upstream_dst / "rootfs"
    (rootfs / "usr" / "local" / "bin").mkdir(parents=True)
    (rootfs / "etc" / "s6-overlay" / "scripts").mkdir(parents=True)
    (rootfs / "usr" / "local" / "bin" / "migrate_otbr_settings.py").write_text(
        "# migrate\n", encoding="utf-8"
    )
    (rootfs / "etc" / "s6-overlay" / "scripts" / "banner.sh").write_text(
        "#!/bin/sh\necho hi\n", encoding="utf-8"
    )
    (upstream_dst / "Dockerfile").write_text(SYNTHETIC_UPSTREAM_DOCKERFILE, encoding="utf-8")

    (tmp_path / "Dockerfile").write_text(SYNTHETIC_LOCAL_DOCKERFILE, encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "build-otbr.sh").write_text(SYNTHETIC_BUILD_OTBR_SH, encoding="utf-8")
    (tmp_path / "patches" / "ot-br-posix").mkdir(parents=True)
    (tmp_path / "patches" / "openthread").mkdir(parents=True)
    assert upstream_pins.main(["--repo-root", str(tmp_path), "sync"]) == 0
    assert upstream_pins.main(["--repo-root", str(tmp_path), "accept-rootfs"]) == 0
    return tmp_path


def test_sync_and_check_pass_on_aligned_repo(sandbox_repo: Path) -> None:
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 0
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "sync"]) == 0


def test_check_fails_on_pin_mismatch(
    sandbox_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    df = sandbox_repo / "Dockerfile"
    df.write_text(
        df.read_text(encoding="utf-8").replace("SERIALX_VERSION=2.3.4", "SERIALX_VERSION=9.9.9"),
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
    original = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        original.replace("-GNinja ..", "-GNinja -DCMAKE_BUILD_TYPE=Release .."),
        encoding="utf-8",
    )
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 0

    build_sh.write_text(
        original.replace("-DOTBR_NAT64=ON", "-DOTBR_NAT64=OFF"),
        encoding="utf-8",
    )
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 1
    err = capsys.readouterr().err
    assert "CMake -D flags mismatch" in err
    assert "-DOTBR_NAT64=ON" in err


def test_sync_succeeds_and_check_reports_cleanly_on_divergent_upstream_builders(
    sandbox_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    upstream_df = sandbox_repo / "upstream" / "openthread_border_router" / "Dockerfile"
    diverged = upstream_df.read_text(encoding="utf-8").replace(
        "-DOTBR_WEB=ON",
        "-DOTBR_WEB=OFF",
        1,
    )
    diverged = diverged.replace("SERIALX_VERSION=2.3.4", "SERIALX_VERSION=3.0.0")
    upstream_df.write_text(diverged, encoding="utf-8")

    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "sync"]) == 0
    assert "ARG SERIALX_VERSION=3.0.0" in (sandbox_repo / "Dockerfile").read_text(encoding="utf-8")

    capsys.readouterr()
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "check"]) == 1
    err = capsys.readouterr().err
    assert "Upstream drift check FAILED:" in err
    assert "disagree on CMake flags" in err


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


def test_tokenizer_fails_loudly_on_unclosed_quote_in_run() -> None:
    broken = 'FROM base\nRUN apt-get install "unclosed\n'
    with pytest.raises(ValueError, match="Failed to tokenize line 2"):
        upstream_pins.extract_apt_packages(broken)


def test_rootfs_diff_is_empty_noop_when_submodule_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sub_src = tmp_path / "sub_src"
    sub_src.mkdir()
    subprocess.run(["git", "-C", str(sub_src), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(sub_src), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(sub_src), "config", "user.name", "T"], check=True)
    rootfs_file = sub_src / "openthread_border_router" / "rootfs" / "a.txt"
    rootfs_file.parent.mkdir(parents=True)
    rootfs_file.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(sub_src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(sub_src), "commit", "-q", "-m", "v1"], check=True)

    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()
    subprocess.run(["git", "-C", str(main_repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(main_repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(main_repo), "config", "user.name", "T"], check=True)
    subprocess.run(
        ["git", "-C", str(main_repo), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), "upstream"],
        check=True,
    )
    subprocess.run(["git", "-C", str(main_repo), "commit", "-q", "-m", "add sub"], check=True)

    assert upstream_pins.main(["--repo-root", str(main_repo), "rootfs-diff"]) == 0
    assert capsys.readouterr().out == ""

    rootfs_file_in_sub = main_repo / "upstream" / "openthread_border_router" / "rootfs" / "a.txt"
    rootfs_file_in_sub.write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(main_repo / "upstream"), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(main_repo / "upstream"), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(main_repo / "upstream"), "add", "."], check=True)
    subprocess.run(["git", "-C", str(main_repo / "upstream"), "commit", "-q", "-m", "v2"], check=True)

    assert upstream_pins.main(["--repo-root", str(main_repo), "rootfs-diff"]) == 0
    diff_out = capsys.readouterr().out
    assert "-v1" in diff_out
    assert "+v2" in diff_out


def test_patch_check_skips_when_empty_and_rejects_stray_patches(
    sandbox_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "patch-check"]) == 0
    assert "patch-check skipped cleanly" in capsys.readouterr().out

    stray_root = sandbox_repo / "patches" / "0001.patch"
    stray_root.write_text("diff\n", encoding="utf-8")
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "patch-check"]) == 1
    assert "0001.patch" in capsys.readouterr().err
    stray_root.unlink()

    stray_typo = sandbox_repo / "patches" / "otbr" / "0001.patch"
    stray_typo.parent.mkdir(parents=True)
    stray_typo.write_text("diff\n", encoding="utf-8")
    assert upstream_pins.main(["--repo-root", str(sandbox_repo), "patch-check"]) == 1
    assert "otbr/0001.patch" in capsys.readouterr().err
