"""Short-lived server bridge for companions that predate living task pages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .reports import ReportServer
from .router import process_token
from .store import Store, browser_home


def available_base(home):
    path = Path(home) / "reports-server.json"
    try:
        value = json.loads(path.read_text())
        if value.get("pid") and process_token(value["pid"]) == value.get("process_token"):
            return f'http://127.0.0.1:{value["port"]}/{value["token"]}'
    except (OSError, ValueError, KeyError):
        pass
    return None


def start_bridge(home):
    if available_base(home):
        return available_base(home)
    root = str(Path(__file__).resolve().parent.parent)
    code = ("import sys;sys.path.insert(0," + repr(root)
            + ");from herdr_browser.serve_reports import main;main()")
    log = Path(home) / "reports-server.log"
    fd = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a") as stream:
        process = subprocess.Popen([sys.executable, "-c", code], stdout=stream, stderr=stream,
                                   env=dict(os.environ, HERDR_BROWSER_HOME=str(home)),
                                   close_fds=True, start_new_session=True)
        threading.Thread(target=process.wait, daemon=True).start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = available_base(home)
        if value:
            return value
        time.sleep(.05)
    raise ValueError("The local task-page server did not start; see its private log.")


def main():
    server = ReportServer(Store())
    server.ensure()
    if not server.server:
        return
    try:
        while True:
            live = []
            for path in (browser_home() / "instances").glob("*.json"):
                try:
                    info = json.loads(path.read_text())
                    if process_token(info["pid"]) == info.get("process_token"):
                        live.append(info)
                except (OSError, ValueError, KeyError):
                    continue
            # Hand the same port back to a report-aware companion after Apply update.
            if not live or any(info.get("reports_url") == server.base for info in live):
                break
            time.sleep(.5)
    finally:
        server.close()


if __name__ == "__main__":
    main()
