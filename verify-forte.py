"""Compare the two QNX FORTE bootfiles and save independently observed results."""

import argparse
import asyncio
import json
from pathlib import Path

from forte import compare, compare_station_restart
from recovery import available_qemu


async def verify(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    qemu = args.qemu or available_qemu()
    if not qemu:
        raise RuntimeError("Install QEMU or set RECOVERY_QEMU")
    if args.station_restart:
        report = await compare_station_restart(args.protected, args.output, qemu)
        first, second = "volatile", "durable"
    else:
        report = await compare(args.blind, args.protected, args.output, qemu)
        first, second = "blind", "protected"
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report[first]["readbacks"] != [0, 1, 2] or report[second]["readbacks"] != [0, 1, 1]:
        raise SystemExit("The station did not reproduce the expected comparison outcomes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blind", type=Path, default=Path("forte-blind.ifs"))
    parser.add_argument("--protected", type=Path, default=Path("forte-protected.ifs"))
    parser.add_argument("--output", type=Path, default=Path("forte-evidence.json"))
    parser.add_argument("--qemu")
    parser.add_argument("--station-restart", action="store_true",
                        help="Replace the station process and compare volatile and durable command history")
    asyncio.run(verify(parser.parse_args()))
