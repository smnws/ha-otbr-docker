#!/usr/bin/env bash
set -euo pipefail

ref="${1:?Usage: fetch-otbr.sh <commit-or-ref>}"
dest="/usr/src/ot-br-posix"
cache_dir="/var/cache/otbr-git/ot-br-posix"

mkdir -p "$(dirname "${cache_dir}")"
if [[ ! -d "${cache_dir}/.git" ]]; then
    rm -rf "${cache_dir}"
    git clone --depth 1 -b main https://github.com/openthread/ot-br-posix.git "${cache_dir}"
fi

git -C "${cache_dir}" fetch --depth 1 origin "${ref}"
git -C "${cache_dir}" checkout --force FETCH_HEAD
git -C "${cache_dir}" reset --hard FETCH_HEAD
git -C "${cache_dir}" clean -fdx
git -C "${cache_dir}" submodule sync --recursive
git -C "${cache_dir}" submodule update --init --force --recursive --depth 1
git -C "${cache_dir}" submodule foreach --recursive 'git reset --hard && git clean -fdx'

rm -rf "${dest}"
cp -a "${cache_dir}" "${dest}"
