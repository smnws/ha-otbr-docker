#!/usr/bin/with-contenv bash
# shellcheck shell=bash
set -euo pipefail

exec python3 /usr/local/lib/otbr/stage2_init.py
