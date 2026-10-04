"""Tracing and metrics for the server: OpenTelemetry spans plus CloudWatch EMF metrics.

Spans use the OpenTelemetry API, which `mcp` already depends on. With no SDK or exporter configured
they cost nothing; install `opentelemetry-sdk` and an exporter (for example the AWS Distro for
OpenTelemetry collector, for X-Ray) to ship them. Metrics go out as EMF log lines (aegis.telemetry).

Nothing here records arguments, transcripts, tokens, phone numbers or voicemail ids.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Final

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from aegis.engine import TransitionEvent
from aegis.telemetry import emit

SERVICE_VERSION: Final = "0.1.0"
KNOWN_TOOLS: Final = frozenset({"list_voicemails", "check_voicemail", "explain_red_flags", "block_number", "report_scam"})
ERROR_STATUSES: Final = frozenset({"invalid_input", "not_found", "permission_denied", "token_expired", "unavailable", "timeout", "unknown_tool"})

tracer = trace.get_tracer("aegis.server", SERVICE_VERSION)


@contextmanager
def tool_span(tool: str) -> Iterator[Span]:
    """A span for one tool call. Attributes are the tool name and outcome only, never arguments."""
    safe_tool = tool if tool in KNOWN_TOOLS else "unknown"
    with tracer.start_as_current_span(f"aegis.tool {safe_tool}", attributes={"aegis.tool": safe_tool}) as span:
        yield span


def finish_tool(span: Span, tool: str, status: str, mode: str | None, latency_ms: float) -> None:
    safe_tool = tool if tool in KNOWN_TOOLS else "unknown"
    span.set_attribute("aegis.status", status)
    if mode:
        span.set_attribute("aegis.mode", mode)
    if status in ERROR_STATUSES:
        span.set_status(Status(StatusCode.ERROR, status))
    emit({"ToolLatency": (latency_ms, "Milliseconds"), "ToolCalls": (1, "Count")}, Tool=safe_tool, Status=status)


def stream_outcome(stream: str, outcome: str, **extra: tuple[float, Any]) -> None:
    """Count a transport event, e.g. Stream=mcp_post Outcome=accepted|body_timeout|client_gone|too_large."""
    trace.get_current_span().add_event("aegis.stream", {"aegis.stream": stream, "aegis.outcome": outcome})
    emit({"StreamEvents": (1, "Count"), **extra}, Stream=stream, Outcome=outcome)


def observe_transition(event: TransitionEvent) -> None:
    """ActionLedger observer: span event plus a transition count and how long the person took to decide."""
    attributes: Mapping[str, str | float] = {
        "aegis.kind": event.kind,
        "aegis.from": event.source,
        "aegis.to": event.target,
        "aegis.age_seconds": round(event.age_seconds, 3),
    }
    trace.get_current_span().add_event("aegis.fsm.transition", attributes)
    metrics: dict[str, tuple[float, Any]] = {"StateTransitions": (1, "Count")}
    if event.target != "PENDING" or event.source != "NONE":
        metrics["ActionAgeSeconds"] = (round(event.age_seconds, 3), "Seconds")
    emit(metrics, Kind=event.kind, From=event.source, To=event.target)


def record_bedrock_usage(model_id: str, input_tokens: int, output_tokens: int, latency_ms: float) -> None:
    """For the Strands agent (docs/aws_bedrock_integration.md): one EMF line per Bedrock turn."""
    emit(
        {
            "BedrockInputTokens": (input_tokens, "Count"),
            "BedrockOutputTokens": (output_tokens, "Count"),
            "BedrockLatency": (latency_ms, "Milliseconds"),
        },
        Model=model_id,
    )
