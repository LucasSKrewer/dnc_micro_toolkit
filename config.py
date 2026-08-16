# config.py - Central configuration.
#
# Three ways to set a value, in increasing order of priority:
#   1. the defaults written below;
#   2. a dnc_config.json file next to this one (git-ignored);
#   3. an environment variable DNCKIT_<NAME>, e.g. DNCKIT_SERIAL_PORT.
#
# The last two exist so that a fleet of Raspberry Pis can run the SAME code
# with different settings - one machine per Pi - instead of every install
# carrying its own edited copy of this file.

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_OVERRIDES = {}
_JSON_PATH = os.path.join(BASE_DIR, "dnc_config.json")
if os.path.exists(_JSON_PATH):
    with open(_JSON_PATH, encoding="utf-8") as _f:
        _OVERRIDES = json.load(_f)


def _as_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "sim")


def setting(name, default, cast=str):
    """Resolve one setting: environment > dnc_config.json > default."""
    env = os.environ.get("DNCKIT_" + name)
    if env is not None:
        return cast(env)
    if name in _OVERRIDES:
        return cast(_OVERRIDES[name])
    return default


# ---------- Which transport this install drives ----------
# "serial" | "dnc-box" | "focas"
TRANSPORT = setting("TRANSPORT", "serial")

# ---------- Machine (Fanuc, over FOCAS) ----------
CNC_IP   = setting("CNC_IP", "192.168.1.50")   # the IP shown on the machine's screen
CNC_PORT = setting("CNC_PORT", 8193, int)      # FOCAS port (Fanuc default; check [FOCAS2])
TIMEOUT  = setting("TIMEOUT", 10, int)         # connection timeout, in seconds

# ---------- Folders / DLL ----------
PROGRAMS_DIR = setting("PROGRAMS_DIR", os.path.join(BASE_DIR, "programs"))
DLL_NAME     = setting("DLL_NAME", "Fwlib32.dll")   # put it in BASE_DIR
LOG_FILE     = setting("LOG_FILE", os.path.join(BASE_DIR, "dnc_log.csv"))

# ---------- Serial machine (Romi, Siemens 802D, older Fanuc with no Ethernet) ----------
# Match the REAL parameters of your machine (the control's RS232 screen).
# If the cable is the same kit that came with the DNC box, the handshake is
# usually strapped on the machine side -> software flow control (XON/XOFF).
#
# ON A RASPBERRY PI the FTDI port is "/dev/ttyUSB0".
# ON WINDOWS (if you test on a PC first) it is "COMx" (see Device Manager).
SERIAL_PORT     = setting("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD     = setting("SERIAL_BAUD", 9600, int)
SERIAL_BYTESIZE = setting("SERIAL_BYTESIZE", 7, int)
SERIAL_PARITY   = setting("SERIAL_PARITY", "E")         # "E"=even, "O"=odd, "N"=none
SERIAL_STOPBITS = setting("SERIAL_STOPBITS", 2, int)
SERIAL_FLOW     = setting("SERIAL_FLOW", "xonxoff")     # "xonxoff" (software) or "rtscts"

# ---------- DNC box (Micro DNC 2) - custom TFTP-like protocol over the network ----------
# Discovered by reverse engineering QSExplorer 4.06 (see dnc_tftp.py).
DNC_IP   = setting("DNC_IP", "192.168.1.236")   # the real IP of your box
DNC_PORT = setting("DNC_PORT", 69, int)         # TFTP (DEFAULT_PORT)

# Read every uploaded program back off the device and compare it byte for byte.
# On a CNC a wrong program is a crash, not a typo - leave this on.
VERIFY_UPLOAD = setting("VERIFY_UPLOAD", True, _as_bool)

# ---------- Web (dnc_web.py) ----------
# The model: sending is a "fixed file" (the operator just presses Send; whoever
# swaps the file before each new part is production planning / engineering),
# and receiving is automatic, always listening in the background.
FIXED_SEND_FILE = setting("FIXED_SEND_FILE", "1")   # the queued program in PROGRAMS_DIR
WEB_HOST = setting("WEB_HOST", "0.0.0.0")
WEB_PORT = setting("WEB_PORT", 5000, int)

# Identifies this installation (one Pi per machine, so this is fixed per
# install) - it goes into the filename of anything received automatically.
MACHINE_NAME = setting("MACHINE_NAME", "MACHINE")
