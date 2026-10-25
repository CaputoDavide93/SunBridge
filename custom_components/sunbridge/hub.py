"""The Home Assistant side of the listener: state per device, and signals.

The server reports frames; the hub decides what Home Assistant may see:

- only a live 0x04 record updates live values. A buffered 0x50 record carries
  OLD values and is counted, never shown;
- the lifetime energy never goes down. A lower value is ignored and counted,
  so the Energy dashboard can never count the same energy twice;
- live values expire when no 0x04 record has arrived for the stale window
  (at night the inverter stops and so do the records); the energy totals and
  the last reading time keep their last value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALLOWED_SERIALS,
    CONF_MODE,
    CONF_PORT,
    CONF_UPLOAD_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_UPLOAD_INTERVAL,
    DEVICE_DATALOGGER_NAME,
    DEVICE_INVERTER_NAME,
    DOMAIN,
    HANDSHAKE_TIMEOUT_SECONDS,
    IDLE_TIMEOUT_SECONDS,
    MANUFACTURER,
    MAX_CONNECTIONS,
    MAX_DATALOGGERS,
    MAX_INVERTERS,
    MODE_ADVANCED,
    MODE_SIMPLE,
    parse_serials,
    stale_after_minutes,
)
from .decoder import Reading
from .server import SunBridge

_LOGGER = logging.getLogger(__name__)

STALE_CHECK_INTERVAL = timedelta(seconds=30)
INVERTER_PREFIX = "inverter_"
DATALOGGER_PREFIX = "datalogger_"


def entry_settings(entry: ConfigEntry) -> dict:
    """The entry's settings: options, where set, over the setup's data."""
    merged = {
        CONF_PORT: DEFAULT_PORT,
        CONF_MODE: MODE_SIMPLE,
        CONF_ALLOWED_SERIALS: "",
        CONF_UPLOAD_INTERVAL: DEFAULT_UPLOAD_INTERVAL,
    }
    merged.update(entry.data)
    merged.update(entry.options)
    return merged


@dataclass
class InverterState:
    """What Home Assistant knows about one inverter."""

    serial: str
    datalogger: str | None = None
    live: Reading | None = None
    live_at: datetime | None = None
    energy_total: float | None = None
    lifetime_decreases_ignored: int = 0
    buffered_records: int = 0
    last_buffered_time: datetime | None = None


@dataclass
class HubCounters:
    """Hub-level counters for diagnostics."""

    live_records: int = 0
    buffered_records: int = 0
    lifetime_decreases_ignored: int = 0
    refused_dataloggers: int = 0
    refused_inverters: int = 0
    connections: dict[str, int] = field(default_factory=dict)


class ShineHub:
    """One config entry: one listening port and the devices seen on it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        settings = entry_settings(entry)
        self.port: int = int(settings[CONF_PORT])
        self.advanced: bool = settings[CONF_MODE] == MODE_ADVANCED
        self.allowed: frozenset[str] = (
            parse_serials(settings[CONF_ALLOWED_SERIALS])
            if self.advanced
            else frozenset()
        )
        self.upload_interval = int(
            settings[CONF_UPLOAD_INTERVAL] if self.advanced else DEFAULT_UPLOAD_INTERVAL
        )
        self.stale_after = timedelta(minutes=stale_after_minutes(self.upload_interval))
        self.inverters: dict[str, InverterState] = {}
        self.dataloggers: set[str] = set()
        self.counters = HubCounters()
        self.server = SunBridge(
            self,
            self.port,
            allowed_serials=self.allowed,
            idle_timeout=IDLE_TIMEOUT_SECONDS,
            handshake_timeout=HANDSHAKE_TIMEOUT_SECONDS,
            max_connections=MAX_CONNECTIONS,
            now=dt_util.utcnow,
        )
        self._unsub_stale: CALLBACK_TYPE | None = None
        self._was_live: dict[str, bool] = {}

    # ── signals ──────────────────────────────────────────────────────────
    @property
    def signal_new_inverter(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_new_inverter"

    @property
    def signal_new_datalogger(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_new_datalogger"

    def signal_update(self, serial: str) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_update_{serial}"

    # ── lifecycle ────────────────────────────────────────────────────────
    async def async_start(self) -> None:
        """Restore known devices, then listen. Raises OSError on a busy port."""
        self._restore_devices()
        await self.server.start()
        self._unsub_stale = async_track_time_interval(
            self.hass, self._check_stale, STALE_CHECK_INTERVAL
        )

    async def async_stop(self) -> None:
        if self._unsub_stale is not None:
            self._unsub_stale()
            self._unsub_stale = None
        await self.server.stop()

    @callback
    def _restore_devices(self) -> None:
        """Devices seen before a restart get their entities back at once."""
        registry = dr.async_get(self.hass)
        devices = dr.async_entries_for_config_entry(registry, self.entry.entry_id)
        by_id = {device.id: device for device in devices}
        for device in devices:
            for domain, ident in device.identifiers:
                if domain != DOMAIN:
                    continue
                if ident.startswith(DATALOGGER_PREFIX):
                    self.dataloggers.add(ident.removeprefix(DATALOGGER_PREFIX))
                elif ident.startswith(INVERTER_PREFIX):
                    state = InverterState(ident.removeprefix(INVERTER_PREFIX))
                    parent = by_id.get(device.via_device_id or "")
                    if parent is not None:
                        for p_domain, p_ident in parent.identifiers:
                            if p_domain == DOMAIN and p_ident.startswith(
                                DATALOGGER_PREFIX
                            ):
                                state.datalogger = p_ident.removeprefix(
                                    DATALOGGER_PREFIX
                                )
                    self.inverters[state.serial] = state

    # ── device info ──────────────────────────────────────────────────────
    @staticmethod
    def datalogger_device(serial: str) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{DATALOGGER_PREFIX}{serial}")},
            name=DEVICE_DATALOGGER_NAME,
            manufacturer=MANUFACTURER,
            serial_number=serial,
        )

    @staticmethod
    def inverter_device(serial: str) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{INVERTER_PREFIX}{serial}")},
            name=DEVICE_INVERTER_NAME,
            manufacturer=MANUFACTURER,
            serial_number=serial,
        )

    @callback
    def _link_inverter(self, inverter: str, datalogger: str) -> None:
        """Create or re-parent the inverter's device under its datalogger."""
        registry = dr.async_get(self.hass)
        parent = registry.async_get_device_by_identifier(
            (DOMAIN, f"{DATALOGGER_PREFIX}{datalogger}"), self.entry.entry_id
        )
        device = registry.async_get_or_create(
            config_entry_id=self.entry.entry_id, **self.inverter_device(inverter)
        )
        if parent is not None and device.via_device_id != parent.id:
            registry.async_update_device(device.id, via_device_id=parent.id)

    # ── what the server reports (server.Listener) ─────────────────────────
    @callback
    def datalogger_connected(self, serial: str, connected: bool) -> bool:
        if (
            connected
            and serial not in self.dataloggers
            and len(self.dataloggers) >= MAX_DATALOGGERS
        ):
            self.counters.refused_dataloggers += 1
            _LOGGER.warning(
                "Refusing another datalogger: this entry already has %s",
                MAX_DATALOGGERS,
            )
            return False
        count = self.counters.connections.get(serial, 0) + (1 if connected else -1)
        self.counters.connections[serial] = max(count, 0)
        if serial not in self.dataloggers:
            self.dataloggers.add(serial)
            dr.async_get(self.hass).async_get_or_create(
                config_entry_id=self.entry.entry_id, **self.datalogger_device(serial)
            )
            async_dispatcher_send(self.hass, self.signal_new_datalogger, serial)
        async_dispatcher_send(self.hass, self.signal_update(serial))
        return True

    @callback
    def inverter_seen(self, datalogger: str, inverter: str) -> bool:
        state = self.inverters.get(inverter)
        if state is not None and state.datalogger == datalogger:
            return True
        if state is None:
            if len(self.inverters) >= MAX_INVERTERS:
                self.counters.refused_inverters += 1
                _LOGGER.warning(
                    "Ignoring another inverter: this entry already has %s",
                    MAX_INVERTERS,
                )
                return False
            state = self.inverters[inverter] = InverterState(inverter)
            state.datalogger = datalogger
            self._link_inverter(inverter, datalogger)
            async_dispatcher_send(self.hass, self.signal_new_inverter, inverter)
            return True
        # Restored after a restart, or moved to another datalogger.
        state.datalogger = datalogger
        self._link_inverter(inverter, datalogger)
        return True

    @callback
    def reading(self, reading: Reading, *, live: bool) -> None:
        state = self.inverters.get(reading.inverter_serial)
        if state is None:  # not accepted by inverter_seen: never reported
            return
        if not live:
            # Buffered: values from when the server was unreachable. Never
            # shown, never allowed near the lifetime total.
            state.buffered_records += 1
            state.last_buffered_time = reading.record_time
            self.counters.buffered_records += 1
            return
        self.counters.live_records += 1
        state.live = reading
        state.live_at = dt_util.utcnow()
        self._raise_lifetime(state, reading.energy_total)
        self._was_live[state.serial] = True
        async_dispatcher_send(self.hass, self.signal_update(state.serial))

    # ── rules ────────────────────────────────────────────────────────────
    def _raise_lifetime(self, state: InverterState, value: float) -> None:
        if state.energy_total is not None and value < state.energy_total:
            state.lifetime_decreases_ignored += 1
            self.counters.lifetime_decreases_ignored += 1
            _LOGGER.debug(
                "Ignoring a lifetime energy of %s kWh below the last %s kWh",
                value,
                state.energy_total,
            )
            return
        state.energy_total = value

    @callback
    def seed_lifetime(self, serial: str, value: float) -> None:
        """A restored lifetime total: the floor a new reading must not go under."""
        state = self.inverters.setdefault(serial, InverterState(serial))
        if state.energy_total is None or value > state.energy_total:
            state.energy_total = value

    def is_live(self, serial: str) -> bool:
        state = self.inverters.get(serial)
        return (
            state is not None
            and state.live_at is not None
            and dt_util.utcnow() - state.live_at < self.stale_after
        )

    def datalogger_is_connected(self, serial: str) -> bool:
        return self.counters.connections.get(serial, 0) > 0

    @callback
    def _check_stale(self, _now: datetime) -> None:
        for serial in list(self._was_live):
            if self._was_live[serial] and not self.is_live(serial):
                self._was_live[serial] = False
                async_dispatcher_send(self.hass, self.signal_update(serial))
