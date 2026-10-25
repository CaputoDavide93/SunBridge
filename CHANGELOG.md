# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] - 2026-10-25

First public release. Tested on one Growatt MIC 2500TL-X with a ShineLan-X
datalogger on firmware 3.6.0.2.

### Added

- **Guided setup.** Welcome (Simple or Advanced), the port with Home
  Assistant's own address detected, numbered steps for the datalogger's web
  page with that address and port filled in, then a step that listens until
  the datalogger's first readable record arrives and shows both serials.
  After ten minutes it offers to finish anyway or listen again. The port is
  freed when setup ends or is abandoned, a port another entry holds is
  explained, and Advanced prefills the allow-list with the datalogger found.
- **Documentation for a first install:** a checklist, every setup screen,
  the datalogger's light, troubleshooting by symptom, going back to Growatt's
  cloud, and a FAQ.
- **Home Assistant as the datalogger's server.** An asyncio TCP listener
  (port 5279 by default) for a Growatt ShineLan-X: frames reassembled across
  reads, CRC-checked, descrambled; `0x03`, `0x04` and `0x50` acknowledged,
  `0x16` pings echoed, the datalogger's clock set in UTC after every announce,
  answers to commands (`0x18`, `0x19`) left unanswered. Several connections at
  once, reconnects, and idle connections closed after ten minutes.
- **Sensors per inverter**, decoded from live `0x04` records only: power,
  energy today, energy total (for the Energy dashboard), PV voltage,
  temperature, status and last reading time, plus a connectivity sensor per
  datalogger. Each was matched against Home Assistant's own history of the
  same inverter (`docs/data-layout.md`).
- **Buffered records never shown.** `0x50` records carry old values: they are
  acknowledged and counted, and never change a state.
- **Lifetime energy never decreases**, including across restarts, so the
  Energy dashboard cannot count the same energy twice.
- **Night handling.** Live sensors go unavailable when no live record has
  arrived for 15 minutes (or three upload intervals); the energy totals and
  the last reading time keep their value.
- **Simple and Advanced setup.** Simple asks for the port. Advanced adds PV
  current and power, grid voltage, frequency and current, working time and an
  unverified second temperature (disabled), a datalogger serial allow-list,
  and the upload interval the datalogger is set to.
- Diagnostics with record counts, CRC failures, the last frame per type and
  the open connections, with every serial removed.

### Security

- Frames with a unit id other than `0x01` are refused.
- The frame parser is bounded: table-driven CRC, index-based parsing, and a
  connection is dropped for a header over 2048 bytes, more than 8 bad frames
  or more than 1024 skipped bytes.
- At most four connections at once. A new connection must send a valid frame
  within 30 seconds, and the idle timeout counts valid frames, not bytes, so a
  connection cannot be held open by trickling bytes.
- Devices are created only after a record's payload decodes completely, never
  from a ping or a damaged frame, and at most two dataloggers and two
  inverters per entry.
- A setup that fails while creating the entities closes the port again.
- Diagnostics keep no payload for unknown record types, and zero both serial
  fields of every other type, so no serial can leak through them.
