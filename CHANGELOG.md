# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0] - 2026-10-26

### Added

- **Reconfigure the port** (**⋮ → Reconfigure**). The new port is checked
  while the old listener still runs, a port another entry uses is refused,
  and if the listener cannot move it goes back to the old port, options and
  title. If going back fails too, the flow aborts saying so and a Repairs
  issue names the port to free, until the entry listens again. The options
  flow no longer has a port.
- **Repairs.** A known datalogger silent through three hours of daylight,
  added up across days, or for 24 hours, raises a Repairs issue, which
  clears itself when it reconnects or its device or the entry is deleted.
  When each datalogger was last connected is saved with the entry, so
  neither the issue nor the count is reset by a restart, a reload or a
  Configure save.
- **Deleting stale devices.** A datalogger that is not connected, or an
  inverter with no current live values, can be deleted from its device page;
  one that reports again comes back.
- Icons for the status sensor and each of its states.
- Diagnostics count lifetime readings ignored as jumps and resets accepted,
  and connections closed for not identifying in time or for a serial
  mismatch.
- The quality scale, rule by rule (`quality_scale.yaml`).
- The setup's port screen warns that the listener is open on every
  interface with no password, and why the allow-list matters; the created
  entry says how to add **Energy total** to the Energy dashboard.
- Documentation: networking prerequisites (a fixed address, the port, Docker
  and VLAN setups) and a port check from another device before the
  datalogger is changed; how to remove SunBridge and what stays; replacing
  hardware.
- `CONTRIBUTING.md`, issue forms (bug, hardware report, feature request,
  question) and a code of conduct.
- CI: strict typing with mypy, a 95% branch coverage gate, the suite on the
  oldest supported and the newest Home Assistant, code scanning and
  dependency review, and a release workflow that refuses unless the tag,
  the manifest and this changelog agree.

### Changed

- **Lifetime energy** ignores a single reading the inverter could not have
  made: lower than the last, or more than 50 kW could have added in the
  time elapsed. Three agreeing live readings accept a reset (a reset
  counter, a replaced inverter), and the Energy dashboard starts a new cycle
  instead of freezing. Buffered readings never count.
- **Energy today** shows 0 from Home Assistant's local midnight (daylight
  saving included) until the first live reading of the new day, and a
  restart after midnight no longer brings yesterday's value back.
- **Availability.** Live sensors also go unavailable two minutes after the
  datalogger's last connection closes; going offline and coming back are
  logged once each, at info level.
- **Simple mode keeps an allow-list**: the datalogger found during setup,
  applied in both modes. A Simple entry whose list is empty (from 0.1.0,
  which had none, or a setup finished before a datalogger connected) adopts
  the datalogger it knows on upgrade and at each start, if it knows exactly
  one; if it knows none or several it keeps answering any, and a Repairs
  issue says so until the list is set under **Configure → Advanced**.
- **Switching to Simple disables the Advanced sensors** instead of deleting
  them; Advanced turns back on only the ones Simple disabled.
- A second datalogger or inverter is named with its serial, so two devices
  never share a name.
- The config entry keeps the port in its data and the preferences in its
  options (version 1.2); existing entries are migrated.
- Allow-list serials are checked with the listener's own rule: 1 to 30
  letters and digits, no duplicates, at most 10, each with its own error.
- **Finish now** checks again that the port opens before the entry is
  saved.
- The Energy dashboard documentation says the source is added by hand and
  what protects the lifetime total.

### Fixed
- Reconfigure no longer reports a moved port when the reload returned failure with the old listener still running: a move counts only when Home Assistant reports success and the listener is on the new port.
- The rollback and silent-datalogger Repairs notices are kept across a restart.

- A connection now belongs to the first datalogger it identifies: a record
  with another serial on it closes it unanswered.
- A connection that does not identify its datalogger within 120 seconds is
  closed, pings or not, so pings alone cannot hold a connection slot.
- The clock command is always written in UTC, whatever the time zone of the
  host.
- An unexpected error in a connection handler is logged instead of lost.
- Diagnostics always blank the current frame's serials, however many
  serials came before.
- Restored devices that have not reported since a restart no longer count
  against the per-entry limit of two dataloggers and two inverters.
- After a restart or reload, the saved lifetime total and reading time are
  loaded before the port opens, so a reading that arrives before the
  sensors are set up is checked against them instead of passing unchecked.
- The diagnostics' redaction cache stops at 64 serials; a frame with two
  new serials could take it to 65.
- 0.1.0 said lifetime energy "cannot be counted twice". That was overstated:
  SunBridge cannot see a second integration feeding the same inverter, and a
  lifetime reading that was too high was not rejected.

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
