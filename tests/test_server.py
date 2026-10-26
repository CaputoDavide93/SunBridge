"""The TCP server on its own: a fake owner, a real localhost socket.

These cover what the server decides by itself (redaction, deadlines, the
connection's identity, its handler tasks), without Home Assistant around it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable

import pytest

from custom_components.sunbridge import server as server_module
from custom_components.sunbridge.protocol import (
    TYPE_ANNOUNCE,
    TYPE_DATA,
    TYPE_PING,
    build_frame,
    parse_frame,
)
from custom_components.sunbridge.server import MAX_SEEN_SERIALS, SunBridge

from .captures import DATALOGGER, INVERTER, of_type
from .conftest import FakeDatalogger

ANNOUNCE = of_type(TYPE_ANNOUNCE)[0].raw
PING = of_type(TYPE_PING)[0].raw
LIVE = of_type(TYPE_DATA)[1].raw


class FakeOwner:
    """Accepts every datalogger and inverter, and remembers what it was told."""

    def __init__(self) -> None:
        self.connected: list[tuple[str, bool]] = []
        self.inverters: list[tuple[str, str]] = []
        self.readings: list = []

    def datalogger_connected(self, serial: str, connected: bool) -> bool:
        self.connected.append((serial, connected))
        return True

    def inverter_seen(self, datalogger: str, inverter: str) -> bool:
        self.inverters.append((datalogger, inverter))
        return True

    def reading(self, reading, *, live: bool) -> None:
        self.readings.append((reading, live))


@pytest.fixture
async def start(
    socket_enabled: None,
) -> AsyncGenerator[Callable[..., SunBridge]]:
    """Start servers on free ports; stop them all at the end of the test."""
    started: list[SunBridge] = []

    async def _start(owner: object | None = None, **kwargs: object) -> SunBridge:
        bridge = SunBridge(owner or FakeOwner(), 0, host="127.0.0.1", **kwargs)
        await bridge.start()
        started.append(bridge)
        return bridge

    yield _start
    for bridge in started:
        await bridge.stop()


async def test_a_full_redaction_cache_still_redacts_the_current_frame(start) -> None:
    """The cache only bounds memory: the serials of the frame being stored are
    always blanked, even after 64 others filled it (the announce repeats the
    inverter's serial among its registers, past the zeroed fields)."""
    bridge = await start()
    bridge._seen_serials.update(f"FILLER{i:04d}" for i in range(MAX_SEEN_SERIALS))
    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    await logger.replies(2)
    stored = bridge.stats.last_frames["0x03"]
    for serial in (DATALOGGER, INVERTER):
        assert serial.encode().hex() not in stored
    assert len(bridge._seen_serials) == MAX_SEEN_SERIALS
    await logger.close()


async def test_the_redaction_cache_never_grows_past_its_cap(start) -> None:
    """One slot left and a frame with two new serials: the cache stops at 64,
    it does not take both and end at 65."""
    bridge = await start()
    bridge._seen_serials.update(
        f"FILLER{i:04d}" for i in range(MAX_SEEN_SERIALS - 1)
    )
    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    await logger.replies(2)
    assert len(bridge._seen_serials) == MAX_SEEN_SERIALS
    stored = bridge.stats.last_frames["0x03"]
    for serial in (DATALOGGER, INVERTER):
        assert serial.encode().hex() not in stored
    await logger.close()


def test_redaction_never_depends_on_what_was_seen_before() -> None:
    stored = server_module._redacted(parse_frame(ANNOUNCE), set())
    for serial in (DATALOGGER, INVERTER):
        assert serial.encode().hex() not in stored


def _other_ping(serial: str = "OTHER00001") -> bytes:
    return build_frame(0x0042, TYPE_PING, serial.encode().ljust(30, b"\x00"))


def _with_serials(raw: bytes, datalogger: str, inverter: str) -> bytes:
    frame = parse_frame(raw)
    payload = (
        datalogger.encode().ljust(30, b"\x00")
        + inverter.encode().ljust(30, b"\x00")
        + frame.payload[60:]
    )
    return build_frame(frame.transaction, frame.record_type, payload)


@pytest.mark.parametrize(
    "intruder",
    [
        _other_ping(),
        _with_serials(LIVE, "OTHER00001", INVERTER),
        _with_serials(ANNOUNCE, "OTHER00001", "OTHERINV01"),
    ],
    ids=["ping", "live", "announce"],
)
async def test_a_connection_stays_bound_to_its_first_datalogger(
    start, intruder: bytes
) -> None:
    """Once identified, a connection belongs to that datalogger: any frame
    carrying another serial closes it, unanswered, and nothing is reported
    for the other serial."""
    owner = FakeOwner()
    bridge = await start(owner)
    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    await logger.replies(2)
    await logger.send(intruder)
    assert await logger.closed_by_server()
    await bridge.stop()
    assert owner.connected == [(DATALOGGER, True), (DATALOGGER, False)]
    assert owner.inverters == [(DATALOGGER, INVERTER)]
    assert owner.readings == []
    assert bridge.stats.serial_mismatches == 1
    await logger.close()


async def test_the_same_datalogger_keeps_its_connection(start) -> None:
    owner = FakeOwner()
    bridge = await start(owner)
    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    await logger.replies(2)
    await logger.send(PING)
    (pong,) = await logger.replies(1)
    assert pong.raw == PING
    await logger.send(LIVE)
    await logger.replies(1)
    assert len(owner.readings) == 1
    assert bridge.stats.serial_mismatches == 0
    await logger.close()


async def _ping_until_closed(logger: FakeDatalogger, every: float) -> None:
    with contextlib.suppress(ConnectionError):
        while True:
            await logger.send(PING)
            await asyncio.sleep(every)


async def test_pings_alone_cannot_hold_the_connection_slots(start) -> None:
    """Four clients that only ping, however validly, are closed once the
    identification deadline passes, and a real datalogger gets in."""
    bridge = await start(max_connections=4, idle_timeout=600, identify_timeout=0.5)
    squatters = [await FakeDatalogger.connect(bridge.port) for _ in range(4)]
    pingers = [
        asyncio.ensure_future(_ping_until_closed(s, 0.05)) for s in squatters
    ]
    try:
        for squatter in squatters:
            assert await squatter.closed_by_server(timeout=3)
    finally:
        for task in pingers:
            task.cancel()
        await asyncio.gather(*pingers, return_exceptions=True)
    assert bridge.stats.unidentified_timeouts == 4

    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    ack, _clock = await logger.replies(2)
    assert ack.record_type == TYPE_ANNOUNCE
    for squatter in squatters:
        await squatter.close()
    await logger.close()


async def test_an_identified_connection_outlives_the_identification_deadline(
    start,
) -> None:
    bridge = await start(idle_timeout=600, identify_timeout=0.3)
    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    await logger.replies(2)
    await asyncio.sleep(0.6)
    await logger.send(PING)
    (pong,) = await logger.replies(1)
    assert pong.raw == PING
    assert bridge.stats.unidentified_timeouts == 0
    await logger.close()


class BrokenOwner(FakeOwner):
    def datalogger_connected(self, serial: str, connected: bool) -> bool:
        raise RuntimeError("owner failed")


async def test_an_unexpected_handler_error_is_logged(start, caplog) -> None:
    """Not left as 'Task exception was never retrieved': the done callback
    takes the exception and logs it with its traceback."""
    bridge = await start(BrokenOwner())
    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    assert await logger.closed_by_server()
    async with asyncio.timeout(2):
        while bridge._tasks:
            await asyncio.sleep(0.01)
    errors = [r for r in caplog.records if r.name == server_module.__name__]
    assert [r.levelname for r in errors if r.exc_info] == ["ERROR"]
    (failed,) = [r for r in errors if r.exc_info]
    assert isinstance(failed.exc_info[1], RuntimeError)
    await logger.close()


async def test_stopping_cancels_handlers_without_an_error(start, caplog) -> None:
    bridge = await start()
    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    await logger.replies(2)
    await bridge.stop()
    assert not bridge._tasks
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    await logger.close()


async def test_the_server_writes_utc_whatever_zone_its_clock_reads(start) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    summer = datetime(2026, 7, 1, 13, 0, 5, tzinfo=ZoneInfo("Europe/London"))
    bridge = await start(now=lambda: summer)
    logger = await FakeDatalogger.connect(bridge.port)
    await logger.send(ANNOUNCE)
    _ack, clock = await logger.replies(2)
    assert clock.payload[-19:] == b"2026-07-01 12:00:05"
    await logger.close()
