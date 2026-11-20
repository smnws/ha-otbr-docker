#!/usr/bin/env python3
"""Synchronize and verify upstream OpenThread Border Router pins, packages, CMake flags, and rootfs."""

from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import subprocess
import sys
from pathlib import Path

PIN_START_MARKER = "# BEGIN UPSTREAM-PINS"
PIN_END_MARKER = "# END UPSTREAM-PINS"
PIN_NAMES = (
    "BUILD_FROM",
    "OTBR_BETA_VERSION",
    "OTBR_VERSION",
    "SERIALX_VERSION",
    "UNIVERSAL_SILABS_FLASHER_VERSION",
)
ROOTFS_EXCLUDED = frozenset({"usr/local/bin/migrate_otbr_settings.py"})
OT_BR_POSIX_REPO = "https://github.com/openthread/ot-br-posix.git"


def _strip_line_continuations(text: str) -> list[str]:
    logical_lines: list[str] = []
    buf: list[str] = []
    continuing = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            if not continuing:
                logical_lines.append(raw_line)
            continue

        has_cont = raw_line.rstrip().endswith("\\")
        part = raw_line.rstrip()[:-1] if has_cont else raw_line
        buf.append(part)

        if has_cont:
            continuing = True
        else:
            logical_lines.append(" ".join(buf))
            buf.clear()
            continuing = False

    if buf:
        logical_lines.append(" ".join(buf))
    return logical_lines


def _split_stages(dockerfile_text: str) -> list[tuple[str | None, str]]:
    stages: list[tuple[str | None, list[str]]] = []
    current_alias: str | None = None
    current_lines: list[str] = []

    for line in dockerfile_text.splitlines():
        m = re.match(r"^\s*FROM\s+\S+(?:\s+AS\s+(\S+))?\s*$", line, re.IGNORECASE)
        if m:
            if current_lines:
                stages.append((current_alias, current_lines))
            current_alias = m.group(1)
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        stages.append((current_alias, current_lines))

    return [(alias, "\n".join(lines)) for alias, lines in stages]


def extract_pin_args(dockerfile_text: str) -> dict[str, str]:
    """Extract assigned ARG values for the tracked upstream pins."""
    pins: dict[str, str] = {}
    for line in dockerfile_text.splitlines():
        m = re.match(r"^\s*ARG\s+([A-Z0-9_]+)=(.+?)\s*$", line)
        if m and m.group(1) in PIN_NAMES:
            pins[m.group(1)] = m.group(2)
    return pins


def _tokenize_commands(text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for line in _strip_line_continuations(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        stripped = re.sub(r"^(?:RUN|ENV|ARG|COPY|FROM)\s+", "", stripped)
        try:
            tokens = shlex.split(stripped, comments=True)
        except ValueError:
            continue
        current: list[str] = []
        for tok in tokens:
            if tok in ("&&", "||", ";", "|"):
                if current:
                    commands.append(current)
                    current = []
            else:
                current.append(tok)
        if current:
            commands.append(current)
    return commands


def extract_apt_packages(stage_text: str) -> tuple[str, ...]:
    """Extract sorted apt-get install package names from a Dockerfile stage."""
    packages: set[str] = set()
    for cmd in _tokenize_commands(stage_text):
        for idx in range(len(cmd) - 1):
            if cmd[idx] in ("apt-get", "apt") and cmd[idx + 1] == "install":
                for tok in cmd[idx + 2 :]:
                    if tok.startswith("-"):
                        continue
                    packages.add(tok)
                break
    return tuple(sorted(packages))


def extract_pip_packages(stage_text: str) -> tuple[str, ...]:
    """Extract sorted pip package names (without version specifiers) from a stage."""
    packages: set[str] = set()
    for cmd in _tokenize_commands(stage_text):
        for idx in range(len(cmd) - 1):
            if cmd[idx] in ("pip", "pip3") and cmd[idx + 1] == "install":
                for tok in cmd[idx + 2 :]:
                    if tok.startswith("-"):
                        continue
                    name = re.split(r"[=<>!~]", tok, maxsplit=1)[0].strip()
                    if name:
                        packages.add(name)
                break
    return tuple(sorted(packages))


def extract_cmake_flags(text: str) -> tuple[str, ...]:
    """Extract sorted normalized cmake -D flags, excluding -DCMAKE_INSTALL_PREFIX."""
    flags: set[str] = set()
    for cmd in _tokenize_commands(text):
        if "cmake" not in cmd:
            continue
        cmake_idx = cmd.index("cmake")
        for tok in cmd[cmake_idx + 1 :]:
            if tok.startswith("-D") and not tok.startswith("-DCMAKE_INSTALL_PREFIX="):
                flags.add(tok)
    return tuple(sorted(flags))


def compute_rootfs_manifest(rootfs_dir: Path) -> dict[str, str]:
    """Compute SHA-256 hex digests for upstream rootfs files, excluding directly copied files."""
    if not rootfs_dir.is_dir():
        raise FileNotFoundError(f"Upstream rootfs directory not found: {rootfs_dir}")

    manifest: dict[str, str] = {}
    for path in sorted(rootfs_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(rootfs_dir).as_posix()
        if rel in ROOTFS_EXCLUDED:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[rel] = digest
    return manifest


def format_manifest(manifest: dict[str, str]) -> str:
    """Format a SHA-256 manifest in deterministic sha256sum order."""
    return "".join(f"{digest}  {rel}\n" for rel, digest in sorted(manifest.items()))


def parse_manifest(text: str) -> dict[str, str]:
    """Parse a sha256sum-formatted manifest file."""
    manifest: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"Malformed manifest line {lineno}: {line!r}")
        manifest[parts[1]] = parts[0]
    return manifest


def _extract_upstream_facts(
    upstream_dockerfile: Path,
) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    text = upstream_dockerfile.read_text(encoding="utf-8")
    pins = extract_pin_args(text)
    missing = [name for name in PIN_NAMES if name not in pins]
    if missing:
        raise ValueError(f"Missing expected ARGs in {upstream_dockerfile}: {missing}")

    stages = _split_stages(text)
    builder_stages = [body for alias, body in stages if alias and "builder" in alias]
    if not builder_stages:
        raise ValueError(f"No builder stage found in {upstream_dockerfile}")
    runtime_stage = stages[-1][1]

    builder_apts = extract_apt_packages(builder_stages[0])
    for extra_builder in builder_stages[1:]:
        extra_apts = extract_apt_packages(extra_builder)
        if extra_apts != builder_apts:
            raise ValueError(
                f"Upstream builder stages disagree on apt packages: {builder_apts} vs {extra_apts}"
            )

    cmake_flags = extract_cmake_flags(builder_stages[0])
    for extra_builder in builder_stages[1:]:
        extra_flags = extract_cmake_flags(extra_builder)
        if extra_flags != cmake_flags:
            raise ValueError(
                f"Upstream builder stages disagree on CMake flags: {cmake_flags} vs {extra_flags}"
            )

    runtime_apts = extract_apt_packages(runtime_stage)
    runtime_pips = extract_pip_packages(runtime_stage)
    return pins, builder_apts, runtime_apts, runtime_pips, cmake_flags


def _repo_paths(repo_root: Path) -> tuple[Path, Path, Path, Path, Path]:
    upstream_dir = repo_root / "upstream" / "openthread_border_router"
    return (
        upstream_dir / "Dockerfile",
        upstream_dir / "rootfs",
        repo_root / "Dockerfile",
        repo_root / "build" / "build-otbr.sh",
        repo_root / "upstream-rootfs.sha256",
    )


def cmd_sync(repo_root: Path) -> int:
    upstream_df, _, local_df, _, _ = _repo_paths(repo_root)
    pins, *_ = _extract_upstream_facts(upstream_df)

    text = local_df.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"({re.escape(PIN_START_MARKER)}\n)(.*?)(\n{re.escape(PIN_END_MARKER)})",
        re.DOTALL,
    )
    if not pattern.search(text):
        print(
            f"ERROR: Missing {PIN_START_MARKER} ... {PIN_END_MARKER} block in {local_df}",
            file=sys.stderr,
        )
        return 1

    new_block = "\n".join(f"ARG {name}={pins[name]}" for name in PIN_NAMES)
    updated = pattern.sub(rf"\g<1>{new_block}\g<3>", text, count=1)
    if updated != text:
        local_df.write_text(updated, encoding="utf-8")
        print(f"Updated {local_df} upstream pins.")
    else:
        print(f"{local_df} upstream pins already up to date.")
    return 0


def _diff_items(label: str, expected: tuple[str, ...], actual: tuple[str, ...]) -> list[str]:
    exp_set = set(expected)
    act_set = set(actual)
    if exp_set == act_set:
        return []
    lines = [f"{label} mismatch:"]
    for item in sorted(exp_set - act_set):
        lines.append(f"  - missing from local: {item}")
    for item in sorted(act_set - exp_set):
        lines.append(f"  + extra in local:     {item}")
    return lines


def cmd_check(repo_root: Path) -> int:
    upstream_df, upstream_rootfs, local_df, build_script, manifest_path = _repo_paths(repo_root)

    upstream_pins, u_builder_apts, u_runtime_apts, u_runtime_pips, u_cmake = (
        _extract_upstream_facts(upstream_df)
    )

    local_text = local_df.read_text(encoding="utf-8")
    local_pins = extract_pin_args(local_text)

    local_stages = _split_stages(local_text)
    builder_base = next(
        (body for alias, body in local_stages if alias == "otbr-builder-base"),
        None,
    )
    runtime_stage = next(
        (body for alias, body in local_stages if alias == "runtime"),
        local_stages[-1][1] if local_stages else "",
    )
    if builder_base is None:
        print(f"ERROR: Missing otbr-builder-base stage in {local_df}", file=sys.stderr)
        return 1

    l_builder_apts = extract_apt_packages(builder_base)
    l_runtime_apts = extract_apt_packages(runtime_stage)
    l_runtime_pips = extract_pip_packages(runtime_stage)
    l_cmake = extract_cmake_flags(build_script.read_text(encoding="utf-8"))

    errors: list[str] = []

    for name in PIN_NAMES:
        u_val = upstream_pins.get(name)
        l_val = local_pins.get(name)
        if u_val != l_val:
            errors.append(f"ARG {name} mismatch:\n  upstream: {u_val}\n  local:    {l_val}")

    errors.extend(_diff_items("Builder apt packages", u_builder_apts, l_builder_apts))
    errors.extend(_diff_items("Runtime apt packages", u_runtime_apts, l_runtime_apts))
    errors.extend(_diff_items("Runtime pip packages", u_runtime_pips, l_runtime_pips))
    errors.extend(_diff_items("CMake -D flags", u_cmake, l_cmake))

    expected_manifest = compute_rootfs_manifest(upstream_rootfs)
    if not manifest_path.is_file():
        errors.append(f"Missing rootfs manifest: {manifest_path}")
    else:
        recorded_manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
        if expected_manifest != recorded_manifest:
            errors.append("Upstream rootfs manifest drift (run accept-rootfs after porting):")
            all_keys = sorted(set(expected_manifest) | set(recorded_manifest))
            for key in all_keys:
                e_hash = expected_manifest.get(key)
                r_hash = recorded_manifest.get(key)
                if e_hash != r_hash:
                    if r_hash is None:
                        errors.append(f"  + added upstream:    {key}")
                    elif e_hash is None:
                        errors.append(f"  - removed upstream:  {key}")
                    else:
                        errors.append(f"  ~ modified upstream: {key}")

    if errors:
        print("Upstream drift check FAILED:", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
        return 1

    print("Upstream drift check PASSED: pins, apt, pip, CMake flags, and rootfs manifest match.")
    return 0


def cmd_accept_rootfs(repo_root: Path) -> int:
    _, upstream_rootfs, _, _, manifest_path = _repo_paths(repo_root)
    manifest = compute_rootfs_manifest(upstream_rootfs)
    manifest_path.write_text(format_manifest(manifest), encoding="utf-8")
    print(f"Wrote {len(manifest)} entries to {manifest_path}.")
    return 0


def cmd_rootfs_diff(repo_root: Path, base: str | None) -> int:
    upstream_repo = repo_root / "upstream"
    if base is None:
        head_tree = subprocess.run(
            ["git", "-C", str(repo_root), "ls-tree", "HEAD", "upstream"],
            capture_output=True,
            text=True,
            check=False,
        )
        current_head = subprocess.run(
            ["git", "-C", str(upstream_repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        recorded_sha = ""
        if head_tree.returncode == 0 and head_tree.stdout.strip():
            parts = head_tree.stdout.strip().split()
            if len(parts) >= 3:
                recorded_sha = parts[2]

        if recorded_sha and recorded_sha != current_head:
            base = recorded_sha
        else:
            base = "HEAD~1"

    res = subprocess.run(
        [
            "git",
            "-C",
            str(upstream_repo),
            "diff",
            base,
            "HEAD",
            "--",
            "openthread_border_router/rootfs",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        return res.returncode
    sys.stdout.write(res.stdout)
    return 0


def _list_patch_files(patches_dir: Path) -> list[Path]:
    found: list[Path] = []
    for sub in ("ot-br-posix", "openthread"):
        sub_dir = patches_dir / sub
        if sub_dir.is_dir():
            found.extend(sorted(sub_dir.glob("*.patch")))
    return found


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def cmd_patch_check(repo_root: Path) -> int:
    patches_dir = repo_root / "patches"
    patches = _list_patch_files(patches_dir)
    if not patches:
        print("No *.patch files under patches/; patch-check skipped cleanly.")
        return 0

    local_df = repo_root / "Dockerfile"
    pins = extract_pin_args(local_df.read_text(encoding="utf-8"))
    commits = [
        ("stable", pins["OTBR_VERSION"]),
        ("beta", pins["OTBR_BETA_VERSION"]),
    ]

    cache_dir = repo_root / ".cache" / "ot-br-posix"
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    if not (cache_dir / ".git").exists():
        _run(["git", "clone", "--depth", "1", OT_BR_POSIX_REPO, str(cache_dir)])

    apply_script = repo_root / "build" / "apply-patches.sh"
    for channel, commit in commits:
        print(f"Checking patches against {channel} ({commit})...")
        _run(["git", "-C", str(cache_dir), "fetch", "--depth", "1", "origin", commit])
        _run(["git", "-C", str(cache_dir), "checkout", "--force", commit])
        _run(["git", "-C", str(cache_dir), "reset", "--hard", commit])
        _run(
            [
                "git",
                "-C",
                str(cache_dir),
                "submodule",
                "update",
                "--init",
                "--depth",
                "1",
                "third_party/openthread/repo",
            ]
        )
        _run(["git", "-C", str(cache_dir / "third_party/openthread/repo"), "reset", "--hard"])
        _run(
            [
                str(apply_script),
                "--check-only",
                "--src-dir",
                str(cache_dir),
                "--patches-dir",
                str(patches_dir),
            ]
        )

    print("Patch check PASSED for stable and beta.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root directory",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="Update Dockerfile UPSTREAM-PINS block from upstream")
    sub.add_parser("check", help="Verify Dockerfile, build flags, and rootfs manifest against upstream")
    sub.add_parser("accept-rootfs", help="Regenerate upstream-rootfs.sha256 from upstream")
    diff_p = sub.add_parser("rootfs-diff", help="Print upstream rootfs diff since base commit")
    diff_p.add_argument("--base", default=None, help="Base commit/ref in upstream submodule")
    sub.add_parser("patch-check", help="Dry-run patches/*.patch against pinned stable and beta commits")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()

    if args.command == "sync":
        return cmd_sync(repo_root)
    if args.command == "check":
        return cmd_check(repo_root)
    if args.command == "accept-rootfs":
        return cmd_accept_rootfs(repo_root)
    if args.command == "rootfs-diff":
        return cmd_rootfs_diff(repo_root, args.base)
    if args.command == "patch-check":
        return cmd_patch_check(repo_root)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
