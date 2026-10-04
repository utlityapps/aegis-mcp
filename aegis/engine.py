"""Aegis analysis engine: deterministic scam heuristics and the approve-to-act ledger.

Stdlib only. No network, no LLM. Every verdict comes from the twelve fixed
heuristics below, and every heuristic carries one plain sentence written to be
read aloud to an older adult.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from aegis.security.sanitize import normalize_text

type Verdict = Literal["SCAM", "SUSPICIOUS", "LEGITIMATE"]
type ActionKind = Literal["block_number", "report_scam"]

VERDICT_SCAM_AT = 60
VERDICT_SUSPICIOUS_AT = 30
RISK_SCORE_MAX = 100
DEFAULT_APPROVAL_TTL_SECONDS = 600
APPROVAL_TTL_RANGE = (60, 3600)

_FLAGS = re.IGNORECASE


@dataclass(frozen=True, slots=True)
class Heuristic:
    """A fixed detector. It fires when every pattern in `patterns` matches."""

    flag_id: str
    weight: int
    say: str
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, transcript: str) -> bool:
        return all(pattern.search(transcript) for pattern in self.patterns)


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, _FLAGS)


HEURISTICS: tuple[Heuristic, ...] = (
    Heuristic(
        "government_impersonation",
        25,
        "The caller claims to be from a government agency. Real agencies usually write to you first.",
        (
            _rx(
                r"\b(irs|internal revenue( service)?|social security (administration|office)|medicare"
                r"|department of (the )?treasury|federal (agent|officer)|u\.?s\.? marshals?)\b"
            ),
        ),
    ),
    Heuristic(
        "arrest_threat",
        25,
        "The caller threatens arrest or a lawsuit. Real agencies don't threaten you over the phone.",
        (
            _rx(
                r"\b(warrants? for your arrest|arrest warrant|(will|could) be arrested|legal action"
                r"|lawsuit|sue you|deport(ed|ation)?)\b"
            ),
        ),
    ),
    Heuristic(
        "urgency_pressure",
        15,
        "The caller is rushing you. Scammers want you to act before you can check.",
        (
            _rx(
                r"\b(immediately|right away|right now|urgent(ly)?|final notice|last warning|act now"
                r"|within (the next )?(\d+|twenty[- ]four) hours|as soon as possible)\b"
            ),
        ),
    ),
    Heuristic(
        "payment_gift_card",
        30,
        "The caller wants payment by gift card, wire transfer or cryptocurrency. Real agencies never ask for that.",
        (
            _rx(
                r"\b(gift ?cards?|google play cards?|itunes cards?|prepaid (debit )?cards?|wire transfer"
                r"|western union|moneygram|bitcoin|crypto(currency)?|cash app|zelle)\b"
            ),
        ),
    ),
    Heuristic(
        "sensitive_info_request",
        25,
        "The caller asks for private numbers, like your Social Security number or PIN. Never share those on a call.",
        (
            _rx(
                r"\b(verify|confirm|give|provide|need|read|tell|share|update)\b[^.?!]{0,60}"
                r"\b(social security number|ssn|pin|password|account number|card number|medicare number"
                r"|routing number|date of birth)\b"
            ),
        ),
    ),
    Heuristic(
        "secrecy_request",
        20,
        "The caller asks you to keep this secret. Scammers do that so your family can't warn you.",
        (
            _rx(
                r"\b(do not|don't|dont) tell (anyone|anybody|your family|mom|dad)\b"
                r"|\bkeep (this|it) (between us|a secret|secret|quiet)\b"
                r"|\b(do not|don't|dont) (call|contact|talk to) (anyone|anybody)\b"
            ),
        ),
    ),
    Heuristic(
        "family_emergency",
        20,
        "The caller says a grandchild is in trouble and needs money. Call your family directly to check.",
        (
            _rx(r"\b(grandma|grandpa|grandmother|grandfather|granny)\b"),
            _rx(r"\b(jail|bail|arrested|accident|hospital|in trouble|lawyer)\b"),
        ),
    ),
    Heuristic(
        "account_suspended",
        15,
        "The caller says your account or benefits are suspended. That's a common scare tactic.",
        (
            _rx(
                r"\b(account|card|benefits|number|license)\b[^.?!]{0,30}\b(has been|have been|will be|is|are|was)\b"
                r"[^.?!]{0,15}\b(suspended|locked|frozen|blocked|deactivated|compromised|cancell?ed)\b"
            ),
        ),
    ),
    Heuristic(
        "press_button_callback",
        10,
        "The message pushes you to press a button or call back fast. Look up the real number yourself.",
        (
            _rx(
                r"\bpress (1|one|2|two|9|nine)\b"
                r"|\bcall (us|me|this number) back (immediately|right away|right now|now|within)\b"
            ),
        ),
    ),
    Heuristic(
        "prize_offer",
        15,
        "The caller says you won a prize. Real prizes never ask you to pay first.",
        (_rx(r"\byou(?:'ve| have) (won|been selected)\b|\b(prize|lottery|sweepstakes|claim your)\b"),),
    ),
    Heuristic(
        "remote_access",
        20,
        "The caller wants to get into your computer. Never let a stranger control your computer.",
        (
            _rx(
                r"\b(remote access|anydesk|teamviewer|install (an|this|the) app"
                r"|your computer (has|is) (a virus|infected|been hacked)|tech(nical)? support)\b"
            ),
        ),
    ),
    Heuristic(
        "suspicious_charge",
        10,
        "The caller mentions a suspicious charge. Check it by calling the number on your card instead.",
        (_rx(r"\b(suspicious|unauthori[sz]ed|fraudulent) (charges?|purchases?|activity|transactions?|orders?|logins?)\b"),),
    ),
)

HEURISTICS_COUNT = len(HEURISTICS)


@dataclass(frozen=True, slots=True)
class RedFlag:
    flag_id: str
    weight: int
    say: str


@dataclass(frozen=True, slots=True)
class Analysis:
    raw_score: int
    risk_score: int
    verdict: Verdict
    flags: tuple[RedFlag, ...]


def verdict_for(raw_score: int) -> Verdict:
    if raw_score >= VERDICT_SCAM_AT:
        return "SCAM"
    if raw_score >= VERDICT_SUSPICIOUS_AT:
        return "SUSPICIOUS"
    return "LEGITIMATE"


def analyze_voicemail(transcript: str) -> Analysis:
    """Run all heuristics over a transcript. Flags are ordered by weight, then id.

    The text is NFKC-normalized and stripped of invisible characters first, so look-alike or
    zero-width obfuscation ("ｇｉｆｔ ｃａｒｄ", "gift\u200bcard") can't dodge the fixed patterns.
    """
    text = normalize_text(transcript)
    fired = [RedFlag(h.flag_id, h.weight, h.say) for h in HEURISTICS if h.matches(text)]
    fired.sort(key=lambda flag: (-flag.weight, flag.flag_id))
    raw_score = sum(flag.weight for flag in fired)
    return Analysis(
        raw_score=raw_score,
        risk_score=min(raw_score, RISK_SCORE_MAX),
        verdict=verdict_for(raw_score),
        flags=tuple(fired),
    )


def explain_red_flags(analysis: Analysis, start: int = 0, limit: int | None = None) -> tuple[RedFlag, ...]:
    """Return a window of the analysis' red flags, in their fixed order."""
    end = None if limit is None else start + limit
    return analysis.flags[start:end]


def propose_actions(analysis: Analysis) -> tuple[ActionKind, ...]:
    """Actions worth offering for a verdict. Advisory only: nothing is staged."""
    match analysis.verdict:
        case "SCAM":
            return ("block_number", "report_scam")
        case "SUSPICIOUS":
            return ("block_number",)
        case "LEGITIMATE":
            return ()


# --------------------------------------------------------------------------
# Permission layer: propose -> approve -> execute, with single-use tokens.
# --------------------------------------------------------------------------


class ApprovalError(PermissionError):
    """A token was unknown, already used, for another action kind, or for another voicemail."""


class ApprovalExpiredError(ApprovalError):
    """A token was valid but its staged action passed its expiry time."""


class IllegalTransitionError(ApprovalError):
    """A staged action was asked to move to a state the state machine does not allow."""


class ActionState(StrEnum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# The only legal moves for a staged action (docs/architecture.md §9.2). PENDING -> PENDING is a
# re-stage that rotates the token; the three other states are terminal.
TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    ActionState.PENDING: frozenset(
        {ActionState.PENDING, ActionState.EXECUTED, ActionState.REJECTED, ActionState.EXPIRED}
    ),
    ActionState.EXECUTED: frozenset(),
    ActionState.REJECTED: frozenset(),
    ActionState.EXPIRED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class PendingAction:
    action_id: str
    kind: ActionKind
    voicemail_id: str
    caller_number: str
    created_at: datetime
    expires_at: datetime
    state: ActionState


def transition(action: PendingAction, target: ActionState, *, expires_at: datetime | None = None) -> PendingAction:
    """Return `action` moved to `target`, or raise IllegalTransitionError. The input is never mutated."""
    if target not in TRANSITIONS[action.state]:
        raise IllegalTransitionError(f"{action.action_id}: {action.state} -> {target} is not allowed")
    return replace(action, state=target, expires_at=expires_at or action.expires_at)


@dataclass(frozen=True, slots=True)
class TransitionEvent:
    """What an observer learns about a state change. No tokens, ids of people, or caller numbers."""

    kind: ActionKind
    source: str  # an ActionState value, or "NONE" for a brand-new action
    target: str
    age_seconds: float  # time since the action was first staged


@dataclass(frozen=True, slots=True)
class Receipt:
    receipt_id: str
    kind: ActionKind
    voicemail_id: str
    caller_number: str
    executed_at: datetime
    simulated: bool = True


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """Either a freshly staged (or re-staged) action with its token, or the earlier receipt."""

    action: PendingAction | None
    approval_token: str | None
    already_done: Receipt | None


@dataclass(slots=True)
class _Entry:
    action: PendingAction
    token_digest: bytes


def _digest(token: str) -> bytes:
    try:
        return hashlib.sha256(token.encode("ascii")).digest()
    except UnicodeEncodeError as exc:
        raise ApprovalError("approval token is not one this server issued") from exc


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ActionLedger:
    """In-memory pending actions plus the simulated block and report registries.

    Not thread-safe: callers serialize access (the MCP server holds one asyncio.Lock).
    Everything here vanishes when the process restarts.
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
        observer: Callable[[TransitionEvent], None] | None = None,
    ) -> None:
        low, high = APPROVAL_TTL_RANGE
        if not low <= ttl_seconds <= high:
            raise ValueError(f"ttl_seconds must be between {low} and {high}, got {ttl_seconds}")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._observer = observer
        self._entries: dict[str, _Entry] = {}
        self._blocked: dict[str, Receipt] = {}
        self._reported: dict[str, Receipt] = {}

    def _notify(self, action: PendingAction, source: str, target: ActionState) -> None:
        if self._observer is None:
            return
        age = (self._clock() - action.created_at).total_seconds()
        try:
            self._observer(TransitionEvent(action.kind, source, target.value, age))
        except Exception:  # telemetry must never change what the ledger does
            logging.getLogger("aegis.engine").exception("transition observer failed")

    def completed(self, kind: ActionKind, voicemail_id: str, caller_number: str) -> Receipt | None:
        """The receipt of an earlier executed action on the same target, if any."""
        if kind == "block_number":
            return self._blocked.get(caller_number)
        return self._reported.get(voicemail_id)

    def stage_action(self, kind: ActionKind, voicemail_id: str, caller_number: str) -> StageOutcome:
        if (receipt := self.completed(kind, voicemail_id, caller_number)) is not None:
            return StageOutcome(action=None, approval_token=None, already_done=receipt)

        now = self._clock()
        token = secrets.token_urlsafe(32)
        existing = self._find_pending(kind, voicemail_id, now)
        if existing is not None:
            action = transition(existing.action, ActionState.PENDING, expires_at=now + self._ttl)
            existing.action = action
            existing.token_digest = _digest(token)
            self._notify(action, ActionState.PENDING.value, ActionState.PENDING)
            return StageOutcome(action=action, approval_token=token, already_done=None)

        action = PendingAction(
            action_id=f"act-{secrets.token_hex(8)}",
            kind=kind,
            voicemail_id=voicemail_id,
            caller_number=caller_number,
            created_at=now,
            expires_at=now + self._ttl,
            state=ActionState.PENDING,
        )
        self._entries[action.action_id] = _Entry(action=action, token_digest=_digest(token))
        self._notify(action, "NONE", ActionState.PENDING)
        return StageOutcome(action=action, approval_token=token, already_done=None)

    def approve_action(self, token: str, kind: ActionKind, voicemail_id: str | None = None) -> Receipt:
        entry = self._claim(token, kind, voicemail_id)
        action = transition(entry.action, ActionState.EXECUTED)  # checked before any side effect
        receipt = Receipt(
            receipt_id=f"rcpt-{secrets.token_hex(8)}",
            kind=action.kind,
            voicemail_id=action.voicemail_id,
            caller_number=action.caller_number,
            executed_at=self._clock(),
        )
        if action.kind == "block_number":
            self._blocked[action.caller_number] = receipt
        else:
            self._reported[action.voicemail_id] = receipt
        del self._entries[action.action_id]
        self._notify(action, ActionState.PENDING.value, ActionState.EXECUTED)
        return receipt

    def reject_action(self, token: str, kind: ActionKind, voicemail_id: str | None = None) -> PendingAction:
        entry = self._claim(token, kind, voicemail_id)
        rejected = transition(entry.action, ActionState.REJECTED)
        del self._entries[entry.action.action_id]
        self._notify(rejected, ActionState.PENDING.value, ActionState.REJECTED)
        return rejected

    def _find_pending(self, kind: ActionKind, voicemail_id: str, now: datetime) -> _Entry | None:
        for action_id, entry in list(self._entries.items()):
            action = entry.action
            if action.kind != kind or action.voicemail_id != voicemail_id:
                continue
            if now > action.expires_at:
                del self._entries[action_id]
                return None
            return entry
        return None

    def _claim(self, token: str, kind: ActionKind, voicemail_id: str | None) -> _Entry:
        """Find the entry a token unlocks and check it may be resolved by this caller.

        Kind and target mismatches leave the token usable; expiry consumes it.
        """
        presented = _digest(token)
        match = next(
            (e for e in self._entries.values() if hmac.compare_digest(e.token_digest, presented)),
            None,
        )
        if match is None:
            raise ApprovalError("unknown or already used approval token")
        if match.action.kind != kind:
            raise ApprovalError(f"token belongs to a {match.action.kind} action, not {kind}")
        if voicemail_id is not None and voicemail_id != match.action.voicemail_id:
            raise ApprovalError("token belongs to a different voicemail")
        if self._clock() > match.action.expires_at:
            transition(match.action, ActionState.EXPIRED)
            del self._entries[match.action.action_id]
            self._notify(match.action, ActionState.PENDING.value, ActionState.EXPIRED)
            raise ApprovalExpiredError("approval token expired")
        if match.action.state is not ActionState.PENDING:
            # Only PENDING actions are ever stored; anything else is a broken invariant, so fail closed.
            raise IllegalTransitionError(f"{match.action.action_id} is {match.action.state}, not PENDING")
        return match
