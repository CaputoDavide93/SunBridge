"""Fixtures: a config entry on a free port, and a fake datalogger client."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunbridge.const import (
    CONF_ALLOWED_SERIALS,
    CONF_MODE,
    CONF_PORT,
    CONF_UPLOAD_INTERVAL,
    DOMAIN,
    MODE_ADVANCED,
    MODE_SIMPLE,
)
from custom_components.sunbridge.protocol import Frame, FrameBuffer


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""
    return


def make_entry(mode: str = MODE_SIMPLE, **extra: object) -> MockConfigEntry:
    # Port 0: the operating system picks a free one; the hub reports it.
    data = {CONF_PORT: 0, CONF_MODE: mode}
    if mode == MODE_ADVANCED:
        data |= {CONF_ALLOWED_SERIALS: "", CONF_UPLOAD_INTERVAL: 5}
    data |= extra
    return MockConfigEntry(domain=DOMAIN, title="SunBridge", data=data)


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, socket_enabled: None
) -> AsyncGenerator[callable]:
    """Set up entries on demand; unload them all at the end of the test."""
    entries: list[MockConfigEntry] = []

    async def _setup(entry: MockConfigEntry | None = None) -> MockConfigEntry:
        entry = entry or make_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entries.append(entry)
        return entry

    yield _setup
    for entry in entries:
        removed = hass.config_entries.async_get_entry(entry.entry_id) is None
        if not removed and entry.state.recoverable:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


class FakeDatalogger:
    """A TCP client that sends captured records and reads the replies."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self._buffer = FrameBuffer()
        self._frames: list[Frame] = []

    @classmethod
    async def connect(cls, port: int) -> FakeDatalogger:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        return cls(reader, writer)

    async def send(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def replies(self, count: int, timeout: float = 5) -> list[Frame]:
        """Wait for `count` whole reply frames."""
        async with asyncio.timeout(timeout):
            while len(self._frames) < count:
                data = await self.reader.read(4096)
                if not data:
                    raise ConnectionResetError("server closed the connection")
                self._frames += self._buffer.feed(data)
        out, self._frames = self._frames[:count], self._frames[count:]
        return out

    async def closed_by_server(self, timeout: float = 5) -> bool:
        """True once the server closes. Closing with our bytes still unread
        makes the kernel send a reset instead of a FIN; both count."""
        async with asyncio.timeout(timeout):
            while True:
                try:
                    if not await self.reader.read(4096):
                        return True
                except ConnectionResetError:
                    return True

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(ConnectionError):
            await self.writer.wait_closed()
