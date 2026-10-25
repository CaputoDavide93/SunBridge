"""The captured fixtures, and Home Assistant's history of the same moments.

`uploads-2026-10-25.jsonl`: every record the datalogger sent (raw wire bytes).
`replies-2026-10-25.jsonl`: what the server answered.
`ha-history-2026-10-25.json`: Home Assistant's own states for the same hours.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path

CAPTURED = Path(__file__).parent / "fixtures" / "captured"

@dataclass(frozen=True)
class Record:
    ts: datetime
    raw: bytes
    plain: bytes
    record_type: int


def _ts(text: str) -> datetime:
    # Nanosecond stamps from docker; microseconds are plenty.
    head, _, frac = text.rstrip("Z").partition(".")
    return datetime.fromisoformat(f"{head}.{frac[:6]}+00:00")


@cache
def records() -> tuple[Record, ...]:
    out = []
    for line in (CAPTURED / "uploads-2026-10-25.jsonl").read_text().splitlines():
        row = json.loads(line)
        out.append(
            Record(
                ts=_ts(row["ts"]),
                raw=bytes.fromhex(row["raw"]),
                plain=bytes.fromhex(row["plain"]),
                record_type=int(row["type"], 16) & 0xFF,
            )
        )
    return tuple(out)


def of_type(record_type: int) -> list[Record]:
    return [r for r in records() if r.record_type == record_type]


def _serials() -> tuple[str, str]:
    """The two serials, as the first announce carries them (plain payload
    bytes 0-29 and 30-59, zero-padded ASCII), so no test names them."""
    payload = of_type(0x03)[0].plain[8:]
    return tuple(  # type: ignore[return-value]
        payload[start : start + 30].rstrip(b"\x00").decode("ascii")
        for start in (0, 30)
    )


DATALOGGER, INVERTER = _serials()


@cache
def responses() -> tuple[dict, ...]:
    path = CAPTURED / "replies-2026-10-25.jsonl"
    return tuple(json.loads(line) for line in path.read_text().splitlines())


@cache
def _history() -> dict[str, list[tuple[datetime, str]]]:
    data = json.loads((CAPTURED / "ha-history-2026-10-25.json").read_text())
    return {
        series[0]["entity_id"]: [
            (datetime.fromisoformat(row["last_changed"]), row["state"]) for row in series
        ]
        for series in data
    }


def _number(state: str) -> float | None:
    try:
        return float(state)
    except ValueError:
        return None


def ground_truth(entity_id: str, at: datetime) -> float | None:
    """What Home Assistant showed for the record received at `at`.

    The history stores changes only. The server of the day published each
    record within a
    second, so a change within (at - 5 s, at + 90 s] is this record's value;
    the one exception in the capture is the 14:44:46 record, published at
    14:46:09 after an MQTT restart. A value restored within 5 s of
    `unavailable`/`unknown` is a restart replaying the old state, not a new
    record, and is skipped. Without a change, the value did not change: the
    last state before `at` stands.
    """
    rows = _history()[entity_id]
    for i, (when, state) in enumerate(rows):
        restored = (
            i > 0
            and rows[i - 1][1] in ("unavailable", "unknown")
            and (when - rows[i - 1][0]).total_seconds() < 5
        )
        if (
            at - timedelta(seconds=5) < when <= at + timedelta(seconds=90)
            and not restored
            and _number(state) is not None
        ):
            return _number(state)
    last = None
    for when, state in rows:
        if when <= at and _number(state) is not None:
            last = _number(state)
    return last


def status_truth(at: datetime) -> str | None:
    """The status text Home Assistant showed at `at` (or None)."""
    last = None
    for when, state in _history()["sensor.solar_inverter_status"]:
        if when <= at + timedelta(seconds=90) and state not in ("unavailable", "unknown"):
            last = state
    return last
