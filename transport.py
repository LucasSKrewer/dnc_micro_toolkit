"""
transport.py  -  One interface over the three ways of reaching a machine.
====================================================================
FOCAS, RS-232 and the WiFi DNC box each have their own quirks, but from the
outside a shop-floor tool only ever wants two things: push this program to the
machine, and take whatever the machine sends back. This module exposes exactly
that, so dnc_web.py can drive any of the three instead of only the serial one.

Capability flags, because the transports genuinely differ:
  can_listen  the machine pushes on its own (serial PUNCH/OUTPUT). Nothing to
              poll - something has to sit there with the port open.
  can_browse  the device holds a filesystem we can list (the DNC box).
  can_fetch   we can pull a named program on demand (box, FOCAS by O-number).
"""

import os
import threading
import time

import config


class TransportError(RuntimeError):
    """Any failure specific to moving a program over this transport."""


class TransportBusy(TransportError):
    """The line is in the middle of something that must not be interrupted."""


class Transport:
    name = "transport"
    can_listen = False
    can_browse = False
    can_fetch = False

    def send(self, local_path, remote_name=None):
        """Push a program to the machine. Returns the number of bytes sent."""
        raise NotImplementedError

    def browse(self):
        """List what is on the device: dicts with name / size / is_dir."""
        raise NotImplementedError

    def fetch(self, remote_name, dest_path):
        """Pull a named program off the device. Returns bytes written."""
        raise NotImplementedError

    def listen(self, on_program, stop_event):
        """Block until stop_event is set, calling on_program(bytes) per program."""
        raise NotImplementedError

    def describe(self):
        return self.name

    def close(self):
        pass


# --------------------------------------------------------------------- serial

LISTEN_READ_TIMEOUT = 0.2   # short, so a send never waits long for the lock
QUIET_END_SECONDS   = 6.0   # silence after data = the machine finished punching


class SerialTransport(Transport):
    """RS-232, for machines that only speak serial.

    The port is opened ONCE and kept open for the life of the process.

    That is not a detail. The previous version reopened the port on every poll
    cycle, leaving it shut for a fraction of a second each time; at 9600 baud a
    0.3 s gap swallows roughly 300 characters, and the characters most likely to
    be swallowed are the first ones - including the leading '%' that marks the
    start of a program. It also toggled DTR/RTS on the machine every cycle.
    """

    name = "serial"
    can_listen = True

    def __init__(self):
        import serial_adapter
        self._sa = serial_adapter
        self._lock = threading.RLock()
        self._port = None
        self._receiving = False

    # -- port lifecycle (callers hold the lock) --
    def _port_open(self):
        if self._port is None or not self._port.is_open:
            self._port = self._sa.open_port(read_timeout=LISTEN_READ_TIMEOUT)
        return self._port

    def _drop_port(self):
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None

    def describe(self):
        return (f"serial {config.SERIAL_PORT} @ {config.SERIAL_BAUD} "
                f"{config.SERIAL_BYTESIZE}{config.SERIAL_PARITY}{config.SERIAL_STOPBITS} "
                f"flow={config.SERIAL_FLOW}")

    def send(self, local_path, remote_name=None):
        with open(local_path, "rb") as f:
            data = self._sa._wrap_tape(f.read())
        with self._lock:
            if self._receiving:
                raise TransportBusy(
                    "A program is arriving from the machine right now. Sending would "
                    "collide with it on the same wire - wait for it to finish."
                )
            port = self._port_open()
            port.reset_output_buffer()
            n = port.write(data)
            port.flush()
        return n

    def listen(self, on_program, stop_event):
        buf = bytearray()
        percent = 0
        last_byte_at = None

        while not stop_event.is_set():
            try:
                with self._lock:
                    chunk = self._port_open().read(256)
                    now = time.monotonic()
                    if chunk:
                        if not buf:
                            start = chunk.find(b"%")
                            if start < 0:
                                continue          # line noise before the program
                            chunk = chunk[start:]  # begin exactly at the '%'
                        buf.extend(chunk)
                        percent += chunk.count(b"%")
                        last_byte_at = now
                        self._receiving = True
                    finished = bool(buf) and (
                        percent >= 2                                   # %...% closed
                        or (last_byte_at is not None
                            and now - last_byte_at > QUIET_END_SECONDS)
                    )
                    if finished:
                        program, buf = bytes(buf), bytearray()
                        percent, last_byte_at = 0, None
                        self._receiving = False
                    else:
                        program = None
            except Exception as e:
                # FTDI unplugged, port died mid-read... whatever it was, this
                # thread must NEVER die: if it does, automatic receiving stops
                # forever and nobody finds out until a program goes missing.
                with self._lock:
                    self._drop_port()
                    self._receiving = False
                buf, percent, last_byte_at = bytearray(), 0, None
                _sleep_until(stop_event, 2.0)
                continue

            if program:
                on_program(program)          # outside the lock: never block the port

    def close(self):
        with self._lock:
            self._drop_port()


def _sleep_until(stop_event, seconds):
    stop_event.wait(seconds)


# ------------------------------------------------------------------- DNC box

class DncBoxTransport(Transport):
    """The WiFi DNC box, over its reverse-engineered protocol."""

    name = "dnc-box"
    can_browse = True
    can_fetch = True

    def __init__(self, verify=True):
        import dnc_tftp
        self._dnc = dnc_tftp
        self.verify = verify

    def describe(self):
        return f"DNC box at {self._dnc.DNC_IP}:{self._dnc.DNC_PORT}"

    def send(self, local_path, remote_name=None):
        remote = remote_name or os.path.basename(local_path)
        return self._dnc.upload(local_path, remote, verify=self.verify)

    def browse(self):
        return self._dnc.list_dir("")

    def fetch(self, remote_name, dest_path):
        return len(self._dnc.download(remote_name, dest_path))


# --------------------------------------------------------------------- FOCAS

class FocasTransport(Transport):
    """Fanuc controls with Ethernet/FOCAS enabled.

    Needs 32-bit Python plus Fanuc's Fwlib32.dll, so everything is imported
    lazily - the other two transports must keep working on a 64-bit machine
    (or a Raspberry Pi) that will never have that DLL.
    """

    name = "focas"
    can_fetch = True

    def __init__(self):
        import focas
        import focas_transfer
        self._focas = focas
        self._ft = focas_transfer
        self._handle = None
        self._lock = threading.RLock()

    def describe(self):
        return f"FOCAS at {config.CNC_IP}:{config.CNC_PORT}"

    def _connect(self):
        if self._handle is None:
            self._handle = self._focas.connect()
        return self._handle

    def send(self, local_path, remote_name=None):
        with self._lock:
            self._ft.send(self._connect(), local_path)
        return os.path.getsize(local_path)

    def fetch(self, remote_name, dest_path):
        """remote_name is the O-number on the control, e.g. 'O1234'."""
        with self._lock:
            written = self._ft.receive(self._connect(), remote_name)
        if os.path.abspath(written) != os.path.abspath(dest_path):
            os.replace(written, dest_path)
        return os.path.getsize(dest_path)

    def close(self):
        with self._lock:
            if self._handle is not None:
                self._focas.disconnect(self._handle)
                self._handle = None


# ------------------------------------------------------------------- factory

TRANSPORTS = {
    "serial": SerialTransport,
    "dnc-box": DncBoxTransport,
    "focas": FocasTransport,
}


def build(name=None):
    """Create the transport named in config.TRANSPORT (or the argument)."""
    name = (name or getattr(config, "TRANSPORT", "serial")).lower()
    try:
        factory = TRANSPORTS[name]
    except KeyError:
        raise TransportError(
            f"Unknown transport {name!r}. Valid values for TRANSPORT in config.py: "
            + ", ".join(sorted(TRANSPORTS))
        ) from None
    return factory()
