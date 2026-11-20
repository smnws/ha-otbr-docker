"""Pure configuration parser and renderer for standalone OTBR."""

from __future__ import annotations

import ipaddress
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ConfigError(ValueError):
    """Raised when container configuration or host state is invalid."""


class Channel(Enum):
    STABLE = "stable"
    BETA = "beta"

    @property
    def prefix(self) -> Path:
        return Path(f"/opt/otbr-{self.value}")


class FlowControl(Enum):
    NONE = "none"
    HARDWARE = "hardware"
    SOFTWARE = "software"

    @property
    def uart_query_param(self) -> str:
        if self is FlowControl.HARDWARE:
            return "&uart-flow-control"
        if self is FlowControl.SOFTWARE:
            return "&uart-flow-control=software&uart-init-deassert"
        return "&uart-init-deassert"


class LogLevel(Enum):
    EMERGENCY = 0
    ALERT = 1
    CRITICAL = 2
    ERROR = 3
    WARNING = 4
    NOTICE = 5
    INFO = 6
    DEBUG = 7


class VarLibState(Enum):
    MISSING = "missing"
    EMPTY_DIR = "empty_dir"
    NONEMPTY_DIR = "nonempty_dir"
    SYMLINK = "symlink"
    OTHER_FILE = "other_file"


class StorageAction(Enum):
    USE_VAR_LIB_DIRECTLY = "use_var_lib_directly"
    SYMLINK_DATA_THREAD = "symlink_data_thread"

    @property
    def data_dir(self) -> Path:
        if self is StorageAction.USE_VAR_LIB_DIRECTLY:
            return Path("/var/lib/thread")
        return Path("/data/thread")


VALID_BAUDRATES = frozenset({57600, 115200, 230400, 460800, 921600, 1000000})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

_TYPO_VARS: tuple[tuple[str, str], ...] = (
    ("BACKBONE_INTERFACE", "BACKBONE_IF"),
    ("OTBR_WEB_PORT", "WEB_PORT"),
    ("WEB_GUI_PORT", "WEB_PORT"),
    ("WEB_GUI", "WEB_PORT"),
    ("OTBR_REST_PORT", "REST_LISTEN_PORT"),
)


@dataclass(frozen=True, slots=True)
class SerialTransport:
    device: Path

    @property
    def device_path(self) -> Path:
        return self.device


@dataclass(frozen=True, slots=True)
class NetworkTransport:
    host: str
    port: int
    pty_path: Path = Path("/tmp/ttyOTBR")

    @property
    def device_path(self) -> Path:
        return self.pty_path

    @property
    def socat_target(self) -> str:
        try:
            addr = ipaddress.ip_address(self.host)
        except ValueError:
            return f"{self.host}:{self.port}"
        if isinstance(addr, ipaddress.IPv6Address):
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"


RadioTransport = SerialTransport | NetworkTransport


@dataclass(frozen=True, slots=True)
class RestApiConfig:
    listen_address: str
    port: int

    @property
    def probe_host(self) -> str:
        if self.listen_address == "::":
            return "::1"
        if self.listen_address == "0.0.0.0":
            return "127.0.0.1"
        return self.listen_address


@dataclass(frozen=True, slots=True)
class HostProbe:
    net_interfaces: frozenset[str]
    default_route_interface: str | None
    ipv6_forwarding_enabled: bool
    mountpoints: frozenset[Path]
    existing_paths: frozenset[Path]
    available_channels: frozenset[Channel]
    var_lib_thread_state: VarLibState


@dataclass(frozen=True, slots=True)
class OtbrConfig:
    channel: Channel
    transport: RadioTransport
    baudrate: int
    flow_control: FlowControl
    tx_power: int
    backbone_if: str
    thread_if: str
    firewall: bool
    nat64: bool
    log_level: LogLevel
    rest_api: RestApiConfig
    web_listen_address: str
    web_port: int | None
    storage: StorageAction
    warnings: tuple[str, ...]


def _get_env(env: Mapping[str, str], key: str) -> str | None:
    val = env.get(key)
    if val is None:
        return None
    stripped = val.strip()
    return stripped if stripped else None


def _parse_bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ConfigError(
        f"Invalid boolean for {name}: {raw!r} (expected 0/1, true/false, yes/no, on/off)."
    )


def _parse_port(name: str, raw: str) -> int:
    if not re.fullmatch(r"\d+", raw):
        raise ConfigError(f"Invalid port for {name}: {raw!r} (must be integer 1..65535).")
    port = int(raw)
    if not (1 <= port <= 65535):
        raise ConfigError(f"Invalid port for {name}: {port} (must be between 1 and 65535).")
    return port


def _parse_baudrate(raw: str | None) -> int:
    if raw is None:
        return 460800
    try:
        baudrate = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Invalid BAUDRATE: {raw!r}") from exc
    if baudrate not in VALID_BAUDRATES:
        valid_str = ", ".join(str(b) for b in sorted(VALID_BAUDRATES))
        raise ConfigError(f"Unsupported BAUDRATE {baudrate}; expected one of: {valid_str}.")
    return baudrate


def _parse_log_level(raw: str | None) -> LogLevel:
    if raw is None:
        return LogLevel.NOTICE
    try:
        return LogLevel[raw.upper()]
    except KeyError as exc:
        valid_levels = ", ".join(lvl.name.lower() for lvl in LogLevel)
        raise ConfigError(
            f"Invalid OTBR_LOG_LEVEL: {raw!r} (expected one of: {valid_levels})."
        ) from exc


def _parse_ip_address(name: str, raw: str | None, default: str) -> str:
    value = raw if raw is not None else default
    if "%" in value:
        raise ConfigError(
            f"Invalid {name}: {value!r} (IPv6 scope/zone IDs are not supported)."
        )
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ConfigError(
            f"Invalid {name}: {value!r} (must be an IPv4 or IPv6 address)."
        ) from exc


def _parse_flow_control(raw: str | None, *, default: FlowControl) -> FlowControl:
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _FALSE_VALUES or lowered == "none":
        return FlowControl.NONE
    if lowered in _TRUE_VALUES or lowered == "hardware":
        return FlowControl.HARDWARE
    if lowered == "software":
        return FlowControl.SOFTWARE
    raise ConfigError(
        f"Invalid FLOW_CONTROL: {raw!r} (expected 0/1, none, hardware, or software)."
    )


def _parse_tx_power(raw: str | None) -> int:
    if raw is None:
        return 6
    if not re.fullmatch(r"-?\d+", raw):
        raise ConfigError(
            f"Invalid TX_POWER: {raw!r} (expected integer between -20 and 20 dBm)."
        )
    power = int(raw)
    if not (-20 <= power <= 20):
        raise ConfigError(
            f"Invalid TX_POWER: {power} (expected integer between -20 and 20 dBm)."
        )
    return power


def _parse_network_device(raw: str) -> NetworkTransport:
    bracket_match = re.fullmatch(r"\[([^\]]+)\]:(\d+)", raw)
    if bracket_match:
        host, port_str = bracket_match.group(1), bracket_match.group(2)
        if not host or "%" in host:
            raise ConfigError(
                f"Invalid NETWORK_DEVICE: {raw!r} (bracketed host must be a plain IP address)."
            )
        try:
            ipaddress.ip_address(host)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid NETWORK_DEVICE: {raw!r} (bracketed host must be an IP address)."
            ) from exc
        return NetworkTransport(host=host, port=_parse_port("NETWORK_DEVICE", port_str))

    if raw.count(":") != 1:
        raise ConfigError(
            f"Invalid NETWORK_DEVICE: {raw!r} (expected host:port or [ipv6]:port)."
        )
    host, port_str = raw.rsplit(":", 1)
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ConfigError(
            f"Invalid NETWORK_DEVICE host: {host!r} (allowed characters: letters, digits, '.', '-')."
        )
    return NetworkTransport(host=host, port=_parse_port("NETWORK_DEVICE", port_str))


def _parse_serial_device(raw: str, existing_paths: frozenset[Path]) -> SerialTransport:
    dev_path = Path(raw)
    if not dev_path.is_absolute():
        raise ConfigError(f"DEVICE must be an absolute path: {raw!r}")
    if any(ch.isspace() or ch in "?#" for ch in raw):
        raise ConfigError(
            f"DEVICE contains invalid characters (whitespace, '?', or '#'): {raw!r}"
        )
    if dev_path not in existing_paths:
        raise ConfigError(f"DEVICE path does not exist: {dev_path}")
    return SerialTransport(device=dev_path)


def _plan_storage(host: HostProbe, warnings: list[str]) -> StorageAction:
    var_lib_thread = Path("/var/lib/thread")
    data_thread = Path("/data/thread")
    data_root = Path("/data")

    has_var_lib = var_lib_thread in host.mountpoints
    has_data = bool({data_thread, data_root} & host.mountpoints)

    if has_var_lib and has_data:
        raise ConfigError(
            "Both /var/lib/thread and /data (or /data/thread) are mounted; "
            "mount only one Thread data volume."
        )

    if has_var_lib:
        return StorageAction.USE_VAR_LIB_DIRECTLY

    if host.var_lib_thread_state is VarLibState.NONEMPTY_DIR:
        raise ConfigError(
            "/var/lib/thread is a non-empty unmounted directory; refusing to overwrite existing data."
        )
    if host.var_lib_thread_state is VarLibState.OTHER_FILE:
        raise ConfigError("/var/lib/thread exists and is not a directory or symlink.")

    if not has_data:
        warnings.append(
            "No persistent volume mounted at /data/thread, /data, or /var/lib/thread; "
            "Thread dataset will be lost when the container is recreated."
        )
    return StorageAction.SYMLINK_DATA_THREAD


def parse_mountpoints(mountinfo_text: str) -> frozenset[Path]:
    """Parse mountpoint paths (field 5) from Linux /proc/self/mountinfo."""
    mountpoints: set[Path] = set()
    for line in mountinfo_text.splitlines():
        if not line.strip():
            continue
        fields = line.split(" ")
        if len(fields) < 5:
            continue
        raw_path = fields[4]
        unescaped = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), raw_path)
        mountpoints.add(Path(unescaped))
    return frozenset(mountpoints)


def parse_config(env: Mapping[str, str], host: HostProbe) -> OtbrConfig:
    """Validate environment variables and host probe into a typed OtbrConfig."""
    warnings: list[str] = []

    for typo_var, canonical_var in _TYPO_VARS:
        if _get_env(env, typo_var) is not None and _get_env(env, canonical_var) is None:
            warnings.append(
                f"{typo_var} is set and ignored; use {canonical_var} instead."
            )

    beta = _parse_bool("BETA", _get_env(env, "BETA"), default=False)
    channel = Channel.BETA if beta else Channel.STABLE
    if channel not in host.available_channels:
        raise ConfigError(
            f"Selected channel {channel.value!r} binary is missing or not executable: "
            f"{channel.prefix / 'sbin' / 'otbr-agent'}"
        )

    network_raw = _get_env(env, "NETWORK_DEVICE")
    device_raw = _get_env(env, "DEVICE")
    transport: RadioTransport
    if network_raw is not None:
        transport = _parse_network_device(network_raw)
        if device_raw is not None:
            warnings.append(
                f"Both DEVICE ({device_raw}) and NETWORK_DEVICE ({network_raw}) are set; "
                "using NETWORK_DEVICE via /tmp/ttyOTBR."
            )
    elif device_raw is not None:
        transport = _parse_serial_device(device_raw, host.existing_paths)
    else:
        raise ConfigError("Either DEVICE or NETWORK_DEVICE must be set.")

    baudrate = _parse_baudrate(_get_env(env, "BAUDRATE"))
    default_flow_control = (
        FlowControl.NONE if isinstance(transport, NetworkTransport) else FlowControl.SOFTWARE
    )
    flow_control = _parse_flow_control(_get_env(env, "FLOW_CONTROL"), default=default_flow_control)
    if isinstance(transport, NetworkTransport) and flow_control is FlowControl.SOFTWARE:
        warnings.append(
            "FLOW_CONTROL=software with NETWORK_DEVICE applies XON/XOFF only to the local "
            "/tmp/ttyOTBR PTY; TCP already provides flow control and remote serial bridges "
            "may not forward XON/XOFF. Use FLOW_CONTROL=none unless your TCP bridge "
            "transparently passes XON/XOFF to a software-flow-control RCP."
        )
    tx_power = _parse_tx_power(_get_env(env, "TX_POWER"))

    backbone_if = _get_env(env, "BACKBONE_IF") or host.default_route_interface
    if not backbone_if:
        raise ConfigError(
            "BACKBONE_IF is unset and no default route interface was detected."
        )
    if backbone_if not in host.net_interfaces:
        ifaces = ", ".join(sorted(host.net_interfaces)) or "(none)"
        raise ConfigError(
            f"BACKBONE_IF {backbone_if!r} does not exist (available: {ifaces})."
        )

    firewall = _parse_bool("FIREWALL", _get_env(env, "FIREWALL"), default=True)
    nat64 = _parse_bool("NAT64", _get_env(env, "NAT64"), default=False)
    log_level = _parse_log_level(_get_env(env, "OTBR_LOG_LEVEL"))

    rest_addr = _parse_ip_address("REST_LISTEN_ADDRESS", _get_env(env, "REST_LISTEN_ADDRESS"), "::")
    rest_port_raw = _get_env(env, "REST_LISTEN_PORT")
    rest_port = _parse_port("REST_LISTEN_PORT", rest_port_raw) if rest_port_raw else 8081

    web_listen_address = _parse_ip_address(
        "WEB_LISTEN_ADDRESS", _get_env(env, "WEB_LISTEN_ADDRESS"), "::"
    )
    web_port_raw = _get_env(env, "WEB_PORT")
    web_port = _parse_port("WEB_PORT", web_port_raw) if web_port_raw else None
    if web_port is not None and web_port == rest_port:
        raise ConfigError(
            f"WEB_PORT ({web_port}) cannot equal REST_LISTEN_PORT ({rest_port})."
        )

    storage = _plan_storage(host, warnings)

    if not host.ipv6_forwarding_enabled:
        warnings.append(
            "IPv6 forwarding (/proc/sys/net/ipv6/conf/all/forwarding) is not 1; "
            "Thread Border Routing will not forward IPv6 traffic."
        )

    return OtbrConfig(
        channel=channel,
        transport=transport,
        baudrate=baudrate,
        flow_control=flow_control,
        tx_power=tx_power,
        backbone_if=backbone_if,
        thread_if="wpan0",
        firewall=firewall,
        nat64=nat64,
        log_level=log_level,
        rest_api=RestApiConfig(listen_address=rest_addr, port=rest_port),
        web_listen_address=web_listen_address,
        web_port=web_port,
        storage=storage,
        warnings=tuple(warnings),
    )


def render_config_env(cfg: OtbrConfig) -> str:
    """Render shlex-quoted OTBR_* shell variables for /run/otbr/config.env."""
    network_device = (
        cfg.transport.socat_target if isinstance(cfg.transport, NetworkTransport) else ""
    )
    pairs = [
        ("OTBR_CHANNEL", cfg.channel.value),
        ("OTBR_THREAD_IF", cfg.thread_if),
        ("OTBR_BACKBONE_IF", cfg.backbone_if),
        ("OTBR_DEVICE", str(cfg.transport.device_path)),
        ("OTBR_NETWORK_DEVICE", network_device),
        ("OTBR_BAUDRATE", str(cfg.baudrate)),
        ("OTBR_FLOW_CONTROL", cfg.flow_control.value),
        ("OTBR_UART_FLOW_PARAM", cfg.flow_control.uart_query_param),
        ("OTBR_TX_POWER", str(cfg.tx_power)),
        ("OTBR_FIREWALL", "1" if cfg.firewall else "0"),
        ("OTBR_NAT64", "1" if cfg.nat64 else "0"),
        ("OTBR_LOG_LEVEL", cfg.log_level.name.lower()),
        ("OTBR_LOG_LEVEL_INT", str(cfg.log_level.value)),
        ("OTBR_REST_LISTEN_ADDRESS", cfg.rest_api.listen_address),
        ("OTBR_REST_LISTEN_PORT", str(cfg.rest_api.port)),
        ("OTBR_REST_CHECK_HOST", cfg.rest_api.probe_host),
        ("OTBR_WEB_LISTEN_ADDRESS", cfg.web_listen_address),
        ("OTBR_WEB_PORT", str(cfg.web_port) if cfg.web_port is not None else ""),
        ("OTBR_DATA_DIR", str(cfg.storage.data_dir)),
    ]
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in pairs)
