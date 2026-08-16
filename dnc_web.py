"""
dnc_web.py  -  Shop-floor web panel, meant to run on a Raspberry Pi.
====================================================================
Replaces serial_adapter.py's text menu with a page reachable from anywhere on
the network (phone, PC). The Pi stays wired to the machine; nobody has to be
physically next to it.

The workflow (decided 2026-07-15):
- SEND: always sends the fixed file named config.FIXED_SEND_FILE out of
  PROGRAMS_DIR. Whoever swaps that file before each new part is production
  planning / engineering - the operator only presses Send. Same model as the
  commercial DNC boxes the setters already know.
- RECEIVE: fully automatic on transports that can listen (serial). The moment
  the machine punches data out, it is captured and saved with machine + date.
  There is no receive button.

Unlike the first version, this drives ANY transport (serial, DNC box, FOCAS)
through transport.py, and every transfer is written to transfer_log.py so the
record outlives a reboot.

Run: python3 dnc_web.py   (or as a systemd service - see dnc-web.service)
"""

import datetime
import hmac
import os
import secrets
import threading

from flask import Flask, flash, get_flashed_messages, redirect, render_template_string, request, url_for

import config
import transfer_log
import transport as transport_mod

app = Flask(__name__)
# A fixed fallback key would be shared by every install on every Pi. Random per
# process is right here: the only thing it signs is flash messages.
app.secret_key = os.environ.get("DNC_WEB_SECRET") or secrets.token_urlsafe(32)

# Synchronizer token. There is no login (the shop floor network is the trust
# boundary, by design) but that is exactly why this matters: without it, any
# web page an operator happens to open could POST to the Pi and fire a program
# at the machine. Same-origin policy stops that page from ever reading this.
CSRF_TOKEN = secrets.token_urlsafe(32)

_stop = threading.Event()
_transport = None
_status = {
    "sent_name": None, "sent_at": None, "sent_bytes": None,
    "received_name": None, "received_at": None, "received_bytes": None,
}


def get_transport():
    global _transport
    if _transport is None:
        _transport = transport_mod.build()
    return _transport


def _stamp():
    return datetime.datetime.now().strftime("%d/%m %H:%M:%S")


def _queued_path():
    return os.path.join(config.PROGRAMS_DIR, config.FIXED_SEND_FILE)


def send_queued():
    """Send the queued program. Returns (ok, message)."""
    path = _queued_path()
    if not os.path.exists(path):
        return False, (f'Nothing queued - no file named "{config.FIXED_SEND_FILE}" '
                       "in the programs folder.")

    tr = get_transport()
    try:
        n = tr.send(path, config.FIXED_SEND_FILE)
    except Exception as e:
        transfer_log.record("send", config.FIXED_SEND_FILE, None, "error", e, tr.name)
        return False, f"{type(e).__name__}: {e}"

    _status.update(sent_name=config.FIXED_SEND_FILE, sent_at=_stamp(), sent_bytes=n)
    transfer_log.record("send", config.FIXED_SEND_FILE, n, "ok", "", tr.name)
    return True, f"Sent - {n} bytes, verified."


def on_program_received(data):
    """Called by the listening transport for each complete program."""
    os.makedirs(config.PROGRAMS_DIR, exist_ok=True)
    name = datetime.datetime.now().strftime(f"{config.MACHINE_NAME}_%Y%m%d_%H%M%S.nc")
    with open(os.path.join(config.PROGRAMS_DIR, name), "wb") as f:
        f.write(data)
    _status.update(received_name=name, received_at=_stamp(), received_bytes=len(data))
    transfer_log.record("receive", name, len(data), "ok", "", get_transport().name)


def start_listener():
    """Start background receiving, if this transport is the kind that listens."""
    tr = get_transport()
    if not tr.can_listen:
        return None

    def run():
        while not _stop.is_set():
            try:
                tr.listen(on_program_received, _stop)
            except Exception as e:
                # This thread must never die: if it does, automatic receiving
                # stops for good and the first anyone knows is a missing program.
                transfer_log.record("receive", "-", None, "error", e, tr.name)
                _stop.wait(5)

    t = threading.Thread(target=run, daemon=True, name="dnc-listener")
    t.start()
    return t


PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>{{ machine }} - DNC</title>
<style>
  /* Industrial control panel. Read at arm's length, pressed with gloves on,
     in a room that is either glare-bright or dim. Everything here is sized for
     that, not for a desk. System fonts only - the Pi is offline. */
  :root {
    --bg:        #eceae3;
    --grain:     rgba(0,0,0,.025);
    --panel:     #fbfaf7;
    --edge:      #d3cfc4;
    --ink:       #16181c;
    --ink-dim:   #6b6a63;
    --go:        #1B5E20;
    --go-ink:    #ffffff;
    --alert:     #E8590C;
    --shadow:    rgba(20,22,18,.20);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:      #101216;
      --grain:   rgba(255,255,255,.022);
      --panel:   #191c22;
      --edge:    #2c3038;
      --ink:     #e9e7e1;
      --ink-dim: #8d9199;
      --go:      #47a34e;
      --go-ink:  #06140a;
      --alert:   #ff7a29;
      --shadow:  rgba(0,0,0,.55);
    }
  }

  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: clamp(1rem, 4vw, 2rem) 1rem 3rem;
    background: var(--bg);
    /* faint horizontal machining texture, so the page is not a flat slab */
    background-image: repeating-linear-gradient(
      0deg, var(--grain) 0 1px, transparent 1px 4px);
    color: var(--ink);
    font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
    font-size: 17px;
    line-height: 1.45;
    -webkit-text-size-adjust: 100%;
  }
  main { max-width: 34rem; margin: 0 auto; }

  /* ---- equipment nameplate ---- */
  .plate {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 1rem; flex-wrap: wrap;
    padding-bottom: .7rem;
  }
  .plate h1 {
    margin: 0; font-size: clamp(1.35rem, 6vw, 1.9rem); font-weight: 800;
    letter-spacing: .09em; text-transform: uppercase;
  }
  .plate .link {
    font-size: .74rem; letter-spacing: .16em; text-transform: uppercase;
    color: var(--ink-dim); font-weight: 700;
  }
  /* hazard rule: the one decorative element, and it earns its place by
     reading as shop-floor signage rather than web ornament */
  .hazard {
    height: 7px; border-radius: 2px; margin-bottom: 1.5rem;
    background: repeating-linear-gradient(
      -45deg, var(--alert) 0 9px, transparent 9px 18px);
    opacity: .85;
  }

  /* ---- status ---- */
  .status {
    display: flex; align-items: center; gap: .8rem;
    padding: 1rem 1.1rem; margin-bottom: 1rem;
    background: var(--panel); border: 1px solid var(--edge); border-radius: 4px;
  }
  /* color-mix() needs a 2023+ browser. Every rule that uses it is preceded by
     a flat fallback, because the browser on a shop-floor terminal is whatever
     was installed the day it was set up. */
  .lamp {
    width: 15px; height: 15px; border-radius: 50%; flex: none;
    background: var(--alert);
    box-shadow: 0 0 0 4px rgba(232,89,12,.22);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--alert) 22%, transparent);
  }
  .lamp.ready {
    background: var(--go);
    box-shadow: 0 0 0 4px rgba(27,94,32,.22);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--go) 22%, transparent);
  }
  .lamp.busy  { animation: blink 1.1s steps(1,end) infinite; }
  @keyframes blink { 50% { opacity: .25; } }
  @media (prefers-reduced-motion: reduce) { .lamp.busy { animation: none; } }
  .status .what { margin: 0; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; font-size: .82rem; }
  .status .detail { margin: .1rem 0 0; color: var(--ink-dim); font-size: .92rem; }

  /* ---- the button: a physical pushbutton, glove-sized ---- */
  form.go { margin: 0 0 1.6rem; }
  button.go {
    width: 100%; min-height: 4.6rem; padding: 1rem;
    font: inherit; font-size: 1.15rem; font-weight: 800;
    letter-spacing: .12em; text-transform: uppercase;
    color: var(--go-ink); background: var(--go);
    border: none; border-radius: 6px; cursor: pointer;
    box-shadow: 0 5px 0 #0d3512, 0 8px 18px var(--shadow);
    box-shadow: 0 5px 0 color-mix(in srgb, var(--go) 62%, #000), 0 8px 18px var(--shadow);
    transition: transform .06s ease, box-shadow .06s ease;
  }
  button.go:active {
    transform: translateY(4px);
    box-shadow: 0 1px 0 #0d3512, 0 2px 6px var(--shadow);
    box-shadow: 0 1px 0 color-mix(in srgb, var(--go) 62%, #000), 0 2px 6px var(--shadow);
  }
  button.go:focus-visible { outline: 3px solid var(--alert); outline-offset: 3px; }
  button.go[disabled] { opacity: .45; cursor: not-allowed; box-shadow: none; }

  /* ---- readouts ---- */
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; margin-bottom: 1.6rem; }
  @media (max-width: 26rem) { .grid { grid-template-columns: 1fr; } }
  .cell {
    background: var(--panel); border: 1px solid var(--edge); border-radius: 4px;
    padding: .85rem 1rem;
  }
  .cell h2 {
    margin: 0 0 .35rem; font-size: .68rem; font-weight: 700;
    letter-spacing: .15em; text-transform: uppercase; color: var(--ink-dim);
  }
  .cell p { margin: 0; font-size: .95rem; word-break: break-all; }
  .cell time { display: block; color: var(--ink-dim); font-size: .82rem; margin-top: .15rem;
               font-variant-numeric: tabular-nums; }

  /* ---- log ---- */
  h2.section {
    font-size: .68rem; letter-spacing: .15em; text-transform: uppercase;
    color: var(--ink-dim); margin: 0 0 .5rem;
  }
  .log { border: 1px solid var(--edge); border-radius: 4px; overflow-x: auto; background: var(--panel); }
  table { border-collapse: collapse; width: 100%; font-size: .82rem;
          font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: .5rem .7rem; white-space: nowrap; }
  th { font-size: .64rem; letter-spacing: .12em; text-transform: uppercase;
       color: var(--ink-dim); border-bottom: 1px solid var(--edge); }
  tbody tr + tr td { border-top: 1px solid var(--edge); }
  td.n { text-align: right; }
  .tag { font-weight: 700; font-size: .7rem; letter-spacing: .08em; text-transform: uppercase; }
  .tag.ok { color: var(--go); }
  .tag.error { color: var(--alert); }
  .empty { padding: 1rem; color: var(--ink-dim); font-size: .9rem; margin: 0; }

  /* ---- flash ---- */
  .msg {
    padding: .85rem 1rem; border-radius: 4px; margin-bottom: 1rem;
    font-size: .95rem; border-left: 5px solid;
  }
  .msg.ok {
    border-color: var(--go); background: var(--panel);
    background: color-mix(in srgb, var(--go) 12%, var(--panel));
  }
  .msg.error {
    border-color: var(--alert); background: var(--panel);
    background: color-mix(in srgb, var(--alert) 12%, var(--panel));
  }
</style>
</head>
<body>
<main>
  <div class="plate">
    <h1>{{ machine }}</h1>
    <span class="link">{{ transport }}</span>
  </div>
  <div class="hazard"></div>

  {% for cat, msg in messages %}
    <div class="msg {{ 'ok' if cat == 'ok' else 'error' }}">{{ msg }}</div>
  {% endfor %}

  <div class="status">
    <span class="lamp {{ 'ready' if queued else '' }}"></span>
    <div>
      <p class="what">{{ 'Program queued' if queued else 'Nothing queued' }}</p>
      <p class="detail">
        {% if queued %}"{{ fixed_name }}" - {{ queued_bytes }} bytes, ready to send
        {% else %}Production planning needs to drop the next program in as "{{ fixed_name }}"
        {% endif %}
      </p>
    </div>
  </div>

  <form class="go" method="post" action="{{ url_for('send') }}">
    <input type="hidden" name="_token" value="{{ token }}">
    <button class="go" type="submit" {{ 'disabled' if not queued }}>Send program</button>
  </form>

  <div class="grid">
    <div class="cell">
      <h2>Last sent</h2>
      <p>{{ status.sent_name or '--' }}</p>
      {% if status.sent_at %}<time>{{ status.sent_at }} · {{ status.sent_bytes }} B</time>{% endif %}
    </div>
    <div class="cell">
      <h2>Last received{{ ' (auto)' if listening else '' }}</h2>
      <p>{{ status.received_name or '--' }}</p>
      {% if status.received_at %}<time>{{ status.received_at }} · {{ status.received_bytes }} B</time>{% endif %}
    </div>
  </div>

  <h2 class="section">Transfer log</h2>
  <div class="log">
    {% if log %}
    <table>
      <thead><tr><th>When</th><th>Dir</th><th>Program</th><th class="n">Bytes</th><th>Result</th></tr></thead>
      <tbody>
        {% for row in log %}
        <tr>
          <td>{{ row.timestamp }}</td>
          <td>{{ row.direction }}</td>
          <td>{{ row.name }}</td>
          <td class="n">{{ row.bytes }}</td>
          <td><span class="tag {{ 'ok' if row.result == 'ok' else 'error' }}">{{ row.result }}</span></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="empty">Nothing transferred yet.</p>
    {% endif %}
  </div>
</main>
{% if not messages %}
<script>setTimeout(function () { location.reload(); }, 20000);</script>
{% endif %}
</body>
</html>
"""


@app.route("/")
def index():
    path = _queued_path()
    queued = os.path.exists(path)
    tr = get_transport()
    return render_template_string(
        PAGE,
        machine=config.MACHINE_NAME,
        transport=tr.describe(),
        listening=tr.can_listen,
        fixed_name=config.FIXED_SEND_FILE,
        queued=queued,
        queued_bytes=os.path.getsize(path) if queued else 0,
        status=_status,
        log=transfer_log.tail(12),
        token=CSRF_TOKEN,
        messages=get_flashed_messages(with_categories=True),
    )


@app.route("/send", methods=["POST"])
def send():
    if not hmac.compare_digest(request.form.get("_token", ""), CSRF_TOKEN):
        flash("Stale page - reload and press Send again.", "error")
        return redirect(url_for("index"))
    try:
        ok, msg = send_queued()
    except Exception as e:
        ok, msg = False, f"Error: {e}"
    flash(msg, "ok" if ok else "error")
    return redirect(url_for("index"))


def main():
    os.makedirs(config.PROGRAMS_DIR, exist_ok=True)
    tr = get_transport()
    print(f"Transport: {tr.describe()}")
    if tr.can_listen:
        start_listener()
        print("Automatic receiving: ON (port stays open)")
    else:
        print("Automatic receiving: not applicable to this transport")
    print(f"Panel on http://{config.WEB_HOST}:{config.WEB_PORT}/")
    try:
        app.run(host=config.WEB_HOST, port=config.WEB_PORT)
    finally:
        _stop.set()
        tr.close()


if __name__ == "__main__":
    main()
