"""
transfer_log.py  -  Append-only record of every program that moved.
====================================================================
The web UI keeps its "last sent / last received" in memory, which is fine for
the screen and useless for everything else: it dies on restart, and the Pi
restarts on every power blip. The question that actually gets asked on the
shop floor is "was the program sent at 14:32 or not?", sometimes days later.

CSV on purpose: append-only, survives a power cut mid-write with at most one
truncated line, and anyone can open it in Excel without this toolkit.
"""

import csv
import datetime
import os
import threading

import config

FIELDS = ["timestamp", "machine", "transport", "direction", "name", "bytes", "result", "detail"]

_lock = threading.Lock()


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def record(direction, name, size=None, result="ok", detail="", transport=""):
    """Append one line. Never raises: a logging failure must not abort a
    transfer that already succeeded."""
    row = {
        "timestamp": _now(),
        "machine": config.MACHINE_NAME,
        "transport": transport,
        "direction": direction,
        "name": name,
        "bytes": "" if size is None else size,
        "result": result,
        "detail": str(detail)[:300].replace("\n", " "),
    }
    try:
        with _lock:
            path = config.LOG_FILE
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
    except Exception:
        pass
    return row


def tail(n=20):
    """Most recent entries first. Returns [] when there is no log yet."""
    path = config.LOG_FILE
    if not os.path.exists(path):
        return []
    try:
        with _lock, open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return []
    return rows[-n:][::-1]
