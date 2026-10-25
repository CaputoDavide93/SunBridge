"""Setup and options.

Setup is guided, for someone who has never seen the datalogger's web page:
welcome (Simple or Advanced), the port and Home Assistant's address, numbered
instructions for the datalogger, then a step that listens on the port until
the datalogger's first record decodes and shows its serials. Advanced adds a
last page: a datalogger serial allow-list (prefilled with the one found) and
the upload interval the datalogger is set to (on its own web page; the
integration does not change it).

Options are the port and the mode, and the Advanced page again.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any

import voluptuous as vol
from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
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
from .hub import entry_settings
from .server import SunBridge

# Only shown during setup, to fill in the datalogger instructions; not stored.
CONF_ADDRESS = "address"

SERIAL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


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


def _main_schema(port: int, mode: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PORT, default=port): _port_selector(),
            vol.Required(CONF_MODE, default=mode): _mode_selector(),
        }
    )


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


def _check_advanced(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict]:
    """Normalised advanced settings, and any errors."""
    serials = parse_serials(user_input.get(CONF_ALLOWED_SERIALS))
    if any(not set(s) <= SERIAL_CHARS for s in serials):
        return {}, {CONF_ALLOWED_SERIALS: "invalid_serial"}
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
            title=f"SunBridge (port {self._data[CONF_PORT]})", data=self._data
        )


class SunBridgeOptionsFlow(OptionsFlowWithReload):
    """The same settings, changed later. Saving reloads the listener."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = entry_settings(self.config_entry)
        if user_input is not None:
            port = int(user_input[CONF_PORT])
            mode = user_input[CONF_MODE]
            entries = self.hass.config_entries.async_entries(DOMAIN)
            if _port_in_use_by_entry(entries, port, skip=self.config_entry.entry_id):
                errors[CONF_PORT] = "port_in_use_by_entry"
            # Our own listener holds the current port; only a new one is tested.
            elif port != int(current[CONF_PORT]) and not await _can_listen(
                self.hass, port
            ):
                errors[CONF_PORT] = "port_unavailable"
            else:
                self._options = {
                    CONF_PORT: port,
                    CONF_MODE: mode,
                    CONF_ALLOWED_SERIALS: current[CONF_ALLOWED_SERIALS],
                    CONF_UPLOAD_INTERVAL: int(current[CONF_UPLOAD_INTERVAL]),
                }
                if mode == MODE_ADVANCED:
                    return await self.async_step_advanced()
                return self.async_create_entry(data=self._options)
        src = user_input or {}
        return self.async_show_form(
            step_id="init",
            data_schema=_main_schema(
                int(src.get(CONF_PORT, current[CONF_PORT])),
                src.get(CONF_MODE, current[CONF_MODE]),
            ),
            errors=errors,
        )

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
