"""Run the recovery workspace on the local loopback interface."""

import argparse
import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from threading import Event, Lock, Thread
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
import webbrowser

from recovery import MODES, ROOT, RUNS, run_check, validate_cases


def reopen_running_workspace(port):
    url = f"http://127.0.0.1:{port}/"
    try:
        with build_opener(ProxyHandler({})).open(url + "api/state", timeout=2) as response:
            state = json.load(response)
    except (OSError, ValueError):
        return False
    if not isinstance(state, dict) or state.get("station_restart") is not True:
        return False
    webbrowser.open(url)
    return True


class Workspace:
    def __init__(self):
        self.lock = Lock()
        self.worker = None
        self.cancel = Event()
        self.active = None
        self.latest = None
        self.error = None

    def history(self):
        files = sorted(RUNS.glob("*/evidence.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        reports = []
        for path in files[:12]:
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                reports.append({key: report[key] for key in ("run_id", "started_at", "verdict", "mode")})
            except (OSError, ValueError, KeyError):
                continue
        return reports

    def finish_run(self, mode, cases):
        try:
            report = asyncio.run(run_check(mode, self.cancel, selected=cases))
            with self.lock:
                self.latest = report["run_id"]
        except Exception as error:
            with self.lock:
                self.error = f"Could not save the run report. {error}"
        finally:
            with self.lock:
                self.active = None


class Handler(SimpleHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def address_ok(self):
        return self.headers.get("Host") in {self.server.authority, f"localhost:{self.server.server_port}"}

    def json_reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.address_ok():
            return self.json_reply({"error": "Use the local workspace URL."}, 403)
        state = self.server.workspace
        if self.path == "/api/state":
            with state.lock:
                return self.json_reply({"active": state.active, "latest": state.latest,
                                        "history": state.history(), "error": state.error,
                                        "release_selection": True, "forte_comparison": True,
                                        "station_restart": True})
        match = re.fullmatch(r"/api/runs/([a-f0-9]{32})", self.path)
        if match:
            path = RUNS / match[1] / "evidence.json"
            if path.is_file():
                return self.json_reply(json.loads(path.read_text(encoding="utf-8")))
            return self.json_reply({"error": "Run could not be found."}, 404)
        if urlsplit(self.path).path not in ("/", "/index.html", "/style.css", "/app.js", "/sample.json"):
            return self.json_reply({"error": "Resource could not be found."}, 404)
        return super().do_GET()

    def end_headers(self):
        if urlsplit(self.path).path in ("/", "/index.html", "/style.css", "/app.js", "/sample.json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        origins = {f"http://{self.server.authority}", f"http://localhost:{self.server.server_port}"}
        if (not self.address_ok() or self.headers.get("Origin") not in origins or
                self.headers.get_content_type() != "application/json"):
            return self.json_reply({"error": "Use the local workspace to change run state."}, 403)
        state = self.server.workspace
        if self.path == "/api/cancel":
            with state.lock:
                if state.active:
                    state.cancel.set()
            return self.json_reply({"ok": True})
        if self.path != "/api/run":
            return self.json_reply({"error": "Unknown action."}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError
            request = json.loads(self.rfile.read(length))
            mode = request["mode"]
            if mode not in MODES:
                raise ValueError
            cases = validate_cases(request["cases"]) if "cases" in request else None
            if cases is not None and mode != "suite":
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return self.json_reply({"error": "Choose a supported test."}, 400)
        with state.lock:
            if state.active:
                return self.json_reply({"error": "A run is already in progress."}, 409)
            state.cancel = Event()
            state.active = mode
            state.error = None
            state.worker = Thread(target=state.finish_run, args=(mode, cases))
            state.worker.start()
        return self.json_reply({"ok": True}, 202)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8775)
    parser.add_argument("--open", action="store_true", help="Open the workspace in a browser")
    args = parser.parse_args()
    workspace = Workspace()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=ROOT / "dist"))
    except OSError:
        if args.open and reopen_running_workspace(args.port):
            return
        parser.exit(1, "The workspace port is already in use. Close the existing server or select --port.\n")
    with server:
        server.workspace = workspace
        server.authority = f"127.0.0.1:{server.server_port}"
        url = f"http://{server.authority}/"
        print(f"Recovery workspace at {url}", flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            workspace.cancel.set()
            if workspace.worker:
                workspace.worker.join()


if __name__ == "__main__":
    main()
