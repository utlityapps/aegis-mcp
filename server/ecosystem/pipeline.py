"""Ring → wearable context → deterministic assessment → Fire TV card.

A doorbell or motion event opens a *visit*. Consented wearable transcripts heard during the
visit are scored by the same deterministic engine as voicemails (no model decides anything),
and every visit change becomes a display-only card broadcast to Fire TV subscribers.
Transcripts are analyzed in memory and dropped; only verdicts and warning-sign sentences remain.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Literal

from aegis.engine import Analysis, Verdict, analyze_voicemail
from aegis.security import looks_like_instruction
from aegis.telemetry import emit
from server.ecosystem.schemas import FIRETV_SCHEMA_ID

type Severity = Literal["info", "caution", "alert"]
type Location = Literal["front_door", "back_door", "driveway", "garage", "other"]

VISIT_WINDOW: Final = timedelta(minutes=2)
CARD_LIFETIME: Final = timedelta(minutes=5)
SUBSCRIBER_QUEUE_SIZE: Final = 16
MAX_SUBSCRIBERS: Final = 8

logger = logging.getLogger("aegis.ecosystem")

_LOCATION_WORDS: Final[dict[str, str]] = {
    "front_door": "front door",
    "back_door": "back door",
    "driveway": "driveway",
    "garage": "garage",
    "other": "house",
}
_SEVERITY: Final[dict[Verdict, Severity]] = {"SCAM": "alert", "SUSPICIOUS": "caution", "LEGITIMATE": "info"}
# Engine sentences are written for phone calls; these fixed rewrites fit a visitor at the door.
_VISITOR_WORDING: Final = (
    ("The caller", "The visitor"),
    ("The message", "The visitor"),
    ("over the phone", "at your door"),
    ("on a call", "with a visitor"),
)


class VisitState(StrEnum):
    OPEN = "OPEN"  # a doorbell or motion event, nothing heard yet
    ASSESSED = "ASSESSED"  # at least one consented transcript has been scored
    CLOSED = "CLOSED"  # the visit window passed


VISIT_TRANSITIONS: Final[dict[VisitState, frozenset[VisitState]]] = {
    VisitState.OPEN: frozenset({VisitState.ASSESSED, VisitState.CLOSED}),
    VisitState.ASSESSED: frozenset({VisitState.ASSESSED, VisitState.CLOSED}),
    VisitState.CLOSED: frozenset(),
}


class IllegalVisitTransitionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Visit:
    visit_id: str
    location: Location
    opened_at: datetime
    expires_at: datetime
    state: VisitState
    verdict: Verdict | None = None
    risk_score: int = 0
    evidence: tuple[str, ...] = ()


def advance(visit: Visit, target: VisitState, **changes: Any) -> Visit:
    if target not in VISIT_TRANSITIONS[visit.state]:
        raise IllegalVisitTransitionError(f"{visit.visit_id}: {visit.state} -> {target} is not allowed")
    return replace(visit, state=target, **changes)


def visitor_wording(sentence: str) -> str:
    for old, new in _VISITOR_WORDING:
        sentence = sentence.replace(old, new)
    return sentence


class CardHub:
    """Fan-out of cards to Fire TV subscribers. A slow subscriber loses its oldest cards, never blocks others."""

    def __init__(self, max_subscribers: int = MAX_SUBSCRIBERS) -> None:
        self._max = max_subscribers
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]] | None:
        if len(self._subscribers) >= self._max:
            return None
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, card: dict[str, Any]) -> int:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()  # drop the oldest card for this slow display
            queue.put_nowait(card)
        return len(self._subscribers)


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    status: Literal["card_published", "ignored"]
    visit_id: str | None = None
    card: dict[str, Any] | None = None
    reason: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EcosystemPipeline:
    """Single-household visit tracker. One active visit at a time; everything is in memory."""

    def __init__(self, hub: CardHub | None = None, clock: Callable[[], datetime] = _utcnow) -> None:
        self.hub = hub or CardHub()
        self._clock = clock
        self._visit: Visit | None = None

    @property
    def current_visit(self) -> Visit | None:
        self._expire()
        return self._visit

    def on_ring(self, event_type: Literal["doorbell_press", "motion"], location: Location) -> PipelineOutcome:
        self._expire()
        now = self._clock()
        if self._visit is not None:
            self._visit = advance(self._visit, VisitState.CLOSED)
        self._visit = Visit(
            visit_id=f"visit-{secrets.token_hex(8)}",
            location=location,
            opened_at=now,
            expires_at=now + VISIT_WINDOW,
            state=VisitState.OPEN,
        )
        where = _LOCATION_WORDS[location]
        title = f"Someone is at the {where}" if event_type == "doorbell_press" else f"Motion at the {where}"
        body = "Take your time. You don't have to open the door to anyone you don't know."
        return self._publish(self._visit, "info", title, body)

    def on_wearable(self, speaker: Literal["other", "wearer", "unknown"], transcript: str) -> PipelineOutcome:
        """Score what a visitor said. The transcript is used here and then dropped; it's never stored or shown."""
        if speaker == "wearer":
            return PipelineOutcome(status="ignored", reason="the wearer's own speech is not analyzed")
        self._expire()
        if self._visit is None:
            return PipelineOutcome(status="ignored", reason="no open visit to attach the context to")

        if looks_like_instruction(transcript):
            emit({"InjectionSuspected": (1, "Count")}, Stream="bee_webhook")  # scored anyway; no model reads it
        analysis: Analysis = analyze_voicemail(transcript)
        visit = self._visit
        if visit.verdict is None or analysis.risk_score > visit.risk_score:
            visit = advance(
                visit,
                VisitState.ASSESSED,
                verdict=analysis.verdict,
                risk_score=analysis.risk_score,
                evidence=tuple(visitor_wording(flag.say) for flag in analysis.flags[:3]),
            )
        else:
            visit = advance(visit, VisitState.ASSESSED)  # an earlier, riskier assessment of this visit stands
        self._visit = visit

        verdict = visit.verdict or "LEGITIMATE"
        match verdict:
            case "SCAM":
                title, body = (
                    "Warning: this visitor sounds like a scam",
                    "Don't pay or share personal details. You don't have to open the door. Call someone you trust.",
                )
            case "SUSPICIOUS":
                title, body = (
                    "Be careful with this visitor",
                    "Some warning signs. Don't pay or sign anything today. Ask someone you trust.",
                )
            case "LEGITIMATE":
                title, body = "No warning signs so far", "Aegis didn't hear anything that sounds like a scam."
        return self._publish(visit, _SEVERITY[verdict], title, body)

    def _expire(self) -> None:
        if self._visit is not None and self._clock() > self._visit.expires_at:
            advance(self._visit, VisitState.CLOSED)
            self._visit = None  # closed visits are forgotten, along with everything learned in them

    def _publish(self, visit: Visit, severity: Severity, title: str, body: str) -> PipelineOutcome:
        now = self._clock()
        buttons = [{"id": "dismiss", "label": "Dismiss"}]
        if severity != "info":
            buttons.insert(0, {"id": "ask_alexa", "label": "Ask Alexa"})
        card = {
            "schema": FIRETV_SCHEMA_ID,
            "card_id": f"card-{secrets.token_hex(8)}",
            "visit_id": visit.visit_id,
            "issued_at": now.isoformat(),
            "expires_at": (now + CARD_LIFETIME).isoformat(),
            "severity": severity,
            "title": title,
            "body": body,
            "evidence": list(visit.evidence),
            "threat": None if visit.verdict is None else {"verdict": visit.verdict, "risk_score": visit.risk_score},
            "buttons": buttons,
            "simulated": True,
        }
        delivered = self.hub.publish(card)
        logger.info(
            "card %s severity=%s visit=%s state=%s delivered_to=%d",
            card["card_id"], severity, visit.visit_id, visit.state, delivered,
        )
        return PipelineOutcome(status="card_published", visit_id=visit.visit_id, card=card)
