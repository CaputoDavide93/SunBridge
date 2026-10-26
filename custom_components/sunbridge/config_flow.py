"""Setup and options.

Setup is guided, for someone who has never seen the datalogger's web page:
welcome (Simple or Advanced), the port and Home Assistant's address, numbered
instructions for the datalogger, then a step that listens on the port until
the datalogger's first record decodes and shows its serials. Advanced adds a
last page: a datalogger serial allow-list (prefilled with the one found) and
the upload interval the datalogger is set to (on its own web page; the
integration does not change it). Simple allow-lists the datalogger found
without asking.

The entry's data is the listener's identity, its port, and nothing else:
Reconfigure changes it, after checking the new port binds, and puts the old
one back if the listener cannot move; if even that fails, the flow aborts and
Repairs names the port. Options are preferences: the mode, and the Advanced
page again.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from enum import Enum
from typing import Any

import voluptuous as vol
from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOWED_SERIALS,
    CONF_MODE,
    CONF_PORT,
    CONF_UPLOAD_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_UPLOAD_INTERVAL,
    DOMAIN,
    HANDSHAKE_TIMEOUT_SECONDS,
    IDLE_TIMEOUT_SECONDS,
    MAX_CONNECTIONS,
    MAX_UPLOAD_INTERVAL,
    MIN_UPLOAD_INTERVAL,
    MODE_ADVANCED,
    MODE_SIMPLE,
    SETUP_WAIT_SECONDS,
    parse_serials,
)
from .hub import async_settle_allow_list, entry_settings, rollback_issue_id
from .protocol import valid_serial
from .server import SunBridge

_LOGGER = logging.getLogger(__name__)

# Only shown during setup, to fill in the datalogger instructions; not stored.
CONF_ADDRESS = "address"

# One entry serves at most a couple of dataloggers; ten leaves room for a
# replaced one or two without making the list a place to paste anything.
MAX_ALLOWED_SERIALS = 10
PREFERENCES = (CONF_MODE, CONF_ALLOWED_SERIALS, CONF_UPLOAD_INTERVAL)


class _Move(Enum):
    """How a port move ended."""

    MOVED = "moved"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


def _title(port: int) -> str:
    return f"SunBridge (port {port})"


def _split(entry: ConfigEntry) -> tuple[dict[str, Any], dict[str, Any]]:
    """The entry as (data, options): the port alone, then the preferences.

    Before Reconfigure existed the options flow stored the port too, and
    options win over data, so an options port is the one in use.
    """
    settings = entry_settings(entry)
    return (
        {CONF_PORT: int(settings[CONF_PORT])},
        {key: settings[key] for key in PREFERENCES},
    )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 to 1.2: the port moves to data, the preferences to options, and a
    Simple entry (0.1.0 had no allow-list) adopts its one known datalogger
    (see `hub.async_settle_allow_list`)."""
    if entry.version > SunBridgeConfigFlow.VERSION:
        return False
    if entry.minor_version < 2:
        data, options = _split(entry)
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=SunBridgeConfigFlow.VERSION,
            minor_version=SunBridgeConfigFlow.MINOR_VERSION,
        )
        async_settle_allow_list(hass, entry)
    return True


def _port_free(port: int) -> bool:
    """True if a TCP listener could bind this port on every interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", port))
        except OSError:
            return False
    return True


async def _can_listen(hass: HomeAssistant, port: int) -> bool:
    return await hass.async_add_executor_job(_port_free, port)


def _port_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX
        )
    )


def _mode_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[MODE_SIMPLE, MODE_ADVANCED],
            translation_key="mode",
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _port_schema(port: int) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_PORT, default=port): _port_selector()})


def _advanced_schema(serials: str, interval: int) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_ALLOWED_SERIALS, description={"suggested_value": serials}
            ): selector.TextSelector(),
            vol.Required(
                CONF_UPLOAD_INTERVAL, default=interval
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_UPLOAD_INTERVAL,
                    max=MAX_UPLOAD_INTERVAL,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
        }
    )


def _allow_list_error(text: str | None) -> str | None:
    """Why an allow-list cannot be saved, or None.

    The listener's own rule (`protocol.valid_serial`) decides what a serial
    can be; anything else could never match a datalogger's frame. The
    reasons are told apart only so the form can say which one it was.
    """
    serials = [s.upper() for s in (text or "").replace(",", " ").split()]
    rejected = [s for s in serials if not valid_serial(s)]
    if any(not (s.isascii() and s.isalnum()) for s in rejected):
        return "invalid_serial"
    if rejected:
        return "serial_too_long"
    if len(set(serials)) != len(serials):
        return "duplicate_serial"
    if len(serials) > MAX_ALLOWED_SERIALS:
        return "too_many_serials"
    return None


def _check_advanced(
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Normalised advanced settings, and any errors."""
    if error := _allow_list_error(user_input.get(CONF_ALLOWED_SERIALS)):
        return {}, {CONF_ALLOWED_SERIALS: error}
    serials = parse_serials(user_input.get(CONF_ALLOWED_SERIALS))
    return {
        CONF_ALLOWED_SERIALS: ", ".join(sorted(serials)),
        CONF_UPLOAD_INTERVAL: int(user_input[CONF_UPLOAD_INTERVAL]),
    }, {}


def _port_in_use_by_entry(
    entries: list[ConfigEntry], port: int, skip: str | None = None
) -> bool:
    return any(
        entry.entry_id != skip and int(entry_settings(entry)[CONF_PORT]) == port
        for entry in entries
    )


class _FirstRecord:
    """The setup's listener: remembers the first datalogger that decodes."""

    def __init__(self) -> None:
        self.found = asyncio.Event()
        self.datalogger: str | None = None
        self.inverter: str | None = None

    def datalogger_connected(self, serial: str, connected: bool) -> bool:
        return True

    def inverter_seen(self, datalogger: str, inverter: str) -> bool:
        if self.datalogger is None:
            self.datalogger, self.inverter = datalogger, inverter
            self.found.set()
        return True

    def reading(self, reading: object, *, live: bool) -> None:
        return


async def _detected_address(hass: HomeAssistant) -> str:
    """Home Assistant's own LAN address, or "" when it cannot be told."""
    try:
        address = await async_get_source_ip(hass)
    except HomeAssistantError:
        return ""
    return address if _is_ipv4(address) else ""


def _is_ipv4(text: str) -> bool:
    try:
        ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return True


class SunBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """A guided setup: welcome, port, point the datalogger, wait for it.

    One entry per listening port. The waiting step listens on the chosen port
    for as long as it runs, answering the datalogger exactly as the entry
    will, and closes the port again before the entry is created or when the
    flow is abandoned.
    """

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._address = ""
        self._port_errors: dict[str, str] = {}
        self._listener: _FirstRecord | None = None
        self._wait_task: asyncio.Task[str] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SunBridgeOptionsFlow:
        return SunBridgeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Welcome: what SunBridge does, and Simple or Advanced."""
        if user_input is not None:
            self._data = {CONF_MODE: user_input[CONF_MODE]}
            return await self.async_step_port()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODE, default=MODE_SIMPLE): _mode_selector(),
                }
            ),
        )

    async def async_step_port(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The port to listen on, and the address the datalogger must use."""
        errors, self._port_errors = self._port_errors, {}
        if user_input is not None:
            port = int(user_input[CONF_PORT])
            address = str(user_input.get(CONF_ADDRESS, "")).strip()
            if _port_in_use_by_entry(self._async_current_entries(), port):
                return self.async_abort(
                    reason="already_configured",
                    description_placeholders={"port": str(port)},
                )
            if not _is_ipv4(address):
                errors[CONF_ADDRESS] = "invalid_address"
            elif not await _can_listen(self.hass, port):
                errors[CONF_PORT] = "port_unavailable"
            else:
                self._data[CONF_PORT] = port
                self._address = address
                return await self.async_step_point()
        if not self._address:
            self._address = await _detected_address(self.hass)
        port = int(self._data.get(CONF_PORT, DEFAULT_PORT))
        return self.async_show_form(
            step_id="port",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT, default=port): _port_selector(),
                    vol.Required(
                        CONF_ADDRESS, description={"suggested_value": self._address}
                    ): selector.TextSelector(),
                }
            ),
            description_placeholders={"address": self._address or "?"},
            errors=errors,
        )

    async def async_step_point(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Numbered instructions for the datalogger's own web page."""
        if user_input is not None:
            return await self.async_step_wait()
        return self.async_show_form(
            step_id="point",
            data_schema=vol.Schema({}),
            description_placeholders=self._placeholders(),
            last_step=False,
        )

    async def async_step_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Listen on the port until a datalogger's record decodes."""
        if self._wait_task is None:
            self._listener = _FirstRecord()
            self._wait_task = self.hass.async_create_background_task(
                self._listen(self._listener, self._data[CONF_PORT]),
                f"{DOMAIN} setup listener on port {self._data[CONF_PORT]}",
            )
        if not self._wait_task.done():
            return self.async_show_progress(
                step_id="wait",
                progress_action="wait_for_datalogger",
                description_placeholders=self._placeholders(),
                progress_task=self._wait_task,
            )
        outcome = self._wait_task.result()
        self._wait_task = None
        if outcome == "found":
            return self.async_show_progress_done(next_step_id="found")
        if outcome == "port_unavailable":
            self._port_errors = {CONF_PORT: "port_unavailable"}
            return self.async_show_progress_done(next_step_id="port")
        return self.async_show_progress_done(next_step_id="not_found")

    @staticmethod
    async def _listen(listener: _FirstRecord, port: int) -> str:
        """found, timeout or port_unavailable. The port is closed on return,
        and also when the task is cancelled (the flow was abandoned)."""
        server = SunBridge(
            listener,
            port,
            idle_timeout=IDLE_TIMEOUT_SECONDS,
            handshake_timeout=HANDSHAKE_TIMEOUT_SECONDS,
            max_connections=MAX_CONNECTIONS,
        )
        try:
            await server.start()
        except OSError:
            return "port_unavailable"
        try:
            async with asyncio.timeout(SETUP_WAIT_SECONDS):
                await listener.found.wait()
        except TimeoutError:
            return "timeout"
        finally:
            await server.stop()
        return "found"

    async def async_step_found(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """What answered: the datalogger's and the inverter's serials."""
        if user_input is not None:
            return await self._next_after_wait()
        return self.async_show_form(
            step_id="found",
            data_schema=vol.Schema({}),
            description_placeholders=self._placeholders(),
        )

    async def async_step_not_found(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nothing arrived in time: finish anyway, or listen again."""
        return self.async_show_menu(
            step_id="not_found",
            menu_options=["finish", "retry"],
            description_placeholders=self._placeholders(),
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """No datalogger yet: the port must still bind, or nothing is saved."""
        if not await _can_listen(self.hass, self._data[CONF_PORT]):
            self._port_errors = {CONF_PORT: "port_unavailable"}
            return await self.async_step_port()
        return await self._next_after_wait()

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_wait()

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow-list (prefilled with the datalogger found) and upload interval."""
        errors: dict[str, str] = {}
        if user_input is not None:
            settings, errors = _check_advanced(user_input)
            if not errors:
                self._data.update(settings)
                return self._create()
        found = self._listener.datalogger if self._listener else None
        src = user_input or {CONF_ALLOWED_SERIALS: found or ""}
        return self.async_show_form(
            step_id="advanced",
            data_schema=_advanced_schema(
                src.get(CONF_ALLOWED_SERIALS, ""),
                int(src.get(CONF_UPLOAD_INTERVAL, DEFAULT_UPLOAD_INTERVAL)),
            ),
            errors=errors,
        )

    async def _next_after_wait(self) -> ConfigFlowResult:
        if self._data[CONF_MODE] == MODE_ADVANCED:
            return await self.async_step_advanced()
        found = self._listener.datalogger if self._listener else None
        self._data[CONF_ALLOWED_SERIALS] = found or ""
        self._data[CONF_UPLOAD_INTERVAL] = DEFAULT_UPLOAD_INTERVAL
        return self._create()

    def _placeholders(self) -> dict[str, str]:
        listener = self._listener
        return {
            "address": self._address,
            "port": str(self._data.get(CONF_PORT, DEFAULT_PORT)),
            "minutes": str(SETUP_WAIT_SECONDS // 60),
            "datalogger": (listener and listener.datalogger) or "?",
            "inverter": (listener and listener.inverter) or "?",
        }

    @callback
    def async_remove(self) -> None:
        """The flow was abandoned: stop listening, so the port is free."""
        if self._wait_task is not None and not self._wait_task.done():
            self._wait_task.cancel()

    def _create(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=_title(self._data[CONF_PORT]),
            data={CONF_PORT: self._data[CONF_PORT]},
            options={key: self._data[key] for key in PREFERENCES},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A new port for the listener. Preferences stay in Configure."""
        entry = self._get_reconfigure_entry()
        current = int(entry_settings(entry)[CONF_PORT])
        errors: dict[str, str] = {}
        port = current
        if user_input is not None:
            port = int(user_input[CONF_PORT])
            entries = self._async_current_entries(include_ignore=False)
            if port == current:
                return self.async_abort(reason="reconfigure_successful")
            if _port_in_use_by_entry(entries, port, skip=entry.entry_id):
                errors[CONF_PORT] = "port_in_use_by_entry"
            # Checked while the old listener still runs: a refusal here
            # stops nothing.
            elif not await _can_listen(self.hass, port):
                errors[CONF_PORT] = "port_unavailable"
            else:
                moved = await self._move_listener(entry, current, port)
                if moved is _Move.MOVED:
                    return self.async_abort(reason="reconfigure_successful")
                if moved is _Move.FAILED:
                    return self.async_abort(
                        reason="port_rollback_failed",
                        description_placeholders={"port": str(current)},
                    )
                errors[CONF_PORT] = "port_unavailable"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_port_schema(port),
            description_placeholders={"port": str(current)},
            errors=errors,
        )

    async def _move_listener(self, entry: ConfigEntry, old: int, new: int) -> _Move:
        """Reload on the new port; if it will not bind, go back to the old.

        Either reload may fail or raise. A failed rollback leaves the old
        settings saved but nothing listening, so Repairs says which port to
        free; the next setup that loads clears it."""
        before = (dict(entry.data), dict(entry.options), entry.title)
        data, options = _split(entry)
        data[CONF_PORT] = new
        title = _title(new) if entry.title == _title(old) else entry.title
        hass = self.hass
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, title=title
        )
        if await self._reload(entry, new):
            return _Move.MOVED
        hass.config_entries.async_update_entry(
            entry, data=before[0], options=before[1], title=before[2]
        )
        if await self._reload(entry, old):
            return _Move.ROLLED_BACK
        ir.async_create_issue(
            hass,
            DOMAIN,
            rollback_issue_id(entry.entry_id),
            is_fixable=False,
            # Kept across a restart: the busy port may stop setup from ever
            # running the code that would raise it again.
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="port_rollback_failed",
            translation_placeholders={
                "title": entry.title,
                "port": str(old),
                "new_port": str(new),
            },
        )
        return _Move.FAILED

    async def _reload(self, entry: ConfigEntry, port: int) -> bool:
        """Reload the entry; True only if the reload reported success and the
        entry is loaded with a listener on `port` afterwards.

        A reload can return False with the OLD listener still loaded (its
        unload failed), so being LOADED alone does not prove the move."""
        try:
            reloaded = await self.hass.config_entries.async_reload(entry.entry_id)
        except Exception:  # any failure means "not moved"; logged below
            _LOGGER.warning(
                "Reloading SunBridge on port %s failed", port, exc_info=True
            )
            return False
        hub = getattr(entry, "runtime_data", None)
        return (
            bool(reloaded)
            and entry.state is ConfigEntryState.LOADED
            and getattr(hub, "port", None) == port
        )


class SunBridgeOptionsFlow(OptionsFlowWithReload):
    """The preferences, changed later. Saving reloads the listener.

    The port is not here: Reconfigure moves it, with a rollback.
    """

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current = entry_settings(self.config_entry)
        if user_input is not None:
            self._keep_port_in_data()
            self._options = {
                CONF_MODE: user_input[CONF_MODE],
                CONF_ALLOWED_SERIALS: current[CONF_ALLOWED_SERIALS],
                CONF_UPLOAD_INTERVAL: int(current[CONF_UPLOAD_INTERVAL]),
            }
            if user_input[CONF_MODE] == MODE_ADVANCED:
                return await self.async_step_advanced()
            return self.async_create_entry(data=self._options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODE, default=current[CONF_MODE]
                    ): _mode_selector(),
                }
            ),
        )

    @callback
    def _keep_port_in_data(self) -> None:
        """Saving replaces the options: an entry that still kept its port
        there (from before Reconfigure) moves it to data first."""
        entry = self.config_entry
        if CONF_PORT in entry.options:
            data, _ = _split(entry)
            self.hass.config_entries.async_update_entry(entry, data=data)

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            settings, errors = _check_advanced(user_input)
            if not errors:
                self._options.update(settings)
                return self.async_create_entry(data=self._options)
        src = user_input or self._options
        return self.async_show_form(
            step_id="advanced",
            data_schema=_advanced_schema(
                src.get(CONF_ALLOWED_SERIALS, ""),
                int(src.get(CONF_UPLOAD_INTERVAL, DEFAULT_UPLOAD_INTERVAL)),
            ),
            errors=errors,
        )
