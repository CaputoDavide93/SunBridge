"""SunBridge: Home Assistant is the server a Growatt ShineLan-X uploads to."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .hub import ShineHub

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type ShineConfigEntry = ConfigEntry[ShineHub]


async def async_setup_entry(hass: HomeAssistant, entry: ShineConfigEntry) -> bool:
    """Start listening, then set up the entities."""
    hub = ShineHub(hass, entry)
    try:
        await hub.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="port_unavailable",
            translation_placeholders={"port": str(hub.port), "error": str(err)},
        ) from err
    entry.runtime_data = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        # Never leave the port bound, or its timer running, behind a failed
        # setup: the retry would find its own listener in the way.
        await hub.async_stop()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ShineConfigEntry) -> bool:
    """Close the port and every connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
