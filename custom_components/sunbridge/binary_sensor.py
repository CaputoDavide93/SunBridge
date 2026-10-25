"""One connectivity sensor per datalogger: is a connection from it open?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ShineConfigEntry
from .hub import ShineHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ShineConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A sensor for every datalogger known now, and each one seen later."""
    hub = entry.runtime_data
    added: set[str] = set()

    @callback
    def add(serial: str) -> None:
        if serial in added:
            return
        added.add(serial)
        async_add_entities([DataloggerConnected(hub, serial)])

    for serial in list(hub.dataloggers):
        add(serial)
    entry.async_on_unload(
        async_dispatcher_connect(hass, hub.signal_new_datalogger, add)
    )


class DataloggerConnected(BinarySensorEntity):
    """On while the datalogger holds a connection that sent a decodable record."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, hub: ShineHub, serial: str) -> None:
        self._hub = hub
        self._serial = serial
        self._attr_unique_id = f"{serial}_connected"
        self._attr_device_info = hub.datalogger_device(serial)

    @property
    def is_on(self) -> bool:
        return self._hub.datalogger_is_connected(self._serial)

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
