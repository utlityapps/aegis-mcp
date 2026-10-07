# Aegis MCP Server: Protocol Architecture

**Status:** Specification only. No implementation code.
**Author role:** Agent A, Protocol Architect
**Date:** 2026-10-03
**Normative sources:**
- `pyproject.toml` `[tool.aegis.*]`: the product agreements. They win over this document if the two conflict.
- `CLAUDE.md`, including its "Alexa+ MCP target rules" section.
- MCP specification 2025-11-25: base protocol, lifecycle, Streamable HTTP transport, tools.
- Alexa+ MCP Toolkit docs: client lifecycle, functional requirements §2, §9 and §13, and the tools/schema design guide.

The words MUST, SHOULD and MAY are used as in RFC 2119.

---

## 1. Purpose and scope

Aegis is an elder-care, voice-native voicemail scam-defense companion on Alexa+. A senior says *"Alexa, ask Aegis to check the voicemail I just got from the IRS."* Alexa+, acting as the MCP client, calls the Aegis MCP server. Aegis gives a deterministic verdict and explains the red flags in plain spoken sentences. If the senior asks for it and then says "yes", Aegis blocks the number or reports the call. Both actions are simulated.

This document defines:
- the transport and lifecycle contract (§3–4)
- the five tools: their names, descriptions, annotations, `inputSchema` and `outputSchema` (§5–6)
- input constraints and validation order (§7)
- the error model (§8)
- the state machines for staged actions, approval tokens and registries (§9)
- the requirements traceability (§11)

It does **not** cover:
- real call blocking or reporting. Every action is simulated (`simulated: true`).
- audio, STT or TTS. Fixtures are text transcripts.
- MCP Apps or visual UI. Aegis is voice-first and uses Alexa's data-only flow.
- account linking or OAuth. Decided: no auth for the hackathon (§12 Q2).

---

## 2. Component architecture

```
 ┌──────────────┐  Streamable HTTP (HTTPS via tunnel)   ┌──────────────────────────────────────────┐
 │ Alexa+ MCP   │ ── POST /mcp  JSON-RPC 2.0 ─────────▶ │ server/  (mcp==2.2.0, uvicorn, starlette)│
 │ Client       │ ◀─ application/json response ──────── │                                          │
 └──────────────┘                                       │  Transport layer   (§3)                  │
                                                        │  Tool layer        (§5–6)                │
                                                        │    ├─ input validation (§7)              │
                                                        │    ├─ speech shaping ("say" fields)      │
                                                        │    └─ error mapping (§8)                 │
                                                        │  Permission layer  (§9)                  │
                                                        │    ├─ PendingActionRegistry (in-memory)  │
                                                        │    ├─ BlockRegistry / ReportRegistry     │
                                                        │    └─ approval tokens (single-use)       │
                                                        │               │                          │
                                                        │               ▼  pure function calls     │
                                                        │  aegis/engine.py  (stdlib, no network,   │
                                                        │    no LLM, 12 fixed heuristics)          │
                                                        │               │                          │
                                                        │               ▼                          │
                                                        │  fixtures/voicemails/  (8 text fixtures) │
                                                        └──────────────────────────────────────────┘
```

**Layering rules**
- `aegis/engine.py` stays stdlib-only, network-free and LLM-free. Every verdict, score and flag sentence comes from it. The server layer MUST NOT change, re-rank or rewrite a verdict. It may only shorten flag lists to fit voice limits.
- Only `server/` imports third-party packages (`mcp`, `uvicorn`, `starlette`, pinned).
- The engine's public API maps onto the tools like this:

| Engine function | Used by tool(s) |
|---|---|
| `analyze_voicemail` | `check_voicemail`, `explain_red_flags`, `report_scam` (staging readback) |
| `explain_red_flags` | `explain_red_flags`, `check_voicemail` (top flags) |
| `propose_actions` | `check_voicemail` (`suggested_actions`) and staging in `block_number` / `report_scam` |
| `approve_action` | `block_number` / `report_scam` with `decision: "approve"` |
| `reject_action` | `block_number` / `report_scam` with `decision: "reject"` |

---

## 3. Transport: Streamable HTTP

### 3.1 Endpoint
- One MCP endpoint, `/mcp`. The default bind is `127.0.0.1:8000`, configurable through `--host`/`AEGIS_HOST` and `--port`/`AEGIS_PORT`.
- Alexa+ needs a remote HTTPS URL. During development the endpoint is exposed through a tunnel (for example cloudflared) that terminates TLS. The add-on manifest (`addon.json`) points `integrations[0].config.endpoints.default.uri` at the tunnel's `https://…/mcp`.
- The legacy HTTP+SSE transport MUST NOT be offered.

### 3.2 Stateless mode (default)
The server runs with `--stateless` by default:

| Aspect | Behavior |
|---|---|
| `Mcp-Session-Id` | Not issued. The server MUST NOT require it and ignores it if present. |
| `POST /mcp` | Accepts exactly one JSON-RPC request or notification per POST. |
| Response mode | JSON response mode: `Content-Type: application/json`, a single response object. An SSE stream is not used for responses, which keeps latency low and has no stream lifecycle. |
| `GET /mcp` | `405 Method Not Allowed`. There is no server-initiated stream. |
| `DELETE /mcp` | `405 Method Not Allowed`. There is no session to end. |
| Notifications / responses sent by the client | `202 Accepted`, no body. |
| Server→client requests (sampling, elicitation, roots/list) | **Not used.** Stateless mode has no channel back to the client. This is why confirmation uses the two-call token pattern (§9) instead of MCP elicitation. |

**Why stateless:** Alexa+ keeps conversation continuity in its own history, not in an MCP session. Stateless requests also survive tunnel reconnects and need no session affinity. The only server-side state is the permission registries (§9), which are process-scoped, not session-scoped.

### 3.3 Required HTTP behavior
- **Accept:** the client sends `Accept: application/json, text/event-stream`. The server replies with `application/json`.
- **`MCP-Protocol-Version` header:** after initialization, clients send this header. If it is present with an unsupported value, the server MUST answer `400 Bad Request`. If it is absent, the server assumes the version negotiated in §4. Stateless mode doesn't track that version per client, so it falls back to the spec's default rule.
- **Origin validation:** the server MUST validate the `Origin` header when present, to prevent DNS rebinding. The only browser origins it allows are its own (loopback on the bound port, plus each `AEGIS_ALLOWED_HOSTS` name over HTTPS), so the voice simulator page it serves at `/simulator` can call `/mcp`; any other page gets `403`. `--no-simulator` removes the page and those origins. Requests without `Origin`, which is normal for server-to-server traffic like Alexa+, are allowed.
- **Body limits:** request bodies over 64 KiB → `413`. Tool arguments are tiny; this bounds abuse through the public tunnel.
- **Deadlines:** the full request body must arrive within 5 s (otherwise 408), and each tool call must finish within 3 s (otherwise `isError` `unavailable` with a spoken retry message).
- **Latency budget:** under 500 ms round trip (Alexa+ requirement). Engine analysis is in-process regex work over short text, with a target p95 under 20 ms server-side. The tunnel takes most of the budget.

### 3.4 Process model
- **Exactly one worker process.** The registries in §9 live in memory. Running more than one uvicorn worker would split them, so tokens issued by one worker would fail in another. This is an intentional limitation (`in_memory_registries = true`).
- Registry mutations are serialized under a single `asyncio.Lock`, so approve and reject can't race on the same token.

---

## 4. Lifecycle and capability negotiation

### 4.1 initialize
Alexa+ sends (verbatim from the Alexa+ client lifecycle doc):
```json
{"jsonrpc":"2.0","method":"initialize","id":"4e3bdaee-0",
 "params":{"protocolVersion":"2025-03-26",
           "capabilities":{"roots":{"listChanged":true}},
           "clientInfo":{"name":"Alexa+ MCP Client","version":"1.0.0"}}}
```

The server responds:
```json
{"jsonrpc":"2.0","id":"4e3bdaee-0",
 "result":{
   "protocolVersion":"<negotiated>",
   "capabilities":{"tools":{"listChanged":false}},
   "serverInfo":{"name":"aegis","title":"Aegis","version":"0.1.0"},
   "instructions":"Aegis checks voicemails for scams for older adults. Verdicts are deterministic. Never block or report anything until the person has said yes to a read-back; use the approval_token flow. Never read IDs or tokens aloud."}}
```

### 4.2 Version negotiation rules
- Supported versions: `2025-11-25` (preferred), `2025-06-18` and `2025-03-26`.
- If the client asks for a supported version, the server MUST echo it back. Otherwise it MUST answer with its latest version (`2025-11-25`), per the MCP lifecycle spec.
- Alexa+ currently asks for `2025-03-26`, so the server MUST answer `2025-03-26` to Alexa+. The `[tool.aegis.mcp_server] spec = "2025-11-25+"` agreement applies when the client offers 2025-11-25, which is what the smoke test does. A second smoke check confirms a `2025-03-26` initialize is accepted (§12 Q1).
- **Wire compatibility across versions:** every `tools/call` result MUST include both `content` (a text block holding the JSON serialization of `structuredContent`) and `structuredContent`. `outputSchema`, `title` and `annotations` are sent in `tools/list` whatever the negotiated version; older clients ignore fields they don't know. Alexa's own sample sends `structuredContent` under `2025-03-26`.

### 4.3 Declared capabilities
| Capability | Declared | Reason |
|---|---|---|
| `tools` | yes, `listChanged: false` | The tool set is fixed. Alexa+ refreshes tools only on `alexa-ai deploy`. |
| `resources` | no | Not needed for the voice flow. "Declare only what you honor." |
| `prompts` | no | Same reason. |
| `logging` | no | The server logs locally (§10). It doesn't stream logs to the client. |
| `completions` | no | — |

### 4.4 Other methods
- `ping` → `{}`.
- `notifications/initialized` → `202`.
- `tools/list` → all five tools in a single page with no `nextCursor`. The list is static and identical across calls.
- `tools/call` → §5–8.
- Any other method → JSON-RPC `-32601 Method not found`.

---

## 5. Tool catalog

Exactly the five tools in `[tool.aegis.mcp_server] tools`. No placeholder tools (Alexa §13).

| # | name | title | Customer intent | Mutates state? |
|---|---|---|---|---|
| 1 | `list_voicemails` | List voicemails | "What voicemails do I have?" | No |
| 2 | `check_voicemail` | Check a voicemail for scams | "Check the voicemail from the IRS." | No |
| 3 | `explain_red_flags` | Explain warning signs | "Why do you think it's a scam?" / "Tell me more." | No |
| 4 | `block_number` | Block a caller | "Block them." → read-back → "Yes." | Yes, through staging + approval |
| 5 | `report_scam` | Report a scam call | "Report it." → read-back → "Yes." | Yes, through staging + approval |

### 5.1 Annotations (hints for the client, not security controls)

| Tool | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` |
|---|---|---|---|---|
| `list_voicemails` | true | false | true | false |
| `check_voicemail` | true | false | true | false |
| `explain_red_flags` | true | false | true | false |
| `block_number` | false | true | false | false |
| `report_scam` | false | true | false | false |

`openWorldHint` is `false` because actions are simulated and nothing leaves the process. If real reporting is ever added, it becomes `true`.

### 5.2 Shared conventions
- **JSON Schema dialect:** 2020-12, the MCP 2025-11-25 default. Every `inputSchema` and `outputSchema` has root `"type": "object"`. Every input schema sets `"additionalProperties": false`.
- **`say` field:** every successful result carries `say`, a plain, senior-friendly English text of at most 60 words. It MUST NOT contain voicemail IDs, action IDs, approval tokens, tool names, JSON, scores as raw numbers, or technical jargon. It MUST NOT mention a screen. Alexa+ composes the final speech, and `say` is the recommended wording.
- **Spoken caller names:** `caller_label` is the spoken form of the caller: the name if known, otherwise the number read in digit groups.
- **Option limit:** a result never offers more than 5 choices (Alexa §9).
- **No hidden parameters:** every declared input is honored. There are no ignored filters.

---

## 6. Tool definitions

The JSON below is exactly what `tools/list` returns for each tool, apart from whitespace. `$defs` are written out inline in each schema because some clients don't resolve `$ref`.

### Shared definitions (written out inline in each schema; shown once here)

```json
{
  "VoicemailId": {
    "type": "string",
    "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$",
    "description": "Opaque voicemail identifier returned by list_voicemails or check_voicemail. Never read aloud."
  },
  "Verdict": { "type": "string", "enum": ["SCAM", "SUSPICIOUS", "LEGITIMATE"] },
  "RiskScore": { "type": "integer", "minimum": 0, "maximum": 100 },
  "VoicemailSummary": {
    "type": "object",
    "required": ["voicemail_id", "caller_label", "caller_number", "received_at"],
    "properties": {
      "voicemail_id": { "$ref": "#/$defs/VoicemailId" },
      "caller_label": { "type": "string", "maxLength": 80, "description": "Spoken form: caller name if known, else the number." },
      "caller_number": { "type": "string", "maxLength": 32, "description": "Caller number as given in fixture metadata (trusted input)." },
      "received_at": { "type": "string", "format": "date-time" }
    },
    "additionalProperties": false
  },
  "ApprovalToken": {
    "type": "string",
    "pattern": "^[A-Za-z0-9_-]{32,128}$",
    "description": "Single-use token from a staged action. Pass back only after the person says yes (or no). Never read aloud."
  }
}
```

### 6.1 `list_voicemails`

```json
{
  "name": "list_voicemails",
  "title": "List voicemails",
  "description": "Lists the person's voicemails, newest first, up to 5 at a time. Call this when the person asks what voicemails they have, or when you need to find a voicemail before checking it. Returns caller names or numbers and when each message arrived. Does not judge whether any message is a scam; use check_voicemail for that.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "limit": {
        "type": "integer", "minimum": 1, "maximum": 5, "default": 5,
        "description": "How many voicemails to return (1-5)."
      },
      "cursor": {
        "type": "string", "maxLength": 64,
        "description": "Pass next_cursor from a previous call to hear more (\"tell me more\", \"next\"). Omit for the newest voicemails."
      }
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["voicemails", "total", "next_cursor", "say"],
    "properties": {
      "voicemails": { "type": "array", "maxItems": 5, "items": { "$ref": "#/$defs/VoicemailSummary" } },
      "total": { "type": "integer", "minimum": 0 },
      "next_cursor": { "type": ["string", "null"] },
      "say": { "type": "string", "maxLength": 400 }
    },
    "additionalProperties": false
  },
  "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false }
}
```

**Semantics**
- Sort order: `received_at` descending, ties broken by `voicemail_id` ascending, so pages are stable.
- `cursor` is an opaque offset token minted by the server. An unknown or tampered cursor gives an input error (§8).
- An empty mailbox returns `voicemails: []` and `say: "You don't have any voicemails right now."`, not an error.

### 6.2 `check_voicemail`

```json
{
  "name": "check_voicemail",
  "title": "Check a voicemail for scams",
  "description": "Checks one voicemail for signs of a scam and returns a verdict: SCAM, SUSPICIOUS, or LEGITIMATE, with the top warning signs in plain words. Call this when the person asks to check a voicemail, for example 'the voicemail from the IRS' (use caller_hint) or 'my last voicemail' (pass no arguments). Verdicts come from fixed rules, not opinion. If more than one voicemail matches, returns up to 5 candidates to ask the person about. Never blocks or reports anything.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "voicemail_id": { "$ref": "#/$defs/VoicemailId" },
      "caller_hint": {
        "type": "string", "minLength": 1, "maxLength": 80,
        "description": "Who the person says called, in their words: a name, organization, or part of a number. Examples: 'IRS', 'Internal Revenue Service', 'the bank', 'Medicare', 'my grandson', '555-0147'."
      }
    },
    "not": { "required": ["voicemail_id", "caller_hint"] },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["status", "say"],
    "properties": {
      "status": { "type": "string", "enum": ["checked", "ambiguous"] },
      "voicemail": { "$ref": "#/$defs/VoicemailSummary" },
      "verdict": { "$ref": "#/$defs/Verdict" },
      "risk_score": { "$ref": "#/$defs/RiskScore" },
      "top_flags": {
        "type": "array", "maxItems": 3,
        "items": {
          "type": "object", "required": ["flag_id", "say"],
          "properties": { "flag_id": { "type": "string" }, "say": { "type": "string", "maxLength": 200 } },
          "additionalProperties": false
        }
      },
      "flag_count": { "type": "integer", "minimum": 0, "maximum": 12 },
      "suggested_actions": {
        "type": "array", "uniqueItems": true,
        "items": { "type": "string", "enum": ["block_number", "report_scam"] },
        "description": "Actions Aegis suggests offering. Nothing is staged yet."
      },
      "candidates": { "type": "array", "minItems": 2, "maxItems": 5, "items": { "$ref": "#/$defs/VoicemailSummary" } },
      "say": { "type": "string", "maxLength": 400 }
    },
    "allOf": [
      { "if": { "properties": { "status": { "const": "checked" } } },
        "then": { "required": ["voicemail", "verdict", "risk_score", "top_flags", "flag_count", "suggested_actions"] } },
      { "if": { "properties": { "status": { "const": "ambiguous" } } },
        "then": { "required": ["candidates"] } }
    ],
    "additionalProperties": false
  },
  "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false }
}
```

**Target resolution (deterministic, no LLM):**
1. If `voicemail_id` is given, use it. Unknown ID → `not_found` error.
2. If `caller_hint` is given, normalize it (lowercase, remove punctuation, expand a fixed synonym table such as `irs` ↔ `internal revenue service`, `ssa` ↔ `social security`). Then match against each fixture's caller name, caller number digits, and the organization the transcript claims to be from.
   - 0 matches → `not_found` error, with `say` offering to list voicemails.
   - 1 match → `checked`.
   - 2 or more → `ambiguous` with up to 5 newest candidates. The server never guesses between matches; it can't see the person's exact words, such as "just got". For the **demo beat** ("the voicemail I just got from the IRS") to resolve in one turn, the fixtures MUST contain exactly one voicemail that matches `IRS`.
3. If neither is given, use the newest voicemail.

**Score clamping:** `risk_score` is the engine score clamped to 0–100. The verdict is computed by the engine from the unclamped score, with thresholds ≥60 for SCAM and 30–59 for SUSPICIOUS.

**`suggested_actions`** comes straight from engine `propose_actions`. It is advisory. `check_voicemail` MUST NOT stage anything.

**Example `say` (SCAM):** "This message looks like a scam. The caller says you owe taxes and threatens arrest, and the real IRS doesn't do that. Would you like me to block this number or report it?"

### 6.3 `explain_red_flags`

```json
{
  "name": "explain_red_flags",
  "title": "Explain warning signs",
  "description": "Explains, in plain spoken sentences, each warning sign Aegis found in a voicemail. Call this after check_voicemail when the person asks why, or says 'tell me more'. Returns up to 5 warning signs per call; use start to continue. For a safe-looking voicemail, says that no warning signs were found.",
  "inputSchema": {
    "type": "object",
    "required": ["voicemail_id"],
    "properties": {
      "voicemail_id": { "$ref": "#/$defs/VoicemailId" },
      "start": {
        "type": "integer", "minimum": 0, "maximum": 11, "default": 0,
        "description": "Index of the first warning sign to explain. Use next_start from the previous call for 'tell me more'."
      },
      "max_flags": {
        "type": "integer", "minimum": 1, "maximum": 5, "default": 3,
        "description": "How many warning signs to explain in this turn (1-5)."
      }
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["voicemail_id", "verdict", "risk_score", "flags", "total_flags", "next_start", "say"],
    "properties": {
      "voicemail_id": { "$ref": "#/$defs/VoicemailId" },
      "verdict": { "$ref": "#/$defs/Verdict" },
      "risk_score": { "$ref": "#/$defs/RiskScore" },
      "flags": {
        "type": "array", "maxItems": 5,
        "items": {
          "type": "object", "required": ["flag_id", "weight", "say"],
          "properties": {
            "flag_id": { "type": "string", "description": "Stable heuristic identifier. Not for speech." },
            "weight": { "type": "integer", "minimum": 0, "description": "Fixed heuristic weight. Not for speech." },
            "say": { "type": "string", "maxLength": 200, "description": "Senior-friendly sentence from the engine." }
          },
          "additionalProperties": false
        }
      },
      "total_flags": { "type": "integer", "minimum": 0, "maximum": 12 },
      "next_start": { "type": ["integer", "null"], "minimum": 1, "maximum": 11 },
      "say": { "type": "string", "maxLength": 600 }
    },
    "additionalProperties": false
  },
  "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false }
}
```

**Semantics**
- Flags are ordered by weight descending, then `flag_id` ascending (deterministic).
- `next_start` is `null` when nothing is left.
- If `start` is at or beyond `total_flags`, the call returns `flags: []`, `next_start: null` and `say: "That's everything I found."`. This is not an error.
- Zero flags → `say: "I didn't find any warning signs in this message."`.

### 6.4 `block_number` and 6.5 `report_scam`: the permission-gated pair

Both tools follow one two-phase contract. Only `name`, `title`, `description`, the action `kind`, and the receipt wording differ.

**Modes, decided by which arguments are present:**

| Mode | Required args | Forbidden args | Effect |
|---|---|---|---|
| **stage** | `voicemail_id` | `approval_token`, `decision` | Creates a PENDING action and returns a read-back plus `approval_token`. Nothing executes. |
| **resolve** | `approval_token`, `decision` | (none; `voicemail_id` optional, MUST match the action's target if given) | `approve` → EXECUTED with a receipt. `reject` → REJECTED and the action is dropped. |

```json
{
  "name": "block_number",
  "title": "Block a caller",
  "description": "Blocks the phone number that left a voicemail. Two steps, always: (1) call with voicemail_id to prepare the block; read the returned 'say' to the person and ask them to confirm. (2) Only after the person clearly says yes, call again with approval_token and decision 'approve'. If they say no, call with decision 'reject'. Never approve without an explicit yes in this conversation. Never read the token aloud. This only blocks; to report a scam use report_scam.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "voicemail_id": { "$ref": "#/$defs/VoicemailId" },
      "approval_token": { "$ref": "#/$defs/ApprovalToken" },
      "decision": {
        "type": "string", "enum": ["approve", "reject"],
        "description": "'approve' only after the person says yes (yes, sure, go ahead, do it). 'reject' if they say no (no, cancel, don't, never mind)."
      }
    },
    "dependentRequired": { "approval_token": ["decision"], "decision": ["approval_token"] },
    "anyOf": [ { "required": ["voicemail_id"] }, { "required": ["approval_token"] } ],
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "required": ["status", "kind", "say"],
    "properties": {
      "status": { "type": "string", "enum": ["staged", "executed", "rejected", "already_done"] },
      "kind": { "type": "string", "const": "block_number" },
      "action": {
        "type": "object",
        "required": ["action_id", "voicemail_id", "caller_label", "caller_number", "created_at", "expires_at"],
        "properties": {
          "action_id": { "type": "string" },
          "voicemail_id": { "$ref": "#/$defs/VoicemailId" },
          "caller_label": { "type": "string" },
          "caller_number": { "type": "string" },
          "created_at": { "type": "string", "format": "date-time" },
          "expires_at": { "type": "string", "format": "date-time" }
        },
        "additionalProperties": false
      },
      "approval_token": { "$ref": "#/$defs/ApprovalToken" },
      "receipt": {
        "type": "object",
        "required": ["receipt_id", "kind", "caller_number", "executed_at", "simulated"],
        "properties": {
          "receipt_id": { "type": "string" },
          "kind": { "type": "string", "const": "block_number" },
          "caller_number": { "type": "string" },
          "executed_at": { "type": "string", "format": "date-time" },
          "simulated": { "type": "boolean", "const": true }
        },
        "additionalProperties": false
      },
      "say": { "type": "string", "maxLength": 400 }
    },
    "allOf": [
      { "if": { "properties": { "status": { "const": "staged" } } },   "then": { "required": ["action", "approval_token"] } },
      { "if": { "properties": { "status": { "const": "executed" } } }, "then": { "required": ["receipt"], "not": { "required": ["approval_token"] } } },
      { "if": { "properties": { "status": { "enum": ["rejected", "already_done"] } } }, "then": { "not": { "required": ["approval_token"] } } }
    ],
    "additionalProperties": false
  },
  "annotations": { "readOnlyHint": false, "destructiveHint": true, "idempotentHint": false, "openWorldHint": false }
}
```

`report_scam` is the same, with these differences:
- `"name": "report_scam"`, `"title": "Report a scam call"`, and `kind`/`receipt.kind` `const: "report_scam"`.
- Description: *"Reports a voicemail as a scam call. Two steps, always: (1) call with voicemail_id to prepare the report; read the returned 'say' and ask the person to confirm. (2) Only after a clear yes, call again with approval_token and decision 'approve'; on no, decision 'reject'. Never read the token aloud. This only reports; to block the caller use block_number."*
- `action` adds `"verdict": Verdict` (required). The read-back states the verdict, so a person who reports a LEGITIMATE-looking voicemail hears that first. For example: "Aegis thought this call looked safe. Do you still want me to report it?"
- `receipt` adds `"voicemail_id"` (required).

**Why one tool per action kind instead of a generic `approve_action` tool:** Alexa §13 wants one tool per customer intent. Binding the approval to the tool that names the action also makes the kind check (§9.3) structural: `block_number` can only ever approve `block_number` actions.

**Why two calls instead of MCP elicitation:** elicitation is a server→client request and needs a stateful session with a return channel (§3.2). The token round-trip works in stateless mode and puts the senior's "yes" between two separate tool calls that are both visible in Alexa's history.

**Example dialogue (screenless Echo Dot)**
1. User: "Block them." → `block_number{voicemail_id}` → `status: staged`, `say: "I can block calls from 2 0 2, 5 5 5, 0 1 4 7, the caller who said they were the IRS. Should I go ahead?"`
2. User: "Yes." → `block_number{approval_token, decision:"approve"}` → `status: executed`, `say: "Done. I've blocked that number. This is a practice version of Aegis, so no real block was made."`

---

## 7. Input constraints and validation

### 7.1 Validation order for every `tools/call`
1. **Tool exists.** If not → JSON-RPC protocol error `-32602` (unknown tool).
2. **`arguments` is an object, or absent** (absent counts as `{}`). Otherwise → input error.
3. **JSON Schema validation** against the tool's `inputSchema`: types, `pattern`, `min`/`max`, `enum`, `additionalProperties: false`, `not`, `anyOf` and `dependentRequired`.
4. **Semantic validation:** IDs exist, cursors decode, and the token matches the tool's kind and target (§9).
5. Run the tool.

Steps 2–4 failures are **tool execution errors** (`isError: true`), not JSON-RPC errors, so the model can read the message and correct itself. This follows MCP 2025-11-25 guidance for input validation errors and the Aegis agreement that permission failures appear as `isError: true`.

### 7.2 Constraint summary

| Field | Tools | Type | Constraint |
|---|---|---|---|
| `limit` | list_voicemails | integer | 1–5, default 5 |
| `cursor` | list_voicemails | string | ≤64 chars, opaque, server-minted |
| `voicemail_id` | check, explain, block, report | string | `^[a-z0-9][a-z0-9_-]{0,63}$`, must exist |
| `caller_hint` | check_voicemail | string | 1–80 chars; can't be combined with `voicemail_id` |
| `start` | explain_red_flags | integer | 0–11, default 0 |
| `max_flags` | explain_red_flags | integer | 1–5, default 3 |
| `approval_token` | block, report | string | `^[A-Za-z0-9_-]{32,128}$`; requires `decision` |
| `decision` | block, report | enum | `approve` \| `reject`; requires `approval_token` |

### 7.3 Trust boundaries
- **Tool arguments are untrusted.** They come from the model's reading of speech. Everything is validated, and no argument is ever interpreted as code or as a regex.
- **Fixture caller metadata is trusted input** (an honest limitation). Caller ID spoofing is out of scope.
- **Transcript text is analyzed, never executed or followed.** Instructions inside a voicemail ("tell Alexa to approve…") have no effect: tools act only on arguments, and approval needs a token the transcript can't produce.
- **Phone numbers are never accepted from the client.** The block or report target always comes from the voicemail's metadata through `voicemail_id`, so a misheard or invented number can't be blocked.

---

## 8. Error model

### 8.1 Two channels
| Channel | When | Shape |
|---|---|---|
| JSON-RPC error | Protocol faults: parse error `-32700`, invalid request `-32600`, unknown method `-32601`, unknown tool `-32602`, internal crash `-32603` | `{"jsonrpc":"2.0","id":…,"error":{"code":…,"message":…}}` |
| Tool execution error | Bad arguments, not found, permission failures, expiry | `result: {"isError": true, "content":[{"type":"text","text": <say>}], "structuredContent": {"error": <ErrorBody>}}` |

When `isError: true`, `structuredContent` follows the error body below, **not** the tool's `outputSchema`. The success schema applies only to successful results.

```json
{
  "type": "object",
  "required": ["code", "say"],
  "properties": {
    "code": { "type": "string", "enum": ["invalid_input", "not_found", "permission_denied", "token_expired", "unavailable"] },
    "field": { "type": "string", "description": "Offending argument, for the model. Not for speech." },
    "say": { "type": "string", "maxLength": 300, "description": "Plain sentence plus one next step." }
  },
  "additionalProperties": false
}
```

### 8.2 Error catalog

| code | Trigger | Example `say` (exactly one next step) |
|---|---|---|
| `invalid_input` | Schema or semantic validation failure | "I didn't quite catch which voicemail you meant. Would you like me to list your voicemails?" |
| `not_found` | Unknown `voicemail_id`, or `caller_hint` with 0 matches | "I couldn't find a voicemail from the IRS. Would you like to hear your recent voicemails?" |
| `permission_denied` | Wrong, unknown, already-used, or wrong-kind token (`PermissionError`) | "I can't do that without your OK. Would you like me to set it up again?" |
| `token_expired` | Token past `expires_at` | "That request timed out. Would you like me to set it up again?" |
| `unavailable` | Fixtures can't be loaded, or an internal fault is caught at the tool boundary | "Aegis is having trouble right now. Please try again in a few minutes." |

Rules:
- `say` never contains codes, tool names, IDs, tokens or JSON.
- Every error has exactly one suggested next step (Alexa §2).
- A tool MUST NOT return an empty result.
- Unexpected exceptions inside a tool are caught and mapped to `unavailable` with `isError: true`. The server process MUST NOT crash or return a malformed payload.

---

## 9. State and state transitions

### 9.1 State inventory (all in-memory, process-scoped, lost on restart)

| Store | Key | Value | Lifetime |
|---|---|---|---|
| `VoicemailStore` | `voicemail_id` | Fixture (metadata + transcript) | Read-only, loaded at startup |
| `PendingActionRegistry` | `action_id` | `{kind, voicemail_id, caller_number, token_digest, created_at, expires_at, state}` | Until resolved, expired or restart |
| `BlockRegistry` | `caller_number` | receipt | Until restart |
| `ReportRegistry` | `voicemail_id` | receipt | Until restart |

Analysis results are **not** state: nothing about them changes what a later call may do. The server *memoizes* them in a bounded TTL cache keyed by `voicemail_id`, and caches caller-hint matches by normalized hint (at most 256 entries). That's correct only because the engine is deterministic per transcript, so a cached analysis is identical to a fresh one. Listing voicemails, or an ambiguous match, speculatively warms the analysis cache in a background task. That prefetch is read-only and never stages anything.

### 9.2 Staged action state machine

```
                 stage (block_number|report_scam, voicemail_id)
      (none) ─────────────────────────────────────────────────▶ PENDING
                                                                  │
          ┌────────────────────────┬──────────────────────────────┼─────────────────────┐
          │ approve + valid token  │ reject + valid token         │ now > expires_at    │ process restart
          │ + matching kind        │ + matching kind              │                     │
          ▼                        ▼                              ▼                     ▼
      EXECUTED (terminal)      REJECTED (terminal)            EXPIRED (terminal)     (gone)
      receipt issued,          action dropped,                removed lazily on
      token consumed,          token consumed                 next access
      registry updated
```

| From | Event | Guard | To | Output |
|---|---|---|---|---|
| — | stage | voicemail exists; target not already blocked/reported | PENDING | `status: staged`, token, read-back |
| — | stage | target already blocked (block) / already reported (report) | — | `status: already_done`, no token |
| — | stage | a PENDING action already exists for the same kind + target | PENDING (same one) | Existing action; **its token is rotated** (old token invalid, new token returned). The read-back is repeated, so each "yes" is tied to the most recent read-back. |
| PENDING | approve | token valid, unconsumed, kind matches, not expired | EXECUTED | `status: executed`, receipt `simulated: true` |
| PENDING | reject | token valid, unconsumed, kind matches, not expired | REJECTED | `status: rejected`, `say: "Okay, I won't do that."` |
| PENDING | approve/reject | expired | EXPIRED | `isError`, `token_expired` |
| PENDING | approve/reject | kind mismatch (e.g. report token sent to `block_number`) | PENDING (unchanged) | `isError`, `permission_denied`. **The token is not consumed**, so the correct tool can still use it. |
| PENDING | approve/reject | `voicemail_id` given and ≠ the action's target | PENDING (unchanged) | `isError`, `permission_denied`, token not consumed |
| EXECUTED / REJECTED / EXPIRED | approve/reject | any | unchanged | `isError`, `permission_denied` (reused token) |
| — | approve/reject | unknown token | — | `isError`, `permission_denied` |

Transitions are enforced in code: `aegis.engine.TRANSITIONS` lists the only legal moves (PENDING → PENDING/EXECUTED/REJECTED/EXPIRED; the three terminal states have no exits), and every state change goes through `transition()`, which raises `IllegalTransitionError` (a `PermissionError`, so `permission_denied`) for anything else. Both validation gates in §7.1 run before any transition.

### 9.3 Approval token rules
- **Generation:** 32 or more bytes from a CSPRNG (`secrets.token_urlsafe(32)`, stdlib in the server layer), URL-safe base64.
- **Storage:** only a SHA-256 digest is stored. Comparison is constant-time (`hmac.compare_digest`).
- **Binding:** a token is bound to exactly one `(action_id, kind, voicemail_id)`.
- **Single use:** the first valid approve or reject consumes it atomically, under the registry lock (§3.4). A second use → `permission_denied`.
- **TTL:** 10 minutes from staging, configurable through `AEGIS_APPROVAL_TTL_SECONDS`, range 60–3600. This is long enough for a slow, careful answer and short enough that a stale "yes" can't approve a forgotten action. It gives the clear expiry message Alexa §1 asks for.
- **Never spoken:** tokens appear only in `structuredContent.approval_token` and in the mirrored `content` text block, never in `say`.

### 9.4 Registry state machines
- `BlockRegistry[caller_number]`: NOT_BLOCKED → BLOCKED, only on an EXECUTED `block_number`. No unblock tool in v1, so there is no reverse transition.
- `ReportRegistry[voicemail_id]`: NOT_REPORTED → REPORTED, only on an EXECUTED `report_scam`.
- Both registries reset on restart. Duplicate detection (Alexa §8) works through these registries and through the rotation rule in §9.2.

### 9.5 Conversation-level flow (informative)
```
list_voicemails ─┐
                 ▼
         check_voicemail ──(ambiguous)──▶ person picks ──▶ check_voicemail{voicemail_id}
                 │
                 ├──▶ explain_red_flags (repeat with next_start for "tell me more")
                 │
                 ├──▶ block_number{voicemail_id} ─▶ read-back ─▶ "yes"/"no" ─▶ block_number{token, decision}
                 └──▶ report_scam{voicemail_id}  ─▶ read-back ─▶ "yes"/"no" ─▶ report_scam{token, decision}
```
Each step's output carries the IDs the next step needs (`voicemail_id`, `approval_token`, `next_cursor`, `next_start`). This is the Alexa §13 "output feeds the next tool" rule.

---

## 10. Observability

- Log one structured line per `tools/call`: timestamp, JSON-RPC `id`, tool name, mode (stage/resolve), result status or error code, and server-side latency in ms.
- **Never log** approval tokens, cursors in plain text, or full transcripts. Voicemail IDs and action IDs are fine.
- Logs go to stderr locally. Nothing is sent over the network.
- Records pass through a `QueueHandler` to a background `QueueListener` thread, so writing a log line never blocks a response.

---

## 11. Requirements traceability

| Requirement | Source | Where satisfied |
|---|---|---|
| Streamable HTTP only, no SSE transport | Alexa quickstart; MCP 2025-11-25 | §3.1 |
| Stateless by default | `[tool.aegis.mcp_server] transport` | §3.2 |
| Negotiate the version Alexa sends (2025-03-26) | Alexa lifecycle doc | §4.2 |
| Under 500 ms latency | Alexa quickstart | §3.3 |
| Exactly 5 tools, all invocable | `[tool.aegis.mcp_server] tools`; Alexa §13 | §5, §6 |
| Valid `inputSchema`, validated before acting | Alexa §13 | §6, §7 |
| Declare only what you honor | Alexa design guide | §5.2, §7.2 |
| Errors via `isError` / JSON-RPC, never malformed or empty | Alexa §13, §2 | §8 |
| No jargon or IDs in speech; one next step | Alexa §2; CLAUDE.md speakable rule | §5.2 `say`, §8.2 |
| At most 5 options; no screen references; read-back + explicit yes | Alexa §9 | §5.2, §6.4 |
| Propose → approve → execute; nothing runs without yes | `[tool.aegis.permission_layer]` | §6.4, §9.2 |
| Single-use tokens; wrong or reused → `PermissionError` → `isError` | `[tool.aegis.permission_layer]` | §8.2, §9.3 |
| Reject drops the action | `[tool.aegis.permission_layer]` | §9.2 |
| Approve tool verifies action kind | CLAUDE.md | §6.4 rationale, §9.2 |
| Duplicate actions detected | Alexa §8 | §9.2, §9.4 |
| Deterministic verdicts, engine untouched | `[tool.aegis.engine]` | §2 layering rules |
| Actions simulated, `simulated: true` | `[tool.aegis.honest_limitations]` | §6.4 receipt schema |
| Pending approvals vanish on restart | `[tool.aegis.honest_limitations]` | §3.4, §9.1 |
| Stable IDs flow between tools | Alexa §13 | §9.5 |

---

## 12. Decisions (resolved 2026-10-03)

Ajayi accepted the proposed default on all five questions. Each decision is recorded in `pyproject.toml` `[tool.aegis.*]`.

| # | Question | Decision | Recorded as |
|---|---|---|---|
| Q1 | Smoke test asserts 2025-11-25 or later, but Alexa+ asks for `2025-03-26` | Keep the existing assertion. Add a second smoke check that a `2025-03-26` initialize is accepted and echoed back. | `mcp_server.accepted_protocol_versions` |
| Q2 | Auth on the public tunnel | No auth for the hackathon. Rely on simulated actions, fixture-only data and the 64 KiB body cap. Note this in the friction log. | `mcp_server.auth = "none"` |
| Q3 | Fixture ID format and fields | Keep the broad `voicemail_id` pattern. Fixtures MUST carry `received_at` and `caller_number`; `caller_name` is optional. Exactly one fixture matches `IRS`. | `mcp_server.fixture_required_fields` |
| Q4 | Receipt `say` mentions the demo nature | Yes. The spoken receipt says no real block or report was made. | `honest_limitations.receipts_say_practice_run` |
| Q5 | Approval TTL | 10 minutes, configurable through `AEGIS_APPROVAL_TTL_SECONDS` (60–3600). | `permission_layer.approval_ttl_seconds = 600` |

---

## 13. Reconciliation note

This spec was written from `pyproject.toml`, `CLAUDE.md` and the Alexa+ docs. The `aegis/`, `server/`, `tests/` and `fixtures/` directories aren't in this working copy. `pyproject.toml` says 58 engine tests and 16 smoke checks pass, so code exists elsewhere. Before implementing, compare the existing `server/` tool schemas against §6. The likely gaps are:
- the `say` fields
- the `caller_hint` resolution
- `next_cursor` / `next_start`
- token rotation and TTL
- the `isError` error bodies

Update whichever side is wrong, and keep `[tool.aegis]` in sync.
