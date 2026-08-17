"""
dnc_tftp.py  -  Micro DNC 2 client over the network (proprietary TFTP-like protocol).
====================================================================
Sibling of focas.py and serial_adapter.py, for the WiFi path: talking DIRECTLY
to the DNC box, with no dependency on the vendor's QSExplorer software.

>>> PROTOCOL MAPPED BYTE BY BYTE by reverse engineering QSExplorer 4.06
    (the vendor's official .NET application): its packet constructors were
    invoked through reflection and the real bytes captured. The format below
    is CONFIRMED. <<<

WIRE FORMAT:
  - Opcode: 2 bytes big-endian at the start of EVERY packet.
  - Command with a path: [opcode][ASCII][0x00]   (Rename = two 0x00 strings).
  - Status/Info: [opcode][0x00 0x00].   Stop: [opcode][0x00].
  - Block (DATA/ACK/RequestData): [opcode][block: 4 bytes big-endian].
  - RRQ/WRQ: [opcode][filename][0x00]  (no 'mode' field, unlike standard TFTP).
  - DATA: [00 03][block:4][up to 512 bytes of payload].

REQUEST OPCODES (client -> device), as captured:
  RRQ=1  WRQ=2  DATA=3  ACK=4  ReadDir=8  NewFolder=10  DeleteFile=12
  DeleteFolder=14  Rename=16  GetInfo=19  SendMsg=21  StartUpCopy=23
  RunFile=25  OpenDir=27  StopDnc=28  GetStatus=50  RequestData=56

Network: UDP port 69, 512-byte blocks, 2 s timeout, up to 10 retransmissions.

INTEGRITY: this client treats a lost transfer as an ERROR, never as a normal
end of file. A G-code program truncated in silence is the worst possible
outcome here - it reaches the machine looking like a valid program. Every
reply is checked for the expected opcode and block number, so a late or
duplicated UDP datagram cannot be accepted in place of the current block.

No external dependencies (plain socket).
"""

import hashlib
import os
import socket
import struct
import time

import config

DNC_IP   = getattr(config, "DNC_IP", "192.168.1.236")
DNC_PORT = getattr(config, "DNC_PORT", 69)
BLOCK    = 512
TIMEOUT  = 2
RESENDS  = 10

# Request opcodes (client -> device)
OP_RRQ = 1; OP_WRQ = 2; OP_DATA = 3; OP_ACK = 4
OP_READDIR = 8; OP_NEWFOLDER = 10; OP_DELFILE = 12; OP_DELFOLDER = 14
OP_RENAME = 16; OP_GETINFO = 19; OP_SENDMSG = 21; OP_STARTUPCOPY = 23
OP_RUNFILE = 25; OP_OPENDIR = 27; OP_STOPDNC = 28; OP_GETSTATUS = 50
OP_REQDATA = 56

# Reply opcodes (device -> client), where they are known
OP_DIR_ENTRY = 9      # one directory entry, in reply to ReadDir
OP_UP_CLOSE  = 52     # close the file being uploaded
OP_DL_OPEN   = 54     # open a file for download
OP_DATA_REPLY = 57    # payload block, in reply to RequestData

END_OF_DIR = 0xFFFF   # sequence number that terminates a directory listing


class DncError(RuntimeError):
    """Any failure while talking to the DNC box."""


class DncTimeout(DncError):
    """The device stopped answering. NEVER treated as a normal end of file."""


class DncProtocolError(DncError):
    """The device answered something outside the mapped protocol."""


class DncVerifyError(DncError):
    """What came back from the device does not match what was sent."""


def _op(code):  return struct.pack(">H", code)          # opcode = 2 bytes BE
def _blk(n):    return struct.pack(">I", n)             # block  = 4 bytes BE
def _cmd(code, text):
    return _op(code) + text.encode("ascii", "replace") + b"\x00"


# ---- Packet builders (EXACT, checked against the vendor binary) ----
def pkt_status():        return _op(OP_GETSTATUS) + b"\x00\x00"
def pkt_info():          return _op(OP_GETINFO) + b"\x00\x00"
def pkt_stop():          return _op(OP_STOPDNC) + b"\x00"
def pkt_readdir(path="\\"):  return _cmd(OP_READDIR, path)
def pkt_opendir(path):       return _cmd(OP_OPENDIR, path)
def pkt_run(path):           return _cmd(OP_RUNFILE, path)
def pkt_msg(text):           return _cmd(OP_SENDMSG, text)
def pkt_newfolder(path):     return _cmd(OP_NEWFOLDER, path)
def pkt_delfile(path):       return _cmd(OP_DELFILE, path)
def pkt_delfolder(path):     return _cmd(OP_DELFOLDER, path)
def pkt_rename(old, new):
    return (_op(OP_RENAME) + old.encode("ascii", "replace") + b"\x00"
            + new.encode("ascii", "replace") + b"\x00")
def pkt_rrq(name):           return _cmd(OP_RRQ, name)
def pkt_wrq(name):           return _cmd(OP_WRQ, name)
def pkt_ack(block):          return _op(OP_ACK) + _blk(block)
def pkt_data(block, data):   return _op(OP_DATA) + _blk(block) + data
def pkt_reqdata(block):      return _op(OP_REQDATA) + _blk(block)
def pkt_dl_open(name):       return _cmd(OP_DL_OPEN, name)
def pkt_up_close():          return _cmd(OP_UP_CLOSE, "0")


def opcode_of(resp):
    return struct.unpack(">H", resp[:2])[0] if resp and len(resp) >= 2 else None


def parse_status(resp):
    """Status (reply op 51): [00 33][9 bytes 00]['IP|text' 00]. Returns the text.
    Seen live: '192.168.1.236|No File Selected'. (confirmed live)"""
    if not resp or opcode_of(resp) != OP_GETSTATUS + 1:
        return None
    return resp[11:].split(b"\x00")[0].decode("ascii", "replace")


def parse_info(resp):
    """Info (reply op 20): [00 14][4 bytes 00]['MICRO DNC2' 00]. Returns the model."""
    if not resp or opcode_of(resp) != OP_GETINFO + 1:
        return None
    return resp[6:].split(b"\x00")[0].decode("ascii", "replace")


# ---- Network ----
LOCAL_PORT = 69   # CRITICAL: the device only answers if we send FROM local port 69
                  # (symmetric). A random source port gets no reply at all.
                  # (confirmed live 2026-07-03)


def _sock():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", LOCAL_PORT))
    except PermissionError as e:
        s.close()
        raise DncError(
            f"Cannot bind local UDP port {LOCAL_PORT}: permission denied. "
            "The device only answers a symmetric port, and ports below 1024 "
            "need root on Linux - run with sudo, or grant the capability once "
            "with: sudo setcap 'cap_net_bind_service=+ep' $(readlink -f $(which python3))"
        ) from e
    except OSError as e:
        s.close()
        raise DncError(
            f"Cannot bind local UDP port {LOCAL_PORT}: {e}. "
            "Another instance of this toolkit (dnc_web / dnc_webdav) is probably "
            "already running and holding the port."
        ) from e
    s.settimeout(TIMEOUT)
    return s


def _exchange(sock, pkt, accept=None, attempts=None, what="request"):
    """Send `pkt` and wait for a reply that `accept` approves.

    A reply that fails `accept` is a stale duplicate left over from an earlier
    retransmission - over UDP that is normal. Such packets are DISCARDED and we
    keep listening inside the same timeout window, instead of handing the caller
    the wrong block. Raises DncTimeout when every attempt is exhausted.
    """
    attempts = attempts or RESENDS     # read at call time, so tests can shrink it
    for _ in range(attempts):
        sock.sendto(pkt, (DNC_IP, DNC_PORT))
        deadline = time.monotonic() + TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                resp = sock.recvfrom(8192)[0]
            except socket.timeout:
                break
            if accept is None or accept(resp):
                return resp
            # stale/duplicate reply: ignore it and keep waiting for the right one
    raise DncTimeout(
        f"No valid reply from the DNC box at {DNC_IP}:{DNC_PORT} for {what} "
        f"after {attempts} attempts."
    )


def _rpc(pkt, sock=None, accept=None, what="request"):
    """One request, one reply. Raises DncTimeout if the device stays silent."""
    s = sock or _sock()
    try:
        return _exchange(s, pkt, accept=accept, what=what)
    finally:
        if sock is None:
            s.close()


def _try_rpc(pkt, what="request"):
    """Same as _rpc but returns None when the device stays silent. For probing
    only - never use this on a transfer, where silence must be an error.

    Only DncTimeout is swallowed. A bind failure is a problem with THIS machine
    (no root, port already taken) and must not be reported as "the box did not
    answer" - that sends you looking at the wrong end of the cable.
    """
    try:
        return _rpc(pkt, what=what)
    except DncTimeout:
        return None


# ---- Commands (single request -> reply) ----
def get_status():          return _try_rpc(pkt_status(), "status")
def get_info():            return _try_rpc(pkt_info(), "info")
def stop_dnc():            return _rpc(pkt_stop(), what="stop")
def run_file(path):        return _rpc(pkt_run(path), what="run file")
def send_message(text):    return _rpc(pkt_msg(text), what="send message")
def new_folder(path):      return _rpc(pkt_newfolder(path), what="new folder")
def delete_file(path):     return _rpc(pkt_delfile(path), what="delete file")
def rename(old, new):      return _rpc(pkt_rename(old, new), what="rename")


def open_dir(path):
    """Open a directory (e.g. '0:program' - NO leading slash). Ack = opcode 7.
    (confirmed live)"""
    return _rpc(pkt_opendir(path), what="open dir")


def _parse_entry(resp):
    """Directory entry: [00 09][4x00][name \\0][seq:2][attrib:1][size:4].
    attrib 0x10 = folder, 0x20 = file. (confirmed live 2026-07-03)"""
    if not resp or opcode_of(resp) != OP_DIR_ENTRY:
        return None
    z = resp.index(0, 6)
    name = resp[6:z].decode("ascii", "replace")
    tail = resp[z + 1:]
    seq = int.from_bytes(tail[0:2], "big") if len(tail) >= 2 else 0
    attrib = tail[2] if len(tail) >= 3 else 0
    size = int.from_bytes(tail[3:7], "big") if len(tail) >= 7 else 0
    return {"name": name, "seq": seq, "is_dir": bool(attrib & 0x10), "size": size}


def list_dir(path=""):
    """List a directory (empty path = root '0:'). Returns a list of dicts.

    CONFIRMED live: OpenDir(path) + [op8 + index(2 bytes) + path + 00];
    each entry comes back as op9; the listing ends when seq == 0xFFFF.

    A timeout here is an ERROR, not the end of the listing - swallowing it
    would silently hide programs from download_all().
    """
    items = []
    s = _sock()
    try:
        _exchange(s, pkt_opendir(path), what=f"open dir {path!r}")
        for i in range(2000):
            req = _op(OP_READDIR) + struct.pack(">H", i) + path.encode("ascii", "replace") + b"\x00"
            resp = _exchange(s, req, accept=lambda r: opcode_of(r) == OP_DIR_ENTRY,
                             what=f"directory entry {i} of {path!r}")
            entry = _parse_entry(resp)
            if entry is None:
                raise DncProtocolError(f"Unreadable directory entry at index {i}.")
            if entry["seq"] == END_OF_DIR:
                break
            items.append(entry)
        else:
            raise DncProtocolError(
                "Directory listing did not terminate after 2000 entries."
            )
    finally:
        s.close()
    return items


# ---- Download (device -> PC) -- CONFIRMED LIVE (2026-07-03) ----
# Real flow (reversed from TFTP_DownloadFileTask):
#   open:  op 54  ->  [00 36][name][00]        ->  ack op 55
#   block N (1,2,3...): op 56 -> [00 38][N:4]  ->  reply op 57 [00 39][N:4][payload]
#   (512 bytes per block; a final block shorter than 512 ends the file).
#   Everything from local port 69.

def _accept_block(block):
    """Only accept the DATA reply that carries the block we just asked for."""
    def accept(resp):
        if opcode_of(resp) != OP_DATA_REPLY or len(resp) < 6:
            return False
        return struct.unpack(">I", resp[2:6])[0] == block
    return accept


def download(remote_name, local_path=None, expected_size=None):
    """Pull a file from the box to the PC. Returns the bytes (and writes them to
    `local_path` when given).

    Pass `expected_size` whenever it is known - from the directory listing, or
    from the data just uploaded. Without it the end of the file can only be
    inferred from a short block, which means a file whose size is an exact
    multiple of 512 always costs one request BEYOND the end. If the firmware
    answers that with silence rather than an empty block, a fully received file
    would be thrown away as a timeout. Silence cannot distinguish "end of file"
    from "packet lost", so the size is the only sound way to tell them apart.

    Raises DncTimeout if the device goes silent mid-file: a partial G-code
    program must never be written out as if it were complete.
    """
    s = _sock()
    data = bytearray()
    try:
        _exchange(s, pkt_dl_open(remote_name), what=f"open download of {remote_name!r}")
        block = 1
        while True:
            resp = _exchange(s, pkt_reqdata(block), accept=_accept_block(block),
                             what=f"block {block} of {remote_name!r}")
            payload = resp[6:]           # [00 39][block:4][payload...]
            data += payload
            if expected_size is not None and len(data) >= expected_size:
                if len(data) > expected_size:
                    raise DncProtocolError(
                        f"{remote_name!r}: the device sent {len(data)} bytes where "
                        f"{expected_size} were expected."
                    )
                break                    # every byte accounted for; ask for nothing more
            if len(payload) < BLOCK:     # short block = end of file
                break                    # ALSO checked when a size is known: the
                                         # device may hold fewer bytes than expected
                                         # (that is what a normalising firmware
                                         # looks like), and without this the loop
                                         # would request blocks for ever.
            block += 1
    finally:
        s.close()
    if local_path:
        with open(local_path, "wb") as f:
            f.write(data)
    return bytes(data)


def download_all(dest_dir):
    """Download EVERY file in the root of the box into a folder.
    Returns a list of (name, bytes_downloaded)."""
    os.makedirs(dest_dir, exist_ok=True)
    out = []
    for entry in list_dir(""):
        if entry["is_dir"]:
            continue
        data = download(entry["name"], os.path.join(dest_dir, entry["name"]),
                        expected_size=entry["size"] or None)
        out.append((entry["name"], len(data)))
    return out


# ---- Upload (PC -> device) -- CONFIRMED LIVE (2026-07-03) ----
# open:   op 2 (WRQ)   -> [00 02][name][00]                 -> ack op 4
# block N (1,2,3...):   DATA op 3 [00 03][N:4][data<=512]   -> ack op 4
# close:  op 52 [00 34]['0'][00]                            -> ack op 53

def _accept_ack(block=None):
    """Accept an ACK, and when the firmware echoes the block number, require it
    to be the block we just sent. Older firmware may send a bare ACK - that is
    tolerated rather than rejected."""
    def accept(resp):
        if opcode_of(resp) != OP_ACK:
            return False
        if block is not None and len(resp) >= 6:
            return struct.unpack(">I", resp[2:6])[0] == block
        return True
    return accept


def upload(local_path, remote_name, verify=True):
    """Send a file from the PC to the box's memory.

    With verify=True (default) the file is read back from the device and
    compared byte for byte. On a CNC a wrong program is a crash, not a typo,
    so the read-back is worth the extra seconds.
    """
    with open(local_path, "rb") as f:
        data = f.read()

    s = _sock()
    try:
        _exchange(s, pkt_wrq(remote_name), accept=_accept_ack(),
                  what=f"open upload of {remote_name!r}")
        block = 1
        for i in range(0, len(data), BLOCK):
            chunk = data[i:i + BLOCK]
            _exchange(s, pkt_data(block, chunk), accept=_accept_ack(block),
                      what=f"block {block} of {remote_name!r}")
            block += 1
        if len(data) % BLOCK == 0:       # exact multiple needs an empty final block
            _exchange(s, pkt_data(block, b""), accept=_accept_ack(block),
                      what=f"final empty block of {remote_name!r}")
        _exchange(s, pkt_up_close(), what=f"close {remote_name!r}")
    finally:
        s.close()

    if verify:
        verify_remote(remote_name, data)
    return len(data)


def verify_remote(remote_name, expected):
    """Read a file back from the device and compare it with `expected`.
    Raises DncVerifyError describing the first difference."""
    # The size is known exactly here, so the read-back never has to guess where
    # the file ends - which matters most for a program that is an exact multiple
    # of the block size, the very case this verification is meant to cover.
    got = download(remote_name, expected_size=len(expected) or None)
    if got == expected:
        return True
    if len(got) != len(expected):
        raise DncVerifyError(
            f"{remote_name!r} came back with {len(got)} bytes, expected "
            f"{len(expected)}. sha256 sent={hashlib.sha256(expected).hexdigest()[:16]} "
            f"got={hashlib.sha256(got).hexdigest()[:16]}"
        )
    offset = next(i for i, (a, b) in enumerate(zip(got, expected)) if a != b)
    raise DncVerifyError(
        f"{remote_name!r} differs from what was sent at byte {offset} "
        f"(sent {expected[offset]:#04x}, got {got[offset]:#04x}). "
        "The program on the device is NOT the one on disk - do not run it."
    )


upload_file = upload   # alias


if __name__ == "__main__":
    print(f"Probing the DNC box at {DNC_IP}:{DNC_PORT} (UDP)...")
    r = get_status()
    if r is None:
        print("No answer. Is the box powered and on the network? Is DNC_IP right in config.py?")
    else:
        print(f"Connected. Status: {parse_status(r)!r}")
        print(f"Model:  {parse_info(get_info())!r}")
        print("Files in the root:")
        for e in list_dir(""):
            print(f"  {'DIR ' if e['is_dir'] else 'file'} {e['size']:>9}  {e['name']}")
