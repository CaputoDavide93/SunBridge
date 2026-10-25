#!/usr/bin/env python3
"""Regenerate the entity tables from the integration's own code.

The sources of truth are the entity descriptions in sensor.py (read with `ast`,
so this runs without Home Assistant installed), the binary sensor in
binary_sensor.py, and the names in translations/en.json. Entity ids follow
from those names and the device names in const.py, which tests/test_listener.py
pins against a running Home Assistant.

  python3 tools/gen_docs.py          # rewrite the AUTOGEN blocks
  python3 tools/gen_docs.py --check  # exit 1 if they are stale (for CI)
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "sunbridge"
TARGETS = {
    ROOT / "README.md": ("status", "modes", "entities"),
    ROOT / "docs" / "entities.md": ("entities",),
    ROOT / "docs" / "setup.md": ("setup-wait",),
}

UNITS = {
    "WATT": "W",
    "KILO_WATT_HOUR": "kWh",
    "VOLT": "V",
    "AMPERE": "A",
    "HERTZ": "Hz",
    "CELSIUS": "°C",
    "HOURS": "h",
}


def _const(name: str) -> str:
    tree = ast.parse((COMPONENT / "const.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == name:
            return ast.literal_eval(node.value)
    raise SystemExit(f"const.py has no {name}")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _descriptions() -> list[dict]:
    """Every LiveDescription / TotalDescription in sensor.py, in order."""
    tree = ast.parse((COMPONENT / "sensor.py").read_text())
    out = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("LiveDescription", "TotalDescription")
        ):
            continue
        row: dict = {"kind": node.func.id}
        for kw in node.keywords:
            value = kw.value
            if isinstance(value, ast.Constant):
                row[kw.arg] = value.value
            elif isinstance(value, ast.Attribute):
                row[kw.arg] = value.attr
        out.append(row)
    return out


def entities() -> str:
    names = json.loads((COMPONENT / "translations" / "en.json").read_text())["entity"]
    inverter = _slug(_const("DEVICE_INVERTER_NAME"))
    datalogger = _slug(_const("DEVICE_DATALOGGER_NAME"))

    rows = []
    for d in _descriptions():
        name = names["sensor"][d["translation_key"]]["name"]
        unit = UNITS.get(d.get("native_unit_of_measurement", ""), "")
        if d.get("device_class") == "TIMESTAMP":
            unit = "timestamp"
        elif d.get("device_class") == "ENUM":
            unit = "waiting / producing / fault"
        if d["kind"] == "LiveDescription":
            kind = "Live: unavailable after the stale window"
        else:
            kind = "Keeps its last value"
        if d.get("state_class") == "TOTAL_INCREASING":
            kind += ", total increasing"
        mode = "Advanced" if d.get("advanced") else "Simple"
        if d.get("entity_registry_enabled_default") is False:
            mode += ", disabled (unverified)"
        rows.append((name, f"`sensor.{inverter}_{_slug(name)}`", unit, kind, mode))
    connected = names["binary_sensor"]["connected"]["name"]
    rows.append(
        (
            connected,
            f"`binary_sensor.{datalogger}_{_slug(connected)}`",
            "on / off",
            "On while an open connection from it has sent a decodable record",
            "Simple",
        )
    )
    out = [
        "| Entity | Default entity id | Unit | Behaviour | Mode |",
        "|---|---|---|---|---|",
    ]
    out += [f"| {' | '.join(r)} |" for r in rows]
    return "\n".join(out)


def modes() -> str:
    """Simple against Advanced: the sensors from sensor.py, the rest from
    const.py, so neither column can drift from the code."""
    names = json.loads((COMPONENT / "translations" / "en.json").read_text())["entity"]
    simple, extra, disabled = [], [], []
    for d in _descriptions():
        name = names["sensor"][d["translation_key"]]["name"]
        if not d.get("advanced"):
            simple.append(name)
        elif d.get("entity_registry_enabled_default") is False:
            disabled.append(name)
        else:
            extra.append(name)
    simple.append(names["binary_sensor"]["connected"]["name"])
    default = _const("DEFAULT_UPLOAD_INTERVAL")
    floor, intervals = _const("MIN_STALE_MINUTES"), _const("STALE_INTERVALS")
    first, last = _const("MIN_UPLOAD_INTERVAL"), _const("MAX_UPLOAD_INTERVAL")
    added = ", ".join(extra)
    if disabled:
        added += f", and {', '.join(disabled)} (created disabled: unverified)"
    rows = [
        ("Sensors", ", ".join(simple), f"Everything in Simple, plus {added}"),
        (
            "Dataloggers answered",
            "Any",
            "Only the serials in the allow-list, prefilled with the one setup "
            "found (empty accepts any)",
        ),
        (
            "Upload interval expected",
            f"{default} minutes, the datalogger's default",
            f"What you enter, {first} to {last} minutes, matching the datalogger",
        ),
        (
            "Live sensors go unavailable after",
            f"{max(floor, intervals * default)} minutes without a live record",
            f"{intervals} upload intervals, never less than {floor} minutes",
        ),
        (
            "Setup screens",
            "Welcome, port, datalogger steps, waiting, found (or not yet)",
            "The same, then Advanced Settings",
        ),
    ]
    out = ["| | Simple | Advanced |", "|---|---|---|"]
    out += [f"| {' | '.join(r)} |" for r in rows]
    return "\n".join(out)


def setup_wait() -> str:
    """How long setup's waiting step listens, from SETUP_WAIT_SECONDS."""
    return f"It listens for up to **{_const('SETUP_WAIT_SECONDS') // 60} minutes**."


def status() -> str:
    """The release line, from the manifest's version."""
    import json

    version = json.loads((COMPONENT / "manifest.json").read_text())["version"]
    return f"**Status: new.** Version {version}."


BLOCKS = {
    "entities": entities,
    "modes": modes,
    "setup-wait": setup_wait,
    "status": status,
}


def render(text: str, names: tuple[str, ...], path: pathlib.Path) -> str:
    for name in names:
        fn = BLOCKS[name]
        pattern = re.compile(
            rf"(<!-- AUTOGEN:{name} -->\n)(?:.*?\n)?(<!-- /AUTOGEN:{name} -->)", re.S
        )
        if not pattern.search(text):
            sys.exit(f"{path.name} has no AUTOGEN:{name} block")
        text = pattern.sub(lambda m, fn=fn: m.group(1) + fn() + "\n" + m.group(2), text)
    return text


def main() -> None:
    stale = []
    for path, names in TARGETS.items():
        current = path.read_text(encoding="utf-8")
        fresh = render(current, names, path)
        if fresh == current:
            continue
        if "--check" in sys.argv[1:]:
            stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(fresh, encoding="utf-8")
            print(f"{path.relative_to(ROOT)} AUTOGEN blocks written")
    if stale:
        print(
            "stale AUTOGEN blocks (run python3 tools/gen_docs.py):\n  "
            + "\n  ".join(stale)
        )
        sys.exit(1)
    if "--check" in sys.argv[1:]:
        print("AUTOGEN blocks up to date")


if __name__ == "__main__":
    main()
