# Aegis on AWS: Bedrock (via Strands) and EC2 behind a load balancer

**Status:** Architecture design. The Aegis MCP server in `server/` is implemented and tested. The Strands agent, the DynamoDB ledger and the AWS deployment described here are **not implemented yet**.
**Date:** 2026-10-03
**Read with:** `docs/architecture.md` (protocol spec), `pyproject.toml` `[tool.aegis.*]` (product agreements), `CLAUDE.md`.

### What was verified while writing this

| Claim | How it was checked |
|---|---|
| Strands' MCP client can drive our server | `strands-agents==1.57.2` `MCPClient(url=...)` against a running `aegis-server`. It listed all 5 tools, called `check_voicemail` with `"I.R.S."`, staged a block, and received `isError` for a forged token. Zero server tracebacks. |
| Strands can't share our server's environment | `strands-agents==1.57.2` requires `mcp<2.2,>=1.23.0`. Our server pins `mcp==2.2.0`. With that pin, pip's resolver falls back to `strands-agents==0.0.1`, a placeholder release. |
| Strands API names used below | Checked against the installed 1.57.2 package: `MCPClient`, `BedrockModel(model_id=...)` (which uses `converse_stream`), `BeforeToolCallEvent.cancel_tool`, `HookProvider`, `S3SessionManager`. |
| §3.3 approval gate | The `ApprovalGate` code below was run against Strands' real `HookRegistry` and `BeforeToolCallEvent`. It cancels only `approve` when the person declines; staging and `reject` pass through. |
| uvicorn forwarded-header setting | `uvicorn/config.py` reads `FORWARDED_ALLOW_IPS` (default `127.0.0.1,::1`). |

**Not verified:** calls to Bedrock itself (no AWS credentials were used), the DynamoDB design, and the ALB/EC2 setup.

---

## 1. Why Bedrock, and what it may not do

Alexa+ is Aegis's production MCP client. A **Strands agent on Amazon Bedrock** is a second client of the same server. It does the job Alexa+ does: it understands the request, picks a tool and phrases the answer. It is useful as:
- a **reference harness**: scripted end-to-end conversations in CI, without an Echo device or the Alexa web simulator;
- a **text channel**, for example a caregiver console, that reuses the same tools and the same permission gate.

**Non-negotiables carried over from `[tool.aegis]`:**
1. **The model never produces a verdict** (`no_llm_verdicts = true`). Verdicts, scores and warning-sign sentences come only from `aegis/engine.py` through the tools. The agent's system prompt tells it to read back the tool's `say` text and nothing else about risk.
2. **Nothing executes without a human yes** (`[tool.aegis.permission_layer]`). In Alexa+, the person's spoken "yes" sits between the two `block_number`/`report_scam` calls. An unattended agent has no person in the loop. It could stage an action and immediately approve it with the token it just received. **The Strands harness must enforce the gate itself (§3.3)**; the server alone can't tell a model's yes from a person's.
3. **Actions stay simulated** (`actions_are_simulated = true`). Moving to AWS doesn't change that.

---

## 2. Topology

```
                         Internet
                            │
            Alexa+ MCP client (Streamable HTTP, JSON-RPC 2.0)
                            │ HTTPS :443
                 ┌──────────▼───────────┐      AWS WAF (rate-based rule)
                 │ Application Load     │◀──── attached to the ALB
                 │ Balancer (public)    │
                 │ ACM cert, TLS 1.2+   │
                 └──────────┬───────────┘
                            │ HTTP :8000  (VPC only; instance SG allows ALB SG only)
┌───────────────────────────▼────────────────────────────────────────────────────────┐
│ EC2 instance (private subnet, Amazon Linux 2023, Python 3.12)                       │
│                                                                                    │
│  aegis-server  (systemd, one uvicorn worker)                                       │
│   --host 0.0.0.0 --allow-remote-bind   AEGIS_ALLOWED_HOSTS=<public hostname>        │
│   RequestGuard → MCP SDK (stateless, JSON) → AegisTools → aegis/engine.py          │
│        │                                                                           │
│        └─ state: in-memory ActionLedger today ──▶ DynamoDB ledger (§4, proposed)   │
│                                                                                    │
│  aegis-agent  (separate venv: strands-agents + boto3, mcp<2.2)                     │
│   Strands Agent ── MCPClient(url="http://127.0.0.1:8000/mcp") ──┘ (loopback)       │
│        │  ApprovalGate hook (§3.3)                                                 │
│        ▼                                                                           │
└────────┼───────────────────────────────────────────────────────────────────────────┘
         │ bedrock-runtime ConverseStream (VPC interface endpoint, IAM role)
         ▼
   Amazon Bedrock          DynamoDB table `aegis-ledger`     SSM Parameter Store
   (model chosen by        (pending actions, receipts)       /aegis/cursor-key (SecureString)
    AEGIS_BEDROCK_MODEL_ID)                                  CloudWatch Logs (stderr via agent)
```

**Design choices:**
- **Two processes, two virtual environments.** The agent is a client that talks to the server over HTTP, as Alexa+ does. That keeps the `mcp` version conflict (Strands needs `<2.2`, the server pins `2.2.0`) from reaching the server. It also keeps `boto3` out of the server process.
- **The agent calls the server over loopback,** not through the ALB. Agent traffic never leaves the instance, and the public ALB serves only Alexa+.
- **Bedrock and DynamoDB are reached through VPC interface/gateway endpoints**, so the instance needs no NAT path for them.

---

## 3. Connecting the MCP tools to Bedrock with Strands

### 3.1 Packaging

`agent/` would be a new top-level directory with its own `requirements.txt`. It must not be added to `pyproject.toml` dependencies, because that would force one `mcp` version on both processes:

```
strands-agents==1.57.2   # requires mcp<2.2 — keep in its own venv
```

Pin the exact versions that were tested. Re-check the conflict when Strands supports `mcp>=2.2`.

### 3.2 Agent wiring

```python
import os

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

SYSTEM_PROMPT = (
    "You help an older adult check voicemails for scams using the Aegis tools. "
    "Never judge a voicemail yourself: verdicts and warning signs come only from tool results. "
    "Speak the tool's 'say' text plainly. Never read IDs or approval tokens aloud. "
    "To block or report, first call the tool with voicemail_id, read back its 'say', and wait for the person."
)

aegis = MCPClient(url=os.environ.get("AEGIS_MCP_URL", "http://127.0.0.1:8000/mcp"))
model = BedrockModel(
    model_id=os.environ["AEGIS_BEDROCK_MODEL_ID"],  # a model enabled in your account and region
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

with aegis:
    agent = Agent(
        model=model,
        tools=aegis.list_tools_sync(),
        system_prompt=SYSTEM_PROMPT,
        hooks=[ApprovalGate(confirm=ask_person)],  # §3.3
    )
    agent("Check the voicemail I just got from the IRS.")
```

- **The tool contract is unchanged.** Strands passes the server's `inputSchema` to the model as the tool spec and returns results that include `structuredContent`; both were verified. The server's validation, `isError` handling and output-schema check (`docs/architecture.md` §7–8) apply to Bedrock-driven calls exactly as they do to Alexa+.
- **Bedrock Guardrails are optional, not a substitute.** `BedrockModel` accepts `guardrail_id`/`guardrail_version`. They can filter the agent's wording, but verdicts and approvals stay with the server.

### 3.3 Human-in-the-loop approval gate (required)

The gate runs inside the agent process, before any approval reaches the server. It uses Strands' `BeforeToolCallEvent.cancel_tool`:

```python
from collections.abc import Callable

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

GATED_TOOLS = frozenset({"block_number", "report_scam"})


class ApprovalGate(HookProvider):
    """Let an approve/reject decision through only if a human gave it, in this turn, outside the model."""

    def __init__(self, confirm: Callable[[str], bool]) -> None:
        self._confirm = confirm  # e.g. console input, or a caregiver UI button

    def register_hooks(self, registry: HookRegistry, **_: object) -> None:
        registry.add_callback(BeforeToolCallEvent, self._check)

    def _check(self, event: BeforeToolCallEvent) -> None:
        tool = event.tool_use["name"]
        args = event.tool_use.get("input") or {}
        if tool not in GATED_TOOLS or args.get("decision") != "approve":
            return  # staging and rejecting need no gate
        if not self._confirm(f"Allow Aegis to {tool.replace('_', ' ')}?"):
            event.cancel_tool = "The person has not approved this. Ask them first."
```

- Staging (`voicemail_id` only) and `reject` pass through. **Only `approve` is gated,** matching propose → approve → execute.
- In CI, `confirm` is a scripted answer per scenario, so both the "yes" and the "no" paths are tested.
- The server-side rules (single-use tokens, matching action kind, expiry) still apply behind the gate. The gate adds the human; it doesn't replace them.

### 3.4 Conversation state

Conversation history belongs to the agent, not the MCP server (the server is stateless, `docs/architecture.md` §3.2). For multi-turn sessions that survive restarts, use Strands' `S3SessionManager(session_id=..., bucket=...)` with a bucket encrypted with SSE-KMS and a lifecycle rule that expires sessions. Session ids must not contain personal data.

---

## 4. Persisting state transitions

### 4.1 Why in-memory state breaks behind a load balancer

The permission layer currently lives in one process (`aegis/engine.py` `ActionLedger`; `in_memory_registries = true`). Three pieces of state are process-local:

| State | Where | What breaks with 2+ instances or a restart |
|---|---|---|
| Pending actions and token digests | `ActionLedger._entries` | A token staged on instance A is "unknown" on instance B, so the senior's "yes" is refused. Everything pending is lost on restart. |
| Block and report registries | `ActionLedger._blocked`, `_reported` | Duplicate detection (`already_done`) works only per instance. |
| Cursor HMAC key | `CursorCodec` (random at startup) | `next_cursor` from instance A is rejected by instance B as `invalid_input`. |

ALB sticky sessions are not a fix. They rely on cookies, and nothing says the Alexa+ client keeps them. **Until §4.2 is built, run exactly one instance** (Auto Scaling group min = max = 1).

### 4.2 Proposed DynamoDB ledger

One table, `aegis-ledger`, on-demand capacity, point-in-time recovery on, encrypted with an AWS-managed KMS key.

| Item | `pk` | `sk` | Attributes |
|---|---|---|---|
| Pending action | `ACTION#<action_id>` | `PENDING` | `kind`, `voicemail_id`, `caller_number`, `token_digest` (SHA-256, never the token), `created_at`, `expires_at`, `ttl` (epoch seconds) |
| Target lock | `TARGET#<kind>#<voicemail_id>` | `PENDING` | `action_id`: one pending action per kind + voicemail (§9.2 rotation rule) |
| Token index | `TOKEN#<token_digest>` | `ACTION` | `action_id`, `kind`, `voicemail_id`, `expires_at`, `ttl` |
| Block receipt | `BLOCK#<caller_number>` | `RECEIPT` | receipt fields, `simulated = true` |
| Report receipt | `REPORT#<voicemail_id>` | `RECEIPT` | receipt fields, `simulated = true` |

**How the state machine in `docs/architecture.md` §9.2 maps onto DynamoDB:**

| Transition | DynamoDB operation | Guarantee |
|---|---|---|
| stage (new) | `TransactWriteItems`: Put action, Put target lock with `attribute_not_exists(pk)`, Put token index | Two concurrent stages can't both create an action |
| stage (already done) | `GetItem` on `BLOCK#…`/`REPORT#…` first | Returns `already_done` and no token |
| stage (re-stage, token rotation) | `TransactWriteItems`: Delete old token index, Put new token index, Update action `token_digest` and `expires_at` with condition `token_digest = :old` | The old token stops working in the same commit |
| approve | `TransactWriteItems`: Delete token index with condition `kind = :kind AND expires_at > :now`, Delete action, Delete target lock, Put receipt with `attribute_not_exists(pk)` | **Single use is enforced by the database**: of two racing approvals, exactly one commits |
| reject | Same as approve, without the receipt | Drops the action (`reject_drops_action = true`) |
| expiry | Condition `expires_at > :now` on approve/reject. Cleanup is DynamoDB TTL on `ttl`. | TTL deletion can lag by up to about 48 h, so **correctness must never rely on TTL**; it only cleans up. |
| wrong kind / different voicemail | The condition fails, **and the item is untouched** | The token stays usable by the right tool (§9.2) |

To tell "expired" (`token_expired`) apart from "wrong kind" (`permission_denied`) after a failed condition, request the item back with `ReturnValuesOnConditionCheckFailure=ALL_OLD` and inspect it.

**Where the code goes:**
- `aegis/engine.py` stays stdlib-only and network-free (enforced by `tests/test_aegis.py`). It would gain a small `typing.Protocol` that `ActionLedger` already satisfies (`stage_action`, `approve_action`, `reject_action`, `completed`).
- A `DynamoLedger` implementing that protocol goes in `server/`, the only package allowed third-party imports. That adds a pinned `boto3`.
- `AegisTools` keeps the same interface. Its in-process `asyncio.Lock` stays for single-instance mode and isn't needed with DynamoDB.
- **The cursor key** moves to SSM Parameter Store (`/aegis/cursor-key`, SecureString), read once at startup, so every instance mints and accepts the same cursors.

**This is a product-agreement change.** `[tool.aegis.honest_limitations] in_memory_registries = true` would become false, and pending approvals would survive restarts. That's Ajayi's call, and `pyproject.toml` must be updated when it lands.

### 4.3 Latency budget

Alexa+ requires under 500 ms per round trip. Tool handlers currently take under 1 ms (server logs show `latency_ms` 0.02–0.45). A transactional DynamoDB write within the same region typically takes single-digit to low tens of milliseconds, which leaves the budget to the network path. Put the EC2 instance, table and ALB in one region and measure from the Alexa web simulator before committing.

---

## 5. Hosting: EC2 behind an Application Load Balancer

### 5.1 Load balancer
- **Listener:** HTTPS :443 with an ACM certificate and a TLS 1.2+ security policy. Redirect HTTP :80 to HTTPS.
- **Target group:** HTTP :8000, target type instance.
- **Health check:** `GET /mcp`, success code **405**. In stateless mode the server answers `GET /mcp` with 405 by design (§3.2), so the server needs no code change and no health endpoint that leaks information. A dedicated `/healthz` route is a reasonable later addition.
- **Idle timeout:** keep the default 60 s, above the server's 10 s body-read deadline and 5 s keep-alive.
- **AWS WAF:** a rate-based rule per source IP, plus AWS managed core rules. This is the main abuse control, because the server has no authentication (`auth = "none"`, decision Q2).
- **No stickiness** (§4.1).

### 5.2 Instance
- Amazon Linux 2023 in a private subnet with Python 3.12 (the project requires Python 3.12 or later). Install from a wheel built in CI.
- **systemd unit**, one process. The server runs a single uvicorn worker by design (`docs/architecture.md` §3.4):

```ini
[Service]
User=aegis
Environment=AEGIS_HOST=0.0.0.0
Environment=AEGIS_PORT=8000
Environment=AEGIS_ALLOW_REMOTE_BIND=1
Environment=AEGIS_ALLOWED_HOSTS=aegis.example.com
Environment=FORWARDED_ALLOW_IPS=10.0.0.0/16
ExecStart=/opt/aegis/venv/bin/aegis-server
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
PrivateTmp=true
```

- **`AEGIS_ALLOW_REMOTE_BIND=1` is required.** The server refuses a non-loopback bind without it, because it has no authentication. It's safe here only because the instance security group admits port 8000 **solely from the ALB's security group**.
- **`AEGIS_ALLOWED_HOSTS`** must list the public hostname Alexa+ calls. The ALB preserves the `Host` header, and DNS-rebinding protection otherwise answers 421.
- **`FORWARDED_ALLOW_IPS`** set to the VPC CIDR makes uvicorn log the real client IP from the ALB's `X-Forwarded-For` header. Trust only the VPC range.
- Ship stderr (one JSON line per tool call, with no tokens or transcripts, `docs/architecture.md` §10) to CloudWatch Logs with the CloudWatch agent.

### 5.3 Security groups

| Group | Inbound | Outbound |
|---|---|---|
| `aegis-alb` | 443 (and 80 for the redirect) from `0.0.0.0/0` | 8000 to `aegis-instance` |
| `aegis-instance` | 8000 from `aegis-alb` only. **No SSH**: use SSM Session Manager. | 443 to the VPC endpoints (Bedrock runtime, DynamoDB, SSM, CloudWatch Logs) |

---

## 6. IAM (least privilege)

The instance role is shared by `aegis-server` and `aegis-agent`. If the agent moves to its own host, split it into two roles.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AgentInvokesOneModel",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "arn:aws:bedrock:<region>::foundation-model/<model-id>"
    },
    {
      "Sid": "ServerLedger",
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                 "dynamodb:ConditionCheckItem"],
      "Resource": "arn:aws:dynamodb:<region>:<account-id>:table/aegis-ledger"
    },
    {
      "Sid": "ServerCursorKey",
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:<region>:<account-id>:parameter/aegis/cursor-key"
    },
    {
      "Sid": "AgentSessions",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::<session-bucket>", "arn:aws:s3:::<session-bucket>/aegis/*"]
    }
  ]
}
```

- `TransactWriteItems` is authorized through the per-item actions above, so it needs no separate permission.
- `BedrockModel` streams through ConverseStream, which needs `bedrock:InvokeModelWithResponseStream`. Scope it to the single model ARN; if you use a cross-region inference profile, scope it to that profile's ARN instead.
- **No `dynamodb:Scan`, `dynamodb:Query`, `dynamodb:DeleteTable` or wildcard resources.** No `iam:*`, and no SSH key on the instance.
- `SecureString` parameters encrypted with the AWS-managed `aws/ssm` key need no extra KMS permission. Add `kms:Decrypt` on the specific key only if you use a customer-managed key.

---

## 7. Risks and open decisions

| # | Item | Recommendation |
|---|---|---|
| R1 | **No authentication on a public endpoint** (decision Q2). Anyone who finds the URL can stage and approve simulated actions. | Acceptable while `actions_are_simulated = true`. Before any real blocking or reporting, add OAuth 2.1 (`CLAUDE.md`, "Auth"), the only option Alexa+ supports. |
| R2 | An unattended Bedrock agent could approve its own staged actions | §3.3 gate is mandatory. Add a CI scenario where `confirm` returns False and assert nothing executes. |
| R3 | `mcp` version split between server (2.2.0) and Strands (<2.2) | Keep separate venvs. Re-test interop on every Strands upgrade. |
| R4 | In-memory ledger forces a single instance | Run one instance until the DynamoDB ledger (§4.2) is approved and built. |
| R5 | Changing `in_memory_registries` changes a stated limitation | Needs Ajayi's sign-off, then update `pyproject.toml` and `CLAUDE.md`. |

### Build order
1. EC2 + ALB + WAF with the existing server, a single instance (§5). No code changes needed.
2. `agent/` with the Strands harness and the approval gate (§3), plus CI scenarios.
3. Cursor key from SSM. A small server change, needed before scaling past one instance.
4. DynamoDB ledger behind the engine protocol (§4.2), after sign-off on R5.
