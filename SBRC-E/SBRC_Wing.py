import re
import socket
import struct
import time

SBRC_IP = "192.168.1.14"
SBRC_PORT = 2202

WING_IP = "192.168.1.12"
WING_PORT = 2223

REFRESH_SECONDS = 2

WING_GROUPS = {
    33: (1, 2),
    34: (3, 4),
    35: (5, 6),
    36: (7, 8),
}

GREEN = 5
ORANGE = 8
RED = 9
BLUE = 14


def osc_address(address):
    data = address.encode()
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def wing_integer(wing_channel, setting, value):
    message = osc_address(f"/ch/{wing_channel}/{setting}")
    message += b",i\x00\x00"
    message += struct.pack(">i", value)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message, (WING_IP, WING_PORT))
    finally:
        sock.close()


def wing_name(wing_channel, text):
    message = osc_address(f"/ch/{wing_channel}/name")
    value = text.encode() + b"\x00"
    value += b"\x00" * ((4 - len(value) % 4) % 4)
    message += b",s\x00\x00" + value

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message, (WING_IP, WING_PORT))
    finally:
        sock.close()


def wing_setup(wing_channel):
    wing_integer(wing_channel, "clink", 1)


def read_charger():
    slots = {number: {} for number in range(1, 9)}
    fields = [
        "BATT_STATE",
        "BATT_CHARGE",
        "BATT_MODULE_TYPE",
    ]

    sock = socket.create_connection((SBRC_IP, SBRC_PORT), 3)
    sock.settimeout(0.3)

    try:
        for field in fields:
            sock.sendall(f"< GET 0 {field} >".encode())

        data = ""
        end_time = time.time() + 1.0

        while time.time() < end_time:
            try:
                data += sock.recv(8192).decode(errors="replace")
            except socket.timeout:
                pass

    finally:
        sock.close()

    replies = re.findall(
        r"< REP (\d+) ([A-Z_]+) ([^ >]+) >",
        data
    )

    for number, field, value in replies:
        slot = int(number)
        if slot in slots:
            slots[slot][field] = value

    return slots


def battery_text(values):
    charge = values.get("BATT_CHARGE", "255")

    if charge == "255":
        return "--"

    return charge


def group_text(first_number, second_number, first, second):
    first_text = battery_text(first)
    second_text = battery_text(second)

    if first_text == second_text:
        return f"{first_number}/{second_number}:{first_text}"

    return f"{first_number}:{first_text} {second_number}:{second_text}"

def group_status(first, second):
    batteries = [first, second]
    present = [
        battery for battery in batteries
        if battery.get("BATT_CHARGE", "255") != "255"
    ]

    if not present:
        return BLUE, 1

    warning_states = ["ERROR", "HOT", "COLD", "RECOVERY"]

    if any(
        battery.get("BATT_STATE", "") in warning_states
        for battery in present
    ):
        return RED, 1

    if any(
        battery.get("BATT_STATE", "") != "FULL"
        for battery in present
    ):
        return ORANGE, 1

    return GREEN, 1


def update_wing(slots):
    for wing_channel, slot_numbers in WING_GROUPS.items():
        first_number, second_number = slot_numbers
        first = slots[first_number]
        second = slots[second_number]
        
        module_number = (first_number + 1) // 2
        module_type = slots[module_number].get(
            "BATT_MODULE_TYPE"
        )
        
        if module_type is None:
            print(
                f"WING {wing_channel}: wacht op laadmodule-info"
            )
            continue

        if module_type == "000":
            wing_name(wing_channel, "")
            wing_integer(wing_channel, "led", 0)
            print(f"WING {wing_channel}: geen laadmodule")
            continue
            
        text = group_text(
            first_number,
            second_number,
            first,
            second,
        )

        color, led = group_status(first, second)

        wing_name(wing_channel, text)
        wing_integer(wing_channel, "col", color)
        wing_integer(wing_channel, "led", led)

        print(
            f"WING {wing_channel}: {text} "
            f"color={color} led={led}"
        )


print("SBRC to WING monitor started")

for channel in WING_GROUPS:
    wing_setup(channel)

while True:
    try:
        update_wing(read_charger())
    except OSError as error:
        print(f"SBRC connection error: {error}")

    time.sleep(REFRESH_SECONDS)
