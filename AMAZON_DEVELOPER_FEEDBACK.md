# Amazon Developer Product Feedback: Aegis (Alexa+ track)

**Project:** Aegis: Voice-First Scam Defense on Alexa+, for the Alexa+ track of the Amazon Developer Hackathon "Build, Ship, Shape"
**Date:** 2026-10-05, updated 2026-10-07 with the outcome of our Alexa+ access request
**Format:** the required 5-question framework, answered for **every tool, API and SDK this submission uses**
**Evidence and reproduction steps:** [`docs/developer_feedback.md`](docs/developer_feedback.md)

| Tag | Meaning |
|---|---|
| **[Docs]** | Read in Amazon's published documentation (fetched through the Amazon Devices Builder Tools MCP server). |
| **[Reproduced]** | Reproduced against our own running server, with a regression test. |
| **[SDK-verified]** | Read from installed package metadata or source. |
| **[Experienced]** | Happened to us while onboarding, with dates. |

| Tool | How we used it |
|---|---|
| **Alexa+ MCP Toolkit**, including the Alexa AI CLI setup | **Track tool.** A self-hosted MCP server built to the spec: 336 tests, 23 HTTP smoke checks, and an add-on manifest and assets ready to deploy. We completed the CLI setup up to role assumption, which is preview-gated, so the demo runs through our own web MCP client. |
| **MCP Python SDK** (`mcp==2.2.0`) | The protocol layer under the server |
| **Amazon Devices Builder Tools MCP server** | Reading Amazon's docs from inside our coding agent, all through the build |

---

## 1. Alexa+ MCP Toolkit and Streamable HTTP (track tool)

**1. What we used it for.**
Aegis is a self-hosted MCP server that Alexa+ calls over Streamable HTTP. It implements MCP 2025-11-25 and also accepts 2025-03-26. It has five tools (list, check, explain, block and report voicemails), and blocking and reporting go through a propose → approve → execute gate with single-use approval codes. We built it to the QuickStart, the Client and App Lifecycle page, the design guide and the Functional Requirements, and followed *Set Up Your Development Environment* to the point where access is granted. The MCP Toolkit turned out to be in preview for select partners, so we demo with the **Aegis Voice Simulator**: a web page served by our server that is a real MCP client (`initialize`, `tools/list`, `tools/call` over Streamable HTTP), as the hackathon FAQ recommends.

**2. What worked well.**
- **Plain MCP, no proprietary envelope.** Nothing in the server is Alexa-specific, so any MCP client can drive it. **[Reproduced]**
- **Certification criteria precise enough to test.** Functional Requirements §2 (errors), §9 (voice-only) and §13 (tool validation) turned directly into tests: one next step per error, at most 5 options, no jargon in speech, and every result checked against its `outputSchema`. **[Docs]**
- **"Declare only what you honor"** (design guide) led to closed schemas: `additionalProperties: false` everywhere. **[Docs]**
- **Support answered quickly and clearly.** Our case about the `AccessDenied` error got a reply in two days that explained the cause (phased preview access) and confirmed our IAM setup wasn't the problem. **[Experienced]**

**3. What needs work.**
- **Preview access is stated only on the docs home page. [Docs] [Experienced]** The home page says *"At this time, Category SDK and MCP Toolkit are available to select partners only."* The pages a developer actually follows don't say it. The MCP Toolkit Overview says only *"The MCP Toolkit is available in the United States"*. The QuickStart and *Set Up Your Development Environment* have no notice, and setup step 2 shows only the successful `get-caller-identity` output. When the role assumption fails, the error is a bare `AccessDenied` with no hint that access is gated. We built the AWS setup exactly as documented, hit that error, and needed a support case to learn it was expected. The hackathon's own FAQ says the gap *"has caused some confusion"*.
- **Protocol version mismatch.** The hackathon rules require *"minimum acceptable version is 2025-11-25"*, and the Overview says Alexa+ supports 2025-11-25. But the lifecycle page's `initialize` sample sends **`2025-03-26`**, then uses `structuredContent` and `_meta.ui`, which only exist from 2025-06-18. We accept both versions and send `structuredContent` plus a text copy on every result. **[Docs]**
- **Header conflict.** The QuickStart requires `401` *without* `WWW-Authenticate`; the official MCP Python SDK's auth middleware sends that header. **[Docs] [SDK-verified]**
- **No device context.** `tools/call` carries no screen, locale or time-zone information, yet §3 and §9 require voice-only adaptation. Every response has to be written for an Echo Dot. **[Docs]**
- **A requirement with no mechanism.** §2 requires a "still working" message for slow tools, but stateless JSON responses have no channel for one, and `_meta.ui.invoking` is documented only for MCP Apps. **[Docs]**
- **No client contract.** Undocumented: the headers Alexa+ sends, request id types, JSON-RPC batching under 2025-03-26, timeouts and retries (which matter with single-use codes), and egress IP ranges. **[Docs]**
- **Doc defects.** The QuickStart's "MCP Apps" link points to the transports page. The docs home page's MCP Toolkit "Supported Capabilities" link is `.add-ons/mcp-toolkit-supported-capabilities.html` (missing a slash), which returns **404**. "Security and data policies" says details *"will be published in a future revision"*. **[Docs] [Reproduced]**
- **Speech normalization is left to every developer.** "I.R.S." and "i r s" missed our exact-match lookup until we added acronym collapsing and NFKC normalization. **[Reproduced]**

**4. Onboarding (zero to "hello world").**
- **Server side was fast.** QuickStart to a passing `initialize` at 2025-11-25 and 2025-03-26 from our own client took hours.
- **The Alexa AI CLI is the heavy part. [Docs]** For an MCP add-on that Amazon never hosts, setup still requires:
  - an AWS account, and an IAM user that assumes an **Amazon-owned role** (`arn:aws:iam::372468808636:role/AddOn3PDeveloperToolsRead`);
  - Git configured for CodeCommit;
  - npm pointed at a **private CodeArtifact registry** whose token **expires every 12 hours**.

  The page also refers to *"the AWS account that you provided to the Alexa Solutions Architect"*, without saying how a developer who has no Solutions Architect gets one.
- **What happened when we tried. [Experienced]**
  - **2026-10-05:** The Alexa developer console showed **My Add-ons** and the **Simulator**, which suggested we had access. We created an AWS account, an IAM user with the documented inline policy, and both CLI profiles, exactly as written. `aws sts get-caller-identity --profile alexa-ai` returned `AccessDenied`. We filed a support case the same day.
  - **2026-10-07:** Support replied that the error *"is expected, the MCP Toolkit is currently in Preview, and access is being granted to partners in phases"*, *"rather than an issue with your IAM configuration"*, and closed the case until access opens.
  - **Cost:** the AWS account, IAM and CLI setup, then two days of waiting, before learning that the path wasn't open to us. One sentence at the top of the setup page would have saved all of it.
  - **Outcome:** we never saw a real Alexa+ request. Everything we say about client behavior below comes from the docs and our own tests, not from live traffic.
- **No headless test path.** There's no headless Alexa+ client or conformance test for CI. We wrote 23 raw-HTTP smoke checks by guessing the client's behavior, and then built our own web MCP client to demo with.
- **Tunnel advice is one line** ("such as cloudflared"). Our DNS-rebinding Host check answered **421** to tunnelled traffic until we set `--http-host-header`.

**5. Would we build with it again?**
**Yes.** Standard MCP is the right bet: one server, any client, nothing Alexa-specific in the core. That's also why the preview gate didn't sink us: our server works unchanged with our own client and will work with Alexa+ once access opens. A preview notice on every setup page, a public `npm` package for the CLI, a client-contract page and a headless conformance test would turn a good platform into an easy one.

---

## 2. MCP Python SDK (`mcp==2.2.0`)

**1. What we used it for.**
The protocol layer: the low-level `Server`, the stateless Streamable HTTP transport, and JSON responses. We used the low-level API so the published tool schemas could match our spec exactly.

**2. What worked well.**
- Handshake negotiation across four protocol versions, out of the box.
- `streamable_http_app(stateless_http=True, json_response=True)` gave us a working endpoint in a few lines.
- Its in-process `Client` made end-to-end tests easy.

**3. What needs work. [Reproduced]** Each item below has a regression test in our repo:
- **Silent drop:** a request whose `id` is null, a float, a bool, an array or an object is treated as a notification and gets **`202` with no body**, so the client waits forever.
- **Wrong error channel:** `tools/call` with non-object `arguments` gets a JSON-RPC `-32602`, while the spec says input errors should come back as `isError` results the model can correct.
- **Stream in stateless mode:** `GET /mcp` still opens a long-lived stream.
- **No body deadline:** a stalled request body holds the connection open indefinitely.
- **Log noise:** each mid-body disconnect logs a full traceback at ERROR level.
- **Lost response:** behind uvicorn's h11 protocol, a client that half-closes after its request gets **no response even though the tool ran**. An approval can execute while its confirmation is lost.

**4. Onboarding (zero to "hello world").**
`pip install mcp`, a server, then a passing `initialize` within an hour. The friction came later: the version-2 API differs from most published examples (which use `FastMCP`), so we read the SDK source to find `Server(on_list_tools=…, on_call_tool=…)`.

**5. Would we build with it again?**
**Yes.** The fixes we needed are small. We'd ship them as a hardening layer by default: body deadline, id screening and half-close handling.

---

## 3. Amazon Devices Builder Tools MCP server

**1. What we used it for.**
Reading the Alexa+ add-on documentation from inside our coding agent, all through the build.

**2. What worked well.**
Official docs in context. It's how we found the protocol mismatch, the CLI's setup requirements, and the preview-only notice that appears on the docs home page but not on the setup pages.

**3. What needs work. [Reproduced]**
- **A mandatory first call:** every search failed with `PROJECT_CONTEXT_REQUIRED` until `set_project_context` was called, even with an empty config.
- **Oversized output:** `list_documents` returned **111,085 characters on one line**.
- **Navigation chrome:** every page includes about 65 navigation links before the content.
- **A filter that doesn't fit:** the required `device_os` filter only offers `vega_os` and `fire_os`; Alexa+ add-ons have no device OS.
- **Weak relevance:** a search for "event schema" returned unrelated Smart Home and skill-event docs.
- **Locale-less URLs refused:** `read_document` answers `Invalid URL` for `https://developer.amazon.com/docs/alexaplus/add-ons/home.html` and only accepts the `/en-US/docs/…` form, although the tool's own description gives a locale-less URL as its example.

**4. Onboarding (zero to "hello world").**
One `.mcp.json` entry (`npx -y @amazon-devices/amazon-devices-buildertools-mcp@latest`). The first searches failed until `set_project_context({})` was called.

**5. Would we build with it again?**
**Yes.** An Alexa+ filter and pages without the navigation would make it indispensable.

---

## Top asks, in priority order

1. **Say who can use it, on every page they'll follow.** Put the preview notice on the MCP Toolkit Overview, the QuickStart and *Set Up Your Development Environment*, and turn the `AccessDenied` at setup step 2 into a documented "you don't have preview access yet; here's how to ask" message.
2. **Fix or document the protocol version.** Make the documented handshake match the 2025-11-25 minimum.
3. **Publish an Alexa+ MCP client contract:** headers, id types, batching, timeouts and retries, egress IPs.
4. **Pass device and locale context** in `tools/call` `_meta`.
5. **Simplify CLI onboarding for MCP add-ons:** a public `npm` package, no AWS role assumption, and a documented access-request path.
6. **Ship a headless conformance test** for CI, plus a tunnel blueprint for local development.
