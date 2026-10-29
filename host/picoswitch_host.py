#!/usr/bin/env python3
"""PicoSwitch host daemon - bridges Pico serial commands to the container runtime.

Protocol (newline-delimited, 115200 8N1):
    Pico -> host:  CMD:ON | CMD:OFF | CMD:STATUS
    host -> Pico:  STAT:<state>|<vram_used_MiB>|<vram_total_MiB>|<ram_used_MiB>|<ram_total_MiB>
                   state is one of running | starting | stopping | stopped | error
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

import serial

BAUD_RATE = 115200

# The container this switch controls. The old default was "llama-server", which
# matches nothing on this host -- the compose service and container_name are
# both "clef-flash", so `ps --filter name=llama-server` came back empty and the
# LCD reported "D" no matter what the container was doing.
CONTAINER_NAME = os.environ.get("PICOSWITCH_CONTAINER", "clef-flash")

COMPOSE_FILE = os.environ.get(
    "PICOSWITCH_COMPOSE_FILE",
    os.path.expanduser("~/projects/clef-flash/docker-compose.yml"),
)

# `docker compose down` removes the container, so it vanishes from `ps -a`
# immediately and there is no observable "stopping" window to report. Hold the
# state artificially for a moment so the LCD spinner means something. Starting
# needs no equivalent: compose creates the container right away and the
# healthcheck's start_period keeps it in "health: starting" until it is ready.
STOPPING_HOLD_S = 8.0

# If compose never converges, stop claiming a transition. clef-flash's
# healthcheck allows a 90 s start_period, so this has to clear it comfortably.
TRANSITION_TIMEOUT_S = 180.0

_transition = None          # None | "starting" | "stopping"
_transition_since = 0.0


def _runtime():
    """Return the container runtime binary, preferring docker.

    The original preferred podman, which is the wrong way round here: this host
    runs virtualisation.docker and has no podman at all, and if podman were
    ever installed alongside it the switch would start talking to the wrong
    daemon and report the container as permanently down.
    """
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            return candidate
    return "docker"


def _compose_cmd():
    return [_runtime(), "compose"]


def find_serial_port():
    """Auto-detect the Pico's serial port.

    Prefer the stable by-id symlink: /dev/ttyACM* numbering shifts when other
    CDC-ACM devices come and go, and this host already has a few.
    """
    for path in sorted(glob.glob("/dev/serial/by-id/*MicroPython*")):
        return path
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        candidates = sorted(glob.glob(pattern))
        if candidates:
            return candidates[0]
    return None


def get_container_state():
    """Map the runtime's status string onto a protocol state.

    Uses `ps -a` so a created-but-not-started or restarting container is still
    visible; plain `ps` only lists running ones, which made those branches
    unreachable. The name filter is anchored because --filter name= is a
    substring regex, not an exact match.
    """
    try:
        result = subprocess.run(
            [_runtime(), "ps", "-a",
             "--filter", "name=^{}$".format(CONTAINER_NAME),
             "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return "error"

    if result.returncode != 0:
        return "error"

    status = result.stdout.strip().lower()
    if not status:
        return "stopped"
    if status.startswith("up"):
        # "Up 10 seconds (health: starting)" -> still warming up.
        return "starting" if "health: starting" in status else "running"
    if status.startswith("created") or status.startswith("restarting"):
        return "starting"
    return "stopped"


def get_vram_usage():
    """GPU memory in MiB, summed across all NVIDIA GPUs."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        used_total = 0
        total_total = 0
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            used, total = [int(x.strip()) for x in line.split(",")]
            used_total += used
            total_total += total
        return used_total, total_total
    except Exception:
        return 0, 0


def get_ram_usage():
    """System RAM in MiB from /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            info = f.read()
        total = int(re.search(r"MemTotal:\s+(\d+)", info).group(1))
        available = int(re.search(r"MemAvailable:\s+(\d+)", info).group(1))
        return (total - available) // 1024, total // 1024   # kB -> MiB
    except Exception:
        return 0, 0


def _begin_transition(kind):
    global _transition, _transition_since
    _transition = kind
    _transition_since = time.monotonic()


def _effective_state():
    """Observed state, overlaid with any transition we are still asserting."""
    global _transition
    state = get_container_state()

    if _transition is None:
        return state

    elapsed = time.monotonic() - _transition_since
    if elapsed > TRANSITION_TIMEOUT_S:
        _transition = None
        return state

    if _transition == "starting":
        if state in ("running", "error"):
            _transition = None
            return state
        return "starting"

    if _transition == "stopping":
        if state == "stopped" and elapsed >= STOPPING_HOLD_S:
            _transition = None
            return "stopped"
        return "stopping"

    return state


def compose_up(compose_file):
    print("[picoswitch] Starting {}...".format(CONTAINER_NAME), flush=True)
    _begin_transition("starting")
    subprocess.Popen(
        _compose_cmd() + ["-f", compose_file, "up", "-d"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def compose_down(compose_file):
    print("[picoswitch] Stopping {}...".format(CONTAINER_NAME), flush=True)
    _begin_transition("stopping")
    subprocess.Popen(
        _compose_cmd() + ["-f", compose_file, "down"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def build_status():
    state = _effective_state()
    vram_used, vram_total = get_vram_usage()
    ram_used, ram_total = get_ram_usage()
    return "STAT:{}|{}|{}|{}|{}".format(state, vram_used, vram_total, ram_used, ram_total)


def serve(port, compose_file, verbose):
    """One serial session. Returns when the link drops, so main() can retry."""
    ser = serial.Serial(port, BAUD_RATE, timeout=0.5)
    print("[picoswitch] Listening on {}".format(port), flush=True)
    try:
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            if line == "CMD:ON":
                compose_up(compose_file)
            elif line == "CMD:OFF":
                compose_down(compose_file)
            elif line != "CMD:STATUS":
                continue                        # ignore REPL noise and junk

            status = build_status()
            ser.write((status + "\n").encode())
            if verbose or line != "CMD:STATUS":
                print("[picoswitch] {} -> {}".format(line, status), flush=True)
    finally:
        try:
            ser.close()
        except Exception:
            pass


def main():
    global CONTAINER_NAME

    parser = argparse.ArgumentParser(description="PicoSwitch host daemon")
    parser.add_argument("-p", "--port", help="Serial port (auto-detect if omitted)")
    parser.add_argument("-f", "--compose-file", default=COMPOSE_FILE,
                        help="Path to the compose file (default: %(default)s)")
    parser.add_argument("-c", "--container", default=CONTAINER_NAME,
                        help="Container name to watch (default: %(default)s)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Log every CMD:STATUS poll, not just switch flips")
    args = parser.parse_args()

    CONTAINER_NAME = args.container

    compose_file = os.path.abspath(os.path.expanduser(args.compose_file))
    if not os.path.isfile(compose_file):
        print("Error: compose file not found: {}".format(compose_file), file=sys.stderr)
        sys.exit(1)

    print("[picoswitch] Runtime:   {}".format(_runtime()), flush=True)
    print("[picoswitch] Container: {}".format(CONTAINER_NAME), flush=True)
    print("[picoswitch] Compose:   {}".format(compose_file), flush=True)

    # Reconnect rather than exit. Unplugging the Pico, or replugging it so the
    # ACM number changes, should not need a systemctl restart -- and exiting
    # here would just have Restart=on-failure spin the unit anyway.
    while True:
        port = args.port or find_serial_port()
        if not port:
            print("[picoswitch] No serial port; waiting...", flush=True)
            time.sleep(5)
            continue
        try:
            serve(port, compose_file, args.verbose)
        except KeyboardInterrupt:
            print("\n[picoswitch] Shutting down.", flush=True)
            return
        except (serial.SerialException, OSError) as exc:
            print("[picoswitch] Link lost ({}); reconnecting...".format(exc), flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
