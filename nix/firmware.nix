# The MicroPython half, exposed as a store path so `picoswitch-flash` (and any
# consumer) can copy a known-good revision onto the board instead of relying on
# a working directory being checked out somewhere.
{ runCommand }:

runCommand "picoswitch-firmware" { } ''
  mkdir -p "$out"
  cp ${../pico}/*.py "$out/"
''
