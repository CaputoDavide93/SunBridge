# 📡 Protocol notes

What the ShineLan-X datalogger on a Growatt MIC 2500TL-X sends, written from
**captures of the author's own datalogger** (25 Oct 2026,
`tests/fixtures/captured/`), not from any other project's source. Every statement here was checked against those bytes.

## Framing

| Bytes | Meaning |
|---|---|
| 0–1 | Transaction id, big-endian. The server echoes it in its reply |
| 2–3 | Protocol: `0x0006` on this datalogger (firmware 3.6.0.2) |
| 4–5 | Length: the bytes after this 6-byte header, **excluding** the 2-byte CRC |
| 6 | Unit id, `0x01` |
| 7 | Record type (below) |
| 8 … n−2 | Payload, **scrambled**: XOR with the ASCII key `Growatt`, repeating, starting at byte 8 |
| n−2 … n | CRC-16/Modbus (poly 0xA001, init 0xFFFF) over every byte before it, **big-endian** |

Verified on all 260 captured records: descrambling matches, CRC matches.

## Record types seen

| Type | From | Meaning | Count in capture |
|---|---|---|---|
| `0x16` | logger | Ping, every ~minute. Server echoes the same record | 98 |
| `0x03` | logger | Announce / identity, on every (re)connect: datalogger serial, inverter serial, settings | 34 |
| `0x04` | logger | **Live** data record, every 5 minutes (the logger's "Data Transfer Interval") | 33 |
| `0x50` | logger | **Buffered** data record: one stored while the server was unreachable, replayed ~every 30 s after reconnect. OLD values: never treat as live | 58 |
| `0x18` | server → logger | Set a datalogger register. Register `0x001F` is the clock: value `YYYY-MM-DD HH:MM:SS` (19 bytes), sent in UTC by the server we watched | — |
| `0x18` / `0x19` | logger | Its answer to a set / query command. No reply needed | 34 / 3 |

## Server replies

- **Ack** for `0x03`, `0x04`, `0x50`: same transaction id, protocol `0x0006`,
  length `0x0003`, unit `0x01`, the same record type, one payload byte `0x00`
  (scrambled: `0x47`), CRC. Example: `00 26 00 06 00 03 01 04 47 d2 9b`.
- **Ping** `0x16`: echo the record back unchanged.
- **Clock set** after an announce: type `0x18`, payload = datalogger serial
  (10 ASCII bytes, zero-padded to 30) + register `00 1F` + value length `00 13`
  + the 19 ASCII bytes of the time; scrambled and CRC'd as above.

## Payload start (types 0x03 / 0x04 / 0x50)

| Payload offset | Bytes | Field |
|---|---|---|
| 0 | 30 | Datalogger serial, ASCII, zero-padded (`DLGEXAMPLE`) |
| 30 | 30 | Inverter serial, ASCII, zero-padded (`INVEXAMPLE`) |

The data fields after that (power, energy, voltages, temperature, status,
record time) are located by matching the captured `0x04` records against Home
Assistant's own history of the same moments
(`tests/fixtures/captured/ha-history-2026-10-25.json`, values the server of the day
published in tenths). See [data-layout.md](data-layout.md).

## Also seen in the captures

- **Clock command transaction id.** The clock command the observed server sent
  always used transaction id `0x0001`, and the value was the UTC time it was
  sent, to the second (all 35 clock commands in
  `replies-2026-10-25.jsonl`).
- **`0x19` from the datalogger, hourly.** Three times (13:00, 14:00, 15:00 UTC)
  the datalogger sent a `0x19` record carrying register `0x001F` with the value
  `2000-01-01 01:01:01`. Nothing was sent back and the datalogger carried on;
  SunBridge does the same.
- **`0x18` answers.** After each clock command the datalogger answered `0x18`
  with register `0x001F` and a status byte `0x00`.
- **Reconnects.** The datalogger announced (`0x03`) 34 times in about three
  hours, each followed by a clock command. Its buffered `0x50` records arrive
  in the slots where a live `0x04` would otherwise be: on the capture day, from
  14:10 on, every third upload was a `0x50`.
- **Record time.** Buffered records carry the datalogger's clock in bytes
  60–65 of the payload; live records carry zeros there. On the capture day that
  clock ran about one hour ahead of UTC: a buffered record that arrived at
  14:54:52 UTC was stamped 15:54:25. SunBridge sets the clock in UTC, as the
  observed server did, so the datalogger may apply a time zone of its own
  setting; that is unverified. SunBridge keeps `record_time` as it comes,
  without a zone and unshifted. Which clock (the datalogger's or the
  inverter's) drives the inverter's daily energy counter is also unverified.
- **Announce timing.** After a reconnect the datalogger announced 1.5 to 17
  seconds after the ping before it. SunBridge gives a new connection 120
  seconds to identify itself (an announce, or a record that decodes), and
  pings do not extend that. A datalogger that connected without announcing
  and with nothing buffered would be closed after 120 seconds and reconnect;
  none did in the captures.
- **Register table.** Data and announce records hold two register blocks, each
  with its own first and last register number (see
  [data-layout.md](data-layout.md)).
- **Upload interval.** No captured command changes the datalogger's upload
  interval, so SunBridge never tries to. It is set on the datalogger's own
  web page.
