# shellcheck shell=bash

log_info() {
    printf 'INFO: %s\n' "$*"
}

log_warning() {
    printf 'WARNING: %s\n' "$*" >&2
}

log_error() {
    printf 'ERROR: %s\n' "$*" >&2
}

load_otbr_config() {
    local config_file="${OTBR_CONFIG_ENV:-/run/otbr/config.env}"
    if [[ ! -f "${config_file}" ]]; then
        log_error "OTBR config file not found: ${config_file}"
        return 1
    fi
    # shellcheck disable=SC1090
    source "${config_file}"
}
