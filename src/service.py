"""Noninteractive local supervisor: python -m src.service. Ctrl+C stops children."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import signal
import socket
import subprocess
import sys
import time

from src import db as dbm
from src.config import load_config
from src.timeutil import now_ist

log = logging.getLogger(__name__)


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)


def main() -> int:
    cfg = load_config()
    # Bind a separate loopback lock port for the full supervisor lifetime.
    # This prevents double-clicks from creating duplicate schedulers.
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 18001))
        lock.listen(1)
    except OSError:
        print("NEUROX is already running, or local port 18001 is occupied.")
        lock.close()
        return 2
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8000)) == 0:
            print("Port 8000 is occupied. Stop the previous dashboard first.")
            lock.close()
            return 2
    logs = cfg.root / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "service.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(message)s")
    with dbm.connection_scope():
        conn = dbm.connect(cfg.path("paths.db"))
        dbm.init_db(conn)
        if not dbm.get_state(conn, "forward_validation_since"):
            dbm.set_state(conn, "forward_validation_since", now_ist().isoformat(timespec="seconds"))
    commands = {
        "dashboard": [sys.executable, "-m", "uvicorn", "src.api.app:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
        "scheduler": [sys.executable, "-m", "src.jobs.scheduler"],
    }
    # Drain child output continuously through rotating logs (no unbounded files).
    import threading

    def drain(stream, name):
        for line in stream:
            log.info("%s: %s", name, line.rstrip())
        stream.close()

    children = {}
    failures = dict.fromkeys(commands, 0)
    next_start = dict.fromkeys(commands, 0.0)
    started = {}
    running = True

    def shutdown(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        while running:
            for name, command in commands.items():
                child = children.get(name)
                if child is not None and child.poll() is not None:
                    failures[name] = 1 if time.monotonic() - started[name] > 300 else failures[name] + 1
                    next_start[name] = time.monotonic() + min(60, 2 ** min(failures[name], 6))
                    log.warning("%s exited (%s); restart in %.0fs", name, child.returncode, next_start[name] - time.monotonic())
                    del children[name]
                if name not in children and time.monotonic() >= next_start[name]:
                    try:
                        child = subprocess.Popen(command, cwd=cfg.root, stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                 text=True, encoding="utf-8", errors="replace",
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        children[name] = child
                        started[name] = time.monotonic()
                        threading.Thread(target=drain, args=(child.stdout, name), daemon=True).start()
                        log.info("%s started", name)
                    except OSError:
                        log.exception("Cannot launch %s", name)
                        next_start[name] = time.monotonic() + 60
            time.sleep(1)
    finally:
        for child in children.values():
            stop_child(child)
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
