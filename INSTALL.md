# Installation

Two independent paths. The FOCAS one is the fussy one (it needs a proprietary
Fanuc DLL and 32-bit Python); the serial and DNC-box ones are plain Python.

---

## Path A — FOCAS on a Windows laptop

### Step 1 — Install **32-bit** Python
> ⚠️ It **must** be 32-bit, because `Fwlib32.dll` is 32-bit. 64-bit Python **cannot** load it.
> ⚠️ Do **NOT** use the Microsoft Store `python` shortcut (the stub causes trouble). Install the real one.

1. Download from **https://www.python.org/downloads/windows/** →
   look for **"Windows installer (32-bit)"** (not 64-bit / ARM64).
2. Install with **"Add python.exe to PATH"** ticked.
3. Confirm it really is 32-bit (open `cmd`):
   ```
   py -3-32 -c "import struct; print(struct.calcsize('P')*8)"
   ```
   It must print **32**. If it prints 64, you are picking up the wrong Python.

### Step 2 — Drop in the FOCAS library
1. Get **`Fwlib32.dll`** (it comes with the machine or the Fanuc SDK) **and its
   companion DLLs** (e.g. `fwlibe1.dll`, `fwlib0DN.dll`).
2. Copy **all** of them into the project folder, next to the scripts.

They are deliberately git-ignored: they are Fanuc's, not ours, and are not redistributed here.

### Step 3 — Set the IP
Edit `config.py` (or set the environment variables — see below):
```python
CNC_IP   = setting("CNC_IP", "192.168.1.50")   # the IP shown on the machine
CNC_PORT = setting("CNC_PORT", 8193, int)      # check the [FOCAS2] screen
```

### Step 4 — Put the machine on the network
The laptop must be on the same network/range as the machine, and **`ping` must answer**.
Without a ping, nothing else will work.

### Step 5 — Run
Easy way: double-click **`run.bat`** and pick an option.

Or from `cmd`, in the folder:
```
py -3-32 focas_probe.py       (connection test)
py -3-32 focas_transfer.py    (send/receive)
```

If the probe prints the machine's type and series → **it works**. 🎉

---

## Path B — Serial or DNC box on a Raspberry Pi

No DLL, no 32-bit requirement, works on any Python 3.

```bash
sudo apt install -y python3-pip
pip3 install pyserial flask
```

Point it at the right hardware and start the panel:

```bash
DNCKIT_TRANSPORT=serial \
DNCKIT_SERIAL_PORT=/dev/ttyUSB0 \
DNCKIT_MACHINE_NAME=LATHE-01 \
python3 dnc_web.py
```

Then open `http://<pi-ip>:5000/` from a phone or PC.

To keep it running across reboots, edit the paths and machine name in
`dnc-web.service` and install it:

```bash
sudo cp dnc-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dnc-web
journalctl -u dnc-web -f
```

> ⚠️ For `TRANSPORT=dnc-box`: the box only answers datagrams coming **from** local
> UDP port 69, and ports below 1024 are privileged on Linux. The unit file already
> grants `CAP_NET_BIND_SERVICE`; if you run it by hand, use `sudo`.

---

## Configuration without editing code

Any setting in `config.py` can be overridden by an environment variable named
`DNCKIT_` + the setting name, or by a `dnc_config.json` file next to it:

```json
{ "MACHINE_NAME": "LATHE-01", "SERIAL_PORT": "/dev/ttyUSB0", "SERIAL_BAUD": 4800 }
```

That is what keeps a fleet of Pis on one identical copy of the code.

---

## Checking it works without a machine

```bash
pip install pytest
python -m pytest tests/ -q
```

The suite talks to a scripted fake device and a fake serial port, so it proves the
protocol and the transfer logic are intact even with no hardware on the bench.

---

## Common problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `Could not load Fwlib32.dll` | 64-bit Python, or missing companion DLLs | Use 32-bit Python; put every DLL in the folder |
| `Connection failed (code ...)` | No ping / wrong IP / FOCAS not enabled | Check ping, `config.py`, and the FOCAS option on the machine |
| Machine fields look scrambled | `ODBSYS` struct alignment | Small tweak in `focas.py` — the layout varies by fwlib version |
| `cnc_dwnstart4 failed` when sending | Machine not in EDIT / program protected | Put the machine in EDIT and turn program protection off |
| `Cannot bind local UDP port 69` | Not root on Linux, or another instance running | `sudo`, or grant `CAP_NET_BIND_SERVICE`; check nothing else is up |
| Panel shows a program but Send is greyed out | The queued file is missing | Drop the next program in as the file named in `FIXED_SEND_FILE` |

> ⚠️ **The FOCAS transfer step is the one most likely to need tuning on the real
> machine, and it has never been run against a control.** The connection probe
> (`focas_probe.py`) is what proves the concept — start there.
