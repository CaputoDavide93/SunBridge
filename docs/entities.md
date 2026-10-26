# 📊 Entities

Two devices per installation: **Solar Datalogger** (one per datalogger) and
**Solar Inverter** (one per inverter, under its datalogger). Entity names come
from the device name and the entity name, so the defaults below are what Home
Assistant creates for the first inverter and datalogger. A second one of
either kind is named with its serial (**Solar Inverter INVEXAMPLE**), so its
ids carry the serial too (`sensor.solar_inverter_invexample_power`). Unique
ids are `<serial>_<key>`, so renaming an entity never breaks it.

<!-- AUTOGEN:entities -->
| Entity | Default entity id | Unit | Behaviour | Mode |
|---|---|---|---|---|
| Power | `sensor.solar_inverter_power` | W | Live: unavailable after the stale window | Simple |
| PV voltage | `sensor.solar_inverter_pv_voltage` | V | Live: unavailable after the stale window | Simple |
| Temperature | `sensor.solar_inverter_temperature` | °C | Live: unavailable after the stale window | Simple |
| Status | `sensor.solar_inverter_status` | waiting / producing / fault | Live: unavailable after the stale window | Simple |
| PV current | `sensor.solar_inverter_pv_current` | A | Live: unavailable after the stale window | Advanced |
| PV power | `sensor.solar_inverter_pv_power` | W | Live: unavailable after the stale window | Advanced |
| Grid voltage | `sensor.solar_inverter_grid_voltage` | V | Live: unavailable after the stale window | Advanced |
| Grid frequency | `sensor.solar_inverter_grid_frequency` | Hz | Live: unavailable after the stale window | Advanced |
| Grid current | `sensor.solar_inverter_grid_current` | A | Live: unavailable after the stale window | Advanced |
| Second temperature | `sensor.solar_inverter_second_temperature` | °C | Live: unavailable after the stale window | Advanced, disabled (unverified) |
| Energy today | `sensor.solar_inverter_energy_today` | kWh | Keeps its last value, total increasing | Simple |
| Energy total | `sensor.solar_inverter_energy_total` | kWh | Keeps its last value, total increasing | Simple |
| Last reading | `sensor.solar_inverter_last_reading` | timestamp | Keeps its last value | Simple |
| Working time | `sensor.solar_inverter_working_time` | h | Keeps its last value, total increasing | Advanced |
| Connected | `binary_sensor.solar_datalogger_connected` | on / off | On while an open connection from it has sent a decodable record | Simple |
<!-- /AUTOGEN:entities -->

- **Live** sensors come only from live `0x04` records. They go unavailable when
  none has arrived for three upload intervals (at least 15 minutes), or two
  minutes after the datalogger's last connection closes. The totals and
  **Last reading** keep their value. The datalogger going offline, and
  coming back, is logged once each at info level.
- **Energy total** is the inverter's lifetime counter. A single reading the
  inverter could not have made (lower than the last, or more than 50 kW
  could have added in the time since) is ignored; three live readings in a
  row that agree accept a reset, and the Energy dashboard starts a new
  cycle. Buffered readings never count. Details in
  [how-it-works.md](how-it-works.md#the-energy-totals).
- **Energy today** is the inverter's raw daily counter: 0 from Home
  Assistant's local midnight (daylight saving included) until the first live
  reading of the new day. Use **Energy total** for the Energy dashboard.
- **Status** is `waiting` (code 0), `producing` (code 1) or `fault` (code 3).
  Any other code leaves it unknown; the raw code is always in its `code`
  attribute. Code 3 has never been seen in the captures.
- **Simple** sensors were each matched against Home Assistant's own history of
  the same inverter, record by record. **Advanced** sensors are
  plausibility-checked only, and the second temperature is unverified, so it is
  created disabled. The evidence for each is in [data-layout.md](data-layout.md).
- **Last reading** is when Home Assistant received the newest live record.
  Live records carry no time of their own.

## Devices

- **Names.** The first datalogger and inverter are **Solar Datalogger** and
  **Solar Inverter**; a second of either kind carries its serial in its name.
- **No model or firmware.** No captured record proves which bytes hold the
  inverter's model or either device's firmware, so the device pages show the
  manufacturer and serial only, rather than a guess shown as fact.
- **Deleting a device.** A device that is not reporting (a replaced or
  retired datalogger or inverter) can be deleted from its page, **⋮ →
  Delete**. A datalogger that is connected, or an inverter whose live sensors
  are current, cannot. A deleted device that reports again comes back.
- **Switching modes.** Simple disables the Advanced sensors instead of
  deleting them, so their history and settings stay; Advanced turns back on
  the ones Simple disabled. A sensor you disabled yourself stays disabled.
