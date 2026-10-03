"""Regression tests for the five verified stress-test failures (Chain-of-Verification pass, 2026-10-03)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from aegis.engine import ActionLedger
from aegis.voicemails import FixtureError, Voicemail, VoicemailStore, collapse_spelled_acronyms
from server.schemas import TOOL_DEFINITIONS
from server.server import HalfCloseTolerantH11Protocol, build_server, output_violations, screen_jsonrpc
from server.tools import AegisTools, ToolResult


def body(message: Any) -> bytes:
    return json.dumps(message).encode()


# 1. Invalid JSON-RPC ids used to be swallowed as notifications (202, no body): the client hung.


@pytest.mark.parametrize("request_id", [None, 1.5, True, [1], {"a": 1}])
def test_invalid_request_id_gets_an_error_not_silence(request_id: Any) -> None:
    status, payload = screen_jsonrpc(body({"jsonrpc": "2.0", "id": request_id, "method": "ping"}))  # type: ignore[misc]
    assert status == 400 and payload["id"] is None and payload["error"]["code"] == -32600


@pytest.mark.parametrize("request_id", [0, 7, "abc", ""])
def test_valid_request_ids_pass_through(request_id: Any) -> None:
    assert screen_jsonrpc(body({"jsonrpc": "2.0", "id": request_id, "method": "ping"})) is None


def test_notifications_pass_through() -> None:
    assert screen_jsonrpc(body({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None


# 2. Non-object `arguments` used to be a JSON-RPC error; spec §7.1 requires isError invalid_input.


@pytest.mark.parametrize("arguments", [[1], "limit=5", 7, True])
def test_non_object_arguments_become_tool_error(arguments: Any) -> None:
    message = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_voicemails", "arguments": arguments}}
    status, payload = screen_jsonrpc(body(message))  # type: ignore[misc]
    result = payload["result"]
    assert status == 200 and payload["id"] == 3 and result["isError"] is True
    assert result["structuredContent"] == {
        "error": {
            "code": "invalid_input",
            "field": "arguments",
            "say": "I didn't quite catch that. Would you like me to list your voicemails?",
        }
    }
    assert result["content"] == [{"type": "text", "text": result["structuredContent"]["error"]["say"]}]
    assert "resultType" not in result


@pytest.mark.parametrize(
    "raw",
    [
        b'{"jsonrpc":"2.0","id":1,"method":',  # truncated JSON
        b"[" * 30000 + b"]" * 30000,  # deep nesting
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"arguments":{"limit":' + b"9" * 5000 + b"}}}",
        b'{"x":"\xff\xfe"}',  # invalid UTF-8
        b"",
        b"[]",
    ],
)
def test_unparseable_bodies_are_left_to_the_sdk_parse_error(raw: bytes) -> None:
    assert screen_jsonrpc(raw) is None


def test_unknown_tool_with_bad_arguments_stays_a_protocol_error() -> None:
    message = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": [1]}}
    assert screen_jsonrpc(body(message)) is None


# 3. A client that half-closed after its request got no response, even though the tool ran.


class FakeCycle:
    def __init__(self, response_complete: bool) -> None:
        self.response_complete = response_complete


def test_half_close_keeps_socket_open_until_the_response_is_sent() -> None:
    protocol = HalfCloseTolerantH11Protocol.__new__(HalfCloseTolerantH11Protocol)
    protocol.cycle = FakeCycle(response_complete=False)  # type: ignore[assignment]
    assert protocol.eof_received() is True
    protocol.cycle = FakeCycle(response_complete=True)  # type: ignore[assignment]
    assert protocol.eof_received() is False
    protocol.cycle = None  # type: ignore[assignment]
    assert protocol.eof_received() is False


# 4. Speech-to-text spells acronyms out ("I.R.S."), which used to miss the IRS voicemail.


@pytest.mark.parametrize(
    ("normalized", "collapsed"),
    [("i r s", "irs"), ("the i r s office", "the irs office"), ("a", "a"), ("i m fine", "im fine"), ("irs", "irs")],
)
def test_collapse_spelled_acronyms(normalized: str, collapsed: str) -> None:
    assert collapse_spelled_acronyms(normalized) == collapsed


@pytest.mark.parametrize("hint", ["I.R.S.", "i r s", "the I. R. S.", "I-R-S", "IRS"])
def test_spelled_out_irs_finds_the_demo_voicemail(hint: str) -> None:
    assert [v.voicemail_id for v in VoicemailStore.from_directory().find_by_hint(hint)] == ["vm-001"]


def test_spelled_out_ssa_finds_social_security() -> None:
    assert [v.voicemail_id for v in VoicemailStore.from_directory().find_by_hint("S.S.A.")] == ["vm-005"]


# 5. Over-long data produced output that broke the outputSchema, and nothing stopped it going out.


BASE = datetime(2026, 10, 3, tzinfo=UTC)
LONG_NAME = ("Very Long Caller Name Corporation Customer Service " * 2)[:80]


def long_name_store() -> VoicemailStore:
    return VoicemailStore(
        [Voicemail(f"vm-{i}", "+15035550100", LONG_NAME, BASE - timedelta(hours=i), "Hello.") for i in range(6)]
    )


@pytest.mark.parametrize(("name", "arguments"), [("list_voicemails", {}), ("check_voicemail", {"caller_hint": "Corporation"})])
def test_long_caller_names_still_fit_the_schema(name: str, arguments: dict[str, Any]) -> None:
    tools = AegisTools(long_name_store(), ActionLedger())
    structured = asyncio.run(tools.call(name, arguments)).structured  # type: ignore[arg-type]
    Draft202012Validator(TOOL_DEFINITIONS[name]["outputSchema"]).validate(structured)
    assert "others" in structured["say"]


@pytest.mark.parametrize(
    ("field", "value"), [("caller_name", "x" * 81), ("caller_number", "+1" + "5" * 40), ("caller_number", "unknown")]
)
def test_fixtures_outside_schema_limits_are_rejected(tmp_path: Path, field: str, value: str) -> None:
    fixture = {
        "voicemail_id": "vm-1",
        "caller_name": "Bank",
        "caller_number": "+15035550100",
        "received_at": "2026-10-03T00:00:00+00:00",
        "transcript": "Hello.",
        field: value,
    }
    (tmp_path / "vm-1.json").write_text(json.dumps(fixture))
    with pytest.raises(FixtureError):
        VoicemailStore.from_directory(tmp_path)


def test_output_violations_reports_schema_breaks() -> None:
    assert output_violations("list_voicemails", {"voicemails": [], "total": 0, "next_cursor": None, "say": "Hi."}) == []
    assert output_violations("list_voicemails", {"voicemails": [], "total": -1, "say": "x" * 401})


def test_non_compliant_output_is_replaced_by_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp import Client

    tools = AegisTools(VoicemailStore.from_directory(), ActionLedger())

    async def broken(arguments: Any) -> ToolResult:
        return ToolResult({"voicemails": [], "total": 0, "next_cursor": None, "say": "x" * 401}, status="listed")

    monkeypatch.setitem(tools._handlers, "list_voicemails", broken)

    async def scenario() -> Any:
        async with Client(build_server(tools), mode="legacy") as client:
            return await client.call_tool("list_voicemails", {})

    result = asyncio.run(scenario())
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "unavailable"
