# PicoSwitch

Physical toggle switch to start/stop a llama.cpp Docker container, with an LCD showing VRAM and RAM usage.

## Hardware

- Raspberry Pi Pico WH
- 1602 LCD with I2C backpack (PCF8574)
- SPDT switch

## Wiring

```
LCD I2C Module        Pico WH
─────────────         ─────────
SDA           ──────  GP0  (Pin 1)
SCL           ──────  GP1  (Pin 2)
VCC           ──────  3V3  (Pin 36)
GND           ──────  GND  (Pin 38)

SPDT Switch           Pico WH
───────────           ─────────
Throw A (ON)  ──────  (not connected)
Common        ──────  GP2  (Pin 4)
Throw B (OFF) ──────  GND  (Pin 3)
```

The switch uses the Pico's internal pull-up resistor: when the common pin is unconnected (ON position), GP2 reads HIGH. When grounded (OFF position), it reads LOW.

## Setup

### 1. Flash MicroPython to the Pico WH

1. Hold the BOOTSEL button on the Pico and plug it into USB
2. Download the MicroPython UF2:
   ```bash
   wget -O /tmp/micropython.uf2 https://micropython.org/resources/firmware/RPI_PICO_W-20250415-v1.25.0.uf2
   ```
3. Copy it to the mounted drive:
   ```bash
   cp /tmp/micropython.uf2 /run/media/$USER/RPI-RP2/
   ```
4. The Pico will reboot with MicroPython

### 2. Copy firmware to the Pico

Using [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html):

```bash
pip install mpremote
mpremote cp pico/lcd_i2c.py :lcd_i2c.py
mpremote cp pico/main.py :main.py
```

### 3. Set up udev rule for serial permissions

```bash
echo 'SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", GROUP="dialout", MODE="0660"' | sudo tee /etc/udev/rules.d/99-pico.rules
sudo udevadm control --reload-rules
sudo usermod -aG dialout "$USER"      # log out and back in
```

`GROUP`/`0660` rather than a world-writable `0666`: `dialout` membership is
needed for the interactive `mpremote` workflow anyway, so the broader grant
buys nothing.

### 4. Install host dependencies

```bash
pip install pyserial
```

### 5. Test manually

```bash
python3 host/picoswitch_host.py
```

Flip the switch and you should see commands logged. The LCD should update with server state and memory usage.

### 6. Install as a systemd service (optional)

```bash
sudo cp host/picoswitch_host.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now picoswitch_host.service
```

Check status:
```bash
sudo systemctl status picoswitch_host.service
journalctl -u picoswitch_host.service -f
```

## NixOS

NixOS is a first-class install path and does **not** use steps 3, 4 or 6 above
-- none of them can work there. `/etc/udev/rules.d` is a read-only symlink into
the store, there is no global `site-packages` to `pip install` into, and units
are declared rather than copied into `/etc/systemd/system`. This flake declares
all three for you.

Add the input and the module:

```nix
{
  inputs.picoswitch = {
    url = "github:bkiu/picoswitch";
    inputs.nixpkgs.follows = "nixpkgs";
  };

  # ... in your nixosSystem modules list:
  modules = [ inputs.picoswitch.nixosModules.default ];
}
```

Then turn it on:

```nix
services.picoswitch = {
  enable = true;
  user = "alice";
  containerName = "clef-flash";
  composeFile = "/home/alice/projects/clef-flash/docker-compose.yml";
};
```

That is everything. The module adds the udev rule, puts `user` in `dialout`,
installs `picoswitch-host` plus the flashing tools, and declares
`picoswitch-host.service`. Group membership needs a fresh login to show up in
an existing shell, but the unit picks it up immediately.

### Options

| Option | Default | |
|---|---|---|
| `enable` | `false` | |
| `user` | *(required)* | Runs the daemon; gets `dialout`. Must already reach the container runtime (e.g. be in `docker`). |
| `containerName` | `"clef-flash"` | Matched exactly. A mismatch is silent -- "no such container" and "stopped" look identical through `ps`. |
| `composeFile` | *(required)* | String, not a path, so it tracks a working checkout instead of being frozen into the store. |
| `serialPort` | `null` | `null` auto-detects, preferring `/dev/serial/by-id/*MicroPython*`. |
| `verbose` | `false` | Log every 2 s poll, not just switch flips. |
| `installFlashTool` | `true` | Install `picoswitch-flash` and `mpremote`. |
| `package` | built from your `pkgs` | The module never pulls a second nixpkgs into your closure. |

### Flashing the board

`picoswitch-flash` copies the firmware from this flake's revision onto the
board and resets it. It stops `picoswitch-host.service` for the duration,
because the daemon holds the same CDC-ACM port and two readers on one tty steal
each other's bytes:

```bash
picoswitch-flash                 # or: nix run github:bkiu/picoswitch#flash
```

### Other flake outputs

```bash
nix run   github:bkiu/picoswitch        # run the host daemon
nix build github:bkiu/picoswitch#firmware   # the pico/*.py as a store path
nix develop                             # mpremote + python-with-pyserial
```

`mpremote` is the top-level `pkgs.mpremote`, not `python3Packages.mpremote`.

## LCD Display

```
VRAM 10G/12G   U
RAM  8G/31G
```

Upper right indicator:

| Glyph | Meaning |
|---|---|
| `U` | container up |
| `D` | container down |
| `\|/-\` spinning | starting or stopping |
| `?` | host daemon not answering (or state unknown) |

When the host daemon is not answering the Pico shows `No host` / `picoswitch
down` rather than holding the last reading, so a stale display is never
mistaken for a live one.

## Serial Protocol

- Pico sends: `CMD:ON`, `CMD:OFF`, `CMD:STATUS`
- Host replies: `STAT:<state>|<vram_used_MiB>|<vram_total_MiB>|<ram_used_MiB>|<ram_total_MiB>`
- `<state>` is one of `running`, `starting`, `stopping`, `stopped`, `error`

The Pico polls `CMD:STATUS` every 2 s and re-announces its physical switch
position whenever the host reappears, so a daemon restart cannot leave the
switch and the container disagreeing.

## Notes

- The I2C bus runs at **100 kHz**. The PCF8574 on these backpacks is the plain
  (non-A, non-T) part, rated for standard-mode I2C only; the 400 kHz this
  project originally used is out of spec and shows up as a blank or
  intermittently garbled display.
- The host daemon watches a container by name (`PICOSWITCH_CONTAINER`,
  default `clef-flash`) and drives `docker compose` against
  `PICOSWITCH_COMPOSE_FILE`. Both must match your setup or the LCD will report
  `D` regardless of what the container is doing.
