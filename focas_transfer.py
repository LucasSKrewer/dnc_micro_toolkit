"""
focas_transfer.py  -  PHASE 4: send and receive programs over FOCAS.
====================================================================
Simple menu to:
  1) RECEIVE a program from the CNC -> saves to programs/<number>.nc
  2) SEND    a .nc from the PC      -> writes into the CNC's memory

*** WARNING: this is the part most likely to need TUNING ON THE MACHINE. ***
The FOCAS transfer functions vary in "type"/format between control versions.
The skeleton below follows the specification, but may need small adjustments
when run for real (marked with  # CHECK? ).

Run (cmd, in the project folder):  py -3-32 focas_transfer.py
"""

import ctypes
import os

import config
import focas

_signatures_done = False


def _lib():
    """Load the DLL and declare the transfer signatures once."""
    global _signatures_done
    lib = focas.get_lib()
    if not _signatures_done:
        lib.cnc_upstart4.argtypes = [ctypes.c_ushort, ctypes.c_short, ctypes.c_char_p]
        lib.cnc_upstart4.restype  = ctypes.c_short
        lib.cnc_upload4.argtypes  = [ctypes.c_ushort, ctypes.POINTER(ctypes.c_long), ctypes.c_char_p]
        lib.cnc_upload4.restype   = ctypes.c_short
        lib.cnc_upend4.argtypes   = [ctypes.c_ushort]
        lib.cnc_upend4.restype    = ctypes.c_short

        lib.cnc_dwnstart4.argtypes  = [ctypes.c_ushort, ctypes.c_short]  # CHECK? some versions want (h, type, path)
        lib.cnc_dwnstart4.restype   = ctypes.c_short
        lib.cnc_download4.argtypes  = [ctypes.c_ushort, ctypes.POINTER(ctypes.c_long), ctypes.c_char_p]
        lib.cnc_download4.restype   = ctypes.c_short
        lib.cnc_dwnend4.argtypes    = [ctypes.c_ushort]
        lib.cnc_dwnend4.restype     = ctypes.c_short
        _signatures_done = True
    return lib


def receive(handle, program_number):
    """Read a program off the CNC and save it to programs/<number>.nc"""
    lib = _lib()
    ret = lib.cnc_upstart4(handle, 0, program_number.encode())   # CHECK? type 0; format "O1234"
    if ret != focas.EW_OK:
        raise focas.FocasError(f"cnc_upstart4 failed (code {ret})")

    chunks = []
    buf = ctypes.create_string_buffer(1290)
    try:
        while True:
            length = ctypes.c_long(1280)
            ret = lib.cnc_upload4(handle, ctypes.byref(length), buf)
            if ret == focas.EW_BUFFER:
                continue                        # no data yet -> retry
            if ret != focas.EW_OK:
                break
            chunk = buf.raw[:length.value]
            chunks.append(chunk)
            if chunk.rstrip().endswith(b"%"):   # "%" marks the end of the program
                break
    finally:
        lib.cnc_upend4(handle)

    os.makedirs(config.PROGRAMS_DIR, exist_ok=True)
    name = program_number.lstrip("Oo") + ".nc"
    dest = os.path.join(config.PROGRAMS_DIR, name)
    with open(dest, "wb") as f:
        f.write(b"".join(chunks))
    return dest


def send(handle, path):
    """Send a .nc file from the PC into the CNC's memory."""
    lib = _lib()
    with open(path, "rb") as f:
        data = f.read()

    # ensure the % ... % tape wrapper (Fanuc format)
    if not data.lstrip().startswith(b"%"):
        data = b"%\n" + data
    if not data.rstrip().endswith(b"%"):
        data = data.rstrip() + b"\n%\n"

    ret = lib.cnc_dwnstart4(handle, 0)         # CHECK? type; machine must be in EDIT
    if ret != focas.EW_OK:
        raise focas.FocasError(
            f"cnc_dwnstart4 failed (code {ret}). Is the machine in EDIT mode? "
            "Is program protection off?"
        )

    try:
        i = 0
        while i < len(data):
            chunk = data[i:i + 256]
            length = ctypes.c_long(len(chunk))
            ret = lib.cnc_download4(handle, ctypes.byref(length), chunk)
            if ret == focas.EW_BUFFER:
                continue
            if ret != focas.EW_OK:
                raise focas.FocasError(f"cnc_download4 failed (code {ret})")
            i += len(chunk)
    finally:
        lib.cnc_dwnend4(handle)
    return len(data)


def menu():
    print("Connecting to", config.CNC_IP, "...")
    handle = focas.connect()
    print("Connected.")
    try:
        while True:
            print("\n==== TRANSFER ====")
            print("1) Receive a program from the CNC")
            print("2) Send a program to the CNC")
            print("0) Quit")
            choice = input("> ").strip()
            if choice == "1":
                number = input("Program number (e.g. O1234): ").strip()
                try:
                    print("OK. Saved to:", receive(handle, number))
                except Exception as e:
                    print("ERROR:", e)
            elif choice == "2":
                path = input("Path to the .nc file: ").strip().strip('"')
                try:
                    send(handle, path)
                    print("OK. Sent to the CNC.")
                except Exception as e:
                    print("ERROR:", e)
            elif choice == "0":
                break
            else:
                print("Invalid option.")
    finally:
        focas.disconnect(handle)
        print("Disconnected.")


if __name__ == "__main__":
    menu()
