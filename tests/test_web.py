"""
Tests for the shop-floor panel and the transfer log.

The panel deliberately has no login - the factory network is the trust
boundary. That makes the CSRF token load-bearing rather than ceremonial: it is
the only thing standing between "an operator opened some other web page on the
same phone" and "a program was fired at the machine". So it gets a test that
asserts the transport was never touched, not merely that the response was a
redirect.

No hardware required.
"""

import config
import dnc_web
import pytest
import transfer_log


class FakeTransport:
    name = "fake"
    can_listen = True
    can_browse = False
    can_fetch = False

    def __init__(self, fail_with=None):
        self.sent = []
        self.fail_with = fail_with

    def describe(self):
        return "fake transport"

    def send(self, local_path, remote_name=None):
        if self.fail_with:
            raise self.fail_with
        with open(local_path, "rb") as f:
            data = f.read()
        self.sent.append((local_path, remote_name, len(data)))
        return len(data)

    def close(self):
        pass


@pytest.fixture
def panel(monkeypatch, tmp_path):
    """A test client with isolated programs folder, log file and transport."""
    programs = tmp_path / "programs"
    programs.mkdir()
    monkeypatch.setattr(config, "PROGRAMS_DIR", str(programs))
    monkeypatch.setattr(config, "LOG_FILE", str(tmp_path / "dnc_log.csv"))
    monkeypatch.setattr(config, "FIXED_SEND_FILE", "1")
    monkeypatch.setattr(config, "MACHINE_NAME", "LATHE-01")

    tr = FakeTransport()
    monkeypatch.setattr(dnc_web, "_transport", tr)
    monkeypatch.setattr(dnc_web, "_status", dict.fromkeys(dnc_web._status, None))

    dnc_web.app.config["TESTING"] = True
    return dnc_web.app.test_client(), tr, programs


def queue_program(programs, body=b"G01 X10\n"):
    (programs / "1").write_bytes(body)


# ---------------------------------------------------------------- rendering

def test_panel_reports_an_empty_queue(panel):
    client, _, _ = panel
    body = client.get("/").get_data(as_text=True)
    assert "Nothing queued" in body
    assert "LATHE-01" in body
    assert "disabled" in body, "Send must be disabled with nothing to send"


def test_panel_reports_a_queued_program(panel):
    client, _, programs = panel
    queue_program(programs)
    body = client.get("/").get_data(as_text=True)
    assert "Program queued" in body
    assert "8 bytes" in body


def test_panel_escapes_the_machine_name(panel, monkeypatch):
    """The name comes from config, which comes from a file or an env var."""
    monkeypatch.setattr(config, "MACHINE_NAME", "<script>alert(1)</script>")
    client, _, _ = panel
    body = client.get("/").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# ---------------------------------------------------------------- CSRF

def test_send_without_a_token_never_reaches_the_machine(panel):
    client, tr, programs = panel
    queue_program(programs)

    resp = client.post("/send", data={})
    assert resp.status_code == 302
    assert tr.sent == [], "a tokenless POST reached the machine"


def test_send_with_a_wrong_token_never_reaches_the_machine(panel):
    client, tr, programs = panel
    queue_program(programs)

    client.post("/send", data={"_token": "not-the-token"})
    assert tr.sent == []


def test_send_with_the_right_token_goes_through(panel):
    client, tr, programs = panel
    queue_program(programs)

    client.post("/send", data={"_token": dnc_web.CSRF_TOKEN}, follow_redirects=True)
    assert len(tr.sent) == 1
    assert tr.sent[0][1] == "1"
    assert tr.sent[0][2] == 8


# ---------------------------------------------------------------- send outcomes

def test_sending_an_empty_queue_is_refused(panel):
    client, tr, _ = panel
    ok, msg = dnc_web.send_queued()
    assert ok is False
    assert "Nothing queued" in msg
    assert tr.sent == []


def test_a_successful_send_is_logged(panel):
    client, _, programs = panel
    queue_program(programs)
    dnc_web.send_queued()

    rows = transfer_log.tail()
    assert len(rows) == 1
    assert rows[0]["direction"] == "send"
    assert rows[0]["result"] == "ok"
    assert rows[0]["bytes"] == "8"
    assert rows[0]["machine"] == "LATHE-01"


def test_a_failed_send_is_logged_as_an_error(panel, monkeypatch):
    client, _, programs = panel
    queue_program(programs)
    monkeypatch.setattr(dnc_web, "_transport", FakeTransport(fail_with=RuntimeError("no ACK")))

    ok, msg = dnc_web.send_queued()
    assert ok is False
    assert "no ACK" in msg

    rows = transfer_log.tail()
    assert rows[0]["result"] == "error"
    assert "no ACK" in rows[0]["detail"]


def test_a_received_program_is_saved_and_logged(panel):
    client, _, programs = panel
    dnc_web.on_program_received(b"%\nO1234\n%\n")

    files = list(programs.glob("LATHE-01_*.nc"))
    assert len(files) == 1
    assert files[0].read_bytes() == b"%\nO1234\n%\n"

    rows = transfer_log.tail()
    assert rows[0]["direction"] == "receive"
    assert rows[0]["result"] == "ok"


def test_the_log_appears_on_the_panel(panel):
    client, _, programs = panel
    queue_program(programs)
    dnc_web.send_queued()

    body = client.get("/").get_data(as_text=True)
    assert "Transfer log" in body
    assert "Nothing transferred yet" not in body


# ---------------------------------------------------------------- transfer log

def test_log_survives_being_reread(panel):
    transfer_log.record("send", "O1", 10, "ok", "", "serial")
    transfer_log.record("receive", "O2", 20, "ok", "", "serial")
    rows = transfer_log.tail()
    assert [r["name"] for r in rows] == ["O2", "O1"]   # newest first


def test_log_never_raises_when_the_path_is_unwritable(panel, monkeypatch, tmp_path):
    """A logging failure must not abort a transfer that already succeeded.

    The unwritable path is built by putting a REGULAR FILE where a directory
    would have to be. A drive letter like Z:\\nope would do it on Windows but is
    a perfectly valid relative filename on Linux, so the write would quietly
    succeed there and the test would pass without testing anything.
    """
    blocker = tmp_path / "this-is-a-file-not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(config, "LOG_FILE", str(blocker / "log.csv"))

    transfer_log.record("send", "O1", 10, "ok")        # must not raise
    assert transfer_log.tail() == []


def test_log_detail_is_flattened_to_one_line(panel):
    transfer_log.record("send", "O1", None, "error", "line one\nline two")
    assert transfer_log.tail()[0]["detail"] == "line one line two"
