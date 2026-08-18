"""
hardware_check.py  -  Acceptance run against the real DNC box.
====================================================================
Everything in this toolkit is tested against a scripted fake device. That
proves the logic is self-consistent; it cannot prove the firmware agrees.
This script closes that gap in one command, and answers the three questions
the fake device cannot:

  Q1  Does the box store bytes EXACTLY as sent, or does its FatFs normalise
      line endings? The upload verification depends on the answer.
  Q2  Does a directory listing really end with the 0xFFFF terminator, or does
      the device just go quiet? list_dir() raises on silence - correct if the
      terminator is real, wrong if it is not.
  Q3  Does block validation hold up under real UDP traffic, including the
      512-byte boundary where an empty final block is sent?

SAFETY - read this before running it next to a machine:
  * It NEVER issues RunFile or StopDnc. Nothing is started, stopped or sent to
    the machine. The box is treated purely as storage.
  * It NEVER writes over an existing file. It uses a canary name and refuses
    to continue if something is already there.
  * Read-only checks run first and need no confirmation. The write checks ask
    before touching anything, and clean up after themselves.
  * With --read-only it performs no writes at all.

Run:
    python hardware_check.py --ip 192.168.1.236
    python hardware_check.py --read-only          # safest, still answers Q2

On Linux this needs root (the box only answers datagrams sent FROM UDP port 69):
    sudo python3 hardware_check.py
"""

import argparse
import datetime
import sys
import tempfile
import os

import config
import dnc_tftp as dnc

CANARY = "ZZCHECK.NC"

# Mixed line endings on purpose: if the firmware normalises, the difference
# shows up here and nowhere else.
CANARY_BODY = (
    b"%\r\n"
    b"O9999 (DNC TOOLKIT HARDWARE CHECK)\r\n"
    b"G00 X0. Y0.\n"                     # bare LF, deliberately
    b"G01 Z-1. F100.\r\n"
    b"(TAB\tAND TRAILING SPACE   )\r\n"
    b"M30\r\n"
    b"%\r\n"
)

RESULTS = []


class Skip(Exception):
    """This check could not run; not a failure of the device."""


def check(name, question=None):
    """Decorator-free step runner: returns a function that records the outcome."""
    def run(fn):
        sys.stdout.write(f"  {name:.<52}")
        sys.stdout.flush()
        try:
            detail = fn()
            status = "PASS"
        except Skip as e:
            detail, status = str(e), "SKIP"
        except Exception as e:
            detail, status = f"{type(e).__name__}: {e}", "FAIL"
        print(status)
        if detail:
            for line in str(detail).splitlines():
                print(f"        {line}")
        RESULTS.append({"name": name, "status": status,
                        "detail": str(detail or ""), "question": question})
        return status == "PASS"
    return run


def _find_readable(state):
    """Pick a file to read back: (qualified name, size, where it was found).

    The root first, then one level down. On a box in actual use the root holds
    only directories and the programs live inside one of them - which made this
    check skip on exactly the hardware it was written to exercise. Returns None
    when the box really has no file anywhere.
    """
    files = state.get("files") or []
    if files:
        target = max(files, key=lambda e: e["size"])
        return target["name"], target["size"], "root"

    for folder in [e for e in state.get("entries", []) if e["is_dir"]]:
        devpath = "0:" + folder["name"]
        try:
            entries = dnc.list_dir(devpath)
        except dnc.DncError:
            continue          # an unreadable folder is not a reason to give up
        inside = [e for e in entries
                  if not e["is_dir"] and e["name"] not in (".", "..")]
        if inside:
            target = max(inside, key=lambda e: e["size"])
            return f"{devpath}\\{target['name']}", target["size"], devpath
    return None


def main():
    ap = argparse.ArgumentParser(description="Acceptance run against the real DNC box.")
    ap.add_argument("--ip", help="override DNC_IP for this run")
    ap.add_argument("--read-only", action="store_true",
                    help="perform no writes at all (still answers Q2)")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt before the write checks")
    ap.add_argument("--report", default="hardware_check_report.txt")
    args = ap.parse_args()

    if args.ip:
        dnc.DNC_IP = args.ip

    print("=" * 62)
    print(" DNC MICRO TOOLKIT - HARDWARE ACCEPTANCE")
    print(f" target: {dnc.DNC_IP}:{dnc.DNC_PORT}   {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(" no RunFile / no StopDnc: nothing is sent to the machine")
    print("=" * 62)

    print("\n[1] Reachability (read-only)")
    state = {}

    @check("Box answers a status request", "reachable")
    def _():
        try:
            resp = dnc._rpc(dnc.pkt_status(), what="status")
        except dnc.DncError as e:
            raise RuntimeError(
                f"{e}\nIs the box powered and on the network? Is the IP right? "
                "On Linux, are you root? The box ignores anything not sent from "
                "local UDP port 69."
            )
        text = dnc.parse_status(resp)
        state["status"] = text
        return f"status = {text!r}"

    if not RESULTS[-1]["status"] == "PASS":
        print("\nCannot reach the box - stopping here.")
        return write_report(args.report, aborted=True)

    @check("Box identifies its model", "model")
    def _():
        model = dnc.parse_info(dnc.get_info())
        state["model"] = model
        if not model:
            raise RuntimeError("no model string came back")
        return f"model = {model!r}"

    print("\n[2] Directory listing  -> answers Q2")

    @check("Listing terminates on the 0xFFFF marker", "Q2")
    def _():
        try:
            entries = dnc.list_dir("")
        except dnc.DncTimeout:
            raise RuntimeError(
                "The device went QUIET instead of sending the 0xFFFF terminator.\n"
                "ANSWER TO Q2: list_dir() raising on silence is WRONG for this\n"
                "firmware. Change the timeout in list_dir() back to a break."
            )
        state["entries"] = entries
        files = [e for e in entries if not e["is_dir"]]
        state["files"] = files
        listing = "\n".join(
            f"{'DIR ' if e['is_dir'] else 'file'} {e['size']:>8}  {e['name']}"
            for e in entries[:12]) or "(empty root)"
        return f"{len(entries)} entries, terminator seen\n{listing}"

    print("\n[3] Download an existing program (read-only)  -> answers Q3")

    @check("Download matches the size in the listing", "Q3")
    def _():
        found = _find_readable(state)
        if not found:
            raise Skip("no file anywhere on the box to read - put any program on it")
        name, size, where = found
        # The size is passed ON PURPOSE. This step is about block accounting
        # holding over real UDP, not about inferring where a file ends without
        # being told - that is the 512-byte boundary check below, which is
        # deliberately kept size-free. Inferring here too would make this step
        # time out on any box whose largest file happens to be an exact
        # multiple of 512, and report "block accounting is off" against a
        # client that is behaving correctly.
        data = dnc.download(name, expected_size=size or None)
        state["read_back"] = len(data)
        if size and len(data) != size:
            raise RuntimeError(
                f"{name!r}: listing says {size} bytes, "
                f"download produced {len(data)}. Block accounting is off."
            )
        return f"{name!r} (in {where}): {len(data)} bytes, matches the listing"

    if args.read_only:
        print("\n[4] Write checks SKIPPED (--read-only)")
        RESULTS.append({"name": "Write checks", "status": "SKIP",
                        "detail": "--read-only", "question": "Q1"})
        return write_report(args.report)

    print("\n[4] Write checks  -> answers Q1")
    print(f"    These upload a file named {CANARY!r}, read it back, and delete it.")
    print("    No existing program is touched, and nothing is sent to the machine.")
    if not args.yes:
        if input("    Proceed? [y/N] ").strip().lower() not in ("y", "s", "yes", "sim"):
            print("    Skipped by the operator.")
            RESULTS.append({"name": "Write checks", "status": "SKIP",
                            "detail": "declined at the prompt", "question": "Q1"})
            return write_report(args.report)

    @check("Canary name is free (nothing will be overwritten)")
    def canary_is_free():
        if "entries" not in state:
            raise RuntimeError(
                "The directory listing did not succeed, so there is no way to know "
                "whether the canary name is taken. Refusing to write blind."
            )
        if CANARY in [e["name"] for e in state["entries"]]:
            raise RuntimeError(
                f"{CANARY!r} already exists on the box. Refusing to overwrite it. "
                "Delete it by hand first if it is left over from an earlier run."
            )
        return f"{CANARY!r} is free"

    # This guard is a GATE, not a note in the report. Without the check below it
    # printed "Refusing to overwrite it" and then the next step overwrote the
    # file anyway, and the cleanup step deleted it.
    if not canary_is_free:
        print("    Write checks skipped - the box is not safe to write to.")
        RESULTS.append({"name": "Write checks", "status": "SKIP",
                        "detail": "the canary guard did not pass", "question": "Q1"})
        return write_report(args.report)

    @check("Upload is stored byte for byte", "Q1")
    def _():
        # verify=False here on purpose: upload()'s own check would raise a bare
        # mismatch, and this run exists to DESCRIBE the mismatch, not just flag it.
        _upload_bytes(CANARY_BODY)
        got = dnc.download(CANARY, expected_size=len(CANARY_BODY))
        if got == CANARY_BODY:
            return (f"{len(CANARY_BODY)} bytes returned identical.\n"
                    "ANSWER TO Q1: the firmware preserves bytes. "
                    "Keep VERIFY_UPLOAD on.")
        diff = _describe_difference(CANARY_BODY, got)
        raise RuntimeError(
            "The box did NOT return what was sent.\n" + diff +
            "\nANSWER TO Q1: if the only differences are line endings, the "
            "firmware normalises them and verify_remote() needs to compare "
            "normalised text instead of raw bytes."
        )

    @check("512-byte boundary (empty final block)", "Q3")
    def _():
        body = (b"%\r\n" + b"(PAD)\r\n" * 80)[:512]
        _upload_bytes(body)
        # Deliberately WITHOUT expected_size: this check exists to find out what
        # the firmware does at the boundary, so it must not be told the answer.
        got = dnc.download(CANARY)
        if len(got) != 512:
            raise RuntimeError(
                f"sent exactly 512 bytes, got {len(got)} back. The empty final "
                "block handling does not match this firmware."
            )
        return "exactly 512 bytes round-tripped"

    @check("Canary is deleted afterwards")
    def _():
        dnc.delete_file(CANARY)
        left = [e["name"] for e in dnc.list_dir("")]
        if CANARY in left:
            raise RuntimeError(
                f"{CANARY!r} is still on the box after delete - remove it by hand."
            )
        return "cleaned up"

    return write_report(args.report)


def _upload_bytes(body):
    """Write `body` to the canary on the device, through a real temp file."""
    fd, path = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(body)
        dnc.upload(path, CANARY, verify=False)
    finally:
        os.remove(path)


def _describe_difference(sent, got):
    lines = [f"sent {len(sent)} bytes, got {len(got)} bytes"]
    if sent.replace(b"\r\n", b"\n") == got.replace(b"\r\n", b"\n"):
        lines.append("=> identical once line endings are normalised (CRLF vs LF)")
    for i, (a, b) in enumerate(zip(sent, got)):
        if a != b:
            lines.append(f"first difference at byte {i}: sent {a:#04x}, got {b:#04x}")
            lines.append(f"  sent context: {sent[max(0,i-12):i+12]!r}")
            lines.append(f"  got  context: {got[max(0,i-12):i+12]!r}")
            break
    return "\n".join(lines)


def write_report(path, aborted=False):
    passed = sum(r["status"] == "PASS" for r in RESULTS)
    failed = sum(r["status"] == "FAIL" for r in RESULTS)
    skipped = sum(r["status"] == "SKIP" for r in RESULTS)

    lines = [
        "DNC MICRO TOOLKIT - HARDWARE ACCEPTANCE REPORT",
        f"date   : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"target : {dnc.DNC_IP}:{dnc.DNC_PORT}",
        f"result : {passed} passed, {failed} failed, {skipped} skipped"
        + ("  (ABORTED: box unreachable)" if aborted else ""),
        "",
    ]
    for r in RESULTS:
        tag = f" [{r['question']}]" if r.get("question") else ""
        lines.append(f"[{r['status']}] {r['name']}{tag}")
        for line in r["detail"].splitlines():
            lines.append(f"       {line}")
    lines += ["", "Open questions this run was meant to settle:",
              "  Q1 does the firmware store bytes exactly as sent?",
              "  Q2 does a listing end with the 0xFFFF terminator?",
              "  Q3 does block accounting hold under real UDP traffic?"]

    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    print("\n" + "=" * 62)
    print(f" {passed} passed, {failed} failed, {skipped} skipped")
    print(f" report written to {path}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    # main() always returns an exit code: non-zero if any check failed, so this
    # can be wired into a script without reading the report by eye.
    sys.exit(main())
