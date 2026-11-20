from __future__ import annotations

import os
import subprocess
from pathlib import Path

import config
import pytest
import stage2_init


def _make_system_paths(
    tmp_path: Path,
    mountinfo_content: str = "10 1 8:1 / /data/thread rw - ext4 /dev/sda1 rw\n",
) -> stage2_init.SystemPaths:
    opt_root = tmp_path / "opt"
    for channel in ("stable", "beta"):
        sbin = opt_root / f"otbr-{channel}" / "sbin"
        sbin.mkdir(parents=True)
        for name in ("otbr-agent", "otbr-web", "ot-ctl"):
            exe = sbin / name
            exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            exe.chmod(0o755)
        (opt_root / f"otbr-{channel}" / "BUILD_INFO").write_text(
            f"channel={channel}\npatches=none\n",
            encoding="utf-8",
        )

    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(mountinfo_content, encoding="utf-8")

    sys_net = tmp_path / "sys_class_net"
    (sys_net / "eth0").mkdir(parents=True)
    (sys_net / "lo").mkdir(parents=True)

    fwd = tmp_path / "forwarding"
    fwd.write_text("1\n", encoding="utf-8")

    return stage2_init.SystemPaths(
        mountinfo=mountinfo,
        sys_class_net=sys_net,
        ipv6_forwarding=fwd,
        usr_sbin=tmp_path / "usr" / "sbin",
        opt_root=opt_root,
        var_lib_thread=tmp_path / "var" / "lib" / "thread",
        data_thread=tmp_path / "data" / "thread",
        run_config_env=tmp_path / "run" / "otbr" / "config.env",
        s6_user_contents=tmp_path / "etc" / "s6-overlay" / "user-bundles.d" / "user" / "contents.d",
        s6_agent_deps=tmp_path / "etc" / "s6-overlay" / "s6-rc.d" / "otbr-agent" / "dependencies.d",
    )


def test_collect_host_probe_reports_var_lib_states(tmp_path: Path) -> None:
    paths = _make_system_paths(tmp_path)
    device = tmp_path / "dev_ttyACM0"
    device.touch()

    probe_missing = stage2_init.collect_host_probe(
        {"DEVICE": str(device)}, paths=paths, default_route_detector=lambda: "eth0"
    )
    assert probe_missing.var_lib_thread_state is config.VarLibState.MISSING
    assert probe_missing.default_route_interface == "eth0"
    assert probe_missing.net_interfaces == frozenset({"eth0", "lo"})
    assert device in probe_missing.existing_paths
    assert probe_missing.available_channels == frozenset(
        {config.Channel.STABLE, config.Channel.BETA}
    )

    paths.var_lib_thread.mkdir(parents=True)
    probe_empty = stage2_init.collect_host_probe(
        {}, paths=paths, default_route_detector=lambda: "eth0"
    )
    assert probe_empty.var_lib_thread_state is config.VarLibState.EMPTY_DIR

    (paths.var_lib_thread / "dataset.bin").write_bytes(b"data")
    probe_nonempty = stage2_init.collect_host_probe(
        {}, paths=paths, default_route_detector=lambda: "eth0"
    )
    assert probe_nonempty.var_lib_thread_state is config.VarLibState.NONEMPTY_DIR

    (paths.var_lib_thread / "dataset.bin").unlink()
    paths.var_lib_thread.rmdir()
    paths.var_lib_thread.write_text("stray file", encoding="utf-8")
    probe_other = stage2_init.collect_host_probe(
        {}, paths=paths, default_route_detector=lambda: "eth0"
    )
    assert probe_other.var_lib_thread_state is config.VarLibState.OTHER_FILE

    paths.var_lib_thread.unlink()
    os.symlink(tmp_path / "missing-target", paths.var_lib_thread)
    probe_symlink = stage2_init.collect_host_probe(
        {}, paths=paths, default_route_detector=lambda: "eth0"
    )
    assert probe_symlink.var_lib_thread_state is config.VarLibState.SYMLINK


def test_stage2_wiring_channel_toggle_and_idempotency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _make_system_paths(tmp_path)
    device = tmp_path / "dev_ttyACM0"
    device.touch()

    paths.var_lib_thread.mkdir(parents=True)

    rc = stage2_init.run_stage2(
        {
            "DEVICE": str(device),
            "BETA": "0",
            "WEB_LISTEN_ADDRESS": "127.0.0.1",
            "WEB_PORT": "8080",
        },
        paths=paths,
        default_route_detector=lambda: "eth0",
    )
    assert rc == 0

    out = capsys.readouterr().out
    assert "channel=stable" in out
    assert "BUILD_INFO patches=none" in out
    assert "web=127.0.0.1:8080" in out

    assert os.readlink(paths.usr_sbin / "otbr-agent") == str(
        paths.channel_dir(config.Channel.STABLE) / "sbin" / "otbr-agent"
    )
    assert paths.var_lib_thread.is_symlink()
    assert Path(os.readlink(paths.var_lib_thread)) == paths.data_thread
    assert (paths.s6_user_contents / "otbr-web").is_file()
    assert not (paths.s6_user_contents / "socat-otbr-tcp").exists()
    assert not (paths.s6_agent_deps / "socat-otbr-tcp").exists()
    assert (paths.run_config_env.stat().st_mode & 0o777) == 0o644

    rc2 = stage2_init.run_stage2(
        {
            "NETWORK_DEVICE": "192.168.1.10:6638",
            "BETA": "1",
        },
        paths=paths,
        default_route_detector=lambda: "eth0",
    )
    assert rc2 == 0
    assert os.readlink(paths.usr_sbin / "otbr-agent") == str(
        paths.channel_dir(config.Channel.BETA) / "sbin" / "otbr-agent"
    )
    assert Path(os.readlink(paths.var_lib_thread)) == paths.data_thread
    assert not (paths.data_thread / "thread").exists()
    assert not (paths.s6_user_contents / "otbr-web").exists()
    assert (paths.s6_user_contents / "socat-otbr-tcp").is_file()
    assert (paths.s6_agent_deps / "socat-otbr-tcp").is_file()

    bash_check = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{paths.run_config_env}" && printf "%s|%s|%s|%s" '
                '"$OTBR_CHANNEL" "$OTBR_DEVICE" "$OTBR_NETWORK_DEVICE" "$OTBR_WEB_LISTEN_ADDRESS"'
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert bash_check.stdout == "beta|/tmp/ttyOTBR|192.168.1.10:6638|::"


def test_stage2_use_var_lib_directly(tmp_path: Path) -> None:
    paths = _make_system_paths(
        tmp_path,
        mountinfo_content="10 1 8:1 / /var/lib/thread rw - ext4 /dev/sda1 rw\n",
    )
    paths.var_lib_thread.mkdir(parents=True)
    existing = paths.var_lib_thread / "0_settings.bin"
    existing.write_bytes(b"dataset")
    device = tmp_path / "dev_ttyACM0"
    device.touch()

    rc = stage2_init.run_stage2(
        {"DEVICE": str(device), "BACKBONE_IF": "eth0"},
        paths=paths,
        default_route_detector=lambda: "eth0",
    )
    assert rc == 0
    assert not paths.var_lib_thread.is_symlink()
    assert existing.read_bytes() == b"dataset"
    assert "OTBR_DATA_DIR=/var/lib/thread\n" in paths.run_config_env.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["nonempty", "file"])
def test_stage2_rejects_unmounted_var_lib_conflict_before_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    paths = _make_system_paths(tmp_path)
    device = tmp_path / "dev_ttyACM0"
    device.touch()
    if kind == "nonempty":
        paths.var_lib_thread.mkdir(parents=True)
        (paths.var_lib_thread / "keep").write_text("data", encoding="utf-8")
    else:
        paths.var_lib_thread.parent.mkdir(parents=True)
        paths.var_lib_thread.write_text("stray", encoding="utf-8")

    rc = stage2_init.run_stage2(
        {"DEVICE": str(device), "BACKBONE_IF": "eth0"},
        paths=paths,
        default_route_detector=lambda: "eth0",
    )
    assert rc == 1
    assert "ERROR:" in capsys.readouterr().err
    assert not paths.run_config_env.exists()
    assert not (paths.usr_sbin / "otbr-agent").exists()


def test_stage2_handles_oserror_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _make_system_paths(tmp_path)
    device = tmp_path / "dev_ttyACM0"
    device.touch()

    blocked_parent = tmp_path / "run_as_file"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    bad_paths = stage2_init.SystemPaths(
        mountinfo=paths.mountinfo,
        sys_class_net=paths.sys_class_net,
        ipv6_forwarding=paths.ipv6_forwarding,
        usr_sbin=paths.usr_sbin,
        opt_root=paths.opt_root,
        var_lib_thread=paths.var_lib_thread,
        data_thread=paths.data_thread,
        run_config_env=blocked_parent / "config.env",
        s6_user_contents=paths.s6_user_contents,
        s6_agent_deps=paths.s6_agent_deps,
    )

    rc = stage2_init.run_stage2(
        {"DEVICE": str(device), "BACKBONE_IF": "eth0"},
        paths=bad_paths,
        default_route_detector=lambda: "eth0",
    )
    assert rc == 1
    assert "ERROR:" in capsys.readouterr().err


def test_stage2_software_flow_control_wires_config_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _make_system_paths(tmp_path)
    device = tmp_path / "dev_ttyACM0"
    device.touch()

    rc = stage2_init.run_stage2(
        {"DEVICE": str(device), "BACKBONE_IF": "eth0", "FLOW_CONTROL": "software"},
        paths=paths,
        default_route_detector=lambda: "eth0",
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "flow_control=software" in captured.out
    assert captured.err == ""

    bash_check = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{paths.run_config_env}" && printf "%s|%s" '
                '"$OTBR_FLOW_CONTROL" "$OTBR_UART_FLOW_PARAM"'
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert bash_check.stdout == "software|&uart-flow-control=software&uart-init-deassert"


def test_stage2_invalid_config_exits_1_without_writing_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _make_system_paths(tmp_path)
    device = tmp_path / "dev_ttyACM0"
    device.touch()

    rc = stage2_init.run_stage2(
        {"DEVICE": str(device), "BACKBONE_IF": "eth0", "FLOW_CONTROL": "invalid"},
        paths=paths,
        default_route_detector=lambda: "eth0",
    )
    assert rc == 1
    assert "ERROR:" in capsys.readouterr().err
    assert not paths.run_config_env.exists()
