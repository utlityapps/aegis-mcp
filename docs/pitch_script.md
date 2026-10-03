# Aegis: 90-second submission video script

**Runtime:** 1:30. The submission allows up to 3:00; see `[tool.aegis.track]` in `pyproject.toml`.
**Format:** public, English, YouTube or Vimeo. 16:9, captions burned in, because judges often watch muted.
**Pace:** about 150 spoken words per minute. Word counts are given per segment so you can check timing before recording.

| # | Segment | Time | Spoken words (budget) |
|---|---|---|---|
| 1 | The problem | 0:00–0:15 | VO 34 (~37) |
| 2 | Live solution on Alexa+ | 0:15–1:00 | VO 8 + user 16 + Alexa 91 |
| 3 | AWS infrastructure and extensibility | 1:00–1:20 | VO 50 (~50) |
| 4 | Developer feedback and impact | 1:20–1:30 | VO 24 (~25) |

**Legend:** **VO** = narrator. **USER** = the person speaking to Alexa. **ALEXA** = live device or simulator audio. *Italics* = on-screen text.

---

## 1. The problem (0:00–0:15)

| Time | Visual / on-screen | Audio |
|---|---|---|
| 0:00–0:05 | Close-up: a phone on a kitchen table buzzing, then the voicemail icon. Slow push-in. | **VO:** "Phone scammers don't need to fool everyone. Just one trusting person, once." |
| 0:05–0:11 | Three transcript lines type on, one after another, each highlighted red: *"There is a warrant for your arrest"*, *"pay immediately with Google Play gift cards"*, *"Do not tell anyone about this call"*. Small caption: *(scripted demo voicemail)* | **VO:** "A fake IRS agent. A 'grandchild' in jail. They push hard and fast:" |
| 0:11–0:15 | Hard cut to black. Title: ***Aegis**: scam defense that speaks first.* | **VO:** "pay now, by gift card, and don't tell anyone." |

> The three red lines are verbatim from fixture `vm-001.json`, a scripted voicemail rather than a real call. Keep the "(scripted demo voicemail)" caption.

---

## 2. Live solution on Alexa+ (0:15–1:00)

Record this in the Alexa web simulator or on an Echo device, with the Aegis server log in a split screen on the right. Alexa's lines below are the exact `say` text the server returns today (captured from `server/tools.py`).

| Time | Visual / on-screen | Audio |
|---|---|---|
| 0:15–0:19 | Split screen. Left: Alexa simulator or Echo. Right: dark terminal running `aegis-server`. | **VO:** "Here's Aegis on Alexa+. One sentence, no typing." |
| 0:19–0:23 | Left: speech bubble appears. | **USER:** "Alexa, ask Aegis to check the voicemail I just got from the IRS." |
| 0:23–0:36 | Right: log line appears, `"tool": "check_voicemail" … "status": "checked"`. Left: a red **SCAM** badge. Lower third: *Verdict from 12 fixed rules. No AI guessing.* | **ALEXA:** "This message looks like a scam. The caller wants payment by gift card, wire transfer or cryptocurrency. Real agencies never ask for that. Would you like me to block this number or report it?" |
| 0:36–0:38 | Speech bubble. | **USER:** "Block them." |
| 0:38–0:48 | Right: log line `"tool": "block_number", "mode": "stage", "status": "staged"`. Lower third: *Staged, not done. Needs your "yes". Single-use approval token, never spoken.* | **ALEXA:** "I can block calls from 2 0 2, 5 5 5, 0 1 4 7. This is the caller whose message looked like a scam. Should I go ahead?" |
| 0:48–0:49 | Speech bubble. | **USER:** "Yes." |
| 0:49–1:00 | Right: log line `"mode": "resolve", "status": "executed"`, then the receipt JSON with `"simulated": true` highlighted in yellow. Lower third: *Practice run. Honestly labeled.* | **ALEXA:** "Done. I've blocked calls from 2 0 2, 5 5 5, 0 1 4 7. This is a practice version of Aegis, so no real block was made." |

**If the segment runs long:**
- Speed up only the gaps between turns, never Alexa's voice.
- If you still need time, cut the narrator's opening line (0:15–0:19), not any step of the stage → yes → execute flow. That flow *is* the pitch.

---

## 3. AWS infrastructure and extensibility (1:00–1:20)

| Time | Visual / on-screen | Audio |
|---|---|---|
| 1:00–1:07 | The README's Mermaid topology diagram, zooming from Alexa+ → Cloudflare Tunnel → `RequestGuard` → `AegisTools` → engine and fixtures. | **VO:** "Under the hood: a self-hosted MCP server over Streamable HTTP, with a deterministic engine." |
| 1:07–1:14 | Pan to the side box: Strands agent → Amazon Bedrock. Overlay the `ApprovalGate` code snippet from `docs/aws_bedrock_integration.md` §3.3, with `cancel_tool` highlighted. Badge: *Interop-tested with Strands.* | **VO:** "The same five tools plug into Amazon Bedrock through the Strands SDK, with an approval gate the model can't skip." |
| 1:14–1:20 | The AWS topology from the Bedrock doc: ALB → EC2 → DynamoDB. Badge on the AWS boxes: ***Designed. Next to deploy.*** | **VO:** "Next: EC2 behind a load balancer, and DynamoDB so each approval can be used exactly once." |

> Keep the "Designed. Next to deploy." badge. Only the Strands-to-server interop was tested; Bedrock calls, EC2 and DynamoDB aren't built yet (see `docs/aws_bedrock_integration.md`).

---

## 4. Developer feedback and future impact (1:20–1:30)

| Time | Visual / on-screen | Audio |
|---|---|---|
| 1:20–1:26 | Title card: *Feedback to Amazon*, with two bullets: *Alexa+ handshake sends MCP `2025-03-26`; docs say `2025-11-25`* and *Tools can't tell an Echo Dot from an Echo Show*. Small caption: `docs/developer_feedback.md` | **VO:** "Our brief to Amazon: fix the Alexa+ protocol mismatch, and tell tools when there's no screen." |
| 1:26–1:30 | End card: **Aegis** logo, repo URL, *Built for the Alexa+ track.* | **VO:** "Aegis. Protection that speaks plainly, and asks first." |

---

## Recording checklist

- [ ] **Record the real thing.** Segment 2 must be a real Alexa+ session. Alexa+ composes its own final wording from our `say` text, so the audio may differ slightly from the captions above. If it does, caption what Alexa actually says.
- [ ] **If the add-on isn't live by recording day,** record the same flow with `curl` or the smoke test against the server, and label it on screen: *Simulated client. Alexa+ connection pending.* Don't present a mock as Alexa.
- [ ] **Agent Skills:** mention Amazon's Add-on Agent Skill only if you actually onboarded with it. This script doesn't claim it.
- [ ] **Fresh state:** restart `aegis-server` right before each take. Approvals live in memory, so a repeat take would otherwise say "already blocked".
- [ ] **Run the tests first:** `pytest tests/ -q` and `python server/smoke_test.py` should be green, so an end card can say *163 tests · 22 smoke checks passing* if there's room.
- [ ] **No tokens on screen.** In the terminal pane, crop or blur the `approval_token` value if `structuredContent` is shown. The server logs never print it.
- [ ] **Captions on**, with the voiceover mixed above the music and Alexa's audio unducked.
- [ ] **Statistics:** none are used. If you add one to segment 1, cite a primary source on screen (for example the FBI IC3 Elder Fraud Report) and check the figure first.
