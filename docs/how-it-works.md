# ⚙️ How it works

## The connection

The datalogger opens a TCP connection to the configured server and keeps it
open. SunBridge reads whatever arrives, reassembles whole frames whatever
the read boundaries (a read can hold half a frame, or two and a half), checks
each frame's CRC-16/Modbus, and descrambles the payload. The wire format is in
[protocol-notes.md](protocol-notes.md).

| Record | From | SunBridge does |
|---|---|---|
| `0x16` ping | datalogger | Echoes it back unchanged |
| `0x03` announce | datalogger | Acknowledges it, then sets the datalogger's clock to the current UTC time (register `0x1F`) |
| `0x04` live data | datalogger | Acknowledges it, decodes it, updates the sensors |
| `0x50` buffered data | datalogger | Acknowledges it, decodes it, counts it. **Never** updates a sensor |
| `0x18`, `0x19` | datalogger | Its answers to commands: nothing is sent back |

An acknowledgement echoes the record's transaction id and type with one
payload byte, exactly as the server in the captures did; `tests/test_protocol.py`
compares every acknowledgement, ping echo and clock command with the captured
bytes.

A frame with a bad CRC is dropped and counted. Bytes that cannot start a frame
(wrong protocol number or unit id) are skipped one at a time until a frame
header fits again, and counted as resyncs. Each connection has a budget: a
header declaring more than 2048 bytes (the largest real record is 585), more
than 8 bad frames, or more than 1024 skipped bytes closes the connection,
counted in diagnostics as a protocol violation. A new connection must send a whole,
CRC-checked frame within 30 seconds, and after that a connection is closed
when ten minutes pass without one. Bytes that never complete a valid frame do
not count, so a connection cannot be held open by trickling bytes. The
datalogger pings about every two minutes, so only a dead link gets there. At
most four connections are open at once; a fifth is closed on arrival and
counted in diagnostics.

Several dataloggers can connect at once, each on its own connection. A
datalogger becomes a device, and each inverter it reports a device under it,
only once one of its records has decoded completely: a ping or a damaged
record creates nothing. In Advanced mode the allow-list is checked before
that. One entry takes at most two dataloggers and two inverters; a third
datalogger's connection is closed, a third inverter is ignored, and both are
logged and counted in diagnostics.

## Live and buffered records

When the datalogger cannot reach its server it stores records, and replays
them as `0x50` after it reconnects, interleaved with live `0x04` records.
Those are **old values**: showing them would make the power jump back in time
and could move the energy totals backwards. SunBridge acknowledges them, so
the datalogger stops resending, counts them in diagnostics, and shows none.

## The energy totals

- **Energy total** is the inverter's lifetime counter and the Energy
  dashboard's source. It never decreases: a lower value is ignored and
  counted (`lifetime_decreases_ignored` in diagnostics), and the value
  restored after a restart is the floor the next reading must reach.
- **Energy today** is the inverter's own daily counter, shown as it comes.
  When it starts again from zero, Home Assistant treats the drop as a new
  cycle, as it does for any `total_increasing` sensor. (No midnight is in the
  captures, so when exactly the inverter resets it is not verified.)

## Night

At night the inverter switches off and live records stop. Live sensors
(power, voltages, currents, frequency, temperatures, status) become
unavailable once no live record has arrived for three upload intervals, and
never sooner than 15 minutes. The energy totals, the working time and the
last reading time keep their last value.

## What runs where

Everything runs on Home Assistant's event loop: socket reads are awaited, and
decoding a record is a few hundred bytes of arithmetic. The only blocking call
is the port check in the setup and options forms, which runs in Home
Assistant's executor.
