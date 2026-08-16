# DNC Micro Toolkit

Python tools to transfer G-code programs (send/receive) between a PC/server and CNC
machines over three different transports — with no dependency on proprietary DNC
software such as CIMCO.

Includes a **reverse-engineered client for a commercial WiFi DNC box**, whose
proprietary protocol is documented byte by byte in this repository.

## What's in here

| Transport | File(s) | When to use |
|---|---|---|
| **FOCAS (Fanuc)** | `focas.py`, `focas_probe.py`, `focas_transfer.py` | Fanuc controls with Ethernet/FOCAS enabled (30i/31i/32i, or 0i with the option). Network transfer, no extra hardware. |
| **Serial (RS-232)** | `serial_adapter.py` | Any non-Fanuc control (or older Fanuc without Ethernet) that only exposes a serial port — Romi, Siemens Sinumerik, etc. Runs directly or as an agent on a Raspberry Pi connected via USB-FTDI. |
| **Network DNC box** | `dnc_tftp.py`, `dnc_webdav.py` | Client for a WiFi DNC box (Micro DNC 2 type), speaking its custom TFTP protocol directly — without the Windows software that normally ships with the device. `dnc_webdav.py` exposes the box as a network share (WebDAV). |

And around them:

| File | What it does |
|---|---|
| `transport.py` | One `send` / `listen` / `browse` / `fetch` interface over all three transports. |
| `dnc_web.py` | Shop-floor web panel for a Raspberry Pi: a Send button plus automatic background receiving. Drives **any** transport. |
| `transfer_log.py` | Append-only CSV record of every program that moved, so the history survives a reboot. |
| `dnc-web.service` | systemd unit for running the panel on a Pi. |
| `tests/` | Test suite. Needs no hardware — see [Testing](#testing). |

## Why it exists

Commercial DNC boxes solve the transfer problem but are a dead end — closed protocol,
no integration with anything. This project grew out of two observations:

1. **FOCAS is not just about transfers.** The same library that pushes a program also
   reads machine status and counters, so using FOCAS for transfer gets future telemetry
   ready for free.

2. **The purchased box became the blueprint.** Its protocol — TFTP over UDP/69, but with
   its own opcodes and packet format, unrelated to standard TFTP — was mapped by reverse
   engineering the official installer (capturing bytes via reflection) and is documented
   byte by byte in `dnc_tftp.py`. That opened the door to running the same logic on a
   Raspberry Pi with a USB-FTDI adapter: a DIY box at a fraction of the per-machine cost.

`serial_adapter.py` was inspired by (not copied from) the OpenDNC project — the logic is
original, both to avoid the GPL copyleft and to stay consistent with the rest of the codebase.

## Integrity

A CNC program that arrives truncated or scrambled is not a cosmetic bug: it reaches
the machine looking like a valid program. Three rules follow from that, and they are
covered by tests:

- **Silence is an error, never an end of file.** If the device stops answering
  mid-transfer, the client raises instead of returning what it managed to collect.
  A partial program is never written to disk.
- **Every reply is checked.** UDP duplicates and delays datagrams; a late answer for
  an earlier block is discarded rather than accepted in place of the current one.
- **Uploads are read back and compared.** After sending, the file is downloaded from
  the device and matched byte for byte (`VERIFY_UPLOAD`, on by default).

## Configuration

Everything lives in `config.py`, resolved in this order — later wins:

1. the defaults in the file;
2. `dnc_config.json` next to it (git-ignored);
3. environment variables, `DNCKIT_` + the setting name.

So a fleet of Raspberry Pis can run identical code and differ only by environment:

```bash
DNCKIT_MACHINE_NAME=LATHE-01 DNCKIT_SERIAL_PORT=/dev/ttyUSB0 python3 dnc_web.py
```

Pick the transport with `TRANSPORT` (`serial`, `dnc-box` or `focas`).

## Installation

See [`INSTALL.md`](INSTALL.md) for the FOCAS path (Windows, 32-bit Python plus
Fanuc's `Fwlib32.dll` — not included here; it ships with the machine or the official SDK).

For the serial path on a Raspberry Pi:

```bash
sudo apt install -y python3-pip
pip3 install pyserial flask
python3 dnc_web.py
```

To keep it running, install the unit:

```bash
sudo cp dnc-web.service /etc/systemd/system/ && sudo systemctl enable --now dnc-web
```

Per-script dependencies are listed in [`requirements.txt`](requirements.txt).

## Testing

The suite runs entirely off hardware: the wire format is built by pure functions,
and the transfer logic is exercised against a scripted fake device and a fake
serial port.

```bash
pip install pytest && python -m pytest tests/ -q
```

`tests/test_protocol.py` holds **golden byte strings captured from the real device**.
They are the most valuable thing in this repository — the DNC box is the only other
copy of that knowledge. If one of them fails, the code is wrong, not the test.

## DNC box protocol (technical summary)

- **Transport:** UDP port 69, 512-byte blocks, 2 s timeout, up to 10 retransmissions.
- **Framing:** a 2-byte big-endian opcode at the start of every packet. This is *not*
  standard RFC 1350 TFTP — it reuses port 69, but the packet format is proprietary.
- **Symmetric port:** the device only answers datagrams sent **from** local port 69.
  On Linux that is privileged: run as root, or grant `CAP_NET_BIND_SERVICE`.
- **Commands covered:** status, info, directory listing (including subfolders), download,
  upload, delete, rename, create folder, and send a message to the operator.
- Full details and annotated opcodes are in `dnc_tftp.py`.

## Known limitations

- The protocol controls files and transfers, not navigation of the device's own display.
- Filenames with special characters may be altered by the device firmware — a limitation
  of its FatFs implementation, not of this client.
- The FOCAS transfer functions (`cnc_upstart4` / `cnc_dwnstart4`, etc.) vary in format
  across control versions. The affected spots are marked with `# CHECK?` in
  `focas_transfer.py`, and the FOCAS path has never been run against a real control.
- The web panel has no login: the factory network is the trust boundary, by design.
  It does carry a CSRF token, so another page open on the same phone cannot fire a
  program at the machine.

## License

See [`LICENSE`](LICENSE).
