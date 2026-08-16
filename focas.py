"""
focas.py  -  Minimal ctypes wrapper around Fanuc's FOCAS library.
====================================================================
This is the reusable foundation of the FOCAS path: it loads the DLL, declares
the structures and offers connection helpers. The other FOCAS scripts import it.

Requires: 32-bit Python + Fwlib32.dll (and its companion DLLs) in BASE_DIR.

The DLL is loaded LAZILY, on first use. Importing this module must never kill
the process - the serial and DNC-box transports have to keep working on a
64-bit PC or a Raspberry Pi that will never have a Fanuc DLL.
"""

import ctypes
import os

import config

# ---- Common FOCAS return codes ----
EW_OK     = 0    # success
EW_BUFFER = 10   # buffer full/empty -> retry (not a fatal error)


class FocasUnavailable(RuntimeError):
    """The FOCAS library could not be loaded on this machine."""


class FocasError(RuntimeError):
    """The library loaded, but a call failed."""


# ---- ODBSYS structure (returned by cnc_sysinfo) ----
# NOTE: the layout varies slightly between fwlib versions.
class ODBSYS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("addinfo",  ctypes.c_short),
        ("max_axis", ctypes.c_char),
        ("cnc_type", ctypes.c_char * 2),   # e.g. "30" / "0i"
        ("mt_type",  ctypes.c_char * 2),   # "M"=mill/machining centre, "T"=lathe
        ("series",   ctypes.c_char * 4),
        ("version",  ctypes.c_char * 4),
        ("axes",     ctypes.c_char * 2),
    ]


_lib = None


def get_lib():
    """Load Fwlib32.dll on first use and declare the signatures.

    Raises FocasUnavailable with an actionable message instead of exiting -
    a library has no business calling sys.exit() on its importer.
    """
    global _lib
    if _lib is not None:
        return _lib

    dll_path = os.path.join(config.BASE_DIR, config.DLL_NAME)
    os.environ["PATH"] = config.BASE_DIR + os.pathsep + os.environ.get("PATH", "")

    if not hasattr(ctypes, "windll"):
        raise FocasUnavailable(
            "FOCAS only works on Windows: Fanuc ships Fwlib32.dll, a 32-bit "
            "Windows DLL. Use the serial or DNC-box transport on Linux/Raspberry Pi."
        )
    if ctypes.sizeof(ctypes.c_void_p) != 4:
        raise FocasUnavailable(
            f"{config.DLL_NAME} is 32-bit and this is 64-bit Python, which cannot "
            "load it. Install the 32-bit build and run with:  py -3-32"
        )
    try:
        lib = ctypes.windll.LoadLibrary(dll_path)   # windll = stdcall, correct for FOCAS
    except OSError as e:
        raise FocasUnavailable(
            f"Could not load {dll_path}: {e}. Is the DLL - and its companion DLLs, "
            "e.g. fwlibe1.dll - in the project folder? They ship with the machine "
            "or the official Fanuc SDK and are not redistributed here."
        ) from e

    # Declaring signatures avoids silent ctypes type bugs.
    lib.cnc_allclibhndl3.argtypes = [
        ctypes.c_char_p, ctypes.c_ushort, ctypes.c_long,
        ctypes.POINTER(ctypes.c_ushort),
    ]
    lib.cnc_allclibhndl3.restype = ctypes.c_short
    lib.cnc_freelibhndl.argtypes = [ctypes.c_ushort]
    lib.cnc_freelibhndl.restype  = ctypes.c_short
    lib.cnc_sysinfo.argtypes = [ctypes.c_ushort, ctypes.POINTER(ODBSYS)]
    lib.cnc_sysinfo.restype  = ctypes.c_short

    _lib = lib
    return _lib


def connect():
    """Open a connection to the machine. Returns the handle (c_ushort)."""
    lib = get_lib()
    handle = ctypes.c_ushort(0)
    ip = config.CNC_IP.encode()
    ret = lib.cnc_allclibhndl3(ip, config.CNC_PORT, config.TIMEOUT, ctypes.byref(handle))
    if ret != EW_OK:
        raise FocasError(
            f"Connection failed (code {ret}). Check the IP and port, that the "
            "machine answers a ping, and that the FOCAS option is enabled on it."
        )
    return handle


def disconnect(handle):
    """Release the handle (always call this at the end)."""
    get_lib().cnc_freelibhndl(handle)


def sysinfo(handle):
    """Read the machine's identity (ODBSYS)."""
    info = ODBSYS()
    ret = get_lib().cnc_sysinfo(handle, ctypes.byref(info))
    if ret != EW_OK:
        raise FocasError(f"cnc_sysinfo failed (code {ret})")
    return info
