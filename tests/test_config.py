from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rootfs/usr/local/lib/otbr"))
import config  # noqa: E402


def make_host(
    *,
    hostname: str = "rpi5",
    net_interfaces: frozenset[str] = frozenset({"lo", "eth0", "wlan0"}),
    default_route_interface: str | None = "eth0",
    ipv6_forwarding_enabled: bool = True,
    mountpoints: frozenset[Path] = frozenset({Path("/"), Path("/data/thread")}),
    existing_paths: frozenset[Path] = frozenset({Path("/dev/ttyACM0")}),
    available_channels: frozenset[config.Channel] = frozenset(
        {config.Channel.STABLE, config.Channel.BETA}
    ),
    var_lib_thread_is_nonempty_dir: bool = False,
) -> config.HostProbe:
    return config.HostProbe(
        hostname=hostname,
        net_interfaces=net_interfaces,
        default_route_interface=default_route_interface,
        ipv6_forwarding_enabled=ipv6_forwarding_enabled,
        mountpoints=mountpoints,
        existing_paths=existing_paths,
        available_channels=available_channels,
        var_lib_thread_is_nonempty_dir=var_lib_thread_is_nonempty_dir,
    )


def test_pi_dropin_compose_config() -> None:
    env = {
        "DEVICE": "/dev/ttyACM0",
        "BACKBONE_IF": "eth0",
        "FLOW_CONTROL": "0",
        "FIREWALL": "1",
        "NAT64": "0",
        "BAUDRATE": "1000000",
        "OTBR_LOG_LEVEL": "error",
        "BETA": "1",
    }
    cfg = config.parse_config(env, make_host())

    assert cfg.channel is config.Channel.BETA
    assert cfg.transport == config.SerialTransport(device=Path("/dev/ttyACM0"))
    assert cfg.baudrate == 1000000
    assert cfg.flow_control is config.FlowControl.NONE
    assert cfg.flow_control.uart_query_param == "&uart-init-deassert"
    assert cfg.flow_control.migrate_flag == "none"
    assert cfg.backbone_if == "eth0"
    assert cfg.thread_if == "wpan0"
    assert cfg.firewall is True
    assert cfg.nat64 is False
    assert cfg.log_level is config.LogLevel.ERROR
    assert cfg.rest_api == config.RestApiConfig(listen_address="::", port=8081)
    assert cfg.rest_api.probe_host == "::1"
    assert cfg.web_port is None
    assert cfg.storage == config.StoragePlan(
        action=config.StorageAction.SYMLINK_DATA_THREAD,
        data_dir=Path("/data/thread"),
        symlink_target=Path("/data/thread"),
        ephemeral_warning=False,
    )
    assert cfg.warnings == ()

    rendered = config.render_config_env(cfg)
    assert "OTBR_CHANNEL=beta\n" in rendered
    assert "OTBR_DEVICE=/dev/ttyACM0\n" in rendered
    assert "OTBR_NETWORK_DEVICE=''\n" in rendered
    assert "OTBR_BAUDRATE=1000000\n" in rendered
    assert "OTBR_FLOW_CONTROL=none\n" in rendered
    assert "OTBR_UART_FLOW_PARAM='&uart-init-deassert'\n" in rendered
    assert "OTBR_LOG_LEVEL_INT=3\n" in rendered


def test_defaults_and_hardware_flow_control() -> None:
    cfg = config.parse_config({"DEVICE": "/dev/ttyACM0"}, make_host())
    assert cfg.channel is config.Channel.STABLE
    assert cfg.baudrate == 460800
    assert cfg.flow_control is config.FlowControl.HARDWARE
    assert cfg.flow_control.uart_query_param == "&uart-flow-control"
    assert cfg.backbone_if == "eth0"
    assert cfg.firewall is True
    assert cfg.nat64 is False
    assert cfg.log_level is config.LogLevel.NOTICE


def test_software_flow_control_rejected_with_patch_guidance() -> None:
    with pytest.raises(config.ConfigError, match=r"patches/openthread/.*FlowControl\.SOFTWARE"):
        config.parse_config({"DEVICE": "/dev/ttyACM0", "FLOW_CONTROL": "software"}, make_host())


@pytest.mark.parametrize(
    ("raw", "expected_target"),
    [
        ("rcp.local:6638", "rcp.local:6638"),
        ("192.168.1.50:6638", "192.168.1.50:6638"),
        ("[fd00::1]:6638", "[fd00::1]:6638"),
    ],
)
def test_network_device_variants(raw: str, expected_target: str) -> None:
    cfg = config.parse_config({"NETWORK_DEVICE": raw}, make_host())
    assert isinstance(cfg.transport, config.NetworkTransport)
    assert cfg.transport.device_path == Path("/tmp/ttyOTBR")
    assert cfg.transport.socat_target == expected_target


def test_network_device_overrides_device_with_warning() -> None:
    cfg = config.parse_config(
        {"DEVICE": "/dev/ttyACM0", "NETWORK_DEVICE": "10.0.0.5:6638"},
        make_host(),
    )
    assert isinstance(cfg.transport, config.NetworkTransport)
    assert any("NETWORK_DEVICE" in w for w in cfg.warnings)


@pytest.mark.parametrize("bad_net", ["fd00::1:6638", "host", "host:0", "host:70000", ":6638"])
def test_invalid_network_device_rejected(bad_net: str) -> None:
    with pytest.raises(config.ConfigError):
        config.parse_config({"NETWORK_DEVICE": bad_net}, make_host())


def test_missing_device_or_missing_path_rejected() -> None:
    with pytest.raises(config.ConfigError, match="Either DEVICE or NETWORK_DEVICE"):
        config.parse_config({}, make_host())
    with pytest.raises(config.ConfigError, match="does not exist"):
        config.parse_config({"DEVICE": "/dev/ttyUSB9"}, make_host())


def test_backbone_interface_validation() -> None:
    with pytest.raises(config.ConfigError, match="no default route"):
        config.parse_config({"DEVICE": "/dev/ttyACM0"}, make_host(default_route_interface=None))
    with pytest.raises(config.ConfigError, match="does not exist"):
        config.parse_config({"DEVICE": "/dev/ttyACM0", "BACKBONE_IF": "eth99"}, make_host())


def test_rest_and_web_port_validation() -> None:
    cfg = config.parse_config(
        {
            "DEVICE": "/dev/ttyACM0",
            "REST_LISTEN_ADDRESS": "0.0.0.0",
            "REST_LISTEN_PORT": "8082",
            "WEB_PORT": "8080",
        },
        make_host(),
    )
    assert cfg.rest_api.probe_host == "127.0.0.1"
    assert cfg.web_port == 8080

    with pytest.raises(config.ConfigError, match="cannot equal REST_LISTEN_PORT"):
        config.parse_config(
            {"DEVICE": "/dev/ttyACM0", "REST_LISTEN_PORT": "8081", "WEB_PORT": "8081"},
            make_host(),
        )


def test_storage_plan_invariants() -> None:
    direct = config.parse_config(
        {"DEVICE": "/dev/ttyACM0"},
        make_host(mountpoints=frozenset({Path("/"), Path("/var/lib/thread")})),
    )
    assert direct.storage.action is config.StorageAction.USE_VAR_LIB_DIRECTLY
    assert direct.storage.symlink_target is None
    assert direct.storage.ephemeral_warning is False

    data_parent = config.parse_config(
        {"DEVICE": "/dev/ttyACM0"},
        make_host(mountpoints=frozenset({Path("/"), Path("/data")})),
    )
    assert data_parent.storage.action is config.StorageAction.SYMLINK_DATA_THREAD
    assert data_parent.storage.ephemeral_warning is False

    ephemeral = config.parse_config(
        {"DEVICE": "/dev/ttyACM0"},
        make_host(mountpoints=frozenset({Path("/")})),
    )
    assert ephemeral.storage.ephemeral_warning is True
    assert any("No persistent volume" in w for w in ephemeral.warnings)

    with pytest.raises(config.ConfigError, match="Both /var/lib/thread and /data/thread"):
        config.parse_config(
            {"DEVICE": "/dev/ttyACM0"},
            make_host(
                mountpoints=frozenset({Path("/var/lib/thread"), Path("/data/thread")})
            ),
        )

    with pytest.raises(config.ConfigError, match="non-empty unmounted directory"):
        config.parse_config(
            {"DEVICE": "/dev/ttyACM0"},
            make_host(
                mountpoints=frozenset({Path("/")}),
                var_lib_thread_is_nonempty_dir=True,
            ),
        )


def test_typo_and_ipv6_forwarding_warnings() -> None:
    cfg = config.parse_config(
        {
            "DEVICE": "/dev/ttyACM0",
            "BACKBONE_INTERFACE": "eth0",
            "WEB_GUI_PORT": "8080",
            "OTBR_REST_PORT": "8081",
        },
        make_host(ipv6_forwarding_enabled=False),
    )
    joined = "\n".join(cfg.warnings)
    assert "use BACKBONE_IF instead" in joined
    assert "use WEB_PORT instead" in joined
    assert "use REST_LISTEN_PORT instead" in joined
    assert "IPv6 forwarding" in joined


def test_parse_mountpoints_unescapes_octal_sequences() -> None:
    mountinfo = (
        "36 35 98:0 / /data/thread\\040with\\011space rw,relatime - ext4 /dev/sda1 rw\n"
        "37 35 98:0 / /var/lib\\134thread rw - ext4 /dev/sda1 rw\n"
    )
    parsed = config.parse_mountpoints(mountinfo)
    assert Path("/data/thread with\tspace") in parsed
    assert Path("/var/lib\\thread") in parsed
