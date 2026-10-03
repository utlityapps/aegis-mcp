"""The five Aegis tool handlers (docs/architecture.md §6-9).

Handlers take raw `arguments`, validate them into typed dataclasses, call the
deterministic engine, and return `structuredContent` dicts that conform to the
tool's outputSchema. Every expected failure is raised as `ToolFailure`, which the
server turns into an `isError: true` result with a spoken next step.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, assert_never

from aegis.engine import (
    ActionKind,
    ActionLedger,
    ApprovalError,
    ApprovalExpiredError,
    PendingAction,
    Receipt,
    analyze_voicemail,
    explain_red_flags as select_red_flags,
    propose_actions,
)
from aegis.voicemails import Voicemail, VoicemailStore
from server import speech
from server.schemas import ToolName
from server.validation import (
    InputError,
    ResolveActionArgs,
    StageActionArgs,
    parse_action,
    parse_check_voicemail,
    parse_explain_red_flags,
    parse_list_voicemails,
)

type ErrorCode = Literal["invalid_input", "not_found", "permission_denied", "token_expired", "unavailable"]
type Structured = dict[str, Any]

_DEFAULT_INPUT_SAY = "I didn't quite catch which voicemail you meant. Would you like me to list your voicemails?"
_INPUT_SAYS: dict[str, str] = {
    "limit": "I can read up to five voicemails at a time. Would you like to hear the newest ones?",
    "arguments": "I didn't quite catch that. Would you like me to list your voicemails?",
    "cursor": "I lost my place in your voicemail list. Would you like me to start again from the newest?",
    "start": "I lost my place in the warning signs. Would you like me to start from the beginning?",
    "max_flags": "I lost my place in the warning signs. Would you like me to start from the beginning?",
    "approval_token": "I need your clear yes or no first. Would you like me to set it up again?",
    "decision": "I need your clear yes or no first. Would you like me to set it up again?",
}
PERMISSION_DENIED_SAY = "I can't do that without your OK. Would you like me to set it up again?"
TOKEN_EXPIRED_SAY = "That request timed out. Would you like me to set it up again?"
UNAVAILABLE_SAY = "Aegis is having trouble right now. Please try again in a few minutes."
NOT_FOUND_ID_SAY = "I couldn't find that voicemail. Would you like to hear your recent voicemails?"
EMPTY_MAILBOX_SAY = "You don't have any voicemails right now. Ask me again when a new one comes in."
MAX_CANDIDATES = 5
_CURSOR_RE = re.compile(r"(?P<offset>[0-9]{1,6})\.(?P<mac>[0-9a-f]{16})", re.ASCII)
TOP_FLAGS = 3


class ToolFailure(Exception):
    """An expected tool execution error, reported as `isError: true` (docs/architecture.md §8)."""

    def __init__(self, code: ErrorCode, say: str, field: str | None = None) -> None:
        super().__init__(f"{code}: {field}" if field else code)
        self.code = code
        self.say = say
        self.field = field

    def body(self) -> Structured:
        error: Structured = {"code": self.code, "say": self.say}
        if self.field is not None:
            error["field"] = self.field
        return error

    @classmethod
    def from_input_error(cls, error: InputError) -> ToolFailure:
        return cls("invalid_input", _INPUT_SAYS.get(error.field, _DEFAULT_INPUT_SAY), error.field)


@dataclass(frozen=True, slots=True)
class ToolResult:
    structured: Structured
    status: str
    mode: Literal["stage", "resolve"] | None = None


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _summary(voicemail: Voicemail) -> Structured:
    return {
        "voicemail_id": voicemail.voicemail_id,
        "caller_label": speech.caller_label(voicemail),
        "caller_number": voicemail.caller_number,
        "received_at": _iso(voicemail.received_at),
    }


class CursorCodec:
    """Opaque, tamper-evident list cursors. The key lives only in this process."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)

    def _mac(self, offset: int) -> str:
        return hmac.new(self._key, f"list_voicemails:{offset}".encode(), hashlib.sha256).hexdigest()[:16]

    def encode(self, offset: int) -> str:
        return f"{offset}.{self._mac(offset)}"

    def decode(self, cursor: str) -> int:
        """Return the offset a cursor encodes, or raise InputError for anything this process didn't mint."""
        match = _CURSOR_RE.fullmatch(cursor)
        if match is None:
            raise InputError("cursor", "is not a cursor this server issued")
        offset = int(match["offset"])
        if offset < 1 or not hmac.compare_digest(match["mac"].encode("ascii"), self._mac(offset).encode("ascii")):
            raise InputError("cursor", "is not a cursor this server issued")
        return offset


class AegisTools:
    def __init__(self, store: VoicemailStore, ledger: ActionLedger, cursors: CursorCodec | None = None) -> None:
        self._store = store
        self._ledger = ledger
        self._cursors = cursors or CursorCodec()
        self._lock = asyncio.Lock()
        self._handlers: dict[ToolName, Callable[[Mapping[str, Any] | None], Awaitable[ToolResult]]] = {
            "list_voicemails": self.list_voicemails,
            "check_voicemail": self.check_voicemail,
            "explain_red_flags": self.explain_red_flags,
            "block_number": self.block_number,
            "report_scam": self.report_scam,
        }

    def has_tool(self, name: str) -> bool:
        return name in self._handlers

    async def call(self, name: ToolName, arguments: Mapping[str, Any] | None) -> ToolResult:
        try:
            return await self._handlers[name](arguments)
        except InputError as exc:
            raise ToolFailure.from_input_error(exc) from exc

    def _voicemail(self, voicemail_id: str) -> Voicemail:
        voicemail = self._store.get(voicemail_id)
        if voicemail is None:
            raise ToolFailure("not_found", NOT_FOUND_ID_SAY, "voicemail_id")
        return voicemail

    # ---------------------------------------------------------------- read-only

    async def list_voicemails(self, arguments: Mapping[str, Any] | None) -> ToolResult:
        args = parse_list_voicemails(arguments)
        offset = 0 if args.cursor is None else self._cursors.decode(args.cursor)
        everything = self._store.newest_first
        shown = everything[offset : offset + args.limit]
        next_offset = offset + len(shown)
        has_more = next_offset < len(everything)
        structured: Structured = {
            "voicemails": [_summary(v) for v in shown],
            "total": len(everything),
            "next_cursor": self._cursors.encode(next_offset) if has_more else None,
            "say": speech.list_say(shown, len(everything), has_more, first_page=offset == 0),
        }
        return ToolResult(structured, status="listed")

    async def check_voicemail(self, arguments: Mapping[str, Any] | None) -> ToolResult:
        args = parse_check_voicemail(arguments)
        if args.voicemail_id is not None:
            voicemail = self._voicemail(args.voicemail_id)
        elif args.caller_hint is not None:
            matches = self._store.find_by_hint(args.caller_hint)
            if not matches:
                say = (
                    f"I couldn't find a voicemail from {speech.speakable_hint(args.caller_hint)}. "
                    "Would you like to hear your recent voicemails?"
                )
                raise ToolFailure("not_found", say, "caller_hint")
            if len(matches) > 1:
                candidates = matches[:MAX_CANDIDATES]
                structured: Structured = {
                    "status": "ambiguous",
                    "candidates": [_summary(v) for v in candidates],
                    "say": speech.ambiguous_say(candidates),
                }
                return ToolResult(structured, status="ambiguous")
            voicemail = matches[0]
        else:
            if not self._store.newest_first:
                raise ToolFailure("not_found", EMPTY_MAILBOX_SAY)
            voicemail = self._store.newest_first[0]

        analysis = analyze_voicemail(voicemail.transcript)
        suggested = propose_actions(analysis)
        structured = {
            "status": "checked",
            "voicemail": _summary(voicemail),
            "verdict": analysis.verdict,
            "risk_score": analysis.risk_score,
            "top_flags": [{"flag_id": f.flag_id, "say": f.say} for f in analysis.flags[:TOP_FLAGS]],
            "flag_count": len(analysis.flags),
            "suggested_actions": list(suggested),
            "say": speech.check_say(analysis, suggested),
        }
        return ToolResult(structured, status="checked")

    async def explain_red_flags(self, arguments: Mapping[str, Any] | None) -> ToolResult:
        args = parse_explain_red_flags(arguments)
        voicemail = self._voicemail(args.voicemail_id)
        analysis = analyze_voicemail(voicemail.transcript)
        window = select_red_flags(analysis, args.start, args.max_flags)
        total = len(analysis.flags)
        next_start = args.start + len(window)
        has_more = bool(window) and next_start < total
        structured: Structured = {
            "voicemail_id": voicemail.voicemail_id,
            "verdict": analysis.verdict,
            "risk_score": analysis.risk_score,
            "flags": [{"flag_id": f.flag_id, "weight": f.weight, "say": f.say} for f in window],
            "total_flags": total,
            "next_start": next_start if has_more else None,
            "say": speech.explain_say([f.say for f in window], total, has_more),
        }
        return ToolResult(structured, status="explained")

    # ------------------------------------------------------ permission-gated pair

    async def block_number(self, arguments: Mapping[str, Any] | None) -> ToolResult:
        return await self._gated_action("block_number", arguments)

    async def report_scam(self, arguments: Mapping[str, Any] | None) -> ToolResult:
        return await self._gated_action("report_scam", arguments)

    async def _gated_action(self, kind: ActionKind, arguments: Mapping[str, Any] | None) -> ToolResult:
        args = parse_action(arguments)
        match args:
            case StageActionArgs():
                return await self._stage(kind, args)
            case ResolveActionArgs():
                return await self._resolve(kind, args)
            case _:
                assert_never(args)

    async def _stage(self, kind: ActionKind, args: StageActionArgs) -> ToolResult:
        voicemail = self._voicemail(args.voicemail_id)
        verdict = analyze_voicemail(voicemail.transcript).verdict
        async with self._lock:
            outcome = self._ledger.stage_action(kind, voicemail.voicemail_id, voicemail.caller_number)

        if outcome.already_done is not None:
            structured: Structured = {
                "status": "already_done",
                "kind": kind,
                "say": speech.already_done_say(kind, voicemail),
            }
            return ToolResult(structured, status="already_done", mode="stage")

        if outcome.action is None or outcome.approval_token is None:
            raise RuntimeError("ledger staged an action without returning it and its token")
        structured = {
            "status": "staged",
            "kind": kind,
            "action": self._action_body(outcome.action, voicemail, verdict),
            "approval_token": outcome.approval_token,
            "say": speech.stage_say(kind, voicemail, verdict),
        }
        return ToolResult(structured, status="staged", mode="stage")

    async def _resolve(self, kind: ActionKind, args: ResolveActionArgs) -> ToolResult:
        try:
            async with self._lock:
                if args.decision == "approve":
                    receipt = self._ledger.approve_action(args.approval_token, kind, args.voicemail_id)
                else:
                    self._ledger.reject_action(args.approval_token, kind, args.voicemail_id)
                    receipt = None
        except ApprovalExpiredError as exc:
            raise ToolFailure("token_expired", TOKEN_EXPIRED_SAY, "approval_token") from exc
        except ApprovalError as exc:
            raise ToolFailure("permission_denied", PERMISSION_DENIED_SAY, "approval_token") from exc

        if receipt is None:
            structured: Structured = {"status": "rejected", "kind": kind, "say": speech.REJECTED_SAY}
            return ToolResult(structured, status="rejected", mode="resolve")

        # The action has already executed, so this must not fail: fall back to the number if the voicemail is gone.
        voicemail = self._store.get(receipt.voicemail_id)
        label = speech.caller_label(voicemail) if voicemail else speech.spoken_number(receipt.caller_number)
        structured = {
            "status": "executed",
            "kind": kind,
            "receipt": self._receipt_body(receipt),
            "say": speech.executed_say(kind, label),
        }
        return ToolResult(structured, status="executed", mode="resolve")

    @staticmethod
    def _action_body(action: PendingAction, voicemail: Voicemail, verdict: str) -> Structured:
        body: Structured = {
            "action_id": action.action_id,
            "voicemail_id": action.voicemail_id,
            "caller_label": speech.caller_label(voicemail),
            "caller_number": action.caller_number,
            "created_at": _iso(action.created_at),
            "expires_at": _iso(action.expires_at),
        }
        if action.kind == "report_scam":
            body["verdict"] = verdict
        return body

    @staticmethod
    def _receipt_body(receipt: Receipt) -> Structured:
        body: Structured = {
            "receipt_id": receipt.receipt_id,
            "kind": receipt.kind,
            "caller_number": receipt.caller_number,
            "executed_at": _iso(receipt.executed_at),
            "simulated": receipt.simulated,
        }
        if receipt.kind == "report_scam":
            body["voicemail_id"] = receipt.voicemail_id
        return body
