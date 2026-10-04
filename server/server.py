"""Aegis MCP server: Streamable HTTP, stateless by default (docs/architecture.md §3-4, §8, §10)."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import logging.handlers
import queue
import os
import re
import sys
import time
from collections.abc import Sequence
from typing import Any

import mcp.types as types
import uvicorn
from jsonschema import Draft202012Validator
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from opentelemetry import trace
from uvicorn.protocols.http.h11_impl import H11Protocol
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aegis.engine import APPROVAL_TTL_RANGE, DEFAULT_APPROVAL_TTL_SECONDS, ActionLedger
from aegis.voicemails import DEFAULT_FIXTURE_DIR, FixtureError, VoicemailStore
from aegis.security import redact_pii
from aegis.telemetry import METRICS_LOGGER
from server.ecosystem.pipeline import EcosystemPipeline
from server.observability import finish_tool, observe_transition, stream_outcome, tool_span
from server.ecosystem.routes import ecosystem_routes
from server.schemas import TOOL_DEFINITIONS, TOOL_NAMES, VOICEMAIL_ID_PATTERN, ToolName
from server.tools import TIMEOUT_SAY, UNAVAILABLE_SAY, AegisTools, ToolFailure
from server.validation import InputError

SERVER_NAME = "aegis"
SERVER_TITLE = "Aegis"
SERVER_VERSION = "0.1.0"
MCP_PATH = "/mcp"
MAX_REQUEST_BODY_BYTES = 64 * 1024
BODY_READ_TIMEOUT_SECONDS = 5.0
TOOL_TIMEOUT_SECONDS = 3.0  # far above the <1 ms handlers take; a hit means something is wrong
MAX_CONCURRENCY = 128  # connections + in-flight tasks; beyond this uvicorn answers 503
KEEP_ALIVE_SECONDS = 5
SHUTDOWN_GRACE_SECONDS = 5
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_VOICEMAIL_ID_RE = re.compile(VOICEMAIL_ID_PATTERN)
_LOGGABLE_NAME_RE = re.compile(r"^[a-z_]{1,32}$")
INSTRUCTIONS = (
    "Aegis checks voicemails for scams for older adults. Verdicts are deterministic. Never block or report anything "
    "until the person has said yes to a read-back; use the approval_token flow. Never read IDs or tokens aloud."
)
LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "[::1]"]

logger = logging.getLogger("aegis.server")

_TOOLS_LIST = types.ListToolsResult(tools=[types.Tool.model_validate(TOOL_DEFINITIONS[n]) for n in TOOL_NAMES])
_OUTPUT_VALIDATORS = {n: Draft202012Validator(TOOL_DEFINITIONS[n]["outputSchema"]) for n in TOOL_NAMES}


def _result(structured: dict[str, Any], *, is_error: bool, text: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structured_content=structured,
        is_error=is_error,
    )


def _failure_result(failure: ToolFailure) -> types.CallToolResult:
    return _result({"error": failure.body()}, is_error=True, text=failure.say)


def output_violations(tool: ToolName, structured: dict[str, Any]) -> list[str]:
    """Every way `structured` breaks the tool's outputSchema, as short path: message strings."""
    return [
        f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.validator}"
        for error in _OUTPUT_VALIDATORS[tool].iter_errors(structured)
    ]


def build_server(tools: AegisTools) -> Server[Any]:
    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return _TOOLS_LIST

    async def call_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
        with tool_span(params.name):
            return await run_tool(ctx, params)

    async def run_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
        started = time.perf_counter()
        name = params.name
        arguments = params.arguments
        if not tools.has_tool(name):
            _log_call(ctx, name, None, "unknown_tool", started, arguments)
            raise MCPError(code=types.INVALID_PARAMS, message="Unknown tool")
        tool_name: ToolName = TOOL_NAMES[TOOL_NAMES.index(name)]  # narrows str to the ToolName literal

        try:
            async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                outcome = await tools.call(tool_name, arguments)
        except TimeoutError:
            logger.error("tool %s exceeded %ss (request_id=%s)", name, TOOL_TIMEOUT_SECONDS, ctx.request_id)
            _log_call(ctx, name, _mode_of(name, arguments), "timeout", started, arguments)
            return _failure_result(ToolFailure("unavailable", TIMEOUT_SAY))
        except ToolFailure as failure:
            _log_call(ctx, name, _mode_of(name, arguments), failure.code, started, arguments)
            return _failure_result(failure)
        except Exception:
            logger.exception("tool %s failed unexpectedly (request_id=%s)", name, ctx.request_id)
            _log_call(ctx, name, _mode_of(name, arguments), "unavailable", started, arguments)
            return _failure_result(ToolFailure("unavailable", UNAVAILABLE_SAY))

        violations = output_violations(tool_name, outcome.structured)
        if violations:
            # Last line of the output contract: never put a result on the wire that breaks its outputSchema.
            logger.error("tool %s produced output outside its schema: %s", name, "; ".join(violations[:5]))
            _log_call(ctx, name, outcome.mode, "unavailable", started, arguments)
            return _failure_result(ToolFailure("unavailable", UNAVAILABLE_SAY))

        _log_call(ctx, name, outcome.mode, outcome.status, started, arguments)
        text = json.dumps(outcome.structured, ensure_ascii=False, separators=(",", ":"))
        return _result(outcome.structured, is_error=False, text=text)

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        title=SERVER_TITLE,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def _mode_of(name: str, arguments: dict[str, Any] | None) -> str | None:
    if name not in ("block_number", "report_scam"):
        return None
    return "resolve" if arguments and ("approval_token" in arguments or "decision" in arguments) else "stage"


def _log_call(
    ctx: ServerRequestContext[Any],
    tool: str,
    mode: str | None,
    status: str,
    started: float,
    arguments: dict[str, Any] | None,
) -> None:
    """One structured line per tools/call, plus the span outcome and EMF latency metric.

    Tokens, cursors and transcripts are never logged.
    """
    latency_ms = (time.perf_counter() - started) * 1000
    finish_tool(trace.get_current_span(), tool, status, mode, latency_ms)
    voicemail_id = arguments.get("voicemail_id") if isinstance(arguments, dict) else None
    record = {
        "event": "tools/call",
        "request_id": ctx.request_id if isinstance(ctx.request_id, int) else str(ctx.request_id)[:64],
        "tool": tool if _LOGGABLE_NAME_RE.fullmatch(tool) else "<invalid>",
        "mode": mode,
        "status": status,
        "voicemail_id": voicemail_id if isinstance(voicemail_id, str) and _VOICEMAIL_ID_RE.fullmatch(voicemail_id) else None,
        "latency_ms": round(latency_ms, 2),
    }
    logger.info(json.dumps(record, default=str))


def transport_security(extra_hosts: Sequence[str]) -> TransportSecuritySettings:
    """Allow local hosts plus configured tunnel hosts; allow no browser origins (docs/architecture.md §3.3)."""
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*LOCAL_HOSTS, *extra_hosts],
        allowed_origins=[],
    )


def screen_jsonrpc(body: bytes) -> tuple[int, dict[str, Any]] | None:
    """Answer two malformed request shapes the SDK mishandles; return None to let the SDK handle the rest.

    - A request `id` that is not a string or integer (null, float, bool, array, object): the SDK treats
      the message as a notification and replies 202 with no body, so the client waits forever.
    - `tools/call` with non-object `arguments`: the SDK replies with a JSON-RPC error, but spec §7.1
      requires a tool execution error (`isError: true`) the model can read and correct.

    Never raises: anything it cannot parse (bad JSON, deep nesting, huge numbers) goes to the SDK,
    which answers with a proper -32700 parse error.
    """
    try:
        message = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(message, dict) or "method" not in message or "id" not in message:
        return None

    request_id = message["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        error = {"code": types.INVALID_REQUEST, "message": "Invalid Request: id must be a string or an integer"}
        return 400, {"jsonrpc": "2.0", "id": None, "error": error}

    params = message.get("params")
    if message.get("method") != "tools/call" or not isinstance(params, dict) or params.get("name") not in TOOL_NAMES:
        return None
    arguments = params.get("arguments")
    if arguments is None or isinstance(arguments, dict):
        return None

    failure = ToolFailure.from_input_error(InputError("arguments", "must be an object"))
    result = _failure_result(failure).model_dump(by_alias=True, mode="json", exclude_none=True, exclude={"result_type"})
    logged_id = request_id if isinstance(request_id, int) else request_id[:64]
    record = {"event": "tools/call", "request_id": logged_id, "tool": params["name"], "status": "invalid_input"}
    logger.info(json.dumps(record))
    return 200, {"jsonrpc": "2.0", "id": request_id, "result": result}


class HalfCloseTolerantH11Protocol(H11Protocol):
    """uvicorn's h11 protocol, but a client that half-closes after sending its request still gets the response.

    Stock uvicorn closes the socket on EOF, so a fully received request still runs - possibly an
    approval that executes - while its response is thrown away. Idle connections still close on EOF.
    """

    def eof_received(self) -> bool:
        cycle = self.cycle
        return cycle is not None and not cycle.response_complete


class RequestGuard:
    """Outermost ASGI guard for the MCP endpoint.

    - Stateless mode: `GET /mcp` is 405 (there is no server-initiated stream, §3.2).
    - Every POST body is read in full before the SDK sees it, under a deadline and the
      64 KiB cap. A stalled body gets 408 instead of pinning a connection forever, and a
      client that disconnects mid-body is dropped quietly instead of reaching the SDK.
    - The buffered body is replayed to the SDK; later `receive()` calls go to the real
      connection so disconnects during the response are still observed.
    """

    def __init__(self, app: ASGIApp, *, stateless: bool, body_timeout: float = BODY_READ_TIMEOUT_SECONDS) -> None:
        self.app = app
        self.stateless = stateless
        self.body_timeout = body_timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].rstrip("/") != MCP_PATH:
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        if method == "GET" and self.stateless:
            await Response("Method Not Allowed", status_code=405, headers={"Allow": "POST"})(scope, receive, send)
            return
        if method != "POST":
            await self.app(scope, receive, send)
            return

        try:
            async with asyncio.timeout(self.body_timeout):
                body = await self._read_body(receive)
        except TimeoutError:
            stream_outcome("mcp_post", "body_timeout")
            logger.warning("dropped POST %s: request body not received within %ss", MCP_PATH, self.body_timeout)
            await Response("Request Timeout", status_code=408, headers={"Connection": "close"})(scope, receive, send)
            return
        except _BodyTooLarge:
            stream_outcome("mcp_post", "too_large")
            await Response("Request body too large", status_code=413)(scope, receive, send)
            return
        except _ClientGone:
            stream_outcome("mcp_post", "client_gone")
            logger.info("client disconnected before sending the full request body")
            return

        screened = screen_jsonrpc(body)
        if screened is not None:
            stream_outcome("mcp_post", "screened")
            status, payload = screened
            await JSONResponse(payload, status_code=status)(scope, receive, send)
            return

        stream_outcome("mcp_post", "accepted")
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _read_body(receive: Receive) -> bytes:
        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise _ClientGone
            chunks += message.get("body", b"")
            if len(chunks) > MAX_REQUEST_BODY_BYTES:
                raise _BodyTooLarge
            if not message.get("more_body", False):
                return bytes(chunks)


class _ClientGone(Exception):
    pass


class _BodyTooLarge(Exception):
    pass


def build_app(
    *,
    stateless: bool = True,
    host: str = "127.0.0.1",
    allowed_hosts: Sequence[str] = (),
    store: VoicemailStore | None = None,
    ledger: ActionLedger | None = None,
    webhook_secret: str | None = None,
    display_token: str | None = None,
    pipeline: EcosystemPipeline | None = None,
) -> Starlette:
    """Build the ASGI app. The ecosystem endpoints are mounted only when both secrets are given."""
    extra_routes = (
        ecosystem_routes(pipeline or EcosystemPipeline(), webhook_secret, display_token)
        if webhook_secret and display_token
        else None
    )
    tools = AegisTools(
        store=store if store is not None else VoicemailStore.from_directory(DEFAULT_FIXTURE_DIR),
        ledger=ledger if ledger is not None else ActionLedger(observer=observe_transition),
    )
    app = build_server(tools).streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=stateless,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
        transport_security=transport_security(allowed_hosts),
        host=host,
        custom_starlette_routes=extra_routes,
    )
    app.add_middleware(RequestGuard, stateless=stateless)
    return app


class RedactingFilter(logging.Filter):
    """Scrub phone numbers, emails, SSNs, card numbers and secrets from every human-readable log line.

    Runs on the listener thread, so redaction never slows a response.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.msg, record.args = redact_pii(message), None
        if record.exc_text:
            record.exc_text = redact_pii(record.exc_text)
        return True


def _only_metrics(record: logging.LogRecord) -> bool:
    return record.name == METRICS_LOGGER


def _not_metrics(record: logging.LogRecord) -> bool:
    return record.name != METRICS_LOGGER


def configure_logging(level: int = logging.INFO) -> logging.handlers.QueueListener:
    """Send every log record through a queue to a background thread, so writing logs never blocks a response.

    The request path only does a non-blocking `queue.put`; formatting and stderr I/O happen on the
    listener's thread. Call `.stop()` on the returned listener at shutdown to flush what's left.
    """
    records: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    stderr.addFilter(_not_metrics)
    stderr.addFilter(RedactingFilter())
    metrics = logging.StreamHandler(sys.stdout)  # EMF: one bare JSON document per line
    metrics.setFormatter(logging.Formatter("%(message)s"))
    metrics.addFilter(_only_metrics)
    listener = logging.handlers.QueueListener(records, stderr, metrics, respect_handler_level=True)
    root = logging.getLogger()
    root.handlers[:] = [logging.handlers.QueueHandler(records)]
    root.setLevel(level)
    listener.start()
    return listener


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aegis-server", description="Run the Aegis MCP server (Streamable HTTP).")
    parser.add_argument("--host", default=os.environ.get("AEGIS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_env_int("AEGIS_PORT", 8000))
    parser.add_argument(
        "--stateless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Serve without MCP sessions (default: on).",
    )
    parser.add_argument(
        "--allow-remote-bind",
        action="store_true",
        default=os.environ.get("AEGIS_ALLOW_REMOTE_BIND") == "1",
        help="Permit a non-loopback --host. The server has no authentication, so only do this behind a trusted proxy.",
    )
    return parser.parse_args(argv)


def is_loopback(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    if not is_loopback(args.host) and not args.allow_remote_bind:
        raise SystemExit(
            f"Refusing to bind to {args.host!r}: Aegis has no authentication, and Host-header checks do not stop "
            "non-browser clients on your network. Keep the default 127.0.0.1 and expose it through a tunnel, or pass "
            "--allow-remote-bind (AEGIS_ALLOW_REMOTE_BIND=1) if a trusted proxy fronts it."
        )
    if not 1 <= args.port <= 65535:
        raise SystemExit(f"--port must be between 1 and 65535, got {args.port}")

    ttl = _env_int("AEGIS_APPROVAL_TTL_SECONDS", DEFAULT_APPROVAL_TTL_SECONDS)
    low, high = APPROVAL_TTL_RANGE
    if not low <= ttl <= high:
        raise SystemExit(f"AEGIS_APPROVAL_TTL_SECONDS must be between {low} and {high}, got {ttl}")
    tunnel_hosts = [h.strip() for h in os.environ.get("AEGIS_ALLOWED_HOSTS", "").split(",") if h.strip()]

    try:
        store = VoicemailStore.from_directory(DEFAULT_FIXTURE_DIR)
    except FixtureError as exc:
        raise SystemExit(f"Cannot load voicemail fixtures: {exc}") from exc

    webhook_secret = os.environ.get("AEGIS_WEBHOOK_SECRET")
    display_token = os.environ.get("AEGIS_DISPLAY_TOKEN")
    if bool(webhook_secret) != bool(display_token):
        raise SystemExit("Set both AEGIS_WEBHOOK_SECRET and AEGIS_DISPLAY_TOKEN to enable the ecosystem endpoints.")

    try:
        app = build_app(
            stateless=args.stateless,
            host=args.host,
            allowed_hosts=tunnel_hosts,
            store=store,
            ledger=ActionLedger(ttl_seconds=ttl, observer=observe_transition),
            webhook_secret=webhook_secret,
            display_token=display_token,
        )
    except ValueError as exc:
        raise SystemExit(f"Cannot enable the ecosystem endpoints: {exc}") from exc
    listener = configure_logging()
    logger.info(
        "ecosystem endpoints %s",
        "enabled: /webhooks/ring, /webhooks/bee, /events/firetv" if webhook_secret else "disabled",
    )
    logger.info(
        "Aegis MCP server on http://%s:%s%s (stateless=%s, voicemails=%d, approval_ttl=%ss)",
        args.host,
        args.port,
        MCP_PATH,
        args.stateless,
        len(store),
        ttl,
    )
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            workers=1,
            log_level="info",
            log_config=None,  # uvicorn's own config prints access logs to stdout, which must carry only EMF
            limit_concurrency=MAX_CONCURRENCY,
            timeout_keep_alive=KEEP_ALIVE_SECONDS,
            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
            server_header=False,
            http=HalfCloseTolerantH11Protocol,
        )
    finally:
        listener.stop()
