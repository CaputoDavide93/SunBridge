"""SunBridge: Home Assistant is the server a Growatt ShineLan-X uploads to."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

# Config entry migration lives with the flow that defines the entry versions.
from .config_flow import async_migrate_entry  # noqa: F401
from .const import DOMAIN
from .hub import (
    DATALOGGER_PREFIX,
    ShineHub,
    allow_list_issue_id,
    async_settle_allow_list,
    entry_store,
    rollback_issue_id,
    silent_issue_id,
)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type ShineConfigEntry = ConfigEntry[ShineHub]


async def async_setup_entry(hass: HomeAssistant, entry: ShineConfigEntry) -> bool:
    """Start listening, then set up the entities."""
    async_settle_allow_list(hass, entry)
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
    # Listening again: a failed port move, if there was one, is fixed.
    ir.async_delete_issue(hass, DOMAIN, rollback_issue_id(entry.entry_id))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ShineConfigEntry) -> bool:
    """Close the port and every connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ShineConfigEntry) -> None:
    """The entry is gone: so are its notices and its saved connection times."""
    store = entry_store(hass, entry.entry_id)
    saved = await store.async_load() or {}
    serials = set(saved.get("dataloggers") or {})
    for device in dr.async_entries_for_config_entry(
        dr.async_get(hass), entry.entry_id
    ):
        for domain, ident in device.identifiers:
            if domain == DOMAIN and ident.startswith(DATALOGGER_PREFIX):
                serials.add(ident.removeprefix(DATALOGGER_PREFIX))
    for serial in serials:
        ir.async_delete_issue(hass, DOMAIN, silent_issue_id(serial))
    ir.async_delete_issue(hass, DOMAIN, rollback_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, allow_list_issue_id(entry.entry_id))
    await store.async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ShineConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let a device that is not reporting be removed: a replaced inverter or
    datalogger, or one that is gone. One that reports again comes back."""
    hub = entry.runtime_data
    if not hub.device_removable(device.identifiers):
        return False
    hub.forget(device.identifiers)
    return True
