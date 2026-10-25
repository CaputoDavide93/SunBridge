"""A fake datalogger for the screenshots, made of the captured uploads.

Every frame comes from `tests/fixtures/captured/uploads-2026-10-25.jsonl`.
Whatever serials the capture carries are replaced by DLGEXAMPLE (the
datalogger) and INVEXAMPLE (the inverter) inside each frame, which is then
re-scrambled with its CRC recomputed, so SunBridge decodes it exactly as the
original and no other serial can reach a screenshot. In the public
repository the fixtures already carry the stand-ins and this changes nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from custom_components.sunbridge.decoder import decode
from custom_components.sunbridge.protocol import crc16_modbus, parse_frame, scramble

ROOT = Path(__file__).resolve().parents[2]
UPLOADS = ROOT / "tests" / "fixtures" / "captured" / "uploads-2026-10-25.jsonl"
STAND_INS = ("DLGEXAMPLE", "INVEXAMPLE")

ANNOUNCE, LIVE, PING = 0x03, 0x04, 0x16


@dataclass(frozen=True)
class Upload:
    at: datetime
    record_type: int
    raw: bytes


def _with_serials(raw: bytes, swaps: dict[bytes, bytes]) -> bytes:
    """The frame with each serial swapped in its payload, re-CRC'd."""
    payload = scramble(raw[8:-2])
    for real, fake in swaps.items():
        payload = payload.replace(real, fake)
    body = raw[:8] + scramble(payload)
    out = body + crc16_modbus(body).to_bytes(2, "big")
    parse_frame(out)  # raises if the result is not a valid frame
    return out


@cache
def uploads() -> tuple[Upload, ...]:
    """Every captured upload, oldest first, carrying only the stand-ins."""
    rows = [json.loads(line) for line in UPLOADS.read_text().splitlines()]
    frames = [(row["ts"], bytes.fromhex(row["raw"])) for row in rows]
    announce = next(
        parse_frame(raw)
        for _, raw in frames
        if parse_frame(raw).record_type == ANNOUNCE
    )
    real = (announce.datalogger_serial, announce.inverter_serial)
    swaps = {}
    for serial, fake in zip(real, STAND_INS, strict=True):
        if serial is None or len(serial) != len(fake):
            raise SystemExit(f"cannot replace serial {serial!r} with {fake}")
        swaps[serial.encode()] = fake.encode()
    out = []
    for ts, raw in frames:
        head, _, frac = ts.rstrip("Z").partition(".")
        at = datetime.fromisoformat(f"{head}.{frac[:6]}").replace(tzinfo=UTC)
        raw = _with_serials(raw, swaps)
        out.append(Upload(at, parse_frame(raw).record_type, raw))
    for upload in out:
        text = upload.raw.hex()
        for serial in real:
            if serial not in STAND_INS and serial.encode().hex() in text:
                raise SystemExit("a captured serial survived the replacement")
    return tuple(out)


def first(record_type: int) -> Upload:
    return next(u for u in uploads() if u.record_type == record_type)


def live_records() -> list[Upload]:
    return [u for u in uploads() if u.record_type == LIVE]


def output_power(upload: Upload) -> float:
    return decode(parse_frame(upload.raw).payload).output_power


class FakeDatalogger:
    """Connects to SunBridge like the datalogger does, and keeps reconnecting.

    Every connection opens with a ping and an announce; `send` queues more
    frames for the current (or next) connection. Replies are read and
    dropped, as the datalogger would act on them.
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self.connections = 0
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def send(self, raw: bytes) -> None:
        self._queue.put_nowait(raw)

    async def _run(self) -> None:
        hello = [first(PING).raw, first(ANNOUNCE).raw]
        while True:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            except OSError:
                await asyncio.sleep(0.5)
                continue
            self.connections += 1
            drain = asyncio.create_task(self._drain(reader))
            try:
                for raw in hello:
                    writer.write(raw)
                    await writer.drain()
                    await asyncio.sleep(0.2)
                while not drain.done():
                    try:
                        raw = await asyncio.wait_for(self._queue.get(), 1)
                    except TimeoutError:
                        continue
                    writer.write(raw)
                    await writer.drain()
            except ConnectionError, OSError:
                pass
            finally:
                drain.cancel()
                writer.close()
                with contextlib.suppress(ConnectionError, OSError):
                    await writer.wait_closed()
            await asyncio.sleep(0.5)

    @staticmethod
    async def _drain(reader: asyncio.StreamReader) -> None:
        while await reader.read(4096):
            pass
