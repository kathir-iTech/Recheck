#!/bin/bash
set -eo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ -n "${QNX_RETRY_SDP_ENV:-}" ]; then
    . "$QNX_RETRY_SDP_ENV" >/dev/null
fi
if ! command -v qcc >/dev/null || ! command -v mkifs >/dev/null || [ -z "${QNX_TARGET:-}" ]; then
    echo "Load the QNX SDP 8.0 environment before running this script." >&2
    exit 2
fi
set -u
if [ -n "${QNX_RETRY_CONFIGURATION:-}" ]; then
    export QNX_CONFIGURATION="$QNX_RETRY_CONFIGURATION"
    export QNX_CONFIGURATION_EXCLUSIVE="$QNX_CONFIGURATION"
fi
export QNX_TARGET="${QNX_INDUSTRIAL_TARGET:-$QNX_TARGET}"
output=industrial-proof.ifs
sources=modbus_probe.c
case "${1:-serial}" in
    serial)
        image=image-serial.build
        qcc -Vgcc_ntox86_64 -Wall -Wextra -Werror -DSTATION_SERIAL modbus_probe.c -o modbus_probe
        ;;
    ethernet)
        image=image.build
        qcc -Vgcc_ntox86_64 -Wall -Wextra -Werror modbus_probe.c -lsocket -o modbus_probe
        ;;
    recovery)
        image=image-serial.build
        output=industrial-recovery.ifs
        sources=recovery_probe.c
        qcc -Vgcc_ntox86_64 -Wall -Wextra -Werror "$sources" -o recovery_probe
        ;;
    *) echo "Usage: bash build.sh [serial|ethernet|recovery]" >&2; exit 2 ;;
esac
definition=$(mktemp)
trap 'rm -f -- "$definition"' EXIT
program="${sources%.c}"
sed "s@/proc/boot/modbus_probe@/proc/boot/$program@" "$image" > "$definition"
printf '\n%s="%s/%s"\n' "$program" "$PWD" "$program" >> "$definition"
mkifs -v "$definition" "$output"
