# DNC Micro Toolkit

Python tools to transfer G-code programs (send/receive) between a PC/server and CNC
machines over three different transports — with no dependency on proprietary DNC
software such as CIMCO.

Includes a **reverse-engineered client for a commercial WiFi DNC box**, whose
proprietary protocol is documented byte by byte in this repository.

## What's in here

| Transport | File(s) | When to use |
|---|---|---|
| **FOCAS (Fanuc)** | `focas.py`, `foco_teste.py`, `foco_transfer.py` | Fanuc controls with Ethernet/FOCAS enabled (30i/31i/32i, or 0i with the option). Network transfer, no extra hardware. |
| **Serial (RS-232)** | `serial_adapter.py` | Any non-Fanuc control (or older Fanuc without Ethernet) that only exposes a serial port — Romi, Siemens Sinumerik, etc. Runs directly or as an agent on a Raspberry Pi connected via USB-FTDI. |
| **Network DNC box** | `dnc_tftp.py`, `dnc_webdav.py` | Client for a WiFi DNC box (Micro DNC 2 type), speaking its custom TFTP protocol directly — without the Windows software that normally ships with the device. `dnc_webdav.py` exposes the box as a network share (WebDAV). |
| **Web UI** | `dnc_web.py` | Minimal Flask interface meant to run on a Raspberry Pi: a "Send" button plus automatic background receiving. Designed to sit next to the machine and be reachable from a phone or PC anywhere on the network. |

Everything is configured in a single place: `config.py`.

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

## Installation

See [`INSTALACAO.md`](INSTALACAO.md) for the FOCAS path (Windows, 32-bit Python plus
Fanuc's `Fwlib32.dll` — not included here; it ships with the machine or the official SDK).

For the serial path on a Raspberry Pi:

```bash
sudo apt install -y python3-pip
pip3 install pyserial
python3 serial_adapter.py
```

Per-script dependencies are listed in [`requirements.txt`](requirements.txt).

## DNC box protocol (technical summary)

- **Transport:** UDP port 69, 512-byte blocks, 2 s timeout, up to 10 retransmissions.
- **Framing:** a 2-byte big-endian opcode at the start of every packet. This is *not*
  standard RFC 1350 TFTP — it reuses port 69, but the packet format is proprietary.
- **Commands covered:** status, info, directory listing (including subfolders), download,
  upload, delete, rename, create folder, and send a message to the operator.
- Full details and annotated opcodes are in `dnc_tftp.py`.

## Known limitations

- The protocol controls files and transfers, not navigation of the device's own display.
- Filenames with special characters may be altered by the device firmware — a limitation
  of its FatFs implementation, not of this client.
- The FOCAS transfer functions (`cnc_upstart4` / `cnc_dwnstart4`, etc.) vary in format
  across control versions. The affected spots are marked with `# AJUSTE?` in
  `foco_transfer.py`.

## License

See [`LICENSE`](LICENSE).
