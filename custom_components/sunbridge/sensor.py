"""Sensors for each inverter the datalogger reports.

Two kinds:

- live measurements (power, voltages, temperature, status, ...) come only from
  live 0x04 records and go unavailable once none has arrived for the stale
  window;
- running totals (energy today, energy total, working time) and the last
  reading time keep their last value, across nights and restarts. Energy
  today is the inverter's own daily counter, shown as 0 from Home Assistant's
  local midnight until the first live record of the new day.

Simple mode creates the core sensors, each matched against Home Assistant's
own history of the same inverter (docs/data-layout.md). Advanced mode adds the
extra ones, which are plausibility-checked but have no such ground truth.
Switching back to Simple disables those in the registry rather than deleting
them, so a later switch to Advanced brings them back as they were.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Self

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from . import ShineConfigEntry
from .const import DOMAIN
from .decoder import STATUS_CODES, Reading
from .hub import InverterState, ShineHub

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class LiveDescription(SensorEntityDescription):
    """A value read from the newest live record."""

    value_fn: Callable[[Reading], Any]
    advanced: bool = False


@dataclass(frozen=True, kw_only=True)
class TotalDescription(SensorEntityDescription):
    """A value that keeps its last reading."""

    value_fn: Callable[[InverterState], Any]
    advanced: bool = False


LIVE_SENSORS: tuple[LiveDescription, ...] = (
    LiveDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=1,
        value_fn=lambda r: r.output_power,
    ),
    LiveDescription(
        key="pv_voltage",
        translation_key="pv_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda r: r.pv_voltage,
    ),
    LiveDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda r: r.temperature,
    ),
    LiveDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=list(STATUS_CODES.values()),
        value_fn=lambda r: r.status,
    ),
    # ── Advanced: plausibility-checked, no ground truth ─────────────────
    LiveDescription(
        key="pv_current",
        translation_key="pv_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda r: r.pv_current,
        advanced=True,
    ),
    LiveDescription(
        key="pv_power",
        translation_key="pv_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=1,
        value_fn=lambda r: r.pv_power,
        advanced=True,
    ),
    LiveDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda r: r.grid_voltage,
        advanced=True,
    ),
    LiveDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=2,
        value_fn=lambda r: r.grid_frequency,
        advanced=True,
    ),
    LiveDescription(
        key="grid_current",
        translation_key="grid_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda r: r.grid_current,
        advanced=True,
    ),
    LiveDescription(
        key="temperature_2",
        translation_key="temperature_2",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Unverified: what this register measures is not known.
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.temperature_2,
        advanced=True,
    ),
)


def _live_value(state: InverterState, field: str) -> Any:
    return getattr(state.live, field) if state.live is not None else None


TOTAL_SENSORS: tuple[TotalDescription, ...] = (
    TotalDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda s: _live_value(s, "energy_today"),
    ),
    TotalDescription(
        key="energy_total",
        translation_key="energy_total",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda s: s.energy_total,
    ),
    TotalDescription(
        key="last_reading",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.live_at,
    ),
    TotalDescription(
        key="working_time",
        translation_key="working_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: _live_value(s, "working_time_hours"),
        advanced=True,
    ),
)

ADVANCED_KEYS = frozenset(
    {
        *(d.key for d in LIVE_SENSORS if d.advanced),
        *(d.key for d in TOTAL_SENSORS if d.advanced),
    }
)
# Registry option marking an entity this integration disabled for Simple mode.
OPTION_DISABLED_BY_SIMPLE = "disabled_by_simple_mode"


def _unique_id(serial: str, key: str) -> str:
    return f"{serial}_{key}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ShineConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Sensors for every inverter known now, and each one seen later."""
    hub = entry.runtime_data
    added: set[str] = set()

    _sync_advanced_entities(hass, entry, hub.advanced)

    @callback
    def add(serial: str) -> None:
        if serial in added:
            return
        added.add(serial)
        entities: list[SensorEntity] = [
            LiveSensor(hub, serial, d)
            for d in LIVE_SENSORS
            if hub.advanced or not d.advanced
        ]
        entities += [
            (TodaySensor if d.key == "energy_today" else TotalSensor)(hub, serial, d)
            for d in TOTAL_SENSORS
            if hub.advanced or not d.advanced
        ]
        async_add_entities(entities)

    for serial in list(hub.inverters):
        add(serial)
    entry.async_on_unload(
        async_dispatcher_connect(hass, hub.signal_new_inverter, add)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, hub.signal_removed, added.discard)
    )


@callback
def _sync_advanced_entities(
    hass: HomeAssistant, entry: ShineConfigEntry, advanced: bool
) -> None:
    """Simple disables the Advanced sensors it does not create, keeping their
    names, history and settings; Advanced enables only those Simple disabled.
    One the user disabled, or one disabled by default and never enabled,
    stays as it was."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain != "sensor" or entity.platform != DOMAIN:
            continue
        if not any(entity.unique_id.endswith(f"_{key}") for key in ADVANCED_KEYS):
            continue
        options: Mapping[str, Any] = entity.options.get(DOMAIN) or {}
        ours = bool(options.get(OPTION_DISABLED_BY_SIMPLE))
        if not advanced and entity.disabled_by is None:
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
            registry.async_update_entity_options(
                entity.entity_id, DOMAIN, {OPTION_DISABLED_BY_SIMPLE: True}
            )
            # Not created in Simple: no "unavailable" left standing for it.
            hass.states.async_remove(entity.entity_id)
        elif advanced and ours:
            if entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
                registry.async_update_entity(entity.entity_id, disabled_by=None)
            registry.async_update_entity_options(
                entity.entity_id, DOMAIN, {OPTION_DISABLED_BY_SIMPLE: False}
            )


class _InverterSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, hub: ShineHub, serial: str, description: SensorEntityDescription
    ) -> None:
        self.entity_description = description
        self._hub = hub
        self._serial = serial
        self._attr_unique_id = _unique_id(serial, description.key)
        self._attr_device_info = hub.inverter_device(serial)

    @property
    def _state(self) -> InverterState:
        # Never stored from here: a removed device must stay forgotten.
        return self._hub.inverters.get(self._serial) or InverterState(self._serial)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._hub.signal_update(self._serial), self._updated
            )
        )

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()


class LiveSensor(_InverterSensor):
    """A live measurement: unavailable without a recent 0x04 record."""

    entity_description: LiveDescription

    @property
    def available(self) -> bool:
        return self._state.live is not None and self._hub.is_live(self._serial)

    @property
    def native_value(self) -> Any:
        live = self._state.live
        return None if live is None else self.entity_description.value_fn(live)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "status" or self._state.live is None:
            return None
        return {"code": self._state.live.status_code}


class TotalSensor(_InverterSensor, RestoreSensor):
    """A running total or time stamp: keeps its value, across restarts too."""

    entity_description: TotalDescription
    _restored: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is None or last.native_value is None:
            return
        value = last.native_value
        if self.entity_description.key == "last_reading":
            if isinstance(value, datetime):
                self._restored = value
                self._hub.seed_last_reading(self._serial, value)
            return
        if isinstance(value, date):  # a total is a number, never a date
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        self._restored = number
        if self.entity_description.key == "energy_total":
            self._hub.seed_lifetime(self._serial, number)

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value_fn(self._state)
        return self._restored if value is None else value


@dataclass
class TodayStoredData(SensorExtraStoredData):
    """The saved value, and the local day it belongs to."""

    day: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return super().as_dict() | {"day": self.day}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> Self | None:
        base = SensorExtraStoredData.from_dict(restored)
        if base is None:
            return None
        return cls(
            base.native_value, base.native_unit_of_measurement, restored.get("day")
        )


class TodaySensor(TotalSensor):
    """The inverter's daily counter, 0 from Home Assistant's local midnight.

    The inverter resets its counter when it starts in the morning, so all
    night the last record still carries yesterday's total. The new day starts
    at local midnight here instead, in whatever time zone Home Assistant is
    set to, daylight saving included. total_increasing turns the drop into
    a new cycle; no last_reset is involved.
    """

    _restored_day: date | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._midnight, hour=0, minute=0, second=0
            )
        )
        if self._restored is None:
            return
        extra = await self.async_get_last_extra_data()
        stored = TodayStoredData.from_dict(extra.as_dict()) if extra else None
        if stored is not None and stored.day:
            self._restored_day = date.fromisoformat(stored.day)
        else:  # saved by an older version: the state's own time decides
            last = await self.async_get_last_state()
            if last is not None:
                self._restored_day = dt_util.as_local(last.last_updated).date()

    @callback
    def _midnight(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        value = self._hub.energy_today(self._serial)
        if value is not None:
            return value
        if self._restored is None:
            return None
        return self._restored if self._restored_day == dt_util.now().date() else 0.0

    @property
    def extra_restore_state_data(self) -> TodayStoredData:
        return TodayStoredData(
            self.native_value,
            self.native_unit_of_measurement,
            dt_util.now().date().isoformat(),
        )
