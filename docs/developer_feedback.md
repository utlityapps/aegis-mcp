# Amazon Developer Product Feedback Brief: Aegis (Alexa+ MCP add-on)

**Product area:** Alexa+ Add-ons, MCP Toolkit (`developer.amazon.com/en-US/docs/alexaplus/add-ons/`) and the Amazon Devices Builder Tools MCP server (`@amazon-devices/amazon-devices-buildertools-mcp`)
**Project:** Aegis, a voice-native voicemail scam-defense companion for older adults. A self-hosted Python MCP server (Streamable HTTP, stateless) behind Alexa+.
**Track:** Amazon Developer Hackathon "Build, Ship, Shape", Alexa+ track
**Date:** 2026-10-03
**Build stack:** Python 3.12, `mcp==2.2.0` (official MCP Python SDK), `uvicorn==0.54.0`, `starlette==1.7.0`

Everything below was observed directly during this build: pages fetched through the Builder Tools MCP server, behavior reproduced against our running server, or package metadata read from installed packages. Where we didn't test a path ourselves (for example the `alexa-ai` CLI or a physical device), we say so instead of guessing.

---

## Summary

| # | Area | Severity | One-line finding |
|---|---|---|---|
| 1 | Protocol versions | **High** | The docs say Alexa+ supports MCP 2025-11-25, but the documented client handshake requests `2025-03-26`, while also using fields that only exist from 2025-06-18. |
| 2 | Auth spec mismatch | Medium | Alexa+ forbids `WWW-Authenticate` on `401`; the official MCP SDK's auth middleware sends it. |
| 3 | Missing client contract | **High** | Undocumented Alexa+ client behavior: headers, retries, timeouts, id types, batching, egress IPs. We had to defend against every possibility. |
| 4 | Missing request context | **High** | Tools can't tell a screenless Echo Dot from an Echo Show, yet certification requires adapting voice-only output. |
| 5 | Missing auth tier | Medium | It's either full OAuth 2.1 + PKCE or no auth; there's no option between them for non-personal tools. |
| 6 | Interim-progress requirement | Medium | Certification requires a "still working" message, but the documented transport (JSON responses) has no channel for one. |
| 7 | No local conformance harness | Medium | No runnable Alexa+ client emulator or conformance suite for CI. We wrote 22 raw-HTTP smoke checks by guessing the client's behavior. |
| 8 | Builder Tools MCP server DX | Medium | A mandatory `set_project_context` call, a 111k-character listing, every page wrapped in its full navigation sidebar, and a device-OS filter that doesn't fit Alexa+. |
| 9 | Doc defects | Low | A wrong link target, an empty "Security and data policies" section, and a capabilities page that only links elsewhere. |
| 10 | Ecosystem: MCP Python SDK and Strands | Info | Upstream issues we had to work around. Not Amazon's code, but they shape the Alexa+ developer experience. |

---

## 1. Protocol version mismatch (High)

**Observed:**
- *Alexa+ MCP Toolkit Overview:* "Alexa+ for Builders supports the 2025-11-25 version of the MCP specification."
- *Alexa+ MCP QuickStart:* "Your MCP server must support Streamable HTTP. The MCP specification (2025-11-25)…"
- *Alexa+ MCP Client and App Lifecycle*, the documented `initialize` request: `"protocolVersion": "2025-03-26"`, `"clientInfo": {"name": "Alexa+ MCP Client"}`.
- The same page's `tools/call` response sample uses `structuredContent` and `_meta.ui.resourceUri`. `structuredContent` (and tool `outputSchema`/`title`) were introduced in **2025-06-18**, so they don't exist in the 2025-03-26 protocol the sample negotiates.

**Impact:** A server that follows the spec literally and only accepts 2025-11-25 would answer the Alexa+ handshake with its own version, and the client might then disconnect. One that negotiates down to 2025-03-26 has no spec basis for sending `structuredContent`. We had to:
- accept `2025-03-26`, `2025-06-18` and `2025-11-25`;
- send both `structuredContent` and a JSON text copy in `content` on every result;
- add a second smoke check for the `2025-03-26` handshake.

**Suggested improvements:**
1. State the exact `protocolVersion` the production Alexa+ client sends today, and the date it will move to 2025-11-25.
2. Fix the lifecycle sample so the version and the fields it uses agree.
3. Say whether Alexa+ reads `structuredContent`, `content`, or both, and whether it validates against `outputSchema`.

---

## 2. Authentication: Alexa+ conflicts with the official SDK's defaults (Medium)

**Observed** (QuickStart, "Authentication checklist"):
- "Your MCP server returns `401 Unauthorized` (**without** a `WWW-Authenticate` header) for unauthenticated requests."
- "Not Supported Yet: … `WWW-Authenticate` header in 401 responses."

In the MCP authorization spec, `WWW-Authenticate` with a `resource_metadata` URL is the primary way a client discovers protected-resource metadata after a 401. 2025-06-18 required it; 2025-11-25 also allows discovery through the well-known URI alone. The official Python SDK's auth middleware sends the header. So **a server built with the official SDK's auth support fails Alexa+'s checklist by default**, even though suppressing the header is spec-legal under 2025-11-25.

**Suggested improvements:**
1. Have the Alexa+ client tolerate (ignore) `WWW-Authenticate` instead of requiring servers to suppress it.
2. Until then, flag the conflict prominently and show how to suppress the header in the major SDKs.
3. Publish a timeline for DCR / CIMD support. Both are listed as unsupported, and CIMD is the client-registration approach the 2025-11-25 spec introduced.

---

## 3. Undocumented Alexa+ client behavior (High)

To make the server robust we needed to know how the Alexa+ client behaves on the wire. None of the following is documented. We had to support every possibility, and in several cases we found real failure modes in the SDK while doing it (see §10).

| Question | Why it matters | What we had to do |
|---|---|---|
| Does Alexa+ send `Mcp-Session-Id`? Does it reuse sessions? | Stateless vs stateful server design | Run stateless and ignore any session id. |
| Does it send the `MCP-Protocol-Version` header after `initialize`? | Spec-mandated 400 on an unsupported value | Accept it absent or present. |
| Does it ever open `GET /mcp` (SSE) or send `DELETE`? | Stream lifecycle, resource use | Return 405 for both in stateless mode. |
| Request `id` types: string, integer, or other? | The SDK silently drops non-integer, non-string ids (see §10) | Pre-screen ids and answer `-32600`. |
| JSON-RPC **batching** under 2025-03-26 (which permits it)? | The SDK rejects batch arrays with 400 | Unsupported. Undocumented whether Alexa+ ever batches. |
| **Timeout and retry policy** for `tools/call` | An `approve` retried after a timeout reuses a single-use token, so the retry gets `permission_denied` even though the action ran | Designed around it: re-staging returns `already_done`. A documented retry policy, or an idempotency key in `_meta`, would remove the guesswork. |
| Where the **500 ms** budget is measured (from the Alexa edge? does TLS count?) | Region choice, cold starts | Measured server-side only (under 1 ms per tool call). |
| **Egress IP ranges / ASNs** for the Alexa+ client | Allowlisting a no-auth endpoint at a WAF | Can only rate-limit. |
| `Host`, `Origin` and `User-Agent` values | DNS-rebinding protection uses a Host allowlist | Made allowed hosts configurable (`AEGIS_ALLOWED_HOSTS`). |
| Is elicitation used over stateless HTTP? | Elicitation needs a server-to-client channel | Used a two-call approval-token pattern instead. |

**Suggested improvement:** Publish an **"Alexa+ MCP Client Contract"** page covering the transport (headers, session use, SSE, batching), id types, timeouts and retries, concurrency per customer, egress ranges, and the exact negotiated version. One page would have saved most of our transport-hardening time.

---

## 4. Tools can't see device modality or locale (High)

**Observed** (Functional Requirements):
- §3: "Provide a clear spoken or visual message when a feature is unavailable on the current device type."
- §9: "Never reference visual elements… on devices without a screen"; "Present a maximum of 5 options…"
- §10: Cross-modal consistency.

But neither the `tools/call` payload nor the documented `_meta` carries **device type, screen presence, locale or time zone**. Our only safe option was to write every response for the lowest-capability device: never mention a screen, never offer more than 5 options. That works, but it means Echo Show users get a voice-only experience even when there's a screen.

**Suggested improvement:** Pass a small, documented context object in `params._meta` on every `tools/call`, for example `alexa/device: {hasScreen, viewportClass}`, `alexa/locale` and `alexa/timeZone`. MCP reserves `_meta` for exactly this kind of host context.

---

## 5. No auth tier between "none" and full OAuth (Medium)

Aegis tools contain no personal account data; they act on fixture voicemails. The only documented protection, though, is OAuth 2.1 + PKCE account linking. That's heavy for a hackathon build and for non-personal tools. We shipped with **no auth** (a recorded product decision) and rely on a WAF rate limit, a 64 KiB body cap and simulated actions.

**Suggested improvement:** Support a **service-level credential**: a per-add-on shared secret, or a signed request from Alexa+ that the server can verify. That would let developers prove a request came from Alexa+ without implementing per-customer account linking.

---

## 6. The "still working" requirement has no transport (Medium)

**Observed** (Functional Requirements §2): "Surface an interim 'still working' message when tool calls are slow."

MCP's mechanism for this is progress notifications, sent over an SSE response stream. The documented Alexa+ setup (Streamable HTTP, with a stateless design encouraged by the lifecycle doc) is naturally served with plain JSON responses, which can't carry interim messages. The lifecycle doc's `_meta.ui.invoking` / `invoked` strings come close, but they're only documented for MCP Apps UI.

**Suggested improvement:** Document how Alexa+ produces the interim message. Is it client-side text from a tool annotation or `_meta.ui.invoking`, progress notifications over SSE, or something else? Then say whether voice-only (non-MCP-App) add-ons can use it.

---

## 7. No local conformance harness (Medium)

The docs list a Local Inspector, the web simulator and devices (we didn't get to them in this build). We found **no downloadable Alexa+ client emulator or conformance suite** we could run headless in CI. We wrote our own 22 raw-HTTP smoke checks covering:
- handshake at 2025-03-26 and 2025-11-25;
- `tools/list` shape;
- `isError` contracts;
- 405 on GET/DELETE, 413 for oversized bodies, 400 for a bad version header, 403 for a foreign Origin;
- invalid ids, half-closed connections, and non-object arguments.

All of that is our best guess at Alexa+ behavior, not a test against it.

**Suggested improvement:** Publish an `alexa-ai test conformance <url>` command (or a container image) that replays real Alexa+ client traffic, checks the certification §13 "MCP Tool Validation" items, and exits non-zero for CI.

---

## 8. Amazon Devices Builder Tools MCP server DX (Medium)

We used `amazon-devices-buildertools` from Claude Code to fetch the Alexa+ docs.

| Friction | Detail | Suggestion |
|---|---|---|
| Mandatory handshake | The first `search_documentation` calls failed with `PROJECT_CONTEXT_REQUIRED` until we called `set_project_context`, even with no `.adbt-config.json` (passing `{}` was enough). | Default to an empty context, or make the requirement a server instruction rather than an error. |
| Platform filter doesn't fit Alexa+ | `search_documentation` and `list_documents` require `target_platform.device_os` (`vega_os` / `fire_os`). Alexa+ add-ons are cloud MCP servers with no device OS; we passed both values to get Alexa+ results. | Add `alexa_plus` (or make the filter optional) and an add-ons document type. |
| Oversized output | `list_documents` returned **111,085 characters on one line**, beyond our client's tool-output limit. It had to be saved to a file and sliced. | Paginate, add a `query`/`category` filter, and return compact rows. |
| Navigation chrome in every page | Each `read_document` result for an Alexa+ page included the **entire left navigation tree** (about 65 links) before the content. Reading 5 pages repeated it 5 times. | Strip site chrome; return the article body plus an optional table of contents. |
| Search relevance | A search for *"event schema JSON payload Alexa"* returned Smart Home `Alexa.DataController` and skill-development notification schemas, neither of which applies to MCP add-ons. | Separate Alexa+ add-on docs from classic Alexa skills and Smart Home in the index, or label results by product. |

---

## 9. Documentation defects (Low)

1. **Wrong link target.** On *Alexa+ MCP QuickStart* → "Technical requirements", the text "**MCP Apps**" links to `…/2025-11-25/basic/transports#streamable-http` (the transports page), not to the MCP Apps extension.
2. **Placeholder section.** QuickStart → "Security and data policies" says only: "Security and data policy details will be published in a future revision of this guide." Developers can't design data handling for certification without it.
3. **Thin capabilities page.** *Supported Capabilities* lists only two links (Authentication, Account Linking). It doesn't say what MCP features the client supports: elicitation, sampling, roots, resources, prompts, progress, logging, cancellation.
4. **No add-on concept of "raw event schemas".** Coming from Alexa skills, we looked for request/event schemas. MCP add-ons have none (the wire format is plain MCP JSON-RPC), but the docs never say so. A sentence like "There are no Alexa-specific event envelopes; your server receives standard MCP requests, examples below" would prevent that search.

---

## 10. Ecosystem notes: MCP Python SDK and Strands (Info)

These are **not Amazon products**. They're included because any Python developer building an Alexa+ add-on with the official SDK will hit them, and Amazon's samples or Agent Skill could warn about them. Each was reproduced against `mcp==2.2.0`, then fixed or worked around in our code.

| # | Behavior | Effect on an Alexa+ server | Our workaround |
|---|---|---|---|
| a | In stateless mode, `GET /mcp` still opens a long-lived stream (200 OK, never closes) | Ties up connections; contradicts "no server-initiated stream" | ASGI guard returns 405 |
| b | A request whose `id` is null, a float, a bool, an array or an object is treated as a notification: **202 with no body** | The client waits forever | Pre-screen, answer `-32600` |
| c | `tools/call` with non-object `arguments` gets a JSON-RPC `-32602` | Spec says tool input errors should be `isError` so the model can self-correct | Pre-screen, answer `isError` invalid_input |
| d | A client disconnecting mid-body logs a **full traceback at ERROR** per request | Easy log flooding on a public endpoint | Buffer the body with a deadline before the SDK sees it |
| e | No request-body read deadline: a stalled body holds the connection indefinitely (observed past 40 s) | Slowloris exposure | 5 s deadline then 408, plus a uvicorn concurrency cap |
| f | `initialize` also accepts `2024-11-05` | Wider surface than intended | Documented; not overridden |
| g | uvicorn (h11) closes the socket on client half-close: **the tool runs but the response is discarded** | An approval can execute while its confirmation is lost | Subclassed the protocol to keep the socket writable until the response is sent |
| h | `strands-agents==1.57.2` requires `mcp<2.2`, so it can't share an environment with an `mcp==2.2.0` server | A Bedrock agent harness needs its own process and venv | Separate `agent/` deployable (see `docs/aws_bedrock_integration.md`) |

**Suggestion for Amazon:** The Add-on Agent Skill and sample servers could ship a short "production hardening" checklist covering items a–g, since every Alexa+ add-on exposes an MCP endpoint to the internet.

---

## What worked well

- **Clear certification criteria.** The Functional Requirements page, especially §2 Error Handling, §9 Voice-Only and §13 MCP Tool Validation, was specific enough to turn into tests directly. Our output-schema check, "one next step per error" rule and 5-option limit came straight from it.
- **The design guide's "Declare only what you honor"** rule, and its advice that the model trusts the schema, shaped our inputs. We accept no phone numbers from the client, have no ignored filters, and set `additionalProperties: false` everywhere.
- **Standard MCP, not a proprietary envelope.** Because Alexa+ speaks plain MCP, the same server worked unchanged with a second client (Strands on Bedrock) in our interop test.
- **Builder Tools MCP inside the coding agent.** Fetching official docs from inside Claude Code, despite the friction in §8, kept the build grounded in the actual docs.

---

## Top five asks, in priority order

1. Publish an **Alexa+ MCP Client Contract** page (§3) and fix the **protocol version story** (§1).
2. **Pass device modality and locale** in `tools/call` `_meta` (§4).
3. **Resolve the `WWW-Authenticate` conflict** with the MCP auth spec (§2) and add a **service-level credential** tier (§5).
4. Ship a **headless conformance test** for CI (§7).
5. Make the **Builder Tools MCP server** add-on-aware: an Alexa+ filter, paginated listings, and pages without navigation chrome (§8).
