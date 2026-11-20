#!/usr/bin/env bash
set -euo pipefail

commit="${1:?Usage: fetch-otbr.sh <commit>}"

git clone --depth 1 -b main https://github.com/openthread/ot-br-posix.git /usr/src/ot-br-posix
cd /usr/src/ot-br-posix
git fetch --depth 1 origin "${commit}"
git checkout "${commit}"
git submodule update --init --recursive --depth 1
