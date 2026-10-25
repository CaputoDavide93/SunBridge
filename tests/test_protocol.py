"""Framing, CRC, scrambling and replies, against our own captured bytes."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from custom_components.sunbridge.protocol import (
    TYPE_ANNOUNCE,
    TYPE_BUFFERED,
    TYPE_DATA,
    TYPE_PING,
    FrameBuffer,
    FrameError,
    build_ack,
    build_clock,
    crc16_modbus,
    descramble,
    parse_frame,
    scramble,
)

from .captures import DATALOGGER, INVERTER, records, responses


def test_every_captured_record_passes_its_crc() -> None:
    frames = [parse_frame(r.raw) for r in records()]
    assert len(frames) == len(records()) > 0


def test_descrambling_matches_the_plain_copy_in_the_log() -> None:
    """Our descrambled payload equals the plain record the server printed."""
    for record in records():
        frame = parse_frame(record.raw)
        assert frame.payload == record.plain[8:-2], record.ts


def test_crc16_modbus_reference_value() -> None:
    """The standard check value: CRC-16/MODBUS of b'123456789' is 0x4B37."""
    assert crc16_modbus(b"123456789") == 0x4B37


@pytest.mark.parametrize("size", [0, 1, 6, 7, 8, 100, 1000])
def test_scramble_round_trip(size: int) -> None:
    data = bytes(random.Random(size).randrange(256) for _ in range(size))
    assert descramble(scramble(data)) == data


def test_serials_are_read_from_the_payload() -> None:
    for record in records():
        frame = parse_frame(record.raw)
        assert frame.datalogger_serial == DATALOGGER
        if frame.record_type in (TYPE_ANNOUNCE, TYPE_DATA, TYPE_BUFFERED):
            assert frame.inverter_serial == INVERTER
        else:
            assert frame.inverter_serial is None


@pytest.mark.parametrize("chunk", [1, 3, 7, 64, 585, 586, 4096])
def test_stream_reassembles_whatever_the_read_size(chunk: int) -> None:
    stream = b"".join(r.raw for r in records())
    buffer = FrameBuffer()
    frames = []
    for i in range(0, len(stream), chunk):
        frames += buffer.feed(stream[i : i + chunk])
    assert [f.raw for f in frames] == [r.raw for r in records()]
    assert buffer.pending == 0
    assert buffer.crc_failures == buffer.resyncs == 0


def test_two_and_a_half_frames_in_one_read() -> None:
    a, b, c = (r.raw for r in records()[:3])
    buffer = FrameBuffer()
    first = buffer.feed(a + b + c[:10])
    assert [f.raw for f in first] == [a, b]
    assert buffer.pending == 10
    assert [f.raw for f in buffer.feed(c[10:])] == [c]


def test_a_bad_crc_drops_that_frame_only() -> None:
    a, b = records()[0].raw, records()[1].raw
    broken = bytearray(a)
    broken[10] ^= 0xFF
    buffer = FrameBuffer()
    frames = buffer.feed(bytes(broken) + b)
    assert [f.raw for f in frames] == [b]
    assert buffer.crc_failures == 1


def test_garbage_before_a_frame_is_skipped() -> None:
    a = records()[0].raw
    buffer = FrameBuffer()
    frames = buffer.feed(b"\xde\xad\xbe\xef\x00" + a)
    assert [f.raw for f in frames] == [a]
    assert buffer.resyncs == 5


def test_parse_frame_rejects_a_wrong_length() -> None:
    with pytest.raises(FrameError):
        parse_frame(records()[0].raw + b"\x00")
    with pytest.raises(FrameError):
        parse_frame(b"\x00\x01")


def test_acks_equal_the_captured_responses() -> None:
    acks = [r for r in responses() if r["kind"] == "ack"]
    assert acks
    for row in acks:
        frame = parse_frame(bytes.fromhex(row["request"]))
        assert build_ack(frame).hex() == row["response"], row["ts"]


def test_the_documented_ack_example() -> None:
    """docs/protocol-notes.md: the 0x04 ack `00 26 00 06 00 03 01 04 47 d2 9b`."""
    request = next(
        r for r in responses() if r["kind"] == "ack" and r["response"].startswith("0026")
    )
    frame = parse_frame(bytes.fromhex(request["request"]))
    assert build_ack(frame) == bytes.fromhex("0026000600030104" "47d29b")


def test_pings_are_echoed_unchanged() -> None:
    pings = [r for r in responses() if r["kind"] == "ping"]
    assert pings
    for row in pings:
        frame = parse_frame(bytes.fromhex(row["request"]))
        assert frame.record_type == TYPE_PING
        assert frame.raw.hex() == row["response"]


def test_clock_commands_equal_the_captured_ones() -> None:
    clocks = [r for r in responses() if r["kind"] == "clock"]
    assert clocks
    for row in clocks:
        when = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        built = build_clock(DATALOGGER, when)
        assert built.hex() == row["response"], row["ts"]
        assert parse_frame(built).payload.hex() == row["plain"][16:]


def test_the_clock_is_sent_in_utc() -> None:
    """Each captured clock value is the UTC time the command was logged at."""
    for row in (r for r in responses() if r["kind"] == "clock"):
        sent = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        logged = datetime.fromisoformat(row["ts"][:19] + "+00:00")
        assert abs((logged - sent).total_seconds()) <= 1, row["ts"]


def _with_unit(raw: bytes, unit: int) -> bytes:
    body = raw[:6] + bytes([unit]) + raw[7:-2]
    return body + crc16_modbus(body).to_bytes(2, "big")


def test_parse_frame_rejects_a_unit_other_than_1() -> None:
    with pytest.raises(FrameError):
        parse_frame(_with_unit(records()[0].raw, 0x02))


def test_stream_skips_a_frame_whose_unit_is_not_1() -> None:
    a, b = records()[0].raw, records()[1].raw
    buffer = FrameBuffer()
    frames = buffer.feed(_with_unit(a, 0x07) + b)
    assert [f.raw for f in frames] == [b]


def test_crc_is_table_driven_and_matches_the_bitwise_definition() -> None:
    from custom_components.sunbridge import protocol

    def bitwise(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        return crc

    assert len(protocol._CRC_TABLE) == 256
    rng = random.Random(1)
    for size in (0, 1, 2, 57, 585, 2048):
        data = bytes(rng.randrange(256) for _ in range(size))
        assert crc16_modbus(data) == bitwise(data)


def test_a_declared_length_over_the_cap_is_a_violation() -> None:
    """The largest real frame is 585 bytes; a header claiming more than
    MAX_LENGTH is not a frame to wait for, it is a reason to hang up."""
    from custom_components.sunbridge.protocol import MAX_LENGTH

    assert MAX_LENGTH <= 2048
    header = bytes.fromhex("0001" "0006") + (MAX_LENGTH + 1).to_bytes(2, "big") + b"\x01\x04"
    buffer = FrameBuffer()
    assert buffer.feed(header + b"\x00" * 100) == []
    assert buffer.violation is not None
    assert buffer.pending == 0


def test_too_many_bad_frames_is_a_violation() -> None:
    from custom_components.sunbridge.protocol import MAX_INVALID_FRAMES

    broken = bytearray(records()[0].raw)
    broken[10] ^= 0xFF
    buffer = FrameBuffer()
    buffer.feed(bytes(broken) * MAX_INVALID_FRAMES)
    assert buffer.violation is None
    buffer.feed(bytes(broken))
    assert buffer.violation is not None


def test_too_much_garbage_is_a_violation_and_fast() -> None:
    """Skipping garbage is index-based: a large junk read is dealt with at
    once and the budget closes the stream instead of holding the bytes."""
    import time

    from custom_components.sunbridge.protocol import MAX_SKIPPED_BYTES

    junk = b"\xff" * 2_000_000
    buffer = FrameBuffer()
    started = time.perf_counter()
    assert buffer.feed(junk) == []
    assert time.perf_counter() - started < 1.0
    assert buffer.violation is not None
    assert buffer.resyncs <= MAX_SKIPPED_BYTES + 1
    assert buffer.pending == 0


def test_after_a_violation_nothing_more_is_parsed() -> None:
    buffer = FrameBuffer()
    buffer.feed(b"\xff" * 100_000)
    assert buffer.feed(records()[0].raw) == []
