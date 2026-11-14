"""Run one isolated station process for the FORTE restart comparison."""

import argparse
import asyncio
import json
import os
import sys
from threading import Thread

from station import PackagingStation
from station_store import StationStore
from verify import HOST


async def serve(database, durable_ids):
    from pymodbus.server import ModbusTcpServer

    store = StationStore(database, durable_ids)
    try:
        station = PackagingStation(store=store)
        server = ModbusTcpServer(station.device(), address=(HOST, 0))
        await server.serve_forever(background=True)
        try:
            port = server.transport.sockets[0].getsockname()[1]
            print(json.dumps({"pid": os.getpid(), "port": port}), flush=True)
            await asyncio.Event().wait()
        finally:
            await server.shutdown()
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--durable-ids", action="store_true")
    args = parser.parse_args()

    def exit_on_supervisor_disconnect():
        sys.stdin.buffer.read(1)
        os._exit(86)

    Thread(target=exit_on_supervisor_disconnect, daemon=True).start()
    asyncio.run(serve(args.database, args.durable_ids))
