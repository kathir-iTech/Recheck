"""Adapt the upstream 4diac E_SPLIT/E_CTU workflow with an output checkpoint."""

import argparse
from pathlib import Path


def workflow(upstream_trace, station_register):
    resource = "Ex3a_01_EventConnections"
    lines = [
        line for line in upstream_trace.splitlines()
        if line.startswith(resource + ";") or
        (line.startswith(";") and f'Name="{resource}"' in line)
    ]
    if len(lines) != 8 or not any("E_SPLIT.EO2" in line and "E_CTU.CU" in line for line in lines):
        raise ValueError("Expected the pinned upstream E_SPLIT/E_CTU trace example")
    suffix = [
        ('CREATE', '<FB Name="STATION" Type="recheck::Station" />'),
        ('WRITE', '<Connection Source="41" Destination="STATION.COMMAND" />'),
        ('WRITE', f'<Connection Source="{station_register}" Destination="STATION.REGISTER" />'),
        ('CREATE', '<Connection Source="E_CTU.CV" Destination="STATION.COUNT" />'),
        ('CREATE', '<Connection Source="E_CTU.CUO" Destination="STATION.REQ" />'),
        ('START', ''),
    ]
    for number, (action, body) in enumerate(suffix, 100):
        ending = f">{body}</Request>" if body else "/>"
        lines.append(f'{resource};<Request ID="{number}" Action="{action}"{ending}')
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--protected", action="store_true")
    args = parser.parse_args()
    trace = args.upstream / "tests/core/trace/referenceSystem.fboot"
    args.output.write_text(workflow(trace.read_text(encoding="utf-8"), 10 if args.protected else 8), encoding="utf-8")
