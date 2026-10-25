"""The screenshot instance's stand-ins. Never installed anywhere real.

`tools/screenshots/take_screenshots.py` copies this into the throwaway Home
Assistant it starts, beside SunBridge. It does two things:

- The network adapters Home Assistant reports become one adapter with the
  documentation address 192.0.2.10, so the setup's "Home Assistant's address"
  is detected as that, exactly as a real network adapter would be, and no
  address of the machine taking the screenshots reaches an image.
- A `screenshot_env.backfill` action writes earlier states of an entity with
  their own timestamps, so a history graph has hours to show. The values are
  the captured records' own, decoded by SunBridge; only their times are moved.
  The recorder then compiles the five-minute statistics of those past
  periods, which is what the entity's history card draws.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.components.network import async_get_network
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.tasks import StatisticsTask
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

DOMAIN = "screenshot_env"
ADDRESS = "192.0.2.10"

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        # [[ISO time, state], ...], oldest first.
        vol.Required("points"): [vol.ExactSequence([cv.string, cv.string])],
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    network = await async_get_network(hass)
    network.adapters = [
        {
            "name": "eth0",
            "index": 1,
            "enabled": True,
            "auto": True,
            "default": True,
            "ipv4": [{"address": ADDRESS, "network_prefix": 24}],
            "ipv6": [],
        }
    ]

    async def backfill(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        current = hass.states.get(entity_id)
        attributes = dict(current.attributes) if current else {}
        times = []
        for when, state in call.data["points"]:
            times.append(datetime.fromisoformat(when))
            hass.states.async_set(
                entity_id,
                state,
                attributes,
                force_update=True,
                timestamp=times[-1].timestamp(),
            )
        recorder = get_instance(hass)
        await recorder.async_block_till_done()
        period = timedelta(minutes=5)
        start = times[0].replace(
            minute=times[0].minute // 5 * 5, second=0, microsecond=0
        )
        # Periods already compiled since start-up are skipped by the recorder.
        while start + period <= dt_util.utcnow():
            recorder.queue_task(StatisticsTask(start, False))
            start += period
        await recorder.async_block_till_done()

    hass.services.async_register(DOMAIN, "backfill", backfill, BACKFILL_SCHEMA)
    return True
