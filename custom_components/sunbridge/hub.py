"""The Home Assistant side of the listener: state per device, and signals.

The server reports frames; the hub decides what Home Assistant may see:

- only a live 0x04 record updates live values. A buffered 0x50 record carries
  OLD values and is counted, never shown;
- the lifetime energy ignores a reading the inverter could not have made: one
  lower than the last, or higher than it could produce in the time since. Such
  a reading is held back; when three live records in a row agree with each
  other, it is real (a reset counter, a replaced inverter) and shown, and the
  total_increasing state class turns the drop into a new meter cycle;
- live values go unavailable when no 0x04 record has arrived for the stale
  window, or as soon as the datalogger has had no connection for a short
  grace. The energy totals and the last reading time keep their last value;
- a datalogger going offline, and coming back, is logged once each; one silent
  through hours of daylight raises a repair issue, cleared when it returns
  (or its device or the entry is removed), never by an unload. When each
  datalogger was last connected is saved with the entry, so the silence is
  measured across reloads and restarts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorExtraStoredData
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import restore_state
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
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
    LIFETIME_CONFIRM_RECORDS,
    LIFETIME_SLACK_KWH,
    MANUFACTURER,
    MAX_CONNECTIONS,
    MAX_DATALOGGERS,
    MAX_INVERTERS,
    MAX_PLAUSIBLE_POWER_KW,
    MODE_ADVANCED,
    MODE_SIMPLE,
    RECONNECT_GRACE_SECONDS,
    SILENT_DAYLIGHT_HOURS,
    SILENT_FALLBACK_HOURS,
    parse_serials,
    stale_after_minutes,
)
from .decoder import Reading
from .server import SunBridge

_LOGGER = logging.getLogger(__name__)

STALE_CHECK_INTERVAL = timedelta(seconds=30)
RECONNECT_GRACE = timedelta(seconds=RECONNECT_GRACE_SECONDS)
INVERTER_PREFIX = "inverter_"
DATALOGGER_PREFIX = "datalogger_"
ISSUE_SILENT = "datalogger_silent"
# When each datalogger was last connected, saved per entry.
STORAGE_VERSION = 1
# A save after a connect or disconnect waits this long, so a burst of
# reconnects is one write; while connected, the time is saved this often.
SAVE_DELAY_SECONDS = 10
SAVE_CONNECTED_EVERY = timedelta(minutes=10)
TROUBLESHOOTING_URL = (
    "https://github.com/CaputoDavide93/SunBridge-Growatt-Home-Assistant/blob/main/docs/troubleshooting.md"
)
SECURITY_URL = "https://github.com/CaputoDavide93/SunBridge-Growatt-Home-Assistant/blob/main/SECURITY.md"


def entry_settings(entry: ConfigEntry) -> dict[str, Any]:
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
    # Seen in this run. Restored devices are not, until they report again,
    # and only seen devices count against the per-entry caps.
    seen: bool = False
    live: Reading | None = None
    live_at: datetime | None = None
    energy_total: float | None = None
    # When the shown lifetime was read: bounds how much it may rise.
    energy_total_at: datetime | None = None
    # A lifetime the filter held back, how many live records agree, and when
    # the newest of them arrived.
    pending_total: float | None = None
    pending_count: int = 0
    pending_at: datetime | None = None
    lifetime_decreases_ignored: int = 0
    lifetime_jumps_ignored: int = 0
    lifetime_resets_accepted: int = 0
    buffered_records: int = 0
    last_buffered_time: datetime | None = None


@dataclass
class DataloggerState:
    """Connection history of one datalogger, for availability and repairs."""

    serial: str
    seen: bool = False
    # No connection since; None while one is open.
    offline_since: datetime | None = None
    offline_logged: bool = False
    issue_raised: bool = False


@dataclass
class HubCounters:
    """Hub-level counters for diagnostics."""

    live_records: int = 0
    buffered_records: int = 0
    lifetime_decreases_ignored: int = 0
    lifetime_jumps_ignored: int = 0
    lifetime_resets_accepted: int = 0
    refused_dataloggers: int = 0
    refused_inverters: int = 0
    connections: dict[str, int] = field(default_factory=dict)


def entry_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """The entry's saved state: when each datalogger was last connected."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


def silent_issue_id(serial: str) -> str:
    return f"{ISSUE_SILENT}_{serial}"


def allow_list_issue_id(entry_id: str) -> str:
    """Raised while a Simple entry answers any datalogger."""
    return f"allow_list_empty_{entry_id}"


@callback
def async_settle_allow_list(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A Simple entry with an empty allow-list answers any datalogger: one
    saved by 0.1.0, which had no list, or a setup finished before a
    datalogger connected. If this entry knows exactly one datalogger, that
    one becomes the list. If it knows none, or several, it keeps answering
    any, and Repairs says so until the list is set in Configure → Advanced.
    """
    settings = entry_settings(entry)
    issue_id = allow_list_issue_id(entry.entry_id)
    if settings[CONF_MODE] != MODE_SIMPLE or parse_serials(
        settings[CONF_ALLOWED_SERIALS]
    ):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    serials = sorted(
        ident.removeprefix(DATALOGGER_PREFIX)
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for domain, ident in device.identifiers
        if domain == DOMAIN and ident.startswith(DATALOGGER_PREFIX)
    )
    if len(serials) == 1:
        _LOGGER.info(
            "The allow-list of %s was empty: it now holds the one datalogger "
            "this entry knows",
            entry.title,
        )
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_ALLOWED_SERIALS: serials[0]}
        )
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="allow_list_ambiguous" if serials else "allow_list_empty",
        translation_placeholders={
            "title": entry.title,
            "port": str(settings[CONF_PORT]),
            "count": str(len(serials)),
        },
        learn_more_url=SECURITY_URL,
    )


def rollback_issue_id(entry_id: str) -> str:
    """Raised when Reconfigure could neither move the port nor put it back."""
    return f"port_rollback_failed_{entry_id}"


def _plausible(
    base: float, base_at: datetime | None, value: float, now: datetime
) -> bool:
    """Could the inverter go from `base` to `value` in the time between?"""
    if value < base:
        return False
    if base_at is None:
        return True
    hours = max((now - base_at).total_seconds(), 0) / 3600
    return value - base <= MAX_PLAUSIBLE_POWER_KW * hours + LIFETIME_SLACK_KWH


class ShineHub:
    """One config entry: one listening port and the devices seen on it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        settings = entry_settings(entry)
        self.port: int = int(settings[CONF_PORT])
        self.advanced: bool = settings[CONF_MODE] == MODE_ADVANCED
        # Simple mode keeps the serial setup found, so the list applies in
        # both modes.
        self.allowed: frozenset[str] = parse_serials(settings[CONF_ALLOWED_SERIALS])
        self.upload_interval = int(
            settings[CONF_UPLOAD_INTERVAL] if self.advanced else DEFAULT_UPLOAD_INTERVAL
        )
        self.stale_after = timedelta(minutes=stale_after_minutes(self.upload_interval))
        self.inverters: dict[str, InverterState] = {}
        self.loggers: dict[str, DataloggerState] = {}
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
        self._store = entry_store(hass, entry.entry_id)
        self._last_connected: dict[str, datetime] = {}
        self._saved_at: datetime | None = None
        self._was_live: dict[str, bool] = {}
        self._names: dict[str, str] = {}

    @property
    def dataloggers(self) -> set[str]:
        """Every datalogger this entry knows, seen this run or restored."""
        return set(self.loggers)

    # ── signals ──────────────────────────────────────────────────────────
    @property
    def signal_new_inverter(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_new_inverter"

    @property
    def signal_new_datalogger(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_new_datalogger"

    @property
    def signal_removed(self) -> str:
        """A device was removed; its serial is the argument."""
        return f"{DOMAIN}_{self.entry.entry_id}_removed"

    def signal_update(self, serial: str) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_update_{serial}"

    # ── lifecycle ────────────────────────────────────────────────────────
    async def async_start(self) -> None:
        """Restore known devices and their saved totals, then listen. Raises
        OSError on a busy port.

        The saved totals are read here, before the port opens, not when the
        sensors load: a record arriving in between would otherwise be the
        first value ever seen, and pass unchecked."""
        await self._load_saved()
        self._restore_devices()
        self._restore_totals()
        await self.server.start()
        self._unsub_stale = async_track_time_interval(
            self.hass, self._check_stale, STALE_CHECK_INTERVAL
        )

    async def async_stop(self) -> None:
        """Close the port. A raised repair stays: an unload is not the
        datalogger coming back. The connection times are saved."""
        if self._unsub_stale is not None:
            self._unsub_stale()
            self._unsub_stale = None
        await self.server.stop()
        await self._store.async_save(self._to_save())

    async def _load_saved(self) -> None:
        saved = await self._store.async_load() or {}
        for serial, info in (saved.get("dataloggers") or {}).items():
            when = dt_util.parse_datetime(str(info.get("last_connected") or ""))
            if when is not None:
                self._last_connected[serial] = when

    @callback
    def _to_save(self) -> dict[str, Any]:
        """Each datalogger's last connection: now, while one is open."""
        now = dt_util.utcnow()
        self._saved_at = now
        return {
            "dataloggers": {
                serial: {
                    "last_connected": (logger.offline_since or now).isoformat()
                }
                for serial, logger in self.loggers.items()
            }
        }

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._to_save, SAVE_DELAY_SECONDS)

    @callback
    def _restore_devices(self) -> None:
        """Devices seen before a restart get their entities back at once."""
        registry = dr.async_get(self.hass)
        devices = dr.async_entries_for_config_entry(registry, self.entry.entry_id)
        by_id = {device.id: device for device in devices}
        now = dt_util.utcnow()
        for device in devices:
            for domain, ident in device.identifiers:
                if domain != DOMAIN:
                    continue
                # Keep a name this integration gave; anything else (a device
                # created without one takes the entry's title) is renamed.
                serial = ident.split("_", 1)[-1]
                for base in (DEVICE_DATALOGGER_NAME, DEVICE_INVERTER_NAME):
                    if device.name in (base, f"{base} {serial}"):
                        self._names[ident] = device.name
                if ident.startswith(DATALOGGER_PREFIX):
                    serial = ident.removeprefix(DATALOGGER_PREFIX)
                    # Offline since its last saved connection (or now, if
                    # none was saved), but a restart is not news. A notice
                    # raised before the restart is still standing.
                    issue = ir.async_get(self.hass).async_get_issue(
                        DOMAIN, silent_issue_id(serial)
                    )
                    self.loggers[serial] = DataloggerState(
                        serial,
                        offline_since=min(self._last_connected.get(serial, now), now),
                        offline_logged=True,
                        issue_raised=issue is not None and issue.active,
                    )
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

    @callback
    def _restore_totals(self) -> None:
        """Seed each restored inverter's lifetime total and reading time from
        what its sensors saved, so the first reading is checked against them."""
        entities = er.async_get(self.hass)
        saved = restore_state.async_get(self.hass).last_states
        for serial in self.inverters:
            for key in ("energy_total", "last_reading"):
                entity_id = entities.async_get_entity_id(
                    "sensor", DOMAIN, f"{serial}_{key}"
                )
                stored = saved.get(entity_id) if entity_id else None
                if stored is None or stored.extra_data is None:
                    continue
                data = SensorExtraStoredData.from_dict(stored.extra_data.as_dict())
                value = None if data is None else data.native_value
                if key == "last_reading":
                    if isinstance(value, datetime):
                        self.seed_last_reading(serial, value)
                elif isinstance(value, int | float):
                    self.seed_lifetime(serial, float(value))

    # ── device info ──────────────────────────────────────────────────────
    def _name(self, ident: str, base: str, others: int) -> str:
        """The plain name for the only device of its kind; with the serial
        when there is another, so two never share a name."""
        if ident not in self._names:
            serial = ident.split("_", 1)[1]
            self._names[ident] = f"{base} {serial}" if others else base
        return self._names[ident]

    def datalogger_device(self, serial: str) -> DeviceInfo:
        ident = f"{DATALOGGER_PREFIX}{serial}"
        others = len(self.loggers.keys() - {serial})
        return DeviceInfo(
            identifiers={(DOMAIN, ident)},
            name=self._name(ident, DEVICE_DATALOGGER_NAME, others),
            manufacturer=MANUFACTURER,
            serial_number=serial,
        )

    def inverter_device(self, serial: str) -> DeviceInfo:
        ident = f"{INVERTER_PREFIX}{serial}"
        others = len(self.inverters.keys() - {serial})
        return DeviceInfo(
            identifiers={(DOMAIN, ident)},
            name=self._name(ident, DEVICE_INVERTER_NAME, others),
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
        logger = self.loggers.get(serial)
        if connected and (logger is None or not logger.seen):
            seen = sum(1 for known in self.loggers.values() if known.seen)
            if seen >= MAX_DATALOGGERS:
                self.counters.refused_dataloggers += 1
                _LOGGER.warning(
                    "Refusing another datalogger: this entry already has %s",
                    MAX_DATALOGGERS,
                )
                return False
        count = self.counters.connections.get(serial, 0) + (1 if connected else -1)
        self.counters.connections[serial] = max(count, 0)
        if logger is None:
            logger = DataloggerState(serial)
            dr.async_get(self.hass).async_get_or_create(
                config_entry_id=self.entry.entry_id, **self.datalogger_device(serial)
            )
            self.loggers[serial] = logger
            async_dispatcher_send(self.hass, self.signal_new_datalogger, serial)
        if connected:
            # Only an offline this run logged is news; not a restart's.
            if logger.seen and logger.offline_logged:
                _LOGGER.info("Datalogger %s is back online", serial)
            logger.seen = True
            logger.offline_since = None
            logger.offline_logged = False
            self._clear_issue(serial)
        elif self.counters.connections[serial] == 0 and logger.offline_since is None:
            logger.offline_since = dt_util.utcnow()
        self._schedule_save()
        async_dispatcher_send(self.hass, self.signal_update(serial))
        for state in self.inverters.values():
            if state.datalogger == serial:
                async_dispatcher_send(self.hass, self.signal_update(state.serial))
        return True

    @callback
    def inverter_seen(self, datalogger: str, inverter: str) -> bool:
        state = self.inverters.get(inverter)
        if state is not None and state.seen and state.datalogger == datalogger:
            return True
        if state is None or not state.seen:
            seen = sum(1 for known in self.inverters.values() if known.seen)
            if seen >= MAX_INVERTERS:
                self.counters.refused_inverters += 1
                _LOGGER.warning(
                    "Ignoring another inverter: this entry already has %s",
                    MAX_INVERTERS,
                )
                return False
        new = state is None
        if state is None:
            state = self.inverters[inverter] = InverterState(inverter)
        state.seen = True
        # New, restored after a restart, or moved to another datalogger.
        state.datalogger = datalogger
        self._link_inverter(inverter, datalogger)
        if new:
            async_dispatcher_send(self.hass, self.signal_new_inverter, inverter)
        return True

    @callback
    def reading(self, reading: Reading, *, live: bool) -> None:
        state = self.inverters.get(reading.inverter_serial)
        if state is None or not state.seen:  # not accepted by inverter_seen
            return
        if not live:
            # Buffered: values from when the server was unreachable. Never
            # shown, never allowed near the lifetime total.
            state.buffered_records += 1
            state.last_buffered_time = reading.record_time
            self.counters.buffered_records += 1
            return
        now = dt_util.utcnow()
        self.counters.live_records += 1
        state.live = reading
        state.live_at = now
        self._apply_lifetime(state, reading.energy_total, now)
        self._was_live[state.serial] = True
        async_dispatcher_send(self.hass, self.signal_update(state.serial))

    # ── removal ──────────────────────────────────────────────────────────
    def device_removable(self, identifiers: set[tuple[str, str]]) -> bool:
        """A device may be removed unless it is reporting right now."""
        for domain, ident in identifiers:
            if domain != DOMAIN:
                continue
            if ident.startswith(DATALOGGER_PREFIX):
                if self.datalogger_is_connected(ident.removeprefix(DATALOGGER_PREFIX)):
                    return False
            elif ident.startswith(INVERTER_PREFIX) and self.is_live(
                ident.removeprefix(INVERTER_PREFIX)
            ):
                return False
        return True

    @callback
    def forget(self, identifiers: set[tuple[str, str]]) -> None:
        """A removed device: drop it, so it no longer counts anywhere, and is
        created afresh if it ever reports again."""
        for domain, ident in identifiers:
            if domain != DOMAIN:
                continue
            self._names.pop(ident, None)
            if ident.startswith(DATALOGGER_PREFIX):
                serial = ident.removeprefix(DATALOGGER_PREFIX)
                self._clear_issue(serial)
                self.loggers.pop(serial, None)
                self.counters.connections.pop(serial, None)
                self._schedule_save()
            elif ident.startswith(INVERTER_PREFIX):
                serial = ident.removeprefix(INVERTER_PREFIX)
                self.inverters.pop(serial, None)
                self._was_live.pop(serial, None)
            else:
                continue
            async_dispatcher_send(self.hass, self.signal_removed, serial)

    # ── rules ────────────────────────────────────────────────────────────
    def _apply_lifetime(
        self, state: InverterState, value: float, now: datetime
    ) -> None:
        if state.energy_total is None or _plausible(
            state.energy_total, state.energy_total_at, value, now
        ):
            self._accept_lifetime(state, value, now)
            return
        # Held back. Does it agree with the one held back before it?
        if state.pending_total is not None and _plausible(
            state.pending_total, state.pending_at, value, now
        ):
            state.pending_count += 1
        else:
            state.pending_count = 1
        state.pending_total = value
        state.pending_at = now
        if state.pending_count >= LIFETIME_CONFIRM_RECORDS:
            state.lifetime_resets_accepted += 1
            self.counters.lifetime_resets_accepted += 1
            _LOGGER.info(
                "Lifetime energy of inverter %s restarts from %s kWh (was %s kWh): "
                "%s records in a row agree",
                state.serial,
                value,
                state.energy_total,
                LIFETIME_CONFIRM_RECORDS,
            )
            self._accept_lifetime(state, value, now)
            return
        if value < state.energy_total:
            state.lifetime_decreases_ignored += 1
            self.counters.lifetime_decreases_ignored += 1
            what = "below"
        else:
            state.lifetime_jumps_ignored += 1
            self.counters.lifetime_jumps_ignored += 1
            what = "too far above"
        _LOGGER.debug(
            "Ignoring a lifetime energy of %s kWh, %s the last %s kWh",
            value,
            what,
            state.energy_total,
        )

    @staticmethod
    def _accept_lifetime(state: InverterState, value: float, now: datetime) -> None:
        state.energy_total = value
        state.energy_total_at = now
        state.pending_total = None
        state.pending_count = 0
        state.pending_at = None

    @callback
    def seed_lifetime(self, serial: str, value: float) -> None:
        """A restored lifetime total: what a new reading is checked against."""
        state = self.inverters.setdefault(serial, InverterState(serial))
        if state.energy_total is None:
            state.energy_total = value

    @callback
    def seed_last_reading(self, serial: str, when: datetime) -> None:
        """A restored reading time: bounds the first rise after a restart."""
        state = self.inverters.setdefault(serial, InverterState(serial))
        if state.energy_total_at is None:
            state.energy_total_at = when

    def energy_today(self, serial: str) -> float | None:
        """The inverter's own daily counter from today's newest live record;
        0 once Home Assistant's local day has moved past it; None if no live
        record arrived in this run."""
        state = self.inverters.get(serial)
        if state is None or state.live is None or state.live_at is None:
            return None
        if dt_util.as_local(state.live_at).date() != dt_util.now().date():
            return 0.0
        return state.live.energy_today

    def is_live(self, serial: str) -> bool:
        state = self.inverters.get(serial)
        if state is None or state.live_at is None:
            return False
        now = dt_util.utcnow()
        if now - state.live_at >= self.stale_after:
            return False
        logger = self.loggers.get(state.datalogger or "")
        return not (
            logger is not None
            and logger.offline_since is not None
            and now - logger.offline_since >= RECONNECT_GRACE
        )

    def datalogger_is_connected(self, serial: str) -> bool:
        return self.counters.connections.get(serial, 0) > 0

    # ── the periodic check ───────────────────────────────────────────────
    @callback
    def _check_stale(self, now: datetime) -> None:
        now = dt_util.utcnow()
        connected = any(lg.offline_since is None for lg in self.loggers.values())
        if connected and (
            self._saved_at is None or now - self._saved_at >= SAVE_CONNECTED_EVERY
        ):
            self._schedule_save()
        for logger in self.loggers.values():
            if logger.offline_since is None:
                continue
            offline_for = now - logger.offline_since
            if not logger.offline_logged and offline_for >= RECONNECT_GRACE:
                logger.offline_logged = True
                _LOGGER.info(
                    "Datalogger %s is offline: no connection for %s minutes. Its "
                    "live sensors are unavailable until it reconnects",
                    logger.serial,
                    round(offline_for.total_seconds() / 60),
                )
            if not logger.issue_raised and self._silent_too_long(
                logger.offline_since, now
            ):
                self._raise_issue(logger)
        for serial in list(self._was_live):
            if self._was_live[serial] and not self.is_live(serial):
                self._was_live[serial] = False
                async_dispatcher_send(self.hass, self.signal_update(serial))

    def _silent_too_long(self, since: datetime, now: datetime) -> bool:
        """Silent for SILENT_DAYLIGHT_HOURS of daylight, measured at Home
        Assistant's location and added up across days (a short winter day
        counts towards the next); a datalogger is off at night by design.
        SILENT_FALLBACK_HOURS of silence are always enough, which also
        covers a day where the sun does not rise or set."""
        if now - since >= timedelta(hours=SILENT_FALLBACK_HOURS):
            return True
        daylight = timedelta(0)
        day = dt_util.as_local(since).date()
        while day <= dt_util.as_local(now).date():
            sunrise = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, day)
            sunset = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, day)
            if sunrise is not None and sunset is not None:
                daylight += max(min(now, sunset) - max(since, sunrise), timedelta(0))
            day += timedelta(days=1)
        return daylight >= timedelta(hours=SILENT_DAYLIGHT_HOURS)

    @callback
    def _raise_issue(self, logger: DataloggerState) -> None:
        logger.issue_raised = True
        name = self._names.get(
            f"{DATALOGGER_PREFIX}{logger.serial}", DEVICE_DATALOGGER_NAME
        )
        _LOGGER.warning(
            "Datalogger %s has sent nothing through %s hours of daylight",
            logger.serial,
            SILENT_DAYLIGHT_HOURS,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            silent_issue_id(logger.serial),
            is_fixable=False,
            # Survives a restart, so the notice does not vanish while the
            # datalogger is still silent; cleared when data returns.
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SILENT,
            translation_placeholders={
                "name": name,
                "port": str(self.server.port),
                "hours": str(SILENT_DAYLIGHT_HOURS),
            },
            learn_more_url=TROUBLESHOOTING_URL,
        )

    @callback
    def _clear_issue(self, serial: str) -> None:
        logger = self.loggers.get(serial)
        if logger is not None:
            logger.issue_raised = False
        ir.async_delete_issue(self.hass, DOMAIN, silent_issue_id(serial))
