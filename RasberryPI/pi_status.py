import socket
import struct
import subprocess
import signal
import threading
import time

WING = ("192.168.1.12", 2223)
stop = threading.Event()


def osc_string(text):
    data = text.encode() + b"\0"
    return data + b"\0" * (-len(data) % 4)


def read_string(data, offset):
    end = data.index(b"\0", offset)
    return data[offset:end].decode(), (end + 4) & ~3


def send_setting(setting, value):
    message = osc_string("/ch/32/" + setting)
    if isinstance(value, int):
        message += osc_string(",i") + struct.pack(">i", value)
    else:
        message += osc_string(",s") + osc_string(value)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(message, WING)


def display(name, color):
    try:
        send_setting("clink", 0)
        send_setting("name", name)
        send_setting("col", color)
        send_setting("led", 1)
    except OSError as error:
        print("Displayfout:", error, flush=True)


def decode_mute(data):
    address, offset = read_string(data, 0)
    if address != "/ch/32/mute":
        return None

    tags, offset = read_string(data, offset)
    integers = []
    strings = []

    for tag in tags.lstrip(","):
        if tag == "s":
            value, offset = read_string(data, offset)
            strings.append(value)
        elif tag in ("i", "f"):
            value = struct.unpack_from(">" + tag, data, offset)[0]
            offset += 4
            if tag == "i":
                integers.append(value)
        else:
            return None

    value = integers[-1] if integers else int(strings[0])
    return value if value in (0, 1) else None


signal.signal(signal.SIGTERM, lambda *_: stop.set())
signal.signal(signal.SIGINT, lambda *_: stop.set())

previous = None
last_reply = time.monotonic()
next_display = 0

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(0.4)

print("Pi-monitor gestart: kanaal 32", flush=True)

try:
    while not stop.is_set():
        now = time.monotonic()

        if now - last_reply > 5:
            previous = None

        if now >= next_display:
            display("PI ONLINE", 5)
            next_display = now + 2

        sock.sendto(osc_string("/ch/32/mute"), WING)

        try:
            data, sender = sock.recvfrom(4096)
            if sender[0] != WING[0]:
                continue

            state = decode_mute(data)
            if state is None:
                continue

            last_reply = time.monotonic()

            if previous is None:
                print("Beginstand MUTE:", state, flush=True)
                if state == 1:
                    send_setting("mute", 0)
                    print("MUTE 32 wordt uitgeschakeld", flush=True)
                    stop.wait(0.25)
                    continue

            elif previous == 0 and state == 1:
                print("MUTE 32: Pi afsluiten", flush=True)
                display("PI SLUIT AF", 9)
                time.sleep(0.5)

                try:
                    result = subprocess.run(
                        ["sudo", "-n", "/usr/bin/systemctl", "poweroff"],
                        capture_output=True,
                        text=True,
                        timeout=15
                    )

                    if result.returncode == 0:
                        stop.set()
                    else:
                        print(
                            "Afsluiten mislukt:",
                            result.stderr,
                            flush=True
                        )
                        display("PI ONLINE", 5)

                except subprocess.TimeoutExpired:
                    print("Afsluitcommando gaf geen antwoord", flush=True)
                    display("PI ONLINE", 5)

            previous = state

        except socket.timeout:
            pass
        except (ValueError, IndexError, struct.error) as error:
            print("Antwoordfout:", error, flush=True)

        stop.wait(0.25)

finally:
    display("PI OFFLINE", 9)
    sock.close()
