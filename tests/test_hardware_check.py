"""
Tests for the hardware acceptance script.

The script's whole job is to be run once, standing next to a machine, and come
back with an answer. A script that crashes halfway through is worse than no
script - so it is exercised here against a stateful simulator of the box,
including the two firmware behaviours it exists to detect. Each scenario
asserts the DIAGNOSIS, not just the exit code: a run that fails without saying
which of Q1/Q2/Q3 it settled would be useless at the machine.

No hardware required.
"""

import socket
import struct

import pytest

import dnc_tftp as d
import hardware_check as hc


class BoxSimulator:
    """A stateful stand-in for the Micro DNC 2, speaking the mapped protocol.

    `normalise_line_endings` and `quiet_instead_of_terminator` reproduce the two
    firmware behaviours we cannot rule out from a desk.
    """

    def __init__(self, files=None, normalise_line_endings=False,
                 quiet_instead_of_terminator=False):
        self.files = dict(files or {})
        self.normalise = normalise_line_endings
        self.quiet_end = quiet_instead_of_terminator
        self._inbox = []
        self._reading = None       # name currently open for download
        self._writing = None       # name currently open for upload
        self._buffer = bytearray()

    # -- socket interface --
    def sendto(self, packet, _addr):
        reply = self._handle(packet)
        if reply is not None:
            self._inbox.append(reply)

    def recvfrom(self, _n):
        if not self._inbox:
            # socket.timeout, NOT the builtin TimeoutError: they are the same
            # class only from Python 3.10 on. Under 3.9 the builtin sails past
            # the except socket.timeout in _exchange, and the toolkit reports a
            # bare TimeoutError instead of its own DncTimeout.
            raise socket.timeout()
        return self._inbox.pop(0), ("sim", 69)

    def settimeout(self, _t):  pass
    def close(self):           pass

    # -- protocol --
    def _handle(self, pkt):
        op = d.opcode_of(pkt)

        if op == d.OP_GETSTATUS:
            return (struct.pack(">H", 51) + b"\x00" * 9
                    + b"192.168.1.236|No File Selected\x00")
        if op == d.OP_GETINFO:
            return struct.pack(">H", 20) + b"\x00" * 4 + b"MICRO DNC2\x00"
        if op == d.OP_OPENDIR:
            return struct.pack(">H", 7)

        if op == d.OP_READDIR:
            index = struct.unpack(">H", pkt[2:4])[0]
            names = sorted(self.files)
            if index < len(names):
                return self._entry(names[index], index)
            if self.quiet_end:
                return None                      # firmware just stops answering
            return self._entry("", d.END_OF_DIR)

        if op == d.OP_DL_OPEN:
            name = pkt[2:].split(b"\x00")[0].decode()
            if name not in self.files:
                return None
            self._reading = name
            return struct.pack(">H", 55)

        if op == d.OP_REQDATA:
            block = struct.unpack(">I", pkt[2:6])[0]
            data = self.files[self._reading]
            chunk = data[(block - 1) * d.BLOCK: block * d.BLOCK]
            return struct.pack(">H", d.OP_DATA_REPLY) + struct.pack(">I", block) + chunk

        if op == d.OP_WRQ:
            self._writing = pkt[2:].split(b"\x00")[0].decode()
            self._buffer = bytearray()
            return struct.pack(">H", d.OP_ACK)

        if op == d.OP_DATA:
            block = struct.unpack(">I", pkt[2:6])[0]
            self._buffer.extend(pkt[6:])
            return struct.pack(">H", d.OP_ACK) + struct.pack(">I", block)

        if op == d.OP_UP_CLOSE:
            stored = bytes(self._buffer)
            if self.normalise:
                stored = stored.replace(b"\r\n", b"\n")
            self.files[self._writing] = stored
            self._writing = None
            return struct.pack(">H", 53)

        if op == d.OP_DELFILE:
            name = pkt[2:].split(b"\x00")[0].decode()
            self.files.pop(name, None)
            return struct.pack(">H", 13)

        return None

    def _entry(self, name, seq):
        size = len(self.files.get(name, b""))
        return (struct.pack(">H", d.OP_DIR_ENTRY) + b"\x00" * 4 + name.encode() + b"\x00"
                + struct.pack(">H", seq) + bytes([0x20]) + struct.pack(">I", size))


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Point the toolkit at a simulator and give the script a scratch report."""
    monkeypatch.setattr(d, "TIMEOUT", 0.01)
    monkeypatch.setattr(d, "RESENDS", 2)
    monkeypatch.setattr(hc, "RESULTS", [])
    report = tmp_path / "report.txt"

    def install(box):
        monkeypatch.setattr(d, "_sock", lambda: box)
        monkeypatch.setattr("sys.argv",
                            ["hardware_check.py", "--yes", "--report", str(report)])
        return box

    return install, report


def outcomes():
    return {r["name"]: r for r in hc.RESULTS}


def test_a_healthy_box_passes_every_check(rig, capsys):
    install, report = rig
    install(BoxSimulator(files={"O1234.NC": b"%\nG01 X10\n%\n" * 60}))

    assert hc.main() == 0

    statuses = {r["status"] for r in hc.RESULTS}
    assert statuses == {"PASS"}, outcomes()

    text = report.read_text(encoding="utf-8")
    assert "0 failed" in text
    assert "the firmware preserves bytes" in text


def test_it_detects_line_ending_normalisation(rig):
    """Q1: the failure mode that would make VERIFY_UPLOAD fire on good programs."""
    install, report = rig
    install(BoxSimulator(files={"O1.NC": b"%\nX\n%\n"}, normalise_line_endings=True))

    assert hc.main() == 1

    upload = outcomes()["Upload is stored byte for byte"]
    assert upload["status"] == "FAIL"
    assert upload["question"] == "Q1"
    assert "identical once line endings are normalised" in upload["detail"]
    assert "first difference at byte" in upload["detail"]
    assert "verify_remote() needs to compare" in upload["detail"]


def test_it_detects_a_listing_that_never_terminates(rig):
    """Q2: the case where list_dir() raising on silence is the wrong call."""
    install, report = rig
    install(BoxSimulator(files={"O1.NC": b"%\nX\n%\n"},
                         quiet_instead_of_terminator=True))

    hc.main()

    listing = outcomes()["Listing terminates on the 0xFFFF marker"]
    assert listing["status"] == "FAIL"
    assert listing["question"] == "Q2"
    assert "went QUIET" in listing["detail"]
    assert "is WRONG for this" in listing["detail"]


def test_it_stops_cleanly_when_the_box_is_unreachable(rig, capsys):
    install, report = rig

    class Dead(BoxSimulator):
        def _handle(self, pkt):
            return None

    install(Dead())
    hc.main()

    assert hc.RESULTS[0]["status"] == "FAIL"
    assert len(hc.RESULTS) == 1, "it must not keep probing a box that never answered"
    assert "ABORTED" in report.read_text(encoding="utf-8")
    assert "local UDP port 69" in capsys.readouterr().out


def test_it_refuses_to_overwrite_an_existing_canary(rig):
    install, report = rig
    install(BoxSimulator(files={hc.CANARY: b"someone elses file"}))

    hc.main()

    guard = outcomes()["Canary name is free (nothing will be overwritten)"]
    assert guard["status"] == "FAIL"
    assert "Refusing to overwrite" in guard["detail"]


def test_read_only_mode_never_writes(rig, monkeypatch):
    install, report = rig
    box = install(BoxSimulator(files={"O1.NC": b"%\nX\n%\n"}))
    monkeypatch.setattr("sys.argv",
                        ["hardware_check.py", "--read-only", "--report", str(report)])
    before = dict(box.files)

    assert hc.main() == 0
    assert box.files == before, "--read-only touched the device"
    assert hc.CANARY not in box.files


def test_the_canary_is_cleaned_up_afterwards(rig):
    install, report = rig
    box = install(BoxSimulator(files={"O1.NC": b"%\nX\n%\n"}))

    hc.main()

    assert hc.CANARY not in box.files, "the check left its canary on the box"
    assert outcomes()["Canary is deleted afterwards"]["status"] == "PASS"


def test_it_never_starts_or_stops_anything_on_the_machine(rig):
    """RunFile/StopDnc would act on the CNC itself. They must never be sent."""
    install, report = rig
    box = install(BoxSimulator(files={"O1.NC": b"%\nX\n%\n"}))
    sent_opcodes = []
    original = box._handle

    def spy(pkt):
        sent_opcodes.append(d.opcode_of(pkt))
        return original(pkt)

    box._handle = spy
    hc.main()

    assert d.OP_RUNFILE not in sent_opcodes
    assert d.OP_STOPDNC not in sent_opcodes
    assert d.OP_DELFOLDER not in sent_opcodes
