# otbr-ha

Standalone multi-arch (`linux/amd64` and `linux/arm64`) Docker image for OpenThread Border Router built from `home-assistant/app-openthread-border-router`. It tracks upstream pins, CMake flags, and runtime packages without requiring Home Assistant Supervisor or `bashio`.

## Quick start

1. Ensure IPv6 forwarding is enabled on the Docker host:

```bash
sysctl net.ipv6.conf.all.forwarding
```

If the output is `0`, enable forwarding:

```bash
sudo sysctl -w net.ipv6.conf.all.forwarding=1
```

2. Authenticate to GitHub Container Registry if the package is private:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u smnws --password-stdin
```

3. Copy [compose.example.yaml](compose.example.yaml) to `compose.yaml`, adjust `DEVICE` and `BACKBONE_IF` for your host, and start the container:

```bash
docker compose up -d
```

Mount one persistent volume at `/data/thread` (or `/data`, or `/var/lib/thread`). Mounting both `/var/lib/thread` and `/data/thread` fails startup by design.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `DEVICE` | unset | Serial RCP path inside the container (required unless `NETWORK_DEVICE` is set) |
| `NETWORK_DEVICE` | unset | TCP RCP endpoint as `host:port` or `[ipv6]:port`; overrides `DEVICE` and starts `socat` on `/tmp/ttyOTBR` |
| `BACKBONE_IF` | default route interface | Host network interface used for Thread border routing and TREL |
| `BAUDRATE` | `460800` | Serial baudrate (`57600`, `115200`, `230400`, `460800`, `921600`, or `1000000`) |
| `FLOW_CONTROL` | `1` | `0` (`none`) or `1` (`hardware`); `software` requires adding an OpenThread HDLC patch |
| `FIREWALL` | `1` | `1` to enforce `ip6tables` and `ipset` Thread ingress filtering, `0` to allow forwarding |
| `NAT64` | `0` | `1` to enable OpenThread NAT64, upstream DNS, and `iptables` masquerading |
| `OTBR_LOG_LEVEL` | `notice` | `emergency`, `alert`, `critical`, `error`, `warning`, `notice`, `info`, or `debug` |
| `BETA` | `0` | `0` runs `/opt/otbr-stable`; `1` runs `/opt/otbr-beta` |
| `REST_LISTEN_ADDRESS` | `::` | IPv4 or IPv6 listen address for the OTBR REST API |
| `REST_LISTEN_PORT` | `8081` | TCP port (`1..65535`) for the OTBR REST API |
| `WEB_PORT` | unset | TCP port (`1..65535`) for `otbr-web`; disabled when unset or empty, and must differ from `REST_LISTEN_PORT` |

## Patching

The image builds `ot-br-posix` from source with zero patches by default and checks `patches/` during both Docker builds and CI. See [patches/README.md](patches/README.md) for the directory layout and the steps to add a software flow control patch.

## Upstream synchronization

A weekly workflow (`.github/workflows/sync-upstream.yaml`) updates the `upstream` submodule, syncs the pinned versions in `Dockerfile`, and opens a `sync/upstream` pull request when upstream changes.

To verify locally that `Dockerfile`, `build/build-otbr.sh`, and `upstream-rootfs.sha256` match the `upstream` submodule:

```bash
python3 scripts/upstream_pins.py check
```

If upstream modified `upstream/openthread_border_router/rootfs/` and you ported the changes into `rootfs/`, update the manifest:

```bash
python3 scripts/upstream_pins.py accept-rootfs
```

## Troubleshooting

- **Container exits right after startup.** Run `docker logs otbr`. Invalid environment variables or missing device paths fail stage2 initialization before touching `ip6tables`.
- **Border routing drops IPv6 packets.** Check that `/proc/sys/net/ipv6/conf/all/forwarding` on the host is `1` and that `BACKBONE_IF` matches your host LAN interface.
- **Serial device changes path across reboots.** Map a `/dev/serial/by-id/...` path to `/dev/ttyACM0` under `devices` instead of passing `/dev/ttyACM0` directly from the host.
- **`FLOW_CONTROL=software` fails at startup.** Stock `ot-br-posix` builds in this repo do not include a software flow control patch. Follow [patches/README.md](patches/README.md) to add it.
