"""Diagnostics: what the listener has seen, with every serial left out.

Serials identify the household's hardware, so they never appear: devices are
numbered instead, the allow-list is reported by its size, and the last frame
of each known record type is the descrambled frame with its serial fields
zeroed (an unknown type keeps its header only).
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import ShineConfigEntry
from .const import CONF_ALLOWED_SERIALS
from .hub import entry_settings


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ShineConfigEntry
) -> dict[str, Any]:
    """Counters, connections and per-inverter state, with no serials."""
    settings = dict(entry_settings(entry))
    settings[CONF_ALLOWED_SERIALS] = (
        f"{len(entry_settings(entry)[CONF_ALLOWED_SERIALS].split(','))} serial(s)"
        if settings[CONF_ALLOWED_SERIALS]
        else "off"
    )
    hub = getattr(entry, "runtime_data", None)
    if hub is None:
        return {
            "entry": {
                "settings": settings,
                "state": entry.state.value,
                "reason": entry.reason,
            }
        }

    stats = hub.server.stats
    now = dt_util.utcnow()
    return {
        "entry": {"settings": settings, "state": entry.state.value},
        "listener": {
            "port": hub.server.port,
            "stale_after_minutes": hub.stale_after.total_seconds() / 60,
            "connections_open": len(hub.server.connections),
            "connections_total": stats.connections_total,
            "connections_refused_over_cap": stats.refused_connections,
            "connections": [
                {
                    "open_seconds": round((now - info.connected_at).total_seconds()),
                    "frames": info.frames,
                    "identified": info.datalogger_serial is not None,
                }
                for info in hub.server.connections.values()
            ],
        },
        "records": {
            "by_type": dict(sorted(stats.record_types.items())),
            "crc_failures": stats.crc_failures,
            "resyncs": stats.resyncs,
            "connections_dropped_for_protocol_violations": stats.violations,
            "rejected_by_allow_list": stats.rejected,
            "decode_errors": stats.decode_errors,
            "clock_sets": stats.clock_sets,
            "live_applied": hub.counters.live_records,
            "buffered_ignored": hub.counters.buffered_records,
            "lifetime_decreases_ignored": hub.counters.lifetime_decreases_ignored,
            "dataloggers_refused_over_cap": hub.counters.refused_dataloggers,
            "inverters_ignored_over_cap": hub.counters.refused_inverters,
            "last_frame_by_type": dict(sorted(stats.last_frames.items())),
        },
        "dataloggers": len(hub.dataloggers),
        "inverters": [
            {
                "live": hub.is_live(state.serial),
                "last_live_at": _iso(state.live_at),
                "energy_total": state.energy_total,
                "status_code": state.live.status_code if state.live else None,
                "buffered_records": state.buffered_records,
                "last_buffered_record_time": _iso(state.last_buffered_time),
                "lifetime_decreases_ignored": state.lifetime_decreases_ignored,
            }
            for state in hub.inverters.values()
        ],
    }
