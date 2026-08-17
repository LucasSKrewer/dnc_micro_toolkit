"""
Golden tests for the reverse-engineered Micro DNC 2 protocol.

The expected byte strings below were CAPTURED FROM THE ORIGINAL implementation
before any refactoring, which itself had been checked against QSExplorer 4.06
and confirmed against the physical device on 2026-07-03.

They are the ground truth of this repository: the device is the only other
copy of this knowledge. Any change that alters a single byte here breaks
communication with hardware that cannot be re-probed from a desk, so these
tests must keep passing no matter how the code around them is reorganised.

No hardware required - the wire format is built by pure functions, and the
transfer logic is exercised against a scripted fake device.
"""

import socket
import struct

import pytest

import dnc_tftp as d


# ---------------------------------------------------------------- golden bytes

GOLDEN = {
    "pkt_status()":                       "00320000",
    "pkt_info()":                         "00130000",
    "pkt_stop()":                         "001c00",
    "pkt_readdir()":                      "00085c00",
    "pkt_opendir('0:program')":           "001b303a70726f6772616d00",
    "pkt_run('0:program\\\\O1234')":      "0019303a70726f6772616d5c4f3132333400",
    "pkt_msg('OK')":                      "00154f4b00",
    "pkt_newfolder('0:novo')":            "000a303a6e6f766f00",
    "pkt_delfile('O1234')":               "000c4f3132333400",
    "pkt_delfolder('0:velho')":           "000e303a76656c686f00",
    "pkt_rename('A.NC', 'B.NC')":         "0010412e4e4300422e4e4300",
    "pkt_rrq('O1234')":                   "00014f3132333400",
    "pkt_wrq('O1234')":                   "00024f3132333400",
    "pkt_ack(0)":                         "000400000000",
    "pkt_ack(1)":                         "000400000001",
    "pkt_ack(65536)":                     "000400010000",
    "pkt_data(1, b'G01 X10')":            "00030000000147303120583130",
    "pkt_data(7, b'')":                   "000300000007",
    "pkt_reqdata(3)":                     "003800000003",
    "pkt_dl_open('O1234')":               "00364f3132333400",
    "pkt_up_close()":                     "00343000",
}


@pytest.mark.parametrize("expression,expected_hex", sorted(GOLDEN.items()))
def test_wire_format_is_unchanged(expression, expected_hex):
    """Every packet builder still produces the exact bytes the device expects."""
    produced = eval(expression, {"d": d}, vars(d))
    assert produced.hex() == expected_hex, (
        f"{expression} changed on the wire.\n"
        f"  expected {expected_hex}\n"
        f"  produced {produced.hex()}\n"
        "This byte sequence was captured from the real device - do not 'fix' the "
        "test, fix the code."
    )


def test_opcode_is_two_bytes_big_endian():
    assert d.opcode_of(d.pkt_status()) == d.OP_GETSTATUS
    assert d.opcode_of(d.pkt_data(1, b"x")) == d.OP_DATA
    assert d.opcode_of(b"") is None
    assert d.opcode_of(b"\x00") is None


def test_every_path_command_is_nul_terminated():
    for pkt in (d.pkt_opendir("0:p"), d.pkt_run("A"), d.pkt_delfile("A"),
                d.pkt_rrq("A"), d.pkt_wrq("A"), d.pkt_dl_open("A")):
        assert pkt.endswith(b"\x00")


def test_rename_carries_two_nul_terminated_strings():
    assert d.pkt_rename("A", "B") == struct.pack(">H", d.OP_RENAME) + b"A\x00B\x00"


def test_non_ascii_never_breaks_the_builder():
    """The device speaks ASCII only; accented names must degrade, not explode."""
    assert d.pkt_delfile("PEÇA") == struct.pack(">H", d.OP_DELFILE) + b"PE?A\x00"


# ---------------------------------------------------------------- reply parsing

def test_parse_status_extracts_the_text():
    resp = struct.pack(">H", 51) + b"\x00" * 9 + b"192.168.1.236|No File Selected\x00"
    assert d.parse_status(resp) == "192.168.1.236|No File Selected"


def test_parse_info_extracts_the_model():
    resp = struct.pack(">H", 20) + b"\x00" * 4 + b"MICRO DNC2\x00"
    assert d.parse_info(resp) == "MICRO DNC2"


def test_parsers_reject_the_wrong_opcode():
    assert d.parse_status(struct.pack(">H", 99) + b"\x00" * 20) is None
    assert d.parse_info(struct.pack(">H", 99) + b"\x00" * 20) is None


def _dir_entry(name, seq, is_dir, size):
    return (struct.pack(">H", d.OP_DIR_ENTRY) + b"\x00" * 4 + name.encode() + b"\x00"
            + struct.pack(">H", seq) + bytes([0x10 if is_dir else 0x20])
            + struct.pack(">I", size))


def test_parse_entry_reads_a_file():
    assert d._parse_entry(_dir_entry("O1234", 3, False, 1024)) == {
        "name": "O1234", "seq": 3, "is_dir": False, "size": 1024}


def test_parse_entry_reads_a_folder():
    assert d._parse_entry(_dir_entry("program", 1, True, 0))["is_dir"] is True


def test_parse_entry_recognises_the_terminator():
    assert d._parse_entry(_dir_entry("X", d.END_OF_DIR, False, 0))["seq"] == d.END_OF_DIR


# ---------------------------------------------------------------- fake device

class FakeBox:
    """Scripted stand-in for the DNC box.

    `script(packet, nth_send)` returns the datagram(s) the device would send
    back, or None to stay silent (a dropped packet).
    """

    def __init__(self, script):
        self.script = script
        self.sent = []
        self._inbox = []

    def sendto(self, packet, _addr):
        self.sent.append(packet)
        reply = self.script(packet, len(self.sent))
        if reply is None:
            return
        self._inbox.extend(reply if isinstance(reply, list) else [reply])

    def recvfrom(self, _n):
        if not self._inbox:
            raise socket.timeout()
        return self._inbox.pop(0), ("fake", 69)

    def settimeout(self, _t):  pass
    def close(self):           pass


@pytest.fixture
def fast(monkeypatch):
    """Shrink the retry budget so timeout paths finish in milliseconds."""
    monkeypatch.setattr(d, "TIMEOUT", 0.01)
    monkeypatch.setattr(d, "RESENDS", 2)


def install(monkeypatch, script):
    box = FakeBox(script)
    monkeypatch.setattr(d, "_sock", lambda: box)
    return box


def _data(block, payload):
    return struct.pack(">H", d.OP_DATA_REPLY) + struct.pack(">I", block) + payload


def _ack(block=None):
    pkt = struct.pack(">H", d.OP_ACK)
    return pkt + struct.pack(">I", block) if block is not None else pkt


# ---------------------------------------------------------------- download

def test_download_reassembles_a_multi_block_file(monkeypatch, fast):
    body = b"G01 X10\n" * 200          # > 512 bytes, so it spans blocks
    blocks = [body[i:i + d.BLOCK] for i in range(0, len(body), d.BLOCK)]

    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        if d.opcode_of(pkt) == d.OP_REQDATA:
            n = struct.unpack(">I", pkt[2:6])[0]
            return _data(n, blocks[n - 1])
        return None

    install(monkeypatch, script)
    assert d.download("O1234") == body


def test_download_ignores_a_stale_duplicate_block(monkeypatch, fast):
    """A late reply for an earlier block must never be accepted as the current
    one - that is how UDP silently corrupts a program."""
    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        n = struct.unpack(">I", pkt[2:6])[0]
        if n == 1:
            return _data(1, b"A" * d.BLOCK)
        # block 2: the device's late answer for block 1 arrives first
        return [_data(1, b"XXXX"), _data(2, b"B" * 10)]

    install(monkeypatch, script)
    got = d.download("O1234")
    assert got == b"A" * d.BLOCK + b"B" * 10
    assert b"XXXX" not in got


def test_download_raises_instead_of_truncating(monkeypatch, fast):
    """The original code returned a PARTIAL file here, with no error at all."""
    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        n = struct.unpack(">I", pkt[2:6])[0]
        return _data(1, b"A" * d.BLOCK) if n == 1 else None   # dies mid-file

    install(monkeypatch, script)
    with pytest.raises(d.DncTimeout):
        d.download("O1234")


def test_download_does_not_write_a_partial_file(monkeypatch, fast, tmp_path):
    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        n = struct.unpack(">I", pkt[2:6])[0]
        return _data(1, b"A" * d.BLOCK) if n == 1 else None

    install(monkeypatch, script)
    dest = tmp_path / "O1234.nc"
    with pytest.raises(d.DncTimeout):
        d.download("O1234", str(dest))
    assert not dest.exists(), "a truncated program must never reach the disk"


def test_download_stops_at_a_known_size_without_asking_past_the_end(monkeypatch, fast):
    """A file that is an exact multiple of 512 must not cost a request beyond it.

    Without the size, the end of file can only be inferred from a short block, so
    a 512-byte program always asks for block 2 - and a firmware that answers that
    with silence would make a complete, correct download fail as a timeout.
    """
    body = b"Z" * d.BLOCK

    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        n = struct.unpack(">I", pkt[2:6])[0]
        return _data(1, body) if n == 1 else None      # silent past the end

    box = install(monkeypatch, script)
    assert d.download("O1", expected_size=len(body)) == body
    assert [d.opcode_of(p) for p in box.sent].count(d.OP_REQDATA) == 1


def test_download_without_a_size_still_asks_past_an_exact_multiple(monkeypatch, fast):
    """Documents exactly why the size matters - this is the hazardous path."""
    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        n = struct.unpack(">I", pkt[2:6])[0]
        return _data(1, b"Z" * d.BLOCK) if n == 1 else None

    install(monkeypatch, script)
    with pytest.raises(d.DncTimeout):
        d.download("O1")


def test_download_stops_when_the_device_holds_fewer_bytes_than_expected(monkeypatch, fast):
    """A normalising firmware returns FEWER bytes than were sent to it.

    Terminating only on the expected size would then never terminate: the device
    keeps answering with empty blocks and the count never reaches the target.
    The short-block rule has to stay in force even when a size is known.
    """
    stored = b"G01 X10\n"                      # 8 bytes on the device

    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        n = struct.unpack(">I", pkt[2:6])[0]
        return _data(n, stored if n == 1 else b"")

    box = install(monkeypatch, script)
    assert d.download("O1", expected_size=999) == stored
    assert [d.opcode_of(p) for p in box.sent].count(d.OP_REQDATA) == 1


def test_download_rejects_more_bytes_than_expected(monkeypatch, fast):
    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        return _data(1, b"X" * 20)

    install(monkeypatch, script)
    with pytest.raises(d.DncProtocolError, match="20 bytes where 10"):
        d.download("O1", expected_size=10)


def test_verify_reads_back_using_the_size_it_already_knows(monkeypatch, fast):
    """Verification of a 512-byte program must not depend on a block past the end."""
    body = b"Z" * d.BLOCK

    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        n = struct.unpack(">I", pkt[2:6])[0]
        return _data(1, body) if n == 1 else None

    install(monkeypatch, script)
    assert d.verify_remote("O1", body) is True


def test_download_short_block_ends_the_file(monkeypatch, fast):
    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        return _data(1, b"%\nO1\n%\n")

    install(monkeypatch, script)
    assert d.download("O1") == b"%\nO1\n%\n"


# ---------------------------------------------------------------- upload

def test_upload_sends_the_documented_sequence(monkeypatch, fast, tmp_path):
    src = tmp_path / "prog.nc"
    src.write_bytes(b"G01 X10")

    def script(pkt, _n):
        op = d.opcode_of(pkt)
        if op == d.OP_WRQ:
            return _ack()
        if op == d.OP_DATA:
            return _ack(struct.unpack(">I", pkt[2:6])[0])
        if op == d.OP_UP_CLOSE:
            return struct.pack(">H", 53)
        return None

    box = install(monkeypatch, script)
    d.upload(str(src), "O1234", verify=False)

    opcodes = [d.opcode_of(p) for p in box.sent]
    assert opcodes == [d.OP_WRQ, d.OP_DATA, d.OP_UP_CLOSE]
    assert box.sent[1] == d.pkt_data(1, b"G01 X10")


def test_upload_appends_empty_block_on_exact_multiple(monkeypatch, fast, tmp_path):
    src = tmp_path / "prog.nc"
    src.write_bytes(b"Z" * d.BLOCK)

    def script(pkt, _n):
        op = d.opcode_of(pkt)
        if op == d.OP_WRQ:                return _ack()
        if op == d.OP_DATA:               return _ack(struct.unpack(">I", pkt[2:6])[0])
        if op == d.OP_UP_CLOSE:           return struct.pack(">H", 53)
        return None

    box = install(monkeypatch, script)
    d.upload(str(src), "O1", verify=False)

    data_pkts = [p for p in box.sent if d.opcode_of(p) == d.OP_DATA]
    assert len(data_pkts) == 2
    assert data_pkts[1] == d.pkt_data(2, b"")


def test_upload_raises_when_a_block_is_never_acked(monkeypatch, fast, tmp_path):
    src = tmp_path / "prog.nc"
    src.write_bytes(b"G01 X10")

    def script(pkt, _n):
        return _ack() if d.opcode_of(pkt) == d.OP_WRQ else None

    install(monkeypatch, script)
    with pytest.raises(d.DncTimeout):
        d.upload(str(src), "O1234", verify=False)


def test_upload_rejects_an_ack_for_the_wrong_block(monkeypatch, fast, tmp_path):
    src = tmp_path / "prog.nc"
    src.write_bytes(b"G01 X10")

    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_WRQ:
            return _ack()
        return _ack(99)                    # firmware acks a block we never sent

    install(monkeypatch, script)
    with pytest.raises(d.DncTimeout):
        d.upload(str(src), "O1234", verify=False)


# ---------------------------------------------------------------- verification

def test_upload_verifies_by_reading_back(monkeypatch, fast, tmp_path):
    src = tmp_path / "prog.nc"
    src.write_bytes(b"G01 X10")

    def script(pkt, _n):
        op = d.opcode_of(pkt)
        if op == d.OP_WRQ:        return _ack()
        if op == d.OP_DATA:       return _ack(struct.unpack(">I", pkt[2:6])[0])
        if op == d.OP_UP_CLOSE:   return struct.pack(">H", 53)
        if op == d.OP_DL_OPEN:    return struct.pack(">H", 55)
        if op == d.OP_REQDATA:    return _data(1, b"G01 X10")
        return None

    install(monkeypatch, script)
    assert d.upload(str(src), "O1234") == 7      # verify=True by default


def test_upload_raises_when_the_device_stored_something_else(monkeypatch, fast, tmp_path):
    src = tmp_path / "prog.nc"
    src.write_bytes(b"G01 X10")

    def script(pkt, _n):
        op = d.opcode_of(pkt)
        if op == d.OP_WRQ:        return _ack()
        if op == d.OP_DATA:       return _ack(struct.unpack(">I", pkt[2:6])[0])
        if op == d.OP_UP_CLOSE:   return struct.pack(">H", 53)
        if op == d.OP_DL_OPEN:    return struct.pack(">H", 55)
        if op == d.OP_REQDATA:    return _data(1, b"G01 X99")   # corrupted on device
        return None

    install(monkeypatch, script)
    with pytest.raises(d.DncVerifyError, match="byte 5"):
        d.upload(str(src), "O1234")


def test_verify_reports_a_length_mismatch(monkeypatch, fast):
    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_DL_OPEN:
            return struct.pack(">H", 55)
        return _data(1, b"SHORT")

    install(monkeypatch, script)
    with pytest.raises(d.DncVerifyError, match="5 bytes, expected 7"):
        d.verify_remote("O1234", b"G01 X10")


# ---------------------------------------------------------------- listing

def test_list_dir_stops_at_the_terminator(monkeypatch, fast):
    entries = [_dir_entry("O1", 0, False, 10),
               _dir_entry("program", 1, True, 0),
               _dir_entry("", d.END_OF_DIR, False, 0)]

    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_OPENDIR:
            return struct.pack(">H", 7)
        return entries[struct.unpack(">H", pkt[2:4])[0]]

    install(monkeypatch, script)
    got = d.list_dir("")
    assert [e["name"] for e in got] == ["O1", "program"]
    assert got[1]["is_dir"] is True


def test_list_dir_raises_instead_of_returning_a_short_list(monkeypatch, fast):
    """A truncated listing silently hides programs from download_all()."""
    def script(pkt, _n):
        if d.opcode_of(pkt) == d.OP_OPENDIR:
            return struct.pack(">H", 7)
        i = struct.unpack(">H", pkt[2:4])[0]
        return _dir_entry("O1", 0, False, 10) if i == 0 else None

    install(monkeypatch, script)
    with pytest.raises(d.DncTimeout):
        d.list_dir("")


def test_probe_helpers_stay_silent_instead_of_raising(monkeypatch, fast):
    """get_status/get_info are used to check whether the box is even there."""
    install(monkeypatch, lambda pkt, n: None)
    assert d.get_status() is None
    assert d.get_info() is None
