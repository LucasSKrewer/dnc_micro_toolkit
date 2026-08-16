"""
focas_probe.py  -  PHASE 3: the first FOCAS "hello".
Connects to the machine, reads its identity and disconnects. Transfers nothing.

This is the step that proves the concept - start here, before trying to move
any program. If it prints the machine's type and series, the hard part is done.

Run (cmd, in the project folder):  py -3-32 focas_probe.py
"""

import config
import focas


def main():
    print("Connecting to", config.CNC_IP, "...")
    handle = focas.connect()
    print("Connected. (handle =", handle.value, ")")

    try:
        info = focas.sysinfo(handle)
        text = lambda b: b.decode(errors="ignore").strip()
        print("------- MACHINE -------")
        print("CNC type :", text(info.cnc_type))
        print("M/T      :", text(info.mt_type), "(M=mill/centre, T=lathe)")
        print("Series   :", text(info.series))
        print("Version  :", text(info.version))
        print("Axes     :", text(info.axes))
        print("-----------------------")
    finally:
        focas.disconnect(handle)
        print("Disconnected.")


if __name__ == "__main__":
    main()
