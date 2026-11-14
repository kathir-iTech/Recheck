#!/bin/bash
set -eo pipefail
if [ "$#" -ne 2 ]; then
    echo "Usage: bash build-forte-proof.sh FORTE_SOURCE FORTE_BUILD" >&2
    exit 2
fi
source_tree=$(realpath "$1")
compiled=$(realpath "$2")
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
definition=$(mktemp)
trap 'rm -f -- "$definition"' EXIT

q++ -Vgcc_ntox86_64 -std=gnu++20 -shared -fPIC -fno-rtti -fno-exceptions -Wall -Wextra \
    -I"$source_tree/core/include" -I"$source_tree/core/arch/common/include" \
    -I"$source_tree/core/arch/posix/include" -I"$compiled/core/include" \
    forte-station.cpp -L"$compiled/core" -lforte-core -o recheck-station.so
qcc -Vgcc_ntox86_64 -Wall -Wextra -Werror forte-supervisor.c -o forte-supervisor

for policy in blind protected; do
    selection=()
    if [ "$policy" = protected ]; then selection+=(--protected); fi
    python3 forte-workflow.py "$source_tree" "forte-$policy.fboot" "${selection[@]}"
    sed "s@/proc/boot/modbus_probe@/proc/boot/forte-supervisor /proc/boot/controller.fboot@" image-serial.build > "$definition"
    printf '\nforte="%s/forte"\nforte-supervisor="%s/forte-supervisor"\nrecheck-station.so="%s/recheck-station.so"\n' \
        "$compiled" "$PWD" "$PWD" >> "$definition"
    printf 'controller.fboot="%s/forte-%s.fboot"\n' "$PWD" "$policy" >> "$definition"
    for library in "$compiled"/core/libforte-core.so "$compiled"/stdfblib/{system,net,events}/libforte-*.so; do
        printf '%s="%s"\n' "$(basename "$library")" "$library" >> "$definition"
    done
    mkifs "$definition" "forte-$policy.ifs"
done
