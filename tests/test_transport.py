"""
Tests for the transport layer and the tape framing.

The serial tests matter more than they look. The bug they pin down was a
reopen-per-poll-cycle loop: the port sat closed for a fraction of a second on
every iteration, and at 9600 baud that window swallows a few hundred
characters - typically the '%' that marks the start of the program. Nothing in
the old code could notice, so the failure showed up as a program that simply
never arrived. "The port is opened once" is therefore a behavioural guarantee,
not an implementation detail, and it gets a test.

No hardware required.
"""

import threading
import time

import pytest

import serial_adapter
import transport as T


# ---------------------------------------------------------------- tape framing

@pytest.mark.parametrize("raw,expected", [
    (b"G01 X10",              b"%\r\nG01 X10\r\n%\r\n"),
    (b"%\r\nG01 X10\r\n%",    b"%\r\nG01 X10\r\n%"),
    (b"   G0 Z0  ",           b"%\r\n   G0 Z0\r\n%\r\n"),
    (b"%already\n%",          b"%already\n%"),
])
def test_wrap_tape(raw, expected):
    assert serial_adapter._wrap_tape(raw) == expected


def test_wrap_tape_is_idempotent():
    once = serial_adapter._wrap_tape(b"G01 X10")
    assert serial_adapter._wrap_tape(once) == once


# ---------------------------------------------------------------- fake port

class FakePort:
    """Stand-in for a pyserial Serial object."""

    def __init__(self, chunks=()):
        self.is_open = True
        self.written = bytearray()
        self.closed_times = 0
        self._chunks = list(chunks)

    def read(self, _n):
        if self._chunks:
            return self._chunks.pop(0)
        time.sleep(0.01)          # emulate the read timeout instead of spinning
        return b""

    def feed(self, data):
        self._chunks.append(data)

    def write(self, data):
        self.written.extend(data)
        return len(data)

    def flush(self):                 pass
    def reset_output_buffer(self):   pass

    def close(self):
        self.closed_times += 1
        self.is_open = False


@pytest.fixture
def serial_rig(monkeypatch):
    """A SerialTransport wired to a FakePort, counting how often it is opened."""
    monkeypatch.setattr(T, "QUIET_END_SECONDS", 0.05)
    port = FakePort()
    opens = []

    def fake_open_port(read_timeout=None):
        opens.append(read_timeout)
        return port

    monkeypatch.setattr(serial_adapter, "open_port", fake_open_port)
    return T.SerialTransport(), port, opens


def run_listener(tr, got, stop):
    def target():
        tr.listen(lambda data: (got.append(data), stop.set()), stop)
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- listening

def test_listener_opens_the_port_once_and_keeps_it_open(serial_rig):
    """The regression guard: no reopen cycle, so no window where bytes vanish."""
    tr, port, opens = serial_rig
    got, stop = [], threading.Event()
    run_listener(tr, got, stop)

    assert wait_for(lambda: len(opens) >= 1)
    port.feed(b"%\nO1234\n")
    port.feed(b"G01 X10\n")
    port.feed(b"%\n")

    assert wait_for(lambda: got), "the program never arrived"
    stop.set()

    assert got[0] == b"%\nO1234\nG01 X10\n%\n"
    assert len(opens) == 1, f"the port was opened {len(opens)} times, expected once"
    assert port.closed_times == 0, "the port was closed while listening"


def test_listener_survives_a_dead_port_and_reopens(serial_rig, monkeypatch):
    """An unplugged FTDI must not kill the thread - it must recover."""
    tr, port, opens = serial_rig
    boom = {"armed": True}
    original_read = port.read

    def flaky_read(n):
        if boom["armed"]:
            boom["armed"] = False
            raise OSError("device disconnected")
        return original_read(n)

    monkeypatch.setattr(port, "read", flaky_read)

    got, stop = [], threading.Event()
    run_listener(tr, got, stop)
    assert wait_for(lambda: len(opens) >= 2, timeout=5), "never reopened after the failure"

    port.feed(b"%\nO9\n%\n")
    assert wait_for(lambda: got, timeout=5), "did not recover after reopening"
    stop.set()
    assert got[0] == b"%\nO9\n%\n"


def test_listener_discards_line_noise_before_the_program(serial_rig):
    tr, port, opens = serial_rig
    got, stop = [], threading.Event()
    run_listener(tr, got, stop)
    assert wait_for(lambda: len(opens) >= 1)

    port.feed(b"\x00\xff garbage ")     # no '%' yet: not a program
    port.feed(b"%\nO7\n%\n")

    assert wait_for(lambda: got)
    stop.set()
    assert got[0] == b"%\nO7\n%\n"


def test_listener_ends_a_program_after_silence(serial_rig):
    """A machine that punches without a closing '%' still finishes."""
    tr, port, opens = serial_rig
    got, stop = [], threading.Event()
    run_listener(tr, got, stop)
    assert wait_for(lambda: len(opens) >= 1)

    port.feed(b"%\nO1\nG01\n")          # only one '%', then goes quiet
    assert wait_for(lambda: got, timeout=3)
    stop.set()
    assert got[0] == b"%\nO1\nG01\n"


# ---------------------------------------------------------------- sending

def test_send_wraps_the_program_in_tape_markers(serial_rig, tmp_path):
    tr, port, _ = serial_rig
    src = tmp_path / "1"
    src.write_bytes(b"G01 X10")
    assert tr.send(str(src)) == len(b"%\r\nG01 X10\r\n%\r\n")
    assert bytes(port.written) == b"%\r\nG01 X10\r\n%\r\n"


def test_send_refuses_while_a_program_is_arriving(serial_rig, tmp_path, monkeypatch):
    """Sending mid-reception would put both programs on the wire at once.

    The other tests shorten the quiet timeout so they finish fast; this one puts
    it back, because the whole point is to catch the transport WHILE a program
    is still open - which is exactly the window that timeout defines.
    """
    tr, port, opens = serial_rig
    monkeypatch.setattr(T, "QUIET_END_SECONDS", 30.0)
    src = tmp_path / "1"
    src.write_bytes(b"G01 X10")

    got, stop = [], threading.Event()
    run_listener(tr, got, stop)
    assert wait_for(lambda: len(opens) >= 1)

    port.feed(b"%\nO1234\n")            # starts, does not finish
    assert wait_for(lambda: tr._receiving), "reception never registered"

    with pytest.raises(T.TransportBusy):
        tr.send(str(src))

    stop.set()
    assert bytes(port.written) == b"", "nothing should have gone out on the wire"


# ---------------------------------------------------------------- box + factory

def test_dnc_box_transport_sends_with_verification(monkeypatch, tmp_path):
    import dnc_tftp
    calls = {}

    def fake_upload(local, remote, verify):
        calls.update(local=local, remote=remote, verify=verify)
        return 42

    monkeypatch.setattr(dnc_tftp, "upload", fake_upload)
    src = tmp_path / "O1234.nc"
    src.write_bytes(b"x")

    assert T.DncBoxTransport(verify=True).send(str(src)) == 42
    assert calls["remote"] == "O1234.nc"
    assert calls["verify"] is True


def test_dnc_box_browse_maps_to_list_dir(monkeypatch):
    import dnc_tftp
    monkeypatch.setattr(dnc_tftp, "list_dir",
                        lambda p: [{"name": "O1", "size": 10, "is_dir": False}])
    assert T.DncBoxTransport().browse()[0]["name"] == "O1"


def test_capabilities_differ_per_transport():
    assert T.SerialTransport.can_listen and not T.SerialTransport.can_browse
    assert T.DncBoxTransport.can_browse and not T.DncBoxTransport.can_listen
    assert T.FocasTransport.can_fetch and not T.FocasTransport.can_listen


def test_build_rejects_an_unknown_transport():
    with pytest.raises(T.TransportError, match="Unknown transport"):
        T.build("carrier-pigeon")


def test_build_uses_the_configured_transport(monkeypatch):
    import config
    monkeypatch.setattr(config, "TRANSPORT", "serial")
    assert isinstance(T.build(), T.SerialTransport)
