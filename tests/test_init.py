"""Setup, unload and diagnostics."""

from __future__ import annotations

import asyncio
import json

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.sunbridge.protocol import (
    TYPE_ANNOUNCE,
    TYPE_BUFFERED,
    TYPE_DATA,
)

from .captures import DATALOGGER, INVERTER, of_type
from .conftest import FakeDatalogger, make_entry


async def test_unload_closes_the_port(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    port = entry.runtime_data.server.port
    logger = await FakeDatalogger.connect(port)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert await logger.closed_by_server()
    await logger.close()
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
    except ConnectionRefusedError:
        return
    writer.close()
    raise AssertionError("port still accepting after unload")


async def test_diagnostics_name_no_serial(
    hass: HomeAssistant, hass_client, setup_entry
) -> None:
    entry = await setup_entry(make_entry("advanced", allowed_serials=DATALOGGER))
    logger = await FakeDatalogger.connect(entry.runtime_data.server.port)
    for record, replies in (
        (of_type(TYPE_ANNOUNCE)[0], 2),
        (of_type(TYPE_DATA)[1], 1),
        (of_type(TYPE_BUFFERED)[0], 1),
    ):
        await logger.send(record.raw)
        await logger.replies(replies)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = json.dumps(diagnostics)
    for serial in (DATALOGGER, INVERTER):
        assert serial not in text
        assert serial.encode().hex() not in text
    records = diagnostics["records"]
    assert records["by_type"] == {"0x03": 1, "0x04": 1, "0x50": 1}
    assert records["live_applied"] == 1
    assert records["buffered_ignored"] == 1
    assert records["crc_failures"] == 0
    assert set(records["last_frame_by_type"]) == {"0x03", "0x04", "0x50"}
    assert diagnostics["listener"]["connections_open"] == 1
    assert diagnostics["entry"]["settings"]["allowed_serials"] == "1 serial(s)"
    assert diagnostics["inverters"][0]["energy_total"] == 2312.1
    await logger.close()


async def test_a_failed_platform_setup_closes_the_port(
    hass: HomeAssistant, socket_enabled
) -> None:
    import socket
    from unittest.mock import patch

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    entry = make_entry(port=port)
    entry.add_to_hass(hass)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform setup failed"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
    except ConnectionRefusedError:
        return
    writer.close()
    raise AssertionError("the listener stayed bound after a failed setup")


async def test_diagnostics_hold_no_serial_from_an_unknown_record_type(
    hass: HomeAssistant, hass_client, setup_entry
) -> None:
    """A frame of a type we do not know, carrying both serials, leaks neither."""
    from custom_components.sunbridge.protocol import TYPE_PING, TYPE_SET, build_frame

    entry = await setup_entry()
    logger = await FakeDatalogger.connect(entry.runtime_data.server.port)
    serials = DATALOGGER.encode().ljust(30, b"\x00") + INVERTER.encode().ljust(30, b"\x00")
    for record_type in (0x77, TYPE_SET):
        await logger.send(build_frame(1, record_type, serials + b"\x01\x02" + INVERTER.encode()))
    await logger.send(of_type(TYPE_PING)[0].raw)
    await logger.replies(1)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = json.dumps(diagnostics)
    for serial in (DATALOGGER, INVERTER):
        assert serial not in text
        assert serial.encode().hex() not in text
    assert diagnostics["records"]["by_type"]["0x77"] == 1
    await logger.close()
