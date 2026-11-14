"""Run the same FORTE workflow twice on each QNX image and compare station readback."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

from verify import HOST, ROOT, count_advances, relay_serial


class RestartingStation:
    def __init__(self, database, log, durable_ids):
        self.database = database
        self.log = log
        self.durable_ids = durable_ids
        self.process = None
        self.pids = []

    async def start(self):
        executable = sys.executable
        environment = os.environ.copy()
        if os.name == "nt" and sys.prefix != sys.base_prefix:
            # Bypass the Windows venv launcher so the subprocess handle owns the interpreter.
            executable = sys._base_executable
            environment["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path)
        command = [executable, str(ROOT / "station_service.py"), str(self.database)]
        if self.durable_ids:
            command.append("--durable-ids")
        self.process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            ready = await asyncio.wait_for(self.process.stdout.readline(), timeout=20)
        except TimeoutError as error:
            raise RuntimeError("The station did not start listening within 20 seconds.") from error
        try:
            info = json.loads(ready)
            if not isinstance(info["pid"], int) or not 0 < info["port"] < 65536:
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Station process did not report its PID and listening port.") from error
        self.pids.append(info["pid"])
        return info["port"]

    async def stop(self):
        if not self.process or self.process.stdin.is_closing():
            return
        collect = asyncio.create_task(self.process.communicate())
        try:
            _, stderr = await asyncio.wait_for(asyncio.shield(collect), timeout=20)
        except TimeoutError:
            if self.process.returncode is None:
                self.process.kill()
            _, stderr = await collect
        with self.log.open("ab") as log:
            log.write(stderr)

    def events(self):
        from station_store import read_events

        return read_events(self.database)


@asynccontextmanager
async def station_endpoint(output, database, durable_ids):
    if database is not None:
        station = RestartingStation(database, output.with_suffix(".station.log"), durable_ids)
        try:
            yield await station.start(), station
        finally:
            await station.stop()
        return

    from pymodbus.server import ModbusTcpServer
    from station import PackagingStation

    station = PackagingStation()
    server = ModbusTcpServer(station.device(), address=(HOST, 0))
    await server.serve_forever(background=True)
    try:
        yield server.transport.sockets[0].getsockname()[1], station
    finally:
        await server.shutdown()


async def run_image(image, output, qemu, register, cancellation=None, database=None, durable_ids=False):
    if cancellation and cancellation.is_set():
        raise InterruptedError("Run canceled")
    if not image.is_file():
        raise RuntimeError(f"{image.name} is missing. Follow the FORTE build steps in the README.")
    async with station_endpoint(output, database, durable_ids) as (port, station):
        return await observe_guest(image, output, qemu, register, port, station, cancellation)


async def observe_guest(image, output, qemu, register, station_port, station, cancellation):
    from pymodbus.client import AsyncModbusTcpClient

    restarting = isinstance(station, RestartingStation)
    observer = AsyncModbusTcpClient(HOST, port=station_port, timeout=2, retries=0)
    guest = relay = None
    console = output.with_suffix(".guest.log")
    qemu_log = output.with_suffix(".qemu.log")
    console.write_text("", encoding="utf-8")
    try:
        if not await observer.connect() or await count_advances(observer) != 0:
            raise RuntimeError("Could not establish the station baseline")
        with socket.socket() as reservation:
            reservation.bind((HOST, 0))
            serial_port = reservation.getsockname()[1]
        command = [
            qemu, "-accel", "tcg,thread=single", "-cpu", "max", "-smp", "1", "-m", "256",
            "-no-reboot", "-display", "none", "-monitor", "none", "-nic", "none",
            "-kernel", str(image), "-serial", f"file:{console}",
            "-serial", f"tcp:{HOST}:{serial_port},server=on,wait=on",
        ]
        with qemu_log.open("wb") as error:
            guest = subprocess.Popen(command, stdout=error, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        readbacks = []
        recovered = None

        async def observe_write():
            nonlocal observer, recovered
            readbacks.append(await count_advances(observer))
            if not restarting or len(readbacks) != 1:
                return None
            observer.close()
            await station.stop()
            if station.process.returncode != 86:
                raise RuntimeError("The original station did not exit at the requested checkpoint.")
            try:
                _, previous = await asyncio.open_connection(HOST, station_port)
            except OSError:
                pass
            else:
                previous.close()
                await previous.wait_closed()
                raise RuntimeError("The original station was still listening after termination.")
            port = await station.start()
            observer = AsyncModbusTcpClient(HOST, port=port, timeout=2, retries=0)
            if not await observer.connect():
                raise RuntimeError("Could not connect to the replacement station.")
            recovered = await count_advances(observer)
            if readbacks[0] != 1 or recovered != readbacks[0] or len(set(station.pids)) != 2:
                raise RuntimeError("Station restart or restored effect count could not be verified.")
            return port

        relay = asyncio.create_task(relay_serial(serial_port, station_port, guest, recovery=True,
                                                 observe_write=observe_write))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 90
        while loop.time() < deadline and guest.poll() is None:
            if cancellation and cancellation.is_set():
                raise InterruptedError("Run canceled")
            if relay.done():
                relay.result()
            log = console.read_text(encoding="ascii", errors="replace")
            if "FORTE_RUN_INCOMPLETE" in log or "FORTE_EFFECT_INCOMPLETE" in log:
                raise RuntimeError(f"FORTE run incomplete. See {console}")
            if "FORTE_RECOVERY_DONE" in log:
                # FORTE's management listener also logs to the guest console.
                effects = re.findall(r"FORTE_EFFECT_ACK pid=(\d+) command=41 counter=2 register=(\d+)\b", log)
                exits = re.findall(r"FORTE_EXIT attempt=([12]) pid=(\d+) status=86\b", log)
                if (len(effects) != 2 or effects[0][0] == effects[1][0] or
                        exits != [("1", effects[0][0]), ("2", effects[1][0])] or
                        any(address != str(register) for _, address in effects)):
                    raise RuntimeError("Native FORTE checkpoint or replacement process could not be verified")
                count = await count_advances(observer)
                station_events = station.events() if restarting else station.advances
                if len(readbacks) != 2 or readbacks[-1] != count or station_events != [
                    {"command_id": 41, "advance": i} for i in range(1, count + 1)
                ]:
                    raise RuntimeError("Independent readbacks and station event records disagree.")
                report = {
                    "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                    "controller_pids": [int(pid) for pid, _ in effects],
                    "command_id": 41,
                    "checkpoint": "station write acknowledged before adapter returns",
                    "observed_advances": count,
                    "readbacks": [0, *readbacks],
                    "release_blocked": count != 1,
                    "station_events": station_events,
                    "guest_log": console.name,
                }
                if restarting:
                    report["station_restart"] = {
                        "process_ids": station.pids,
                        "checkpoint": "first write committed and acknowledged to bridge before QNX receives its reply",
                        "readback_after_restart": recovered,
                        "durable_command_history": station.durable_ids,
                    }
                return report
            await asyncio.sleep(0.05)
        raise RuntimeError(f"FORTE did not finish. See {console} and {qemu_log}")
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


async def compare(blind, protected, output, qemu, cancellation=None):
    report = {"target": "QNX 8.0 x86-64 in QEMU", "upstream": "Eclipse 4diac FORTE 3.3.0",
              "upstream_commit": "6db22560feff98fc113ca7af2100e2c6dbc4a6e8",
              "scenario": "Upstream E_SPLIT/E_CTU with a test station function block",
              "observer": "Separate Modbus/TCP station readback"}
    for name, image, register in (("blind", blind, 8), ("protected", protected, 10)):
        report[name] = await run_image(image.resolve(), output.with_name(f"{output.stem}-{name}.json"),
                                       qemu, register, cancellation)
        print(f"{name}: {report[name]['observed_advances']} advance(s) independently observed", flush=True)
    return report


async def compare_station_restart(image, output, qemu, cancellation=None):
    from tempfile import TemporaryDirectory

    report = {
        "target": "QNX 8.0 x86-64 in QEMU",
        "upstream": "Eclipse 4diac FORTE 3.3.0",
        "scenario": "Same protected controller image, host station process replaced after first write",
        "observer": "Separate Modbus/TCP station readback before and after replacement",
    }
    for name, retained in (("volatile", False), ("durable", True)):
        log_path = output.with_name(f"{output.stem}-{name}.json")
        with TemporaryDirectory(prefix=f"station-{name}-", dir=output.parent) as directory:
            # Each comparison starts from empty storage, even when reusing an output filename.
            report[name] = await run_image(image.resolve(), log_path, qemu, 10, cancellation,
                                           Path(directory) / "station.sqlite", retained)
        print(f"{name}: {report[name]['observed_advances']} advance(s) independently observed", flush=True)
    return report
