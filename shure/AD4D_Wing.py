import socket
import struct
import threading
import time

# ==========================================
# WING KLEURCODES
# ==========================================
# 5  = GROEN
# 8  = ORANJE
# 9  = ROOD
# 14 = BLAUW
# ==========================================
# WING Symbolen CODES
# ==========================================
# 2    = Jack
# 6    = FX
# 102  = Draadloze Mic
# 601  = InEar
# ==========================================
# AD4D-kanaal : WING-kanaal en normale kleur
# PSM1000 kanaal
#    "1": {"wing": 1, "color": 5, "icon": MIC_ICON},
#    "2": {"wing": 3, "color": 5, "icon": MIC_ICON},
# ==========================================
#    "1": {"wing": 3, "color": 5, "icon": INEAR_ICON},
#    "2": {"wing": 4, "color": 5, "icon": INEAR_ICON},
# ==========================================


AD4D_IP = "192.168.1.11"
AD4D_PORT = 2202

WING_IP = "192.168.1.12"
WING_PORT = 2223

PSM1000_IP = "192.168.1.13"
PSM1000_PORT = 2202

MIC_ICON = 102
INEAR_ICON = 601

IEM_CHANNELS = {
    "1": {"wing": 2, "source_group": "LCL", "source_input": 2, "color": 5, "icon": INEAR_ICON, "name": "InEar 1"},
    "2": {"wing": 4, "source_group": "LCL", "source_input": 4, "color": 5, "icon": INEAR_ICON, "name": "InEar 2"},
}

CHANNELS = {
    "1": {"wing": 1, "source_group": "LCL", "source_input": 1, "color": 5, "icon": MIC_ICON, "name": "Mic 1"},
    "2": {"wing": 3, "source_group": "LCL", "source_input": 3, "color": 5, "icon": MIC_ICON, "name": "Mic 2"},
 }

WING_CHANNEL = {
    channel: settings["wing"]
    for channel, settings in CHANNELS.items()
}

normal_color = {
    channel: settings["color"]
    for channel, settings in CHANNELS.items()
}
WING_NAME = {
    settings["wing"]: settings["name"]
    for settings in CHANNELS.values()
}

def wing_custom_link(wing_channel, state):
    address = f"/ch/{wing_channel}/clink".encode()
    address += b"\x00" * ((4 - len(address) % 4) % 4)

    message = address + b",i\x00\x00" + struct.pack(">i", state)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message, (WING_IP, WING_PORT))
    finally:
        sock.close()

last_led = {
    "1": None,
    "2": None
}

last_battery = {
    "1": None,
    "2": None
}

def wing_led(wing_channel, state):

    address = f"/ch/{wing_channel}/led".encode()
    address += b"\x00" * ((4 - len(address) % 4) % 4)

    message = address
    message += b",i\x00\x00"
    message += struct.pack(">i", state)

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    try:
        sock.sendto(
            message,
            (WING_IP, WING_PORT)
        )
    finally:
        sock.close()

def wing_color(wing_channel, color):

    address = f"/ch/{wing_channel}/col".encode()
    address += b"\x00" * ((4 - len(address) % 4) % 4)

    message = address
    message += b",i\x00\x00"
    message += struct.pack(">i", color)

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    try:
        sock.sendto(
            message,
            (WING_IP, WING_PORT)
        )
    finally:
        sock.close()

blink_threads = {}
blink_stops = {}

def wing_icon(wing_channel, icon):
    address = f"/ch/{wing_channel}/icon".encode()
    address += b"\x00" * ((4 - len(address) % 4) % 4)

    message = address
    message += b",i\x00\x00"
    message += struct.pack(">i", icon)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message, (WING_IP, WING_PORT))
    finally:
        sock.close()

def start_blinking(channel):

    if channel in blink_threads and blink_threads[channel].is_alive():
        return

    stop_event = threading.Event()
    blink_stops[channel] = stop_event

    def blink():
        state = 0

        while not stop_event.is_set():

            state = 1 - state

            wing_led(
                WING_CHANNEL[channel],
                state
            )

            stop_event.wait(0.5)

        wing_led(
            WING_CHANNEL[channel],
            0
        )

    thread = threading.Thread(
        target=blink,
        daemon=True
    )

    blink_threads[channel] = thread
    thread.start()


def stop_blinking(channel):

    if channel in blink_stops:
        blink_stops[channel].set()

    if channel in blink_threads:
        blink_threads[channel] = None
        
def wing_battery(wing_channel, bars):
    #settings = CHANNELS[channel]
    #wing_channel = settings["wing"]

    address = f"/ch/{wing_channel}/name".encode()
    address += b"\x00" * ((4 - len(address) % 4) % 4)

    if bars == 255:
        text = WING_NAME[wing_channel]
    else:
        text = f"{bars}/5"

    value = text.encode() + b"\x00"
    value += b"\x00" * ((4 - len(value) % 4) % 4)

    message = address
    message += b",s\x00\x00"
    message += value

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.sendto(
            message,
            (WING_IP, WING_PORT)
        )
    finally:
        sock.close()
        
def wing_name(wing_channel, text):
    address = f"/ch/{wing_channel}/name".encode()
    address += b"\x00" * ((4 - len(address) % 4) % 4)

    value = text.encode() + b"\x00"
    value += b"\x00" * ((4 - len(value) % 4) % 4)

    message = address + b",s\x00\x00" + value

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message, (WING_IP, WING_PORT))
    finally:
        sock.close()

def process_sample(message):

    global last_led
    global last_battery

    parts = message.strip("<>").split()

    # Batterijstatus van AD4D
    if len(parts) >= 4 and parts[0] == "REP" and parts[2] == "TX_BATT_BARS":

        channel = parts[1]

        if channel in WING_CHANNEL:

            bars = int(parts[3])

            if bars != last_battery[channel]:

                wing_battery(
                    WING_CHANNEL[channel],
                    bars
                )

                if bars != 255:
                    print(
                        f"CH{channel} | BATTERIJ: {bars}/5"
                    )
                else:
                    print(
                        f"CH{channel} | BATTERIJ: UIT"
                    )

                last_battery[channel] = bars

        return

    # Alleen SAMPLE berichten verwerken
    if len(parts) < 12:
        return

    if parts[0] != "SAMPLE":
        return

    channel = parts[1]

    if channel not in WING_CHANNEL:
        return

    tx_status = parts[7]

    rssi_a = int(parts[9])
    rssi_b = int(parts[11])

    tx_on = tx_status == "BB"

    print(
        f"CH{channel} | "
        f"TX: {'AAN' if tx_on else 'UIT'} | "
        f"RF: {rssi_a}/{rssi_b}"
    )

    # LED gedrag afhankelijk van batterijstatus

    bars = last_battery[channel]

    if not tx_on:

# Microfoon uit
        stop_blinking(channel)

        wing_color(
            WING_CHANNEL[channel],
            normal_color[channel]
        )

        wing_led(
            WING_CHANNEL[channel],
            0
        )

        print(
            f"  -> WING CH{WING_CHANNEL[channel]} LED: UIT"
        )

    elif bars == 1:

        # 1/5 = ROOD + KNIPPEREN
        # WING kleurcode 9 = Rood
        
        wing_color(
            WING_CHANNEL[channel],
            9
        )

        start_blinking(channel)

        print(
            f"  -> WING CH{WING_CHANNEL[channel]}: "
            f"ROOD + LED KNIPPERT"
        )

    elif bars == 2:

        # 2/5 = ORANJE + CONTINU
        # WING kleurcode 8 = Orange
        
        stop_blinking(channel)

        wing_color(
            WING_CHANNEL[channel],
            8
        )

        wing_led(
            WING_CHANNEL[channel],
            1
        )

        print(
            f"  -> WING CH{WING_CHANNEL[channel]}: "
            f"ORANJE + LED AAN"
        )

    else:

        # 3/5 t/m 5/5 = NORMALE KLEUR + CONTINU
        # WING kleurcode 5 = groen

        stop_blinking(channel)

        wing_color(
            WING_CHANNEL[channel],
            normal_color[channel]
        )

        wing_led(
            WING_CHANNEL[channel],
            1
        )

        print(
            f"  -> WING CH{WING_CHANNEL[channel]} LED: AAN"
        )
def wing_input_source(wing_channel, source_group, source_input):
    group_address = f"/ch/{wing_channel}/in/conn/grp".encode()
    group_address += b"\x00" * ((4 - len(group_address) % 4) % 4)

    group_value = source_group.encode() + b"\x00"
    group_value += b"\x00" * ((4 - len(group_value) % 4) % 4)

    group_message = group_address + b",s\x00\x00" + group_value

    input_address = f"/ch/{wing_channel}/in/conn/in".encode()
    input_address += b"\x00" * ((4 - len(input_address) % 4) % 4)

    input_message = input_address + b",i\x00\x00" + struct.pack(">i", source_input)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(group_message, (WING_IP, WING_PORT))
        time.sleep(0.05)
        sock.sendto(input_message, (WING_IP, WING_PORT))
    finally:
        sock.close()
        
def main():

    print()
    print("================================")
    print(" AD4D -> WING MONITOR")
    print("================================")
    print()
for channel, wing_channel in WING_CHANNEL.items():
    print(f"AD4D CH{channel} -> WING CH{wing_channel}")
    print()
    print("CTRL+C = stoppen")
    print()

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )

    sock.connect(
        (AD4D_IP, AD4D_PORT)
    )

    print("AD4D verbonden")
    print()
      
    for channel, settings in CHANNELS.items():
        wing_input_source(
            settings["wing"],
            settings["source_group"],
            settings["source_input"],
        )
        wing_custom_link(settings["wing"], 0)
        wing_icon(settings["wing"], settings["icon"])
        
    for settings in IEM_CHANNELS.values():
        wing_input_source(
            settings["wing"],
            settings["source_group"],
            settings["source_input"],
        )
        wing_custom_link(settings["wing"], 0)
        wing_color(settings["wing"], settings["color"])
        wing_led(settings["wing"], 1)
        wing_icon(settings["wing"], settings["icon"])
        wing_name(settings["wing"], settings["name"])        

    # Live meter-data voor beide ontvangers
    sock.sendall(
        b"< SET 1 METER_RATE 00200 >"
    )

    sock.sendall(
        b"< SET 2 METER_RATE 00200 >"
    )
    sock.sendall(
    b"< GET 1 TX_BATT_BARS >"
    )

    sock.sendall(
    b"< GET 2 TX_BATT_BARS >"
    )

    buffer = ""

    try:

        while True:

            data = sock.recv(8192)

            if not data:
                print("AD4D verbinding verbroken")
                break

            buffer += data.decode(
                "latin-1",
                errors="ignore"
            )

            while "<" in buffer and ">" in buffer:

                start = buffer.find("<")
                end = buffer.find(">", start)

                if end == -1:
                    break

                message = buffer[
                    start:end + 1
                ]

                buffer = buffer[
                    end + 1:
                ]


                if "SAMPLE" in message or "TX_BATT_BARS" in message:
                    process_sample(message)
  
    except KeyboardInterrupt:

        print()
        print("Gestopt")

    finally:

        print()
        print("LED's uitschakelen...")

        wing_led(wing_channel, 0)

        sock.close()


if __name__ == "__main__":
    main()
