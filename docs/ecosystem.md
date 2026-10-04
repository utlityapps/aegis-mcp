# Aegis ecosystem pipeline: Ring → wearable → Fire TV (simulator-grade)

**Status:** Implemented and tested against Aegis's own simulator (`scripts/simulate_ecosystem_event.py`). **Not connected to real Ring, Bee or Fire TV devices.** Ring, Bee and Fire TV publish no third-party webhook, event or ambient-push APIs, so the payload formats below are **Aegis-defined**, and a real deployment needs a bridge that maps vendor events onto them.
**Code:** `server/ecosystem/`. Engine rules are unchanged: verdicts come only from `aegis/engine.py`.

## Flow

```mermaid
sequenceDiagram
    participant R as Ring (via bridge)
    participant B as Wearable (via bridge)
    participant A as Aegis server
    participant E as Deterministic engine
    participant T as Fire TV display

    T->>A: GET /events/firetv (Bearer token, SSE)
    R->>A: POST /webhooks/ring (HMAC-signed)
    A-->>T: card · info · "Someone is at the front door"
    B->>A: POST /webhooks/bee (HMAC-signed, consent required)
    A->>E: score the visitor's words (transcript held in memory only)
    E-->>A: SCAM · risk 65 · warning signs
    A-->>T: card · alert · warning signs reworded for a visitor
```

A **visit** opens on a doorbell or motion event and lasts 2 minutes. Wearable context only counts inside an open visit. The visit state machine (`VISIT_TRANSITIONS`) allows `OPEN → ASSESSED → ASSESSED … → CLOSED`. A new doorbell closes the old visit. If later speech is benign, the riskier earlier assessment still stands.

## Decisions (2026-10-04)

| Topic | Decision | Why |
|---|---|---|
| Who judges threats | **The deterministic engine.** Bedrock/Strands may at most phrase text, and isn't wired in. | Keeps `no_llm_verdicts`. The full chain runs in about 1 ms, far inside the 200 ms target. |
| Wearable audio | **Text only**, refused unless `consent.all_parties_consented` is `true`. Never stored, logged or shown; cards carry the engine's warning-sign sentences, not what was said. The wearer's own speech is ignored. | Recording visitors can require everyone's consent (two-party-consent states). |
| Inbound auth | HMAC-SHA256 over `"<timestamp>." + body` in `X-Aegis-Signature: v1=…`, 5-minute clock skew, `event_id` replay rejection (409), 16 KiB cap, 5 s body deadline. | Unsigned webhooks would let anyone push fake "alerts" (for example a callback number) to an older adult's TV. |
| Display auth | `Authorization: Bearer <AEGIS_DISPLAY_TOKEN>` on the SSE stream; at most 8 displays; slow displays drop their oldest cards. | Door activity is sensitive ("nobody's home"). |
| Card buttons | Only `ask_alexa` and `dismiss`. **No button blocks, reports or approves anything**; actions still need the spoken read-back and "yes". | Keeps the permission layer intact. |
| MCP tools | Unchanged. Still exactly the five agreed tools. | `[tool.aegis.mcp_server] tools` |

## Running it

```bash
export AEGIS_WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_DISPLAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
aegis-server                                         # mounts /webhooks/ring, /webhooks/bee, /events/firetv
python scripts/simulate_ecosystem_event.py --scenario scam     # or: --scenario benign
```

The endpoints are mounted **only when both secrets are set**, each at least 32 characters. Otherwise they return 404.

Sample simulator output:

```
OK  Ring doorbell press                          HTTP 202     1.4 ms
OK  Bee context -> assessment -> Fire TV card    HTTP 202     1.1 ms
    Fire TV card [info   ] Someone is at the front door
    Fire TV card [alert  ] Warning: this visitor sounds like a scam  (SCAM, risk 65)
        - The visitor wants payment by gift card, wire transfer or cryptocurrency. Real agencies never ask for that.
        - The visitor asks you to keep this secret. Scammers do that so your family can't warn you.
        - The visitor is rushing you. Scammers want you to act before you can check.
```

## Schemas

Defined in `server/ecosystem/schemas.py` (JSON Schema 2020-12, closed objects):
- `aegis.ring.event/v1`: `event_type` (`doorbell_press` or `motion`), `device.location`, `occurred_at` (with a time zone).
- `aegis.wearable.context/v1`: `consent`, `speaker` (`other`, `wearer` or `unknown`), `language` (`en`), and `transcript` (1–2000 characters, no audio).
- `aegis.firetv.card/v1`: `severity` (`info`, `caution` or `alert`), `title`, `body`, `evidence` (at most 3), `threat` (or null), `buttons`, and `simulated: true`.

## Not done (needs real integrations)

- Bridges from real Ring, Bee and Fire TV APIs, if and when those vendors offer them.
- A Fire TV (Vega / Fire OS) app that subscribes to `/events/firetv` and renders the card.
- Persistence: visits, seen event ids and subscribers live in memory, like the approval ledger.
