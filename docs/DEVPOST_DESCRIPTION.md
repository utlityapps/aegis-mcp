# Devpost submission text: Aegis

Copy the sections below into the matching Devpost fields.

---

## Project name
**Aegis: Voice-First Scam Defense on Alexa+**

## Tagline
Ask Alexa if a voicemail is a scam. Aegis explains why in plain words, and never acts without your "yes".

## Track and mini challenges
- **Track:** Alexa+ (self-hosted MCP server, Streamable HTTP, MCP spec 2025-11-25)
- **Mini challenges:** none

---

## What it does

Phone scams aimed at older adults are built for speed and secrecy: a fake "IRS agent" threatens arrest, demands gift cards, and says not to tell anyone. Aegis gives the person on the other end of that voicemail a second opinion in one sentence:

> "Check the voicemail I just got from the IRS."
> *"This message looks like a scam. The caller wants payment by gift card, wire transfer or cryptocurrency. Real agencies never ask for that. Would you like me to block this number or report it?"*

Aegis can:
- **Check** a voicemail: SCAM, SUSPICIOUS or LEGITIMATE, with the top warning signs in plain words.
- **Explain** every warning sign, a few at a time ("tell me more").
- **Find** the right voicemail from how people describe callers: "the IRS", "I.R.S.", "the bank", "my grandson", or part of a phone number.
- **Block or report** the caller, but only after reading back exactly what it will do and hearing a clear "yes".

## How it works

- **A self-hosted Alexa+ MCP server.** Python, on the official MCP SDK, serving five tools over stateless Streamable HTTP. It implements MCP 2025-11-25, and also accepts the 2025-03-26 handshake shown in Amazon's Alexa+ lifecycle docs.
- **Deterministic verdicts, no AI guessing.** Twelve fixed, weighted heuristics (gift-card payment, arrest threats, secrecy requests, government impersonation and more) score each transcript. The engine uses only Python's standard library, with no network and no LLM, and tests enforce that. Alexa+ handles the conversation; Aegis's rules decide the risk.
- **Nothing happens without a "yes".** Block and report are two-step: the first call *stages* the action and returns a read-back plus a single-use approval code; only an explicit "yes" executes it. Codes expire after 10 minutes, are stored only as hashes, and can't be reused, and the block tool can't approve a staged report.
- **Built for voice and for seniors.** Every result carries a short `say` sentence written to be read aloud, with no IDs, jargon or screen references and at most five options. That follows the Alexa+ Functional Requirements §2 and §9, which we turned into tests.
- **Hardened for a public endpoint:**
  - validation in two places (typed parsing, then the published input schema), with an output-schema check on every result;
  - body-size and time limits, and a pre-screen for malformed JSON-RPC;
  - fixes for several failure modes we reproduced in the MCP SDK stack: silent hangs on bad request ids, lost responses on half-closed connections, and stalled bodies;
  - Unicode normalization against look-alike characters, screening for prompt injection in caller names, and PII-redacted logs;
  - CloudWatch-compatible metrics and OpenTelemetry spans.
- **A voice simulator that is a real MCP client.** Alexa+'s add-on developer tools are preview-only for select partners, so, as the hackathon FAQ describes, we demo through our own web page. The server serves it at `/simulator`. It sends `initialize`, `tools/list` and `tools/call` over Streamable HTTP like Alexa+ would, takes speech through the browser's microphone, reads Aegis's replies aloud, and shows every JSON-RPC message live beside the conversation. A small phrase matcher stands in for Alexa+'s language model when choosing a tool; every word, verdict and action comes from the server.
- **Tested:** 336 tests, including the full demo conversation driven through the simulator's own client against a live server at both protocol versions, plus 23 end-to-end HTTP smoke checks.

## Honest limitations
- **It's a practice version.** It checks 8 scripted sample voicemails (5 scams, 3 legitimate), and blocking and reporting are simulated. Alexa says *"no real block was made"* every time.
- **English only.** Pending approvals live in memory.
- **The demo client stands in for Alexa+.** The add-on kit (manifest, icons, privacy and terms pages) is ready, but deploying to Alexa+ needs preview access that hackathon entrants can't get.
- **The server has no authentication.** This is acceptable only because every action is simulated.

## Built during the hackathon
Everything in the repository was built during the hackathon window, from the protocol spec to the engine, the server, the voice simulator, the tests, the add-on kit and the documentation. The git history starts within the window.

## Product feedback
Our 5-question feedback for each tool used (the Alexa+ MCP Toolkit, the MCP Python SDK and the Amazon Devices Builder Tools MCP server) is in [`AMAZON_DEVELOPER_FEEDBACK.md`](../AMAZON_DEVELOPER_FEEDBACK.md). The headlines:
- Alexa+ docs promise MCP 2025-11-25, but the documented handshake sends 2025-03-26.
- Tools get no device context, so they can't adapt for screenless Echo Dots.
- The Alexa AI CLI requires AWS role assumption and a private npm registry, even for a self-hosted MCP add-on.

## Try it
- **Repository:** <https://github.com/utlityapps/aegis-mcp> (MIT)
- **Run locally (about 2 minutes, no accounts or keys):** `pip install -e ".[dev]"`, then `aegis-server`, then open <http://127.0.0.1:8000> and say *"Check the voicemail I just got from the IRS."* Details in the README Quickstart.
- **Deploy to Alexa+ (needs preview access):** [`docs/ALEXA_DEPLOY.md`](ALEXA_DEPLOY.md)
