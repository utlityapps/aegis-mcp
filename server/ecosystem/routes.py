"""Starlette routes: signed Ring / wearable webhooks and the Fire TV card stream (Server-Sent Events)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Final

from jsonschema import Draft202012Validator
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from server.cache import TTLCache
from server.ecosystem.display import display_routes
from server.ecosystem.pipeline import EcosystemPipeline, PipelineOutcome
from server.observability import stream_outcome
from server.ecosystem.schemas import RING_EVENT_SCHEMA, WEARABLE_CONTEXT_SCHEMA
from server.ecosystem.security import (
    MIN_SECRET_LENGTH,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SignatureError,
    bearer_matches,
    verify,
)

MAX_WEBHOOK_BODY_BYTES: Final = 16 * 1024
BODY_READ_TIMEOUT_SECONDS: Final = 5.0
HEARTBEAT_SECONDS: Final = 15.0
SEEN_EVENT_TTL_SECONDS: Final = 600.0
SEEN_EVENT_MAX: Final = 4096

logger = logging.getLogger("aegis.ecosystem")

_REJECTION_OUTCOMES: Final[dict[int, str]] = {
    400: "invalid", 401: "signature", 408: "body_timeout", 409: "duplicate", 413: "too_large", 422: "invalid",
}

_RING_VALIDATOR = Draft202012Validator(RING_EVENT_SCHEMA)
_WEARABLE_VALIDATOR = Draft202012Validator(WEARABLE_CONTEXT_SCHEMA)


class _Rejected(Exception):
    def __init__(self, status: int, error: str, details: list[str] | None = None) -> None:
        super().__init__(error)
        self.status = status
        self.error = error
        self.details = details or []

    def response(self) -> JSONResponse:
        body: dict[str, Any] = {"error": self.error}
        if self.details:
            body["details"] = self.details
        return JSONResponse(body, status_code=self.status)


async def _read_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_WEBHOOK_BODY_BYTES):
        raise _Rejected(413, "request body too large")
    chunks = bytearray()
    try:
        async with asyncio.timeout(BODY_READ_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                chunks += chunk
                if len(chunks) > MAX_WEBHOOK_BODY_BYTES:
                    raise _Rejected(413, "request body too large")
    except TimeoutError as exc:
        raise _Rejected(408, "request body not received in time") from exc
    return bytes(chunks)


def _validated(body: bytes, validator: Draft202012Validator, time_field: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise _Rejected(400, "body is not valid JSON") from exc
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        # Report where and which rule failed, never the submitted values.
        details = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}" for e in errors[:5]]
        raise _Rejected(422, "payload does not match the schema", details)
    try:
        moment = datetime.fromisoformat(payload[time_field])
    except ValueError as exc:
        raise _Rejected(422, "payload does not match the schema", [f"{time_field}: date-time"]) from exc
    if moment.tzinfo is None:
        raise _Rejected(422, "payload does not match the schema", [f"{time_field}: timezone required"])
    return payload


def _accepted(outcome: PipelineOutcome) -> JSONResponse:
    body: dict[str, Any] = {"status": outcome.status}
    if outcome.visit_id:
        body["visit_id"] = outcome.visit_id
    if outcome.card:
        body["card_id"] = outcome.card["card_id"]
    if outcome.reason:
        body["reason"] = outcome.reason
    return JSONResponse(body, status_code=202)


def ecosystem_routes(pipeline: EcosystemPipeline, webhook_secret: str, display_token: str) -> list[Route]:
    if len(webhook_secret) < MIN_SECRET_LENGTH or len(display_token) < MIN_SECRET_LENGTH:
        raise ValueError(f"webhook secret and display token must each be at least {MIN_SECRET_LENGTH} characters")
    seen_events: TTLCache[str, bool] = TTLCache(SEEN_EVENT_MAX, SEEN_EVENT_TTL_SECONDS)

    async def authenticated_payload(request: Request, validator: Draft202012Validator, time_field: str) -> dict[str, Any]:
        body = await _read_limited(request)
        try:
            verify(webhook_secret, request.headers.get(TIMESTAMP_HEADER), request.headers.get(SIGNATURE_HEADER), body)
        except SignatureError as exc:
            logger.warning("rejected %s: %s", request.url.path, exc)
            raise _Rejected(401, "invalid or missing signature") from exc
        payload = _validated(body, validator, time_field)
        if seen_events.get(payload["event_id"]) is not None:
            raise _Rejected(409, "duplicate event_id")
        seen_events.set(payload["event_id"], True)
        return payload

    async def ring_webhook(request: Request) -> Response:
        try:
            event = await authenticated_payload(request, _RING_VALIDATOR, "occurred_at")
            outcome = pipeline.on_ring(event["event_type"], event["device"]["location"])
        except _Rejected as rejected:
            stream_outcome("ring_webhook", _REJECTION_OUTCOMES.get(rejected.status, "rejected"))
            return rejected.response()
        except ClientDisconnect:
            stream_outcome("ring_webhook", "client_gone")
            return Response(status_code=499)
        stream_outcome("ring_webhook", "accepted")
        return _accepted(outcome)

    async def wearable_webhook(request: Request) -> Response:
        try:
            context = await authenticated_payload(request, _WEARABLE_VALIDATOR, "captured_at")
            outcome = pipeline.on_wearable(context["speaker"], context["transcript"])
        except _Rejected as rejected:
            stream_outcome("bee_webhook", _REJECTION_OUTCOMES.get(rejected.status, "rejected"))
            return rejected.response()
        except ClientDisconnect:
            stream_outcome("bee_webhook", "client_gone")
            return Response(status_code=499)
        stream_outcome("bee_webhook", "accepted")
        return _accepted(outcome)

    async def firetv_stream(request: Request) -> Response:
        if not bearer_matches(request.headers.get("authorization"), display_token):
            stream_outcome("firetv_sse", "rejected_auth")
            return JSONResponse({"error": "invalid or missing display token"}, status_code=401)
        queue = pipeline.hub.subscribe()
        if queue is None:
            stream_outcome("firetv_sse", "rejected_capacity")
            return JSONResponse({"error": "too many displays connected"}, status_code=503)
        stream_outcome("firetv_sse", "opened")
        opened = time.monotonic()

        async def events() -> AsyncIterator[str]:
            try:
                yield "retry: 5000\n: connected\n\n"
                while True:
                    try:
                        card = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"event: card\nid: {card['card_id']}\ndata: {json.dumps(card, separators=(',', ':'))}\n\n"
            finally:
                pipeline.hub.unsubscribe(queue)
                stream_outcome("firetv_sse", "closed", ConnectionSeconds=(round(time.monotonic() - opened, 3), "Seconds"))

        headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        return StreamingResponse(events(), media_type="text/event-stream", headers=headers)

    return [
        Route("/webhooks/ring", ring_webhook, methods=["POST"]),
        Route("/webhooks/bee", wearable_webhook, methods=["POST"]),
        Route("/events/firetv", firetv_stream, methods=["GET"]),
        *display_routes(),
    ]
