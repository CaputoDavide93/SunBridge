"""Decode a data record (0x04 live, 0x50 buffered) into inverter readings.

Pure Python, no Home Assistant imports. Every offset here was found by matching
our own captured records against Home Assistant's history of the same moments;
docs/data-layout.md says which records and which values proved each one.

After the two 30-byte serials the payload carries:

    60  6 bytes   record time, y-2000 m d h m s; all zero in live 0x04 records
    66  1 byte    number of register blocks (2 in every capture)
    67  per block start register (2), end register (2), then 2 bytes each

Values are big-endian unsigned registers; 32-bit values span two registers,
high word first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .protocol import SERIAL_LEN

TIME_OFFSET = 2 * SERIAL_LEN
BLOCKS_OFFSET = TIME_OFFSET + 6

# Register numbers, as the record's own block headers number them.
REG_STATUS = 3000
REG_PV_POWER = 3001  # 32-bit, 0.1 W
REG_PV1_VOLTAGE = 3003  # 0.1 V
REG_PV1_CURRENT = 3004  # 0.1 A
REG_OUTPUT_POWER = 3023  # 32-bit, 0.1 W
REG_GRID_FREQUENCY = 3025  # 0.01 Hz
REG_GRID_VOLTAGE = 3026  # 0.1 V
REG_GRID_CURRENT = 3027  # 0.1 A
REG_WORKING_TIME = 3047  # 32-bit, 0.5 s
REG_ENERGY_TODAY = 3049  # 32-bit, 0.1 kWh
REG_ENERGY_TOTAL = 3051  # 32-bit, 0.1 kWh
REG_TEMPERATURE = 3093  # 0.1 °C
REG_TEMPERATURE_2 = 3097  # 0.1 °C, unverified: no ground truth to match

STATUS_WAITING = "waiting"
STATUS_PRODUCING = "producing"
STATUS_FAULT = "fault"
STATUS_CODES = {0: STATUS_WAITING, 1: STATUS_PRODUCING, 3: STATUS_FAULT}


class DecodeError(ValueError):
    """A data record whose layout is not the one we know."""


@dataclass(frozen=True, slots=True)
class Reading:
    """One data record's values, in display units."""

    datalogger_serial: str
    inverter_serial: str
    record_time: datetime | None
    status_code: int
    status: str | None
    output_power: float
    pv_power: float
    pv_voltage: float
    pv_current: float
    grid_frequency: float
    grid_voltage: float
    grid_current: float
    energy_today: float
    energy_total: float
    temperature: float
    temperature_2: float
    working_time_hours: float


def serials(payload: bytes) -> tuple[str, str]:
    """The datalogger and inverter serials. Raises DecodeError."""
    return _serial(payload[:SERIAL_LEN]), _serial(payload[SERIAL_LEN : 2 * SERIAL_LEN])


def check_announce(payload: bytes) -> tuple[str, str]:
    """An announce's serials, once its whole register table parses."""
    found = serials(payload)
    if not registers(payload):
        raise DecodeError("announce carries no registers")
    return found


def registers(payload: bytes) -> dict[int, int]:
    """Every register the record carries, by number. Raises DecodeError."""
    if len(payload) < BLOCKS_OFFSET + 1:
        raise DecodeError("record too short for its block table")
    count = payload[BLOCKS_OFFSET]
    pos = BLOCKS_OFFSET + 1
    regs: dict[int, int] = {}
    for _ in range(count):
        if len(payload) < pos + 4:
            raise DecodeError("block header past the end of the record")
        start = int.from_bytes(payload[pos : pos + 2], "big")
        end = int.from_bytes(payload[pos + 2 : pos + 4], "big")
        pos += 4
        if end < start:
            raise DecodeError(f"block {start}..{end} runs backwards")
        size = (end - start + 1) * 2
        if len(payload) < pos + size:
            raise DecodeError(f"block {start}..{end} past the end of the record")
        for i in range(end - start + 1):
            at = pos + 2 * i
            regs[start + i] = int.from_bytes(payload[at : at + 2], "big")
        pos += size
    return regs


def record_time(payload: bytes) -> datetime | None:
    """The record's own time stamp (the datalogger's clock), if it has one."""
    raw = payload[TIME_OFFSET:BLOCKS_OFFSET]
    if len(raw) < 6 or not any(raw):
        return None
    year, month, day, hour, minute, second = raw
    try:
        return datetime(2000 + year, month, day, hour, minute, second)
    except ValueError:
        return None


def decode(payload: bytes) -> Reading:
    """A descrambled 0x04 or 0x50 payload as a Reading. Raises DecodeError."""
    datalogger, inverter = serials(payload)
    regs = registers(payload)

    def u16(reg: int) -> int:
        try:
            return regs[reg]
        except KeyError:
            raise DecodeError(f"register {reg} not in the record") from None

    def u32(reg: int) -> int:
        return (u16(reg) << 16) | u16(reg + 1)

    status_code = u16(REG_STATUS)
    return Reading(
        datalogger_serial=datalogger,
        inverter_serial=inverter,
        record_time=record_time(payload),
        status_code=status_code,
        status=STATUS_CODES.get(status_code),
        output_power=u32(REG_OUTPUT_POWER) / 10,
        pv_power=u32(REG_PV_POWER) / 10,
        pv_voltage=u16(REG_PV1_VOLTAGE) / 10,
        pv_current=u16(REG_PV1_CURRENT) / 10,
        grid_frequency=u16(REG_GRID_FREQUENCY) / 100,
        grid_voltage=u16(REG_GRID_VOLTAGE) / 10,
        grid_current=u16(REG_GRID_CURRENT) / 10,
        energy_today=u32(REG_ENERGY_TODAY) / 10,
        energy_total=u32(REG_ENERGY_TOTAL) / 10,
        temperature=u16(REG_TEMPERATURE) / 10,
        temperature_2=u16(REG_TEMPERATURE_2) / 10,
        working_time_hours=u32(REG_WORKING_TIME) / 2 / 3600,
    )


def _serial(chunk: bytes) -> str:
    text = chunk.rstrip(b"\x00")
    if not text or not all(0x20 < b < 0x7F for b in text):
        raise DecodeError("no readable serial")
    return text.decode("ascii")
