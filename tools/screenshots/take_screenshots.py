#!/usr/bin/env python3
"""Take the README and setup screenshots from a real, throwaway Home Assistant.

    make screenshots                 # builds .venv-screenshots on first use
    .venv-screenshots/bin/python tools/screenshots/take_screenshots.py [--keep]

1. Starts Home Assistant (the version pinned in requirements.txt beside this
   file) in a new temporary config directory on a free localhost port, with
   SunBridge and the `screenshot_env` stand-ins copied into
   custom_components/. Onboarding is completed through its own API.
2. Opens the guided setup in the real frontend (Chromium, through
   Playwright), and photographs every screen in the light and the dark theme.
   The port screen is taken as it opens (5279, the default); SunBridge then
   listens on 15279, so a real listener on 5279 is never in the way, and the
   later screens show that port. While the waiting screen shows, a fake
   datalogger (replay.py) sends the captured announce, so setup really finds
   one.
3. After setup, it writes the captured live records' power into the power
   sensor's history (their times moved to end as Home Assistant started),
   sends the last live record over the socket, and photographs both devices,
   the power sensor's history and the Energy dashboard's solar setting.

Every image is docs/assets/screenshots/<screen>-{light,dark}.png, re-saved
without any metadata and quantised to 256 colours. The data is demo data
only: the serials are DLGEXAMPLE and INVEXAMPLE, and the address Home
Assistant detects is 192.0.2.10.
Nothing outside the temporary directory is touched; --keep leaves it behind
for a look.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import aiohttp  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from playwright.async_api import Locator, Page, async_playwright  # noqa: E402
from replay import (  # noqa: E402  (beside this file)
    FakeDatalogger,
    live_records,
    output_power,
)

HERE = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "assets" / "screenshots"
SUNBRIDGE_PORT = 15279
VIEWPORT = {"width": 1280, "height": 900}
SCALE = 2
THEMES = ("light", "dark")
USER = {"name": "Demo", "username": "demo", "password": "demo-password-0000"}
POWER = "sensor.solar_inverter_power"
ADDRESS = "192.0.2.10"  # the documentation address Home Assistant must report

CONFIGURATION = """\
homeassistant:
  name: Home
  unit_system: metric
  time_zone: UTC
  currency: EUR
  country: NL
  latitude: 52.37
  longitude: 4.89
  elevation: 0
http:
  server_host: 127.0.0.1
  server_port: {port}
frontend:
config:
history:
energy:
logger:
  default: warning
screenshot_env:
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def prepare(config: Path, port: int) -> None:
    (config / "configuration.yaml").write_text(CONFIGURATION.format(port=port))
    # Every onboarding step but the owner is marked done before the start:
    # the core-config step would otherwise set up example integrations this
    # instance has no use for. The owner is created through the API below.
    storage = config / ".storage"
    storage.mkdir()
    (storage / "onboarding").write_text(
        json.dumps(
            {
                "version": 4,
                "minor_version": 1,
                "key": "onboarding",
                "data": {"done": ["core_config", "analytics", "integration"]},
            }
        )
    )
    components = config / "custom_components"
    components.mkdir()
    shutil.copytree(
        ROOT / "custom_components" / "sunbridge",
        components / "sunbridge",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        HERE / "screenshot_env",
        components / "screenshot_env",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


async def wait_for_http(session: aiohttp.ClientSession, base: str, proc) -> None:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit("Home Assistant exited during start-up; see its log")
        try:
            async with session.get(f"{base}/api/onboarding") as resp:
                if resp.status == 200:
                    return
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(1)
    raise SystemExit("Home Assistant did not start within five minutes")


async def onboard(session: aiohttp.ClientSession, base: str) -> dict:
    """The owner, through the onboarding API; returns the browser's tokens."""
    client_id = f"{base}/"
    async with session.post(
        f"{base}/api/onboarding/users",
        json={**USER, "client_id": client_id, "language": "en"},
    ) as resp:
        resp.raise_for_status()
        code = (await resp.json())["auth_code"]
    async with session.post(
        f"{base}/auth/token",
        data={"grant_type": "authorization_code", "code": code, "client_id": client_id},
    ) as resp:
        resp.raise_for_status()
        tokens = await resp.json()
    return {
        **tokens,
        "hassUrl": base,
        "clientId": client_id,
        "expires": int(time.time() * 1000) + tokens["expires_in"] * 1000,
    }


async def settle(session: aiohttp.ClientSession, base: str, token: str) -> None:
    """Keep the HTTP settings from configuration.yaml for good (otherwise they
    are a five-minute trial that asks to be confirmed on every page), and
    dismiss the repairs that only say YAML is deprecated, so no badge or
    dialog that has nothing to do with SunBridge reaches a picture."""
    async with session.ws_connect(f"{base}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        if (await ws.receive_json())["type"] != "auth_ok":
            raise SystemExit("websocket authentication failed")
        ids = iter(range(1, 1_000_000))

        async def call(message: dict):
            await ws.send_json({"id": next(ids), **message})
            reply = await ws.receive_json()
            if not reply.get("success"):
                raise SystemExit(f"{message['type']} failed: {reply}")
            return reply["result"]

        # The HTTP server answers before start-up has finished; the stand-ins
        # must be in place before anything is photographed.
        for _ in range(600):
            config = await call({"type": "get_config"})
            if (
                config["state"] == "RUNNING"
                and "screenshot_env" in config["components"]
            ):
                break
            await asyncio.sleep(1)
        else:
            raise SystemExit("Home Assistant did not finish starting")
        adapters = (await call({"type": "network"}))["adapters"]
        addresses = [ip["address"] for a in adapters for ip in a["ipv4"] + a["ipv6"]]
        if addresses != [ADDRESS]:
            raise SystemExit(
                f"the stand-in network adapter is not in place: {addresses}"
            )
        await call({"type": "http/config/promote"})
        for issue in (await call({"type": "repairs/list_issues"}))["issues"]:
            await call(
                {
                    "type": "repairs/ignore_issue",
                    "domain": issue["domain"],
                    "issue_id": issue["issue_id"],
                    "ignore": True,
                }
            )


# ── pictures ──────────────────────────────────────────────────────────────────
def strip_metadata(path: Path, radius: float = 0) -> None:
    """Re-save the PNG with pixels only: no text chunks, times or profiles,
    quantised to 256 colours (flat interface colours lose nothing visible,
    and the file shrinks to about a third).

    With a radius, the corners outside the dialog's rounded edge become
    transparent, so no stray piece of the page behind it shows."""
    with Image.open(path) as image:
        pixels = image.convert("RGBA" if radius else "RGB")
    if radius:
        mask = Image.new("L", pixels.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, pixels.width - 1, pixels.height - 1), radius=radius * SCALE, fill=255
        )
        pixels.putalpha(mask)
    pixels = pixels.quantize(
        256,
        method=Image.Quantize.FASTOCTREE if radius else Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    buffer = io.BytesIO()
    pixels.save(buffer, format="PNG", optimize=True)
    path.write_bytes(buffer.getvalue())


class Camera:
    def __init__(self, page: Page, only: set[str] | None) -> None:
        self.page = page
        self.only = only
        self.taken: list[Path] = []

    async def theme(self, name: str) -> None:
        await self.page.emulate_media(color_scheme=name)
        await self.page.wait_for_timeout(600)

    async def shoot(self, name: str, target: Locator | None = None) -> None:
        """<name>-light.png and <name>-dark.png, of a dialog or the viewport."""
        if self.only and name not in self.only:
            return
        for theme in THEMES:
            await self.theme(theme)
            path = OUT / f"{name}-{theme}.png"
            radius = 0.0
            if target is None:
                await self.page.screenshot(path=path, animations="disabled")
            else:
                radius = await target.evaluate(
                    "e => parseFloat(getComputedStyle(e).borderTopLeftRadius) || 0"
                )
                box = await target.bounding_box()
                if box is None:
                    raise SystemExit(f"{name}: nothing to photograph")
                await self.page.screenshot(path=path, clip=box, animations="disabled")
            strip_metadata(path, radius)
            self.taken.append(path)
            print(f"  {path.relative_to(ROOT)}")
        await self.theme("light")


def dialog(page: Page, host: str = "dialog-data-entry-flow") -> Locator:
    """The visible surface of an open dialog."""
    return page.locator(f"{host} ha-dialog wa-dialog dialog.dialog").first


async def screen(page: Page, title: str) -> None:
    """Wait for a dialog screen by its title, and for it to settle."""
    await page.get_by_text(title, exact=True).first.wait_for(timeout=60000)
    await page.wait_for_timeout(1000)


async def press(page: Page, *names: str) -> None:
    for name in names:
        button = page.get_by_role("button", name=name, exact=True)
        if await button.count():
            await button.last.click()
            return
    raise SystemExit(f"no button named any of {names}")


async def call_ws(page: Page, message: dict):
    return await page.evaluate(
        "m => document.querySelector('home-assistant').hass.callWS(m)", message
    )


async def call_service(page: Page, domain: str, service: str, data: dict) -> None:
    await page.evaluate(
        "([d, s, data]) => document.querySelector('home-assistant')"
        ".hass.callService(d, s, data)",
        [domain, service, data],
    )


async def run(args: argparse.Namespace) -> None:
    only = set(args.only.split(",")) if args.only else None
    OUT.mkdir(parents=True, exist_ok=True)
    config = Path(tempfile.mkdtemp(prefix="sunbridge-screens-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    prepare(config, port)
    started = datetime.now(UTC)
    log = (config / "hass.out").open("w")
    print(f"Home Assistant in {config}, on {base}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "homeassistant", "--config", str(config)],
        stdout=log,
        stderr=subprocess.STDOUT,
        # Not the repository: `python -m` puts the working directory on the
        # path, and its custom_components/ would shadow the config's own.
        cwd=config,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    datalogger = FakeDatalogger(SUNBRIDGE_PORT)
    try:
        async with aiohttp.ClientSession() as session:
            await wait_for_http(session, base, proc)
            tokens = await onboard(session, base)
            await settle(session, base, tokens["access_token"])
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(
                viewport=VIEWPORT,
                device_scale_factor=SCALE,
                locale="en-GB",
                timezone_id="UTC",
                color_scheme="light",
            )
            await context.add_init_script(
                f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
            )
            page = await context.new_page()
            camera = Camera(page, only)
            await shoot_setup(page, camera, base, datalogger)
            await shoot_devices(page, camera, base, datalogger, started)
            await shoot_reconfigure(page, camera, base)
            await browser.close()
        print(f"{len(camera.taken)} screenshots in {OUT.relative_to(ROOT)}")
    finally:
        await datalogger.stop()
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(30)
        if proc.poll() is None:
            proc.kill()
        log.close()
        if args.keep:
            print(f"kept {config}")
        else:
            shutil.rmtree(config, ignore_errors=True)


async def shoot_setup(
    page: Page, camera: Camera, base: str, datalogger: FakeDatalogger
) -> None:
    """Every setup screen, in Advanced mode so the last one is shown too."""
    await page.goto(f"{base}/config/integrations/dashboard/add?domain=sunbridge")
    await page.get_by_text("Do you want to set up SunBridge?").wait_for(timeout=60000)
    await page.get_by_role("button", name="OK", exact=True).click()

    await screen(page, "Welcome to SunBridge")
    await camera.shoot("setup-welcome", dialog(page))
    await page.get_by_role("radio", name="Advanced").check()
    await press(page, "Submit", "Next")

    await screen(page, "Choose the Port")
    if not await page.get_by_text(ADDRESS, exact=True).count():
        raise SystemExit(f"setup did not detect {ADDRESS}: nothing more is taken")
    await camera.shoot("setup-port", dialog(page))
    port = page.get_by_role("spinbutton", name="Port")
    await port.fill(str(SUNBRIDGE_PORT))
    await press(page, "Submit", "Next")

    await screen(page, "Point Your Datalogger Here")
    await camera.shoot("setup-point", dialog(page))
    await press(page, "Next", "Submit")

    await page.get_by_text("Waiting for your datalogger").first.wait_for(timeout=60000)
    await page.wait_for_timeout(1000)
    await camera.shoot("setup-wait", dialog(page))
    datalogger.start()

    await screen(page, "Datalogger Found")
    await camera.shoot("setup-found", dialog(page))
    await press(page, "Submit", "Next")

    await screen(page, "Advanced Settings")
    await camera.shoot("setup-advanced", dialog(page))
    await press(page, "Submit", "Next")

    await page.get_by_text("Name and assign").wait_for(timeout=60000)
    await page.wait_for_timeout(1500)
    await camera.shoot("setup-created", dialog(page))
    await press(page, "Skip and finish", "Finish")


async def state(page: Page, entity_id: str) -> str | None:
    return await page.evaluate(
        "e => document.querySelector('home-assistant').hass.states[e]?.state", entity_id
    )


async def wait_state(page: Page, entity_id: str, want: str, seconds: int = 60) -> None:
    for _ in range(seconds * 4):
        if await state(page, entity_id) == want:
            return
        await page.wait_for_timeout(250)
    raise SystemExit(
        f"{entity_id} never became {want!r} (is {await state(page, entity_id)!r})"
    )


async def shoot_devices(
    page: Page,
    camera: Camera,
    base: str,
    datalogger: FakeDatalogger,
    started: datetime,
) -> None:
    """Both devices with live values, the power history, the Energy setting."""
    await wait_state(page, "binary_sensor.solar_datalogger_connected", "on")
    # The power sensor's earlier hours: every captured live record but the
    # last, decoded by SunBridge, at its own spacing, ending a minute before
    # Home Assistant started (the recorder compiles only periods not yet run).
    records = live_records()
    last = records[-1].at
    end = started - timedelta(minutes=1)
    points = [
        [(end - (last - r.at)).isoformat(), str(output_power(r))] for r in records[:-1]
    ]
    await call_service(
        page, "screenshot_env", "backfill", {"entity_id": POWER, "points": points}
    )
    # The newest record arrives as the datalogger sends it.
    datalogger.send(records[-1].raw)
    await wait_state(page, POWER, str(output_power(records[-1])))

    devices = await call_ws(page, {"type": "config/device_registry/list"})
    ids = {
        d["name"]: d["id"]
        for d in devices
        if any(i[0] == "sunbridge" for i in d["identifiers"])
    }
    # Each page at the height its cards need, so nothing is cut off and no
    # empty screen is left below them.
    for name, shot, height in (
        ("Solar Inverter", "device-inverter", 1260),
        ("Solar Datalogger", "device-datalogger", 640),
    ):
        await page.set_viewport_size({"width": VIEWPORT["width"], "height": height})
        await page.goto(f"{base}/config/devices/device/{ids[name]}")
        await page.get_by_text(name, exact=True).first.wait_for(timeout=60000)
        await page.wait_for_timeout(2500)
        await camera.shoot(shot)
    await page.set_viewport_size(VIEWPORT)

    await page.goto(f"{base}/config/devices/device/{ids['Solar Inverter']}")
    await page.wait_for_timeout(2000)
    await page.evaluate(
        """e => document.querySelector('home-assistant').dispatchEvent(
             new CustomEvent('hass-more-info',
               {detail: {entityId: e}, bubbles: true, composed: true}))""",
        POWER,
    )
    await page.wait_for_timeout(4000)
    await camera.shoot("power-history", dialog(page, "ha-more-info-dialog"))
    await page.keyboard.press("Escape")

    await page.goto(f"{base}/config/energy")
    await page.get_by_role("button", name="Add solar production").first.click()
    await page.wait_for_timeout(1500)
    await page.locator(
        "dialog-energy-solar-settings ha-statistic-picker"
    ).first.wait_for()
    # Picked the way each picker reports a choice to its dialog: the lifetime
    # energy as production, and the power sensor as production power.
    await page.evaluate(
        """ids => {
          const dlg = document.querySelector('home-assistant').shadowRoot
            .querySelector('dialog-energy-solar-settings');
          const pickers = dlg.shadowRoot.querySelectorAll('ha-statistic-picker');
          ids.forEach((id, n) => {
            pickers[n].value = id;
            pickers[n].dispatchEvent(new CustomEvent('value-changed',
              {detail: {value: id}, bubbles: true, composed: true}));
          });
        }""",
        ["sensor.solar_inverter_energy_total", POWER],
    )
    await page.wait_for_timeout(1500)
    await camera.shoot("energy-solar", dialog(page, "dialog-energy-solar-settings"))


async def shoot_reconfigure(page: Page, camera: Camera, base: str) -> None:
    """The entry's Reconfigure form, opened from its menu and left unsaved."""
    if camera.only and "setup-reconfigure" not in camera.only:
        return
    await page.goto(f"{base}/config/integrations/integration/sunbridge")
    row = page.locator("ha-config-entry-row").first
    await row.wait_for(timeout=60000)
    await page.wait_for_timeout(1500)
    await row.get_by_role("button", name="Menu", exact=True).first.click()
    await page.get_by_text("Reconfigure", exact=True).first.click()
    await screen(page, "Change the Port")
    await camera.shoot("setup-reconfigure", dialog(page))
    await page.keyboard.press("Escape")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep the temporary config")
    parser.add_argument("--only", help="comma-separated screen names to (re)take")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
