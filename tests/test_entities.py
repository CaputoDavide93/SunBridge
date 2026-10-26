"""What the entities show over time: the lifetime filter, the daily counter
at local midnight, availability, offline logging, repairs, device lifecycle
and the Simple/Advanced switch.

Most tests drive the hub directly, as the server would after decoding a
frame, so time can be frozen and moved without a socket in the way.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.sunbridge.const import DOMAIN, MODE_ADVANCED, MODE_SIMPLE
from custom_components.sunbridge.decoder import decode
from custom_components.sunbridge.protocol import TYPE_DATA, parse_frame

from .captures import DATALOGGER, INVERTER, of_type
from .conftest import make_entry

POWER = "sensor.solar_inverter_power"
TODAY = "sensor.solar_inverter_energy_today"
TOTAL = "sensor.solar_inverter_energy_total"
LAST_READING = "sensor.solar_inverter_last_reading"
CONNECTED = "binary_sensor.solar_datalogger_connected"

# 12:20:00 on the capture day: 272.9 W, 2.5 kWh today, 2312.1 kWh total.
BASE = decode(parse_frame(of_type(TYPE_DATA)[1].raw).payload)
LONDON = (51.5, -0.12)


# ── helpers ──────────────────────────────────────────────────────────────
def _connect(hub, datalogger: str = DATALOGGER) -> None:
    assert hub.datalogger_connected(datalogger, True)


def _disconnect(hub, datalogger: str = DATALOGGER) -> None:
    hub.datalogger_connected(datalogger, False)


async def _feed(
    hass: HomeAssistant,
    hub,
    *,
    total: float | None = None,
    today: float | None = None,
    live: bool = True,
    inverter: str = INVERTER,
    datalogger: str = DATALOGGER,
) -> None:
    reading = replace(
        BASE,
        datalogger_serial=datalogger,
        inverter_serial=inverter,
        energy_total=BASE.energy_total if total is None else total,
        energy_today=BASE.energy_today if today is None else today,
    )
    assert hub.inverter_seen(datalogger, inverter)
    hub.reading(reading, live=live)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _move_to(hass: HomeAssistant, freezer, when: datetime) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


async def _london(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/London")
    hass.config.latitude, hass.config.longitude = LONDON


def _local(*args: int) -> datetime:
    return datetime(*args, tzinfo=dt_util.get_time_zone("Europe/London"))


def _restored_inverter(hass: HomeAssistant, entry, serial: str = INVERTER):
    """An inverter device and its total sensors, as a previous run left them."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"inverter_{serial}")},
        name="Solar Inverter",
    )
    registry = er.async_get(hass)
    for key in ("energy_total", "energy_today", "last_reading"):
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{serial}_{key}",
            suggested_object_id=f"solar_inverter_{key}",
            config_entry=entry, device_id=device.id,
        )
    return device


async def _restored_setup(hass, setup_entry, restore: list, entry=None):
    entry = entry or make_entry()
    entry.add_to_hass(hass)
    _restored_inverter(hass, entry)
    mock_restore_cache_with_extra_data(hass, restore)
    entry.add_to_hass = lambda hass: None  # already added
    await setup_entry(entry)
    return entry


def _kwh(value: float | str, **extra) -> dict:
    return {"native_value": value, "native_unit_of_measurement": "kWh", **extra}


def _when(when: datetime) -> dict:
    """A saved timestamp sensor's extra data, as Home Assistant stores it."""
    return {
        "native_value": {
            "__type": "<class 'datetime.datetime'>",
            "isoformat": when.isoformat(),
        },
        "native_unit_of_measurement": None,
    }


# ── #2 lifetime energy: bounded anomaly rejection ────────────────────────
async def test_a_single_lower_lifetime_is_ignored(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, total=2312.1)
    await _tick(hass, freezer, timedelta(minutes=5))
    await _feed(hass, hub, total=2300.0)
    assert _state(hass, TOTAL) == "2312.1"
    assert hub.counters.lifetime_decreases_ignored == 1
    await _tick(hass, freezer, timedelta(minutes=5))
    await _feed(hass, hub, total=2312.2)
    assert _state(hass, TOTAL) == "2312.2"


async def test_a_single_impossible_jump_is_ignored(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Ten times the total five minutes later: no inverter makes that."""
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, total=2312.1)
    await _tick(hass, freezer, timedelta(minutes=5))
    await _feed(hass, hub, total=23121.0)
    assert _state(hass, TOTAL) == "2312.1"
    assert hub.counters.lifetime_jumps_ignored == 1
    await _tick(hass, freezer, timedelta(minutes=5))
    await _feed(hass, hub, total=2312.2)
    assert _state(hass, TOTAL) == "2312.2"


async def test_a_large_rise_after_a_long_gap_is_accepted(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Twenty kWh in two hours is possible: it is not an anomaly."""
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, total=2312.1)
    await _tick(hass, freezer, timedelta(hours=2))
    await _feed(hass, hub, total=2332.1)
    assert _state(hass, TOTAL) == "2332.1"
    assert hub.counters.lifetime_jumps_ignored == 0


async def test_a_sustained_lower_lifetime_is_a_reset(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """A counter reset or a replaced inverter: three records agree, so the
    lower value is real. total_increasing turns the drop into a new cycle."""
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, total=2312.1)
    for value, shown in ((0.1, "2312.1"), (0.1, "2312.1"), (0.2, "0.2")):
        await _tick(hass, freezer, timedelta(minutes=5))
        await _feed(hass, hub, total=value)
        assert _state(hass, TOTAL) == shown
    assert hub.counters.lifetime_resets_accepted == 1
    assert hass.states.get(TOTAL).attributes["state_class"] == "total_increasing"
    await _tick(hass, freezer, timedelta(minutes=5))
    await _feed(hass, hub, total=0.3)
    assert _state(hass, TOTAL) == "0.3"


async def test_lower_records_that_disagree_are_not_a_reset(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, total=2312.1)
    for value in (5.0, 1.0, 9000.0, 3.0, 0.5):
        await _tick(hass, freezer, timedelta(minutes=5))
        await _feed(hass, hub, total=value)
        assert _state(hass, TOTAL) == "2312.1"
    assert hub.counters.lifetime_resets_accepted == 0


async def test_buffered_records_never_confirm_a_reset(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, total=2312.1)
    await _tick(hass, freezer, timedelta(minutes=5))
    await _feed(hass, hub, total=0.1)
    for _ in range(5):
        await _feed(hass, hub, total=0.1, live=False)
    assert _state(hass, TOTAL) == "2312.1"
    assert hub.counters.lifetime_resets_accepted == 0


async def test_a_restored_lifetime_yields_to_a_sustained_lower_one(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """The restored total holds against one lower record, not for ever."""
    entry = await _restored_setup(
        hass, setup_entry, [(State(TOTAL, "2400.0"), _kwh(2400.0))]
    )
    hub = entry.runtime_data
    assert _state(hass, TOTAL) == "2400.0"
    _connect(hub)
    for shown in ("2400.0", "2400.0", "2312.1"):
        await _feed(hass, hub, total=2312.1)
        assert _state(hass, TOTAL) == shown
        await _tick(hass, freezer, timedelta(minutes=5))


async def test_a_restored_last_reading_bounds_the_first_jump(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """After a restart the restored reading time says how much energy could
    have been made since: a wild first record is still caught."""
    last = dt_util.utcnow() - timedelta(minutes=10)
    entry = await _restored_setup(
        hass,
        setup_entry,
        [
            (State(TOTAL, "2312.1"), _kwh(2312.1)),
            (
                State(LAST_READING, last.isoformat()),
                {
                    "native_value": {
                        "__type": "<class 'datetime.datetime'>",
                        "isoformat": last.isoformat(),
                    },
                    "native_unit_of_measurement": None,
                },
            ),
        ],
    )
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, total=99999.0)
    assert _state(hass, TOTAL) == "2312.1"
    await _feed(hass, hub, total=2312.3)
    assert _state(hass, TOTAL) == "2312.3"


async def test_a_reading_before_the_sensors_load_is_still_checked(
    hass: HomeAssistant, setup_entry
) -> None:
    """The listener is open before the sensors restore their state: a record
    arriving in between is checked against the saved total, not accepted
    as the first value ever seen."""
    original = hass.config_entries.async_forward_entry_setups

    async def reading_first(entry, platforms):
        hub = entry.runtime_data
        _connect(hub)
        await _feed(hass, hub, total=1000.0)
        return await original(entry, platforms)

    with patch.object(
        hass.config_entries, "async_forward_entry_setups", reading_first
    ):
        entry = await _restored_setup(
            hass, setup_entry, [(State(TOTAL, "2312.1"), _kwh(2312.1))]
        )
    hub = entry.runtime_data
    assert hub.inverters[INVERTER].energy_total == 2312.1
    assert hub.inverters[INVERTER].lifetime_decreases_ignored == 1
    assert _state(hass, TOTAL) == "2312.1"


async def test_a_restored_reading_time_bounds_a_reading_before_the_sensors_load(
    hass: HomeAssistant, setup_entry
) -> None:
    last = dt_util.utcnow() - timedelta(minutes=10)
    original = hass.config_entries.async_forward_entry_setups

    async def reading_first(entry, platforms):
        hub = entry.runtime_data
        _connect(hub)
        await _feed(hass, hub, total=99999.0)
        return await original(entry, platforms)

    with patch.object(
        hass.config_entries, "async_forward_entry_setups", reading_first
    ):
        entry = await _restored_setup(
            hass,
            setup_entry,
            [
                (State(TOTAL, "2312.1"), _kwh(2312.1)),
                (State(LAST_READING, last.isoformat()), _when(last)),
            ],
        )
    hub = entry.runtime_data
    assert hub.inverters[INVERTER].lifetime_jumps_ignored == 1
    assert _state(hass, TOTAL) == "2312.1"


async def test_unreadable_saved_totals_are_not_seeded(
    hass: HomeAssistant, setup_entry
) -> None:
    """A saved state that is not a number, or a time that is not a time,
    seeds nothing: the first reading is then the first value."""
    entry = await _restored_setup(
        hass,
        setup_entry,
        [
            (State(TOTAL, "unknown"), _kwh("not a number")),
            (State(LAST_READING, "x"), {"native_value": "x", "native_unit_of_measurement": None}),
        ],
    )
    state = entry.runtime_data.inverters[INVERTER]
    assert state.energy_total is None
    assert state.energy_total_at is None


# ── #3/#4 energy today: the inverter's counter, zero after local midnight ─
async def test_energy_today_is_zero_after_local_midnight(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    await _london(hass)
    freezer.move_to(_local(2026, 7, 1, 20, 0))
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, today=12.3)
    assert _state(hass, TODAY) == "12.3"
    attributes = hass.states.get(TODAY).attributes
    assert attributes["state_class"] == "total_increasing"
    assert "last_reset" not in attributes

    # 00:30 BST is 23:30 UTC: still "yesterday" in UTC, already today here.
    await _move_to(hass, freezer, _local(2026, 7, 1, 23, 59, 59))
    assert _state(hass, TODAY) == "12.3"
    await _move_to(hass, freezer, _local(2026, 7, 2, 0, 0, 0))
    assert _state(hass, TODAY) == "0.0"
    await _move_to(hass, freezer, _local(2026, 7, 2, 0, 30))
    assert _state(hass, TODAY) == "0.0"
    assert _state(hass, TOTAL) == "2312.1"  # the lifetime does not reset

    await _move_to(hass, freezer, _local(2026, 7, 2, 6, 0))
    _connect(hub)
    await _feed(hass, hub, today=0.1)
    assert _state(hass, TODAY) == "0.1"


async def test_energy_today_resets_on_the_autumn_dst_day(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """25 Oct 2026: clocks go back at 02:00 BST. Both midnights are local."""
    await _london(hass)
    freezer.move_to(_local(2026, 10, 24, 16, 0))
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, today=4.2)
    await _move_to(hass, freezer, _local(2026, 10, 25, 0, 0, 1))  # 23:00:01 UTC
    assert _state(hass, TODAY) == "0.0"
    await _move_to(hass, freezer, _local(2026, 10, 25, 12, 0))
    await _feed(hass, hub, today=3.1)
    await _move_to(hass, freezer, _local(2026, 10, 25, 23, 59, 59))
    assert _state(hass, TODAY) == "3.1"
    await _move_to(hass, freezer, _local(2026, 10, 26, 0, 0, 1))  # 00:00:01 UTC
    assert _state(hass, TODAY) == "0.0"


async def test_energy_today_resets_on_the_spring_dst_day(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """28 Mar 2027: clocks go forward at 01:00 GMT, a 23-hour day."""
    await _london(hass)
    freezer.move_to(_local(2027, 3, 27, 16, 0))
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, today=6.0)
    await _move_to(hass, freezer, _local(2027, 3, 28, 0, 0, 1))  # 00:00:01 UTC
    assert _state(hass, TODAY) == "0.0"
    await _move_to(hass, freezer, _local(2027, 3, 28, 12, 0))
    await _feed(hass, hub, today=2.0)
    await _move_to(hass, freezer, _local(2027, 3, 28, 23, 59, 59))
    assert _state(hass, TODAY) == "2.0"
    await _move_to(hass, freezer, _local(2027, 3, 29, 0, 0, 1))  # 23:00:01 UTC
    assert _state(hass, TODAY) == "0.0"


async def test_a_restart_after_midnight_does_not_bring_yesterday_back(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    await _london(hass)
    freezer.move_to(_local(2026, 7, 2, 0, 5))
    await _restored_setup(
        hass, setup_entry, [(State(TODAY, "9.9"), _kwh(9.9, day="2026-07-01"))]
    )
    assert _state(hass, TODAY) == "0.0"


async def test_a_restart_the_same_day_keeps_the_value(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    await _london(hass)
    freezer.move_to(_local(2026, 7, 1, 21, 0))
    await _restored_setup(
        hass, setup_entry, [(State(TODAY, "9.9"), _kwh(9.9, day="2026-07-01"))]
    )
    assert _state(hass, TODAY) == "9.9"
    await _move_to(hass, freezer, _local(2026, 7, 2, 0, 0, 1))
    assert _state(hass, TODAY) == "0.0"


async def test_a_value_saved_without_its_day_uses_its_state_time(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Saved by an older version: the restored state's own time decides."""
    await _london(hass)
    freezer.move_to(_local(2026, 7, 2, 0, 5))
    yesterday = _local(2026, 7, 1, 19, 0)
    await _restored_setup(
        hass,
        setup_entry,
        [(State(TODAY, "9.9", last_updated=yesterday, last_changed=yesterday), _kwh(9.9))],
    )
    assert _state(hass, TODAY) == "0.0"


async def test_the_saved_day_travels_with_the_value(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    await _london(hass)
    freezer.move_to(_local(2026, 7, 1, 20, 0))
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub, today=12.3)
    from homeassistant.helpers.restore_state import async_get

    stored = next(
        s.extra_data.as_dict()
        for s in async_get(hass).async_get_stored_states()
        if s.state.entity_id == TODAY
    )
    assert stored["native_value"] == 12.3
    assert stored["day"] == "2026-07-01"


# ── #11 availability and offline logging ──────────────────────────────────
async def test_a_closed_connection_makes_live_values_unavailable(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Data stale and connection gone are two reasons: the second acts
    after a short grace, so a reconnect does not flicker the sensors."""
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    _disconnect(hub)
    await _tick(hass, freezer, timedelta(seconds=60))
    assert _state(hass, POWER) == "272.9"
    await _tick(hass, freezer, timedelta(seconds=90))
    assert _state(hass, POWER) == STATE_UNAVAILABLE
    assert _state(hass, TOTAL) == "2312.1"
    assert _state(hass, TODAY) == "2.5"
    # Data still within the stale window comes back with the connection.
    _connect(hub)
    await hass.async_block_till_done()
    assert _state(hass, POWER) == "272.9"


async def test_a_quick_reconnect_keeps_values_and_logs_nothing(
    hass: HomeAssistant, setup_entry, freezer, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.sunbridge")
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    _disconnect(hub)
    await _tick(hass, freezer, timedelta(seconds=30))
    _connect(hub)
    await _tick(hass, freezer, timedelta(minutes=5))
    assert _state(hass, POWER) == "272.9"
    ours = " ".join(
        r.getMessage() for r in caplog.records
        if r.name.startswith("custom_components.sunbridge")
    )
    assert "is offline" not in ours
    assert "back online" not in ours


async def test_offline_and_back_are_logged_once_each(
    hass: HomeAssistant, setup_entry, freezer, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.sunbridge")
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    _disconnect(hub)
    for _ in range(40):  # a whole night of stale checks
        await _tick(hass, freezer, timedelta(minutes=15))
    ours = [r for r in caplog.records if r.name.startswith("custom_components.sunbridge")]
    offline = [r for r in ours if "is offline" in r.getMessage()]
    assert len(offline) == 1
    assert offline[0].levelno == logging.INFO
    _connect(hub)
    _disconnect(hub)
    _connect(hub)
    await hass.async_block_till_done()
    ours = [r for r in caplog.records if r.name.startswith("custom_components.sunbridge")]
    back = [r for r in ours if "back online" in r.getMessage()]
    assert len(back) == 1
    assert back[0].levelno == logging.INFO


# ── #21 repairs: silent in daylight ──────────────────────────────────────
def _silent_issue(hass: HomeAssistant):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"datalogger_silent_{DATALOGGER}")


async def test_a_datalogger_silent_through_daylight_raises_a_repair(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    await _london(hass)
    freezer.move_to(_local(2026, 7, 1, 9, 0))
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    _disconnect(hub)
    await _move_to(hass, freezer, _local(2026, 7, 1, 11, 50))
    assert _silent_issue(hass) is None
    await _move_to(hass, freezer, _local(2026, 7, 1, 12, 5))
    issue = _silent_issue(hass)
    assert issue is not None
    assert issue.translation_key == "datalogger_silent"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert not issue.is_fixable
    assert issue.translation_placeholders["name"] == "Solar Datalogger"

    _connect(hub)
    await hass.async_block_till_done()
    assert _silent_issue(hass) is None


async def test_a_night_without_data_raises_nothing(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    await _london(hass)
    freezer.move_to(_local(2026, 7, 1, 21, 0))
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    _disconnect(hub)
    when = _local(2026, 7, 1, 21, 0)
    while when < _local(2026, 7, 2, 7, 0):  # sunrise is about 04:45 BST
        when += timedelta(minutes=30)
        await _move_to(hass, freezer, when)
        assert _silent_issue(hass) is None, when


async def test_without_a_sunrise_the_repair_waits_a_day(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Polar day or night: no sunrise to measure from, so 24 hours."""
    freezer.move_to(datetime(2026, 7, 1, 12, 0, tzinfo=dt_util.UTC))
    with patch("custom_components.sunbridge.hub.get_astral_event_date", return_value=None):
        entry = await setup_entry()
        hub = entry.runtime_data
        _connect(hub)
        await _feed(hass, hub)
        _disconnect(hub)
        await _tick(hass, freezer, timedelta(hours=23))
        assert _silent_issue(hass) is None
        await _tick(hass, freezer, timedelta(hours=1, minutes=1))
        assert _silent_issue(hass) is not None


async def test_a_restored_datalogger_that_never_returns_raises_a_repair(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    await _london(hass)
    freezer.move_to(_local(2026, 7, 1, 8, 0))
    entry = make_entry()
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"datalogger_{DATALOGGER}")},
        name="Solar Datalogger",
    )
    entry.add_to_hass = lambda hass: None
    await setup_entry(entry)
    await _move_to(hass, freezer, _local(2026, 7, 1, 11, 5))
    assert _silent_issue(hass) is not None


async def test_a_repair_survives_unload_and_reload(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Unloading (a reload, a Configure save, a Reconfigure) is not the
    datalogger coming back: the notice stays until data does."""
    freezer.move_to(datetime(2026, 7, 1, 12, 0, tzinfo=dt_util.UTC))
    with patch("custom_components.sunbridge.hub.get_astral_event_date", return_value=None):
        entry = await setup_entry()
        hub = entry.runtime_data
        _connect(hub)
        _disconnect(hub)
        await _tick(hass, freezer, timedelta(hours=25))
        assert _silent_issue(hass) is not None
        assert _silent_issue(hass).is_persistent, "kept across a restart"
        await hass.config_entries.async_unload(entry.entry_id)
        assert _silent_issue(hass) is not None
        assert await hass.config_entries.async_setup(entry.entry_id)
        await _tick(hass, freezer, timedelta(minutes=1))
        assert _silent_issue(hass) is not None
        _connect(entry.runtime_data)
        await hass.async_block_till_done()
        assert _silent_issue(hass) is None


async def test_the_silence_timer_survives_a_reload(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Silent since 09:00, reloaded at 10:00: three hours of daylight are up
    at 12:00, not at 13:00."""
    await _london(hass)
    freezer.move_to(_local(2026, 7, 1, 9, 0))
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    _disconnect(hub)
    await _move_to(hass, freezer, _local(2026, 7, 1, 10, 0))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await _move_to(hass, freezer, _local(2026, 7, 1, 12, 5))
    assert _silent_issue(hass) is not None


async def test_the_silence_timer_survives_a_restart(
    hass: HomeAssistant, setup_entry, freezer, hass_storage
) -> None:
    """The last connection's time is saved with the entry, so a restart at
    10:00 does not start the three hours again."""
    await _london(hass)
    freezer.move_to(_local(2026, 7, 1, 10, 0))
    entry = make_entry()
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"datalogger_{DATALOGGER}")},
        name="Solar Datalogger",
    )
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {
            "dataloggers": {
                DATALOGGER: {"last_connected": _local(2026, 7, 1, 9, 0).isoformat()}
            }
        },
    }
    entry.add_to_hass = lambda hass: None
    await setup_entry(entry)
    await _move_to(hass, freezer, _local(2026, 7, 1, 12, 5))
    assert _silent_issue(hass) is not None


async def test_the_last_connection_is_saved(
    hass: HomeAssistant, setup_entry, freezer, hass_storage
) -> None:
    freezer.move_to(datetime(2026, 7, 1, 9, 0, tzinfo=dt_util.UTC))
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    _disconnect(hub)
    await hass.config_entries.async_unload(entry.entry_id)
    saved = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["dataloggers"]
    assert saved == {DATALOGGER: {"last_connected": "2026-07-01T09:00:00+00:00"}}


async def test_a_connected_datalogger_is_saved_as_connected_until_now(
    hass: HomeAssistant, setup_entry, freezer, hass_storage
) -> None:
    """While connected, the saved time keeps moving, so a crash does not
    leave a time hours old behind."""
    freezer.move_to(datetime(2026, 7, 1, 9, 0, tzinfo=dt_util.UTC))
    entry = await setup_entry()
    _connect(entry.runtime_data)
    await _tick(hass, freezer, timedelta(minutes=11))
    await _tick(hass, freezer, timedelta(seconds=30))
    saved = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["dataloggers"]
    assert saved[DATALOGGER]["last_connected"] >= "2026-07-01T09:10:00+00:00"


async def test_removing_the_entry_clears_its_repairs_and_saved_times(
    hass: HomeAssistant, setup_entry, freezer, hass_storage
) -> None:
    freezer.move_to(datetime(2026, 7, 1, 12, 0, tzinfo=dt_util.UTC))
    with patch("custom_components.sunbridge.hub.get_astral_event_date", return_value=None):
        entry = await setup_entry()
        hub = entry.runtime_data
        _connect(hub)
        _disconnect(hub)
        await _tick(hass, freezer, timedelta(hours=25))
    assert _silent_issue(hass) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _silent_issue(hass) is None
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage


def _days(sunrise: int, sunset: int):
    """Every day's sunrise and sunset at these UTC hours."""

    def event(hass, event, day):
        hour = sunrise if event == "sunrise" else sunset
        return datetime(day.year, day.month, day.day, hour, tzinfo=dt_util.UTC)

    return event


async def _silent_from(hass, setup_entry, freezer, days, since, checks) -> None:
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(since)
    with patch("custom_components.sunbridge.hub.get_astral_event_date", side_effect=days):
        entry = await setup_entry()
        hub = entry.runtime_data
        _connect(hub)
        _disconnect(hub)
        for when, raised in checks:
            await _move_to(hass, freezer, when)
            assert (_silent_issue(hass) is not None) is raised, when


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=dt_util.UTC)


async def test_daylight_adds_up_across_the_night(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Silent from 16:30 with sunset at 18:00: one and a half hours that
    evening and one and a half the next morning are three."""
    await _silent_from(
        hass, setup_entry, freezer, _days(6, 18), _utc(2026, 12, 1, 16, 30),
        [
            (_utc(2026, 12, 2, 7, 0), False),
            (_utc(2026, 12, 2, 7, 35), True),
        ],
    )


async def test_a_day_shorter_than_the_threshold_still_raises_a_repair(
    hass: HomeAssistant, setup_entry, freezer
) -> None:
    """Two hours of daylight a day never add up to three within a day:
    24 hours of silence raise the notice, at whatever hour they end."""
    await _silent_from(
        hass, setup_entry, freezer, _days(10, 12), _utc(2026, 12, 1, 9, 0),
        [
            (_utc(2026, 12, 1, 12, 30), False),
            (_utc(2026, 12, 2, 8, 55), False),
            (_utc(2026, 12, 2, 9, 5), True),
        ],
    )


# ── #5 stale devices ─────────────────────────────────────────────────────
async def _remove_device(hass: HomeAssistant, hass_ws_client, entry, device) -> bool:
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device.id,
        }
    )
    response = await client.receive_json()
    return response["success"]


def _device(hass: HomeAssistant, entry, ident: str):
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ident), entry.entry_id
    )


async def test_a_connected_datalogger_cannot_be_removed(
    hass: HomeAssistant, setup_entry, hass_ws_client
) -> None:
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    logger = _device(hass, entry, f"datalogger_{DATALOGGER}")
    inverter = _device(hass, entry, f"inverter_{INVERTER}")
    assert not await _remove_device(hass, hass_ws_client, entry, logger)
    assert not await _remove_device(hass, hass_ws_client, entry, inverter)
    assert _device(hass, entry, f"datalogger_{DATALOGGER}") is not None


async def test_a_gone_device_can_be_removed_and_comes_back_if_it_returns(
    hass: HomeAssistant, setup_entry, hass_ws_client, freezer
) -> None:
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    _disconnect(hub)
    await _tick(hass, freezer, timedelta(minutes=20))
    inverter = _device(hass, entry, f"inverter_{INVERTER}")
    logger = _device(hass, entry, f"datalogger_{DATALOGGER}")
    assert await _remove_device(hass, hass_ws_client, entry, inverter)
    assert await _remove_device(hass, hass_ws_client, entry, logger)
    await hass.async_block_till_done()
    assert _device(hass, entry, f"inverter_{INVERTER}") is None
    assert INVERTER not in hub.inverters
    assert DATALOGGER not in hub.dataloggers
    assert er.async_get(hass).async_get(POWER) is None

    _connect(hub)
    await _feed(hass, hub)
    assert _device(hass, entry, f"inverter_{INVERTER}") is not None
    assert _state(hass, POWER) == "272.9"
    assert _state(hass, CONNECTED) == "on"


async def test_restored_but_unseen_devices_do_not_fill_the_caps(
    hass: HomeAssistant, setup_entry
) -> None:
    """Two old devices left in the registry must not block a replacement."""
    entry = make_entry()
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    for i in range(2):
        parent = devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"datalogger_OLDLOGGER{i}")},
        )
        devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"inverter_OLDINVERT{i}")},
            via_device_id=parent.id,
        )
        assert parent
    entry.add_to_hass = lambda hass: None
    await setup_entry(entry)
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    assert _device(hass, entry, f"inverter_{INVERTER}") is not None
    assert hub.counters.refused_dataloggers == 0
    assert hub.counters.refused_inverters == 0


async def test_the_caps_still_count_devices_seen_this_run(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry()
    hub = entry.runtime_data
    for i in range(2):
        _connect(hub, f"LOGGER000{i}")
    assert not hub.datalogger_connected("LOGGER0002", True)
    for i in range(2):
        assert hub.inverter_seen("LOGGER0000", f"INVERT000{i}")
    assert not hub.inverter_seen("LOGGER0000", "INVERT0002")


async def test_the_allow_list_applies_in_simple_mode_too(
    hass: HomeAssistant, setup_entry
) -> None:
    """Simple keeps the serial setup found as its allow-list."""
    entry = await setup_entry(make_entry(MODE_SIMPLE, allowed_serials="OTHER12345"))
    assert entry.runtime_data.allowed == frozenset({"OTHER12345"})
    entry = await setup_entry(make_entry(MODE_SIMPLE, port=0))
    assert entry.runtime_data.allowed == frozenset()


# ── #13 device names ─────────────────────────────────────────────────────
async def test_a_second_device_gets_a_name_of_its_own(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry()
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    _connect(hub, "LOGGER0001")
    await _feed(hass, hub, datalogger="LOGGER0001", inverter="INVERT0001")
    names = {
        ident: device.name
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for _, ident in device.identifiers
    }
    assert names == {
        f"datalogger_{DATALOGGER}": "Solar Datalogger",
        f"inverter_{INVERTER}": "Solar Inverter",
        "datalogger_LOGGER0001": "Solar Datalogger LOGGER0001",
        "inverter_INVERT0001": "Solar Inverter INVERT0001",
    }
    # The first one keeps its entity ids.
    assert _state(hass, POWER) == "272.9"
    assert hass.states.get("sensor.solar_inverter_invert0001_power") is not None


async def test_device_names_survive_a_restart(hass: HomeAssistant, setup_entry) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    for serial, name in ((INVERTER, "Solar Inverter"), ("INVERT0001", "Solar Inverter INVERT0001")):
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"inverter_{serial}")},
            name=name,
        )
    entry.add_to_hass = lambda hass: None
    await setup_entry(entry)
    hub = entry.runtime_data
    assert hub.inverter_device(INVERTER)["name"] == "Solar Inverter"
    assert hub.inverter_device("INVERT0001")["name"] == "Solar Inverter INVERT0001"


async def test_device_info_names_no_unverified_model_or_firmware(
    hass: HomeAssistant, setup_entry
) -> None:
    """No captured record proves a model or firmware field: none is shown."""
    entry = await setup_entry()
    hub = entry.runtime_data
    for info in (hub.inverter_device(INVERTER), hub.datalogger_device(DATALOGGER)):
        assert "model" not in info
        assert "sw_version" not in info
        assert info["manufacturer"] == "Growatt"


# ── #14 Advanced to Simple is reversible ─────────────────────────────────
ADVANCED_ONLY = "sensor.solar_inverter_grid_frequency"
UNVERIFIED = "sensor.solar_inverter_second_temperature"
USER_OFF = "sensor.solar_inverter_pv_power"


async def _switch(hass: HomeAssistant, entry, mode: str) -> None:
    hass.config_entries.async_update_entry(entry, options={"mode": mode})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_simple_disables_the_advanced_sensors_and_advanced_restores_them(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(make_entry(MODE_ADVANCED))
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    registry = er.async_get(hass)
    registry.async_update_entity(
        ADVANCED_ONLY, name="Mains frequency"
    )
    registry.async_update_entity(USER_OFF, disabled_by=er.RegistryEntryDisabler.USER)
    assert registry.async_get(UNVERIFIED).disabled_by is er.RegistryEntryDisabler.INTEGRATION

    await _switch(hass, entry, MODE_SIMPLE)
    entity = registry.async_get(ADVANCED_ONLY)
    assert entity is not None
    assert entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entity.name == "Mains frequency"
    assert registry.async_get(USER_OFF).disabled_by is er.RegistryEntryDisabler.USER
    assert registry.async_get(POWER).disabled_by is None
    assert hass.states.get(ADVANCED_ONLY) is None

    await _switch(hass, entry, MODE_ADVANCED)
    assert registry.async_get(ADVANCED_ONLY).disabled_by is None
    assert registry.async_get(ADVANCED_ONLY).name == "Mains frequency"
    assert registry.async_get(USER_OFF).disabled_by is er.RegistryEntryDisabler.USER
    # Never enabled by the user: it stays off, as it was.
    assert registry.async_get(UNVERIFIED).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    _connect(hub := entry.runtime_data)
    await _feed(hass, hub)
    assert _state(hass, ADVANCED_ONLY) == "50.04"


async def test_an_enabled_unverified_sensor_comes_back_enabled(
    hass: HomeAssistant, setup_entry
) -> None:
    entry = await setup_entry(make_entry(MODE_ADVANCED))
    hub = entry.runtime_data
    _connect(hub)
    await _feed(hass, hub)
    registry = er.async_get(hass)
    registry.async_update_entity(UNVERIFIED, disabled_by=None)
    await _switch(hass, entry, MODE_SIMPLE)
    assert registry.async_get(UNVERIFIED).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    await _switch(hass, entry, MODE_ADVANCED)
    assert registry.async_get(UNVERIFIED).disabled_by is None


# ── translations and icons ───────────────────────────────────────────────
def _component_json(name: str) -> dict:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "custom_components" / "sunbridge"
    return json.loads((root / name).read_text())


def test_the_repair_issue_is_translated_with_its_placeholders() -> None:
    import re

    for name in ("strings.json", "translations/en.json"):
        issue = _component_json(name)["issues"]["datalogger_silent"]
        text = issue["title"] + issue["description"]
        assert set(re.findall(r"{(\w+)}", text)) <= {"name", "port", "hours"}
        assert "—" not in text
        assert "–" not in text


def test_every_icon_names_a_translated_entity() -> None:
    icons = _component_json("icons.json")["entity"]
    names = _component_json("strings.json")["entity"]
    for platform, keys in icons.items():
        for key, icon in keys.items():
            assert key in names[platform], key
            assert icon["default"].startswith("mdi:")
            for state in icon.get("state", {}):
                assert state in names[platform][key]["state"], (key, state)
