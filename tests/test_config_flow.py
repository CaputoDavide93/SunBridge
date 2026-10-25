"""Setup and options.

Setup is walked end to end: welcome, port, the datalogger instructions, and
the waiting step listening on a real localhost port for a fake datalogger
sending captured records.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

from custom_components.sunbridge.config_flow import CONF_ADDRESS, _port_free
from custom_components.sunbridge.const import (
    CONF_ALLOWED_SERIALS,
    CONF_MODE,
    CONF_PORT,
    CONF_UPLOAD_INTERVAL,
    DOMAIN,
    MODE_ADVANCED,
    MODE_SIMPLE,
)
from custom_components.sunbridge.protocol import TYPE_ANNOUNCE, TYPE_PING

from .captures import DATALOGGER, INVERTER, of_type
from .conftest import FakeDatalogger, make_entry

PORT_FREE = "custom_components.sunbridge.config_flow._port_free"
SOURCE_IP = "custom_components.sunbridge.config_flow.async_get_source_ip"
WAIT = "custom_components.sunbridge.config_flow.SETUP_WAIT_SECONDS"
ADDRESS = "192.0.2.10"  # a documentation address (RFC 5737)

ANNOUNCE = of_type(TYPE_ANNOUNCE)[0].raw
PING = of_type(TYPE_PING)[0].raw


@pytest.fixture(autouse=True)
def no_setup():
    """The flow is under test, not the entry's own listener."""
    with patch(
        "custom_components.sunbridge.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture(autouse=True)
def detected_address():
    """Home Assistant's own address, as the network helper reports it."""
    with patch(SOURCE_IP, return_value=ADDRESS) as mock:
        yield mock


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _to_port(hass: HomeAssistant, mode: str = MODE_SIMPLE):
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODE: mode}
    )
    assert result["step_id"] == "port"
    return result


async def _to_wait(hass: HomeAssistant, port: int, mode: str = MODE_SIMPLE):
    """Welcome, port and instructions: the flow is now listening on `port`."""
    result = await _to_port(hass, mode)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: port, CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "point"
    assert result["description_placeholders"]["address"] == ADDRESS
    assert result["description_placeholders"]["port"] == str(port)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "wait"
    assert result["progress_action"] == "wait_for_datalogger"
    return result


async def _listening(port: int) -> bool:
    """True once the waiting step has bound the port (it starts in the
    background, a moment after the progress page is shown)."""
    for _ in range(200):
        if not _port_free(port):
            return True
        await asyncio.sleep(0.01)
    return False


async def _datalogger_announces(hass: HomeAssistant, port: int) -> None:
    """A ping (which identifies nothing), then the captured announce."""
    assert await _listening(port)
    logger = await FakeDatalogger.connect(port)
    await logger.send(PING)
    assert (await logger.replies(1))[0].raw == PING
    await logger.send(ANNOUNCE)
    await logger.replies(1)  # the ack
    await hass.async_block_till_done(wait_background_tasks=True)
    await logger.close()


async def _progress_done(hass: HomeAssistant, flow_id: str):
    await hass.async_block_till_done(wait_background_tasks=True)
    return await hass.config_entries.flow.async_configure(flow_id)


async def test_the_port_page_shows_the_detected_address(hass: HomeAssistant) -> None:
    result = await _to_port(hass)
    assert result["description_placeholders"] == {"address": ADDRESS}
    schema = result["data_schema"].schema
    port_key = next(k for k in schema if k == CONF_PORT)
    address_key = next(k for k in schema if k == CONF_ADDRESS)
    assert port_key.default() == 5279
    assert address_key.description == {"suggested_value": ADDRESS}


async def test_an_undetected_address_must_be_typed(
    hass: HomeAssistant, detected_address
) -> None:
    detected_address.side_effect = HomeAssistantError("no IPv4")
    result = await _to_port(hass)
    assert result["description_placeholders"] == {"address": "?"}
    with patch(PORT_FREE, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PORT: 5279, CONF_ADDRESS: "homeassistant"}
        )
    assert result["step_id"] == "port"
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


async def test_simple_setup_waits_for_the_datalogger(
    hass: HomeAssistant, socket_enabled
) -> None:
    port = free_port()
    result = await _to_wait(hass, port)
    flow_id = result["flow_id"]
    await _datalogger_announces(hass, port)
    result = await _progress_done(hass, flow_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "found"
    assert result["description_placeholders"]["datalogger"] == DATALOGGER
    assert result["description_placeholders"]["inverter"] == INVERTER
    # The waiting step has closed the port again, before the entry exists.
    assert _port_free(port)
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PORT: port, CONF_MODE: MODE_SIMPLE}
    assert result["title"] == f"SunBridge (port {port})"


async def test_advanced_setup_prefills_the_found_datalogger(
    hass: HomeAssistant, socket_enabled
) -> None:
    port = free_port()
    result = await _to_wait(hass, port, MODE_ADVANCED)
    flow_id = result["flow_id"]
    await _datalogger_announces(hass, port)
    result = await _progress_done(hass, flow_id)
    assert result["step_id"] == "found"
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "advanced"
    allow_list = next(
        k for k in result["data_schema"].schema if k == CONF_ALLOWED_SERIALS
    )
    assert allow_list.description == {"suggested_value": DATALOGGER}
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_ALLOWED_SERIALS: f"{DATALOGGER.lower()}, ABC123", CONF_UPLOAD_INTERVAL: 2},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_PORT: port,
        CONF_MODE: MODE_ADVANCED,
        CONF_ALLOWED_SERIALS: f"ABC123, {DATALOGGER}",
        CONF_UPLOAD_INTERVAL: 2,
    }


async def test_nothing_arrives_then_try_again_then_finish(
    hass: HomeAssistant, socket_enabled
) -> None:
    port = free_port()
    with patch(WAIT, 0.05):
        result = await _to_wait(hass, port)
        flow_id = result["flow_id"]
        result = await _progress_done(hass, flow_id)
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "not_found"
        assert result["menu_options"] == ["finish", "retry"]
        assert _port_free(port)

        result = await hass.config_entries.flow.async_configure(
            flow_id, {"next_step_id": "retry"}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        result = await _progress_done(hass, flow_id)
        assert result["step_id"] == "not_found"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PORT: port, CONF_MODE: MODE_SIMPLE}


async def test_finishing_advanced_without_a_datalogger_leaves_the_list_empty(
    hass: HomeAssistant, socket_enabled
) -> None:
    port = free_port()
    with patch(WAIT, 0.05):
        result = await _to_wait(hass, port, MODE_ADVANCED)
        result = await _progress_done(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["step_id"] == "advanced"
    allow_list = next(
        k for k in result["data_schema"].schema if k == CONF_ALLOWED_SERIALS
    )
    assert allow_list.description == {"suggested_value": ""}


async def test_abandoning_the_wait_frees_the_port(
    hass: HomeAssistant, socket_enabled
) -> None:
    port = free_port()
    result = await _to_wait(hass, port)
    assert await _listening(port)
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _port_free(port)


async def test_a_port_taken_while_waiting_goes_back_to_the_port_page(
    hass: HomeAssistant, socket_enabled
) -> None:
    with socket.socket() as busy:
        busy.bind(("", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with patch(PORT_FREE, return_value=True):
            result = await _to_wait(hass, port)
            result = await _progress_done(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "port"
    assert result["errors"] == {CONF_PORT: "port_unavailable"}


async def test_a_port_that_cannot_be_bound(hass: HomeAssistant) -> None:
    result = await _to_port(hass)
    with patch(PORT_FREE, return_value=False):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PORT: 5279, CONF_ADDRESS: ADDRESS}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PORT: "port_unavailable"}


async def test_a_port_another_entry_listens_on_is_explained(
    hass: HomeAssistant,
) -> None:
    make_entry(port=5279).add_to_hass(hass)
    result = await _to_port(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: 5279, CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert result["description_placeholders"] == {"port": "5279"}


async def test_an_invalid_serial_is_refused(
    hass: HomeAssistant, socket_enabled
) -> None:
    port = free_port()
    with patch(WAIT, 0.05):
        result = await _to_wait(hass, port, MODE_ADVANCED)
        result = await _progress_done(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ALLOWED_SERIALS: "ABCD-1234", CONF_UPLOAD_INTERVAL: 5}
    )
    assert result["errors"] == {CONF_ALLOWED_SERIALS: "invalid_serial"}


async def test_the_bind_check_is_real(hass: HomeAssistant, socket_enabled) -> None:
    """A port another socket listens on is reported as unavailable."""
    with socket.socket() as busy:
        busy.bind(("", 0))
        busy.listen()
        assert not _port_free(busy.getsockname()[1])


async def test_options_simple_to_advanced(hass: HomeAssistant) -> None:
    entry = make_entry(port=5279)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    # The entry's own port is not re-tested: its listener holds it.
    with patch(PORT_FREE, return_value=False):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PORT: 5279, CONF_MODE: MODE_ADVANCED}
        )
    assert result["step_id"] == "advanced"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ALLOWED_SERIALS: "", CONF_UPLOAD_INTERVAL: 10}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_PORT: 5279,
        CONF_MODE: MODE_ADVANCED,
        CONF_ALLOWED_SERIALS: "",
        CONF_UPLOAD_INTERVAL: 10,
    }


async def test_options_new_port_is_checked(hass: HomeAssistant) -> None:
    entry = make_entry(port=5279)
    entry.add_to_hass(hass)
    other = make_entry(port=6000)
    other.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PORT: 6000, CONF_MODE: MODE_SIMPLE}
    )
    assert result["errors"] == {CONF_PORT: "port_in_use_by_entry"}
    with patch(PORT_FREE, return_value=False):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PORT: 6001, CONF_MODE: MODE_SIMPLE}
        )
    assert result["errors"] == {CONF_PORT: "port_unavailable"}
    with patch(PORT_FREE, return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PORT: 6001, CONF_MODE: MODE_SIMPLE}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PORT] == 6001
