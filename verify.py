"""Verify a native QNX Modbus write by reading station state on another connection."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess

from station import ADVANCE_COUNT_REGISTER, COMMAND_REGISTER, DEVICE, EXPECTED_COMMAND, PackagingStation


HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parent


async def count_advances(client):
    reply = await client.read_holding_registers(ADVANCE_COUNT_REGISTER, count=1, device_id=DEVICE)
    if reply.isError() or len(reply.registers) != 1:
        raise RuntimeError(f"Station readback failed: {reply}")
    return reply.registers[0]


async def read_frame(reader):
    header = await reader.readexactly(7)
    length = int.from_bytes(header[4:6], "big")
    if header[2:4] != b"\0\0" or not 2 <= length <= 254:
        raise RuntimeError("Invalid Modbus frame header")
    return header + await reader.readexactly(length - 1)


async def relay_serial(guest_port, station_port, guest, recovery=False, observe_write=None):
    while guest.poll() is None:
        try:
            reader, writer = await asyncio.open_connection(HOST, guest_port)
        except OSError:
            await asyncio.sleep(0.1)
            continue
        break
    else:
        raise RuntimeError("QEMU stopped before the serial bridge connected")

    links = {}
    try:
        while True:
            request = await read_frame(reader)
            function = request[7]
            route = "observer" if function == 3 else "command"
            if route not in links:
                links[route] = await asyncio.open_connection(HOST, station_port)
            station_reader, station_writer = links[route]
            station_writer.write(request)
            await station_writer.drain()
            response = await read_frame(station_reader)
            if observe_write and function == 16 and response[7] == function:
                replacement_port = await observe_write()
                if replacement_port is not None:
                    for _, link in links.values():
                        link.close()
                    await asyncio.gather(*(link[1].wait_closed() for link in links.values()),
                                         return_exceptions=True)
                    links.clear()
                    station_port = replacement_port
            writer.write(response)
            await writer.drain()
            if not recovery:
                return
    finally:
        writer.close()
        await writer.wait_closed()
        for _, station_writer in links.values():
            station_writer.close()
        await asyncio.gather(*(link[1].wait_closed() for link in links.values()), return_exceptions=True)


async def verify(image, qemu, output, station_only, ethernet, recovery=False, observer_outage=False,
                 cancellation=None):
    from pymodbus.client import AsyncModbusTcpClient
    from pymodbus.server import ModbusTcpServer

    if cancellation and cancellation.is_set():
        raise InterruptedError("Run canceled")
    if not station_only:
        output.parent.mkdir(parents=True, exist_ok=True)
    station = PackagingStation(observer_outage=observer_outage)
    server = ModbusTcpServer(station.device(), address=(HOST, 0))
    await server.serve_forever(background=True)
    port = server.transport.sockets[0].getsockname()[1]
    observer = AsyncModbusTcpClient(HOST, port=port, timeout=2, retries=0)
    guest = None
    relay = None
    console = output.with_suffix(".guest.log")
    stderr = output.with_suffix(".qemu.log")
    try:
        if not await observer.connect() or await count_advances(observer) != 0:
            raise RuntimeError("Station baseline could not be established")
        if station_only:
            sender = AsyncModbusTcpClient(HOST, port=port, timeout=2, retries=0)
            try:
                if not await sender.connect():
                    raise RuntimeError("Station command connection failed")
                reply = await sender.write_register(COMMAND_REGISTER, EXPECTED_COMMAND, device_id=DEVICE)
                if reply.isError():
                    raise RuntimeError(f"Station write failed: {reply}")
                if await count_advances(observer) != 1 or len(station.advances) != 1:
                    raise RuntimeError("Independent observer did not see exactly one advance")
                print("Host station self-check passed. Separate Modbus write and read connections.")
                return
            finally:
                sender.close()

        console.write_text("", encoding="ascii")
        command = [
            str(qemu), "-accel", "tcg,thread=single", "-cpu", "max", "-smp", "1", "-m", "256",
            "-no-reboot", "-display", "none", "-monitor", "none",
            "-kernel", str(image), "-serial", f"file:{console}",
        ]
        if ethernet:
            command += [
                "-netdev", f"user,id=station,restrict=on,guestfwd=tcp:10.0.2.100:1502-tcp:{HOST}:{port}",
                "-device", "e1000,netdev=station",
            ]
        else:
            with socket.socket() as reservation:
                reservation.bind((HOST, 0))
                guest_port = reservation.getsockname()[1]
            command += ["-nic", "none", "-serial", f"tcp:{HOST}:{guest_port},server=on,wait=on"]
        with stderr.open("wb") as log:
            guest = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if not ethernet:
                relay = asyncio.create_task(relay_serial(guest_port, port, guest, recovery))
            deadline = asyncio.get_running_loop().time() + 75
            while asyncio.get_running_loop().time() < deadline and guest.poll() is None:
                if cancellation and cancellation.is_set():
                    raise InterruptedError("Run canceled")
                if relay and relay.done():
                    relay.result()
                captured = console.read_text(encoding="ascii", errors="replace")
                if recovery:
                    if "RECOVERY_SUITE_OK" in captured or "RECOVERY_INCOMPLETE" in captured:
                        return await verify_recovery(captured, station, observer, image, output, observer_outage)
                    await asyncio.sleep(0.1)
                    continue
                match = re.search(r"^MODBUS_WRITE_OK (\{.*\})\s*$", captured, re.MULTILINE)
                if match:
                    native = json.loads(match[1])
                    if native.get("os") != "QNX" or native.get("command_id") != EXPECTED_COMMAND:
                        raise RuntimeError(f"Unexpected guest acknowledgement: {native}")
                    if await count_advances(observer) != 1 or station.advances != [
                        {"command_id": EXPECTED_COMMAND, "advance": 1}
                    ]:
                        raise RuntimeError("Independent observer did not see the expected advance")
                    evidence = {
                        "target": "QNX x86-64 in QEMU",
                        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                        "guest": native,
                        "command_transport": "native Modbus/TCP" if ethernet else "serial bridge to Modbus/TCP",
                        "observer": {"transport": "separate Modbus/TCP connection", "before": 0, "after": 1},
                        "station_events": station.advances,
                    }
                    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
                    print("QNX Modbus write verified. Separate observer measured one station advance.")
                    return
                await asyncio.sleep(0.1)
        if guest.poll() is not None:
            raise RuntimeError(f"QEMU exited with status {guest.returncode} before verification completed.")
        raise RuntimeError("QNX did not finish before the verification deadline.")
    finally:
        if relay:
            relay.cancel()
            await asyncio.gather(relay, return_exceptions=True)
        if guest and guest.poll() is None:
            guest.terminate()
            try:
                guest.wait(timeout=5)
            except subprocess.TimeoutExpired:
                guest.kill()
                guest.wait()
        observer.close()
        await server.shutdown()


CASES = [
    ("normal", 41, "ACK", 1, 1, False),
    ("before_action_retry", 42, "ESRCH", 0, 1, True),
    ("before_action_suppressed", 43, "ESRCH", 0, 0, False),
    ("after_action_blind_retry", 44, "ESRCH", 1, 2, True),
    ("after_action_protected_retry", 45, "ESRCH", 1, 1, True),
    ("after_reply_control", 46, "ACK", 1, 1, False),
]


async def verify_recovery(console, station, observer, image, output, observer_outage):
    evidence = {
        "schema_version": 1,
        "target": "QNX 8.0 x86-64 in QEMU",
        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "command_transport": "serial bridge to Modbus/TCP",
        "observer": "separate Modbus/TCP connection",
        "station_events": station.advances,
        "station_observations": station.observations,
    }
    if "RECOVERY_INCOMPLETE" in console:
        if not observer_outage or "observer could not establish effect count" not in console:
            failure = next(line for line in console.splitlines() if line.startswith("RECOVERY_INCOMPLETE"))
            raise RuntimeError(f"QNX recovery test incomplete. {failure.removeprefix('RECOVERY_INCOMPLETE ')}")
        if station.advances != [{"command_id": 41, "advance": 1}]:
            raise RuntimeError("Observer outage occurred outside the intended checkpoint")
        evidence["verdict"] = "incomplete"
        evidence["reason"] = "station observer unavailable after the first effect"
        evidence["release_blocked"] = True
        output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print("Observer outage verified. Test incomplete and release blocked.")
        return 2
    if observer_outage:
        raise RuntimeError("Recovery suite incorrectly completed despite observer outage")

    cases = [json.loads(line.removeprefix("RECOVERY_CASE ")) for line in console.splitlines()
             if line.startswith("RECOVERY_CASE ")]
    histories = [json.loads(line.removeprefix("RECOVERY_HISTORY ")) for line in console.splitlines()
                 if line.startswith("RECOVERY_HISTORY ")]
    if len(cases) != len(CASES) or len(histories) != 1:
        raise RuntimeError("Missing or duplicate recovery observations")

    expected_events = []
    expected_observations = [0]  # Host baseline before starting QEMU.
    for row, (name, command_id, reply, before_retry, final, replaced) in zip(cases, CASES):
        baseline = len(expected_events)
        if (row["case"], row["command_id"], row["caller_reply"], row["baseline"],
            row["before_retry"] - baseline, row["final"] - baseline) != (
            name, command_id, reply, baseline, before_retry, final
        ):
            raise RuntimeError(f"Unexpected result in recovery case {name}: {row}")
        old, new = row["original_pid"], row["replacement_pid"]
        if old <= 0 or (replaced and (new <= 0 or new == old)) or (not replaced and new != 0):
            raise RuntimeError(f"Unexpected controller identity in {name}")
        row["observed_advances"] = final
        row["verdict"] = "pass" if final == 1 else "regression"
        row["release_blocked"] = final != 1
        expected_observations.extend((baseline, row["before_retry"], row["final"]))
        expected_events.extend([command_id] * final)

    baseline = len(expected_events)
    final = baseline + 2
    if histories[0] != {"baseline": baseline, "before_retry": final, "after_retry": final,
                        "after_conflict": final, "conflict_acknowledged": True}:
        raise RuntimeError(f"Unexpected interleaved replay or payload conflict result: {histories}")
    expected_events.extend((51, 52))
    expected_observations.extend((baseline, final, final, final))
    if station.observations != expected_observations or await count_advances(observer) != final:
        raise RuntimeError("Independent station readings disagree with the guest report")
    if station.advances != [{"command_id": command_id, "advance": index}
                            for index, command_id in enumerate(expected_events, 1)]:
        raise RuntimeError("Station effects do not match the command sequence")
    evidence.update(verdict="suite_verified", cases=cases, history=histories[0])
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print("QNX recovery suite verified. Two release regressions caught, four control/recovery cases passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--qemu", type=Path, default=Path("D:/QNX/qemu/qemu-system-x86_64.exe"))
    parser.add_argument("--output", type=Path, default=ROOT / "evidence.json")
    parser.add_argument("--station-only", action="store_true")
    parser.add_argument("--ethernet", action="store_true")
    parser.add_argument("--recovery", action="store_true")
    parser.add_argument("--observer-outage", action="store_true")
    args = parser.parse_args()
    if args.recovery and (args.ethernet or args.station_only):
        parser.error("Recovery requires the serial QNX image")
    if args.observer_outage and not args.recovery:
        parser.error("--observer-outage requires --recovery")
    image = args.image or ROOT / ("industrial-recovery.ifs" if args.recovery else "industrial-proof.ifs")
    raise SystemExit(asyncio.run(verify(
        image.resolve(), args.qemu, args.output.resolve(), args.station_only,
        args.ethernet, args.recovery, args.observer_outage,
    )))
