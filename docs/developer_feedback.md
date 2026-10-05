# Amazon Developer Product Feedback: detailed evidence

**Companion to:** [`AMAZON_DEVELOPER_FEEDBACK.md`](../AMAZON_DEVELOPER_FEEDBACK.md). Same tools, same 5 questions; this file holds the quotes, reproductions and workarounds behind each answer.
**Project:** Aegis: Voice-First Scam Defense on Alexa+ (Alexa+ track, Amazon Developer Hackathon "Build, Ship, Shape")
**Date:** 2026-10-05
**Build stack:** Python 3.12, `mcp==2.2.0` (official MCP Python SDK), `uvicorn==0.54.0`, `starlette==1.7.0`

Everything here was observed directly: pages fetched through the Amazon Devices Builder Tools MCP server, behavior reproduced against our own server, or package metadata read from installed packages. Where we haven't exercised something for real yet (deploying to a live Alexa+ client), we say so.

---

## Tool 1: Alexa+ MCP Toolkit and Streamable HTTP (track tool)

### 1. What we used it for
A self-hosted MCP server (five tools, stateless Streamable HTTP, JSON responses) built to the Alexa+ MCP QuickStart, Client and App Lifecycle page, design guide and Functional Requirements. Deployment to the Alexa+ development stage is documented in [`ALEXA_DEPLOY.md`](ALEXA_DEPLOY.md).

### 2. What worked well
- **Clear certification criteria.** Functional Requirements §2 (errors), §9 (voice-only) and §13 (MCP tool validation) turned directly into tests: one next step per error, a 5-option limit, and an `outputSchema` check on every result.
- **"Declare only what you honor"** (design guide) shaped our inputs. We accept no phone numbers from the client, have no ignored filters, and set `additionalProperties: false` everywhere.
- **Standard MCP, not a proprietary envelope.** Nothing in the server is Alexa-specific, so any MCP client can drive it.

### 3. What needs work: evidence

#### Protocol version mismatch (High)

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

#### Authentication: Alexa+ conflicts with the official SDK's defaults (Medium)

**Observed** (QuickStart, "Authentication checklist"):
- "Your MCP server returns `401 Unauthorized` (**without** a `WWW-Authenticate` header) for unauthenticated requests."
- "Not Supported Yet: … `WWW-Authenticate` header in 401 responses."

In the MCP authorization spec, `WWW-Authenticate` with a `resource_metadata` URL is the primary way a client discovers protected-resource metadata after a 401. 2025-06-18 required it; 2025-11-25 also allows discovery through the well-known URI alone. The official Python SDK's auth middleware sends the header. So **a server built with the official SDK's auth support fails Alexa+'s checklist by default**, even though suppressing the header is spec-legal under 2025-11-25.

**Suggested improvements:**
1. Have the Alexa+ client tolerate (ignore) `WWW-Authenticate` instead of requiring servers to suppress it.
2. Until then, flag the conflict prominently and show how to suppress the header in the major SDKs.
3. Publish a timeline for DCR / CIMD support. Both are listed as unsupported, and CIMD is the client-registration approach the 2025-11-25 spec introduced.

#### Undocumented Alexa+ client behavior (High)

To make the server robust we needed to know how the Alexa+ client behaves on the wire. None of the following is documented. We had to support every possibility, and in several cases we found real failure modes in the SDK while doing it (see Tool 2: MCP Python SDK).

| Question | Why it matters | What we had to do |
|---|---|---|
| Does Alexa+ send `Mcp-Session-Id`? Does it reuse sessions? | Stateless vs stateful server design | Run stateless and ignore any session id. |
| Does it send the `MCP-Protocol-Version` header after `initialize`? | Spec-mandated 400 on an unsupported value | Accept it absent or present. |
| Does it ever open `GET /mcp` (SSE) or send `DELETE`? | Stream lifecycle, resource use | Return 405 for both in stateless mode. |
| Request `id` types: string, integer, or other? | The SDK silently drops non-integer, non-string ids (see Tool 2: MCP Python SDK) | Pre-screen ids and answer `-32600`. |
| JSON-RPC **batching** under 2025-03-26 (which permits it)? | The SDK rejects batch arrays with 400 | Unsupported. Undocumented whether Alexa+ ever batches. |
| **Timeout and retry policy** for `tools/call` | An `approve` retried after a timeout reuses a single-use token, so the retry gets `permission_denied` even though the action ran | Designed around it: re-staging returns `already_done`. A documented retry policy, or an idempotency key in `_meta`, would remove the guesswork. |
| Where the **500 ms** budget is measured (from the Alexa edge? does TLS count?) | Region choice, cold starts | Measured server-side only (under 1 ms per tool call). |
| **Egress IP ranges / ASNs** for the Alexa+ client | Allowlisting a no-auth endpoint at a WAF | Can only rate-limit. |
| `Host`, `Origin` and `User-Agent` values | DNS-rebinding protection uses a Host allowlist | Made allowed hosts configurable (`AEGIS_ALLOWED_HOSTS`). |
| Is elicitation used over stateless HTTP? | Elicitation needs a server-to-client channel | Used a two-call approval-token pattern instead. |

**Suggested improvement:** Publish an **"Alexa+ MCP Client Contract"** page covering the transport (headers, session use, SSE, batching), id types, timeouts and retries, concurrency per customer, egress ranges, and the exact negotiated version. One page would have saved most of our transport-hardening time.

#### Tools can't see device modality or locale (High)

**Observed** (Functional Requirements):
- §3: "Provide a clear spoken or visual message when a feature is unavailable on the current device type."
- §9: "Never reference visual elements… on devices without a screen"; "Present a maximum of 5 options…"
- §10: Cross-modal consistency.

But neither the `tools/call` payload nor the documented `_meta` carries **device type, screen presence, locale or time zone**. Our only safe option was to write every response for the lowest-capability device: never mention a screen, never offer more than 5 options. That works, but it means Echo Show users get a voice-only experience even when there's a screen.

**Suggested improvement:** Pass a small, documented context object in `params._meta` on every `tools/call`, for example `alexa/device: {hasScreen, viewportClass}`, `alexa/locale` and `alexa/timeZone`. MCP reserves `_meta` for exactly this kind of host context.

#### No auth tier between "none" and full OAuth (Medium)

Aegis tools contain no personal account data; they act on fixture voicemails. The only documented protection, though, is OAuth 2.1 + PKCE account linking. That's heavy for a hackathon build and for non-personal tools. We shipped with **no auth** (a recorded product decision) and rely on a WAF rate limit, a 64 KiB body cap and simulated actions.

**Suggested improvement:** Support a **service-level credential**: a per-add-on shared secret, or a signed request from Alexa+ that the server can verify. That would let developers prove a request came from Alexa+ without implementing per-customer account linking.

#### The "still working" requirement has no transport (Medium)

**Observed** (Functional Requirements §2): "Surface an interim 'still working' message when tool calls are slow."

MCP's mechanism for this is progress notifications, sent over an SSE response stream. The documented Alexa+ setup (Streamable HTTP, with a stateless design encouraged by the lifecycle doc) is naturally served with plain JSON responses, which can't carry interim messages. The lifecycle doc's `_meta.ui.invoking` / `invoked` strings come close, but they're only documented for MCP Apps UI.

**Suggested improvement:** Document how Alexa+ produces the interim message. Is it client-side text from a tool annotation or `_meta.ui.invoking`, progress notifications over SSE, or something else? Then say whether voice-only (non-MCP-App) add-ons can use it.

#### No local conformance harness (Medium)

The docs list a Local Inspector, the web simulator and devices (we didn't get to them in this build). We found **no downloadable Alexa+ client emulator or conformance suite** we could run headless in CI. We wrote our own 23 raw-HTTP smoke checks covering:
- handshake at 2025-03-26 and 2025-11-25;
- `tools/list` shape;
- `isError` contracts;
- 405 on GET/DELETE, 413 for oversized bodies, 400 for a bad version header, 403 for a foreign Origin;
- invalid ids, half-closed connections, and non-object arguments.

All of that is our best guess at Alexa+ behavior, not a test against it.

**Suggested improvement:** Publish an `alexa-ai test conformance <url>` command (or a container image) that replays real Alexa+ client traffic, checks the certification §13 "MCP Tool Validation" items, and exits non-zero for CI.

#### Documentation defects (Low)

1. **Wrong link target.** On *Alexa+ MCP QuickStart* → "Technical requirements", the text "**MCP Apps**" links to `…/2025-11-25/basic/transports#streamable-http` (the transports page), not to the MCP Apps extension.
2. **Placeholder section.** QuickStart → "Security and data policies" says only: "Security and data policy details will be published in a future revision of this guide." Developers can't design data handling for certification without it.
3. **Thin capabilities page.** *Supported Capabilities* lists only two links (Authentication, Account Linking). It doesn't say what MCP features the client supports: elicitation, sampling, roots, resources, prompts, progress, logging, cancellation.
4. **No add-on concept of "raw event schemas".** Coming from Alexa skills, we looked for request/event schemas. MCP add-ons have none (the wire format is plain MCP JSON-RPC), but the docs never say so. A sentence like "There are no Alexa-specific event envelopes; your server receives standard MCP requests, examples below" would prevent that search.

### 4. Onboarding (zero to "hello world")
- **Server side was fast.** QuickStart to a passing `initialize` at `2025-03-26` from our own client took hours.
- **Live Alexa+ isn't done.** We haven't completed `alexa-ai deploy`, so we've never seen Alexa+'s real requests.
- **The friction:** no headless client or conformance test (we wrote 23 smoke checks by guessing), and no tunnel blueprint. Our DNS-rebinding Host check answered 421 to cloudflared traffic until we set `--http-host-header` or allowlisted the tunnel hostname.

- **Alexa AI CLI setup (from the docs):** for a self-hosted MCP add-on it still requires an AWS account, an IAM user assuming the Amazon-owned role `arn:aws:iam::372468808636:role/AddOn3PDeveloperToolsRead`, Git configured for CodeCommit, and npm pointed at a private CodeArtifact registry whose token *"is valid for 12 hours"*. It also refers to *"the AWS account that you provided to the Alexa Solutions Architect"*, with no documented way to request access.

### 5. Would we build with it again?
**Yes.** Standard MCP means one server for every client. A client-contract page and a conformance test would close most of the gaps above.

---

## Tool 2: MCP Python SDK (`mcp==2.2.0`)

### 1. What we used it for
The protocol layer under Aegis: the low-level `Server`, the stateless Streamable HTTP transport and JSON responses.

### 2. What worked well
Version negotiation across four protocol versions, a working endpoint in a few lines, and an in-process `Client` that made end-to-end tests easy.

### 3. What needs work: evidence
These are upstream behaviors (MCP Python SDK 2.2.0, uvicorn 0.54.0), not Amazon code. They're listed because they're the stack the Alexa+ docs lead Python developers to.

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

**Suggestion for Amazon:** The Add-on Agent Skill and sample servers could ship a short "production hardening" checklist covering items a–g, since every Alexa+ add-on exposes an MCP endpoint to the internet.

### 4. Onboarding (zero to "hello world")
`pip install mcp`, then a passing `initialize` within an hour. The version-2 API differs from most published examples (which use `FastMCP`), so we read the SDK source to find `Server(on_list_tools=…, on_call_tool=…)`.

### 5. Would we build with it again?
**Yes.** The fixes are small; they should ship as defaults (body deadline, id screening, half-close handling).

---

## Tool 3: Amazon Devices Builder Tools MCP server

### 1. What we used it for
Reading the Alexa+ add-on documentation from inside our coding agent (Claude Code).

### 2. What worked well
Official docs in context. It's how we found the Alexa+ version mismatch, the CLI's setup requirements and the simulator's Isolation mode.

### 3. What needs work: evidence

We used `amazon-devices-buildertools` from Claude Code to fetch the Alexa+ docs.

| Friction | Detail | Suggestion |
|---|---|---|
| Mandatory handshake | The first `search_documentation` calls failed with `PROJECT_CONTEXT_REQUIRED` until we called `set_project_context`, even with no `.adbt-config.json` (passing `{}` was enough). | Default to an empty context, or make the requirement a server instruction rather than an error. |
| Platform filter doesn't fit Alexa+ | `search_documentation` and `list_documents` require `target_platform.device_os` (`vega_os` / `fire_os`). Alexa+ add-ons are cloud MCP servers with no device OS; we passed both values to get Alexa+ results. | Add `alexa_plus` (or make the filter optional) and an add-ons document type. |
| Oversized output | `list_documents` returned **111,085 characters on one line**, beyond our client's tool-output limit. It had to be saved to a file and sliced. | Paginate, add a `query`/`category` filter, and return compact rows. |
| Navigation chrome in every page | Each `read_document` result for an Alexa+ page included the **entire left navigation tree** (about 65 links) before the content. Reading 5 pages repeated it 5 times. | Strip site chrome; return the article body plus an optional table of contents. |
| Search relevance | A search for *"event schema JSON payload Alexa"* returned Smart Home `Alexa.DataController` and skill-development notification schemas, neither of which applies to MCP add-ons. | Separate Alexa+ add-on docs from classic Alexa skills and Smart Home in the index, or label results by product. |

### 4. Onboarding (zero to "hello world")
One `.mcp.json` entry (`npx -y @amazon-devices/amazon-devices-buildertools-mcp@latest`). The first searches failed with `PROJECT_CONTEXT_REQUIRED`; `set_project_context({})` fixed it.

### 5. Would we build with it again?
**Yes.** Make it Alexa+-aware (a filter, and pages without the navigation) and it becomes indispensable.


---

## Top asks, in priority order

1. **Alexa+:** fix or document the protocol version; publish a client-contract page; pass device and locale context in `tools/call` `_meta`.
2. **Alexa AI CLI:** a public `npm` package for MCP add-ons, no AWS role assumption, and a documented access-request path.
3. **Alexa+:** a headless conformance test for CI, and a tunnel blueprint.
4. **MCP Python SDK:** ship body deadlines, request-id screening and half-close handling by default.
5. **Builder Tools MCP:** an Alexa+ filter, paginated listings, and pages without navigation chrome.
