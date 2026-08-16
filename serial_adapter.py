"""
serial_adapter.py  -  RS-232 DNC adapter (PySerial).
====================================================================
Sibling of focas.py, for NON-Fanuc / serial-only machines:
Romi Mach 9, Siemens 802D, or an older Fanuc with no Ethernet.

INSPIRED BY (not copied from) the OpenDNC project. The logic is original,
both to avoid the GPL copyleft and to stay consistent with focas.py.

*** NEEDS THE REAL PARAMETERS OF EACH MACHINE: ***
    COM port, baud, data bits, parity, stop bits, flow control, and the
    cable pinout (null-modem). Set them in config.py (SERIAL_* section).
    The sensitive spots are marked with  # CHECK?

Implemented mode: "copy to memory" (send the whole program with the machine
in INPUT/READ). Drip-feed (running while receiving) is left for later.

Requires: pip install pyserial   (only this script needs it; FOCAS does not)
Works on 32- or 64-bit Python (it does not touch the DLL).

Run:  python serial_adapter.py
"""

import os
import time

import config

try:
    import serial  # pyserial
except ImportError as _e:                # pragma: no cover - depends on the host
    serial = None
    _IMPORT_ERROR = _e


class SerialUnavailable(RuntimeError):
    """PySerial is not installed. Raised on use, never at import time - importing
    this module must not kill a process that only wanted _wrap_tape()."""


def _require_pyserial():
    if serial is None:
        raise SerialUnavailable(
            "PySerial is missing. Install it with:  pip install pyserial"
        ) from _IMPORT_ERROR


# ---- Translate the plain values in config.py -> pyserial constants ----
def _parity(letter):
    _require_pyserial()
    return {"E": serial.PARITY_EVEN, "O": serial.PARITY_ODD,
            "N": serial.PARITY_NONE}[letter]


def _bytesize(n):
    _require_pyserial()
    return {7: serial.SEVENBITS, 8: serial.EIGHTBITS}[n]


def _stopbits(n):
    _require_pyserial()
    return {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}[n]


def open_port(read_timeout=2):
    """Open the serial port with the parameters from config.py."""
    _require_pyserial()
    flow = config.SERIAL_FLOW.lower()
    return serial.Serial(
        port     = config.SERIAL_PORT,
        baudrate = config.SERIAL_BAUD,
        bytesize = _bytesize(config.SERIAL_BYTESIZE),
        parity   = _parity(config.SERIAL_PARITY),
        stopbits = _stopbits(config.SERIAL_STOPBITS),
        xonxoff  = (flow == "xonxoff"),   # software handshake (XON/XOFF)
        rtscts   = (flow == "rtscts"),    # hardware handshake (RTS/CTS)
        timeout  = read_timeout,
        write_timeout = 15,
    )


def _wrap_tape(data):
    """Make sure the program sits between % markers (Fanuc/ISO tape format)."""
    if not data.lstrip().startswith(b"%"):
        data = b"%\r\n" + data
    if not data.rstrip().endswith(b"%"):
        data = data.rstrip() + b"\r\n%\r\n"
    return data


def send(path):
    """Send a .nc from the PC to the machine.
    The machine must already be in RECEIVE mode (INPUT/READ) before starting."""
    with open(path, "rb") as f:
        data = _wrap_tape(f.read())

    port = open_port()
    try:
        print(f"Sending {os.path.basename(path)} ({len(data)} bytes)...")
        # With XON/XOFF, pyserial honours the machine's XOFF automatically
        # (it pauses when the machine's buffer fills). # CHECK? if the machine
        # uses RTS/CTS instead.
        n = port.write(data)
        port.flush()
        print(f"OK, {n} bytes sent.")
        return n
    finally:
        port.close()


# How long the line must stay quiet, mid-program, before we call it finished.
QUIET_END_SECONDS = 6.0


def receive(out_name, wait_timeout=300):
    """Receive a program from the machine.

    On the machine, trigger PUNCH/OUTPUT after starting this command.
    Reads until the %...% pair is complete or the line goes quiet.

    `wait_timeout` bounds the wait for the FIRST byte, so this can no longer
    hang forever when nobody ever presses PUNCH. Returns the path written, or
    None if nothing arrived.
    """
    os.makedirs(config.PROGRAMS_DIR, exist_ok=True)
    dest = os.path.join(config.PROGRAMS_DIR, out_name)

    port = open_port(read_timeout=0.5)
    print(f"Waiting up to {wait_timeout:.0f}s... (trigger PUNCH/OUTPUT on the machine now)")
    buf = bytearray()
    percent = 0
    started = time.monotonic()
    last_byte_at = None
    try:
        while True:
            chunk = port.read(256)
            if chunk:
                buf.extend(chunk)
                percent += chunk.count(b"%")
                last_byte_at = time.monotonic()
                if percent >= 2:                      # complete %...% -> done
                    break
            elif last_byte_at is None:
                if time.monotonic() - started > wait_timeout:
                    print("Nothing arrived - was PUNCH/OUTPUT triggered on the machine?")
                    return None
            elif time.monotonic() - last_byte_at > QUIET_END_SECONDS:
                break                                  # quiet after data -> done
    finally:
        port.close()

    with open(dest, "wb") as f:
        f.write(bytes(buf))
    print(f"Received {len(buf)} bytes -> {dest}")
    return dest


def menu():
    print(f"Port {config.SERIAL_PORT} @ {config.SERIAL_BAUD} "
          f"{config.SERIAL_BYTESIZE}{config.SERIAL_PARITY}{config.SERIAL_STOPBITS} "
          f"| flow={config.SERIAL_FLOW}")
    while True:
        print("\n==== SERIAL DNC ====")
        print("1) Send a program to the machine")
        print("2) Receive a program from the machine")
        print("0) Quit")
        choice = input("> ").strip()
        if choice == "1":
            path = input("Path to the .nc: ").strip().strip('"')
            try:
                send(path)
            except Exception as e:
                print("ERROR:", e)
        elif choice == "2":
            name = input("Save as (e.g. 1234.nc): ").strip()
            try:
                receive(name)
            except Exception as e:
                print("ERROR:", e)
        elif choice == "0":
            break
        else:
            print("Invalid option.")


if __name__ == "__main__":
    menu()
