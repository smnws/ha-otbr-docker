from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rootfs/usr/local/lib/otbr"))
import config  # noqa: E402
import stage2_init  # noqa: E402


def _make_system_paths(tmp_path: Path) -> stage2_init.SystemPaths:
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
    mountinfo.write_text("10 1 8:1 / /data/thread rw - ext4 /dev/sda1 rw\n", encoding="utf-8")

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


def _host_for_paths(paths: stage2_init.SystemPaths, device: Path) -> config.HostProbe:
    return config.HostProbe(
        hostname="pi-otbr",
        net_interfaces=frozenset({"lo", "eth0"}),
        default_route_interface="eth0",
        ipv6_forwarding_enabled=True,
        mountpoints=frozenset({Path("/data/thread")}),
        existing_paths=frozenset({device}),
        available_channels=frozenset({config.Channel.STABLE, config.Channel.BETA}),
        var_lib_thread_is_nonempty_dir=False,
    )


def test_stage2_wiring_channel_toggle_and_idempotency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _make_system_paths(tmp_path)
    device = tmp_path / "dev_ttyACM0"
    device.touch()
    host = _host_for_paths(paths, device)

    paths.var_lib_thread.mkdir(parents=True)

    rc = stage2_init.run_stage2(
        {
            "DEVICE": str(device),
            "BACKBONE_IF": "eth0",
            "BETA": "0",
            "WEB_PORT": "8080",
        },
        paths=paths,
        host=host,
    )
    assert rc == 0

    out = capsys.readouterr().out
    assert "channel=stable" in out
    assert "BUILD_INFO patches=none" in out

    assert os.readlink(paths.usr_sbin / "otbr-agent") == str(
        paths.opt_root / "otbr-stable" / "sbin" / "otbr-agent"
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
            "BACKBONE_IF": "eth0",
            "BETA": "1",
        },
        paths=paths,
        host=host,
    )
    assert rc2 == 0
    assert os.readlink(paths.usr_sbin / "otbr-agent") == str(
        paths.opt_root / "otbr-beta" / "sbin" / "otbr-agent"
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
            f'source "{paths.run_config_env}" && printf "%s|%s|%s" "$OTBR_CHANNEL" "$OTBR_DEVICE" "$OTBR_NETWORK_DEVICE"',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert bash_check.stdout == "beta|/tmp/ttyOTBR|192.168.1.10:6638"


def test_stage2_invalid_config_exits_1_without_writing_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _make_system_paths(tmp_path)
    device = tmp_path / "dev_ttyACM0"
    device.touch()
    host = _host_for_paths(paths, device)

    rc = stage2_init.run_stage2(
        {"DEVICE": str(device), "BACKBONE_IF": "eth0", "FLOW_CONTROL": "software"},
        paths=paths,
        host=host,
    )
    assert rc == 1
    assert "ERROR:" in capsys.readouterr().err
    assert not paths.run_config_env.exists()
