"""The data layout, proven against Home Assistant's history of the same moments.

Every captured live (0x04) record is decoded and compared with what Home
Assistant showed when the server of the day published that record (see `ground_truth`). The
record with no ground truth (12:15:01, before Home Assistant had states) is
still decoded, and must decode.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise

import pytest

from custom_components.sunbridge.decoder import (
    STATUS_PRODUCING,
    STATUS_WAITING,
    TIME_OFFSET,
    DecodeError,
    check_announce,
    decode,
    record_time,
    registers,
)
from custom_components.sunbridge.protocol import (
    TYPE_ANNOUNCE,
    TYPE_BUFFERED,
    TYPE_DATA,
    parse_frame,
)

from .captures import DATALOGGER, INVERTER, ground_truth, of_type, status_truth

LIVE = of_type(TYPE_DATA)

FIELDS = {
    "sensor.solar_power": "output_power",
    "sensor.solar_energy_today": "energy_today",
    "sensor.solar_energy_total": "energy_total",
    "sensor.solar_panel_voltage": "pv_voltage",
    "sensor.solar_inverter_temperature": "temperature",
}


def test_there_are_live_records() -> None:
    assert len(LIVE) >= 30


@pytest.mark.parametrize("entity_id", list(FIELDS))
def test_every_live_record_matches_home_assistant(entity_id: str) -> None:
    compared = 0
    for record in LIVE:
        expected = ground_truth(entity_id, record.ts)
        if expected is None:
            continue
        reading = decode(parse_frame(record.raw).payload)
        assert getattr(reading, FIELDS[entity_id]) == pytest.approx(expected), (
            f"{entity_id} at {record.ts:%H:%M:%S}"
        )
        compared += 1
    # All but the one record that predates Home Assistant's history.
    assert compared == len(LIVE) - 1


def test_status_matches_home_assistant() -> None:
    compared = 0
    for record in LIVE:
        expected = status_truth(record.ts)
        if expected is None:
            continue
        reading = decode(parse_frame(record.raw).payload)
        assert reading.status == expected.lower() == STATUS_PRODUCING
        compared += 1
    assert compared == len(LIVE) - 1


def test_live_records_carry_no_time_of_their_own() -> None:
    for record in LIVE:
        assert decode(parse_frame(record.raw).payload).record_time is None


def test_serials() -> None:
    reading = decode(parse_frame(LIVE[0].raw).payload)
    assert (reading.datalogger_serial, reading.inverter_serial) == (DATALOGGER, INVERTER)


def test_two_register_blocks_3000_to_3249() -> None:
    for record in LIVE + of_type(TYPE_BUFFERED):
        regs = registers(parse_frame(record.raw).payload)
        assert min(regs) == 3000
        assert max(regs) == 3249
        assert len(regs) == 250


def test_announce_has_the_same_block_layout() -> None:
    """The 0x03 record uses the same table: registers 0..124, then 3000..3124."""
    regs = registers(parse_frame(of_type(TYPE_ANNOUNCE)[0].raw).payload)
    assert sorted(regs) == [*range(125), *range(3000, 3125)]


def test_buffered_records_carry_their_own_time() -> None:
    """0x50 records are stamped by the datalogger's clock, never later than
    their arrival. That clock ran one hour ahead of UTC on the capture day (UK
    summer time had ended that morning), hence the hour of slack."""
    buffered = of_type(TYPE_BUFFERED)
    assert buffered
    for record in buffered:
        stamp = decode(parse_frame(record.raw).payload).record_time
        assert isinstance(stamp, datetime)
        arrived = record.ts.replace(tzinfo=None)
        assert stamp <= arrived + timedelta(hours=1, minutes=1), record.ts


def test_waiting_status_seen_in_buffered_morning_records() -> None:
    """Status 0 with zero output: the inverter waiting at dawn (buffered)."""
    readings = [decode(parse_frame(r.raw).payload) for r in of_type(TYPE_BUFFERED)]
    waiting = [r for r in readings if r.status_code == 0]
    assert waiting
    assert all(r.status == STATUS_WAITING and r.output_power == 0 for r in waiting)


def test_working_time_advances_with_the_wall_clock() -> None:
    """Registers 3047-3048 count half-seconds: consecutive live records five
    minutes apart differ by about 300 s (within the 15 s sampling jitter)."""
    for a, b in pairwise(LIVE):
        ra = decode(parse_frame(a.raw).payload)
        rb = decode(parse_frame(b.raw).payload)
        wall = (b.ts - a.ts).total_seconds()
        counted = (rb.working_time_hours - ra.working_time_hours) * 3600
        assert abs(counted - wall) < 20, f"{a.ts:%H:%M} -> {b.ts:%H:%M}"


def test_plausible_extra_fields() -> None:
    """No ground truth for these, so only physics (docs/data-layout.md): a
    50 Hz grid, UK mains, grid V x I within 40% of output power, PV power at
    least the output, PV V x I within 25% of PV power when producing."""
    for record in LIVE:
        r = decode(parse_frame(record.raw).payload)
        assert 49.5 < r.grid_frequency < 50.5
        assert 216 < r.grid_voltage < 253
        assert abs(r.grid_voltage * r.grid_current - r.output_power) < 0.4 * r.output_power
        assert r.pv_power >= r.output_power * 0.9
        if r.pv_power > 100:
            assert abs(r.pv_voltage * r.pv_current - r.pv_power) < 0.25 * r.pv_power


def test_a_truncated_record_raises() -> None:
    payload = parse_frame(LIVE[0].raw).payload
    with pytest.raises(DecodeError):
        decode(payload[:200])
    with pytest.raises(DecodeError):
        decode(b"\x00" * 600)


def test_registers_the_docs_say_repeat() -> None:
    """PV power (3001-3002) equals PV1 power (3005-3006) on this single-string
    inverter, and 3038 repeats the grid voltage (3026), in every data record."""
    for record in LIVE + of_type(TYPE_BUFFERED):
        regs = registers(parse_frame(record.raw).payload)
        assert (regs[3001], regs[3002]) == (regs[3005], regs[3006])
        assert regs[3038] == regs[3026]


def _stamped(*fields: int) -> bytes:
    """A payload with only the six record-time bytes set (year minus 2000)."""
    return bytes(TIME_OFFSET) + bytes(fields) + b"\x00"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ((26, 10, 25, 0, 0, 0), datetime(2026, 10, 25, 0, 0, 0)),  # midnight
        ((26, 10, 24, 23, 59, 59), datetime(2026, 10, 24, 23, 59, 59)),
        ((26, 12, 31, 23, 59, 59), datetime(2026, 12, 31, 23, 59, 59)),
        ((27, 1, 1, 0, 0, 0), datetime(2027, 1, 1, 0, 0, 0)),
        ((28, 2, 29, 12, 0, 0), datetime(2028, 2, 29, 12, 0, 0)),  # leap day
        # UK clocks go back at 02:00 BST on 25 Oct 2026: 01:30 happens twice
        # in local time. The stamp is taken as written, once, with no zone.
        ((26, 10, 25, 1, 30, 0), datetime(2026, 10, 25, 1, 30, 0)),
        # UK clocks go forward at 01:00 GMT on 29 Mar 2026: 01:30 never
        # happens in local time, but the datalogger may still write it.
        ((26, 3, 29, 1, 30, 0), datetime(2026, 3, 29, 1, 30, 0)),
    ],
)
def test_record_time_is_the_dataloggers_wall_clock(
    fields: tuple[int, ...], expected: datetime
) -> None:
    """The stamp is the datalogger's own clock, whose zone the record does not
    say (the captured one ran an hour ahead of the UTC it was given), so it is
    returned naive and never shifted for DST."""
    stamp = record_time(_stamped(*fields))
    assert stamp == expected
    assert stamp.tzinfo is None


@pytest.mark.parametrize(
    "fields",
    [
        (0, 0, 0, 0, 0, 0),  # live records: no stamp
        (27, 2, 29, 12, 0, 0),  # no 29 February in 2027
        (26, 10, 25, 24, 0, 0),  # hour 24
        (26, 13, 1, 0, 0, 0),  # month 13
        (26, 10, 25, 12, 60, 0),  # minute 60
    ],
)
def test_record_time_that_is_not_a_time_is_none(fields: tuple[int, ...]) -> None:
    assert record_time(_stamped(*fields)) is None


def test_record_time_of_a_short_payload_is_none() -> None:
    assert record_time(bytes(TIME_OFFSET + 3)) is None


def _payload(blocks: bytes, *, stamp: bytes = bytes(6)) -> bytes:
    """Two serials, a record time, then a block table."""
    serial = DATALOGGER.encode().ljust(30, b"\x00")
    return serial + INVERTER.encode().ljust(30, b"\x00") + stamp + blocks


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (_payload(b""), "too short"),
        (_payload(bytes([1]) + b"\x00\x01"), "block header past the end"),
        (_payload(bytes([1]) + b"\x00\x05\x00\x01"), "runs backwards"),
        (_payload(bytes([1]) + b"\x00\x01\x00\x02\x00"), "past the end of the record"),
    ],
)
def test_a_malformed_block_table_is_refused(payload: bytes, reason: str) -> None:
    with pytest.raises(DecodeError, match=reason):
        registers(payload)


def test_an_announce_without_registers_is_refused() -> None:
    with pytest.raises(DecodeError, match="no registers"):
        check_announce(_payload(bytes([0])))


def test_a_record_time_that_is_not_a_date_is_none() -> None:
    assert record_time(_payload(bytes([0]), stamp=bytes([26, 13, 40, 25, 61, 0]))) is None


def test_a_record_missing_a_register_is_refused() -> None:
    with pytest.raises(DecodeError, match="not in the record"):
        decode(_payload(bytes([1]) + b"\x00\x00\x00\x00" + b"\x00\x00"))
