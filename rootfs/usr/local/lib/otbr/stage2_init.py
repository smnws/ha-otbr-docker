#!/usr/bin/env python3
"""s6-overlay stage2 initialization: probe host, validate config, wire container."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    Channel,
    ConfigError,
    HostProbe,
    NetworkTransport,
    OtbrConfig,
    StorageAction,
    parse_config,
    parse_mountpoints,
    render_config_env,
)


@dataclass(frozen=True, slots=True)
class SystemPaths:
    mountinfo: Path = Path("/proc/self/mountinfo")
    sys_class_net: Path = Path("/sys/class/net")
    ipv6_forwarding: Path = Path("/proc/sys/net/ipv6/conf/all/forwarding")
    usr_sbin: Path = Path("/usr/sbin")
    opt_root: Path = Path("/opt")
    var_lib_thread: Path = Path("/var/lib/thread")
    data_thread: Path = Path("/data/thread")
    run_config_env: Path = Path("/run/otbr/config.env")
    s6_user_contents: Path = Path("/etc/s6-overlay/user-bundles.d/user/contents.d")
    s6_agent_deps: Path = Path("/etc/s6-overlay/s6-rc.d/otbr-agent/dependencies.d")


def _detect_default_route_interface() -> str | None:
    for cmd in (
        ["ip", "-o", "route", "show", "default"],
        ["ip", "-6", "-o", "route", "show", "default"],
    ):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError:
            continue
        if res.returncode != 0:
            continue
        for line in res.stdout.splitlines():
            m = re.search(r"\bdev\s+(\S+)", line)
            if m:
                return m.group(1)
    return None


def collect_host_probe(
    env: dict[str, str],
    paths: SystemPaths = SystemPaths(),
) -> HostProbe:
    mountinfo_text = (
        paths.mountinfo.read_text(encoding="utf-8") if paths.mountinfo.is_file() else ""
    )
    mountpoints = parse_mountpoints(mountinfo_text)

    net_interfaces = (
        frozenset(p.name for p in paths.sys_class_net.iterdir())
        if paths.sys_class_net.is_dir()
        else frozenset()
    )

    ipv6_forwarding = False
    if paths.ipv6_forwarding.is_file():
        ipv6_forwarding = paths.ipv6_forwarding.read_text(encoding="utf-8").strip() == "1"

    existing_paths: set[Path] = set()
    device_env = env.get("DEVICE", "").strip()
    if device_env:
        dev_path = Path(device_env)
        if dev_path.exists():
            existing_paths.add(dev_path)

    available_channels: set[Channel] = set()
    for ch in Channel:
        real_bin = paths.opt_root / f"otbr-{ch.value}" / "sbin" / "otbr-agent"
        if real_bin.is_file() and os.access(real_bin, os.X_OK):
            available_channels.add(ch)

    vlt = paths.var_lib_thread
    is_nonempty_unmounted = (
        vlt.is_dir()
        and not vlt.is_symlink()
        and Path("/var/lib/thread") not in mountpoints
        and any(vlt.iterdir())
    )

    return HostProbe(
        hostname=socket.gethostname(),
        net_interfaces=net_interfaces,
        default_route_interface=_detect_default_route_interface(),
        ipv6_forwarding_enabled=ipv6_forwarding,
        mountpoints=mountpoints,
        existing_paths=frozenset(existing_paths),
        available_channels=frozenset(available_channels),
        var_lib_thread_is_nonempty_dir=is_nonempty_unmounted,
    )


def _atomic_symlink(target: Path, link_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_link = link_path.with_name(f".{link_path.name}.tmp.{os.getpid()}")
    tmp_link.unlink(missing_ok=True)
    os.symlink(target, tmp_link)
    os.replace(tmp_link, link_path)


def _wire_storage(cfg: OtbrConfig, paths: SystemPaths) -> None:
    if cfg.storage.action is StorageAction.USE_VAR_LIB_DIRECTLY:
        paths.var_lib_thread.mkdir(parents=True, exist_ok=True)
        return

    paths.data_thread.mkdir(parents=True, exist_ok=True)
    vlt = paths.var_lib_thread
    target = paths.data_thread if paths.data_thread != Path("/data/thread") else cfg.storage.symlink_target
    assert target is not None

    if vlt.is_symlink():
        if Path(os.readlink(vlt)) == target:
            return
        vlt.unlink()
    elif vlt.exists():
        if not vlt.is_dir():
            raise ConfigError(f"{vlt} exists and is not a directory or symlink.")
        vlt.rmdir()

    vlt.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, vlt)


def _set_marker(path: Path, enabled: bool) -> None:
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)


def apply_wiring(cfg: OtbrConfig, paths: SystemPaths = SystemPaths()) -> None:
    """Apply channel symlinks, storage layout, s6 bundles, and config.env idempotently."""
    channel_sbin = paths.opt_root / f"otbr-{cfg.channel.value}" / "sbin"
    for binary in ("otbr-agent", "otbr-web", "ot-ctl"):
        _atomic_symlink(channel_sbin / binary, paths.usr_sbin / binary)

    _wire_storage(cfg, paths)

    _set_marker(paths.s6_user_contents / "otbr-web", cfg.web_port is not None)

    use_network = isinstance(cfg.transport, NetworkTransport)
    _set_marker(paths.s6_user_contents / "socat-otbr-tcp", use_network)
    _set_marker(paths.s6_agent_deps / "socat-otbr-tcp", use_network)

    paths.run_config_env.parent.mkdir(parents=True, exist_ok=True)
    tmp_env = paths.run_config_env.with_name(f".{paths.run_config_env.name}.tmp.{os.getpid()}")
    tmp_env.write_text(render_config_env(cfg), encoding="utf-8")
    os.chmod(tmp_env, 0o644)
    os.replace(tmp_env, paths.run_config_env)


def print_banner(cfg: OtbrConfig, paths: SystemPaths = SystemPaths()) -> None:
    build_info_path = paths.opt_root / f"otbr-{cfg.channel.value}" / "BUILD_INFO"
    print(f"INFO: Starting otbr-ha (channel={cfg.channel.value})")
    if build_info_path.is_file():
        for line in build_info_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                print(f"INFO: BUILD_INFO {line}")

    if isinstance(cfg.transport, NetworkTransport):
        transport_desc = f"tcp:{cfg.transport.socat_target} -> {cfg.transport.device_path}"
    else:
        transport_desc = str(cfg.transport.device_path)

    web_desc = str(cfg.web_port) if cfg.web_port is not None else "disabled"
    print(
        "INFO: Effective config: "
        f"transport={transport_desc} baudrate={cfg.baudrate} "
        f"flow_control={cfg.flow_control.value} backbone_if={cfg.backbone_if} "
        f"thread_if={cfg.thread_if} firewall={int(cfg.firewall)} nat64={int(cfg.nat64)} "
        f"log_level={cfg.log_level.name.lower()} "
        f"rest={cfg.rest_api.listen_address}:{cfg.rest_api.port} "
        f"web_port={web_desc} data_dir={cfg.storage.data_dir}"
    )
    for warning in cfg.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)


def run_stage2(
    env: dict[str, str],
    paths: SystemPaths = SystemPaths(),
    host: HostProbe | None = None,
) -> int:
    probe = host if host is not None else collect_host_probe(env, paths)
    try:
        cfg = parse_config(env, probe)
        apply_wiring(cfg, paths)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_banner(cfg, paths)
    return 0


def main() -> int:
    return run_stage2(dict(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
