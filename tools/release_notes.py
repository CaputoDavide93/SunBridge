#!/usr/bin/env python3
"""Check that a release's version agrees everywhere, and print its notes.

    python3 tools/release_notes.py [vX.Y.Z]

Exits 1, saying what disagrees, unless:

- the manifest's "version" is X.Y.Z (when a tag is given);
- CHANGELOG.md has a `## [X.Y.Z] - YYYY-MM-DD` section for the manifest's
  version, with a real date and something in it;
- that section is the newest release in CHANGELOG.md (the first after
  `## [Unreleased]`).

On success it prints that section's body, which the release workflow
(.github/workflows/release.yaml) publishes as the GitHub release's notes.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "sunbridge" / "manifest.json"
CHANGELOG = ROOT / "CHANGELOG.md"
HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?: - (?P<date>\S+))?\s*$", re.M)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def fail(message: str) -> None:
    sys.exit(f"release REFUSED: {message}")


def release_notes(tag: str | None) -> str:
    version = json.loads(MANIFEST.read_text())["version"]
    if not SEMVER.match(version):
        fail(f"manifest.json version {version!r} is not X.Y.Z")
    if tag is not None and tag != f"v{version}":
        fail(f"tag {tag} does not match manifest.json version {version} (v{version})")

    text = CHANGELOG.read_text()
    headings = list(HEADING.finditer(text))
    released = [h for h in headings if h["version"] != "Unreleased"]
    if not released:
        fail("CHANGELOG.md has no released version")
    newest = released[0]
    if newest["version"] != version:
        fail(
            f"the newest CHANGELOG.md section is {newest['version']}, but "
            f"manifest.json says {version}"
        )
    try:
        dt.date.fromisoformat(newest["date"] or "")
    except ValueError:
        fail(f"CHANGELOG.md section {version} needs a YYYY-MM-DD date")

    following = [h.start() for h in headings if h.start() > newest.start()]
    end = following[0] if following else len(text)
    body = text[newest.end() : end].strip()
    if not body:
        fail(f"CHANGELOG.md section {version} is empty")
    return body + "\n"


def main() -> None:
    args = sys.argv[1:]
    if len(args) > 1 or (args and args[0].startswith("-")):
        sys.exit(__doc__)
    sys.stdout.write(release_notes(args[0] if args else None))


if __name__ == "__main__":
    main()
