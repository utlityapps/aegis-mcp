"""Tool handler tests against the architect's schemas (docs/architecture.md §6-9)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from aegis.engine import ActionLedger
from aegis.voicemails import VoicemailStore
from server.schemas import ERROR_BODY_SCHEMA, TOOL_DEFINITIONS, TOOL_NAMES
from server.tools import AegisTools, ToolFailure, ToolResult

ID_LIKE = re.compile(r"vm-\d|act-|rcpt-|[A-Za-z0-9_-]{32,}")
VALIDATORS = {name: Draft202012Validator(spec["outputSchema"]) for name, spec in TOOL_DEFINITIONS.items()}
INPUT_VALIDATORS = {name: Draft202012Validator(spec["inputSchema"]) for name, spec in TOOL_DEFINITIONS.items()}
ERROR_VALIDATOR = Draft202012Validator(ERROR_BODY_SCHEMA)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def tools(clock: FakeClock) -> AegisTools:
    return AegisTools(VoicemailStore.from_directory(), ActionLedger(clock=clock))


def assert_speakable(say: str, max_length: int) -> None:
    assert say and len(say) <= max_length
    assert not ID_LIKE.search(say), say
    assert not any(word in say for word in ("block_number", "report_scam", "{", "screen", "tap"))


def run(tools: AegisTools, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    result: ToolResult = asyncio.run(tools.call(name, arguments))  # type: ignore[arg-type]
    VALIDATORS[name].validate(result.structured)
    say_limit = TOOL_DEFINITIONS[name]["outputSchema"]["properties"]["say"]["maxLength"]
    assert_speakable(result.structured["say"], say_limit)
    if name != "explain_red_flags":
        assert len(result.structured["say"].split()) <= 60
    return result.structured


def fail(tools: AegisTools, name: str, arguments: dict[str, Any] | None) -> ToolFailure:
    with pytest.raises(ToolFailure) as excinfo:
        asyncio.run(tools.call(name, arguments))  # type: ignore[arg-type]
    ERROR_VALIDATOR.validate(excinfo.value.body())
    assert_speakable(excinfo.value.say, 300)
    return excinfo.value


def test_tool_catalog_is_exactly_the_agreed_five() -> None:
    assert list(TOOL_NAMES) == ["list_voicemails", "check_voicemail", "explain_red_flags", "block_number", "report_scam"]
    for spec in TOOL_DEFINITIONS.values():
        Draft202012Validator.check_schema(spec["inputSchema"])
        Draft202012Validator.check_schema(spec["outputSchema"])
        assert spec["inputSchema"]["type"] == spec["outputSchema"]["type"] == "object"
        assert spec["inputSchema"]["additionalProperties"] is False
        assert "$ref" not in str(spec)


# ------------------------------------------------------------------ list_voicemails


def test_list_pages_newest_first(tools: AegisTools) -> None:
    first = run(tools, "list_voicemails", {})
    assert first["total"] == 8 and len(first["voicemails"]) == 5
    assert first["voicemails"][0]["voicemail_id"] == "vm-001"
    times = [v["received_at"] for v in first["voicemails"]]
    assert times == sorted(times, reverse=True)
    second = run(tools, "list_voicemails", {"cursor": first["next_cursor"]})
    assert len(second["voicemails"]) == 3 and second["next_cursor"] is None
    seen = {v["voicemail_id"] for v in first["voicemails"] + second["voicemails"]}
    assert len(seen) == 8


def test_list_limit(tools: AegisTools) -> None:
    assert len(run(tools, "list_voicemails", {"limit": 2})["voicemails"]) == 2


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 6}, "limit"),
        ({"limit": True}, "limit"),
        ({"limit": "5"}, "limit"),
        ({"cursor": "5.0000000000000000"}, "cursor"),
        ({"cursor": "x" * 65}, "cursor"),
        ({"filter": "scam"}, "filter"),
    ],
)
def test_list_rejects_bad_input(tools: AegisTools, arguments: dict[str, Any], field: str) -> None:
    failure = fail(tools, "list_voicemails", arguments)
    assert failure.code == "invalid_input" and failure.field == field


def test_cursor_from_another_process_is_rejected(tools: AegisTools) -> None:
    cursor = run(tools, "list_voicemails", {})["next_cursor"]
    other = AegisTools(VoicemailStore.from_directory(), ActionLedger())
    assert fail(other, "list_voicemails", {"cursor": cursor}).field == "cursor"


# ------------------------------------------------------------------ check_voicemail


def test_demo_beat_resolves_irs_in_one_turn(tools: AegisTools) -> None:
    out = run(tools, "check_voicemail", {"caller_hint": "the IRS"})
    assert out["status"] == "checked" and out["voicemail"]["voicemail_id"] == "vm-001"
    assert out["verdict"] == "SCAM" and out["risk_score"] == 100
    assert out["suggested_actions"] == ["block_number", "report_scam"]
    assert len(out["top_flags"]) == 3 and out["flag_count"] == 6
    assert out["say"].startswith("This message looks like a scam.")


def test_check_without_arguments_uses_newest(tools: AegisTools) -> None:
    assert run(tools, "check_voicemail", None)["voicemail"]["voicemail_id"] == "vm-001"


def test_check_legitimate_voicemail(tools: AegisTools) -> None:
    out = run(tools, "check_voicemail", {"voicemail_id": "vm-006"})
    assert out["verdict"] == "LEGITIMATE" and out["top_flags"] == [] and out["suggested_actions"] == []


def test_check_ambiguous_hint_lists_candidates(tools: AegisTools) -> None:
    out = run(tools, "check_voicemail", {"caller_hint": "office"})
    assert out["status"] == "ambiguous" and 2 <= len(out["candidates"]) <= 5
    assert "verdict" not in out


def test_check_not_found(tools: AegisTools) -> None:
    assert fail(tools, "check_voicemail", {"caller_hint": "Publishers Clearing House"}).code == "not_found"
    assert fail(tools, "check_voicemail", {"voicemail_id": "vm-999"}).code == "not_found"


def test_check_rejects_both_selectors(tools: AegisTools) -> None:
    args = {"voicemail_id": "vm-001", "caller_hint": "IRS"}
    assert not INPUT_VALIDATORS["check_voicemail"].is_valid(args)
    assert fail(tools, "check_voicemail", args).code == "invalid_input"


@pytest.mark.parametrize("voicemail_id", ["VM-001", "-vm", "", "vm 001", "a" * 65])
def test_check_rejects_malformed_ids(tools: AegisTools, voicemail_id: str) -> None:
    assert fail(tools, "check_voicemail", {"voicemail_id": voicemail_id}).field == "voicemail_id"


# ---------------------------------------------------------------- explain_red_flags


def test_explain_pages_through_flags(tools: AegisTools) -> None:
    first = run(tools, "explain_red_flags", {"voicemail_id": "vm-001"})
    assert len(first["flags"]) == 3 and first["total_flags"] == 6 and first["next_start"] == 3
    rest = run(tools, "explain_red_flags", {"voicemail_id": "vm-001", "start": 3, "max_flags": 5})
    assert len(rest["flags"]) == 3 and rest["next_start"] is None
    ids = [f["flag_id"] for f in first["flags"] + rest["flags"]]
    assert len(set(ids)) == 6


def test_explain_past_the_end_and_no_flags(tools: AegisTools) -> None:
    past = run(tools, "explain_red_flags", {"voicemail_id": "vm-001", "start": 11})
    assert past["flags"] == [] and past["next_start"] is None and past["say"] == "That's everything I found."
    safe = run(tools, "explain_red_flags", {"voicemail_id": "vm-008"})
    assert safe["say"] == "I didn't find any warning signs in this message."


def test_explain_requires_voicemail_id(tools: AegisTools) -> None:
    assert fail(tools, "explain_red_flags", {}).field == "voicemail_id"
    assert fail(tools, "explain_red_flags", {"voicemail_id": "vm-001", "max_flags": 6}).field == "max_flags"


# ------------------------------------------------------- block_number / report_scam


def stage(tools: AegisTools, name: str, voicemail_id: str = "vm-001") -> dict[str, Any]:
    out = run(tools, name, {"voicemail_id": voicemail_id})
    assert out["status"] == "staged" and out["kind"] == name
    return out


def test_block_flow_stage_approve_then_already_done(tools: AegisTools) -> None:
    staged = stage(tools, "block_number")
    assert "Should I go ahead?" in staged["say"]
    done = run(tools, "block_number", {"approval_token": staged["approval_token"], "decision": "approve"})
    assert done["status"] == "executed" and done["receipt"]["simulated"] is True
    assert "practice version" in done["say"]
    assert run(tools, "block_number", {"voicemail_id": "vm-001"})["status"] == "already_done"


def test_reused_token_is_denied(tools: AegisTools) -> None:
    token = stage(tools, "block_number")["approval_token"]
    run(tools, "block_number", {"approval_token": token, "decision": "approve"})
    assert fail(tools, "block_number", {"approval_token": token, "decision": "approve"}).code == "permission_denied"


def test_block_tool_cannot_approve_a_staged_report(tools: AegisTools) -> None:
    token = stage(tools, "report_scam")["approval_token"]
    assert fail(tools, "block_number", {"approval_token": token, "decision": "approve"}).code == "permission_denied"
    done = run(tools, "report_scam", {"approval_token": token, "decision": "approve"})
    assert done["status"] == "executed" and done["receipt"]["voicemail_id"] == "vm-001"


def test_target_mismatch_is_denied_without_consuming(tools: AegisTools) -> None:
    token = stage(tools, "block_number")["approval_token"]
    args = {"approval_token": token, "decision": "approve", "voicemail_id": "vm-002"}
    assert fail(tools, "block_number", args).code == "permission_denied"
    assert run(tools, "block_number", {"approval_token": token, "decision": "approve"})["status"] == "executed"


def test_reject_drops_the_action(tools: AegisTools) -> None:
    token = stage(tools, "block_number")["approval_token"]
    out = run(tools, "block_number", {"approval_token": token, "decision": "reject"})
    assert out == {"status": "rejected", "kind": "block_number", "say": "Okay, I won't do that."}
    assert fail(tools, "block_number", {"approval_token": token, "decision": "approve"}).code == "permission_denied"
    assert stage(tools, "block_number")["status"] == "staged"


def test_restage_rotates_token(tools: AegisTools) -> None:
    first = stage(tools, "block_number")
    second = stage(tools, "block_number")
    assert first["action"]["action_id"] == second["action"]["action_id"]
    assert fail(tools, "block_number", {"approval_token": first["approval_token"], "decision": "approve"}).code == (
        "permission_denied"
    )
    assert run(tools, "block_number", {"approval_token": second["approval_token"], "decision": "approve"})


def test_expired_token(tools: AegisTools, clock: FakeClock) -> None:
    token = stage(tools, "block_number")["approval_token"]
    clock.now += timedelta(minutes=11)
    assert fail(tools, "block_number", {"approval_token": token, "decision": "approve"}).code == "token_expired"
    assert fail(tools, "block_number", {"approval_token": token, "decision": "approve"}).code == "permission_denied"


def test_report_on_safe_voicemail_reads_back_verdict(tools: AegisTools) -> None:
    staged = stage(tools, "report_scam", "vm-006")
    assert staged["action"]["verdict"] == "LEGITIMATE"
    assert staged["say"].startswith("Aegis thought this call looked safe.")


def test_duplicate_report_is_detected(tools: AegisTools) -> None:
    token = stage(tools, "report_scam")["approval_token"]
    run(tools, "report_scam", {"approval_token": token, "decision": "approve"})
    assert run(tools, "report_scam", {"voicemail_id": "vm-001"})["status"] == "already_done"


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({}, "voicemail_id"),
        ({"approval_token": "a" * 43}, "decision"),
        ({"decision": "approve"}, "approval_token"),
        ({"approval_token": "a" * 43, "decision": "maybe"}, "decision"),
        ({"approval_token": "short", "decision": "approve"}, "approval_token"),
        ({"voicemail_id": "vm-001", "phone_number": "+15550100"}, "phone_number"),
    ],
)
def test_action_input_rules(tools: AegisTools, arguments: dict[str, Any], field: str) -> None:
    assert not INPUT_VALIDATORS["block_number"].is_valid(arguments)
    failure = fail(tools, "block_number", arguments)
    assert failure.code == "invalid_input" and failure.field == field


def test_unknown_token_is_denied(tools: AegisTools) -> None:
    assert fail(tools, "report_scam", {"approval_token": "A" * 43, "decision": "reject"}).code == "permission_denied"


# ------------------------------------------------------------- server error mapping


def test_unexpected_exception_becomes_unavailable(tools: AegisTools, monkeypatch, caplog) -> None:
    from mcp import Client

    from server.server import build_server

    async def explode(arguments: Any) -> ToolResult:
        raise RuntimeError("fixture disk vanished")

    monkeypatch.setitem(tools._handlers, "list_voicemails", explode)

    async def scenario() -> Any:
        async with Client(build_server(tools), mode="legacy") as client:
            return await client.call_tool("list_voicemails", {})

    with caplog.at_level("INFO", logger="aegis.server"):
        result = asyncio.run(scenario())
    assert result.is_error is True
    assert result.structured_content == {
        "error": {"code": "unavailable", "say": "Aegis is having trouble right now. Please try again in a few minutes."}
    }
    assert "fixture disk vanished" in caplog.text
    assert '"status": "unavailable"' in caplog.text
