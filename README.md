# Home Assistant OpenThread Border Router for Docker Compose

Run the official Home Assistant [OpenThread Border Router app](https://github.com/home-assistant/app-openthread-border-router) in standalone Docker Compose on a Raspberry Pi (`linux/arm64`) or x86 server (`linux/amd64`) without Home Assistant OS.

It runs the same OpenThread setup, radio recovery, and dataset migration as the Home Assistant OS app, configured through environment variables and ready to connect to Home Assistant's OpenThread Border Router integration on port `8081`.

## Quick start

1. Configure persistent IPv6 forwarding and Router Advertisement settings on the Docker host. Replace `<BACKBONE_IF>` with your LAN interface name, such as `eth0`:

```bash
sudo tee /etc/sysctl.d/60-otbr.conf >/dev/null <<'EOF'
net.ipv6.conf.all.forwarding=1
net.ipv6.conf.<BACKBONE_IF>.accept_ra=2
net.ipv6.conf.<BACKBONE_IF>.accept_ra_rt_info_max_plen=64
EOF
sudo sysctl --system
```

2. Add the `otbr` service to your `compose.yaml` (or copy [compose.example.yaml](compose.example.yaml)) and set your serial adapter path and `BACKBONE_IF`:

```yaml
services:
  otbr:
    container_name: otbr
    image: ghcr.io/smnws/ha-otbr-docker:latest
    network_mode: host
    cap_add:
      - NET_ADMIN
      - NET_RAW
      - IPC_LOCK
    environment:
      DEVICE: "/dev/ttyACM0"
      BACKBONE_IF: "eth0"
      BAUDRATE: "460800"
      FLOW_CONTROL: "1"
      FIREWALL: "1"
      NAT64: "0"
      OTBR_LOG_LEVEL: "notice"
      BETA: "0"
    devices:
      - /dev/serial/by-id/usb-YOUR_ADAPTER_ID-if00-port0:/dev/ttyACM0
      - /dev/net/tun:/dev/net/tun
    volumes:
      - ./otbr_data:/data/thread
    restart: unless-stopped
```

3. Start the container:

```bash
docker compose up -d otbr
```

Mount one persistent volume at `/data/thread`, `/data`, or `/var/lib/thread`. Startup fails if you mount `/var/lib/thread` together with `/data` or `/data/thread`.

## Security

The container runs with `network_mode: host`. With the default `REST_LISTEN_ADDRESS="::"`, `otbr-agent` binds its unauthenticated REST API on port `8081` across every host interface. Setting `WEB_PORT` with the default `WEB_LISTEN_ADDRESS="::"` exposes the unauthenticated `otbr-web` UI on every host interface as well.

If Home Assistant runs on the same host, set `REST_LISTEN_ADDRESS=127.0.0.1` and `WEB_LISTEN_ADDRESS=127.0.0.1` to bind both services to loopback. If Home Assistant runs on another host, restrict access to those ports with a host firewall or bind them to a management interface IP address.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `DEVICE` | unset | Absolute serial RCP path inside the container (required unless `NETWORK_DEVICE` is set) |
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
| `WEB_LISTEN_ADDRESS` | `::` | IPv4 or IPv6 listen address for `otbr-web` |
| `WEB_PORT` | unset | TCP port (`1..65535`) for `otbr-web`; disabled when unset or empty, and must differ from `REST_LISTEN_PORT` |

## Patching

The image builds `ot-br-posix` from source with zero patches by default and checks `patches/` during Docker builds and CI. See [patches/README.md](patches/README.md) for the directory layout and the steps to add a software flow control patch.

## Upstream synchronization

A weekly workflow (`.github/workflows/sync-upstream.yaml`) updates the `upstream` submodule, syncs the pinned versions in `Dockerfile`, and opens a `sync/upstream` pull request when upstream changes.

To let the workflow open pull requests, enable **Allow GitHub Actions to create and approve pull requests** in the repository's Actions settings. Pull requests opened with the default `GITHUB_TOKEN` do not trigger `pull_request` CI workflows automatically. Close and reopen the pull request, or push a commit, to trigger CI checks.

To verify locally that `Dockerfile`, `build/build-otbr.sh`, and `upstream-rootfs.sha256` match the `upstream` submodule, run:

```bash
python3 scripts/upstream_pins.py check
```

If upstream modified `upstream/openthread_border_router/rootfs/` and you ported the changes into `rootfs/`, update the manifest:

```bash
python3 scripts/upstream_pins.py accept-rootfs
```

## Troubleshooting

- **Container exits right after startup.** Run `docker logs otbr`. Invalid environment variables, missing device paths, or unmounted `/var/lib/thread` conflicts fail stage2 initialization before touching `ip6tables`.
- **Border routing drops IPv6 packets.** Verify that `/etc/sysctl.d/60-otbr.conf` is applied (`net.ipv6.conf.all.forwarding=1` and `accept_ra=2` on `BACKBONE_IF`) and that `BACKBONE_IF` matches your host LAN interface.
- **Firewall rules fail or do not apply on the host.** Debian Trixie in this image uses the `iptables-nft` backend by default. If your host runs `iptables-legacy` rules or custom `nftables` chains that drop forwarded traffic before the `ip6tables` hooks, align the host firewall rules or verify that `ipset` and `ip6tables` tables are visible from the host.
- **Serial device changes path across reboots.** Map a `/dev/serial/by-id/...` path to `/dev/ttyACM0` under `devices` instead of passing `/dev/ttyACM0` directly from the host.
- **`FLOW_CONTROL=software` fails at startup.** Stock `ot-br-posix` builds in this repo do not include a software flow control patch. Follow [patches/README.md](patches/README.md) to add it.
