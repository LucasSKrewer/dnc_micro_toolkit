"""
Tests for the FOCAS telemetry readings.

None of this can be validated without a control, and these tests do not pretend
otherwise: the ctypes structure layouts are exactly the part a test cannot
check, because the wrong layout returns plausible nonsense rather than an error.

What IS worth pinning is everything around the call - the alarm bit decoding,
and the promise that one unsupported function costs its own row instead of
taking the whole snapshot down. A shop floor tool that shows nothing because
the control lacks cnc_rdexecprog would be useless.

No hardware required.
"""

from unittest.mock import MagicMock

import pytest

import focas
import focas_telemetry as ft


# ---------------------------------------------------------------- alarm bits

def test_no_alarm_bits_means_no_alarm():
    assert ft.decode_alarms(0) == []


def test_a_single_alarm_bit_is_named():
    assert ft.decode_alarms(1 << 4) == ["OT (overtravel)"]
    assert ft.decode_alarms(1 << 6) == ["SV (servo)"]


def test_several_alarms_come_back_in_specification_order():
    mask = (1 << 9) | (1 << 3) | (1 << 4)      # SP, PS, OT
    assert ft.decode_alarms(mask) == [
        "PS (foreground P/S)", "OT (overtravel)", "SP (spindle)"]


def test_the_reserved_bit_12_is_not_reported():
    """Bit 12 has no meaning in the spec; inventing a name for it would be a lie."""
    assert ft.decode_alarms(1 << 12) == []


def test_every_alarm_bit_at_once_is_still_readable():
    mask = 0xFFFF
    active = ft.decode_alarms(mask)
    assert len(active) == len(ft.ALARM_BITS)


# ---------------------------------------------------------------- decoding

def test_known_codes_decode_to_names():
    assert ft.describe(ft.RUN_STATE, 3) == "running"
    assert ft.describe(ft.AUT_MODE, 1) == "MEM"
    assert ft.describe(ft.EDIT_STATE, 0) == "idle"


def test_an_unknown_code_is_surfaced_not_hidden():
    """A control that reports something outside the spec must not read as normal."""
    assert ft.describe(ft.RUN_STATE, 99) == "?99"


# ---------------------------------------------------------------- readings

def test_read_status_decodes_a_control_sitting_at_reset():
    lib = MagicMock()
    lib.cnc_statinfo.return_value = focas.EW_OK      # struct stays zeroed
    st = ft.read_status(lib, 1)
    assert st["run"] == "reset"
    assert st["mode"] == "MDI"
    assert st["motion"] == "idle"
    assert st["emergency"] == "normal"
    assert st["alarm_flag"] is False


@pytest.mark.parametrize("reader,fn", [
    (ft.read_status, "cnc_statinfo"),
    (ft.read_program_numbers, "cnc_rdprgnum"),
    (ft.read_alarms, "cnc_alarm"),
    (ft.read_feedrate, "cnc_actf"),
    (ft.read_spindle, "cnc_acts"),
])
def test_a_nonzero_return_code_becomes_an_error(reader, fn):
    lib = MagicMock()
    getattr(lib, fn).return_value = -2               # EW_FUNC / not supported
    with pytest.raises(focas.FocasError, match="code -2"):
        reader(lib, 1)


# ---------------------------------------------------------------- snapshot

def test_snapshot_isolates_an_unsupported_call(monkeypatch):
    """The property that matters: one dead reading must not blank the screen."""
    def fine(lib, handle):
        return {"run": "running"}

    def unsupported(lib, handle):
        raise focas.FocasError("cnc_acts failed (code -2)")

    monkeypatch.setattr(ft, "READINGS",
                        [("status", fine), ("spindle", unsupported)])

    snap = ft.snapshot(handle=1, lib=MagicMock())

    assert snap["status"]["ok"] is True
    assert snap["status"]["run"] == "running"
    assert snap["spindle"]["ok"] is False
    assert "code -2" in snap["spindle"]["error"]
    assert "taken_at" in snap


def test_snapshot_survives_every_reading_failing(monkeypatch):
    def dead(lib, handle):
        raise focas.FocasError("nope")

    monkeypatch.setattr(ft, "READINGS", [(name, dead) for name, _ in ft.READINGS])
    snap = ft.snapshot(handle=1, lib=MagicMock())
    assert all(snap[name]["ok"] is False for name, _ in ft.READINGS)


# ---------------------------------------------------------------- formatting

def _snap(**overrides):
    base = {
        "taken_at": "2026-08-16 21:00:00",
        "status": {"ok": True, "run": "running", "mode": "MEM", "motion": "moving",
                   "emergency": "normal", "edit": "idle", "alarm_flag": False},
        "program": {"ok": True, "running": 1234, "main": 1234},
        "alarms": {"ok": True, "mask": 0, "active": []},
        "feedrate": {"ok": True, "feed": 250},
        "spindle": {"ok": True, "rpm": 1800},
        "block": {"ok": True, "block_number": 120, "text": "G01 X10. F250."},
    }
    base.update(overrides)
    return base


def test_format_shows_a_machine_that_is_cutting():
    text = ft.format_snapshot(_snap())
    assert "running / MEM / moving" in text
    assert "O1234" in text
    assert "F250" in text and "S1800" in text
    assert "none" in text                       # alarms


def test_format_names_the_unavailable_readings():
    text = ft.format_snapshot(_snap(
        spindle={"ok": False, "error": "cnc_acts failed (code -2)"},
        block={"ok": False, "error": "cnc_rdexecprog failed (code -2)"}))
    assert "S?" in text, "a missing spindle reading must not look like S0"
    assert "F250" in text, "the readings that DID work must still show"


def test_format_makes_an_emergency_stop_impossible_to_miss():
    text = ft.format_snapshot(_snap(
        status={"ok": True, "run": "stop", "mode": "MEM", "motion": "idle",
                "emergency": "EMERGENCY STOP", "edit": "idle", "alarm_flag": True}))
    assert "EMERGENCY" in text


def test_format_lists_active_alarms():
    text = ft.format_snapshot(_snap(
        alarms={"ok": True, "mask": 1 << 4, "active": ["OT (overtravel)"]}))
    assert "OT (overtravel)" in text


def test_format_reports_a_dead_status_call_instead_of_pretending():
    text = ft.format_snapshot(_snap(
        status={"ok": False, "error": "cnc_statinfo failed (code -16)"}))
    assert "unavailable" in text
    assert "code -16" in text
