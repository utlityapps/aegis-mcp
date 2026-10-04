"""Typed parsing of tool arguments, enforcing every inputSchema constraint (docs/architecture.md §7).

Each parser takes the raw `arguments` object and returns a frozen dataclass, or
raises `InputError` naming the offending field.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from aegis.security import UnsafeInputError, clean_text, looks_like_instruction
from aegis.telemetry import emit
from server.schemas import APPROVAL_TOKEN_PATTERN, VOICEMAIL_ID_PATTERN

type Decision = Literal["approve", "reject"]

_VOICEMAIL_ID_RE = re.compile(VOICEMAIL_ID_PATTERN)
_APPROVAL_TOKEN_RE = re.compile(APPROVAL_TOKEN_PATTERN)


class InputError(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ListVoicemailsArgs:
    limit: int = 5
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class CheckVoicemailArgs:
    voicemail_id: str | None = None
    caller_hint: str | None = None


@dataclass(frozen=True, slots=True)
class ExplainRedFlagsArgs:
    voicemail_id: str
    start: int = 0
    max_flags: int = 3


@dataclass(frozen=True, slots=True)
class StageActionArgs:
    voicemail_id: str


@dataclass(frozen=True, slots=True)
class ResolveActionArgs:
    approval_token: str
    decision: Decision
    voicemail_id: str | None = None


type ActionArgs = StageActionArgs | ResolveActionArgs


def _object(arguments: Mapping[str, Any] | None, allowed: frozenset[str]) -> Mapping[str, Any]:
    if arguments is None:
        return {}
    if not isinstance(arguments, Mapping):
        raise InputError("arguments", "must be an object")
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise InputError(unknown[0], "is not an accepted argument")
    return arguments


def _integer(args: Mapping[str, Any], name: str, low: int, high: int, default: int) -> int:
    if name not in args:
        return default
    value = args[name]
    if isinstance(value, bool):
        raise InputError(name, "must be an integer")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise InputError(name, "must be an integer")
    if not low <= value <= high:
        raise InputError(name, f"must be between {low} and {high}")
    return value


def _string(
    args: Mapping[str, Any],
    name: str,
    *,
    min_length: int = 0,
    max_length: int | None = None,
    pattern: re.Pattern[str] | None = None,
) -> str | None:
    if name not in args:
        return None
    value = args[name]
    if not isinstance(value, str):
        raise InputError(name, "must be a string")
    if len(value) < min_length:
        raise InputError(name, f"must be at least {min_length} characters")
    if max_length is not None and len(value) > max_length:
        raise InputError(name, f"must be at most {max_length} characters")
    if pattern is not None and not pattern.fullmatch(value):
        raise InputError(name, "has an invalid format")
    return value


def _voicemail_id(args: Mapping[str, Any]) -> str | None:
    return _string(args, "voicemail_id", pattern=_VOICEMAIL_ID_RE)


def parse_list_voicemails(arguments: Mapping[str, Any] | None) -> ListVoicemailsArgs:
    args = _object(arguments, frozenset({"limit", "cursor"}))
    return ListVoicemailsArgs(
        limit=_integer(args, "limit", 1, 5, 5),
        cursor=_string(args, "cursor", max_length=64),
    )


def parse_check_voicemail(arguments: Mapping[str, Any] | None) -> CheckVoicemailArgs:
    args = _object(arguments, frozenset({"voicemail_id", "caller_hint"}))
    if "voicemail_id" in args and "caller_hint" in args:
        raise InputError("caller_hint", "cannot be combined with voicemail_id")
    hint = _string(args, "caller_hint", min_length=1, max_length=80)
    if hint is not None:
        try:
            hint = clean_text(hint, max_length=80)
        except UnsafeInputError as exc:
            raise InputError("caller_hint", "is empty after removing invisible characters") from exc
        if looks_like_instruction(hint):
            emit({"InjectionSuspected": (1, "Count")}, Tool="check_voicemail")
            raise InputError("caller_hint", "reads like an instruction, not a caller")
    return CheckVoicemailArgs(voicemail_id=_voicemail_id(args), caller_hint=hint)


def parse_explain_red_flags(arguments: Mapping[str, Any] | None) -> ExplainRedFlagsArgs:
    args = _object(arguments, frozenset({"voicemail_id", "start", "max_flags"}))
    voicemail_id = _voicemail_id(args)
    if voicemail_id is None:
        raise InputError("voicemail_id", "is required")
    return ExplainRedFlagsArgs(
        voicemail_id=voicemail_id,
        start=_integer(args, "start", 0, 11, 0),
        max_flags=_integer(args, "max_flags", 1, 5, 3),
    )


def parse_action(arguments: Mapping[str, Any] | None) -> ActionArgs:
    """Decide stage vs resolve mode (docs/architecture.md §6.4) and validate accordingly."""
    args = _object(arguments, frozenset({"voicemail_id", "approval_token", "decision"}))
    voicemail_id = _voicemail_id(args)
    token = _string(args, "approval_token", pattern=_APPROVAL_TOKEN_RE)
    decision = _string(args, "decision")
    if decision is not None and decision not in ("approve", "reject"):
        raise InputError("decision", "must be 'approve' or 'reject'")

    if token is None and decision is None:
        if voicemail_id is None:
            raise InputError("voicemail_id", "is required to prepare an action")
        return StageActionArgs(voicemail_id=voicemail_id)
    if token is None:
        raise InputError("approval_token", "is required with decision")
    if decision is None:
        raise InputError("decision", "is required with approval_token")
    resolved: Decision = "approve" if decision == "approve" else "reject"
    return ResolveActionArgs(approval_token=token, decision=resolved, voicemail_id=voicemail_id)
