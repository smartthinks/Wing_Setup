# -*- coding: utf-8 -*-
import copy
import json
import math
import socket
import struct
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WING_IP = "192.168.1.12"
OSC_PORT = 2223
METER_PORT = 2222
WEB_PORT = 8085

REPLY_PORT = 10029
FAST_POLL_SECONDS = 0.25
SLOW_POLL_SECONDS = 5

BANK_ENDS = {1: 12, 13: 24, 25: 36}

BANKS = {
    1: "Ch 1-12",
    13: "Ch 13-24",
    25: "Ch 25-36",
}

LOCK = threading.Lock()


def new_channel():
    return {
        "name": "",
        "col": "1",
        "mute": "0",
        "fdr": "-oo",
        "icon": "0",
        "fdr_pos": 0,
        "meter_l": -128.0,
        "meter_r": -128.0,
    }


STATE = {
    "connected": False,
    "error": None,
    "updated": 0,
    "bank_start": 1,
    "channels": {
        str(number): new_channel()
        for number in range(1, 41)
    },
    "master": new_channel(),
    "wireless_tx": {
        "1": None,
        "3": None,
    },
}


def osc_pad(text):
    data = text.encode() + b"\x00"
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def read_osc_string(data, position):
    end = data.index(b"\x00", position)
    text = data[position:end].decode(errors="replace")
    position = end + 1
    position += (-position) % 4
    return text, position


def decode_osc(data):
    address, position = read_osc_string(data, 0)
    tags, position = read_osc_string(data, position)

    values = []

    for tag in tags[1:]:
        if tag == "s":
            value, position = read_osc_string(data, position)

        elif tag == "i":
            value = struct.unpack(">i", data[position:position + 4])[0]
            position += 4

        elif tag == "f":
            value = round(
                struct.unpack(">f", data[position:position + 4])[0],
                3,
            )
            position += 4

        else:
            value = None

        values.append(value)

    return address, values


def poll_osc(fields, numbers):
    answers = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", REPLY_PORT))
    sock.settimeout(0.04)

    try:
        for number in numbers:
            for field in fields:
                address = f"/ch/{number}/{field}"
                request = f"/%{REPLY_PORT}{address}"

                sock.sendto(
                    osc_pad(request),
                    (WING_IP, OSC_PORT),
                )

        for field in fields:
            address = f"/main/1/{field}"
            request = f"/%{REPLY_PORT}{address}"

            sock.sendto(
                osc_pad(request),
                (WING_IP, OSC_PORT),
            )

        end_time = time.time() + 0.45

        while time.time() < end_time:
            try:
                data, _ = sock.recvfrom(8192)
                address, values = decode_osc(data)

                if values:
                    answers[address] = values

            except socket.timeout:
                pass

    finally:
        sock.close()

    with LOCK:
        for number in numbers:
            channel = STATE["channels"][str(number)]

            for field in fields:
                address = f"/ch/{number}/{field}"

                if address in answers:
                    values = answers[address]
                    channel[field] = str(values[0])

                    if field in ("fdr", "pan") and len(values) > 1:
                        if isinstance(values[1], (float, int)):
                            channel[f"{field}_pos"] = values[1]

        for field in fields:
            address = f"/main/1/{field}"

            if address in answers:
                values = answers[address]
                STATE["master"][field] = str(values[0])

                if field in ("fdr", "pan") and len(values) > 1:
                    if isinstance(values[1], (float, int)):
                        STATE["master"][f"{field}_pos"] = values[1]

        STATE["connected"] = bool(answers)
        STATE["error"] = None if answers else "Geen antwoord van de WING"
        STATE["updated"] = time.time()


def get_meters(selection, expected_values):
    udp = None
    tcp = None

    try:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("", 0))
        udp.settimeout(0.08)

        reply_port = udp.getsockname()[1]

        tcp = socket.create_connection(
            (WING_IP, METER_PORT),
            2,
        )

        packet = bytearray([
            0xdf, 0xd3,
            0xd3,
            (reply_port >> 8) & 255,
            reply_port & 255,
            0xd4, 0, 1,
            (reply_port >> 8) & 255,
            reply_port & 255,
            0xdc,
        ])

        packet += selection
        packet += bytes([0xde, 0xdf, 0xd1])

        tcp.sendall(packet)

        end_time = time.time() + 0.30

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

    except OSError:
        return None

    finally:
        if tcp:
            tcp.close()

        if udp:
            udp.close()

    return None


def monitor_meters():
    while True:
        with LOCK:
            bank_start = STATE["bank_start"]

        numbers = list(range(bank_start, BANK_ENDS[bank_start] + 1))

        selection = bytearray()

        for number in numbers:
            # WING meterprotocol telt vanaf 0.
            selection += bytes([0xa0, number - 1])

        values = get_meters(
            selection,
            len(numbers) * 8,
        )

        if values is not None:
            with LOCK:
                for index, number in enumerate(numbers):
                    base = index * 8

                    # Positie 2 en 3 zijn de post-fader L/R meterwaarden.
                    STATE["channels"][str(number)]["meter_l"] = (
                        values[base + 0] / 256
                    )
                    STATE["channels"][str(number)]["meter_r"] = (
                        values[base + 1] / 256
                    )

        # Main 1 / Main 2 meter.
        master_values = get_meters(
            bytes([0xa3, 0]),
            8,
        )

        if master_values is not None:
            with LOCK:
                STATE["master"]["meter_l"] = master_values[2] / 256
                STATE["master"]["meter_r"] = master_values[3] / 256

        time.sleep(0.12)


def monitor_ad4d():
    while True:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8081/api",
                timeout=0.5,
            ) as response:
                data = json.load(response)

            status = {
                "1": None,
                "3": None,
            }

            for channel in data.get("channels", []):
                if channel.get("number") == "1":
                    status["1"] = channel.get("battery_bars", 255) if channel.get("tx_on") else False

                elif channel.get("number") == "2":
                    status["3"] = channel.get("battery_bars", 255) if channel.get("tx_on") else False

            with LOCK:
                STATE["wireless_tx"] = status

        except Exception:
            pass

        time.sleep(1)


def monitor_wing():

    next_slow_poll = 0

    while True:
        with LOCK:
            bank_start = STATE["bank_start"]

        numbers = range(bank_start, BANK_ENDS[bank_start] + 1)

        now = time.time()

        # Naam, kleur en pictogram alleen voor de zichtbare bank.
        if now >= next_slow_poll:
            poll_osc(("name", "col", "icon"), numbers)
            next_slow_poll = now + SLOW_POLL_SECONDS

        # Fader en mute blijven snel bijgewerkt.
        poll_osc(("mute", "fdr", "pan", "$solo"), numbers)

        time.sleep(FAST_POLL_SECONDS)


PAGE = r"""
<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WING MixPad</title>

<style>
* { box-sizing: border-box; }

body {
    margin: 0;
    background: #080a0e;
    color: #e8e9ed;
    font-family: Arial, sans-serif;
}

header {
    height: 66px;
    padding: 11px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #101218;
    border-bottom: 2px solid #30333d;
}

h1 {
    font-size: 19px;
    margin: 0;
}

.sub {
    color: #a9acb5;
    font-size: 13px;
    margin-top: 5px;
}

#status {
    padding: 9px 12px;
    border-radius: 6px;
    font-size: 13px;
    background: #252832;
}

.online { color: #69e670; }
.offline { color: #ff7777; }

.layout {
    display: grid;
    grid-template-columns: 110px minmax(0, 1fr) 90px 96px;
    min-height: calc(100dvh - 66px);
}

.banks {
    padding: 10px 8px;
    background: #111319;
    border-right: 2px solid #30333d;
}

.bank {
    position: relative;
    width: 100%;
    min-height: 76px;
    margin-bottom: 7px;
    color: #e8e9ed;
    background: #292b34;
    border: 2px solid #484b57;
    border-radius: 7px;
    font-size: 14px;
    cursor: pointer;
}

.bank::before {
    content: "";
    position: absolute;
    top: 0;
    left: 0;
    bottom: 0;
    width: 9px;
    border-radius: 4px 0 0 4px;
    background: var(--bank-colour);
}

.bank.active {
    border-color: #48d756;
    box-shadow: inset 0 0 0 1px #2e8a36;
}

.mixer {
    min-width: 0;
    padding: 13px 12px;
    background: linear-gradient(#181a22, #101217);
}

.mixer-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 32px;
    padding: 0 3px 10px;
}

.mixer-head b { font-size: 15px; }
.mixer-head span { color: #9fa2ad; font-size: 12px; }

.strips {
    display: grid;
    grid-template-columns: repeat(var(--channel-count, 12), minmax(0, 1fr));
    height: calc(100dvh - 125px);
    min-height: 0;
    gap: 4px;
}

.strip {
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    overflow: hidden;
    background: #111820;
    border: 1px solid #29313d;
    border-radius: 5px;
}

.routing {
    width: 100%;
    height: 37px;
    padding-top: 10px;
    text-align: center;
    background: var(--channel-colour);
    color: #111217;
    font-size: 11px;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
}

.trace {
    width: 100%;
    height: 38px;
    border-bottom: 1px solid #303541;
    background:
        linear-gradient(
            173deg,
            transparent 43%,
            #d1c44c 46%,
            #d1c44c 50%,
            transparent 53%
        );
    opacity: .8;
}

.channel-name {
    width: calc(100% - 6px);
    height: 43px;
    margin: 5px 3px;
    padding: 12px 2px;
    color: #15161b;
    background: var(--channel-colour);
    border-radius: 5px;
    text-align: center;
    font-size: 12px;
    font-weight: bold;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
}

.mute {
    width: calc(100% - 6px);
    height: 33px;
    margin: 0 3px 8px;
    color: #f5c7c4;
    background: #600d08;
    border: 1px solid #7a1915;
    border-radius: 50%;
    font-size: 12px;
    font-weight: bold;
}

.strip.muted .mute {
    background: #d12118;
    color: white;
}

.fader-area {
    display: grid;
    grid-template-columns: 17px 1fr 12px;
    gap: 3px;
    width: 100%;
    min-height: 220px;
    flex: 1;
    padding: 0 5px;
}


.meter {
    position: relative;
    margin: 7px 0;
    overflow: hidden;
    background: #090d0b;
    border: 1px solid #203427;
    border-radius: 3px;
}

.meter-left::before,
.meter-right::before {
    position: absolute;
    top: 2px;
    left: 0;
    width: 100%;
    z-index: 2;
    color: #cbd5e1;
    font-size: 8px;
    font-weight: bold;
    text-align: center;
}
.meter-left::before { content: "L"; }
.meter-right::before { content: "R"; }

.meter::after {
    content: "";
    position: absolute;
    right: 2px;
    bottom: 2px;
    left: 2px;
    height: var(--meter-level);
    border-radius: 2px;
    background: linear-gradient(
        to top,
        #31b34b 0 64%,
        #d4bf2f 65% 82%,
        #ef514b 83%
    );
    box-shadow: 0 0 5px #39be58;
}

.fader-track {
    position: relative;
    min-height: 220px;
    margin: 0 2px;
    border-right: 2px solid #68717d;
    border-left: 2px solid #68717d;
    background:
        repeating-linear-gradient(
            to bottom,
            transparent 0 26px,
            #59616b 27px 29px
        );
}

.fader {
    position: absolute;
    left: 50%;
    bottom: var(--fader-position);
    width: 37px;
    height: 51px;
    transform: translate(-50%, 50%);
    border: 1px solid #343a43;
    border-radius: 3px;
    background: linear-gradient(
        #7e8793,
        #d9dce1 48%,
        #8b939e 50%,
        #4e5764
    );
    box-shadow: 0 2px 5px #000c;
}

.fader::after {
    content: "";
    position: absolute;
    top: 23px;
    left: 3px;
    width: 29px;
    border-top: 2px solid white;
    opacity: .82;
}

.db,
.channel-number {
    height: 20px;
    color: #c0c3ca;
    font-size: 11px;
}

.solo {
    width: calc(100% - 6px);
    height: 37px;
    margin: 0 3px 5px;
    color: #f0e99c;
    background: #50480c;
    border: 1px solid #6c6220;
    border-radius: 5px;
    font-size: 12px;
}

.master {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 13px 8px;
    background: #15171e;
    border-left: 2px solid #30333d;
}

.master h2 {
    margin: 0 0 10px;
    font-size: 14px;
}

.master-name {
    width: 100%;
    margin-bottom: 8px;
    padding: 12px 2px;
    color: white;
    background: #3f76c7;
    border-radius: 5px;
    text-align: center;
    font-size: 12px;
}

.master .mute {
    width: 100%;
    margin-bottom: 10px;
}

.master-fader {
    display: grid;
    grid-template-columns: 1fr 26px;
    gap: 5px;
    width: 64px;
    flex: 1;
    min-height: 390px;
}

.master .fader-track {
    min-height: 390px;
}

.master .fader {
    background: linear-gradient(
        #2b5aa2,
        #4f86d5 48%,
        #315f9f 50%,
        #18396e
    );
}

.master .solo {
    width: 100%;
    margin-top: 9px;
}

@media (max-width: 1100px) {
    body { overflow-x: hidden; }
    .layout { min-width: 0; }
}
/* Compacte MixStation fader-layout */
.strips {
    height: calc(100dvh - 142px);
    min-height: 0;
}

.routing {
    height: 29px;
    padding-top: 8px;
}

.trace {
    height: 24px;
}

.channel-name {
    height: 37px;
    margin: 4px 3px;
    padding-top: 10px;
}

.mute {
    height: 29px;
    margin-bottom: 5px;
}

.fader-area {
    grid-template-columns: 17px 1fr 12px;
    gap: 2px;
    min-height: 180px;
    padding: 0 4px;
}

.scale {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 7px 0;
    color: #aeb3bc;
    font-size: 9px;
    line-height: 1;
    text-align: right;
}

.meter {
    margin: 5px 0;
}

.fader-track {
    min-height: 180px;
    margin: 0;
}

.fader {
    width: 31px;
    height: 43px;
}

.db,
.channel-number {
    height: 17px;
}

.solo {
    height: 31px;
}

/* Faderknop blijft altijd volledig binnen het spoor */
.fader {
    transform: translate(-50%, -50%);
    bottom: clamp(
        0px,
        calc(var(--fader-position) - 21px),
        calc(100% - 43px)
    );
}


/* Vast bronpictogram voor de lege blauwe kanalen */
.source-symbol {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 2px;
    width: 22px;
    height: 22px;
    border: 2px solid #15233e;
    border-radius: 50%;
}

.source-symbol i {
    width: 3px;
    height: 3px;
    background: #15233e;
    border-radius: 50%;
}


/* AD4D microfoon uit: tegel niet groen */
.strip.transmitter-off .channel-name {
    background: #353841 !important;
    color: #b9bdc6 !important;
    border: 1px solid #50545f;
}
.routing {
    display: none;
}
.trace {
    display: none;
}
.strip .fader {
    background: var(--channel-colour);
    border-color: #1d2632;
}

.strip .fader::after {
    border-top-color: rgba(255, 255, 255, .9);
}

/* Compacte horizontale fader onder MUTE */
.mini-fader {
    position: relative;
    width: calc(100% - 12px);
    height: 38px;
    margin: 0 6px 5px;
    background: #383d45;
    border-radius: 5px;
}

.mini-fader::after { content:""; position:absolute; left:50%; top:4px; bottom:4px; width:4px; background:#f4f4f2; z-index:2; }
.pan-fill { position:absolute; top:0; bottom:0; background:#3579d3; opacity:.9; z-index:1; }
.mini-fader-knob { z-index:3; }
.mini-fader-knob {
    position: absolute;
    top: 50%;
    width: 9px;
    height: 9px;
    transform: translate(-50%, -50%);
    background: #f4f4f2;
    border-radius: 50%;
    box-shadow: 0 1px 3px #000b;
}


/* Werkende stereo VU: twee echte L/R-balken */
.fader-area .stereo-pair {
    display: grid !important;
    grid-template-columns: 1fr 1fr !important;
    gap: 1px !important;
    min-height: 220px !important;
    padding: 1px !important;
    background: #000 !important;
    overflow: hidden !important;
}
.fader-area .stereo-pair::before,
.fader-area .stereo-pair::after {
    display: none !important;
}
.fader-area .stereo-pair .vu-column {
    display: flex !important;
    align-items: flex-end !important;
    min-height: 216px !important;
    overflow: hidden !important;
    background: #061009 !important;
}
.fader-area .stereo-pair .vu-fill {
    display: block !important;
    width: 100% !important;
    background: linear-gradient(to top,
        #28d54d 0 65%,
        #efc82f 66% 84%,
        #ff4e4e 85%) !important;
    box-shadow: 0 0 5px #32e85a !important;
}


/* Schone stereo VU-meters: twee onafhankelijke kolommen */
.stereo-vu,
.master-stereo-vu {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1px;
    padding: 1px;
    background: #000;
    border: 1px solid #203427;
    border-radius: 3px;
    overflow: hidden;
}
.stereo-vu {
    min-height: 180px;
}
.master-stereo-vu {
    min-height: 390px;
}
.stereo-vu .vu-column,
.master-stereo-vu .vu-column {
    display: flex;
    align-items: flex-end;
    overflow: hidden;
    background: #061009;
}
.stereo-vu .vu-fill,
.master-stereo-vu .vu-fill {
    width: 100%;
    min-height: 0;
    background: linear-gradient(to top,
        #28d54d 0 65%,
        #efc82f 66% 84%,
        #ff4e4e 85%);
    box-shadow: 0 0 5px #32e85a;
}


/* MixStation-balk helemaal rechts */
.mix-menu {
    display: flex;
    flex-direction: column;
    gap: 5px;
    padding: 8px 6px;
    background: #111319;
    border-left: 2px solid #30333d;
}
.mix-choice,
.mix-special,
.mix-fine {
    position: relative;
    min-height: 48px;
    flex: 1;
    padding: 0 6px 0 20px;
    color: #e8e9ed;
    background: #292b34;
    border: 2px solid #484b57;
    border-radius: 6px;
    font-size: 12px;
    text-align: center;
}
.mix-choice::before {
    content: "";
    position: absolute;
    top: 4px;
    bottom: 4px;
    left: 5px;
    width: 9px;
    border-radius: 4px;
    background: #ff6065;
}
.mix-choice.lime::before { background: #a5bb00; }
.mix-choice.purple::before { background: #9858bb; }
.mix-special {
    margin-top: auto;
    padding: 0 5px;
    color: #ffd2cf;
    background: #620d08;
    border-color: #7a1915;
}
.mix-fine {
    min-height: 42px;
    padding: 0 5px;
}



/* WING verbindingsstatus */
#status.online { color: #43d650 !important; }
#status.offline { color: #ff5555 !important; }

/* Touch and mouse controls */
.fader-area, .master-fader, .mini-fader { touch-action: none; user-select: none; -webkit-user-select: none; cursor: pointer; }
.solo.solo-active { background: #e8c525; color: #111; }
.mute:disabled { opacity: .4; cursor: not-allowed; }
.control-message { position: fixed; bottom: 12px; left: 12px; z-index: 100; background: #522; color: white; padding: 12px; border-radius: 6px; }

.master .mute.muted { background: #d12118; color: white; }
</style>
</head>

<body>
<header>
    <div>
        <h1>WING - MixPad overzicht</h1>
        <div class="sub">Bediening - realtime faders en signalen</div>
    </div>
        <a id="status"
href="#"
onclick="window.location.href=window.location.protocol+'//'+window.location.hostname+':8080/';return false;"
title="Terug naar dashboard"
style="cursor:pointer;text-decoration:none">Verbinden?</a>
</header>

<div class="layout">
    <nav class="banks">
        <button class="bank active" data-start="1"
                style="--bank-colour:#43d650">Ch 1-12</button>

        <button class="bank" data-start="13"
                style="--bank-colour:#397bd4">Ch 13-24</button>

        <button class="bank" data-start="25"
                style="--bank-colour:#18afe0">Ch 25-36</button>

        <button class="bank" style="--bank-colour:#df7d1d">Aux</button>
        <button class="bank" style="--bank-colour:#ff5c63">Mix</button>
        <button class="bank" style="--bank-colour:#3d78d1">Mtx/Main</button>
    </nav>
  <main class="mixer">
        <div class="mixer-head">
            <b id="bank-title">Ch 1-12</b>
            <span>Naam | kleur | mute | fader | L/R-signaal</span>
        </div>

        <section class="strips" id="strips"></section>
    </main>

    <aside class="master" id="master" data-target="main"></aside>

    <aside class="mix-menu">
        <button class="mix-choice red">INEAR 1</button>
        <button class="mix-choice red">INEAR 2</button>
        <button class="mix-choice red">FX FRONT</button>
        <button class="mix-choice red">FX INEAR</button>
        <button class="mix-choice red">Mix 5</button>
        <button class="mix-choice red">Mix 6</button>
        <button class="mix-choice red">Mix 7</button>
        <button class="mix-special">Mute enable</button>
        <button class="mix-fine">Fine mode</button>
    </aside>
</div>

<script>

let pageBusy = false;
let dragging = null;
let holdRenderUntil = 0;
let sendingControl = false;
const controlQueue = new Map();
const faderPoints = [[-90,0],[-70,6],[-60,10],[-50,15],[-40,21],[-30,30],[-20,41],[-10,53],[0,75],[10,100]];

function controlError() {
    let note = document.getElementById("control-error");
    if (!note) {
        note = document.createElement("div");
        note.id = "control-error";
        note.className = "control-message";
        document.body.appendChild(note);
    }
    note.textContent = "Bediening niet verzonden. Controleer de verbinding.";
    setTimeout(() => note.remove(), 5000);
}

function queueControl(target, field, value) {
    if (target === "32" && field === "mute") return;
    controlQueue.set(target + ":" + field, {target, field, value});
    holdRenderUntil = Date.now() + 1000;
}

async function flushControls() {
    if (sendingControl || !controlQueue.size) return;
    sendingControl = true;
    try {
        for (const [key, command] of [...controlQueue]) {
            // Remove before sending, so a newer value can replace it in flight.
            controlQueue.delete(key);
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 2500);
            try {
                const response = await fetch("/control", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(command),
                    signal: controller.signal
                });
                if (!response.ok) throw new Error("HTTP " + response.status);
                holdRenderUntil = Date.now() + 1000;
            } finally { clearTimeout(timer); }
        }
    } catch (error) {
        controlQueue.clear();
        controlError();
    } finally { sendingControl = false; }
}
setInterval(flushControls, 70);

function dbAt(percent) {
    if (percent <= 0) return -144;
    for (let i = 1; i < faderPoints.length; i++) {
        const [a, x] = faderPoints[i-1], [b, y] = faderPoints[i];
        if (percent <= y) return a + (percent-x) / (y-x) * (b-a);
    }
    return 10;
}

function moveControl(event) {
    if (!dragging || event.pointerId !== dragging.pointer) return;
    event.preventDefault();
    const {area, owner, target, field} = dragging;
    const rect = area.getBoundingClientRect();
    if (field === "pan") {
        const percent = Math.max(0, Math.min(100, (event.clientX-rect.left) / rect.width * 100));
        const knob = area.querySelector(".mini-fader-knob");
        if (knob) knob.style.left = percent + "%";
        const fill = area.querySelector(".pan-fill");
        if (fill) {
            fill.style.left = Math.min(50, percent) + "%";
            fill.style.width = Math.abs(percent-50) + "%";
        }
        queueControl(target, field, Math.round(percent*2-100));
    } else {
        const percent = Math.max(0, Math.min(100, (rect.bottom-event.clientY) / rect.height * 100));
        const db = Math.round(dbAt(percent)*10)/10;
        (target === "main" ? area : owner).style.setProperty("--fader-position", percent + "%");
        const label = owner.querySelector(".db");
        if (label) label.textContent = (db <= -90 ? "-oo" : db.toFixed(1)) + " dB";
        queueControl(target, field, db);
    }
}

document.addEventListener("pointerdown", function(event) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if (dragging) return;
    const area = event.target.closest(".fader-area, .master-fader, .mini-fader");
    if (!area) return;
    const owner = area.closest("[data-target]");
    if (!owner) return;
    dragging = {pointer: event.pointerId, area, owner, target: owner.dataset.target,
        field: area.classList.contains("mini-fader") ? "pan" : "fdr"};
    area.setPointerCapture(event.pointerId);
    moveControl(event);
});
document.addEventListener("pointermove", moveControl, {passive:false});
function endDrag(event) {
    if (!dragging || event.pointerId !== dragging.pointer) return;
    dragging = null;
    holdRenderUntil = Date.now() + 1000;
    flushControls();
}
document.addEventListener("pointerup", endDrag);
document.addEventListener("pointercancel", endDrag);
document.addEventListener("lostpointercapture", endDrag);
document.addEventListener("click", function(event) {
    const button = event.target.closest("button.mute, button.solo");
    if (!button || button.disabled) return;
    const owner = button.closest("[data-target]");
    if (!owner) return;
    const isMute = button.classList.contains("mute");
    const key = isMute ? "mute" : "solo";
    const value = owner.dataset[key] === "1" ? 0 : 1;
    if (isMute && owner.dataset.target === "32") return;
    owner.dataset[key] = String(value);
    if (isMute) {
        if (owner.dataset.target === "main") button.classList.toggle("muted", !!value);
        else owner.classList.toggle("muted", !!value);
    } else button.classList.toggle("solo-active", !!value);
    queueControl(owner.dataset.target, isMute ? "mute" : "$solo", value);
    flushControls();
});

function colour(code) {
    const colours = {
        "1": "#b9bbc0",
        "5": "#48df43",
        "8": "#f29328",
        "9": "#ff5a59",
        "14": "#1ca7e8"
    };

    return colours[String(code)] || "#5680c8";
}

function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, function(character) {
        return {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#039;"
        }[character];
    });
}

function meterPercent(left, right) {
    const db = Math.max(Number(left), Number(right));

    if (!Number.isFinite(db) || db <= -90) {
        return 0;
    }

    return Math.max(0, Math.min(100, (db + 60) * 1.65));
}

function masterMeterPercent(left, right) {
    const db = Math.max(Number(left), Number(right));

    if (!Number.isFinite(db) || db <= -90) {
        return 0;
    }

    return Math.max(0, Math.min(100, (db + 90) * 1.1));
}

function faderPercent(position, fallback) {
    const db = Number(fallback);

    if (!Number.isFinite(db) || db <= -90) {
        return 0;
    }

    const points = [
        [-90, 0],
        [-70, 6],
        [-60, 10],
        [-50, 15],
        [-40, 21],
        [-30, 30],
        [-20, 41],
        [-10, 53],
        [0, 75],
        [10, 100]
    ];

    for (let index = 1; index < points.length; index++) {
        const previous = points[index - 1];
        const next = points[index];

        if (db <= next[0]) {
            const part = (db - previous[0]) / (next[0] - previous[0]);

            return previous[1] + part * (next[1] - previous[1]);
        }
    }

    return 100;
}


function channelLabel(number, channel, wireless) {
    const rawName = String(channel.name || "").trim();
const bars = Number(wireless[String(number)]);
if ((number === 1 || number === 3) && wireless[String(number)] !== false && Number.isInteger(bars) && bars >= 0 && bars <= 5) return `BAT ${bars}/5`;

    if (rawName && rawName !== "NONE") {
        return escapeHtml(rawName);
    }

    const fixedLabels = {
        5: "FX FRONT",
        6: "FX INEAR",
        7: "Ch 7",
        8: "ICON",
        9: "Ch 9",
        10: "ICON",
        11: "BT",
        12: "Jack",
        13: "ICON",
        14: "ICON",
        15: "ICON",
        16: "ICON"
    };

    const label = fixedLabels[number] || "";

    if (label === "ICON") {
        return '<span class="source-symbol"><i></i><i></i><i></i></span>';
    }

    return escapeHtml(label);
}


function layoutColour(number, channel, transmitterOff) {
    if ((number === 1 || number === 3) && transmitterOff) {
        return "#353841";
    }

    const fixedColours = {
        1: "#48df43",
        2: "#48df43",
        3: "#48df43",
        4: "#48df43",
        5: "#ff6268",
        6: "#ff6268",
        7: "#a5bb00",
        8: "#497ed1",
        9: "#10b7eb",
        10: "#497ed1",
        11: "#497ed1",
        12: "#497ed1",
        13: "#a8bf00",
        14: "#10b7eb",
        15: "#a8bf00",
        16: "#10b7eb"
    };

    return fixedColours[number] || colour(channel.col);
}

function panPercent(value) { const pan = Number(value); return Number.isFinite(pan) ? Math.max(0, Math.min(100, pan * 100)) : 50; }

function strip(number, channel, wireless) {


    const meter = meterPercent(channel.meter_l, channel.meter_r);
    const fader = faderPercent(channel.fdr_pos, channel.fdr);
    const muted = channel.mute === "1" ? " muted" : "";
    const transmitterOff = (
        (number === 1 || number === 3) &&
        wireless[String(number)] === false
    );
    const label = channelLabel(number, channel, wireless);

    return `
        <article data-target="${number}" data-mute="${channel.mute}" data-solo="${channel['$solo'] || '0'}" class="strip${muted}${transmitterOff ? " transmitter-off" : ""}"
            style="--channel-colour:${layoutColour(number, channel, transmitterOff)};
                   --meter-level:${meter}%;
                   --fader-position:${fader}%">

            <div class="routing">Ch ${number}</div>
            <div class="trace"></div>
            <div class="channel-name">${label}</div>
            <button class="mute" ${number === 32 ? 'disabled title="Pi-afsluitknop: alleen op de WING"' : ''}>MUTE</button>

            <div class="mini-fader">
            <div class="pan-fill" style="left: ${Math.min(50, panPercent(channel.pan_pos))}%; width: ${Math.abs(panPercent(channel.pan_pos) - 50)}%"></div>
                <div class="mini-fader-knob"
                     style="left: ${panPercent(channel.pan_pos)}%"></div>
            </div>

            <div class="fader-area">
    <div class="scale">
        <span>10</span>
        <span>5</span>
        <span>0</span>
        <span>-5</span>
        <span>-10</span>
        <span>-20</span>
        <span>-30</span>
        <span>-50</span>
    </div>

    <div class="fader-track">
        <div class="fader"></div>
    </div>

    <div class="stereo-vu">
        <div class="vu-column">
            <div class="vu-fill"
                 style="height:${meterPercent(channel.meter_l, channel.meter_l)}%"></div>
        </div>
        <div class="vu-column">
            <div class="vu-fill"
                 style="height:${meterPercent(channel.meter_r, channel.meter_r)}%"></div>
        </div>
    </div>
</div>

<div class="db">${escapeHtml(channel.fdr)} dB</div>
            <div class="channel-number">Ch ${number}</div>
            <button class="solo${channel['$solo'] === '1' ? ' solo-active' : ''}">SOLO</button>
        </article>
    `;
}

function master(channel) {
    const meter = meterPercent(channel.meter_l, channel.meter_r);
    const fader = faderPercent(channel.fdr_pos, channel.fdr);
    const muted = channel.mute === "1" ? " muted" : "";
    document.getElementById("master").dataset.mute = channel.mute;
    document.getElementById("master").dataset.solo = channel["$solo"] || "0";

    return `
        <h2>MASTER</h2>
        <div class="master-name">Main 1</div>
        <button class="mute${muted}" data-value="${channel.mute}">MUTE</button>

        <div class="master-fader"
     style="--fader-position:${fader}%">

    <div class="fader-track">
        <div class="fader"></div>
    </div>

    <div class="master-stereo-vu">
        <div class="vu-column">
            <div class="vu-fill"
                 style="height:${masterMeterPercent(channel.meter_l, channel.meter_l)}%"></div>
        </div>
        <div class="vu-column">
            <div class="vu-fill"
                 style="height:${masterMeterPercent(channel.meter_r, channel.meter_r)}%"></div>
        </div>
    </div>
</div>

<div class="db">${escapeHtml(channel.fdr)} dB</div>
        <button class="solo${channel['$solo'] === '1' ? ' solo-active' : ''}">SOLO</button>
    `;
}

async function updatePage() {
    if (pageBusy) return;
    pageBusy = true;
    try {
        const response = await fetch("/api");
        const data = await response.json();

        const status = document.getElementById("status");

        if (data.connected) {
            status.innerHTML = '<span style="color:#43d650">WING verbonden</span><span style="color:white"> | Dashboard</span>';
            status.className = "online";
        } else {
            status.innerHTML = '<span style="color:#ff5555">WING niet verbonden</span><span style="color:white"> | Dashboard</span>';
            status.className = "offline";
        }

        if (dragging || sendingControl || Date.now() < holdRenderUntil) return;
        const start = data.bank_start;
        const title = {
            1: "Ch 1-12",
            13: "Ch 13-24",
            25: "Ch 25-36"
        }[start];

        document.getElementById("bank-title").textContent = title;

        let html = "";

        const end = {1: 12, 13: 24, 25: 36}[start];
        document.getElementById("strips").style.setProperty("--channel-count", end-start+1);
        for (let number = start; number <= end; number++) {
            const channel = data.channels[String(number)] || {};
            html += strip(number, channel, data.wireless_tx || {});
        }

        document.getElementById("strips").innerHTML = html;
        document.getElementById("master").innerHTML = master(data.master);

    } catch (error) {
        const status = document.getElementById("status");
        status.textContent = "? Pagina niet bereikbaar";
        status.className = "offline";
    } finally {
        pageBusy = false;
    }
}

document.querySelectorAll("[data-start]").forEach(function(button) {
    button.addEventListener("click", async function() {
        const start = button.dataset.start;

        await fetch("/select?bank=" + start);

        document.querySelectorAll("[data-start]").forEach(function(other) {
            other.classList.remove("active");
        });

        button.classList.add("active");
        updatePage();
    });
});

updatePage();
setInterval(updatePage, 200);
</script>
</body>
</html>
"""



def validate_control(payload):
    if not isinstance(payload, dict):
        raise ValueError("Ongeldige opdracht")
    target = payload.get("target")
    field = payload.get("field")
    if not isinstance(target, str) or target not in {str(n) for n in range(1, 41)} | {"main"}:
        raise ValueError("Onbekend kanaal")
    if field not in ("fdr", "pan", "mute", "$solo"):
        raise ValueError("Onbekende bediening")
    if target == "32" and field == "mute":
        raise PermissionError("MUTE 32 is uitsluitend op de WING bedienbaar")
    if target == "main" and field == "pan":
        raise ValueError("Main-pan wordt niet ondersteund")
    raw = payload.get("value")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("Ongeldige waarde")
    value = float(raw)
    limits = {"fdr": (-144, 10), "pan": (-100, 100), "mute": (0, 1), "$solo": (0, 1)}
    low, high = limits[field]
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Waarde buiten bereik")
    if field in ("mute", "$solo") and value not in (0, 1):
        raise ValueError("Schakelaar moet 0 of 1 zijn")
    address = ("/main/1/" if target == "main" else "/ch/" + target + "/") + field
    return address, str(int(value)) if field in ("mute", "$solo") else format(value, ".3f")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        if urlparse(self.path).path != "/control":
            self.send_error(404)
            return
        # Only the page served by this host may send browser commands.
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != self.headers.get("Host"):
                self.send_error(403)
                return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send_error(415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError("Ongeldige berichtlengte")
            payload = json.loads(self.rfile.read(length))
            address, value = validate_control(payload)
        except PermissionError:
            self.send_error(403, "MUTE 32 blocked")
            return
        except (ValueError, TypeError, UnicodeError):
            self.send_error(400, "Invalid control")
            return
        try:
            # A string argument uses the parameter's native displayed units.
            packet = osc_pad(address) + osc_pad(",s") + osc_pad(value)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(packet, (WING_IP, OSC_PORT))
        except OSError:
            self.send_error(502, "WING send failed")
            return
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        request = urlparse(self.path)

        if request.path == "/api":
            with LOCK:
                body = json.dumps(copy.deepcopy(STATE)).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if request.path == "/select":
            query = parse_qs(request.query)
            bank = query.get("bank", ["1"])[0]

            try:
                bank = int(bank)
            except ValueError:
                bank = 1

            if bank in BANKS:
                with LOCK:
                    STATE["bank_start"] = bank

            self.send_response(204)
            self.end_headers()
            return

        body = PAGE.encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    threading.Thread(
        target=monitor_wing,
        daemon=True,
    ).start()

    threading.Thread(
        target=monitor_meters,
        daemon=True,
    ).start()

    threading.Thread(
        target=monitor_ad4d,
        daemon=True,
    ).start()

    print(f"WING MixPad gestart op poort {WEB_PORT}")

    ThreadingHTTPServer(
        ("", WEB_PORT),
        Handler,
    ).serve_forever()


if __name__ == "__main__":
    main()
