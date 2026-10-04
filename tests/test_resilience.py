"""Regression tests for the resilience pass: state machine, validation gates, timeouts, payloads, caching."""

from __future__ import annotations

import asyncio
import io
import logging
import logging.handlers
import time
from dataclasses import replace
from typing import Any

import pytest

from aegis.engine import (
    TRANSITIONS,
    ActionLedger,
    ActionState,
    ApprovalError,
    IllegalTransitionError,
    transition,
)
from aegis.voicemails import VoicemailStore
from server import server as server_module
from server import tools as tools_module
from server.cache import TTLCache
from server.server import BODY_READ_TIMEOUT_SECONDS, RequestGuard, build_server, configure_logging, screen_jsonrpc
from server.tools import TIMEOUT_SAY, AegisTools, ToolFailure, ToolResult
from server.validation import StageActionArgs


def make_tools() -> AegisTools:
    return AegisTools(VoicemailStore.from_directory(), ActionLedger())


def run(tools: AegisTools, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    return asyncio.run(tools.call(name, arguments)).structured  # type: ignore[arg-type]


# ------------------------------------------------------------- 1. state machine


ALL_STATES = list(ActionState)


def pending_action() -> Any:
    ledger = ActionLedger()
    return ledger.stage_action("block_number", "vm-001", "+12025550147").action


@pytest.mark.parametrize("source", ALL_STATES)
@pytest.mark.parametrize("target", ALL_STATES)
def test_transition_table_is_enforced(source: ActionState, target: ActionState) -> None:
    action = replace(pending_action(), state=source)
    if target in TRANSITIONS[source]:
        moved = transition(action, target)
        assert moved.state is target and action.state is source  # the input is never mutated
    else:
        with pytest.raises(IllegalTransitionError):
            transition(action, target)


def test_terminal_states_have_no_exits() -> None:
    assert TRANSITIONS[ActionState.EXECUTED] == TRANSITIONS[ActionState.REJECTED] == TRANSITIONS[ActionState.EXPIRED] == frozenset()


def test_illegal_transition_is_a_permission_error() -> None:
    assert issubclass(IllegalTransitionError, ApprovalError) and issubclass(IllegalTransitionError, PermissionError)


def test_ledger_fails_closed_on_a_corrupted_entry() -> None:
    ledger = ActionLedger()
    staged = ledger.stage_action("block_number", "vm-001", "+12025550147")
    assert staged.action and staged.approval_token
    entry = ledger._entries[staged.action.action_id]
    entry.action = replace(entry.action, state=ActionState.EXECUTED)  # simulate a broken invariant
    with pytest.raises(IllegalTransitionError):
        ledger.approve_action(staged.approval_token, "block_number")
    assert ledger.completed("block_number", "vm-001", "+12025550147") is None  # no side effect happened


def test_handlers_are_not_public() -> None:
    tools = make_tools()
    for name in ("list_voicemails", "check_voicemail", "explain_red_flags", "block_number", "report_scam"):
        assert not hasattr(tools, name), f"{name} must only be reachable through AegisTools.call"


def test_schema_gate_stops_what_a_lenient_parser_lets_through(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = make_tools()
    monkeypatch.setitem(tools_module._PARSERS, "block_number", lambda arguments: StageActionArgs("vm-001"))
    with pytest.raises(ToolFailure) as excinfo:
        asyncio.run(tools.call("block_number", {"voicemail_id": "vm-001", "phone_number": "+15550100"}))
    assert excinfo.value.code == "invalid_input"
    assert tools._ledger._entries == {}  # nothing was staged: the safe default state


def test_failed_validation_leaves_pending_actions_untouched() -> None:
    tools = make_tools()
    token = run(tools, "block_number", {"voicemail_id": "vm-001"})["approval_token"]
    with pytest.raises(ToolFailure):
        asyncio.run(tools.call("block_number", {"approval_token": token, "decision": "maybe"}))
    assert run(tools, "block_number", {"approval_token": token, "decision": "approve"})["status"] == "executed"


# ----------------------------------------------------------------- 2. timeouts


def call_through_server(tools: AegisTools, name: str, arguments: dict[str, Any]) -> Any:
    from mcp import Client

    async def scenario() -> Any:
        async with Client(build_server(tools), mode="legacy") as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(scenario())


def test_slow_tool_times_out_with_a_spoken_message(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = make_tools()

    async def stalls(args: Any) -> ToolResult:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    monkeypatch.setattr(server_module, "TOOL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setitem(tools._handlers, "list_voicemails", stalls)
    started = time.monotonic()
    result = call_through_server(tools, "list_voicemails", {})
    assert time.monotonic() - started < 5
    assert result.is_error is True
    assert result.structured_content == {"error": {"code": "unavailable", "say": TIMEOUT_SAY}}
    assert "Traceback" not in result.content[0].text


def test_tool_and_body_deadlines_are_within_budget() -> None:
    assert server_module.TOOL_TIMEOUT_SECONDS <= 5 and BODY_READ_TIMEOUT_SECONDS <= 5


# --------------------------------------------------------- 3. malformed payloads


class Recorder:
    def __init__(self) -> None:
        self.body = b""
        self.calls = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        self.body = (await receive())["body"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def feed(guard: RequestGuard, chunks: list[bytes], disconnect: bool = False) -> int:
    messages: list[dict[str, Any]] = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1 or disconnect} for i, c in enumerate(chunks)
    ]
    if disconnect:
        messages.append({"type": "http.disconnect"})
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [], "query_string": b""}
    await guard(scope, receive, send)
    return next((m["status"] for m in sent if m["type"] == "http.response.start"), 0)


def test_body_split_into_single_bytes_is_reassembled_exactly() -> None:
    inner = Recorder()
    payload = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
    assert asyncio.run(feed(RequestGuard(inner, stateless=True), [bytes([b]) for b in payload])) == 200
    assert inner.body == payload


def test_invalid_utf8_mid_stream_reaches_the_sdk_parse_error_untouched() -> None:
    inner = Recorder()
    payload = b'{"jsonrpc":"2.0","id":1,"method":"pi\xff\xfeng"}'
    assert screen_jsonrpc(payload) is None
    assert asyncio.run(feed(RequestGuard(inner, stateless=True), [payload[:20], payload[20:]])) == 200
    assert inner.body == payload


def test_stream_dropped_after_partial_json_never_reaches_handlers() -> None:
    inner = Recorder()
    status = asyncio.run(feed(RequestGuard(inner, stateless=True), [b'{"jsonrpc":"2.0","id":1,'], disconnect=True))
    assert status == 0 and inner.calls == 0


@pytest.mark.parametrize(
    "payload",
    [b"\xef\xbb\xbf{}", b"null", b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":null}', b"{" * 5000],
)
def test_screen_never_raises_on_odd_payloads(payload: bytes) -> None:
    screen_jsonrpc(payload)


# -------------------------------------------------------- 4. caching and prefetch


def test_ttl_cache_expires_and_evicts() -> None:
    now = [0.0]
    cache: TTLCache[str, int] = TTLCache(maxsize=2, ttl=10, clock=lambda: now[0])
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1  # "a" is now most recently used
    cache.set("c", 3)  # evicts "b"
    assert cache.get("b") is None and cache.get("c") == 3
    now[0] = 11
    assert cache.get("a") is None and len(cache) == 1
    assert cache.hits == 2 and cache.misses == 2


def test_repeated_checks_hit_the_analysis_cache_and_match_fresh_results() -> None:
    tools = make_tools()
    first = run(tools, "check_voicemail", {"voicemail_id": "vm-001"})
    second = run(tools, "check_voicemail", {"voicemail_id": "vm-001"})
    explain = run(tools, "explain_red_flags", {"voicemail_id": "vm-001"})
    assert first == second and explain["verdict"] == first["verdict"]
    assert tools.cache_stats["analysis_misses"] == 1 and tools.cache_stats["analysis_hits"] == 2


def test_spelled_variants_share_one_hint_cache_entry() -> None:
    tools = make_tools()
    for hint in ("I.R.S.", "i r s", "IRS"):
        assert run(tools, "check_voicemail", {"caller_hint": hint})["voicemail"]["voicemail_id"] == "vm-001"
    assert tools.cache_stats["hint_misses"] == 1 and tools.cache_stats["hint_hits"] == 2


def test_hint_cache_is_bounded() -> None:
    tools = make_tools()
    for i in range(tools_module.HINT_CACHE_MAX_ENTRIES + 50):
        with pytest.raises(ToolFailure):
            asyncio.run(tools.call("check_voicemail", {"caller_hint": f"nobody {i}"}))
    assert len(tools._hint_matches) == tools_module.HINT_CACHE_MAX_ENTRIES


def test_listing_prefetches_analyses_without_staging_anything() -> None:
    tools = make_tools()

    async def scenario() -> dict[str, int]:
        listed = await tools.call("list_voicemails", {})
        await tools.drain_background()
        for voicemail in listed.structured["voicemails"]:
            await tools.call("check_voicemail", {"voicemail_id": voicemail["voicemail_id"]})
        return tools.cache_stats

    stats = asyncio.run(scenario())
    assert stats["analysis_hits"] == 5 and stats["analysis_misses"] == 0
    assert tools._ledger._entries == {}


def test_prefetch_failure_is_logged_not_raised(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    tools = make_tools()

    def broken(transcript: str) -> Any:
        raise RuntimeError("engine offline")

    monkeypatch.setattr(tools_module, "analyze_voicemail", broken)

    async def scenario() -> None:
        tools._prefetch(tools._store.newest_first[:2])
        await tools.drain_background()

    with caplog.at_level(logging.ERROR, logger="aegis.tools"):
        asyncio.run(scenario())
    assert "speculative prefetch failed" in caplog.text


# ------------------------------------------------------------ 5. background logs


def test_logging_goes_through_a_background_queue() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    listener = configure_logging()
    try:
        assert len(root.handlers) == 1 and isinstance(root.handlers[0], logging.handlers.QueueHandler)
        captured = io.StringIO()
        listener.handlers[0].setStream(captured)  # type: ignore[attr-defined]
        logging.getLogger("aegis.server").info("queued line")
    finally:
        listener.stop()  # flushes the queue
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    assert "queued line" in captured.getvalue()
