"""Reconfigure (the listening port) and the migration that made it possible.

These run the entry's real listener: the point of Reconfigure is that a port
that cannot be bound leaves the working listener where it was.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunbridge.config_flow import (
    SunBridgeConfigFlow,
    _port_free,
    async_migrate_entry,
)
from custom_components.sunbridge.const import (
    CONF_ALLOWED_SERIALS,
    CONF_MODE,
    CONF_PORT,
    CONF_UPLOAD_INTERVAL,
    DOMAIN,
    MODE_ADVANCED,
    MODE_SIMPLE,
)

from .conftest import make_entry

PORT_FREE = "custom_components.sunbridge.config_flow._port_free"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def new_entry(port: int, title: str | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=title or f"SunBridge (port {port})",
        data={CONF_PORT: port},
        options={
            CONF_MODE: MODE_ADVANCED,
            CONF_ALLOWED_SERIALS: "ABC123",
            CONF_UPLOAD_INTERVAL: 2,
        },
        version=1,
        minor_version=2,
    )


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry, port: int):
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: port}
    )


async def test_a_new_port_moves_the_listener_and_the_title(
    hass: HomeAssistant, setup_entry
) -> None:
    old, new = free_port(), free_port()
    entry = await setup_entry(new_entry(old))
    options = dict(entry.options)

    result = await _reconfigure(hass, entry, new)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {CONF_PORT: new}
    assert entry.options == options  # preferences are untouched
    assert entry.title == f"SunBridge (port {new})"
    assert entry.runtime_data.server.port == new
    assert _port_free(old)
    assert not _port_free(new)


async def test_a_title_the_user_chose_is_kept(
    hass: HomeAssistant, setup_entry
) -> None:
    old, new = free_port(), free_port()
    entry = await setup_entry(new_entry(old, title="Roof"))
    result = await _reconfigure(hass, entry, new)
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Roof"


async def test_the_same_port_changes_nothing(
    hass: HomeAssistant, setup_entry
) -> None:
    port = free_port()
    entry = await setup_entry(new_entry(port))
    hub = entry.runtime_data
    result = await _reconfigure(hass, entry, port)
    assert result["reason"] == "reconfigure_successful"
    assert entry.runtime_data is hub  # not reloaded


async def test_a_port_another_entry_uses_is_refused(
    hass: HomeAssistant, setup_entry
) -> None:
    port, theirs = free_port(), free_port()
    other = new_entry(theirs)
    other.add_to_hass(hass)
    entry = await setup_entry(new_entry(port))
    result = await _reconfigure(hass, entry, theirs)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PORT: "port_in_use_by_entry"}
    assert entry.data == {CONF_PORT: port}


async def test_a_port_that_is_taken_is_refused_before_anything_stops(
    hass: HomeAssistant, setup_entry
) -> None:
    port = free_port()
    entry = await setup_entry(new_entry(port))
    hub = entry.runtime_data
    with socket.socket() as busy:
        busy.bind(("", 0))
        busy.listen()
        result = await _reconfigure(hass, entry, busy.getsockname()[1])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PORT: "port_unavailable"}
    assert entry.runtime_data is hub  # the working listener never stopped
    assert entry.data == {CONF_PORT: port}
    assert not _port_free(port)


async def test_a_bind_that_fails_after_the_check_rolls_back(
    hass: HomeAssistant, setup_entry
) -> None:
    """The port was free when checked and taken by the time the listener
    moved: the entry goes back to its old port, title and all."""
    port = free_port()
    entry = await setup_entry(new_entry(port))
    title = entry.title
    with socket.socket() as busy:
        busy.bind(("", 0))
        busy.listen()
        with patch(PORT_FREE, return_value=True):
            result = await _reconfigure(hass, entry, busy.getsockname()[1])
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PORT: "port_unavailable"}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {CONF_PORT: port}
    assert entry.title == title
    assert entry.runtime_data.server.port == port
    assert not _port_free(port)


async def test_reconfigure_moves_a_port_left_in_the_options(
    hass: HomeAssistant, setup_entry
) -> None:
    """An entry from before Reconfigure kept its port in the options, which
    win over the data: a new port must not be shadowed by the old one."""
    old, new = free_port(), free_port()
    entry = make_entry(port=5279)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_PORT: old})
    entry = await setup_entry(entry)
    result = await _reconfigure(hass, entry, new)
    assert result["reason"] == "reconfigure_successful"
    assert CONF_PORT not in entry.options
    assert entry.runtime_data.server.port == new


async def test_version_1_1_entries_are_migrated(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="SunBridge (port 5279)",
        data={CONF_PORT: 5279, CONF_MODE: MODE_SIMPLE},
        options={
            CONF_PORT: 6000,
            CONF_MODE: MODE_ADVANCED,
            CONF_ALLOWED_SERIALS: "ABC123",
            CONF_UPLOAD_INTERVAL: 2,
        },
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.data == {CONF_PORT: 6000}
    assert entry.options == {
        CONF_MODE: MODE_ADVANCED,
        CONF_ALLOWED_SERIALS: "ABC123",
        CONF_UPLOAD_INTERVAL: 2,
    }
    assert (entry.version, entry.minor_version) == (
        SunBridgeConfigFlow.VERSION,
        SunBridgeConfigFlow.MINOR_VERSION,
    )


def _old_simple_entry() -> MockConfigEntry:
    """A Simple entry as 0.1.0 saved it: no allow-list at all."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="SunBridge (port 5279)",
        data={CONF_PORT: 5279, CONF_MODE: MODE_SIMPLE},
        version=1,
        minor_version=1,
    )


def _add_datalogger(hass: HomeAssistant, entry: MockConfigEntry, serial: str) -> None:
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"datalogger_{serial}")},
        name="Solar Datalogger",
    )


def _allow_list_issue(hass: HomeAssistant, entry: MockConfigEntry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"allow_list_empty_{entry.entry_id}")


async def test_a_migrated_simple_entry_adopts_its_one_datalogger(
    hass: HomeAssistant,
) -> None:
    """0.1.0 answered any datalogger. The one this entry has already seen is
    the one it meant: it becomes the allow-list, and Repairs stays quiet."""
    entry = _old_simple_entry()
    entry.add_to_hass(hass)
    _add_datalogger(hass, entry, "ABC123")
    assert await async_migrate_entry(hass, entry)
    assert entry.data == {CONF_PORT: 5279}
    assert entry.options == {
        CONF_MODE: MODE_SIMPLE,
        CONF_ALLOWED_SERIALS: "ABC123",
        CONF_UPLOAD_INTERVAL: 5,
    }
    assert _allow_list_issue(hass, entry) is None


async def test_a_migrated_entry_with_no_datalogger_accepts_any_and_says_so(
    hass: HomeAssistant,
) -> None:
    entry = _old_simple_entry()
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.data == {CONF_PORT: 5279}
    assert entry.options == {
        CONF_MODE: MODE_SIMPLE,
        CONF_ALLOWED_SERIALS: "",
        CONF_UPLOAD_INTERVAL: 5,
    }
    issue = _allow_list_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "allow_list_empty"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert not issue.is_fixable
    assert issue.translation_placeholders["port"] == "5279"


async def test_a_migrated_entry_with_two_dataloggers_asks_which(
    hass: HomeAssistant,
) -> None:
    """Two dataloggers: which one is the user's is not ours to guess."""
    entry = _old_simple_entry()
    entry.add_to_hass(hass)
    _add_datalogger(hass, entry, "ABC123")
    _add_datalogger(hass, entry, "XYZ789")
    assert await async_migrate_entry(hass, entry)
    assert entry.options[CONF_ALLOWED_SERIALS] == ""
    issue = _allow_list_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "allow_list_ambiguous"


async def test_setup_adopts_the_one_datalogger_of_an_empty_simple_list(
    hass: HomeAssistant, setup_entry
) -> None:
    """A Simple entry whose setup finished before a datalogger connected:
    once one has, the next setup adopts it, and the listener uses it."""
    entry = make_entry(allowed_serials="")
    entry.add_to_hass(hass)
    _add_datalogger(hass, entry, "ABC123")
    entry.add_to_hass = lambda hass: None
    await setup_entry(entry)
    assert entry.options[CONF_ALLOWED_SERIALS] == "ABC123"
    assert entry.runtime_data.allowed == frozenset({"ABC123"})
    assert _allow_list_issue(hass, entry) is None


async def test_setting_an_allow_list_clears_the_notice(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(make_entry(allowed_serials=""))
    assert _allow_list_issue(hass, entry) is not None
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_MODE: MODE_ADVANCED,
            CONF_ALLOWED_SERIALS: "ABC123",
            CONF_UPLOAD_INTERVAL: 5,
        },
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert _allow_list_issue(hass, entry) is None


async def test_an_advanced_empty_list_is_a_choice(
    hass: HomeAssistant, setup_entry
) -> None:
    """Advanced with an empty list was chosen on its form: no notice."""
    entry = await setup_entry(make_entry(MODE_ADVANCED))
    assert _allow_list_issue(hass, entry) is None


async def test_removing_the_entry_clears_the_notice(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(make_entry(allowed_serials=""))
    assert _allow_list_issue(hass, entry) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    assert _allow_list_issue(hass, entry) is None


async def test_a_newer_entry_is_not_downgraded(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_PORT: 5279}, version=2)
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)


# ── a move whose reloads fail ───────────────────────────────────────────
RELOAD = "homeassistant.config_entries.ConfigEntries.async_reload"


def _reloads(*steps):
    """A stand-in for async_reload that runs one step per call: an exception
    is raised, anything else is awaited with the entry id."""
    calls = iter(steps)

    async def reload(entry_id: str) -> bool:
        step = next(calls)
        if isinstance(step, Exception):
            raise step
        return await step(entry_id)

    return reload


def _rollback_issue(hass: HomeAssistant, entry: MockConfigEntry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"port_rollback_failed_{entry.entry_id}")


async def test_a_move_that_raises_rolls_back(
    hass: HomeAssistant, setup_entry
) -> None:
    """The reload onto the new port raises: the old port comes back."""
    port, new = free_port(), free_port()
    entry = await setup_entry(new_entry(port))
    real = hass.config_entries.async_reload
    with patch(RELOAD, side_effect=_reloads(RuntimeError("boom"), real)):
        result = await _reconfigure(hass, entry, new)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PORT: "port_unavailable"}
    assert entry.data == {CONF_PORT: port}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.server.port == port
    assert _rollback_issue(hass, entry) is None


async def test_a_rollback_that_raises_aborts_and_raises_a_repair(
    hass: HomeAssistant, setup_entry
) -> None:
    port, new = free_port(), free_port()
    entry = await setup_entry(new_entry(port))
    with patch(
        RELOAD, side_effect=_reloads(RuntimeError("boom"), RuntimeError("again"))
    ):
        result = await _reconfigure(hass, entry, new)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "port_rollback_failed"
    assert result["description_placeholders"] == {"port": str(port)}
    assert entry.data == {CONF_PORT: port}
    issue = _rollback_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "port_rollback_failed"
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders["port"] == str(port)
    assert issue.translation_placeholders["new_port"] == str(new)


async def test_a_rollback_that_does_not_load_aborts_and_the_repair_clears_on_setup(
    hass: HomeAssistant, setup_entry
) -> None:
    """The new port is taken after the check, and the reload back to the
    old one leaves the entry unloaded: the flow says so and Repairs names the
    port; the next setup that works clears it."""
    port = free_port()
    entry = await setup_entry(new_entry(port))
    real = hass.config_entries.async_reload

    async def not_loaded(entry_id: str) -> bool:
        await hass.config_entries.async_unload(entry_id)
        return False

    with socket.socket() as busy:
        busy.bind(("", 0))
        busy.listen()
        with (
            patch(PORT_FREE, return_value=True),
            patch(RELOAD, side_effect=_reloads(real, not_loaded)),
        ):
            result = await _reconfigure(hass, entry, busy.getsockname()[1])
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "port_rollback_failed"
    assert entry.state is not ConfigEntryState.LOADED
    assert _rollback_issue(hass, entry) is not None

    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert _rollback_issue(hass, entry) is None


async def test_a_reload_that_fails_while_the_old_listener_stays_loaded_is_not_a_move(
    hass: HomeAssistant, setup_entry
) -> None:
    """async_reload can return False with the old listener still loaded (its
    unload failed). That must never be reported as a moved port."""
    port = free_port()
    entry = await setup_entry(new_entry(port))
    real = hass.config_entries.async_reload

    async def unload_failed(entry_id: str) -> bool:
        return False  # nothing unloaded: the old hub keeps running

    new = free_port()
    with (
        patch(PORT_FREE, return_value=True),
        patch(RELOAD, side_effect=_reloads(unload_failed, real)),
    ):
        result = await _reconfigure(hass, entry, new)
    await hass.async_block_till_done()
    assert not (result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful")
    assert entry.data[CONF_PORT] == port
    assert entry.runtime_data.port == port


async def test_the_rollback_repair_survives_a_restart(hass: HomeAssistant, setup_entry) -> None:
    """The rollback notice is persistent: a busy port may keep setup from
    ever raising it again."""
    port = free_port()
    entry = await setup_entry(new_entry(port))
    real = hass.config_entries.async_reload

    async def not_loaded(entry_id: str) -> bool:
        await hass.config_entries.async_unload(entry_id)
        return False

    with socket.socket() as busy:
        busy.bind(("", 0))
        busy.listen()
        with (
            patch(PORT_FREE, return_value=True),
            patch(RELOAD, side_effect=_reloads(real, not_loaded)),
        ):
            await _reconfigure(hass, entry, busy.getsockname()[1])
    await hass.async_block_till_done()
    issue = _rollback_issue(hass, entry)
    assert issue is not None and issue.is_persistent
