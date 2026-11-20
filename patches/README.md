# Patches

Add `*.patch` files here to customize the `ot-br-posix` build or its `openthread` submodule without forking upstream.

## Directory layout

- `patches/ot-br-posix/`: patches applied against `/usr/src/ot-br-posix`
- `patches/openthread/`: patches applied against `/usr/src/ot-br-posix/third_party/openthread/repo`

`build/apply-patches.sh` sorts `*.patch` files in each directory using `LC_ALL=C`, checks each patch with `git apply --check`, applies it, and writes `BUILD_INFO` inside `/opt/otbr-stable` and `/opt/otbr-beta` with the applied patch names and SHA-256 digests.

## Adding software flow control

Software flow control (`FLOW_CONTROL=software`) requires patching OpenThread POSIX HDLC transport first.

1. Generate a patch against the OpenThread submodule (`third_party/openthread/repo/src/posix/platform/hdlc_interface.cpp`) with paths relative to `third_party/openthread/repo`, and save it as `patches/openthread/0001-software-flow-control.patch`.
2. Enable `FlowControl.SOFTWARE` in `rootfs/usr/local/lib/otbr/config.py` so `FLOW_CONTROL=software` resolves cleanly instead of raising `ConfigError`.
3. Verify the patch applies cleanly against both pinned `OTBR_VERSION` and `OTBR_BETA_VERSION` commits:

```bash
python3 scripts/upstream_pins.py patch-check
```

## Patch rules

- Generate patches with `git diff` from inside the target repository root so `a/` and `b/` paths match that repository.
- Prefix filenames with a two-digit or four-digit sequence number (for example, `0001-description.patch`) so order stays deterministic.
- Never place a patch for `third_party/openthread/repo` in `patches/ot-br-posix/`, because `git apply` cannot cross submodule boundaries.
