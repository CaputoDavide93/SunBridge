"""The datalogger's TCP framing: CRC, scrambling, stream reassembly, replies.

Pure Python, no Home Assistant imports, so every rule here is tested on its
own against the bytes captured from our own datalogger (docs/protocol-notes.md).

A frame on the wire:

    0-1   transaction id (big-endian), echoed in the reply
    2-3   protocol, 0x0006 on the ShineLan-X we captured
    4-5   length of what follows this 6-byte header, CRC excluded
    6     unit id, 0x01
    7     record type
    8..   payload, XOR-scrambled with the repeating key b"Growatt"
    -2..  CRC-16/Modbus over every byte before it, big-endian
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

KEY = b"Growatt"
HEADER_LEN = 8  # 6-byte header + unit id + record type
CRC_LEN = 2
PROTOCOL = 0x0006
UNIT_ID = 0x01
# The longest record captured is 585 bytes. A header declaring more than this
# is not a frame worth waiting for: the connection is dropped.
MAX_LENGTH = 2048
# Per-connection budgets: past either, the stream is abusive or hopelessly out
# of step, and the connection is dropped.
MAX_INVALID_FRAMES = 8
MAX_SKIPPED_BYTES = 1024

TYPE_ANNOUNCE = 0x03
TYPE_DATA = 0x04
TYPE_PING = 0x16
TYPE_SET = 0x18
TYPE_QUERY = 0x19
TYPE_BUFFERED = 0x50

ACKED_TYPES = frozenset({TYPE_ANNOUNCE, TYPE_DATA, TYPE_BUFFERED})

SERIAL_LEN = 30
REGISTER_CLOCK = 0x001F
# The clock command's transaction id, as the server we observed sent it.
CLOCK_TRANSACTION = 0x0001


def _crc_table() -> tuple[int, ...]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


_CRC_TABLE = _crc_table()


def crc16_modbus(data: bytes) -> int:
    """CRC-16/Modbus: reflected polynomial 0xA001, initial value 0xFFFF."""
    crc = 0xFFFF
    table = _CRC_TABLE
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return crc


def scramble(data: bytes) -> bytes:
    """XOR with the repeating key. Its own inverse, so it also descrambles."""
    return bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(data))


descramble = scramble


@dataclass(frozen=True, slots=True)
class Frame:
    """One whole, CRC-checked frame, with its payload already descrambled."""

    transaction: int
    protocol: int
    unit: int
    record_type: int
    payload: bytes
    raw: bytes = field(repr=False)

    @property
    def datalogger_serial(self) -> str | None:
        """The ASCII serial every logger record starts with, if present."""
        return _ascii(self.payload[:SERIAL_LEN])

    @property
    def inverter_serial(self) -> str | None:
        """The inverter serial that follows it in 0x03/0x04/0x50 records."""
        if self.record_type not in ACKED_TYPES:
            return None
        return _ascii(self.payload[SERIAL_LEN : 2 * SERIAL_LEN])


def valid_serial(text: str) -> bool:
    """Whether `text` can be a serial a frame carries: 1 to 30 ASCII letters
    or digits. A longer one would not fit the 30-byte field, so it could never
    match a datalogger's frame."""
    return 0 < len(text) <= SERIAL_LEN and text.isascii() and text.isalnum()


def serial_candidates(frame: Frame) -> list[str]:
    """Whatever readable text sits in either serial field, whatever the type.

    For redaction only: it errs towards treating text as a serial.
    """
    found = (
        _ascii(frame.payload[:SERIAL_LEN]),
        _ascii(frame.payload[SERIAL_LEN : 2 * SERIAL_LEN]),
    )
    return [text for text in found if text]


def _ascii(chunk: bytes) -> str | None:
    text = chunk.rstrip(b"\x00")
    if not text or not all(0x20 < b < 0x7F for b in text):
        return None
    return text.decode("ascii")


class FrameError(ValueError):
    """A frame that cannot be taken apart."""


def _checked(raw: bytes) -> Frame:
    if raw[6] != UNIT_ID:
        raise FrameError(f"unit id 0x{raw[6]:02x}, not 0x{UNIT_ID:02x}")
    if crc16_modbus(raw[:-CRC_LEN]) != int.from_bytes(raw[-CRC_LEN:], "big"):
        raise FrameError("bad CRC")
    return Frame(
        transaction=int.from_bytes(raw[0:2], "big"),
        protocol=int.from_bytes(raw[2:4], "big"),
        unit=raw[6],
        record_type=raw[7],
        payload=descramble(raw[HEADER_LEN:-CRC_LEN]),
        raw=raw,
    )


def parse_frame(raw: bytes) -> Frame:
    """One complete frame from its exact bytes. Raises FrameError."""
    if len(raw) < HEADER_LEN + CRC_LEN:
        raise FrameError("too short")
    if int.from_bytes(raw[4:6], "big") + 6 + CRC_LEN != len(raw):
        raise FrameError("length field does not match")
    return _checked(raw)


@dataclass
class FrameBuffer:
    """Turns a TCP byte stream into frames, whatever the read boundaries.

    A read may hold half a frame, or two and a half. Bytes wait here until a
    whole frame is present, and never more than one frame's worth. A frame
    whose CRC fails is dropped and counted; a header that cannot be a frame
    (wrong protocol or unit, impossible length) means the stream is out of
    step, so one byte is skipped until a header fits again.

    Parsing walks an index and trims the buffer once per read. Three things
    end the stream for good, recorded in `violation`: a header declaring more
    than MAX_LENGTH bytes, more than MAX_INVALID_FRAMES bad frames, or more
    than MAX_SKIPPED_BYTES skipped. The owner then closes the connection.
    """

    crc_failures: int = 0
    resyncs: int = 0
    violation: str | None = None
    _buf: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> list[Frame]:
        if self.violation is not None:
            return []
        buf = self._buf
        buf.extend(data)
        frames: list[Frame] = []
        pos, end = 0, len(buf)
        while end - pos >= 7:
            protocol = (buf[pos + 2] << 8) | buf[pos + 3]
            length = (buf[pos + 4] << 8) | buf[pos + 5]
            if protocol != PROTOCOL or buf[pos + 6] != UNIT_ID or length < 2:
                pos += 1
                self.resyncs += 1
                if self.resyncs > MAX_SKIPPED_BYTES:
                    self.violation = f"more than {MAX_SKIPPED_BYTES} bytes skipped"
                    break
                continue
            if length > MAX_LENGTH:
                self.violation = f"a header declaring {length} bytes"
                break
            total = 6 + length + CRC_LEN
            if end - pos < total:
                break
            raw = bytes(buf[pos : pos + total])
            pos += total
            try:
                frames.append(_checked(raw))
            except FrameError:
                self.crc_failures += 1
                if self.crc_failures > MAX_INVALID_FRAMES:
                    self.violation = f"more than {MAX_INVALID_FRAMES} bad frames"
                    break
        if self.violation is not None:
            buf.clear()
        else:
            del buf[:pos]
        return frames

    @property
    def pending(self) -> int:
        """Bytes held back waiting for the rest of a frame."""
        return len(self._buf)


def build_frame(
    transaction: int, record_type: int, plain_payload: bytes, protocol: int = PROTOCOL
) -> bytes:
    """Header, scrambled payload and CRC, ready for the wire."""
    body = (
        transaction.to_bytes(2, "big")
        + protocol.to_bytes(2, "big")
        + (len(plain_payload) + 2).to_bytes(2, "big")
        + bytes([UNIT_ID, record_type])
        + scramble(plain_payload)
    )
    return body + crc16_modbus(body).to_bytes(2, "big")


def build_ack(frame: Frame) -> bytes:
    """The reply to 0x03, 0x04 and 0x50: same id and type, one 0x00 byte."""
    return build_frame(frame.transaction, frame.record_type, b"\x00", frame.protocol)


def build_clock(datalogger_serial: str, now_utc: datetime) -> bytes:
    """Set the datalogger's clock (register 0x1F) to `now_utc`, in UTC.

    The value is always the UTC wall time, to the second (fractions dropped),
    as the server we observed sent it: an aware time in any other zone is
    converted first, so the value never jumps when local clocks change. A
    naive time is refused, since its zone is unknown. What the datalogger
    then does with the value (the one we captured stamped its buffered
    records an hour ahead of UTC) is its own affair; see decoder.record_time.
    """
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("the clock needs a time zone aware time")
    value = now_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S").encode("ascii")
    serial = datalogger_serial.encode("ascii")[:SERIAL_LEN].ljust(SERIAL_LEN, b"\x00")
    payload = (
        serial
        + REGISTER_CLOCK.to_bytes(2, "big")
        + len(value).to_bytes(2, "big")
        + value
    )
    return build_frame(CLOCK_TRANSACTION, TYPE_SET, payload)
