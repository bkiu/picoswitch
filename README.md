# PicoSwitch

Physical toggle switch to start/stop a llama.cpp Podman container, with an LCD showing VRAM and RAM usage.

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
Common        ──────  GP15 (Pin 20)
Throw B (OFF) ──────  GND  (Pin 18)
```

The switch uses the Pico's internal pull-up resistor: when the common pin is unconnected (ON position), GP15 reads HIGH. When grounded (OFF position), it reads LOW.

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
echo 'SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", MODE="0666"' | sudo tee /etc/udev/rules.d/99-pico.rules
sudo udevadm control --reload-rules
```

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

## LCD Display

```
VRAM 10G/12G   U
RAM  8G/31G
```

Upper right indicator: `U` = server up, `D` = server down, spinning `|/-\` = starting/stopping.

## Serial Protocol

- Pico sends: `CMD:ON`, `CMD:OFF`, `CMD:STATUS`
- Host replies: `STAT:<state>|<vram_used_MiB>|<vram_total_MiB>|<ram_used_MiB>|<ram_total_MiB>`
