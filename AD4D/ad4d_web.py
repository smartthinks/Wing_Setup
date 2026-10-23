import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AD4D_IP = "192.168.1.11"
AD4D_PORT = 2202
WEB_PORT = 8081

lock = threading.Lock()

state = {
    "connected": False,
    "channels": {
        "1": {
            "name": "Channel 1",
            "frequency": 0,
            "battery_bars": 255,
            "battery_percent": 255,
            "battery_mins": 65535,
            "battery_health": 255,
            "battery_temp": 255,
            "battery_type": "",
            "battery_cycles": 65535,
            "tx_model": "",
            "interference": "",
            "tx_status": "",
            "rssi_a": 0,
            "rssi_b": 0,
            "peak": 0,
            "rms": 0,
        },
        "2": {
            "name": "Channel 2",
            "frequency": 0,
            "battery_bars": 255,
            "battery_percent": 255,
            "battery_mins": 65535,
            "battery_health": 255,
            "battery_temp": 255,
            "battery_type": "",
            "battery_cycles": 65535,
            "tx_model": "",
            "interference": "",
            "tx_status": "",
            "rssi_a": 0,
            "rssi_b": 0,
            "peak": 0,
            "rms": 0,
        },
    },
}

QUERIES = [
    "< SET 1 METER_RATE 00200 >",
    "< SET 2 METER_RATE 00200 >",
    "< GET 1 CHAN_NAME >",
    "< GET 1 FREQUENCY >",
    "< GET 1 TX_BATT_BARS >",
    "< GET 1 TX_BATT_CHARGE_PERCENT >",
    "< GET 1 TX_BATT_MINS >",
    "< GET 1 TX_BATT_HEALTH_PERCENT >",
    "< GET 1 TX_BATT_TEMP_C >",
    "< GET 1 TX_BATT_TYPE >",
    "< GET 1 TX_BATT_CYCLE_COUNT >",
    "< GET 1 TX_MODEL >",
    "< GET 1 INTERFERENCE_STATUS >",
    "< GET 2 CHAN_NAME >",
    "< GET 2 FREQUENCY >",
    "< GET 2 TX_BATT_BARS >",
    "< GET 2 TX_BATT_CHARGE_PERCENT >",
    "< GET 2 TX_BATT_MINS >",
    "< GET 2 TX_BATT_HEALTH_PERCENT >",
    "< GET 2 TX_BATT_TEMP_C >",
    "< GET 2 TX_BATT_TYPE >",
    "< GET 2 TX_BATT_CYCLE_COUNT >",
    "< GET 2 TX_MODEL >",
    "< GET 2 INTERFERENCE_STATUS >",
]

def clean(value):
    return value.strip().strip("{}").strip()

def parse_report(report):
    text = report.strip("<> ").strip()
    parts = text.split()

    if len(parts) < 4 or parts[0] != "REP":
        return

    channel = parts[1]

    if channel not in state["channels"]:
        return

    parameter = parts[2]
    value = " ".join(parts[3:])


    with lock:
        data = state["channels"][channel]

        if parameter == "CHAN_NAME":
            data["name"] = clean(value)
        elif parameter == "FREQUENCY":
            data["frequency"] = int(value)
        elif parameter == "TX_BATT_BARS":
            data["battery_bars"] = int(value)
        elif parameter == "TX_BATT_CHARGE_PERCENT":
            data["battery_percent"] = int(value)
        elif parameter == "TX_BATT_MINS":
            data["battery_mins"] = int(value)
        elif parameter == "TX_BATT_HEALTH_PERCENT":
            data["battery_health"] = int(value)
        elif parameter == "TX_BATT_TEMP_C":
            data["battery_temp"] = int(value)
        elif parameter == "TX_BATT_TYPE":
            data["battery_type"] = value
        elif parameter == "TX_BATT_CYCLE_COUNT":
            data["battery_cycles"] = int(value)
        elif parameter == "TX_MODEL":
            data["tx_model"] = value
        elif parameter == "INTERFERENCE_STATUS":
            data["interference"] = value

def parse_sample(report):
    parts = report.strip("<> ").split()

    if len(parts) < 12 or parts[0] != "SAMPLE":
        return

    channel = parts[1]

    if channel not in state["channels"]:
        return

    with lock:
        data = state["channels"][channel]
        data["battery_bars"] = int(parts[3])
        data["peak"] = int(parts[5])
        data["rms"] = int(parts[6])
        data["tx_status"] = parts[7]
        data["rssi_a"] = int(parts[9])
        data["rssi_b"] = int(parts[11])

def parse_message(message):
    if message.startswith("< REP "):
        parse_report(message)
    elif message.startswith("< SAMPLE "):
        parse_sample(message)

def ad4d_monitor():
    while True:
        sock = None

        try:
            sock = socket.create_connection((AD4D_IP, AD4D_PORT), 3)
            sock.settimeout(0.5)

            with lock:
                state["connected"] = True

            buffer = ""
            next_query = 0

            while True:
                if time.time() >= next_query:
                    for query in QUERIES:
                        sock.sendall(query.encode())

                    next_query = time.time() + 5

                try:
                    received = sock.recv(8192)

                    if not received:
                        raise ConnectionError("AD4D verbinding gesloten")

                    buffer += received.decode(errors="replace")

                    while "<" in buffer and ">" in buffer:
                        start = buffer.find("<")
                        end = buffer.find(">", start)

                        if end == -1:
                            break

                        message = buffer[start:end + 1]
                        buffer = buffer[end + 1:]
                        parse_message(message)

                except socket.timeout:
                    pass

        except Exception as error:
            print("AD4D verbinding:", error)

            with lock:
                state["connected"] = False

            time.sleep(3)

        finally:
            if sock:
                sock.close()

def channel_data(number):
    data = state["channels"][number]
    battery_present = data["battery_percent"] != 255
    tx_on = data["tx_status"] == "BB"

    return {
        "number": number,
        "name": data["name"],
        "tx_on": tx_on,
        "battery_present": battery_present,
        "battery_bars": data["battery_bars"],
        "battery_percent": data["battery_percent"],
        "battery_mins": data["battery_mins"],
        "battery_health": data["battery_health"],
        "battery_temp": data["battery_temp"],
        "battery_type": data["battery_type"],
        "battery_cycles": data["battery_cycles"],
        "tx_model": data["tx_model"],
        "interference": data["interference"],
        "frequency": (
            f"{data['frequency'] / 1000:.3f} MHz"
            if data["frequency"] else "--"
        ),
        "rssi_a": data["rssi_a"],
        "rssi_b": data["rssi_b"],
        "peak": data["peak"],
        "rms": data["rms"],
    }

HTML = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shure AD4D</title>
<style>
.back-arrow {
    font-weight: 900;
    -webkit-text-stroke: 1px currentColor;
}
body {
    margin: 0;
    background: #111827;
    color: #f8fafc;
    font-family: Arial, sans-serif;
}
header {
    padding: 24px;
    background: #1f2937;
    display: flex;
    justify-content: space-between;
}
h1 { margin: 0; font-size: 28px; }
main {
    max-width: 1000px;
    margin: auto;
    padding: 24px;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 20px;
}
.card {
    background: #1f2937;
    border-radius: 12px;
    padding: 20px;
    border: 3px solid #475569;
}
.card.on { border-color: #22c55e; }
.card.off { border-color: #64748b; }
.grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
}
.label { color: #94a3b8; font-size: 13px; }
.value { font-weight: bold; font-size: 18px; }
.meter-row { margin-top: 18px; }
.meter {
    height: 20px;
    background: #0f172a;
    border-radius: 5px;
    overflow: hidden;
}
.bar {
    height: 100%;
    background: #22c55e;
    width: 0%;
}
.orange { background: #f59e0b; }
.red { background: #ef4444; }
.dashboard-button {
    display: inline-block;
    background: transparent;
    color: #f9fafb;
    text-decoration: none;
    font-weight: bold;
    padding: 0;
}
.header-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 5px;
}

.dashboard-button:hover {
    background: transparent;
    color: #9ca3af;
    text-decoration: underline;
}
</style>
</head>
<body>
<header>
    <div>
        <h1>Shure AD4D</h1>
        <div>Draadloze microfoonmonitor</div>
    </div>
    <div class="header-right">
    <strong id="status">Verbinden...</strong>

    <a class="dashboard-button"
       href="http://127.0.0.1:8080"
       onclick="this.href='http://' + location.hostname + ':8080'">
       <span class="back-arrow">&#11013;</span> Terug naar dashboard
    </a>
</div>
</header>


<main id="channels"></main>

<script>
function batteryClass(percent) {
    if (percent <= 20) return "bar red";
    if (percent <= 40) return "bar orange";
    return "bar";
}

function visible(value, empty = "--") {
    return value === 255 || value === 65535 || value === "" ? empty : value;
}

function card(channel) {
    const state = channel.tx_on ? "AAN" : "UIT";
    const box = channel.tx_on ? "on" : "off";

    let battery = "Geen actieve microfoon";
    if (channel.battery_present) {
        battery = `${channel.battery_percent}% - ${channel.battery_bars}/5 balken`;
    }

    return `
    <section class="card ${box}">
        <h2>${channel.name} - ${state}</h2>
        <div class="grid">
            <div><div class="label">Frequentie</div><div class="value">${channel.frequency}</div></div>
            <div><div class="label">Zender</div><div class="value">${visible(channel.tx_model)}</div></div>
            <div><div class="label">Batterij</div><div class="value">${battery}</div></div>
            <div><div class="label">Resterende tijd</div><div class="value">${visible(channel.battery_mins, "--")} min</div></div>
            <div><div class="label">RF antenne A / B</div><div class="value">${channel.rssi_a} / ${channel.rssi_b}</div></div>
            <div><div class="label">Interferentie</div><div class="value">${visible(channel.interference)}</div></div>
            <div><div class="label">Accu-conditie</div><div class="value">${visible(channel.battery_health, "--")}%</div></div>
            <div><div class="label">Temperatuur</div><div class="value">${visible(channel.battery_temp, "--")} C</div></div>
        </div>

        <div class="meter-row">
            <div class="label">Batterij: ${battery}</div>
            <div class="meter">
                <div class="${batteryClass(channel.battery_percent)}"
                     style="width:${Math.min(100, visible(channel.battery_percent, 0))}%"></div>
            </div>
        </div>

        <div class="meter-row">
            <div class="label">Audio Peak: ${channel.peak} | RMS: ${channel.rms}</div>
            <div class="meter">
                <div class="bar"
                     style="width:${Math.min(100, channel.rms * 4)}%"></div>
            </div>
        </div>
    </section>`;
}

async function refresh() {
    const response = await fetch("/api");
    const data = await response.json();

    document.getElementById("status").textContent =
        data.connected ? "AD4D verbonden" : "AD4D niet verbonden";

    document.getElementById("channels").innerHTML =
        data.channels.map(card).join("");
}

refresh();
setInterval(refresh, 500);
</script>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/api":
            with lock:
                body = json.dumps({
                    "connected": state["connected"],
                    "channels": [
                        channel_data("1"),
                        channel_data("2"),
                    ],
                }).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")

        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

threading.Thread(target=ad4d_monitor, daemon=True).start()

print(f"AD4D webpagina gestart op poort {WEB_PORT}")
ThreadingHTTPServer(("", WEB_PORT), Handler).serve_forever()
