"""The asyncio TCP server the datalogger uploads to.

No Home Assistant imports: it runs on the event loop it is started from and
reports through a `Listener`. Every socket read is awaited, decoding is a few
hundred bytes of arithmetic, so nothing here blocks the loop.

Per connection, for every whole frame:

- a frame from a datalogger outside the allow-list closes the connection,
  unanswered;
- 0x16 ping: echoed back unchanged (it creates nothing);
- 0x03 announce: acknowledged, then the datalogger's clock is set (UTC);
- 0x04 live data: acknowledged, decoded, reported as live;
- 0x50 buffered data: acknowledged, decoded, reported as buffered (old values);
- only a record whose payload decodes identifies the connection's datalogger
  and its inverter, and so may create a device;
- a connection that has not identified its datalogger within
  IDENTIFY_TIMEOUT_SECONDS of connecting is closed, pings or not;
- once identified, a connection belongs to that datalogger for good: any
  later frame carrying another datalogger serial closes it, unanswered;
- 0x18 / 0x19: the datalogger's answers to commands; nothing is sent back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from .decoder import DecodeError, Reading, check_announce, decode
from .protocol import (
    ACKED_TYPES,
    SERIAL_LEN,
    TYPE_ANNOUNCE,
    TYPE_DATA,
    TYPE_PING,
    TYPE_QUERY,
    TYPE_SET,
    Frame,
    FrameBuffer,
    build_ack,
    build_clock,
    serial_candidates,
)

_LOGGER = logging.getLogger(__name__)

READ_SIZE = 4096
# Bound on the serials remembered for redaction. The serial fields themselves
# are always zeroed and the frame's own serials always blanked wherever they
# appear; this cache only adds serials met in earlier frames.
MAX_SEEN_SERIALS = 64
# A connection must identify its datalogger (an announce or data record that
# decodes) within this long of connecting; valid pings before that do not
# extend it, so pings alone cannot hold a connection slot. In the captures
# every announce came at most 18 s after the ping before it, and buffered
# records follow every 30 s after a reconnect.
IDENTIFY_TIMEOUT_SECONDS = 120


class Listener(Protocol):
    """What the server tells its owner. Called on the event loop."""

    def datalogger_connected(self, serial: str, connected: bool) -> bool:
        """False refuses a new datalogger: the connection is closed."""

    def inverter_seen(self, datalogger: str, inverter: str) -> bool:
        """False refuses a new inverter: its readings are not reported."""

    def reading(self, reading: Reading, *, live: bool) -> None: ...


@dataclass
class ConnectionInfo:
    """One open connection, for diagnostics."""

    connected_at: datetime
    datalogger_serial: str | None = None
    frames: int = 0


@dataclass
class ServerStats:
    """Counters since the listener started."""

    record_types: Counter[str] = field(default_factory=Counter)
    crc_failures: int = 0
    resyncs: int = 0
    rejected: int = 0
    serial_mismatches: int = 0
    unidentified_timeouts: int = 0
    decode_errors: int = 0
    violations: int = 0
    connections_total: int = 0
    refused_connections: int = 0
    clock_sets: int = 0
    # Last descrambled frame per record type, serial fields blanked.
    last_frames: dict[str, str] = field(default_factory=dict)


def _type_name(record_type: int) -> str:
    return f"0x{record_type:02x}"


KNOWN_TYPES = frozenset({*ACKED_TYPES, TYPE_PING, TYPE_SET, TYPE_QUERY})


def _redacted(frame: Frame, seen: set[str]) -> str:
    """The descrambled frame as hex, with nothing that could be a serial.

    For a record type we do not know, nothing of the payload is kept, only
    the header. For the others the two serial fields are zeroed whatever the
    type, and the frame's own serials, plus every serial in `seen`, are
    blanked wherever they appear (the announce repeats the inverter's among
    its registers). The frame's own never depend on `seen`, which is bounded.
    """
    if frame.record_type not in KNOWN_TYPES:
        withheld = len(frame.payload)
        return f"{frame.raw[:8].hex()} (payload of {withheld} bytes withheld)"
    payload = bytearray(frame.payload)
    payload[: 2 * SERIAL_LEN] = bytes(min(len(payload), 2 * SERIAL_LEN))
    clean = bytes(payload)
    for serial in {*serial_candidates(frame), *seen}:
        encoded = serial.encode("ascii")
        clean = clean.replace(encoded, bytes(len(encoded)))
    return (frame.raw[:8] + clean).hex()


class SunBridge:
    """Listens for dataloggers on one TCP port."""

    def __init__(
        self,
        listener: Listener,
        port: int,
        *,
        host: str | None = None,
        allowed_serials: frozenset[str] = frozenset(),
        idle_timeout: float = 600,
        handshake_timeout: float = 30,
        identify_timeout: float = IDENTIFY_TIMEOUT_SECONDS,
        max_connections: int = 4,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._listener = listener
        self.port = port
        self._host = host
        self._allowed = allowed_serials
        self._idle_timeout = idle_timeout
        self._handshake_timeout = handshake_timeout
        self._identify_timeout = identify_timeout
        self._max_connections = max_connections
        self._now = now
        self._server: asyncio.Server | None = None
        self._closing = False
        # Serials met in frames, only to blank them out of diagnostics.
        self._seen_serials: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self.connections: dict[int, ConnectionInfo] = {}
        self.stats = ServerStats()

    async def start(self) -> None:
        """Bind and start accepting. Raises OSError if the port is taken."""
        self._server = await asyncio.start_server(
            self._accepted, host=self._host, port=self.port, reuse_address=True
        )
        if self.port == 0:  # tests ask for any free port: report the IPv4 one
            self.port = next(
                sock.getsockname()[1]
                for sock in self._server.sockets
                if sock.family == socket.AF_INET
            )

    async def stop(self) -> None:
        """Stop accepting, close every connection, wait for the handlers."""
        self._closing = True
        if self._server is not None:
            self._server.close()
            self._server.close_clients()
        for writer in list(self._writers):
            writer.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

    def _accepted(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._closing:  # accepted from the backlog while stopping
            writer.close()
            return
        if len(self._tasks) >= self._max_connections:
            self.stats.refused_connections += 1
            _LOGGER.warning(
                "Refusing a connection: %s already open", self._max_connections
            )
            writer.close()
            return
        task = asyncio.get_running_loop().create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._handler_done)

    def _handler_done(self, task: asyncio.Task[None]) -> None:
        """Forget a finished handler, and log any error it did not expect.

        Retrieving the exception here is what keeps it from surfacing later as
        "Task exception was never retrieved". A cancelled handler (stopping)
        is not an error.
        """
        self._tasks.discard(task)
        if task.cancelled():
            return
        if (err := task.exception()) is not None:
            _LOGGER.error(
                "A datalogger connection failed unexpectedly", exc_info=err
            )

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.stats.connections_total += 1
        info = ConnectionInfo(connected_at=self._now())
        key = id(writer)
        self.connections[key] = info
        self._writers.add(writer)
        buffer = FrameBuffer()
        loop = asyncio.get_running_loop()
        # Only whole, CRC-checked frames move the deadline, never bare bytes,
        # and until the datalogger is identified, never past identify_by.
        identify_by = loop.time() + self._identify_timeout
        deadline = min(loop.time() + self._handshake_timeout, identify_by)
        try:
            while True:
                remaining = deadline - loop.time()
                try:
                    if remaining <= 0:
                        raise TimeoutError
                    data = await asyncio.wait_for(reader.read(READ_SIZE), remaining)
                except TimeoutError:
                    if info.datalogger_serial is None and info.frames:
                        self.stats.unidentified_timeouts += 1
                        _LOGGER.debug(
                            "Closing a connection that never identified a "
                            "datalogger in time"
                        )
                    else:
                        _LOGGER.debug(
                            "Closing a connection with no valid frame in time"
                        )
                    break
                if not data:
                    break
                crc, resync = buffer.crc_failures, buffer.resyncs
                frames = buffer.feed(data)
                self.stats.crc_failures += buffer.crc_failures - crc
                self.stats.resyncs += buffer.resyncs - resync
                keep_open = buffer.violation is None
                if not keep_open:
                    self.stats.violations += 1
                    _LOGGER.warning(
                        "Closing a connection that broke the protocol: %s",
                        buffer.violation,
                    )
                for frame in frames if keep_open else ():
                    if not await self._handle(frame, info, writer):
                        keep_open = False
                        break
                if not keep_open:
                    break
                if frames:
                    deadline = loop.time() + self._idle_timeout
                    if info.datalogger_serial is None:
                        deadline = min(deadline, identify_by)
        except (ConnectionError, OSError) as err:
            _LOGGER.debug("Connection dropped: %s", err)
        finally:
            self._writers.discard(writer)
            self.connections.pop(key, None)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if info.datalogger_serial is not None:
                self._listener.datalogger_connected(info.datalogger_serial, False)

    async def _handle(
        self, frame: Frame, info: ConnectionInfo, writer: asyncio.StreamWriter
    ) -> bool:
        """Answer one frame. False closes the connection."""
        name = _type_name(frame.record_type)
        serial = frame.datalogger_serial
        if self._allowed and serial not in self._allowed:
            self.stats.rejected += 1
            _LOGGER.warning(
                "Closing a connection from a datalogger not in the allow-list"
            )
            return False

        bound = info.datalogger_serial
        if bound is not None and serial != bound:
            self.stats.serial_mismatches += 1
            _LOGGER.warning(
                "Closing a connection that switched to another datalogger serial"
            )
            return False

        info.frames += 1
        self.stats.record_types[name] += 1
        # One at a time: a frame with two new serials must not overshoot.
        for candidate in serial_candidates(frame):
            if len(self._seen_serials) >= MAX_SEEN_SERIALS:
                break
            self._seen_serials.add(candidate)
        self.stats.last_frames[name] = _redacted(frame, self._seen_serials)

        if frame.record_type == TYPE_PING:
            await self._send(writer, frame.raw)
            return True
        if frame.record_type in (TYPE_SET, TYPE_QUERY):
            return True
        if frame.record_type not in ACKED_TYPES:
            _LOGGER.debug("Ignoring record type %s", name)
            return True

        await self._send(writer, build_ack(frame))

        # The serials prove nothing until the payload decodes: only then may
        # the connection be identified, or a device created.
        reading: Reading | None = None
        try:
            if frame.record_type == TYPE_ANNOUNCE:
                datalogger, inverter = check_announce(frame.payload)
            else:
                reading = decode(frame.payload)
                datalogger = reading.datalogger_serial
                inverter = reading.inverter_serial
        except DecodeError as err:
            self.stats.decode_errors += 1
            _LOGGER.warning("Could not decode a %s record: %s", name, err)
            return True

        if info.datalogger_serial is None:
            if not self._listener.datalogger_connected(datalogger, True):
                return False
            info.datalogger_serial = datalogger
        if not self._listener.inverter_seen(datalogger, inverter):
            return True

        if frame.record_type == TYPE_ANNOUNCE:
            await self._send(writer, build_clock(datalogger, self._now()))
            self.stats.clock_sets += 1
        elif reading is not None:
            self._listener.reading(reading, live=frame.record_type == TYPE_DATA)
        return True

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(data)
        await writer.drain()
