#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
apply_script="${repo_root}/build/apply-patches.sh"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

init_git_repo() {
    local dir="$1"
    local filename="$2"
    mkdir -p "${dir}"
    git -C "${dir}" init -q
    git -C "${dir}" config user.email "test@example.com"
    git -C "${dir}" config user.name "Test"
    echo "line1" > "${dir}/${filename}"
    git -C "${dir}" add "${filename}"
    git -C "${dir}" commit -q -m "init"
}

src_dir="${tmp_dir}/ot-br-posix"
ot_dir="${src_dir}/third_party/openthread/repo"
init_git_repo "${src_dir}" "otbr.txt"
echo "second1" > "${src_dir}/otbr2.txt"
git -C "${src_dir}" add "otbr2.txt"
git -C "${src_dir}" commit -q -m "add otbr2"
init_git_repo "${ot_dir}" "hdlc_interface.cpp"

otbr_sha="$(git -C "${src_dir}" rev-parse HEAD)"
ot_sha="$(git -C "${ot_dir}" rev-parse HEAD)"

empty_patches="${tmp_dir}/empty-patches"
mkdir -p "${empty_patches}/ot-br-posix" "${empty_patches}/openthread"

"${apply_script}" --check-only --src-dir "${src_dir}" --patches-dir "${empty_patches}"
prefix_none="${tmp_dir}/opt-none"
"${apply_script}" --src-dir "${src_dir}" --patches-dir "${empty_patches}" stable "${prefix_none}" "${otbr_sha}"

grep -Fxq "channel=stable" "${prefix_none}/BUILD_INFO"
grep -Fxq "otbr_commit=${otbr_sha}" "${prefix_none}/BUILD_INFO"
grep -Fxq "openthread_commit=${ot_sha}" "${prefix_none}/BUILD_INFO"
grep -Fxq "patches=none" "${prefix_none}/BUILD_INFO"

prefix_ref="${tmp_dir}/opt-ref"
"${apply_script}" --src-dir "${src_dir}" --patches-dir "${empty_patches}" stable "${prefix_ref}" "main"
grep -Fxq "otbr_commit=${otbr_sha}" "${prefix_ref}/BUILD_INFO"
grep -Fxq "otbr_ref=main" "${prefix_ref}/BUILD_INFO"

patches_dir="${tmp_dir}/patches"
mkdir -p "${patches_dir}/ot-br-posix" "${patches_dir}/openthread"

echo "line2" >> "${src_dir}/otbr.txt"
git -C "${src_dir}" diff -- otbr.txt > "${patches_dir}/ot-br-posix/0001-first.patch"
echo "second2" >> "${src_dir}/otbr2.txt"
git -C "${src_dir}" diff -- otbr2.txt > "${patches_dir}/ot-br-posix/0002-second.patch"
git -C "${src_dir}" checkout -- otbr.txt otbr2.txt

echo "patched-hdlc" >> "${ot_dir}/hdlc_interface.cpp"
git -C "${ot_dir}" diff > "${patches_dir}/openthread/0001-hdlc.patch"
git -C "${ot_dir}" checkout -- hdlc_interface.cpp

"${apply_script}" --check-only --src-dir "${src_dir}" --patches-dir "${patches_dir}"
test -z "$(git -C "${src_dir}" status --porcelain -- otbr.txt otbr2.txt)"
test -z "$(git -C "${ot_dir}" status --porcelain)"

prefix_patched="${tmp_dir}/opt-patched"
"${apply_script}" --src-dir "${src_dir}" --patches-dir "${patches_dir}" beta "${prefix_patched}" "${otbr_sha}"

grep -Fxq "line2" "${src_dir}/otbr.txt"
grep -Fxq "second2" "${src_dir}/otbr2.txt"
grep -Fxq "patched-hdlc" "${ot_dir}/hdlc_interface.cpp"

expected_sha1="$(sha256sum "${patches_dir}/ot-br-posix/0001-first.patch" | awk '{print $1}')"
expected_sha2="$(sha256sum "${patches_dir}/ot-br-posix/0002-second.patch" | awk '{print $1}')"
expected_sha3="$(sha256sum "${patches_dir}/openthread/0001-hdlc.patch" | awk '{print $1}')"

grep -Fxq "channel=beta" "${prefix_patched}/BUILD_INFO"
grep -Fxq "patch=ot-br-posix/0001-first.patch sha256=${expected_sha1}" "${prefix_patched}/BUILD_INFO"
grep -Fxq "patch=ot-br-posix/0002-second.patch sha256=${expected_sha2}" "${prefix_patched}/BUILD_INFO"
grep -Fxq "patch=openthread/0001-hdlc.patch sha256=${expected_sha3}" "${prefix_patched}/BUILD_INFO"

bad_patches="${tmp_dir}/bad-patches"
mkdir -p "${bad_patches}/ot-br-posix" "${bad_patches}/openthread"
cp "${patches_dir}/ot-br-posix/0001-first.patch" "${bad_patches}/ot-br-posix/0001-conflict.patch"
if "${apply_script}" --check-only --src-dir "${src_dir}" --patches-dir "${bad_patches}" 2>/dev/null; then
    echo "Expected conflicting patch check to fail" >&2
    exit 1
fi

stray_root="${tmp_dir}/stray-root-patches"
mkdir -p "${stray_root}/ot-br-posix" "${stray_root}/openthread"
cp "${patches_dir}/ot-br-posix/0001-first.patch" "${stray_root}/0001.patch"
if "${apply_script}" --check-only --src-dir "${src_dir}" --patches-dir "${stray_root}" 2>"${tmp_dir}/stray_root.err"; then
    echo "Expected stray root patch to fail" >&2
    exit 1
fi
grep -Fq "0001.patch" "${tmp_dir}/stray_root.err"

stray_typo="${tmp_dir}/stray-typo-patches"
mkdir -p "${stray_typo}/otbr"
cp "${patches_dir}/ot-br-posix/0001-first.patch" "${stray_typo}/otbr/0001.patch"
if "${apply_script}" --check-only --src-dir "${src_dir}" --patches-dir "${stray_typo}" 2>"${tmp_dir}/stray_typo.err"; then
    echo "Expected stray typo-dir patch to fail" >&2
    exit 1
fi
grep -Fq "otbr/0001.patch" "${tmp_dir}/stray_typo.err"

variant_src="${tmp_dir}/variant-src"
variant_ot="${variant_src}/third_party/openthread/repo"
init_git_repo "${variant_src}" "otbr.txt"
init_git_repo "${variant_ot}" "hdlc.cpp"

variant_patches="${tmp_dir}/variant-patches"
mkdir -p "${variant_patches}/ot-br-posix" "${variant_patches}/openthread"

echo "v1-change" >> "${variant_ot}/hdlc.cpp"
git -C "${variant_ot}" diff > "${variant_patches}/openthread/0001-fc.patch"
git -C "${variant_ot}" checkout -- hdlc.cpp

echo "other-base" > "${variant_ot}/hdlc.cpp"
git -C "${variant_ot}" add hdlc.cpp
git -C "${variant_ot}" commit -q -m "refactor"
echo "v2-change" >> "${variant_ot}/hdlc.cpp"
git -C "${variant_ot}" diff > "${variant_patches}/openthread/0001-fc@v2.patch"
git -C "${variant_ot}" checkout -- hdlc.cpp

"${apply_script}" --check-only --src-dir "${variant_src}" --patches-dir "${variant_patches}"
prefix_variant="${tmp_dir}/opt-variant"
"${apply_script}" --src-dir "${variant_src}" --patches-dir "${variant_patches}" beta "${prefix_variant}" "main"
grep -Fxq "v2-change" "${variant_ot}/hdlc.cpp"
grep -Eq "^patch=openthread/0001-fc@v2\.patch sha256=[0-9a-f]{64}$" "${prefix_variant}/BUILD_INFO"

cp "${variant_patches}/openthread/0001-fc@v2.patch" "${variant_patches}/openthread/0001-fc@dup.patch"
git -C "${variant_ot}" checkout -- hdlc.cpp
if "${apply_script}" --check-only --src-dir "${variant_src}" --patches-dir "${variant_patches}" 2>"${tmp_dir}/ambig.err"; then
    echo "Expected ambiguous variant slot to fail" >&2
    exit 1
fi
grep -Fq "Ambiguous patch slot" "${tmp_dir}/ambig.err"

echo "test_apply_patches.sh PASSED"
