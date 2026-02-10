"""HD44780 LCD driver over PCF8574 I2C backpack for MicroPython."""

from machine import I2C
import utime

# PCF8574 bit mapping to HD44780 pins
MASK_RS = 0x01
MASK_RW = 0x02
MASK_EN = 0x04
MASK_BL = 0x08  # backlight

LCD_CLR = 0x01
LCD_HOME = 0x02
LCD_ENTRY_MODE = 0x04
LCD_DISPLAY_CTRL = 0x08
LCD_SHIFT = 0x10
LCD_FUNCTION_SET = 0x20
LCD_SET_CGRAM = 0x40
LCD_SET_DDRAM = 0x80

LCD_ENTRY_INC = 0x02
LCD_DISPLAY_ON = 0x04
LCD_FUNCTION_2LINE = 0x08
LCD_FUNCTION_4BIT = 0x00

LINE_ADDRS = [0x00, 0x40]


class LCD:
    def __init__(self, i2c, addr=None, cols=16, rows=2):
        self.i2c = i2c
        self.cols = cols
        self.rows = rows
        self.backlight = MASK_BL

        if addr is not None:
            self.addr = addr
        else:
            self.addr = self._scan()

        self._init_display()

    def _scan(self):
        devices = self.i2c.scan()
        for a in (0x27, 0x3F):
            if a in devices:
                return a
        if devices:
            return devices[0]
        raise OSError("No I2C device found")

    def _write_byte(self, val):
        self.i2c.writeto(self.addr, bytes([val | self.backlight]))

    def _pulse_enable(self, val):
        self._write_byte(val | MASK_EN)
        utime.sleep_us(1)
        self._write_byte(val & ~MASK_EN)
        utime.sleep_us(50)

    def _write_nibble(self, val):
        # val already shifted to upper 4 bits of the byte
        self._write_byte(val)
        self._pulse_enable(val)

    def _write(self, val, mode=0):
        high = (val & 0xF0) | mode
        low = ((val << 4) & 0xF0) | mode
        self._write_nibble(high)
        self._write_nibble(low)

    def _cmd(self, val):
        self._write(val, 0)

    def _data(self, val):
        self._write(val, MASK_RS)

    def _init_display(self):
        utime.sleep_ms(50)
        # init sequence for 4-bit mode
        for _ in range(3):
            self._write_nibble(0x30)
            utime.sleep_ms(5)
        self._write_nibble(0x20)
        utime.sleep_ms(1)

        self._cmd(LCD_FUNCTION_SET | LCD_FUNCTION_4BIT | LCD_FUNCTION_2LINE)
        self._cmd(LCD_DISPLAY_CTRL | LCD_DISPLAY_ON)
        self._cmd(LCD_CLR)
        utime.sleep_ms(2)
        self._cmd(LCD_ENTRY_MODE | LCD_ENTRY_INC)

    def clear(self):
        self._cmd(LCD_CLR)
        utime.sleep_ms(2)

    def move_to(self, col, row):
        addr = LCD_SET_DDRAM | (LINE_ADDRS[row] + col)
        self._cmd(addr)

    def putstr(self, string):
        for c in string:
            self._data(ord(c))

    def _pad(self, text):
        """Pad or truncate text to display width."""
        text = text[:self.cols]
        return text + " " * (self.cols - len(text))

    def show(self, line1, line2=""):
        """Write both lines at once, padded/truncated to display width."""
        self.move_to(0, 0)
        self.putstr(self._pad(line1))
        self.move_to(0, 1)
        self.putstr(self._pad(line2))

    def set_backlight(self, on):
        self.backlight = MASK_BL if on else 0
        self._write_byte(0)
