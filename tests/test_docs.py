"""The documentation says only what the code does."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def test_no_doc_claims_every_record_is_acknowledged() -> None:
    """Only 0x03, 0x04 and 0x50 are acknowledged; pings are echoed, and
    0x18/0x19 and unknown types get nothing."""
    pattern = re.compile(r"acknowledg\w*\s+(every|all|each)\s+(record|frame)", re.I)
    for path in DOCS:
        assert not pattern.search(path.read_text()), path.name


def test_readme_names_what_is_acknowledged() -> None:
    text = (ROOT / "README.md").read_text()
    assert re.search(r"acknowledges\s+`0x03`,\s+`0x04`\s+and\s+`0x50`", text)
    assert re.search(r"echoes\s+(its\s+)?(`0x16`\s+)?pings", text)


def _strings() -> dict:
    import json

    path = ROOT / "custom_components" / "sunbridge" / "strings.json"
    return json.loads(path.read_text())


def test_setup_guide_has_a_section_per_setup_screen() -> None:
    """docs/setup.md walks through every screen the setup can show."""
    headings = re.findall(r"^### (.+)$", (ROOT / "docs" / "setup.md").read_text(), re.M)
    for step in _strings()["config"]["step"].values():
        assert any(h.startswith(step["title"]) for h in headings), step["title"]


def test_setup_text_has_no_long_dashes() -> None:
    """Sentences use colons and commas, not em or en dashes."""
    text = (ROOT / "custom_components" / "sunbridge" / "strings.json").read_text()
    assert "—" not in text
    assert "–" not in text
