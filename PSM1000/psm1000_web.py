import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PSM_IP = "192.168.1.13"
PSM_PORT = 2202
WEB_PORT = 8082

lock = threading.Lock()

state = {
    "connected": False,
    "device_name": "PSM1000",
    "updated": 0,
    "channels": {
        "1": {
            "group_chan": "--,--",
            "frequency": 0,
            "rf_tx_lvl": "",
            "rf_mute": "",
            "audio_tx_mode": "",
            "audio_in_line_lvl": "",
            "meter_rate": "",
            "left": 0,
            "right": 0,
            "chan_name": "",
        },
        "2": {
            "group_chan": "--,--",
            "frequency": 0,
            "rf_tx_lvl": "",
            "rf_mute": "",
            "audio_tx_mode": "",
            "audio_in_line_lvl": "",
            "meter_rate": "",
            "left": 0,
            "right": 0,
            "chan_name": "",
        },
    },
}

QUERIES = [
    "< GET DEVICE_NAME >",
    "< GET 1 GROUP_CHAN >",
    "< GET 1 FREQUENCY >",
    "< GET 1 RF_TX_LVL >",
    "< GET 1 RF_MUTE >",
    "< GET 1 AUDIO_TX_MODE >",
    "< GET 1 AUDIO_IN_LINE_LVL >",
    "< GET 1 METER_RATE >",
    "< GET 1 CHAN_NAME >",
    "< GET 2 GROUP_CHAN >",
    "< GET 2 FREQUENCY >",
    "< GET 2 RF_TX_LVL >",
    "< GET 2 RF_MUTE >",
    "< GET 2 AUDIO_TX_MODE >",
    "< GET 2 AUDIO_IN_LINE_LVL >",
    "< GET 2 METER_RATE >",
    "< GET 2 CHAN_NAME >",
]

def parse_report(report):
    text = report.strip("<> ").strip()
    parts = text.split()

    if len(parts) < 3 or parts[0] != "REPORT":
        return

    with lock:
        if parts[1] == "DEVICE_NAME":
            state["device_name"] = " ".join(parts[2:])
            state["updated"] = time.time()
            return

        if len(parts) < 4 or parts[1] not in state["channels"]:
            return

        channel = parts[1]
        parameter = parts[2]
        value = " ".join(parts[3:])

        if parameter == "AUDIO_IN_LVL_L":
            state["channels"][channel]["left"] = int(value)
        elif parameter == "AUDIO_IN_LVL_R":
            state["channels"][channel]["right"] = int(value)
        elif parameter == "GROUP_CHAN":
            state["channels"][channel]["group_chan"] = value
        elif parameter == "FREQUENCY":
            state["channels"][channel]["frequency"] = int(value)
        elif parameter == "RF_TX_LVL":
            state["channels"][channel]["rf_tx_lvl"] = value
        elif parameter == "RF_MUTE":
            state["channels"][channel]["rf_mute"] = value
        elif parameter == "AUDIO_TX_MODE":
            state["channels"][channel]["audio_tx_mode"] = value
        elif parameter == "AUDIO_IN_LINE_LVL":
            state["channels"][channel]["audio_in_line_lvl"] = value
        elif parameter == "METER_RATE":
            state["channels"][channel]["meter_rate"] = value
        elif parameter == "CHAN_NAME":
            state["channels"][channel]["chan_name"] = value

        state["updated"] = time.time()

def psm_monitor():
    while True:
        sock = None

        try:
            sock = socket.create_connection((PSM_IP, PSM_PORT), 3)
            sock.settimeout(0.5)

            with lock:
                state["connected"] = True

            next_query = 0
            buffer = ""

            while True:
                if time.time() >= next_query:
                    for query in QUERIES:
                        sock.sendall((query + "\r\n").encode())

                    next_query = time.time() + 10

                try:
                    data = sock.recv(8192)

                    if not data:
                        raise ConnectionError("PSM connection closed")

                    buffer += data.decode(errors="replace")

                    while "<" in buffer and ">" in buffer:
                        start = buffer.find("<")
                        end = buffer.find(">", start)

                        if end == -1:
                            break

                        report = buffer[start:end + 1]
                        buffer = buffer[end + 1:]
                        parse_report(report)

                except socket.timeout:
                    pass

        except Exception as error:
            print("PSM connection:", error)

            with lock:
                state["connected"] = False

            time.sleep(3)

        finally:
            if sock:
                sock.close()

def mode_name(value):
    return {
        "1": "Mono",
        "2": "PTP",
        "3": "Stereo",
    }.get(str(value), "--")

def input_name(value):
    return {
        "0": "Aux",
        "1": "Line",
    }.get(str(value), "--")

def channel_data(number):
    channel = state["channels"][number]
    frequency = channel["frequency"]

    return {
        "number": number,
        "name": channel["chan_name"] or "IEM " + number,
        "group_chan": channel["group_chan"],
        "frequency": (
            f"{frequency / 1000:.3f} MHz"
            if frequency else "--"
        ),
        "rf_power": (
            channel["rf_tx_lvl"] + " mW"
            if channel["rf_tx_lvl"] else "--"
        ),
        "rf_mute": channel["rf_mute"] == "1",
        "mode": mode_name(channel["audio_tx_mode"]),
        "input": input_name(channel["audio_in_line_lvl"]),
        "meter_rate": channel["meter_rate"] + " ms",
        "left": channel["left"],
        "right": channel["right"],
    }

HTML = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shure PSM1000</title>
<style>
.back-arrow {
    font-weight: 900;
    -webkit-text-stroke: 1px currentColor;
}
.dashboard-button {
    display: inline-block;
    background: transparent;
    color: #9ca3af;
    text-decoration: none;
    font-weight: bold;
    padding: 0;
}

.dashboard-button:hover {
    background: transparent;
    color: #f9fafb;
    text-decoration: underline;
}

.header-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 5px;
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
#status { font-weight: bold; }
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
}
.card h2 { margin-top: 0; }
.muted { border: 3px solid #dc2626; }
.grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
}
.label { color: #94a3b8; font-size: 13px; }
.value { font-size: 18px; font-weight: bold; }
.meter-row { margin-top: 18px; }
.meter {
    height: 22px;
    background: #0f172a;
    border-radius: 5px;
    overflow: hidden;
}
.bar {
    height: 100%;
    width: 0%;
    background: #22c55e;
    transition: width .15s;
}
.bar.warn { background: #f59e0b; }
.bar.hot { background: #ef4444; }
</style>
</head>
<body>
<header>
    <div>
        <h1 id="device">Shure PSM1000</h1>
        <div>Live zender- en audiomonitor</div>
    </div>
    <div class="header-right">
    <div id="status">Verbinden...</div>

    <a class="dashboard-button"
       href="#"
       onclick="window.location.href='http://' + location.hostname + ':8080'; return false;">
       <span class="back-arrow">&#11013;</span> Terug naar dashboard
    </a>
</div>
</header>

<main id="channels"></main>

<script>
function meterClass(value) {
    if (value > 850) return "bar hot";
    if (value > 650) return "bar warn";
    return "bar";
}

function card(channel) {
    const mute = channel.rf_mute ? " muted" : "";
    const rf = channel.rf_mute ? "RF MUTE / UIT" : "RF AAN";

    return `
    <section class="card${mute}">
        <h2>${channel.name} - ${rf}</h2>
        <div class="grid">
            <div><div class="label">Groep / kanaal</div><div class="value">${channel.group_chan}</div></div>
            <div><div class="label">Frequentie</div><div class="value">${channel.frequency}</div></div>
            <div><div class="label">RF vermogen</div><div class="value">${channel.rf_power}</div></div>
            <div><div class="label">Audio</div><div class="value">${channel.mode}</div></div>
            <div><div class="label">Ingang</div><div class="value">${channel.input}</div></div>
            <div><div class="label">Meter tempo</div><div class="value">${channel.meter_rate}</div></div>
        </div>

        <div class="meter-row">
            <div class="label">Audio links: ${channel.left}</div>
            <div class="meter"><div class="${meterClass(channel.left)}" style="width:${Math.min(100, channel.left / 10.23)}%"></div></div>
        </div>

        <div class="meter-row">
            <div class="label">Audio rechts: ${channel.right}</div>
            <div class="meter"><div class="${meterClass(channel.right)}" style="width:${Math.min(100, channel.right / 10.23)}%"></div></div>
        </div>
    </section>`;
}

async function refresh() {
    const response = await fetch("/api");
    const data = await response.json();

    document.getElementById("device").textContent = "Shure " + data.device_name;
    document.getElementById("status").textContent =
        data.connected ? "PSM verbonden" : "PSM niet verbonden";

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
                data = {
                    "connected": state["connected"],
                    "device_name": state["device_name"],
                    "channels": [
                        channel_data("1"),
                        channel_data("2"),
                    ],
                }

            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

threading.Thread(target=psm_monitor, daemon=True).start()

print(f"PSM1000 webpagina gestart op poort {WEB_PORT}")
ThreadingHTTPServer(("", WEB_PORT), Handler).serve_forever()
