"""End to end: a fake datalogger on a real localhost socket, captured records,
and the entity states Home Assistant ends up with."""

from __future__ import annotations

import asyncio
import contextlib
import random
from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.sunbridge.const import DOMAIN, MODE_ADVANCED
from custom_components.sunbridge.decoder import REG_ENERGY_TOTAL
from custom_components.sunbridge.protocol import (
    TYPE_ANNOUNCE,
    TYPE_BUFFERED,
    TYPE_DATA,
    TYPE_PING,
    TYPE_SET,
    build_ack,
    build_frame,
    parse_frame,
)

from .captures import DATALOGGER, INVERTER, of_type
from .conftest import FakeDatalogger, make_entry

POWER = "sensor.solar_inverter_power"
TODAY = "sensor.solar_inverter_energy_today"
TOTAL = "sensor.solar_inverter_energy_total"
PV_VOLTAGE = "sensor.solar_inverter_pv_voltage"
TEMPERATURE = "sensor.solar_inverter_temperature"
STATUS = "sensor.solar_inverter_status"
LAST_READING = "sensor.solar_inverter_last_reading"
CONNECTED = "binary_sensor.solar_datalogger_connected"

ANNOUNCE = of_type(TYPE_ANNOUNCE)[0].raw
PING = of_type(TYPE_PING)[0].raw
LIVE = [r.raw for r in of_type(TYPE_DATA)]
BUFFERED = [r.raw for r in of_type(TYPE_BUFFERED)]
LIVE_1220 = LIVE[1]  # 12:20:00: 272.9 W, 2.5 kWh today, 2312.1 kWh total


def with_energy_total(raw: bytes, tenths: int) -> bytes:
    """A captured record with its lifetime energy rewritten, re-framed."""
    frame = parse_frame(raw)
    payload = bytearray(frame.payload)
    pos = 71 + 2 * (REG_ENERGY_TOTAL - 3000)
    payload[pos : pos + 4] = tenths.to_bytes(4, "big")
    return build_frame(frame.transaction, frame.record_type, bytes(payload))


async def _client(hass: HomeAssistant, entry) -> FakeDatalogger:
    return await FakeDatalogger.connect(entry.runtime_data.server.port)


async def _send(hass: HomeAssistant, logger: FakeDatalogger, raw: bytes, replies=1):
    await logger.send(raw)
    got = await logger.replies(replies)
    await hass.async_block_till_done()
    return got


async def test_announce_is_acked_then_the_clock_is_set(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    ack, clock = await _send(hass, logger, ANNOUNCE, replies=2)
    assert ack.raw == build_ack(parse_frame(ANNOUNCE))
    assert clock.record_type == TYPE_SET
    assert clock.datalogger_serial == DATALOGGER
    assert clock.payload[30:34] == bytes.fromhex("001f0013")
    assert len(clock.payload[34:].decode()) == 19
    await logger.close()


async def test_live_record_sets_the_states(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    (ack,) = await _send(hass, logger, LIVE_1220)
    assert ack.raw == build_ack(parse_frame(LIVE_1220))

    assert hass.states.get(POWER).state == "272.9"
    assert hass.states.get(TODAY).state == "2.5"
    assert hass.states.get(TOTAL).state == "2312.1"
    assert hass.states.get(PV_VOLTAGE).state == "192.4"
    assert hass.states.get(TEMPERATURE).state == "33.0"
    assert hass.states.get(STATUS).state == "producing"
    assert hass.states.get(LAST_READING).state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert hass.states.get(CONNECTED).state == STATE_ON
    await logger.close()


async def test_entity_ids_and_classes_the_dashboard_relies_on(
    hass: HomeAssistant, setup_entry
) -> None:
    """The power entity id contains "solar" or "inverter" and is a power
    sensor; energy today sits next to it; the lifetime energy is the Energy
    dashboard source."""
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    power = hass.states.get(POWER)
    assert power.attributes["device_class"] == "power"
    assert power.attributes["state_class"] == "measurement"
    for entity_id in (TODAY, TOTAL):
        state = hass.states.get(entity_id)
        assert state.attributes["device_class"] == "energy"
        assert state.attributes["state_class"] == "total_increasing"
        assert state.attributes["unit_of_measurement"] == "kWh"

    registry = er.async_get(hass)
    assert registry.async_get(POWER).unique_id == f"{INVERTER}_power"
    assert registry.async_get(CONNECTED).unique_id == f"{DATALOGGER}_connected"
    devices = dr.async_get(hass)
    inverter = devices.async_get_device_by_identifier(
        (DOMAIN, f"inverter_{INVERTER}"), entry.entry_id
    )
    datalogger = devices.async_get_device_by_identifier(
        (DOMAIN, f"datalogger_{DATALOGGER}"), entry.entry_id
    )
    assert inverter.name == "Solar Inverter"
    assert inverter.via_device_id == datalogger.id
    await logger.close()


async def test_simple_mode_has_no_extra_sensors(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    assert hass.states.get("sensor.solar_inverter_grid_frequency") is None
    await logger.close()


async def test_advanced_mode_adds_the_extra_sensors(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(make_entry(MODE_ADVANCED))
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    assert hass.states.get("sensor.solar_inverter_grid_frequency").state == "50.04"
    assert hass.states.get("sensor.solar_inverter_grid_voltage").state == "245.5"
    assert hass.states.get("sensor.solar_inverter_pv_current").state == "1.3"
    assert hass.states.get("sensor.solar_inverter_pv_power").state == "280.6"
    assert float(hass.states.get("sensor.solar_inverter_working_time").state) > 5000
    # Unverified, so off until enabled.
    registry = er.async_get(hass)
    second = registry.async_get("sensor.solar_inverter_second_temperature")
    assert second.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    await logger.close()


async def test_back_to_simple_removes_the_extra_sensors(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(make_entry(MODE_ADVANCED))
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    await logger.close()
    registry = er.async_get(hass)
    assert registry.async_get("sensor.solar_inverter_grid_frequency") is not None

    hass.config_entries.async_update_entry(entry, options={"mode": "simple"})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("sensor.solar_inverter_grid_frequency") is None
    assert registry.async_get(POWER) is not None


async def test_buffered_records_never_change_the_states(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    before = {e: hass.states.get(e).state for e in (POWER, TODAY, TOTAL, PV_VOLTAGE)}
    for raw in BUFFERED:
        (ack,) = await _send(hass, logger, raw)
        assert ack.raw == build_ack(parse_frame(raw))
    after = {e: hass.states.get(e).state for e in before}
    assert after == before
    assert entry.runtime_data.counters.buffered_records == len(BUFFERED)
    await logger.close()


async def test_buffered_only_leaves_live_sensors_unavailable(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, BUFFERED[0])
    assert hass.states.get(POWER).state == STATE_UNAVAILABLE
    assert hass.states.get(TOTAL).state == STATE_UNKNOWN
    await logger.close()


async def test_lifetime_energy_never_goes_down(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE[-1])
    assert hass.states.get(TOTAL).state == "2312.6"
    lower = with_energy_total(LIVE_1220, 23000)
    await _send(hass, logger, lower)
    assert hass.states.get(TOTAL).state == "2312.6"
    assert hass.states.get(POWER).state == "272.9"  # the rest still updates
    assert entry.runtime_data.counters.lifetime_decreases_ignored == 1
    await _send(hass, logger, with_energy_total(LIVE_1220, 23127))
    assert hass.states.get(TOTAL).state == "2312.7"
    await logger.close()


async def test_a_restored_lifetime_is_the_floor(hass: HomeAssistant, setup_entry) -> None:
    """After a restart the restored total still blocks a lower reading."""
    registry = er.async_get(hass)
    entry = make_entry()
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"inverter_{INVERTER}")},
    )
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{INVERTER}_energy_total",
        suggested_object_id="solar_inverter_energy_total",
        config_entry=entry, device_id=device.id,
    )
    mock_restore_cache_with_extra_data(
        hass,
        [(State(TOTAL, "2400.0"), {"native_value": 2400.0, "native_unit_of_measurement": "kWh"})],
    )
    entry.add_to_hass = lambda hass: None  # already added
    await setup_entry(entry)
    assert hass.states.get(TOTAL).state == "2400.0"

    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    assert hass.states.get(TOTAL).state == "2400.0"
    assert hass.states.get(POWER).state == "272.9"
    await logger.close()


async def test_live_values_expire_but_totals_stay(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    reading_time = hass.states.get(LAST_READING).state

    freezer.tick(timedelta(minutes=14))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(POWER).state == "272.9"

    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    for entity_id in (POWER, PV_VOLTAGE, TEMPERATURE, STATUS):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    assert hass.states.get(TODAY).state == "2.5"
    assert hass.states.get(TOTAL).state == "2312.1"
    assert hass.states.get(LAST_READING).state == reading_time

    # Sixteen silent minutes also outlast the idle timeout: the datalogger
    # reconnects, as it does every morning.
    assert await logger.closed_by_server()
    await logger.close()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE[-1])
    assert hass.states.get(POWER).state == "87.5"
    await logger.close()


async def test_records_split_and_concatenated_on_the_wire(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    stream = ANNOUNCE + PING + b"".join(LIVE) + b"".join(BUFFERED[:5])
    rng = random.Random(5)
    pos = 0
    while pos < len(stream):
        step = rng.randint(1, 900)
        await logger.send(stream[pos : pos + step])
        pos += step
    replies = await logger.replies(2 + 1 + len(LIVE) + 5)
    await hass.async_block_till_done()
    assert [r.record_type for r in replies[:3]] == [TYPE_ANNOUNCE, TYPE_SET, TYPE_PING]
    assert hass.states.get(POWER).state == "87.5"  # the last live record
    assert hass.states.get(TOTAL).state == "2312.6"
    assert entry.runtime_data.counters.live_records == len(LIVE)
    await logger.close()


async def test_a_bad_crc_is_dropped_and_counted(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    broken = bytearray(LIVE_1220)
    broken[100] ^= 0x01
    await logger.send(bytes(broken))
    (pong,) = await _send(hass, logger, PING)
    assert pong.raw == PING
    assert entry.runtime_data.server.stats.crc_failures == 1
    assert hass.states.get(POWER) is None  # nothing was decoded
    await logger.close()


async def test_reconnection_and_the_connected_sensor(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    assert hass.states.get(CONNECTED).state == STATE_ON
    await logger.close()
    await hass.async_block_till_done()
    for _ in range(50):
        if hass.states.get(CONNECTED).state == STATE_OFF:
            break
        await hass.async_add_executor_job(lambda: None)
        await hass.async_block_till_done()
    assert hass.states.get(CONNECTED).state == STATE_OFF

    again = await _client(hass, entry)
    await _send(hass, again, ANNOUNCE, replies=2)
    await _send(hass, again, LIVE[-1])
    assert hass.states.get(CONNECTED).state == STATE_ON
    assert hass.states.get(POWER).state == "87.5"
    assert entry.runtime_data.server.stats.connections_total == 2
    await again.close()


async def test_two_connections_at_once(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    first = await _client(hass, entry)
    second = await _client(hass, entry)
    (a,) = await _send(hass, first, PING)
    (b,) = await _send(hass, second, PING)
    assert a.raw == b.raw == PING
    assert len(entry.runtime_data.server.connections) == 2
    await first.close()
    await _send(hass, second, LIVE_1220)
    assert hass.states.get(CONNECTED).state == STATE_ON  # one still open
    await second.close()


async def test_allow_list_closes_other_dataloggers(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(make_entry(MODE_ADVANCED, allowed_serials="OTHER12345"))
    logger = await _client(hass, entry)
    await logger.send(ANNOUNCE)
    assert await logger.closed_by_server()
    await hass.async_block_till_done()
    assert entry.runtime_data.server.stats.rejected == 1
    assert hass.states.get(POWER) is None
    assert hass.states.get(CONNECTED) is None
    await logger.close()


async def test_allow_list_accepts_the_listed_datalogger(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(
        make_entry(MODE_ADVANCED, allowed_serials=f"other12345, {DATALOGGER.lower()}")
    )
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    assert hass.states.get(POWER).state == "272.9"
    await logger.close()


async def test_idle_connections_are_closed(hass: HomeAssistant, setup_entry) -> None:
    with patch("custom_components.sunbridge.hub.IDLE_TIMEOUT_SECONDS", 0.2):
        entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, PING)
    assert await logger.closed_by_server()
    await logger.close()


async def test_answers_to_commands_get_no_reply(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    answer = next(r.raw for r in of_type(TYPE_SET))
    await logger.send(answer)
    (pong,) = await _send(hass, logger, PING)  # the only reply is the ping's
    assert pong.raw == PING
    assert entry.runtime_data.server.stats.record_types["0x18"] == 1
    await logger.close()


@pytest.mark.parametrize("busy", [True])
async def test_a_busy_port_retries_setup(hass: HomeAssistant, setup_entry, busy) -> None:
    first = await setup_entry()
    second = make_entry(port=first.runtime_data.server.port)
    second.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(second.entry_id)
    assert second.state.name == "SETUP_RETRY"
    await hass.config_entries.async_unload(second.entry_id)


async def test_every_documented_entity_id_exists(
    hass: HomeAssistant, setup_entry
) -> None:
    """The ids tools/gen_docs.py writes into the docs are the real ones."""
    import importlib.util
    import re
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tools" / "gen_docs.py"
    spec = importlib.util.spec_from_file_location("gen_docs", path)
    gen_docs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen_docs)
    documented = set(re.findall(r"`((?:binary_)?sensor\.[a-z0-9_]+)`", gen_docs.entities()))

    entry = await setup_entry(make_entry(MODE_ADVANCED))
    logger = await _client(hass, entry)
    await _send(hass, logger, ANNOUNCE, replies=2)
    await _send(hass, logger, LIVE_1220)
    registry = er.async_get(hass)
    created = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert documented == created
    await logger.close()


async def test_an_oversized_frame_closes_the_connection(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    await logger.send(bytes.fromhex("0001000613880104") + b"\x00" * 64)
    assert await logger.closed_by_server()
    assert entry.runtime_data.server.stats.violations == 1
    await logger.close()


async def test_connections_over_the_cap_are_refused(
    hass: HomeAssistant, setup_entry
) -> None:
    from custom_components.sunbridge.const import MAX_CONNECTIONS

    assert MAX_CONNECTIONS <= 4
    entry = await setup_entry()
    loggers = [await _client(hass, entry) for _ in range(MAX_CONNECTIONS)]
    for logger in loggers:
        (pong,) = await _send(hass, logger, PING)
        assert pong.raw == PING
    extra = await _client(hass, entry)
    assert await extra.closed_by_server()
    assert entry.runtime_data.server.stats.refused_connections == 1
    await extra.close()
    for logger in loggers:
        await logger.close()


async def _dribble(logger: FakeDatalogger, data: bytes, every: float) -> None:
    for i in range(len(data)):
        await logger.send(data[i : i + 1])
        await asyncio.sleep(every)


async def test_bytes_without_a_valid_frame_do_not_keep_a_connection(
    hass: HomeAssistant, setup_entry
) -> None:
    """Slowloris: a byte at a time, never a whole frame, before the handshake."""
    with patch("custom_components.sunbridge.hub.HANDSHAKE_TIMEOUT_SECONDS", 0.3):
        entry = await setup_entry()
    logger = await _client(hass, entry)
    dribble = asyncio.ensure_future(_dribble(logger, LIVE_1220, 0.02))
    try:
        assert await logger.closed_by_server(timeout=3)
    finally:
        dribble.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionError):
            await dribble
    await logger.close()


async def test_idle_is_measured_from_the_last_valid_frame(
    hass: HomeAssistant, setup_entry
) -> None:
    with patch("custom_components.sunbridge.hub.IDLE_TIMEOUT_SECONDS", 0.4):
        entry = await setup_entry()
    logger = await _client(hass, entry)
    await _send(hass, logger, PING)
    dribble = asyncio.ensure_future(_dribble(logger, LIVE_1220, 0.02))
    try:
        assert await logger.closed_by_server(timeout=3)
    finally:
        dribble.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionError):
            await dribble
    await logger.close()


def with_serials(raw: bytes, datalogger: str, inverter: str) -> bytes:
    """A captured data record re-framed with other serials."""
    frame = parse_frame(raw)
    payload = (
        datalogger.encode().ljust(30, b"\x00")
        + inverter.encode().ljust(30, b"\x00")
        + frame.payload[60:]
    )
    return build_frame(frame.transaction, frame.record_type, payload)


def _devices(hass: HomeAssistant, entry) -> list:
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)


async def test_a_ping_alone_creates_no_device(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    logger = await _client(hass, entry)
    (pong,) = await _send(hass, logger, PING)
    assert pong.raw == PING
    assert _devices(hass, entry) == []
    assert hass.states.get(CONNECTED) is None
    await logger.close()


@pytest.mark.parametrize(
    "record", [ANNOUNCE, LIVE_1220, BUFFERED[0]], ids=["announce", "live", "buffered"]
)
async def test_an_undecodable_record_creates_no_device(
    hass: HomeAssistant, setup_entry, record: bytes
) -> None:
    """Serials in a frame prove nothing until its payload decodes."""
    entry = await setup_entry()
    logger = await _client(hass, entry)
    frame = parse_frame(record)
    truncated = build_frame(frame.transaction, frame.record_type, frame.payload[:90])
    await logger.send(truncated)
    await _send(hass, logger, PING)
    assert _devices(hass, entry) == []
    assert entry.runtime_data.server.stats.decode_errors == 1
    await logger.close()


async def test_at_most_two_dataloggers_per_entry(hass: HomeAssistant, setup_entry) -> None:
    from custom_components.sunbridge.const import MAX_DATALOGGERS

    assert MAX_DATALOGGERS == 2
    entry = await setup_entry()
    loggers = []
    for i in range(3):
        logger = await _client(hass, entry)
        loggers.append(logger)
        await logger.send(with_serials(LIVE_1220, f"LOGGER000{i}", f"INVERT000{i}"))
    assert await loggers[2].closed_by_server()
    await hass.async_block_till_done()
    hub = entry.runtime_data
    assert hub.dataloggers == {"LOGGER0000", "LOGGER0001"}
    assert set(hub.inverters) == {"INVERT0000", "INVERT0001"}
    assert len(_devices(hass, entry)) == 4
    assert hub.counters.refused_dataloggers == 1
    for logger in loggers:
        await logger.close()


async def test_at_most_two_inverters_per_entry(hass: HomeAssistant, setup_entry) -> None:
    from custom_components.sunbridge.const import MAX_INVERTERS

    assert MAX_INVERTERS == 2
    entry = await setup_entry()
    logger = await _client(hass, entry)
    for i in range(3):
        await _send(hass, logger, with_serials(LIVE_1220, DATALOGGER, f"INVERT000{i}"))
    hub = entry.runtime_data
    assert set(hub.inverters) == {"INVERT0000", "INVERT0001"}
    assert hub.counters.refused_inverters == 1
    assert len(_devices(hass, entry)) == 3
    await logger.close()


async def test_the_allow_list_is_checked_before_any_registry_write(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(make_entry(MODE_ADVANCED, allowed_serials="OTHER12345"))
    logger = await _client(hass, entry)
    await logger.send(LIVE_1220)
    assert await logger.closed_by_server()
    await hass.async_block_till_done()
    assert _devices(hass, entry) == []
    await logger.close()
