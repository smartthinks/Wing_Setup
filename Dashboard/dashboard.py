import json
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEB_PORT = 8080
WING_IP = "192.168.1.12"

wing_lock = threading.Lock()
wing_last_check = 0
wing_last_status = False


def get_api(port):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api",
            timeout=2,
        ) as response:
            return json.loads(response.read().decode()), None

    except Exception as error:
        return None, str(error)


def wing_online():
    global wing_last_check, wing_last_status

    with wing_lock:
        if time.time() - wing_last_check < 10:
            return wing_last_status

        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "1", WING_IP],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            wing_last_status = result.returncode == 0

        except Exception:
            wing_last_status = False

        wing_last_check = time.time()
        return wing_last_status


def dashboard_data():
    ad4d, ad4d_error = get_api(8081)
    psm, psm_error = get_api(8082)
    sbrc, sbrc_error = get_api(8083)

    return {
        "wing_online": wing_online(),
        "ad4d": ad4d,
        "ad4d_error": ad4d_error,
        "psm": psm,
        "psm_error": psm_error,
        "sbrc": sbrc,
        "sbrc_error": sbrc_error,
    }


HTML = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shure Overzicht</title>
<style>
* { box-sizing: border-box; }

body {
    margin: 0;
    background: #111827;
    color: #f9fafb;
    font-family: Arial, sans-serif;
}

main {
    max-width: 1200px;
    margin: auto;
    padding: 24px;
}

header {
    display: flex;
    justify-content: space-between;
    gap: 20px;
    align-items: center;
    margin-bottom: 22px;
}

h1 {
    margin: 0;
    font-size: 2rem;
}

h2 {
    margin: 0;
    font-size: 1.25rem;
}

.sub {
    color: #9ca3af;
    margin-top: 6px;
}

.statuses {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: end;
}

.status {
    background: #1f2937;
    border-radius: 999px;
    padding: 8px 11px;
    font-size: .9rem;
    text-decoration: none;
}

.online { color: #4ade80; }
.offline { color: #f87171; }

.system {
    display: block;
    text-decoration: none;
    color: inherit;
    background: #1f2937;
    border: 2px solid #374151;
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 16px;
}

.system:hover {
    border-color: #60a5fa;
}

.system-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 15px;
}

.open {
    color: #60a5fa;
    font-size: .9rem;
}


.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(245px, 1fr));
    gap: 12px;
}

.item {
    background: #111827;
    border-left: 4px solid #64748b;
    border-radius: 8px;
    padding: 13px;
}

.item.good { border-left-color: #22c55e; }
.item.warn { border-left-color: #f59e0b; }

.row {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin: 6px 0;
}

.label {
    color: #9ca3af;
    font-size: .9rem;
}

.meter {
    height: 9px;
    background: #374151;
    border-radius: 20px;
    overflow: hidden;
    margin: 7px 0 12px;
}

.fill {
    height: 100%;
    background: #22c55e;
}

.fill.blue { background: #60a5fa; }
.fill.orange { background: #f59e0b; }

.slots {
    display: grid;
    grid-template-columns: repeat(8, minmax(70px, 1fr));
    gap: 9px;
}

.slot {
    background: #111827;
    border-radius: 8px;
    padding: 11px 7px;
    text-align: center;
    border-bottom: 4px solid #64748b;
}

.slot.full { border-color: #22c55e; }
.slot.charging { border-color: #f59e0b; }
.slot.empty { opacity: .55; }

.slot-number {
    color: #9ca3af;
    font-size: .8rem;
}

.slot-value {
    font-size: 1.15rem;
    font-weight: bold;
    margin-top: 5px;
}

.error {
    color: #fca5a5;
}

@media (max-width: 700px) {
    header { align-items: start; flex-direction: column; }
    .statuses { justify-content: start; }
    .slots { grid-template-columns: repeat(4, minmax(70px, 1fr)); }
}
</style>
</head>
<body>
<main>
<header>
    <div>
        <h1>Shure Overzicht</h1>
        <div class="sub">WingManager</div>
    </div>
    <div class="statuses" id="statuses"></div>
</header>

<div id="content">Status wordt opgehaald...</div>
</main>

<script>
function pageUrl(port) {
    return "http://" + window.location.hostname + ":" + port;
}

function status(name, online, url) {
    const state = online ? "online" : "offline";
    const text = online ? "online" : "offline";

    return `<a class="status ${state}" href="${url}">
        &bull; ${name}: ${text}
    </a>`;
}

function value(value, fallback = "--") {
    return value === undefined || value === null ||
        value === "" || value === 255 || value === 65535
        ? fallback : value;
}
function updateTime(value) {
    if (!value) {
        return "--";
    }

    return new Date(value * 1000).toLocaleTimeString("nl-NL");
}
function ad4dItem(channel) {
    const on = channel.tx_on;
    const battery = channel.battery_present
        ? `${channel.battery_percent}%`
        : "Geen microfoon";

    return `
    <div class="item ${on ? "good" : ""}">
        <div class="row"><strong>${channel.name}</strong><strong>${on ? "AAN" : "UIT"}</strong></div>
        <div class="row"><span class="label">Batterij</span><strong>${battery}</strong></div>
        <div class="meter">
            <div class="fill" style="width:${channel.battery_present ? channel.battery_percent : 0}%"></div>
        </div>
        <div class="row"><span class="label">RF A / B</span><strong>${channel.rssi_a} / ${channel.rssi_b}</strong></div>
        <div class="row"><span class="label">Audio RMS</span><strong>${channel.rms}</strong></div>
    </div>`;
}

function psmItem(channel) {
    const muted = channel.rf_mute;

    return `
    <div class="item ${muted ? "warn" : "good"}">
        <div class="row"><strong>${channel.name}</strong><strong>${muted ? "RF MUTE" : "RF AAN"}</strong></div>
        <div class="row"><span class="label">Frequentie</span><strong>${channel.frequency}</strong></div>
        <div class="row"><span class="label">Audio L</span><strong>${channel.left}</strong></div>
        <div class="meter"><div class="fill blue" style="width:${Math.min(100, channel.left / 10.23)}%"></div></div>
        <div class="row"><span class="label">Audio R</span><strong>${channel.right}</strong></div>
        <div class="meter"><div class="fill blue" style="width:${Math.min(100, channel.right / 10.23)}%"></div></div>
    </div>`;
}

function sbrcSlot(number, values) {
    const charge = Number(values.BATT_CHARGE);
    const state = values.BATT_STATE || "ONBEKEND";
    const hasBattery = charge >= 0 && charge <= 100;

    let css = "empty";
    let text = "--";

    if (hasBattery) {
        css = state === "FULL" ? "full" : "charging";
        text = `${charge}%`;
    }

    return `
    <div class="slot ${css}">
        <div class="slot-number">Slot ${number}</div>
        <div class="slot-value">${text}</div>
        <div class="label">${hasBattery ? state : "Leeg"}</div>
    </div>`;
}

function render(data) {
    const ad4dOnline = data.ad4d && data.ad4d.connected;
    const psmOnline = data.psm && data.psm.connected;
    const sbrcOnline = data.sbrc && !data.sbrc.error;

    document.getElementById("statuses").innerHTML =
        status("WING", data.wing_online, pageUrl(8085)) +
        status("AD4D", ad4dOnline, pageUrl(8081)) +
        status("PSM1000", psmOnline, pageUrl(8082)) +
        status("SBRC-e", sbrcOnline, pageUrl(8083));

    let html = "";

    html += `<a class="system" href="${pageUrl(8081)}">
        <div class="system-head"><h2>Draadloze microfoons</h2><span class="open">Open detailpagina &rarr;</span></div>`;

    if (ad4dOnline) {
        html += `<div class="grid">${data.ad4d.channels.map(ad4dItem).join("")}</div>`;
    } else {
        html += `<div class="error">AD4D-status niet beschikbaar</div>`;
    }

    html += `</a>`;

    html += `<a class="system" href="${pageUrl(8082)}">
        <div class="system-head"><h2>In-ear</h2><span class="open">Open detailpagina &rarr;</span></div>`;

    if (psmOnline) {
        html += `<div class="grid">${data.psm.channels.map(psmItem).join("")}</div>`;
    } else {
        html += `<div class="error">PSM1000-status niet beschikbaar</div>`;
    }

    html += `</a>`;

    html += `<a class="system" href="${pageUrl(8083)}">
<div class="system-head">
    <div>
        <h2>SBRC-e acculader</h2>
        <div class="label">Bijgewerkt: ${updateTime(data.sbrc.updated)}</div>
    </div>

    <span class="open">Open detailpagina &rarr;</span>
</div>`;
    if (sbrcOnline) {
        const slots = data.sbrc.slots;
        html += `<div class="slots">`;

        for (let number = 1; number <= 8; number++) {
            html += sbrcSlot(number, slots[String(number)] || {});
        }

        html += `</div>`;
    } else {
        html += `<div class="error">SBRC-e-status niet beschikbaar</div>`;
    }

    html += `</a>`;

    document.getElementById("content").innerHTML = html;
}

async function refresh() {
    try {
        const response = await fetch("/api");
        render(await response.json());
    } catch (error) {
        document.getElementById("content").innerHTML =
            `<div class="error">Dashboard-status niet beschikbaar</div>`;
    }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/api":
            body = json.dumps(dashboard_data()).encode()
            content_type = "application/json"
        else:
            body = HTML.encode()
            content_type = "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


print(f"Shure Dashboard gestart op poort {WEB_PORT}")
ThreadingHTTPServer(("", WEB_PORT), Handler).serve_forever()
