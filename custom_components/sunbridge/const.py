"""Constants for SunBridge. Imports nothing from Home Assistant."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "sunbridge"

CONF_PORT: Final = "port"
CONF_MODE: Final = "mode"
CONF_ALLOWED_SERIALS: Final = "allowed_serials"
CONF_UPLOAD_INTERVAL: Final = "upload_interval"

MODE_SIMPLE: Final = "simple"
MODE_ADVANCED: Final = "advanced"

DEFAULT_PORT: Final = 5279
# The datalogger's own "Data Transfer Interval", in minutes. The integration
# does not set it (no captured command proves how); it only expects it.
DEFAULT_UPLOAD_INTERVAL: Final = 5
MIN_UPLOAD_INTERVAL: Final = 1
MAX_UPLOAD_INTERVAL: Final = 10

# Live measurements go unavailable when no 0x04 record has arrived for this
# long: three upload intervals, and never less than 15 minutes.
MIN_STALE_MINUTES: Final = 15
STALE_INTERVALS: Final = 3

# A connection that sends no valid frame for this long is closed; bytes that
# never make a whole, CRC-checked frame do not count. The datalogger pings
# about every two minutes (longest silence captured: 167 s).
IDLE_TIMEOUT_SECONDS: Final = 600
# A new connection must send its first valid frame within this long. The
# datalogger pings or announces as soon as it connects.
HANDSHAKE_TIMEOUT_SECONDS: Final = 30
# One datalogger normally holds one connection; a few more cover reconnects
# overlapping a connection the old side has not closed yet.
MAX_CONNECTIONS: Final = 4
# Devices one entry will create. One datalogger with one inverter is the
# expected setup; anything past these is logged and ignored.
MAX_DATALOGGERS: Final = 2
MAX_INVERTERS: Final = 2

# How long the setup's "Waiting for your datalogger" step listens before it
# offers to finish anyway or try again.
SETUP_WAIT_SECONDS: Final = 600

DEVICE_INVERTER_NAME: Final = "Solar Inverter"
DEVICE_DATALOGGER_NAME: Final = "Solar Datalogger"
MANUFACTURER: Final = "Growatt"


def stale_after_minutes(upload_interval: int) -> int:
    """How long live values stay valid without a new 0x04 record."""
    return max(MIN_STALE_MINUTES, STALE_INTERVALS * upload_interval)


def parse_serials(text: str | None) -> frozenset[str]:
    """A comma- or space-separated allow-list, upper-cased, blanks dropped."""
    if not text:
        return frozenset()
    return frozenset(
        part.strip().upper() for part in text.replace(",", " ").split() if part.strip()
    )
