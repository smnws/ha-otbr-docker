#!/usr/bin/env python3
"""s6-overlay stage2 initialization: probe host, validate config, wire container."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config import (
    Channel,
    ConfigError,
    HostProbe,
    NetworkTransport,
    OtbrConfig,
    StorageAction,
    VarLibState,
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

    def channel_dir(self, channel: Channel) -> Path:
        return self.opt_root / f"otbr-{channel.value}"


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


def _inspect_var_lib_thread(path: Path) -> VarLibState:
    if path.is_symlink():
        return VarLibState.SYMLINK
    if not path.exists():
        return VarLibState.MISSING
    if not path.is_dir():
        return VarLibState.OTHER_FILE
    return VarLibState.NONEMPTY_DIR if any(path.iterdir()) else VarLibState.EMPTY_DIR


def collect_host_probe(
    env: dict[str, str],
    paths: SystemPaths = SystemPaths(),
    default_route_detector: Callable[[], str | None] = _detect_default_route_interface,
) -> HostProbe:
    mountinfo_text = (
        paths.mountinfo.read_text(encoding="utf-8") if paths.mountinfo.is_file() else ""
    )
    mountpoints_set = set(parse_mountpoints(mountinfo_text))
    if paths.var_lib_thread in mountpoints_set or Path("/var/lib/thread") in mountpoints_set:
        mountpoints_set.add(Path("/var/lib/thread"))
    if paths.data_thread in mountpoints_set or Path("/data/thread") in mountpoints_set:
        mountpoints_set.add(Path("/data/thread"))
    mountpoints = frozenset(mountpoints_set)

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
        real_bin = paths.channel_dir(ch) / "sbin" / "otbr-agent"
        if real_bin.is_file() and os.access(real_bin, os.X_OK):
            available_channels.add(ch)

    return HostProbe(
        net_interfaces=net_interfaces,
        default_route_interface=default_route_detector(),
        ipv6_forwarding_enabled=ipv6_forwarding,
        mountpoints=mountpoints,
        existing_paths=frozenset(existing_paths),
        available_channels=frozenset(available_channels),
        var_lib_thread_state=_inspect_var_lib_thread(paths.var_lib_thread),
    )


def _atomic_symlink(target: Path, link_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_link = link_path.with_name(f".{link_path.name}.tmp.{os.getpid()}")
    tmp_link.unlink(missing_ok=True)
    os.symlink(target, tmp_link)
    os.replace(tmp_link, link_path)


def _wire_storage(cfg: OtbrConfig, paths: SystemPaths) -> None:
    if cfg.storage is StorageAction.USE_VAR_LIB_DIRECTLY:
        paths.var_lib_thread.mkdir(parents=True, exist_ok=True)
        return

    paths.data_thread.mkdir(parents=True, exist_ok=True)
    vlt = paths.var_lib_thread
    if vlt.is_symlink() and Path(os.readlink(vlt)) == paths.data_thread:
        return
    if not vlt.is_symlink() and vlt.is_dir():
        vlt.rmdir()
    _atomic_symlink(paths.data_thread, vlt)


def _set_marker(path: Path, enabled: bool) -> None:
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)


def apply_wiring(cfg: OtbrConfig, paths: SystemPaths = SystemPaths()) -> None:
    """Apply channel symlinks, storage layout, s6 bundles, and config.env idempotently."""
    channel_sbin = paths.channel_dir(cfg.channel) / "sbin"
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
    build_info_path = paths.channel_dir(cfg.channel) / "BUILD_INFO"
    print(f"INFO: Starting ha-otbr-docker (channel={cfg.channel.value})")
    if build_info_path.is_file():
        for line in build_info_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                print(f"INFO: BUILD_INFO {line}")

    if isinstance(cfg.transport, NetworkTransport):
        transport_desc = f"tcp:{cfg.transport.socat_target} -> {cfg.transport.device_path}"
    else:
        transport_desc = str(cfg.transport.device_path)

    web_desc = (
        f"{cfg.web_listen_address}:{cfg.web_port}" if cfg.web_port is not None else "disabled"
    )
    print(
        "INFO: Effective config: "
        f"transport={transport_desc} baudrate={cfg.baudrate} "
        f"flow_control={cfg.flow_control.value} tx_power={cfg.tx_power} "
        f"backbone_if={cfg.backbone_if} "
        f"thread_if={cfg.thread_if} firewall={int(cfg.firewall)} nat64={int(cfg.nat64)} "
        f"log_level={cfg.log_level.name.lower()} "
        f"rest={cfg.rest_api.listen_address}:{cfg.rest_api.port} "
        f"web={web_desc} data_dir={cfg.storage.data_dir}"
    )
    for warning in cfg.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)


def run_stage2(
    env: dict[str, str],
    paths: SystemPaths = SystemPaths(),
    host: HostProbe | None = None,
    default_route_detector: Callable[[], str | None] = _detect_default_route_interface,
) -> int:
    try:
        probe = (
            host
            if host is not None
            else collect_host_probe(env, paths, default_route_detector=default_route_detector)
        )
        cfg = parse_config(env, probe)
        apply_wiring(cfg, paths)
    except (ConfigError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_banner(cfg, paths)
    return 0


def main() -> int:
    return run_stage2(dict(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
