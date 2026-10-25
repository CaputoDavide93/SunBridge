"""SunBridge only ever sends three things, and never a command to the inverter.

Owner's rule (25 Oct 2026): "make sure the integration is not sending strange
signals to the inverter". Everything SunBridge puts on the wire goes through
`_send` in server.py, and every frame it builds goes through `build_frame` in
protocol.py. This pins both ends so a fourth kind of message cannot be added
without this test, and its docstring, being changed on purpose:

* a ping (0x16) echoed back unchanged;
* the one-byte acknowledgement (0x00) to an announce, live or buffered record;
* the datalogger's clock, register 0x1F, written to the DATALOGGER (0x18).

No register of the inverter is ever read or written, and nothing changes the
datalogger's network or upload settings.
"""
from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from custom_components.sunbridge import protocol

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "sunbridge"


def _calls(path: Path, name: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Attribute) and node.func.attr == name)
             or (isinstance(node.func, ast.Name) and node.func.id == name))
    ]


def test_server_sends_only_echo_ack_and_clock() -> None:
    sends = _calls(SRC / "server.py", "_send")
    payloads = sorted(ast.unparse(call.args[1]) for call in sends)
    assert payloads == sorted([
        "frame.raw",
        "build_ack(frame)",
        "build_clock(datalogger, self._now())",
    ])
    # writer.write appears once, inside _send itself.
    assert len(_calls(SRC / "server.py", "write")) == 1


def test_only_ack_and_clock_build_frames() -> None:
    tree = ast.parse((SRC / "protocol.py").read_text(encoding="utf-8"))
    callers = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "build_frame"
    }
    assert callers == {"build_ack", "build_clock"}
    for module in SRC.glob("*.py"):
        if module.name != "protocol.py":
            assert not _calls(module, "build_frame"), module.name


def test_the_ack_is_one_zero_byte_of_the_same_type() -> None:
    for record_type in (0x03, 0x04, 0x50):
        raw = protocol.build_frame(0x1234, record_type, b"\x00" * 70)
        ack = protocol.parse_frame(protocol.build_ack(protocol.parse_frame(raw)))
        assert (ack.record_type, ack.payload) == (record_type, b"\x00")


def test_the_clock_writes_register_0x1f_of_the_datalogger_only() -> None:
    frame = protocol.parse_frame(protocol.build_clock("DLGEXAMPLE", datetime(2026, 10, 25, 12, 0, tzinfo=UTC)))
    assert frame.record_type == 0x18
    p = frame.payload
    assert p[:10] == b"DLGEXAMPLE"
    reg_at = len(p) - 19 - 4
    assert int.from_bytes(p[reg_at:reg_at + 2], "big") == 0x1F
    assert p[-19:] == b"2026-10-25 12:00:00"
