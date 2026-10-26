# ⚙️ How it works

## Step by step

For each connection, in order:

1. **Frames.** Bytes are reassembled into whole frames however the reads
   split them, each frame's CRC is checked (a bad one is dropped and
   counted), and the payload is descrambled.
2. **Answers.** An announce (`0x03`) is acknowledged and followed by the
   clock command; live (`0x04`) and buffered (`0x50`) records are
   acknowledged; pings (`0x16`) are echoed. Every reply equals, byte for
   byte, what the captured server sent.
3. **Devices.** The first record that decodes completely creates the
   **Solar Datalogger** device and the **Solar Inverter** under it. A
   serial not on the allow-list is closed before anything is answered, in
   Simple and Advanced alike.
4. **Sensors.** Only live records update sensors. Buffered records are
   old values the datalogger stored while it was cut off: they are counted
   in diagnostics and never shown, so the power never jumps back in time.
5. **Energy.** A single lifetime reading the inverter could not have made
   is ignored, even across restarts; three agreeing live readings accept a
   reset. Buffered records never reach it.
6. **Night.** With no live record for three upload intervals (at least 15
   minutes), or two minutes after the datalogger's last connection closes,
   live sensors go unavailable; totals keep their value.

The sections below explain each step.

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
datalogger pings about every two minutes, so only a dead link gets there.
A new connection must also identify itself within 120 seconds, with an
announce or a record that decodes; pings do not extend that. A connection
belongs to the first datalogger serial it identifies: a record with a
different serial on the same connection closes it unanswered. Both are
counted in diagnostics (`connections_closed_unidentified`,
`connections_closed_for_serial_mismatch`). At
most four connections are open at once; a fifth is closed on arrival and
counted in diagnostics.

Several dataloggers can connect at once, each on its own connection. A
datalogger becomes a device, and each inverter it reports a device under it,
only once one of its records has decoded completely: a ping or a damaged
record creates nothing. The allow-list is checked before that, in either
mode. One entry takes at most two dataloggers and two inverters; a third
datalogger's connection is closed, a third inverter is ignored, and both are
logged and counted in diagnostics.

The first datalogger and inverter are named **Solar Datalogger** and
**Solar Inverter**. A second one of either kind is named with its serial,
for example **Solar Inverter INVEXAMPLE**, so two devices never share a
name. No model or firmware is shown on either device: no captured record
proves which bytes hold them, and a guessed value would be shown as fact.

## Live and buffered records

When the datalogger cannot reach its server it stores records, and replays
them as `0x50` after it reconnects, interleaved with live `0x04` records.
Those are **old values**: showing them would make the power jump back in time
and could move the energy totals backwards. SunBridge acknowledges them, so
the datalogger stops resending, counts them in diagnostics, and shows none.

## The energy totals

- **Energy total** is the inverter's lifetime counter and the Energy
  dashboard's source. SunBridge ignores a single reading the inverter could
  not have made: one lower than the last, or more than 50 kW could have
  added in the time since the last (plus 1 kWh for rounding). Each is
  counted in diagnostics (`lifetime_decreases_ignored`,
  `lifetime_jumps_ignored`). Three live readings in a row that agree with
  each other are real, a counter reset or a replaced inverter: SunBridge
  accepts them (`lifetime_resets_accepted`, logged at info), and the
  Energy dashboard starts a new cycle from the new value, as it does for any
  `total_increasing` sensor. Buffered readings never count towards that.
  After a restart, the restored total and the last reading time are what
  the first new reading is checked against.
- **Energy today** is the inverter's own daily counter, shown as it comes.
  At Home Assistant's local midnight (in its time zone, daylight saving
  included) it shows 0, and stays 0 until the first live reading of the new
  day, because all night the last record still carries yesterday's total. A
  restart after midnight does not bring yesterday's value back. (No midnight
  is in the captures, so the moment the inverter itself resets its counter is
  not verified.) Use **Energy total**, not this one, for the Energy
  dashboard.

The Energy dashboard reads **Energy total** only, and only after you add it
([setup](setup.md#4-energy-dashboard)). The protection above covers what
SunBridge can see: replays, drops and impossible jumps. It cannot stop a
second integration for the same inverter from being counted beside it.

## Night

At night the inverter switches off and live records stop. Live sensors
(power, voltages, currents, frequency, temperatures, status) become
unavailable once no live record has arrived for three upload intervals
(never sooner than 15 minutes), or two minutes after the datalogger's last
connection closes, whichever comes first. Shorter gaps are the datalogger
reconnecting, which it does often. The energy totals, the working time and the last reading
time keep their last value.

A datalogger going offline and coming back is logged once each, at info
level. One that stays silent through three hours of daylight at Home
Assistant's location raises a notice in **Settings → System → Repairs**.
Daylight adds up across days, so a quiet evening and the next morning count
together; 24 hours of silence raise it whatever the sun does, which covers
short winter days and places where the sun does not rise or set. When each
datalogger was last connected is saved with the entry, so a restart or a
reload does not start the count again. The notice clears itself when the
datalogger connects again, or when its device or the entry is deleted; a
reload or a Configure save leaves it standing.

## What runs where

Everything runs on Home Assistant's event loop: socket reads are awaited, and
decoding a record is a few hundred bytes of arithmetic. The only blocking call
is the port check in the setup and reconfigure forms, which runs in Home
Assistant's executor.
