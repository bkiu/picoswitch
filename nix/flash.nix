# `picoswitch-flash` — copy the firmware in this revision onto the board.
#
# Wraps the two mpremote invocations from the README, but adds the bit that
# catches people out: the host daemon holds the same CDC-ACM port, and mpremote
# and the daemon reading the same tty steal each other's bytes. So stop the
# unit for the duration and put it back afterwards.
{
  lib,
  writeShellApplication,
  mpremote,
  callPackage,
  systemd,
}:

let
  firmware = callPackage ./firmware.nix { };
in
writeShellApplication {
  name = "picoswitch-flash";
  runtimeInputs = [ mpremote systemd ];
  text = ''
    port=""
    force=0
    while [ $# -gt 0 ]; do
      case "$1" in
        -p|--port) port="$2"; shift 2 ;;
        -f|--force) force=1; shift ;;
        -h|--help)
          echo "usage: picoswitch-flash [--port DEV] [--force]"
          echo
          echo "Copies ${firmware}/*.py onto the Pico, then resets it."
          echo "Stops picoswitch-host.service for the duration unless --force."
          exit 0 ;;
        *) echo "picoswitch-flash: unknown argument: $1" >&2; exit 2 ;;
      esac
    done

    if [ -z "$port" ]; then
      for candidate in /dev/serial/by-id/*MicroPython* /dev/ttyACM*; do
        [ -e "$candidate" ] || continue
        port="$candidate"
        break
      done
    fi
    if [ -z "$port" ]; then
      echo "picoswitch-flash: no Pico found (looked for /dev/serial/by-id/*MicroPython* and /dev/ttyACM*)" >&2
      exit 1
    fi
    if [ ! -w "$port" ]; then
      echo "picoswitch-flash: $port is not writable — are you in the 'dialout' group?" >&2
      echo "  (group membership needs a fresh login; 'sg dialout -c picoswitch-flash' works now)" >&2
      exit 1
    fi

    restart_after=0
    if [ "$force" -eq 0 ] && systemctl is-active --quiet picoswitch-host.service; then
      echo "picoswitch-flash: stopping picoswitch-host.service (it holds $port)"
      if systemctl stop picoswitch-host.service; then
        restart_after=1
      else
        echo "picoswitch-flash: could not stop the unit; run as root, or:" >&2
        echo "  sudo systemctl stop picoswitch-host && picoswitch-flash && sudo systemctl start picoswitch-host" >&2
        exit 1
      fi
    fi
    # shellcheck disable=SC2064
    trap "[ $restart_after -eq 1 ] && systemctl start picoswitch-host.service || true" EXIT

    echo "picoswitch-flash: using $port"
    # lcd_i2c.py first: main.py imports it at the top, so a board that resets
    # between the two copies comes up with a clean ImportError rather than
    # half-old firmware.
    mpremote connect "$port" cp ${firmware}/lcd_i2c.py :lcd_i2c.py
    mpremote connect "$port" cp ${firmware}/main.py :main.py
    mpremote connect "$port" reset
    echo "picoswitch-flash: done"
  '';

  meta.description = "Copy PicoSwitch firmware onto a connected Pico";
}
