# Recording guide: the 2:45 Alexa+ pitch, step by step

This maps every line of [`PITCH_SCRIPT.md`](PITCH_SCRIPT.md) to what you do, what you say and what should be on screen. The demo runs in the **Aegis Voice Simulator**, our web MCP client. Alexa+'s own developer tools are preview-only, and the hackathon FAQ names this as the way to demo.

## 0. Before each take (about 2 minutes)

```
┌────────────────────────────────────────────────────────────────┐
│ SAFARI (or Chrome), full screen: http://127.0.0.1:8766         │
│ ┌────────────────────────────┬───────────────────────────────┐ │
│ │ Conversation + microphone  │ MCP traffic (live JSON-RPC)   │ │
│ └────────────────────────────┴───────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
```

| Step | Do this | You should see |
|---|---|---|
| **Reset Aegis** | `cd ~/Downloads/Aegis && scripts/demo_down.sh && scripts/demo_up.sh` | **AEGIS READY** |
| **Open the simulator** | Go to <http://127.0.0.1:8766>, or reload the tab if it's already open. Press ⌘+ once or twice so the text reads well on video. | *Connected to Aegis 0.1.0 · MCP 2025-11-25 · 5 tools* |
| **Check the microphone** | Click 🎤 once and allow the microphone if the browser asks. Then click **New conversation** so the test doesn't show. | The light bar pulses while listening |
| **Sound** | Leave **Read replies aloud** ticked, turn the Mac's volume up, and record system audio, so viewers hear the replies. | |
| **QuickTime** | File → New Screen Recording → Options → Microphone → record the entire screen. QuickTime doesn't capture system audio by itself; if the replies don't come through, use a tool that does (for example OBS) or add the voice in editing. | |

> **Reset before every take.** Each take blocks the IRS number. A second run would answer *"already blocked"*.

---

## 1. The problem (0:00–0:30): B-roll and voiceover

| | |
|---|---|
| **ACTION** | No live step (phone and title B-roll, added in editing). Optional on-screen source for the red lines: `.venv/bin/python -c "import json;print(json.load(open('fixtures/voicemails/vm-001.json'))['transcript'])"` |
| **SCRIPT** | *"Every day, someone's mom or grandfather gets a voicemail from the 'IRS': there's a warrant, pay today, with gift cards. These scams don't need to fool everyone. Just one trusting person, once, alone, and in a hurry. They're built to stop you from checking with anyone. By the time the family finds out, the money is gone."* |
| **VISUAL** | The scripted IRS voicemail, captioned *(scripted demo voicemail)*, then the black title lines |

## 2. Who it's for and the solution (0:30–1:00): B-roll and voiceover

| | |
|---|---|
| **ACTION** | No live step. Title card: **Aegis: Voice-First Scam Defense on Alexa+**. |
| **SCRIPT** | *"Aegis is for older adults living independently, and the families who worry about them. It's named for the shield of Greek myth: protection that's always there. Just ask Alexa. Aegis checks the voicemail, says whether it looks like a scam, and explains the warning signs in plain words. And it never blocks or reports anything without a clear yes."* |
| **VISUAL** | Living-room shot with an Echo, then the captions and the shield icon (`alexa/assets/icon-241x241.png`) |

## 3. Track tool (1:00–1:15)

| | |
|---|---|
| **ACTION** | `open "https://github.com/utlityapps/aegis-mcp#built-with"` |
| **SCRIPT** | *"Our track tool: a self-hosted Alexa+ MCP server over Streamable HTTP, implementing MCP 2025-11-25."* |
| **VISUAL** | The **Built With** table, with the ⭐ track-tool row on top |

| | |
|---|---|
| **ACTION** | `open "https://github.com/utlityapps/aegis-mcp#system-topology"` |
| **SCRIPT** | *"The client handles the conversation. Aegis's fixed rules decide the risk."* |
| **VISUAL** | The topology diagram: voice simulator (and, dotted, Alexa+) → Aegis server → engine |

## 4. Working demo in the voice simulator (1:15–2:15)

Switch to the simulator. Keep both panels in view: the conversation on the left, the MCP traffic on the right.

| | |
|---|---|
| **ACTION** | Show the simulator, with the top bar reading *Connected* |
| **SCRIPT** | *"This is a real MCP client, standing in for Alexa+, calling our server live."* |
| **VISUAL** | Caption (in editing): *Live: web MCP client → Aegis MCP server. Alexa+ developer tools are preview-only.* |

| | |
|---|---|
| **SAY** (click 🎤 first; or click the phrase under **Try saying**) | *"Check the voicemail I just got from the IRS."* |
| **SCRIPT** | Nothing; let the reply play. |
| **VISUAL** | A red **SCAM · risk 100/100** label, then *"This message looks like a scam. The caller wants payment by gift card…"*. Traffic: `tools/call · check_voicemail`. Caption: *Verdict from 12 fixed rules.* |

| | |
|---|---|
| **SAY** | *"Why does it look like a scam?"* |
| **SCRIPT** | *"Every warning sign comes in words a grandparent can act on."* |
| **VISUAL** | Warning signs: gift cards, threat of arrest, government impersonation. Traffic: `tools/call · explain_red_flags` |

| | |
|---|---|
| **SAY** | *"Block them."* |
| **SCRIPT** | Nothing; let the read-back play. |
| **VISUAL** | *"I can block calls from 2 0 2, 5 5 5, 0 1 4 7… Should I go ahead?"* Traffic: `block_number`; open the response and point out `"status": "staged"` and the `approval_token`. Caption: *Staged, not done. A single-use approval code, never spoken.* |

| | |
|---|---|
| **SAY** | *"Yes."* |
| **SCRIPT** | Once the reply plays: *"Nothing happens until she says yes. And Aegis tells her when it's only practice."* |
| **VISUAL** | *"Done… This is a practice version of Aegis, so no real block was made."* Traffic: the request carries `"decision": "approve"`; the response shows `"status": "executed"` and `"simulated": true` |

## 5. Developer feedback and close (2:15–2:45)

| | |
|---|---|
| **ACTION** | `open https://github.com/utlityapps/aegis-mcp/blob/main/AMAZON_DEVELOPER_FEEDBACK.md` |
| **SCRIPT** | *"We're sending Amazon's Alexa+ team direct feedback, in the five-question format, for every tool we used. The documented handshake asks for an older protocol than the docs promise, so we support both. And tools can't tell when there's no screen, which a voice-first product for seniors needs."* |
| **VISUAL** | Scroll through section 1 (Alexa+ MCP). Callouts: *handshake `2025-03-26` versus docs `2025-11-25`*; *no device context* |

| | |
|---|---|
| **ACTION** | `open https://github.com/utlityapps/aegis-mcp` |
| **SCRIPT** | *"Aegis. Protection that speaks plainly, and asks first."* |
| **VISUAL** | The repo home page. End card in editing. Stop recording (⌘⌃Esc). |

---

## If something goes wrong mid-take

| Symptom | Fix |
|---|---|
| Top bar says *Can't reach the Aegis server* | Run `scripts/demo_up.sh`, then press **Reconnect** |
| 🎤 is greyed out, or nothing happens when you speak | The browser can't do speech input. Use Safari or Chrome, allow the microphone, or click the **Try saying** phrases instead |
| No sound | Tick **Read replies aloud** and check the Mac's volume |
| A reply is tagged *Simulator* instead of *Alexa (Aegis)* | The phrase matcher didn't recognise what it heard. Say the scripted line again, or click it under **Try saying** |
| *"already blocked"* | Reset (step 0) and reload the page |
| GitHub pages show old content | Push first; the README and feedback doc must be on `main` |
