"""PicoSwitch - physical toggle for the clef-flash llama.cpp container."""

import sys
import select
import utime
from machine import Pin, I2C
from lcd_i2c import LCD

# --- Hardware setup ---
SWITCH_PIN = 2
I2C_SDA = 0
I2C_SCL = 1

# 100 kHz, not 400 kHz. The PCF8574 on these 1602 backpacks is the plain
# (non-A, non-T) part, rated for 100 kHz standard-mode I2C only. Driving it at
# 400 kHz is out of spec and shows up as a blank or garbled display -- usually
# intermittently, because it depends on wire length and pull-up strength.
I2C_FREQ = 100000

switch = Pin(SWITCH_PIN, Pin.IN, Pin.PULL_UP)
i2c = I2C(0, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=I2C_FREQ)
lcd = LCD(i2c)

# --- Timing ---
STATUS_INTERVAL_MS = 2000   # how often to ask the host for stats
LCD_INTERVAL_MS = 250       # redraw rate; also the spinner's step rate
HOST_TIMEOUT_MS = 6000      # three missed replies before declaring the host gone
DEBOUNCE_MS = 50
SPINNER = "|/-\\"

# --- State ---
last_switch_state = None
pending_raw = switch.value()
# Not None: the elif below only fires once a reading has been stable for
# DEBOUNCE_MS, so seeding this with "stable since boot" is what makes the Pico
# announce the switch's initial position. Left as None it would stay silent
# until the switch was physically flipped, and a host started with the switch
# already ON would never be told to bring the container up.
pending_since = 0
server_state = "unknown"
vram_used = 0.0
vram_total = 0.0
ram_used = 0.0
ram_total = 0.0
last_status_time = 0
last_lcd_time = 0
spinner_idx = 0

# Far enough in the past that the first STAT counts as the host appearing, which
# triggers the switch-position re-announce below.
host_seen_at = -HOST_TIMEOUT_MS * 2

# --- Serial helpers ---
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)
read_buf = ""


def serial_write(msg):
    sys.stdout.write(msg + "\n")


def serial_readline():
    """Non-blocking read of a full line from USB serial, or None."""
    global read_buf
    while poll.poll(0):
        ch = sys.stdin.read(1)
        if ch is None:
            break
        if ch == "\n":
            line = read_buf
            read_buf = ""
            return line.strip()
        read_buf += ch
    return None


def parse_status(line):
    """Parse STAT:<state>|<vram_used>|<vram_total>|<ram_used>|<ram_total>.

    Returns True if the line was a well-formed status, so the caller can treat
    it as proof of life from the host.
    """
    global server_state, vram_used, vram_total, ram_used, ram_total
    if not line.startswith("STAT:"):
        return False
    parts = line[5:].split("|")
    if len(parts) != 5:
        return False
    try:
        vram_used = float(parts[1]) / 1024.0   # MiB -> GiB
        vram_total = float(parts[2]) / 1024.0
        ram_used = float(parts[3]) / 1024.0
        ram_total = float(parts[4]) / 1024.0
    except ValueError:
        return False
    server_state = parts[0]
    return True


def format_gb(val):
    """Format a float as a compact GB value, never wider than 4 chars.

    Rounds before choosing the format: testing `val >= 10` first renders 9.96
    as "10.0G", which is 5 chars and both uglier and harder to fit on a 16-col
    display than the "10G" it should be.
    """
    if round(val, 1) >= 10:
        return "{:.0f}G".format(val)
    return "{:.1f}G".format(val)


def state_char(host_up):
    """Single status glyph for the top-right cell."""
    global spinner_idx
    if not host_up:
        return "?"                      # host daemon not answering
    if server_state == "running":
        return "U"
    if server_state == "stopped":
        return "D"
    if server_state in ("starting", "stopping"):
        spinner_idx += 1
        return SPINNER[spinner_idx % len(SPINNER)]
    return "?"


def update_lcd(host_up):
    if host_up:
        line1 = "VRAM {}/{}".format(format_gb(vram_used), format_gb(vram_total))
        line2 = "RAM  {}/{}".format(format_gb(ram_used), format_gb(ram_total))
    else:
        # Say so rather than sitting on a stale reading forever.
        line1 = "No host"
        line2 = "picoswitch down"
    line1 = line1[:15]
    line1 = line1 + " " * (15 - len(line1)) + state_char(host_up)
    try:
        lcd.show(line1, line2)
    except OSError:
        pass                            # transient I2C NAK; next redraw retries


# --- Main loop ---
lcd.show("PicoSwitch", "Connecting...")

while True:
    now = utime.ticks_ms()

    # Debounce without blocking: track how long the raw reading has been
    # stable instead of sleeping and re-reading. The old version slept 50 ms
    # inside the loop, which starved the serial reader.
    raw = switch.value()
    if raw != pending_raw:
        pending_raw = raw
        pending_since = now
    elif pending_since is not None and utime.ticks_diff(now, pending_since) >= DEBOUNCE_MS:
        pending_since = None
        current = "ON" if raw == 1 else "OFF"
        if current != last_switch_state:
            last_switch_state = current
            serial_write("CMD:" + current)

    if utime.ticks_diff(now, last_status_time) >= STATUS_INTERVAL_MS:
        serial_write("CMD:STATUS")
        last_status_time = now

    # Drain everything buffered, not just one line per iteration.
    while True:
        line = serial_readline()
        if line is None:
            break
        if parse_status(line):
            host_was_down = utime.ticks_diff(now, host_seen_at) >= HOST_TIMEOUT_MS
            host_seen_at = now
            # The host has no idea which way the switch is physically thrown
            # when it starts, and the container may have been left in the other
            # state (clef-flash is `restart: unless-stopped`, so it returns on
            # boot regardless). Re-announce on every (re)connect so the two
            # reconcile instead of silently disagreeing until the next flip.
            if host_was_down and last_switch_state is not None:
                serial_write("CMD:" + last_switch_state)

    if utime.ticks_diff(now, last_lcd_time) >= LCD_INTERVAL_MS:
        update_lcd(utime.ticks_diff(now, host_seen_at) < HOST_TIMEOUT_MS)
        last_lcd_time = now

    utime.sleep_ms(10)
