"""Ecosystem pipeline: schemas, signing, the visit state machine, privacy rules, and end-to-end timing."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from server.ecosystem.pipeline import (
    VISIT_TRANSITIONS,
    VISIT_WINDOW,
    CardHub,
    EcosystemPipeline,
    IllegalVisitTransitionError,
    Visit,
    VisitState,
    advance,
)
from server.ecosystem.schemas import (
    FIRETV_CARD_SCHEMA,
    RING_EVENT_SCHEMA,
    RING_SCHEMA_ID,
    WEARABLE_CONTEXT_SCHEMA,
    WEARABLE_SCHEMA_ID,
)
from server.ecosystem.security import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, bearer_matches, sign, verify
from server.server import build_app

SECRET = "test-webhook-secret-0123456789abcdef"
TOKEN = "test-display-token-0123456789abcdefg"
SCAM_SPEECH = (
    "Hi, I'm from the electric company. Your power will be shut off today unless you pay the overdue bill "
    "right now. We only take gift cards or Zelle. Don't tell anyone, it's a special arrangement."
)
CARD_VALIDATOR = Draft202012Validator(FIRETV_CARD_SCHEMA)
_counter = iter(range(10**9))


def ring_event(**overrides: Any) -> dict[str, Any]:
    event = {
        "schema": RING_SCHEMA_ID,
        "event_id": f"ring-evt-{next(_counter):08d}",
        "event_type": "doorbell_press",
        "device": {"device_id": "front-doorbell", "location": "front_door"},
        "occurred_at": datetime.now(UTC).isoformat(),
        "person_detected": True,
    }
    event.update(overrides)
    return event


def wearable_event(transcript: str = SCAM_SPEECH, **overrides: Any) -> dict[str, Any]:
    event = {
        "schema": WEARABLE_SCHEMA_ID,
        "event_id": f"bee-evt-{next(_counter):08d}",
        "device_id": "bee-pendant",
        "captured_at": datetime.now(UTC).isoformat(),
        "consent": {"all_parties_consented": True, "basis": "verbal"},
        "speaker": "other",
        "language": "en",
        "transcript": transcript,
    }
    event.update(overrides)
    return event


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


# ---------------------------------------------------------------- schemas


@pytest.mark.parametrize("schema", [RING_EVENT_SCHEMA, WEARABLE_CONTEXT_SCHEMA, FIRETV_CARD_SCHEMA])
def test_schemas_are_valid_and_closed(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False


def test_example_payloads_validate() -> None:
    Draft202012Validator(RING_EVENT_SCHEMA).validate(ring_event())
    Draft202012Validator(WEARABLE_CONTEXT_SCHEMA).validate(wearable_event())


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_type": "package_delivered"},
        {"device": {"device_id": "x", "location": "roof"}},
        {"event_id": "short"},
        {"extra": 1},
        {"schema": "ring.official/v9"},
    ],
)
def test_bad_ring_payloads_fail_schema(overrides: dict[str, Any]) -> None:
    assert not Draft202012Validator(RING_EVENT_SCHEMA).is_valid(ring_event(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"consent": {"all_parties_consented": False, "basis": "verbal"}},
        {"consent": {"basis": "verbal"}},
        {"language": "es"},
        {"speaker": "neighbor"},
        {"transcript": "x" * 2001},
        {"transcript": ""},
        {"audio_url": "https://example.com/a.wav"},
    ],
)
def test_bad_wearable_payloads_fail_schema(overrides: dict[str, Any]) -> None:
    assert not Draft202012Validator(WEARABLE_CONTEXT_SCHEMA).is_valid(wearable_event(**overrides))


# ---------------------------------------------------------------- signing


def test_signature_round_trip_and_failures() -> None:
    body, now = b'{"a":1}', 1_800_000_000
    good = sign(SECRET, str(now), body)
    verify(SECRET, str(now), good, body, clock=lambda: now)
    cases = [
        (str(now), sign("another-secret-0123456789abcdefghij", str(now), body)),  # wrong key
        (str(now), good.replace("v1=", "v1=0")),  # tampered
        (str(now - 301), sign(SECRET, str(now - 301), body)),  # stale
        (str(now + 301), sign(SECRET, str(now + 301), body)),  # future
        ("12.5", good),  # malformed timestamp
        (None, good),
        (str(now), None),
    ]
    for timestamp, signature in cases:
        with pytest.raises(SignatureError):
            verify(SECRET, timestamp, signature, body, clock=lambda: now)
    with pytest.raises(SignatureError):
        verify(SECRET, str(now), good, b'{"a":2}', clock=lambda: now)  # body changed


def test_bearer_token_check() -> None:
    assert bearer_matches(f"Bearer {TOKEN}", TOKEN) and bearer_matches(f"bearer {TOKEN}", TOKEN)
    assert not bearer_matches(None, TOKEN) and not bearer_matches(TOKEN, TOKEN)
    assert not bearer_matches("Bearer wrong", TOKEN) and not bearer_matches("Bearer é", TOKEN)


# ---------------------------------------------------- visit state machine


@pytest.mark.parametrize("source", list(VisitState))
@pytest.mark.parametrize("target", list(VisitState))
def test_visit_transitions_are_enforced(source: VisitState, target: VisitState) -> None:
    now = datetime.now(UTC)
    visit = Visit("visit-0000000000000000", "front_door", now, now, source)
    if target in VISIT_TRANSITIONS[source]:
        assert advance(visit, target).state is target
    else:
        with pytest.raises(IllegalVisitTransitionError):
            advance(visit, target)


def test_wearable_context_needs_an_open_visit() -> None:
    clock = FakeClock()
    pipeline = EcosystemPipeline(clock=clock)
    assert pipeline.on_wearable("other", SCAM_SPEECH).status == "ignored"
    pipeline.on_ring("doorbell_press", "front_door")
    clock.now += VISIT_WINDOW + timedelta(seconds=1)
    assert pipeline.on_wearable("other", SCAM_SPEECH).status == "ignored"
    assert pipeline.current_visit is None


def test_wearers_own_speech_is_not_analyzed() -> None:
    pipeline = EcosystemPipeline()
    pipeline.on_ring("doorbell_press", "front_door")
    outcome = pipeline.on_wearable("wearer", SCAM_SPEECH)
    assert outcome.status == "ignored" and pipeline.current_visit and pipeline.current_visit.verdict is None


def test_a_riskier_assessment_stands_when_later_speech_is_benign() -> None:
    pipeline = EcosystemPipeline()
    pipeline.on_ring("doorbell_press", "front_door")
    pipeline.on_wearable("other", SCAM_SPEECH)
    card = pipeline.on_wearable("other", "Okay, thanks, have a nice day.").card
    assert card and card["severity"] == "alert" and card["threat"]["verdict"] == "SCAM"


def test_new_doorbell_closes_the_previous_visit() -> None:
    pipeline = EcosystemPipeline()
    first = pipeline.on_ring("doorbell_press", "front_door").visit_id
    pipeline.on_wearable("other", SCAM_SPEECH)
    second = pipeline.on_ring("motion", "driveway").visit_id
    visit = pipeline.current_visit
    assert first != second and visit and visit.verdict is None and visit.state is VisitState.OPEN


# ------------------------------------------------------------------ privacy


def test_transcripts_never_reach_cards_state_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    pipeline = EcosystemPipeline()
    with caplog.at_level(logging.DEBUG):
        pipeline.on_ring("doorbell_press", "front_door")
        card = pipeline.on_wearable("other", SCAM_SPEECH).card
    assert card is not None
    for fragment in ("electric company", "Zelle", "special arrangement"):
        assert fragment not in json.dumps(card) and fragment not in caplog.text
        assert fragment not in repr(pipeline.current_visit)


def test_cards_validate_and_buttons_never_take_action() -> None:
    pipeline = EcosystemPipeline()
    cards = [pipeline.on_ring("doorbell_press", "front_door").card, pipeline.on_wearable("other", SCAM_SPEECH).card]
    for card in cards:
        assert card is not None
        CARD_VALIDATOR.validate(card)
        assert {b["id"] for b in card["buttons"]} <= {"ask_alexa", "dismiss"}
    assert cards[1] and "The visitor" in cards[1]["evidence"][0] and "caller" not in " ".join(cards[1]["evidence"])


def test_slow_display_drops_oldest_cards_without_blocking() -> None:
    hub = CardHub(max_subscribers=1)

    async def scenario() -> list[int]:
        queue = hub.subscribe()
        assert queue is not None and hub.subscribe() is None
        for i in range(40):
            hub.publish({"n": i})
        return [queue.get_nowait()["n"] for _ in range(queue.qsize())]

    received = asyncio.run(scenario())
    assert received == list(range(24, 40))


# ------------------------------------------------------------ HTTP (ASGI)


async def asgi(
    app: Any, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None
) -> tuple[int, bytes]:
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    raw_headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": raw_headers, "scheme": "http", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 5000),
        "http_version": "1.1", "root_path": "",
    }
    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def signed(payload: dict[str, Any] | bytes, secret: str = SECRET) -> tuple[bytes, dict[str, str]]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    return body, {"content-type": "application/json", TIMESTAMP_HEADER: timestamp, SIGNATURE_HEADER: sign(secret, timestamp, body)}


def app_with(pipeline: EcosystemPipeline) -> Any:
    return build_app(webhook_secret=SECRET, display_token=TOKEN, pipeline=pipeline)


def test_end_to_end_ring_then_wearable_reaches_display_under_200ms() -> None:
    pipeline = EcosystemPipeline()
    app = app_with(pipeline)

    async def scenario() -> tuple[float, list[dict[str, Any]], list[int]]:
        display = pipeline.hub.subscribe()
        assert display is not None
        started = time.perf_counter()
        ring_status, _ = await asgi(app, "POST", "/webhooks/ring", *signed(ring_event()))
        bee_status, _ = await asgi(app, "POST", "/webhooks/bee", *signed(wearable_event()))
        cards = [await asyncio.wait_for(display.get(), timeout=1) for _ in range(2)]
        return (time.perf_counter() - started) * 1000, cards, [ring_status, bee_status]

    elapsed_ms, cards, statuses = asyncio.run(scenario())
    assert statuses == [202, 202]
    assert [c["severity"] for c in cards] == ["info", "alert"]
    assert cards[1]["threat"] == {"verdict": "SCAM", "risk_score": 65}
    for card in cards:
        CARD_VALIDATOR.validate(card)
    assert elapsed_ms < 200, f"pipeline took {elapsed_ms:.1f} ms"


def test_firetv_stream_delivers_cards_as_server_sent_events() -> None:
    pipeline = EcosystemPipeline()
    app = app_with(pipeline)

    async def scenario() -> str:
        chunks: list[bytes] = []
        got_card = asyncio.Event()
        disconnect = asyncio.Event()

        async def receive() -> dict[str, Any]:
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if b"event: card" in b"".join(chunks):
                    got_card.set()

        scope = {
            "type": "http", "method": "GET", "path": "/events/firetv", "raw_path": b"/events/firetv",
            "query_string": b"", "headers": [(b"authorization", f"Bearer {TOKEN}".encode())], "scheme": "http",
            "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 5000), "http_version": "1.1", "root_path": "",
        }
        stream = asyncio.create_task(app(scope, receive, send))
        while pipeline.hub.subscriber_count == 0:
            await asyncio.sleep(0.001)
        pipeline.on_ring("doorbell_press", "front_door")
        await asyncio.wait_for(got_card.wait(), timeout=2)
        disconnect.set()
        await asyncio.wait_for(stream, timeout=2)
        assert pipeline.hub.subscriber_count == 0  # the display was unsubscribed on disconnect
        return b"".join(chunks).decode()

    text = asyncio.run(scenario())
    assert text.startswith("retry: 5000\n: connected\n\n")
    data = next(line[6:] for line in text.splitlines() if line.startswith("data: "))
    CARD_VALIDATOR.validate(json.loads(data))


@pytest.mark.parametrize(
    ("path", "make", "expected"),
    [
        ("/webhooks/ring", lambda: (json.dumps(ring_event()).encode(), {"content-type": "application/json"}), 401),
        ("/webhooks/ring", lambda: signed(ring_event(), secret="not-the-secret-0123456789abcdefghij"), 401),
        ("/webhooks/ring", lambda: signed(b'{"schema": '), 400),
        ("/webhooks/ring", lambda: signed(ring_event(event_type="explosion")), 422),
        ("/webhooks/ring", lambda: signed(ring_event(occurred_at="2026-10-04T12:00:00")), 422),
        ("/webhooks/bee", lambda: signed(wearable_event(consent={"all_parties_consented": False, "basis": "verbal"})), 422),
        ("/webhooks/bee", lambda: signed(b"x" * (17 * 1024)), 413),
    ],
)
def test_webhook_rejections(path: str, make: Any, expected: int) -> None:
    status, body = asyncio.run(asgi(app_with(EcosystemPipeline()), "POST", path, *make()))
    assert status == expected
    assert b"explosion" not in body  # submitted values are never echoed back


def test_replayed_event_is_rejected() -> None:
    app = app_with(EcosystemPipeline())
    event = ring_event()

    async def scenario() -> list[int]:
        first, _ = await asgi(app, "POST", "/webhooks/ring", *signed(event))
        again, _ = await asgi(app, "POST", "/webhooks/ring", *signed(event))
        return [first, again]

    assert asyncio.run(scenario()) == [202, 409]


def test_display_stream_requires_token_and_caps_subscribers() -> None:
    pipeline = EcosystemPipeline(hub=CardHub(max_subscribers=0))
    app = app_with(pipeline)
    assert asyncio.run(asgi(app, "GET", "/events/firetv"))[0] == 401
    assert asyncio.run(asgi(app, "GET", "/events/firetv", headers={"authorization": f"Bearer {TOKEN}"}))[0] == 503


def test_endpoints_are_absent_without_secrets() -> None:
    app = build_app()
    assert asyncio.run(asgi(app, "POST", "/webhooks/ring", *signed(ring_event())))[0] == 404


def test_short_secrets_are_refused() -> None:
    with pytest.raises(ValueError):
        build_app(webhook_secret="short", display_token=TOKEN)
