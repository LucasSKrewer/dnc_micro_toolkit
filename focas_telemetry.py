"""
focas_telemetry.py  -  Read what the machine is doing, over FOCAS.
====================================================================
This is the payoff the README has been claiming since the first commit: the
same library that pushes a program also reports run state, the program in
execution, alarms, feedrate and spindle speed. Transfer was never the
interesting part - knowing whether the machine is cutting, idle or alarmed is.

STRICTLY READ-ONLY. Nothing here writes a parameter, starts a cycle or clears
an alarm. Every function reads and returns.

*** NONE OF THIS HAS RUN AGAINST A REAL CONTROL. ***

Two reasons to expect friction on the first run:

  1. The structures below are version-dependent. ODBST in particular has a
     different field order across control series, and the wrong layout does not
     crash - it returns plausible nonsense. If a field reads as garbage while
     its neighbours look sane, the layout is the first suspect. Marked # CHECK?
  2. Not every control supports every call. That is why each reading is taken
     independently: one unsupported function returns "unavailable" for its own
     row instead of taking the whole snapshot down with it.

Run:  py -3-32 focas_telemetry.py            (one snapshot)
      py -3-32 focas_telemetry.py --watch 2  (refresh every 2 seconds)
"""

import argparse
import ctypes
import datetime
import sys
import time

import config
import focas


# ---- Structures (see the warning above: layouts vary by control series) ----

class ODBST(ctypes.Structure):
    """Status info. # CHECK? field order differs on 15i and some 0i variants."""
    _pack_ = 1
    _fields_ = [
        ("dummy",     ctypes.c_short),
        ("tmmode",    ctypes.c_short),   # T/M mode
        ("aut",       ctypes.c_short),   # selected automatic mode
        ("run",       ctypes.c_short),   # running status
        ("motion",    ctypes.c_short),   # axis moving / dwell
        ("mstb",      ctypes.c_short),   # M/S/T/B function status
        ("emergency", ctypes.c_short),
        ("alarm",     ctypes.c_short),
        ("edit",      ctypes.c_short),   # editing status
    ]


class ODBPRO(ctypes.Structure):
    """Program numbers: data = running, mdata = main."""
    _pack_ = 1
    _fields_ = [("dummy", ctypes.c_short * 2),
                ("data", ctypes.c_long), ("mdata", ctypes.c_long)]


class ODBALM(ctypes.Structure):
    """Alarm status as a bit mask."""
    _pack_ = 1
    _fields_ = [("dummy", ctypes.c_short * 2), ("data", ctypes.c_short)]


class ODBACT(ctypes.Structure):
    """Actual feedrate / actual spindle speed."""
    _pack_ = 1
    _fields_ = [("dummy", ctypes.c_short * 2), ("data", ctypes.c_long)]


# ---- Decoding tables (from the FOCAS specification) ----

AUT_MODE = {0: "MDI", 1: "MEM", 2: "----", 3: "EDIT", 4: "HND", 5: "JOG",
            6: "Teach in JOG", 7: "Teach in HND", 8: "INC", 9: "REF", 10: "RMT"}

RUN_STATE = {0: "reset", 1: "stop", 2: "hold", 3: "running", 4: "restarting"}

MOTION = {0: "idle", 1: "moving", 2: "dwell"}

EMERGENCY = {0: "normal", 1: "EMERGENCY STOP", 2: "reset"}

EDIT_STATE = {0: "idle", 1: "edit", 2: "search", 3: "output", 4: "input",
              5: "compare", 6: "label skip", 7: "restart", 8: "hpcc",
              9: "PTRR", 10: "RVRS", 11: "RTRY", 12: "RVED", 13: "handle"}

# Alarm bits, in the order the specification lists them.
ALARM_BITS = [
    (0,  "SW",  "parameter switch on"),
    (1,  "PW",  "power must be cycled"),
    (2,  "IO",  "I/O error"),
    (3,  "PS",  "foreground P/S"),
    (4,  "OT",  "overtravel"),
    (5,  "OH",  "overheat"),
    (6,  "SV",  "servo"),
    (7,  "SR",  "data I/O"),
    (8,  "MC",  "macro"),
    (9,  "SP",  "spindle"),
    (10, "PU",  "punch / purge"),
    (11, "FS",  "fuse blown"),
    (13, "DS",  "external alarm"),
    (14, "IE",  "malfunction prevention"),
    (15, "BG",  "background P/S"),
]


def decode_alarms(mask):
    """Turn the alarm bit mask into a readable list. Empty means no alarm."""
    if not mask:
        return []
    return [f"{code} ({text})" for bit, code, text in ALARM_BITS if mask & (1 << bit)]


def describe(table, value, unknown="?"):
    return table.get(value, f"{unknown}{value}")


# ---- Individual readings ----------------------------------------------------
# Each one is separate on purpose: an unsupported call must cost its own row,
# not the whole snapshot.

def _declare(lib):
    lib.cnc_statinfo.argtypes = [ctypes.c_ushort, ctypes.POINTER(ODBST)]
    lib.cnc_statinfo.restype = ctypes.c_short
    lib.cnc_rdprgnum.argtypes = [ctypes.c_ushort, ctypes.POINTER(ODBPRO)]
    lib.cnc_rdprgnum.restype = ctypes.c_short
    lib.cnc_alarm.argtypes = [ctypes.c_ushort, ctypes.POINTER(ODBALM)]
    lib.cnc_alarm.restype = ctypes.c_short
    lib.cnc_actf.argtypes = [ctypes.c_ushort, ctypes.POINTER(ODBACT)]
    lib.cnc_actf.restype = ctypes.c_short
    lib.cnc_acts.argtypes = [ctypes.c_ushort, ctypes.POINTER(ODBACT)]
    lib.cnc_acts.restype = ctypes.c_short
    lib.cnc_rdexecprog.argtypes = [ctypes.c_ushort, ctypes.POINTER(ctypes.c_ushort),
                                   ctypes.POINTER(ctypes.c_short), ctypes.c_char_p]
    lib.cnc_rdexecprog.restype = ctypes.c_short
    return lib


def read_status(lib, handle):
    st = ODBST()
    ret = lib.cnc_statinfo(handle, ctypes.byref(st))
    if ret != focas.EW_OK:
        raise focas.FocasError(f"cnc_statinfo failed (code {ret})")
    return {
        "mode": describe(AUT_MODE, st.aut),
        "run": describe(RUN_STATE, st.run),
        "motion": describe(MOTION, st.motion),
        "emergency": describe(EMERGENCY, st.emergency),
        "edit": describe(EDIT_STATE, st.edit),
        "alarm_flag": bool(st.alarm),
    }


def read_program_numbers(lib, handle):
    pro = ODBPRO()
    ret = lib.cnc_rdprgnum(handle, ctypes.byref(pro))
    if ret != focas.EW_OK:
        raise focas.FocasError(f"cnc_rdprgnum failed (code {ret})")
    return {"running": pro.data, "main": pro.mdata}


def read_alarms(lib, handle):
    alm = ODBALM()
    ret = lib.cnc_alarm(handle, ctypes.byref(alm))
    if ret != focas.EW_OK:
        raise focas.FocasError(f"cnc_alarm failed (code {ret})")
    return {"mask": alm.data, "active": decode_alarms(alm.data)}


def read_feedrate(lib, handle):
    act = ODBACT()
    ret = lib.cnc_actf(handle, ctypes.byref(act))
    if ret != focas.EW_OK:
        raise focas.FocasError(f"cnc_actf failed (code {ret})")
    return {"feed": act.data}          # mm/min (or inch/min, per the control)


def read_spindle(lib, handle):
    act = ODBACT()
    ret = lib.cnc_acts(handle, ctypes.byref(act))
    if ret != focas.EW_OK:
        raise focas.FocasError(f"cnc_acts failed (code {ret})")
    return {"rpm": act.data}


def read_executing_block(lib, handle):
    """The block the control is executing right now, as text."""
    size = 1024
    buf = ctypes.create_string_buffer(size + 1)
    length = ctypes.c_ushort(size)
    blknum = ctypes.c_short(0)
    ret = lib.cnc_rdexecprog(handle, ctypes.byref(length), ctypes.byref(blknum), buf)
    if ret != focas.EW_OK:
        raise focas.FocasError(f"cnc_rdexecprog failed (code {ret})")
    text = buf.raw[:length.value].decode("ascii", "replace")
    return {"block_number": blknum.value,
            "text": text.strip().splitlines()[0] if text.strip() else ""}


READINGS = [
    ("status",    read_status),
    ("program",   read_program_numbers),
    ("alarms",    read_alarms),
    ("feedrate",  read_feedrate),
    ("spindle",   read_spindle),
    ("block",     read_executing_block),
]


def snapshot(handle, lib=None):
    """Take every reading. Returns {name: {"ok": bool, ...}}.

    Never raises for an unsupported call: a control that lacks cnc_rdexecprog
    should still report its run state.
    """
    lib = _declare(lib or focas.get_lib())
    out = {"taken_at": datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")}
    for name, reader in READINGS:
        try:
            out[name] = dict(reader(lib, handle), ok=True)
        except Exception as e:
            out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out


def format_snapshot(snap):
    """One readable block. Unavailable readings say so instead of vanishing."""
    lines = [f"--- {config.CNC_IP}  {snap['taken_at']} ---"]

    def row(label, value):
        lines.append(f"  {label:<12} {value}")

    st = snap["status"]
    if st["ok"]:
        row("state", f"{st['run']} / {st['mode']} / {st['motion']}")
        if st["emergency"] != "normal":
            row("EMERGENCY", st["emergency"])
    else:
        row("state", f"unavailable - {st['error']}")

    pr = snap["program"]
    row("program", f"O{pr['running']} (main O{pr['main']})" if pr["ok"]
        else f"unavailable - {pr['error']}")

    bl = snap["block"]
    if bl["ok"]:
        row("block", f"N{bl['block_number']}  {bl['text']}")

    fd, sp = snap["feedrate"], snap["spindle"]
    if fd["ok"] or sp["ok"]:
        feed = f"{fd['feed']}" if fd["ok"] else "?"
        rpm = f"{sp['rpm']}" if sp["ok"] else "?"
        row("cutting", f"F{feed}  S{rpm}")

    al = snap["alarms"]
    if al["ok"]:
        row("alarms", ", ".join(al["active"]) if al["active"] else "none")
    else:
        row("alarms", f"unavailable - {al['error']}")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Read machine state over FOCAS (read-only).")
    ap.add_argument("--watch", type=float, metavar="SECONDS",
                    help="keep refreshing every SECONDS until Ctrl+C")
    args = ap.parse_args()

    print(f"Connecting to {config.CNC_IP}:{config.CNC_PORT} ...")
    handle = focas.connect()
    try:
        while True:
            print(format_snapshot(snapshot(handle)))
            if not args.watch:
                break
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        focas.disconnect(handle)


if __name__ == "__main__":
    sys.exit(main() or 0)
