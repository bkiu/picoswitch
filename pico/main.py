"""PicoSwitch - physical toggle for llama.cpp Docker server."""

import sys
import select
import utime
from machine import Pin, I2C
from lcd_i2c import LCD

# --- Hardware setup ---
SWITCH_PIN = 15
I2C_SDA = 0
I2C_SCL = 1

switch = Pin(SWITCH_PIN, Pin.IN, Pin.PULL_UP)
i2c = I2C(0, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=400000)
lcd = LCD(i2c)

# --- State ---
last_switch_state = None
server_state = "unknown"
vram_used = 0.0
vram_total = 0.0
ram_used = 0.0
ram_total = 0.0
last_status_time = 0
spinner_idx = 0
STATUS_INTERVAL_MS = 2000
DEBOUNCE_MS = 50
SPINNER = "|/-\\"

# --- Serial helpers ---
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)
read_buf = ""


def serial_write(msg):
    sys.stdout.write(msg + "\n")


def serial_readline():
    """Non-blocking read of a full line from USB serial."""
    global read_buf
    while poll.poll(0):
        ch = sys.stdin.read(1)
        if ch is None:
            break
        if ch == "\n":
            line = read_buf
            read_buf = ""
            return line
        read_buf += ch
    return None


def parse_status(line):
    """Parse STAT:<state>|<vram_used>|<vram_total>|<ram_used>|<ram_total>"""
    global server_state, vram_used, vram_total, ram_used, ram_total
    if not line.startswith("STAT:"):
        return
    parts = line[5:].split("|")
    if len(parts) != 5:
        return
    server_state = parts[0]
    try:
        vram_used = float(parts[1]) / 1024.0   # MiB → GiB
        vram_total = float(parts[2]) / 1024.0
        ram_used = float(parts[3]) / 1024.0
        ram_total = float(parts[4]) / 1024.0
    except ValueError:
        pass


def format_gb(val):
    """Format a float as compact GB value."""
    if val >= 10:
        return "{:.0f}G".format(val)
    return "{:.1f}G".format(val)


def state_char():
    global spinner_idx
    if server_state == "running":
        return "U"
    if server_state == "stopped":
        return "D"
    if server_state in ("starting", "stopping"):
        c = SPINNER[spinner_idx % len(SPINNER)]
        spinner_idx += 1
        return c
    return "?"


def update_lcd():
    vram = "VRAM {}/{}".format(format_gb(vram_used), format_gb(vram_total))
    line1 = vram[:15]
    line1 = line1 + " " * (15 - len(line1)) + state_char()
    line2 = "RAM  {}/{}".format(format_gb(ram_used), format_gb(ram_total))
    try:
        lcd.show(line1, line2)
    except OSError:
        pass


# --- Main loop ---
lcd.show("PicoSwitch", "Connecting...")

while True:
    now = utime.ticks_ms()

    # Read switch (HIGH=ON via pull-up when unconnected, LOW=OFF when grounded)
    raw = switch.value()
    utime.sleep_ms(DEBOUNCE_MS)
    if switch.value() == raw:  # stable reading
        current = "ON" if raw == 1 else "OFF"
        if current != last_switch_state:
            last_switch_state = current
            serial_write("CMD:" + current)

    # Poll for status periodically
    if utime.ticks_diff(now, last_status_time) >= STATUS_INTERVAL_MS:
        serial_write("CMD:STATUS")
        last_status_time = now

    # Read any incoming serial data
    line = serial_readline()
    if line is not None:
        parse_status(line)
        update_lcd()

    utime.sleep_ms(50)
