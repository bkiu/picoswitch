#!/usr/bin/env python3
"""PicoSwitch host daemon - bridges Pico serial commands to Podman and system stats."""

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
COMPOSE_FILE = os.environ.get(
    "PICOSWITCH_COMPOSE_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "..", "llamacpp", "docker-compose.yml")
)


def _compose_cmd():
    """Return the compose command prefix (podman or docker)."""
    if shutil.which("podman"):
        return ["podman", "compose"]
    return ["docker", "compose"]


def _container_cmd():
    """Return the container runtime command (podman or docker)."""
    if shutil.which("podman"):
        return "podman"
    return "docker"


def find_serial_port():
    """Auto-detect Pico serial port."""
    candidates = sorted(glob.glob("/dev/ttyACM*"))
    if candidates:
        return candidates[0]
    candidates = sorted(glob.glob("/dev/ttyUSB*"))
    if candidates:
        return candidates[0]
    return None


def get_container_state(compose_file):
    """Check if the llama-server container is running."""
    try:
        result = subprocess.run(
            [_container_cmd(), "ps", "--filter", "name=llama-server",
             "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=5
        )
        status = result.stdout.strip().lower()
        if not status:
            return "stopped"
        if "up" in status:
            return "running"
        if "created" in status or "restarting" in status:
            return "starting"
        return "stopped"
    except Exception:
        return "error"


def get_vram_usage():
    """Get GPU memory usage from nvidia-smi in MiB."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
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
    """Get system RAM usage from /proc/meminfo in MiB."""
    try:
        with open("/proc/meminfo") as f:
            info = f.read()
        total = int(re.search(r"MemTotal:\s+(\d+)", info).group(1))
        available = int(re.search(r"MemAvailable:\s+(\d+)", info).group(1))
        # /proc/meminfo reports in kB
        total_mib = total // 1024
        used_mib = (total - available) // 1024
        return used_mib, total_mib
    except Exception:
        return 0, 0


def docker_up(compose_file):
    """Start the llama.cpp server."""
    print("[picoswitch] Starting llama.cpp server...")
    subprocess.Popen(
        _compose_cmd() + ["-f", compose_file, "up", "-d"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def docker_down(compose_file):
    """Stop the llama.cpp server."""
    print("[picoswitch] Stopping llama.cpp server...")
    subprocess.Popen(
        _compose_cmd() + ["-f", compose_file, "down"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def build_status(compose_file):
    """Build a STAT: response line."""
    state = get_container_state(compose_file)
    vram_used, vram_total = get_vram_usage()
    ram_used, ram_total = get_ram_usage()
    return "STAT:{}|{}|{}|{}|{}".format(state, vram_used, vram_total, ram_used, ram_total)


def main():
    parser = argparse.ArgumentParser(description="PicoSwitch host daemon")
    parser.add_argument("-p", "--port", help="Serial port (auto-detect if omitted)")
    parser.add_argument("-f", "--compose-file", default=COMPOSE_FILE,
                        help="Path to docker-compose.yml")
    args = parser.parse_args()

    compose_file = os.path.abspath(args.compose_file)
    if not os.path.isfile(compose_file):
        print(f"Error: compose file not found: {compose_file}", file=sys.stderr)
        sys.exit(1)

    port = args.port or find_serial_port()
    if not port:
        print("Error: no serial port found. Is the Pico connected?", file=sys.stderr)
        sys.exit(1)

    print(f"[picoswitch] Using serial port: {port}")
    print(f"[picoswitch] Compose file: {compose_file}")

    ser = serial.Serial(port, BAUD_RATE, timeout=0.1)
    print("[picoswitch] Listening...")

    try:
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            print(f"[picoswitch] Received: {line}")

            if line == "CMD:ON":
                docker_up(compose_file)
                status = build_status(compose_file)
                ser.write((status + "\n").encode())

            elif line == "CMD:OFF":
                docker_down(compose_file)
                status = build_status(compose_file)
                ser.write((status + "\n").encode())

            elif line == "CMD:STATUS":
                status = build_status(compose_file)
                ser.write((status + "\n").encode())
                print(f"[picoswitch] Sent: {status}")

    except KeyboardInterrupt:
        print("\n[picoswitch] Shutting down.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
