# 🔬 Data layout

How each value SunBridge reads was located in the datalogger's data records,
and what proves it. Everything here comes from
**captures of the author's own datalogger** on 25 Oct 2026 (`tests/fixtures/captured/`) and from Home Assistant's own history
of the same hours (`ha-history-2026-10-25.json`, the states Home Assistant
showed at the time). No other project's source was read.

`tests/test_decoder.py` re-runs every comparison below on every `make check`.

## Method

1. Descramble every captured `0x04` (live) record (see
   [protocol-notes.md](protocol-notes.md)).
2. For each record, take the value Home Assistant showed for the same moment:
   the state change within (−5 s, +90 s] of the record's arrival, or, if the
   state did not change, the last state before it. A state replayed within 5 s
   of `unavailable`/`unknown` is a restart, not a record, and is skipped. The
   one late publication (the 14:44:46 record, shown at 14:46:09 after an MQTT
   restart) is covered by the 90 s window.
3. Try every byte offset as a big-endian 16-bit and 32-bit unsigned integer,
   divided by 10, and keep the offsets that equal the ground truth in every
   record.

Of the 33 live records, 32 have ground truth; the first (12:15:01) arrived
before Home Assistant had any state, and is decoded but not compared.

## The record

After the two 30-byte serials (datalogger, then inverter):

| Payload offset | Bytes | Meaning | Evidence |
|---|---|---|---|
| 60 | 6 | Record time: year − 2000, month, day, hour, minute, second | All zero in all 33 live records. In all 58 buffered (`0x50`) records it is a plausible time no later than the arrival (the datalogger's clock ran one hour ahead of UTC that day) |
| 66 | 1 | Number of register blocks | `2` in every data and announce record |
| 67 | 4 | Block: first register, last register | `0x0BB8 0x0C34` (3000..3124), then `0x0C35 0x0CB1` (3125..3249) in data records; `0..124`, then `3000..3124` in the announce |
| 71 | 2 each | The registers, in order | 250 bytes per block; two blocks plus headers make the 575-byte payload exactly |

The decoder reads the block table rather than fixed offsets, so register
3000 is at payload offset 71 and register *n* at `71 + 2 × (n − 3000)`.

## Verified against Home Assistant's history (all 32 records)

| Value | Register(s) | Scale | Example, 12:20:00 record | Home Assistant showed |
|---|---|---|---|---|
| Output power | 3023–3024, 32-bit | 0.1 W | `0, 2729` → 272.9 W | `sensor.solar_power` 272.9 |
| Energy today | 3049–3050, 32-bit | 0.1 kWh | `0, 25` → 2.5 kWh | `sensor.solar_energy_today` 2.5 |
| Energy total (lifetime) | 3051–3052, 32-bit | 0.1 kWh | `0, 23121` → 2312.1 kWh | `sensor.solar_energy_total` 2312.1 |
| PV voltage | 3003 | 0.1 V | `1924` → 192.4 V | `sensor.solar_panel_voltage` 192.4 |
| Inverter temperature | 3093 | 0.1 °C | `330` → 33.0 °C | `sensor.solar_inverter_temperature` 33.0 |
| Status | 3000 | code | `1` | `sensor.solar_inverter_status` Producing |

Two more examples from the same run: 14:04:49 (130.6 W, 2.8 kWh, 2312.4 kWh,
180.6 V, 29.0 °C) and 15:19:43 (87.5 W, 3.0 kWh, 2312.6 kWh, 184.5 V,
27.7 °C) match the same registers.

**Caveat on the 32-bit reads.** In every capture the high word of the power
and energy pairs is 0, so the captures prove the low word only. They are read
as 32-bit because the high register sits immediately before each one and is
otherwise unused, and because the working-time pair (3047–3048, below) does
carry a non-zero high word in the same layout. A lifetime total above
6553.5 kWh is the first time the high word will matter.

## Status codes

| Code | State | Evidence |
|---|---|---|
| 0 | `waiting` | Four buffered dawn records (25 Oct, 05:58–06:10 by the datalogger's clock) with zero output power and PV voltage present. No Home Assistant ground truth (buffered) |
| 1 | `producing` | Every live record, and Home Assistant showed "Producing" throughout |
| 3 | `fault` | **Not observed.** Taken from the brief; never seen in the captures |

Any other code leaves the status sensor `unknown`; the raw code is always in
its `code` attribute.

## Plausibility-checked only (Advanced mode)

No Home Assistant history exists for these, so they are checked against
physics, in `tests/test_decoder.py`, and shown only in Advanced mode:

| Value | Register(s) | Scale | Check |
|---|---|---|---|
| PV power | 3001–3002, 32-bit | 0.1 W | ≥ 90 % of output power in every record (it is the DC input); equals register 3005–3006 (PV1 power) on this single-string inverter |
| PV current | 3004 | 0.1 A | PV voltage × current within 25 % of PV power whenever PV power > 100 W (the 0.1 A step limits the precision) |
| Grid frequency | 3025 | 0.01 Hz | 49.79–50.13 Hz across the live records |
| Grid voltage | 3026 | 0.1 V | 242.5–245.5 V, inside UK limits (216–253 V); register 3038 repeats it in every record |
| Grid current | 3027 | 0.1 A | Grid voltage × current within 40 % of output power (at 0.4–1.3 A the 0.1 A step is most of the error) |
| Working time | 3047–3048, 32-bit | 0.5 s | Between consecutive live records the counter advances by the wall-clock gap within ±20 s; 5,723 h on the capture day |

## Unverified

| Value | Register | Why it is unverified |
|---|---|---|
| Second temperature | 3097, 0.1 °C | 38.5 → 35.1 over the afternoon, falling with the inverter temperature, but nothing says what it measures. Advanced mode creates it **disabled**, labelled "Second temperature" |

## Left out

Registers 3028–3029 (grid apparent power, ≈ grid V × I), 3053–3058 (PV-side
energy counters), 3086–3088, 3091, 3098, 3100, 3101 and the second block
(3125–3249) change or hold values, but nothing in the captures says what they
mean. The decoder does not read them.
