#!/usr/bin/env bash
set -euo pipefail

check_only=0
src_dir="/usr/src/ot-br-posix"
patches_dir="/usr/src/patches"
positional=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check-only)
            check_only=1
            shift
            ;;
        --src-dir)
            src_dir="${2:?--src-dir requires a path}"
            shift 2
            ;;
        --patches-dir)
            patches_dir="${2:?--patches-dir requires a path}"
            shift 2
            ;;
        *)
            positional+=("$1")
            shift
            ;;
    esac
done

if [[ "${check_only}" -eq 0 ]]; then
    if [[ ${#positional[@]} -ne 3 ]]; then
        echo "Usage: apply-patches.sh [--src-dir DIR] [--patches-dir DIR] <channel> <install_prefix> <otbr_commit>" >&2
        exit 2
    fi
    channel="${positional[0]}"
    install_prefix="${positional[1]}"
    otbr_commit="${positional[2]}"
fi

shopt -s nullglob

if [[ -d "${patches_dir}" ]]; then
    patches_dir="$(cd "${patches_dir}" && pwd)"
elif [[ "${patches_dir}" != "/" ]]; then
    patches_dir="${patches_dir%/}"
fi

check_stray_patches() {
    if [[ ! -d "${patches_dir}" ]]; then
        return 0
    fi
    local stray=()
    local p rel
    while IFS= read -r -d '' p; do
        rel="${p#"${patches_dir}/"}"
        case "${rel}" in
            ot-br-posix/*.patch|openthread/*.patch)
                if [[ "${rel}" == */*/* ]]; then
                    stray+=("${rel}")
                fi
                ;;
            *)
                stray+=("${rel}")
                ;;
        esac
    done < <(find "${patches_dir}" -type f -name '*.patch' -print0 | LC_ALL=C sort -z)

    if [[ ${#stray[@]} -gt 0 ]]; then
        echo "ERROR: Found stray *.patch file(s) outside patches/ot-br-posix/ and patches/openthread/:" >&2
        printf '  %s\n' "${stray[@]}" >&2
        exit 1
    fi
}

check_stray_patches

applied_lines=()

apply_repo_patches() {
    local repo_name="$1"
    local target_dir="$2"
    local patch_subdir="${patches_dir}/${repo_name}"

    if [[ ! -d "${patch_subdir}" ]]; then
        return 0
    fi

    local raw_patches=("${patch_subdir}"/*.patch)
    if [[ ${#raw_patches[@]} -eq 0 ]]; then
        return 0
    fi

    local sorted_patches=()
    mapfile -t sorted_patches < <(LC_ALL=C printf '%s\n' "${raw_patches[@]}" | LC_ALL=C sort)

    local -A slot_seen=()
    local -A slot_files=()
    local slots=()
    local patch base slot
    for patch in "${sorted_patches[@]}"; do
        base="$(basename "${patch}" .patch)"
        slot="${base%%@*}"
        if [[ -z "${slot_seen[${slot}]:-}" ]]; then
            slot_seen["${slot}"]=1
            slots+=("${slot}")
            slot_files["${slot}"]="${patch}"
        else
            slot_files["${slot}"]+=$'\n'"${patch}"
        fi
    done

    local selected_patches=()
    local candidates=()
    local candidate matches=()
    for slot in "${slots[@]}"; do
        mapfile -t candidates <<< "${slot_files[${slot}]}"
        if [[ ${#candidates[@]} -eq 1 ]]; then
            selected_patches+=("${candidates[0]}")
            continue
        fi

        matches=()
        for candidate in "${candidates[@]}"; do
            if git -C "${target_dir}" apply --check "${candidate}" >/dev/null 2>&1; then
                matches+=("${candidate}")
            fi
        done

        if [[ ${#matches[@]} -eq 0 ]]; then
            echo "ERROR: No patch variant in slot '${repo_name}/${slot}' applies cleanly to ${target_dir}:" >&2
            printf '  %s\n' "${candidates[@]}" >&2
            exit 1
        fi
        if [[ ${#matches[@]} -gt 1 ]]; then
            echo "ERROR: Ambiguous patch slot '${repo_name}/${slot}': multiple variants apply cleanly:" >&2
            printf '  %s\n' "${matches[@]}" >&2
            exit 1
        fi
        selected_patches+=("${matches[0]}")
    done

    if [[ "${check_only}" -eq 1 ]]; then
        echo "Checking ${#selected_patches[@]} patch(es) for ${repo_name} against ${target_dir}"
        git -C "${target_dir}" apply --check "${selected_patches[@]}"
        return 0
    fi

    for patch in "${selected_patches[@]}"; do
        echo "Applying patch ${repo_name}/$(basename "${patch}") to ${target_dir}"
        git -C "${target_dir}" apply --verbose "${patch}"
        local digest
        digest="$(sha256sum "${patch}" | awk '{print $1}')"
        applied_lines+=("patch=${repo_name}/$(basename "${patch}") sha256=${digest}")
    done
}

apply_repo_patches "ot-br-posix" "${src_dir}"
apply_repo_patches "openthread" "${src_dir}/third_party/openthread/repo"

if [[ "${check_only}" -eq 0 ]]; then
    mkdir -p "${install_prefix}"
    resolved_otbr_commit="$(git -C "${src_dir}" rev-parse HEAD)"
    openthread_commit="$(git -C "${src_dir}/third_party/openthread/repo" rev-parse HEAD)"
    {
        echo "channel=${channel}"
        echo "otbr_commit=${resolved_otbr_commit}"
        if [[ "${otbr_commit}" != "${resolved_otbr_commit}" ]]; then
            echo "otbr_ref=${otbr_commit}"
        fi
        echo "openthread_commit=${openthread_commit}"
        if [[ ${#applied_lines[@]} -eq 0 ]]; then
            echo "patches=none"
        else
            printf '%s\n' "${applied_lines[@]}"
        fi
    } > "${install_prefix}/BUILD_INFO"
fi
