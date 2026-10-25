# 📊 Entities

Two devices per installation: **Solar Datalogger** (one per datalogger) and
**Solar Inverter** (one per inverter, under its datalogger). Entity names come
from the device name and the entity name, so the defaults below are what Home
Assistant creates for the first inverter; a second one gets `_2`. Unique ids
are `<serial>_<key>`, so renaming an entity never breaks it.

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
  none has arrived for three upload intervals (at least 15 minutes).
- **Status** is `waiting` (code 0), `producing` (code 1) or `fault` (code 3).
  Any other code leaves it unknown; the raw code is always in its `code`
  attribute. Code 3 has never been seen in the captures.
- **Simple** sensors were each matched against Home Assistant's own history of
  the same inverter, record by record. **Advanced** sensors are
  plausibility-checked only, and the second temperature is unverified, so it is
  created disabled. The evidence for each is in [data-layout.md](data-layout.md).
- **Last reading** is when Home Assistant received the newest live record.
  Live records carry no time of their own.
