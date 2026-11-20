# Patches

Add `*.patch` files here to customize the `ot-br-posix` build or its `openthread` submodule without forking upstream.

## Directory layout

- `patches/ot-br-posix/`: patches applied against `/usr/src/ot-br-posix`
- `patches/openthread/`: patches applied against `/usr/src/ot-br-posix/third_party/openthread/repo`

`build/apply-patches.sh` sorts `*.patch` files in each directory using `LC_ALL=C`, checks each patch with `git apply --check`, applies it, and writes `BUILD_INFO` inside `/opt/otbr-stable` and `/opt/otbr-beta` with the applied patch names and SHA-256 digests.

## Included patches

- `patches/openthread/0001-posix-hdlc-software-flow-control.patch` extends OpenThread POSIX HDLC `uart-flow-control` to accept bare (`hardware`), `hardware`, `software`, and `none`, and rejects duplicate or invalid values. It clears `IXON`, `IXOFF`, `IXANY`, and `CRTSCTS` before setting the requested mode. For `software`, it enables `IXON` and `IXOFF` with `VSTART=0x11` and `VSTOP=0x13` and calls `tcflow(TCOON)`. It runs `uart-init-deassert` (`TIOCMBIC`) after `tcsetattr` and ignores `ENOTTY`, `EINVAL`, and `EOPNOTSUPP` on modemless PTYs.

Verify that all patches apply cleanly against both pinned `OTBR_VERSION` and `OTBR_BETA_VERSION` commits:

```bash
python3 scripts/upstream_pins.py patch-check
```

## Patch rules

- Generate patches with `git diff` from inside the target repository root so `a/` and `b/` paths match that repository.
- Prefix filenames with a two-digit or four-digit sequence number (for example, `0001-description.patch`) so order stays deterministic.
- Never place a patch for `third_party/openthread/repo` in `patches/ot-br-posix/`, because `git apply` cannot cross submodule boundaries.
