# Aegis Product Teardown: Building a Voice-First MCP Add-on for Alexa+

**For:** Amazon Alexa+ Add-ons and Amazon Bedrock product teams
**From:** the Aegis team, Amazon Developer Hackathon "Build, Ship, Shape", Alexa+ track
**Date:** 2026-10-04
**Detailed companion brief:** [`docs/developer_feedback.md`](docs/developer_feedback.md), with doc quotes and per-item evidence

---

## How to read this document

Every finding is tagged with **where it came from**, because they don't carry the same weight:

| Tag | Meaning |
|---|---|
| **[Docs]** | Read in Amazon's published Alexa+ documentation (fetched through the Amazon Devices Builder Tools MCP server). |
| **[Reproduced]** | Reproduced against our own running server with raw HTTP probes, and fixed with a regression test. |
| **[SDK-verified]** | Read from installed package metadata or source (`mcp==2.2.0`, `strands-agents==1.57.2`). |
| **[Anticipated]** | A risk we designed for but could not observe, because it depends on Alexa+ or Bedrock behavior we haven't exercised. |

**What we have not done:** connect Aegis to a live Alexa+ client, or call Bedrock. Our transport findings come from our own probes against the official MCP Python SDK stack the docs lead you to. They're the failure modes an Alexa+ add-on is exposed to, not behavior we saw from the Alexa+ client itself.

---

## Executive summary

Aegis checks a senior's voicemails for scams by voice: *"Alexa, ask Aegis to check the voicemail I just got from the IRS."* The stack:
- a self-hosted MCP server (Python, official MCP SDK 2.2.0), over Streamable HTTP, stateless;
- five tools, a deterministic 12-rule engine, and a propose → approve → execute permission layer;
- a designed (not deployed) Bedrock agent through the Strands SDK.

The build ended with 308 unit tests and 23 end-to-end HTTP smoke checks.

**Three conclusions:**
1. **Alexa+ choosing plain MCP is the right bet.** Because Alexa+ speaks standard MCP, the same server worked unchanged with a second client: Strands' MCP client, tested against our live server.
2. **The Alexa+ client contract is the biggest gap.** The docs state support for MCP 2025-11-25, but the documented handshake requests `2025-03-26` while using fields from 2025-06-18. **[Docs]** Nothing documents headers, retries, timeouts, id types, batching or egress ranges. We spent most of our hardening time defending against client behavior we couldn't look up.
3. **The default Python stack needs production hardening that no Amazon material mentions.** With the official SDK and uvicorn, we reproduced seven transport failure modes (plus one bug of our own), from silent hangs to executed-but-unconfirmed approvals. Each is cheap to fix once known. **[Reproduced]**

---

## 1. Alexa+ MCP and Streamable HTTP: protocol friction

### 1.1 Connection resilience **[Reproduced]**

All of these come from the official MCP Python SDK (2.2.0) behind uvicorn (0.54.0). An Alexa+ add-on exposes exactly this stack to the internet through a tunnel.

| Failure | What we observed | Severity for a voice add-on | Our fix |
|---|---|---|---|
| **Half-closed connection** | The client sent a full request, then half-closed its write side. The tool **ran**, but the client got **0 bytes**: uvicorn's h11 protocol closes the socket on EOF. | **Critical.** An `approve` can execute while the confirmation is lost, so the senior hears an error after the block happened. | Subclassed the protocol to keep the socket writable until the response is sent. |
| **Stalled request body** | A body that stops mid-stream held the connection open indefinitely (still open after 40 s). 300 such connections were accepted with no cap. | High: slowloris exposure on a public tunnel. | Buffer each body under a 5 s deadline, then 408. Cap concurrency at 128. |
| **Mid-body disconnects** | Each disconnect made the SDK log a full traceback at ERROR level. | Medium: log flooding and storage cost. | Drop the client before the SDK sees the request. |
| **Non-ASCII pagination cursor** | A `cursor` *tool argument* containing `²` or `é` crashed our handler (`int("²")`, `hmac.compare_digest` on non-ASCII), so the person heard "Aegis is having trouble" instead of a re-prompt. | Medium. Our bug, but typical of what voice clients can pass through. | Strict ASCII cursor format. Fuzz tests. |
| **Request id of null, float, bool, array or object** | The SDK treated the request as a notification and answered **202 with no body**. | High: the client waits for an answer that never comes. | Pre-screen and answer `-32600`. |
| **`GET /mcp` in stateless mode** | The SDK opened a long-lived stream anyway (200, never closes). | Medium: idle connections pile up. | Return 405. |

### 1.2 JSON-RPC and spec ambiguity: null or non-object `arguments` **[Reproduced] [SDK-verified]**

- **Missing or null `arguments`:** the MCP spec allows `arguments` to be omitted, and the SDK passes `None`. Every handler has to treat "no arguments" and `{}` the same way. Our `check_voicemail` uses that path for "check my last voicemail". Nothing in the Alexa+ docs says whether Alexa+ omits the field, sends `{}`, or sends `null`. **[Anticipated]**
- **Non-object `arguments`** (a list, string or number): the SDK rejects them at the protocol level with a JSON-RPC `-32602` error. The MCP 2025-11-25 guidance says *input* errors should come back as tool results with `isError: true`, so the model can read the problem and re-ask the person. We intercept these and return a spoken `isError` result instead.
- **What we can't tell from the docs:** whether Alexa+ treats a JSON-RPC error and an `isError` result differently when deciding what to say. A voice client that reads out "Invalid request parameters" would violate Amazon's own certification rule against technical jargon (Functional Requirements §2).

### 1.3 Speech-to-text normalization gaps **[Reproduced] [Anticipated]**

Our first matcher resolved "IRS" but returned *not found* for **"I.R.S."**, **"i r s"**, **"the I. R. S."**, **"I-R-S"** and **"S.S.A."**. That would have broken our one-sentence demo. We reproduced it with our own inputs; we haven't seen Alexa+'s actual transcripts. Spelled-out acronyms are a common speech-to-text output style, and the docs don't say what form entity names take when Alexa+ fills tool arguments.

| What we needed | What we built |
|---|---|
| Spelled-out acronyms | Join runs of single letters: "the i r s" becomes "the irs". |
| Look-alike Unicode / zero-width characters | NFKC normalization, and invisible and bidi characters stripped. |
| Synonyms ("Internal Revenue Service", "taxes") | A fixed synonym table per caller group. |
| Phone fragments ("555-0147") | Digit-subsequence matching. |

Every MCP add-on that accepts names will rebuild this table.

---

## 2. Bedrock and Strands SDK developer experience

### 2.1 Dependency isolation **[SDK-verified]**

`strands-agents==1.57.2` requires `mcp<2.2,>=1.23.0`, and our server pins `mcp==2.2.0`. With both pinned, pip's resolver silently falls back to `strands-agents==0.0.1`, a placeholder release, instead of failing. The agent has to run as a separate process with its own environment. That's a sound architecture, but it was forced on us, and the silent downgrade is easy to miss.

### 2.2 Timeouts for live voice turns **[SDK-verified] [Anticipated]**

Alexa+ requires under 500 ms per round trip; a spoken turn tolerates a few seconds at most. In Strands 1.57.2:
- `Agent` has **no per-turn deadline parameter**. We wrapped `agent.invoke_async()` in `asyncio.timeout(5)` ourselves.
- **Timeouts live in four places:** `botocore.config.Config(connect_timeout, read_timeout, retries)` for Bedrock, `MCPClient(startup_timeout=...)`, `call_tool_async(read_timeout_seconds=...)`, and the turn wrapper. Nothing explains how they combine or what the user hears when one fires.
- **Retry and failover can blow the budget.** Strands has a `ModelRetryStrategy` for throttling and model failover (`ModelRouter` / `FallbackStrategy`). Both are useful, but either can exceed a voice deadline. We found no guidance on bounding them for voice.

We measured none of this against live Bedrock; the latency and token figures in our docs come from stubs.

### 2.3 State persistence overhead **[Anticipated]**

Strands offers `S3SessionManager` for conversation state. Our MCP-side approval state is in memory, and the DynamoDB design for running more than one instance is documented but not built. **We haven't measured persistence latency.** What we can say: no Amazon material offers a reference pattern for single-use approval tokens behind a load balancer. That's the hardest state problem in a permission-gated voice add-on. We designed one with DynamoDB conditional transactions.

### 2.4 Pre-execution validation and safe fallbacks **[SDK-verified]**

What exists, and helped:
- `BeforeToolCallEvent.cancel_tool` let us build a human-approval gate in about 20 lines, and we tested it against the real hook registry.
- Strands validates tool *names* (`strands.tools._validator`, a private module).

What we didn't find:
1. **Validating tool *arguments* against the MCP `inputSchema` before the call.** We enforce it server-side with typed parsing plus a second schema check.
2. **A safe, user-facing fallback.** Failover switches models; it doesn't produce a spoken "please try again" when every path fails. For a safety product, "fall back to cached or default data" is the wrong default: a stale verdict can call a scam safe. We return an honest retry message instead.
3. **A declarative way to mark a tool "requires human confirmation".** MCP tool annotations (`destructiveHint`) exist, but nothing in Strands or Alexa+ enforces them.

---

## 3. High-impact recommendations

### 3.1 Speech-aware argument normalization in the Alexa+ client

Normalize entity-like spans **before** they become tool arguments:
- join spelled-out acronyms;
- apply NFKC and strip invisible characters;
- optionally pass both the raw and the normalized form in `_meta`, so servers can choose.

A small declarative hint on a parameter, for example `x-alexa-entity: organization` in the tool's `inputSchema`, would let developers opt in per parameter. This removes a class of bug every name-matching add-on will otherwise ship.

> Terminology note: Amazon's "Add-on Agent Skill" is an onboarding aid for coding agents, not a runtime. This recommendation targets the Alexa+ client's request pipeline.

### 3.2 An "Alexa+ MCP Client Contract" page

Document, on one page:
- the negotiated protocol version, and which response fields Alexa+ reads (`structuredContent`, `content`, or both);
- headers sent (`MCP-Protocol-Version`, `Mcp-Session-Id`, `Origin`, `Host`);
- request id types, and whether batching is used;
- per-call timeout and retry policy, including whether a timed-out `tools/call` is retried (single-use tokens make this matter);
- egress IP ranges, for allowlisting;
- what Alexa+ says to the person on a JSON-RPC error versus an `isError` result.

### 3.3 Stream-buffering and hardening guidelines for MCP servers

A short checklist for the Add-on Agent Skill and sample servers:
1. Buffer the full request body under a deadline (we use 5 s) before JSON-RPC parsing.
2. Cap body size (we use 64 KiB) and concurrent connections.
3. Answer invalid request ids with `-32600`; never stay silent.
4. Keep the socket writable after a client half-closes.
5. Return 405 for `GET`/`DELETE` in stateless mode.
6. Put a deadline on every tool call (we use 3 s), with a spoken fallback.
7. Validate every result against its own `outputSchema` before sending.

### 3.4 Official tunnel blueprints (Cloudflare Tunnel, ngrok)

The QuickStart suggests "a tunneling service (such as cloudflared)" but gives no configuration. Our Host-header check (DNS-rebinding protection) answered **421** to tunnelled requests until we either:
- passed `--http-host-header 127.0.0.1:8000` to cloudflared, or
- allowlisted the tunnel hostname (`AEGIS_ALLOWED_HOSTS`).

We tested the server side of this; we haven't run `cloudflared` itself. A blueprint per tunnel should cover:
- the Host-header choice;
- keeping the server bound to loopback;
- a stable named-tunnel URL, since quick-tunnel URLs change and Alexa+ only re-reads tools on `alexa-ai deploy`;
- a matching ngrok configuration.

### 3.5 Bedrock and Strands for voice

- **Add a `turn_timeout` to `Agent`** with a configurable fallback response.
- **Publish a voice profile** that bounds retry and failover within a total deadline.
- **Document the `mcp` version range** each Strands release supports, and fail loudly instead of resolving to `0.0.1`.
- **Add a first-class "requires confirmation" hook** keyed off MCP's `destructiveHint`.

---

## 4. What worked well

- **Certification criteria precise enough to test.** Functional Requirements §2 (errors), §9 (voice-only) and §13 (MCP tool validation) turned directly into assertions in our test suite.
- **"Declare only what you honor"** from the design guide shaped our schemas: `additionalProperties: false` everywhere, and no parameters we ignore.
- **Plain MCP everywhere.** No proprietary envelope, so one server serves Alexa+ and a Strands agent alike.
- **Builder Tools MCP inside the coding agent** kept the build grounded in the actual docs, despite the friction in the companion brief (§8).

---

## Appendix: evidence in this repository

| Claim | Where to verify |
|---|---|
| Transport failures and fixes | `server/server.py` (`RequestGuard`, `screen_jsonrpc`, `HalfCloseTolerantH11Protocol`); `tests/test_hardening.py`, `tests/test_stress.py` |
| Live HTTP behavior | `python server/smoke_test.py` (23 checks) |
| Speech-to-text normalization | `aegis/voicemails.py` (`collapse_spelled_acronyms`), `aegis/security/sanitize.py`; `tests/test_stress.py`, `tests/test_telemetry_security.py` |
| Strands interop, approval gate, timeout wrapper | `docs/aws_bedrock_integration.md` (§3.2–3.3, "What was verified") |
| Full suite | `pytest tests/ -q` (308 tests) |
