# -*- coding: latin-1 -*-

import html
import re
import socket
import time
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SBRC_IP = "192.168.1.14"
SBRC_PORT = 2202
WEB_PORT = 8083
REFRESH_SECONDS = 120

FIELDS = [
    "BATT_STATE",
    "BATT_CHARGE",
    "BATT_TIME_TO_FULL",
    "BATT_HEALTH",
    "BATT_CYCLE",
    "BATT_TEMP_C",
]

cache_lock = threading.Lock()
cached_slots = None
cached_error = "Status wordt opgehaald..."
cached_updated = 0

def read_charger():
    slots = {number: {} for number in range(1, 9)}

    try:
        sock = socket.create_connection((SBRC_IP, SBRC_PORT), 3)
        sock.settimeout(0.3)

        for field in FIELDS:
            sock.sendall(f"< GET 0 {field} >".encode())

        data = ""
        end_time = time.time() + 3

        while time.time() < end_time:
            try:
                data += sock.recv(8192).decode(errors="replace")
            except socket.timeout:
                pass

        sock.close()


        replies = re.findall(
            r"< REP (\d+) ([A-Z_]+) ([^ >]+) >",
            data
        )

        for number, field, value in replies:
            slot = int(number)
            if slot in slots:
                slots[slot][field] = value

    except OSError as error:
        return None, str(error)

    return slots, None
def refresh_cache():
    global cached_slots, cached_error, cached_updated

    while True:
        slots, error = read_charger()

        with cache_lock:
            cached_slots = slots
            cached_error = error
            cached_updated = time.time()

        time.sleep(REFRESH_SECONDS)


def get_cached_status():
    with cache_lock:
        return cached_slots, cached_error, cached_updated

def display_time(value):
    if value == "65529":
        return "Vol"
    if value in ("65535", "255", ""):
        return "?"

    try:
        minutes = int(value)
        return f"{minutes // 60}u {minutes % 60:02d}m"
    except ValueError:
        return value


def display_temperature(value):
    if value in ("255", ""):
        return "?"

    try:
        return f"{int(value) - 40} °C"
    except ValueError:
        return value


def card(slot, values):
    state = values.get("BATT_STATE", "ONBEKEND")
    charge = values.get("BATT_CHARGE", "255")

    if state == "NO_BATT" or charge == "255":
        return f"""
        <section class="slot empty">
          <h2>Slot {slot}</h2>
          <div class="empty-text">Leeg</div>
        </section>
        """

    try:
        percentage = int(charge)
    except ValueError:
        percentage = 0

    status_class = "full" if state == "FULL" else "charging"

    return f"""
    <section class="slot {status_class}">
      <h2>Slot {slot}</h2>
      <div class="state">{html.escape(state)}</div>
      <div class="percentage">{percentage}%</div>
      <div class="bar"><div class="fill" style="width: {percentage}%"></div></div>
      <dl>
        <div><dt>Naar vol</dt><dd>{display_time(values.get("BATT_TIME_TO_FULL", ""))}</dd></div>
        <div><dt>Conditie</dt><dd>{html.escape(values.get("BATT_HEALTH", "?"))}%</dd></div>
        <div><dt>Laadcycli</dt><dd>{html.escape(values.get("BATT_CYCLE", "?"))}</dd></div>
        <div><dt>Temperatuur</dt><dd>{display_temperature(values.get("BATT_TEMP_C", ""))}</dd></div>
      </dl>
    </section>
    """

def page(slots, error, updated):
    updated_text = (
        time.strftime("%H:%M:%S", time.localtime(updated))
        if updated else "--"
    )

    if error:
        content = f"""
        <main>
          <h1>Shure SBRC-e</h1>
          <p class="error">Geen verbinding met de lader: {html.escape(error)}</p>
        </main>
        """
    else:
        cards = "".join(card(slot, slots[slot]) for slot in range(1, 9))
        content = f"""
        <main>
          <header>
            <div>
              <h1>Shure SBRC-e</h1>
              <p>Accustatus - automatisch vernieuwd elke {REFRESH_SECONDS // 60} minuten</p>
            </div>
            <div class="header-right">
  <div class="updated">Bijgewerkt: {updated_text}</div>

  <a class="dashboard-button"
     href="#"
     onclick="window.location.href='http://' + location.hostname + ':8080'; return false;">
     <span class="back-arrow">&#11013;</span> Terug naar dashboard
  </a>
</div>
          </header>
          <div class="grid">{cards}</div>
        </main>
        """

    return f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{REFRESH_SECONDS}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SBRC-e Accustatus</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #111827;
      color: #f9fafb;
      font-family: Arial, sans-serif;
    }}
    main {{ max-width: 1200px; margin: auto; padding: 28px; }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      margin-bottom: 24px;
    }}
    h1 {{ margin: 0; font-size: 2rem; }}
    header p {{ color: #9ca3af; margin: 7px 0 0; }}
    .updated {{ color: #9ca3af; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 16px;
    }}
    .slot {{
      background: #1f2937;
      border: 2px solid #374151;
      border-radius: 12px;
      padding: 18px;
    }}
    .slot.full {{ border-color: #22c55e; }}
    .slot.charging {{ border-color: #f59e0b; }}
    .slot.empty {{ opacity: .55; }}
    h2 {{ margin: 0 0 12px; }}
    .state {{ color: #d1d5db; font-size: .9rem; }}
    .percentage {{ font-size: 2.5rem; font-weight: bold; margin: 8px 0; }}
    .bar {{
      height: 12px;
      background: #111827;
      border-radius: 999px;
      overflow: hidden;
      margin-bottom: 16px;
    }}
    .fill {{ height: 100%; background: #22c55e; }}
    .charging .fill {{ background: #f59e0b; }}
    dl {{ margin: 0; }}
    dl div {{
      display: flex;
      justify-content: space-between;
      border-top: 1px solid #374151;
      padding: 7px 0;
    }}
    dt {{ color: #9ca3af; }}
    dd {{ margin: 0; font-weight: bold; }}
    .empty-text {{ font-size: 1.6rem; color: #9ca3af; margin-top: 25px; }}
    .error {{
      background: #7f1d1d;
      padding: 16px;
      border-radius: 8px;
    }}
    .dashboard-button {{
    display: inline-block;
    background: transparent;
    color: #9ca3af;
    text-decoration: none;
    font-weight: bold;
    padding: 0;
}}
.dashboard-button:hover {{
    background: transparent;
    color: #f9fafb;
    text-decoration: underline;
}}
.header-right {{
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 5px;
}}
.back-arrow {{
    font-weight: 900;
    -webkit-text-stroke: 1px currentColor;
}}
</style>
</head>
<body>
{content}
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        slots, error, updated = get_cached_status()

        if self.path == "/api":
            body = json.dumps({
                "slots": slots or {},
                "error": error,
                "updated": updated,
            }).encode()

            content_type = "application/json"
        else:
            body = page(slots, error, updated).encode()
            content_type = "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

threading.Thread(
    target=refresh_cache,
    daemon=True,
).start()

print(f"SBRC-e webpagina gestart op poort {WEB_PORT}")
HTTPServer(("", WEB_PORT), Handler).serve_forever()
