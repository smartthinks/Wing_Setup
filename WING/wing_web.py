import html
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WING_IP = "192.168.1.12"
WING_PORT = 2223
WEB_PORT = 8084

REPLY_PORT = 10028
POLL_SECONDS = 1
FADER_CHANNELS = list(range(1, 31))
CHARGER_CHANNELS = [33, 34, 35, 36]
CHANNEL_NUMBERS = FADER_CHANNELS + CHARGER_CHANNELS
CHANNEL_FIELDS = ("name", "mute", "fdr", "col")

LOCK = threading.Lock()

STATE = {
    "connected": False,
    "error": None,
    "updated": 0,
    "channels": {
        str(number): {
            "name": f"CH {number}",
            "mute": "0",
            "fdr": "-oo",
            "col": "14",
            "meter_l": -128.0,
            "meter_r": -128.0,
        }
        for number in CHANNEL_NUMBERS
    },
    "master": {
        "name": "MASTER",
        "mute": "0",
        "fdr": "-oo",
        "col": "14",
        "meter_l": -128.0,
        "meter_r": -128.0,
    },
}


def osc_pad(text):
    data = text.encode() + b"\x00"
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def read_osc_string(data, position):
    end = data.index(b"\x00", position)
    value = data[position:end].decode(errors="replace")
    position = end + 1
    position += (-position) % 4
    return value, position


def decode_osc(data):
    address, position = read_osc_string(data, 0)
    types, position = read_osc_string(data, position)
    values = []

    for value_type in types[1:]:
        if value_type == "s":
            value, position = read_osc_string(data, position)
        elif value_type == "i":
            value = struct.unpack(">i", data[position:position + 4])[0]
            position += 4
        elif value_type == "f":
            value = round(
                struct.unpack(">f", data[position:position + 4])[0],
                2,
            )
            position += 4
        else:
            value = None

        values.append(value)

    return address, values


def poll_wing():
    answers = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", REPLY_PORT))
    sock.settimeout(0.15)

    try:
        for number in CHANNEL_NUMBERS:
            for field in CHANNEL_FIELDS:
                address = f"/ch/{number}/{field}"
                request = f"/%{REPLY_PORT}{address}"
                sock.sendto(
                    osc_pad(request),
                    (WING_IP, WING_PORT),
                )

        for field in CHANNEL_FIELDS:
            address = f"/main/1/{field}"
            request = f"/%{REPLY_PORT}{address}"
            sock.sendto(
                osc_pad(request),
                (WING_IP, WING_PORT),
            )

        end_time = time.time() + 1.5

        while time.time() < end_time:
            try:
                data, _ = sock.recvfrom(8192)
                address, values = decode_osc(data)

                if values:
                    answers[address] = str(values[0])
                if address.endswith("/fdr") and len(values) > 1:
                    answers[address + "/pos"] = values[1]

            except socket.timeout:
                pass

    finally:
        sock.close()

    with LOCK:
        for number in CHANNEL_NUMBERS:
            channel = STATE["channels"][str(number)]

            for field in CHANNEL_FIELDS:
                address = f"/ch/{number}/{field}"

                if address in answers:
                    channel[field] = answers[address]
                if field == "fdr" and address + "/pos" in answers:
                    channel["fdr_pos"] = answers[address + "/pos"]
                if field == "fdr" and address + "/pos" in answers:
                    channel["fdr_pos"] = answers[address + "/pos"]

        for field in CHANNEL_FIELDS:
            address = f"/main/1/{field}"

            if address in answers:
                STATE["master"][field] = answers[address]
            if field == "fdr" and address + "/pos" in answers:
                STATE["master"]["fdr_pos"] = answers[address + "/pos"]
            if field == "fdr" and address + "/pos" in answers:
                STATE["master"]["fdr_pos"] = answers[address + "/pos"]

        STATE["connected"] = bool(answers)
        STATE["error"] = None if answers else "Geen antwoord van de WING"
        STATE["updated"] = time.time()

def monitor_meters():
    while True:
        udp = None

        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.bind(("", 0))
            udp.settimeout(0.7)

            meter_port = udp.getsockname()[1]

            def get_meters(selection, expected_values):
                tcp = None

                try:
                    tcp = socket.create_connection(
                        (WING_IP, 2222),
                        3,
                    )

                    packet = bytearray([
                        0xdf, 0xd3,
                        0xd3,
                        (meter_port >> 8) & 255,
                        meter_port & 255,
                        0xd4, 0, 1,
                        (meter_port >> 8) & 255,
                        meter_port & 255,
                        0xdc,
                    ])

                    packet += selection
                    packet += bytes([0xde, 0xdf, 0xd1])

                    tcp.sendall(packet)

                    end_time = time.time() + 0.15

                    while time.time() < end_time:
                        try:
                            data, _ = udp.recvfrom(8192)
                        except socket.timeout:
                            continue

                        if len(data) < 4:
                            continue

                        values = struct.unpack(
                            ">" + "h" * ((len(data) - 4) // 2),
                            data[4:],
                        )

                        if len(values) >= expected_values:
                            return values

                finally:
                    if tcp:
                        tcp.close()

                return None

            # All 30 channel meters in one request
            numbers = list(FADER_CHANNELS)
            selection = bytearray()
    
            for number in numbers:
                selection += bytes([0xa0, number - 1])
    
            values = get_meters(
                selection,
                len(numbers) * 8,
            )
    
            if values is not None:
                with LOCK:
                    for index, number in enumerate(numbers):
                        base = index * 8
    
                        left = values[base + 2] / 256
                        right = values[base + 3] / 256
    
                        STATE["channels"][str(number)]["meter_l"] = left
                        STATE["channels"][str(number)]["meter_r"] = right
    
            # Master meter separately
            values = get_meters(
                bytes([0xa3, 0]),
                8,
            )
    
            if values is not None:
                with LOCK:
                    STATE["master"]["meter_l"] = values[2] / 256
                STATE["master"]["meter_r"] = values[3] / 256
                STATE["master"]["meter_r"] = values[3] / 256
        except Exception:
            time.sleep(1)

        finally:
            if udp:
                udp.close()

        time.sleep(0.15)
                
def monitor_wing():
    while True:
        started = time.time()

        try:
            poll_wing()
        except Exception as error:
            with LOCK:
                STATE["connected"] = False
                STATE["error"] = str(error)
                STATE["updated"] = time.time()

        elapsed = time.time() - started
        time.sleep(max(0.2, POLL_SECONDS - elapsed))
def snapshot():
    with LOCK:
        return json.loads(json.dumps(STATE))


def page():
    return """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Behringer WING</title>
<style>
* { box-sizing: border-box; }

body {
    margin: 0;
    background: #111827;
    color: #f9fafb;
    font-family: Arial, sans-serif;
}

main {
    max-width: 1800px;
    margin: auto;
    padding: 24px;
}

header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 20px;
    margin-bottom: 18px;
}

h1 {
    margin: 0;
    font-size: 2rem;
}

header p {
    margin: 6px 0 0;
    color: #9ca3af;
}

.header-right {
    text-align: right;
    color: #9ca3af;
    line-height: 1.7;
}

.dashboard-button {
    color: #9ca3af;
    text-decoration: none;
    font-weight: bold;
}

.dashboard-button:hover {
    color: #f9fafb;
    text-decoration: underline;
}

.mixer {
    display: flex;
    gap: 12px;
    align-items: stretch;
}
.strips {
    display: grid;
    grid-template-columns: repeat(10, minmax(68px, 1fr));
    gap: 8px;
}
.strip,
.master,
.charger-card {
    background: #1f2937;
    border: 1px solid #374151;
    border-radius: 8px;
}

.strip {
    min-height: 180px;
    padding: 10px 7px;
    text-align: center;
}

.strip.muted {
    opacity: .5;
}

.strip-name {
    height: 34px;
    overflow: hidden;
    font-weight: bold;
    font-size: .82rem;
    word-break: break-word;
}

.fader-area {
    display: flex;
    justify-content: center;
    align-items: end;
    height: 105px;
    margin: 7px 0;
}

.fader-track {
    position: relative;
    width: 10px;
    height: 100px;
    border-radius: 8px;
    background: #0b1220;
    border: 1px solid #475569;
}

.fader-knob {
    position: absolute;
    left: 50%;
    width: 27px;
    height: 13px;
    transform: translateX(-50%);
    border-radius: 5px;
    background: #e5e7eb;
    box-shadow: 0 1px 4px #000;
}

.channel-number {
    color: #cbd5e1;
    font-weight: bold;
}

.channel-info {
    font-size: .72rem;
    color: #9ca3af;
    margin-top: 4px;
}

.master {
    grid-row: 1 / span 3;
    padding: 12px 10px;
    text-align: center;
}

.master h2 {
    margin: 0 0 12px;
    font-size: 1.25rem;
}

.master-meters {
    display: flex;
    justify-content: center;
    gap: 10px;
    height: 250px;
}

.stereo-meter {
    width: 15px;
    display: flex;
    flex-direction: column-reverse;
    gap: 3px;
}

.segment {
    flex: 1;
    background: #334155;
}

.segment.green { background: #22c55e; }
.segment.yellow { background: #facc15; }
.segment.red { background: #ef4444; }

.master .fader-area {
    height: 185px;
}

.master .fader-track {
    height: 175px;
    width: 13px;
}

.charger-title {
    margin: 24px 0 10px;
    font-size: 1.5rem;
}

.chargers {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
}

.charger-card {
    padding: 15px;
}

.charger-card h3 {
    margin: 0 0 10px;
}

.charger-name {
    font-size: 1.25rem;
    color: #dbeafe;
}

.updated {
    color: #9ca3af;
    text-align: right;
    margin-top: 12px;
}

@media (max-width: 1050px) {
    .strips {
        grid-template-columns: repeat(5, minmax(68px, 1fr));
        flex: 1;
    }

    .master {
        width: 145px;
        flex: 0 0 145px;
    }

    .chargers {
        grid-template-columns: repeat(2, 1fr);
    }
}
.audio-meter {
    position: relative;
    width: 9px;
    height: 100px;
    overflow: hidden;
    border: 1px solid #475569;
    border-radius: 4px;
    background: #0b1220;
}

.meter-fill {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    background: linear-gradient(
        to top,
        #22c55e 0%,
        #22c55e 70%,
        #facc15 70%,
        #facc15 88%,
        #ef4444 88%,
        #ef4444 100%
    );
}
.master-meter {
    width: 16px;
    height: 230px;
}
.channel-meters {
    display: flex;
    gap: 3px;
    align-items: end;
}
.channel-meters small {
    display: block;
    text-align: center;
    color: #9ca3af;
    font-size: .65rem;
    margin-bottom: 3px;
}
</style>
</head>
<body>
<main>
<header>
    <div>
        <h1>Behringer WING</h1>
        <p>Technisch overzicht</p>
    </div>

    <div class="header-right">
        <div id="status">Verbinden...</div>
        <a class="dashboard-button"
           href="#"
           onclick="window.location.href='http://' + location.hostname + ':8080'; return false;">
            &#11013; Terug naar dashboard
        </a>
        <div id="updated">Bijgewerkt: --</div>
    </div>
</header>

<section class="mixer">
    <div class="strips" id="strips"></div>
    <aside class="master" id="master"></aside>
</section>

<h2 class="charger-title">SBRC-e acculader op WING</h2>
<section class="chargers" id="chargers"></section>
</main>

<script>
function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, function(character) {
        return {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#039;"
        }[character];
    });
}

function faderPosition(position, fallback) {
    const normalized = Number(position);

    if (Number.isFinite(normalized)) {
        return Math.max(0, Math.min(100, normalized * 100));
    }

    if (fallback === "-oo" || fallback === "-144.0") {
        return 0;
    }

    const db = Number(fallback);
    return Number.isFinite(db)
        ? Math.max(0, Math.min(100, (db + 90) * 1.0))
        : 0;
}

function meterPosition(value) {
    const db = Number(value);

    if (!Number.isFinite(db) || db <= -60) {
        return 0;
    }

    return Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
}

function audioMeter(value, extraClass = "") {
    const level = meterPosition(value);

    return `<div class="audio-meter ${extraClass}">
        <div class="meter-fill" style="height: ${level}%"></div>
    </div>`;
}

function wingColor(code) {
    const colors = {
        "5": "#22c55e",
        "8": "#f59e0b",
        "9": "#ef4444",
        "14": "#3b82f6",
    };

    return colors[String(code)] || "#374151";
}

function strip(number, channel) {
    const position = faderPosition(channel.fdr_pos, channel.fdr);
    const muted = channel.mute === "1" ? " muted" : "";
    const name = channel.name || `CH ${number}`;

    return `<div class="strip${muted}" style="border-color: ${wingColor(channel.col)}">
        <div class="strip-name">${escapeHtml(name)}</div>
        <div class="fader-area">
            <div class="channel-meters">
            <div><small>L</small>${audioMeter(channel.meter_l)}</div>
            <div><small>R</small>${audioMeter(channel.meter_r)}</div>
</div>
            <div class="fader-track">
                <div class="fader-knob"
                     style="bottom: calc(${position}% - 6px)"></div>
            </div>
        </div>
        <div class="channel-number">${number}</div>
        <div class="channel-info">${channel.mute === "1" ? "MUTE" : channel.fdr + " dB"}</div>
    </div>`;
}

function master(channel) {
    const position = faderPosition(channel.fdr_pos, channel.fdr);

    return `<h2>MASTER</h2>
        <div class="master-meters">
            <div><small>L</small>${audioMeter(channel.meter_l, "master-meter")}</div>
            <div><small>R</small>${audioMeter(channel.meter_r, "master-meter")}</div>
        </div>
        <div class="fader-area">
            <div class="fader-track">
                <div class="fader-knob"
                     style="bottom: calc(${position}% - 6px)"></div>
            </div>
        </div>
        <div class="channel-info">${channel.mute === "1" ? "MASTER MUTE" : channel.fdr + " dB"}</div>`;
}
function charger(number, channel) {
    const name = channel.name || "--";

    return `<article class="charger-card">
        <h3>CH ${number}</h3>
        <div class="charger-name">${escapeHtml(name)}</div>
        <div class="channel-info">Kleurcode: ${escapeHtml(channel.col)}</div>
    </article>`;
}

async function updatePage() {
    try {
        const response = await fetch("/api");
        const data = await response.json();

        document.getElementById("status").textContent =
            data.connected ? "WING verbonden" : "WING niet verbonden";

        document.getElementById("updated").textContent =
            "Bijgewerkt: " + new Date(data.updated * 1000).toLocaleTimeString("nl-NL");

        let strips = "";

        for (let number = 1; number <= 30; number++) {
            strips += strip(number, data.channels[String(number)]);
        }

        document.getElementById("strips").innerHTML = strips;
        document.getElementById("master").innerHTML = master(data.master);

        let chargers = "";

        for (let number = 33; number <= 36; number++) {
            chargers += charger(number, data.channels[String(number)]);
        }

        document.getElementById("chargers").innerHTML = chargers;

    } catch (error) {
        document.getElementById("status").textContent =
            "WING-pagina niet beschikbaar";
    }
}

updatePage();
setInterval(updatePage, 250);
</script>
</body>
</html>
"""
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api":
            body = json.dumps(snapshot()).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = page().encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    threading.Thread(
        target=monitor_wing,
        daemon=True,
    ).start()
    
    threading.Thread(
        target=monitor_meters,
        daemon=True,
    ).start()

    print(f"WING webpagina gestart op poort {WEB_PORT}")
    ThreadingHTTPServer(("", WEB_PORT), Handler).serve_forever()
