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
        self.write_timeout = 15
        self.timeout_during_write = []
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
        self.timeout_during_write.append(self.write_timeout)
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


def test_listener_marks_the_line_busy_while_a_program_arrives(serial_rig, monkeypatch):
    """Half of the busy guard: the listener must publish that the wire is in use."""
    tr, port, opens = serial_rig
    monkeypatch.setattr(T, "QUIET_END_SECONDS", 30.0)   # keep the program open

    got, stop = [], threading.Event()
    run_listener(tr, got, stop)
    assert wait_for(lambda: len(opens) >= 1)

    assert tr._receiving is False
    port.feed(b"%\nO1234\n")                            # starts, does not finish
    assert wait_for(lambda: tr._receiving), "reception never registered"
    stop.set()


def test_send_refuses_when_the_line_is_busy(serial_rig, tmp_path):
    """The other half: with the flag set, sending must be refused.

    Deliberately NOT driven through a live listener thread. Doing that means
    asserting on a state another thread can leave at any moment, and it failed
    on a loaded CI runner while passing locally every time. The guard and the
    flag are two separate properties, so they get two separate tests instead of
    one racy test that only sometimes checks either.
    """
    tr, port, _ = serial_rig
    src = tmp_path / "1"
    src.write_bytes(b"G01 X10")

    tr._receiving = True

    with pytest.raises(T.TransportBusy):
        tr.send(str(src))
    assert bytes(port.written) == b"", "nothing should have gone out on the wire"


# ---------------------------------------------------------------- drip-feed
#
# Drip-feed runs while the machine is CUTTING. The properties worth pinning are
# the ones that decide what happens when something goes wrong mid-job: that an
# abort actually stops the wire, that it reports how far it got (the operator
# needs to know where the program stopped), and that the long write timeout it
# installs is put back afterwards - otherwise the next ordinary send inherits a
# five-minute timeout and a dead cable looks like a hang.

def test_drip_feed_streams_the_whole_program(serial_rig, tmp_path):
    tr, port, opens = serial_rig
    body = b"G01 X10\n" * 100
    src = tmp_path / "big.nc"
    src.write_bytes(body)

    sent = tr.drip_feed(str(src))

    expected = serial_adapter._wrap_tape(body)
    assert bytes(port.written) == expected
    assert sent == len(expected)
    assert len(opens) == 1, "drip-feed reopened the port"


def test_drip_feed_writes_in_chunks_not_one_blob(serial_rig, tmp_path):
    tr, port, _ = serial_rig
    src = tmp_path / "big.nc"
    src.write_bytes(b"G01 X10\n" * 100)

    tr.drip_feed(str(src))

    # one write() per chunk: that is the granularity an abort can act on
    assert len(port.timeout_during_write) > 1


def test_drip_feed_reports_progress_to_the_end(serial_rig, tmp_path):
    tr, port, _ = serial_rig
    body = b"G01 X10\n" * 100
    src = tmp_path / "big.nc"
    src.write_bytes(body)

    seen = []
    tr.drip_feed(str(src), on_progress=lambda sent, total: seen.append((sent, total)))

    total = len(serial_adapter._wrap_tape(body))
    assert seen[-1] == (total, total)
    assert [s for s, _ in seen] == sorted(s for s, _ in seen), "progress went backwards"


def test_drip_feed_aborts_mid_job_and_says_where_it_stopped(serial_rig, tmp_path):
    tr, port, _ = serial_rig
    src = tmp_path / "big.nc"
    src.write_bytes(b"G01 X10\n" * 500)

    stop = threading.Event()

    def brake(sent, total):
        if sent >= 512:
            stop.set()

    with pytest.raises(serial_adapter.DripAborted) as excinfo:
        tr.drip_feed(str(src), on_progress=brake, stop_event=stop)

    assert excinfo.value.sent >= 512
    assert excinfo.value.total > excinfo.value.sent
    assert len(port.written) == excinfo.value.sent, "kept writing after the abort"


def test_drip_feed_raises_the_write_timeout_then_restores_it(serial_rig, tmp_path):
    """A control can hold XOFF for minutes during a slow pass; 15s would abort
    a healthy job. But the raised timeout must not leak into the next send."""
    tr, port, _ = serial_rig
    src = tmp_path / "big.nc"
    src.write_bytes(b"G01 X10\n" * 40)

    tr.drip_feed(str(src))

    assert set(port.timeout_during_write) == {serial_adapter.DRIP_WRITE_TIMEOUT}
    assert port.write_timeout == 15, "the long drip timeout leaked out of the job"


def test_drip_feed_restores_the_timeout_even_when_aborted(serial_rig, tmp_path):
    tr, port, _ = serial_rig
    src = tmp_path / "big.nc"
    src.write_bytes(b"G01 X10\n" * 500)

    stop = threading.Event()
    with pytest.raises(serial_adapter.DripAborted):
        tr.drip_feed(str(src), on_progress=lambda s, t: stop.set(), stop_event=stop)

    assert port.write_timeout == 15


def test_drip_feed_refuses_when_the_line_is_busy(serial_rig, tmp_path):
    """Same split as the send guard: no live thread to race against."""
    tr, port, _ = serial_rig
    src = tmp_path / "big.nc"
    src.write_bytes(b"G01 X10\n" * 10)

    tr._receiving = True

    with pytest.raises(T.TransportBusy):
        tr.drip_feed(str(src))
    assert bytes(port.written) == b""
    assert port.write_timeout == 15, "the drip timeout leaked out of a refused job"


def test_pace_throttles_the_stream(serial_rig, tmp_path):
    """The crude brake for a control whose handshake cannot be trusted."""
    tr, port, _ = serial_rig
    src = tmp_path / "big.nc"
    src.write_bytes(b"X" * (serial_adapter.DRIP_CHUNK * 3))

    started = time.monotonic()
    tr.drip_feed(str(src), pace=0.05)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.10, f"pace was ignored (took {elapsed:.3f}s)"


def test_only_serial_advertises_drip_feed():
    assert T.SerialTransport.can_drip
    assert not T.DncBoxTransport.can_drip
    assert not T.FocasTransport.can_drip


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
