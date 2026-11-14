"""Shared local QNX release-check runner and command-line entry point."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import uuid

from verify import CASES, verify

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
IMAGE = ROOT / "industrial-recovery.ifs"
STATION_IMAGE = ROOT / "forte-protected.ifs"
MODES = ("suite", "observer_outage", "forte", "station_restart")
CASE_NAMES = [case[0] for case in CASES]


def validate_cases(cases):
    if (not isinstance(cases, list) or not cases or
            any(not isinstance(case, str) or case not in CASE_NAMES for case in cases) or
            len(set(cases)) != len(cases)):
        raise ValueError("Choose one or more distinct recovery cases.")
    return cases


def available_qemu():
    local = Path("D:/QNX/qemu/qemu-system-x86_64.exe")
    return os.environ.get("RECOVERY_QEMU") or shutil.which("qemu-system-x86_64") or (
        str(local) if local.is_file() else None
    )


async def run_check(mode, cancellation=None, qemu=None, image=None, output=None, selected=None,
                    station_policy="durable"):
    run_id = uuid.uuid4().hex
    destination = Path(output) if output else RUNS / run_id / "evidence.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    with TemporaryDirectory(prefix="recheck-") as directory:
        raw = Path(directory) / "evidence.json"
        try:
            qemu = qemu or available_qemu()
            if not qemu or not (Path(qemu).is_file() or shutil.which(str(qemu))):
                raise RuntimeError("Install QEMU or set RECOVERY_QEMU to its executable path.")
            if mode == "station_restart":
                from forte import compare_station_restart

                report = await compare_station_restart(image or STATION_IMAGE, raw, qemu, cancellation)
                if report["volatile"]["readbacks"] != [0, 1, 2]:
                    report.update(schema_version=1, verdict="incomplete", release_blocked=True,
                                  reason="The control trial did not reproduce a duplicate. The gate could not be verified.")
                else:
                    report.update(schema_version=1, verdict="comparison_verified",
                                  release_blocked=report[station_policy]["release_blocked"])
            elif mode == "forte":
                from forte import compare
                report = await compare(ROOT / "forte-blind.ifs", ROOT / "forte-protected.ifs", raw,
                                       qemu, cancellation)
                report.update(schema_version=1, verdict="comparison_verified",
                              release_blocked=report["protected"]["release_blocked"])
            else:
                image = image or IMAGE
                if not image.is_file():
                    raise RuntimeError("The QNX image is missing. Build it with bash build.sh recovery.")
                await verify(image.resolve(), qemu, raw, False, False, recovery=True,
                             observer_outage=(mode == "observer_outage"), cancellation=cancellation)
                report = json.loads(raw.read_text(encoding="utf-8"))
        except (InterruptedError, asyncio.CancelledError):
            report = {"schema_version": 1, "verdict": "canceled", "release_blocked": True}
        except Exception as error:
            reason = str(error).replace(str(directory), "the run logs")
            if isinstance(error, ModuleNotFoundError) and error.name == "pymodbus":
                reason = "Install the station dependency with python -m pip install -r requirements.txt."
            report = {"schema_version": 1, "verdict": "incomplete", "release_blocked": True,
                      "reason": reason}
        if mode in ("forte", "station_restart"):
            report["logs"] = {path.name: path.read_text(encoding="utf-8", errors="replace")
                              for path in sorted(raw.parent.glob("evidence-*.log"))}
        else:
            for key, suffix in (("guest_log", ".guest.log"), ("qemu_log", ".qemu.log")):
                log = raw.with_suffix(suffix)
                if log.is_file():
                    report[key] = log.read_text(encoding="utf-8", errors="replace")

    report.update(run_id=run_id, mode=mode, started_at=started,
                  finished_at=datetime.now(timezone.utc).isoformat())
    report["selected_cases"] = (selected if selected is not None else CASE_NAMES) if mode == "suite" else []
    if report["verdict"] == "suite_verified":
        report["release_blocked"] = any(
            row["release_blocked"] for row in report["cases"] if row["case"] in report["selected_cases"]
        )
    if mode == "station_restart":
        report["gate"] = {"station_history": station_policy, "required_advances": 1}
    temporary = destination.with_name(destination.name + "." + run_id + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", choices=CASE_NAMES, help="Gate on this case from a fresh QNX suite")
    selection.add_argument("--scenario", type=Path, help="JSON with the case IDs to gate on")
    selection.add_argument("--observer-outage", action="store_true")
    selection.add_argument("--station-restart", action="store_true",
                           help="Run the two station restart trials and gate on persistent history by default")
    parser.add_argument("--station-policy", choices=("durable", "volatile"),
                        help="Which history policy determines the station restart exit status")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image", type=Path,
                        help="QNX guest image for the reference suite or station restart")
    parser.add_argument("--qemu")
    args = parser.parse_args()
    if args.station_policy and not args.station_restart:
        parser.error("--station-policy requires --station-restart")
    selected = [args.case] if args.case else CASE_NAMES
    if args.scenario:
        try:
            scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
            selected = validate_cases(scenario["cases"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(f"Invalid scenario file. {error}")
    mode = "station_restart" if args.station_restart else "observer_outage" if args.observer_outage else "suite"
    policy = args.station_policy or "durable"
    report = asyncio.run(run_check(mode, qemu=args.qemu, image=args.image, output=args.output,
                                   selected=selected, station_policy=policy))
    if mode == "station_restart":
        if report["verdict"] != "comparison_verified":
            print(report.get("reason", report["verdict"]))
            return 2
        for name in ("volatile", "durable"):
            row = report[name]
            verdict = "BLOCKED" if row["release_blocked"] else "PASSED"
            print(f"{name}: {row['observed_advances']} advance(s)  {verdict}")
        print(f"Release gate: {policy} station history")
        return int(report["release_blocked"])
    if report["verdict"] != "suite_verified":
        print(report.get("reason", report["verdict"]))
        return 2
    rows = [row for row in report["cases"] if row["case"] in selected]
    for row in rows:
        print(f"{row['case']}  {row['observed_advances']} advance(s)  {row['verdict'].upper()}")
    return int(report["release_blocked"])


if __name__ == "__main__":
    raise SystemExit(main())
