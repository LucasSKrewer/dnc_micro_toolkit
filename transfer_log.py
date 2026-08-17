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


# How much of the end of the file to read. Comfortably more than any plausible
# page of entries, and a hard ceiling on the work a page render can cost.
TAIL_BYTES = 64 * 1024


def tail(n=20):
    """Most recent entries first. Returns [] when there is no log yet.

    Reads only the END of the file. The panel renders this on every load and
    reloads itself every 20 seconds, so parsing the whole log would mean a Pi
    re-reading a year of history several times a minute, for twelve rows.

    Safe to slice by lines because record() flattens newlines out of every
    field, so no entry can span more than one line.
    """
    path = config.LOG_FILE
    if not os.path.exists(path):
        return []
    try:
        with _lock, open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - TAIL_BYTES))
            blob = f.read().decode("utf-8", "replace")
    except Exception:
        return []

    lines = blob.splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]            # the first line is probably cut in half
    rows = []
    for fields in csv.reader(lines[-(n + 1):]):
        if not fields or fields[0] == FIELDS[0]:      # skip the header row
            continue
        rows.append(dict(zip(FIELDS, fields)))
    return rows[-n:][::-1]
