# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Aegis is a voice-native voicemail scam-defense companion for seniors on Alexa+: a self-hosted MCP server backed by a deterministic analysis engine. The `[tool.aegis]` tables in `pyproject.toml` are the machine-readable product agreements — read them before changing behavior.

## Commands

- Install: `pip install -e ".[dev]"`
- Engine tests: `pytest tests/ -q`
- MCP smoke checks: `python server/smoke_test.py` (run from repo root)
- Run server: `aegis-server` or `python -m server` → `http://127.0.0.1:8000/mcp` (`--host`/`AEGIS_HOST`, `--port`/`AEGIS_PORT`, `--stateless`/`--no-stateless`, stateless by default)
  - Loopback only by default: a non-loopback `--host` is refused unless `--allow-remote-bind` (`AEGIS_ALLOW_REMOTE_BIND=1`). There is no auth, so expose it through a tunnel instead.
  - Tunnel hostnames must be listed in `AEGIS_ALLOWED_HOSTS` (comma-separated) or the Host check returns 421. `AEGIS_APPROVAL_TTL_SECONDS` sets approval expiry (60–3600, default 600).
  - `server.server.RequestGuard` buffers each POST body (5 s deadline, 64 KiB cap) before the SDK sees it; each tool call has a 3 s limit (`TOOL_TIMEOUT_SECONDS`); uvicorn caps concurrency at 128. Don't remove these — they're what stops stalled or flooding clients.
  - Tools run only through `AegisTools.call`: typed parse → published `inputSchema` check → private handler. Staged actions move only along `aegis.engine.TRANSITIONS` via `transition()`; never assign `state` directly.
  - Failure paths return an honest spoken retry message. Never fall back to cached or default *verdict* data: a stale answer could call a scam safe. The analysis cache is safe only because the engine is deterministic per transcript.

## Engine rules (`aegis/engine.py`)

- Stdlib only, no network, no LLM. Verdicts come only from the fixed heuristics, never from a model. Tests enforce that the engine source imports no network modules — don't add any, even indirectly.
- `demo/server.py` is also stdlib-only. Only `server/` may use third-party packages (`mcp`, `uvicorn`, `starlette`, `jsonschema`, all pinned).
- Verdicts: `risk_score >= 60` → SCAM, `30–59` → SUSPICIOUS, else LEGITIMATE. Heuristics, weights and thresholds may be tuned as long as tests pass; keep `heuristics_count`/thresholds in `pyproject.toml` in sync.
- Every heuristic must emit a plain, senior-friendly sentence meant to be read aloud by Alexa — not technical jargon.

## Security & observability

- `aegis/security` and `aegis/telemetry` follow the engine rules: stdlib only, no network. OpenTelemetry lives in `server/observability.py`.
- **stdout carries only EMF metric lines.** Human logs go to stderr through the queue and `RedactingFilter`. Never `print()` in the server, and keep `log_config=None` on `uvicorn.run`.
- Metric dimensions must come from `ALLOWED_DIMENSIONS`: never ids, phone numbers, tokens or transcripts. Span attributes never carry tool arguments.
- Don't strip PII from tool arguments (callers are named by number). Instruction screening is a heuristic; the structural defences (deterministic verdicts, human yes) are what matter.

## Ecosystem pipeline (`server/ecosystem/`, `docs/ecosystem.md`)

- Simulator-grade, with Aegis-defined schemas. Don't describe it as an official Ring/Bee/Fire TV integration.
- Threat levels come from the deterministic engine only; a model may phrase text, never judge.
- Wearable events must carry `consent.all_parties_consented: true`. Transcripts are never stored, logged or put on a card.
- Webhooks are HMAC-signed and display streams need the bearer token; both are mounted only when `AEGIS_WEBHOOK_SECRET` and `AEGIS_DISPLAY_TOKEN` are set. Card buttons are display-only (`ask_alexa`, `dismiss`).

## Permission layer

- Nothing executes without an explicit yes: propose → approve → execute. Actions are staged until approved.
- Approval tokens are single-use; a wrong or reused token raises `PermissionError`, surfaced as an MCP tool error (`isError: true`). Reject drops the action.
- Each approve tool must verify the action *kind* it is approving (e.g. `block_number` cannot approve a staged report).

## Intentional limitations (don't "fix" without being asked)

- Actions are simulated: receipts carry `simulated: true`; nothing is really blocked or reported.
- Fixtures in `fixtures/voicemails/` are scripted text transcripts (5 scam + 3 legitimate), not audio.
- English-only heuristics; caller metadata is treated as trusted input.
- Pending approvals live in in-memory registries and vanish on restart.

## Alexa+ MCP target rules

Source: Alexa+ MCP Toolkit docs on developer.amazon.com (`docs/alexaplus/add-ons/`): quickstart, client lifecycle, tools/schema design guide, functional requirements. Fetched 2026-10-03 through the amazon-devices-buildertools MCP server.

### Transport
- Only Streamable HTTP is supported. The legacy HTTP+SSE transport is deprecated and will not connect. Alexa+ targets MCP spec 2025-11-25, but its sample `initialize` sends `protocolVersion: "2025-03-26"`, so the server must negotiate both.
- Alexa+ talks JSON-RPC 2.0 over Streamable HTTP to one remote HTTPS URL. For local dev, expose `127.0.0.1:8000/mcp` through a tunnel (e.g. cloudflared).
- Session continuity comes from the customer's Alexa+ conversation history, not from an MCP session ID. That's why the server runs stateless by default: don't rely on per-session server state.
- Round-trip latency must stay under 500 ms. Alexa+ refreshes tools only on `alexa-ai deploy`, so redeploy after any change to `tools/list`.

### Wire payloads (from the Alexa+ client lifecycle doc)
```json
// initialize request (client → server)
{"jsonrpc":"2.0","method":"initialize","id":"4e3bdaee-0",
 "params":{"protocolVersion":"2025-03-26","capabilities":{"roots":{"listChanged":true}},
           "clientInfo":{"name":"Alexa+ MCP Client","version":"1.0.0"}}}
// initialize response
{"jsonrpc":"2.0","id":"4e3bdaee-0",
 "result":{"protocolVersion":"2025-03-26","serverInfo":{"name":"...","version":"1.0.0"},
           "capabilities":{"tools":{},"resources":{}}}}
// tools/call request
{"jsonrpc":"2.0","method":"tools/call","id":"4a5bda6e-1",
 "params":{"name":"<tool>","arguments":{...}}}
// tools/call response: structuredContent plus a text mirror; _meta.ui is optional (MCP Apps visuals only)
{"jsonrpc":"2.0","id":"4a5bda6e-1",
 "result":{"structuredContent":{...},
           "content":[{"type":"text","text":"<JSON string of structuredContent>"}],
           "_meta":{"ui":{"resourceUri":"ui://...","invoking":"Working...","invoked":"Done"}}}}
```
- If there's no `_meta.ui.resourceUri`, Alexa+ uses the data-only flow and renders from `structuredContent` itself. Aegis is voice-first, so leave `_meta.ui` out unless we build an MCP App.

### Tool and schema rules (certification §13)
- Every tool in `tools/list` must work when called. No placeholder tools.
- Every tool needs a valid JSON Schema `inputSchema` that declares all required params. Validate input against it before acting, and handle bad or unexpected params without crashing.
- Declare only what you honor: never put a parameter in the schema that the server ignores.
- Results must match the declared output schema. Failures go through `isError: true` or JSON-RPC errors, never through malformed payloads, and a tool must never return empty.
- Return stable IDs (like our approval tokens or voicemail IDs) that later calls accept, so propose → approve → execute chains work.
- One tool per customer intent, with no overlapping tools. Descriptions say when to call the tool, why, and what it returns. Put synonyms in parameter descriptions and enums.

### Voice and customer-facing rules
- Customer-facing text must never contain API codes, tool names, JSON or internal IDs. Every error needs one clear next step.
- On screenless devices (Echo Dot): offer at most 5 options, never mention anything on a screen, read back key details and get an explicit "yes" before any commitment. This fits our approval layer. Keep spoken responses under 30 seconds.
- High-consequence actions such as blocking or reporting need explicit confirmation first. Duplicate actions must be detected, not run twice.
- Data hygiene: `structuredContent` gets no third-party tracking parameters and no upstream deep links.

### Auth (only if we add account linking)
- OAuth 2.1 authorization code flow with PKCE `S256`, a `resource` parameter, and RFC 9728 PRM at the well-known URI plus `/.well-known/oauth-authorization-server`. Unauthenticated requests get `401` with **no** `WWW-Authenticate` header. Bearer tokens go in the header only. DCR, CIMD, OIDC and step-up auth aren't supported.
