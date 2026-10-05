# Recording guide: the 2:45 Alexa+ pitch, step by step

This maps every line of [`PITCH_SCRIPT.md`](PITCH_SCRIPT.md) to what you do, what you say and what should be on screen. **Prerequisite:** Aegis is deployed to the development stage and answers in the Alexa+ simulator ([`ALEXA_DEPLOY.md`](ALEXA_DEPLOY.md)).

## 0. Before each take (about 2 minutes)

```
┌──────────────────────────────────┬───────────────────────────────┐
│ BROWSER: Alexa+ web simulator    │ TERMINAL: live Aegis tool log │
│ Add-on: Aegis · Mode: Isolation  │ (big font: ⌘+ ×3)             │
│ Stage: development               │                               │
└──────────────────────────────────┴───────────────────────────────┘
```

| Step | Do this | You should see |
|---|---|---|
| **Reset Aegis** | `cd ~/Downloads/Aegis && scripts/demo_down.sh && scripts/demo_up.sh` | **AEGIS READY FOR ALEXA+**. The cloudflared tunnel can stay running in its own terminal. |
| **Start the log view** | `cd ~/Downloads/Aegis && clear && tail -n 0 -f logs/aegis-server.log \| grep --line-buffered '"event": "tools/call"'` | Nothing yet; one line per tool call during the demo |
| **Simulator** | Open <https://developer.amazon.com/alexa/console/ask/addons/simulator>, click **New Chat**, then set Add-on: Aegis · Mode: *Isolation* · Stage: *development* | An empty conversation |
| **QuickTime** | File → New Screen Recording → Options → Microphone → record the entire screen | |

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
| **SCRIPT** | *"Alexa+ handles the conversation. Aegis's fixed rules decide the risk."* |
| **VISUAL** | The topology diagram: Alexa+ → tunnel → Aegis server → engine |

## 4. Working demo on Alexa+ (1:15–2:15)

Switch to the simulator (left) and the log terminal (right).

| | |
|---|---|
| **ACTION** | Show the simulator with **Aegis** selected |
| **SCRIPT** | *"This is Alexa+, calling our server live."* |
| **VISUAL** | The simulator header shows Aegis · Isolation · development |

| | |
|---|---|
| **TYPE IN "ASK ALEXA"** (or say it to the Echo) | `Ask Aegis to check the voicemail I just got from the IRS` |
| **SCRIPT** | Nothing; let Alexa's reply play or show. |
| **VISUAL** | Alexa says it looks like a scam, because of the gift-card demand, and offers to block or report. Log: `"tool": "check_voicemail" … "status": "checked"`. Caption: *Verdict from 12 fixed rules.* |

| | |
|---|---|
| **TYPE** | `Why does it look like a scam?` |
| **SCRIPT** | *"Every warning sign comes in words a grandparent can act on."* |
| **VISUAL** | Warning signs: gift cards, threat of arrest, government impersonation. Log: `"tool": "explain_red_flags"` |

| | |
|---|---|
| **TYPE** | `Block them` |
| **SCRIPT** | Nothing; let the read-back show. |
| **VISUAL** | *"I can block calls from 2 0 2, 5 5 5, 0 1 4 7… Should I go ahead?"* Log: `"tool": "block_number", "mode": "stage", "status": "staged"`. Caption: *Staged, not done.* |

| | |
|---|---|
| **TYPE** | `Yes` |
| **SCRIPT** | Once the reply shows: *"Nothing happens until she says yes. And Aegis tells her when it's only practice."* |
| **VISUAL** | *"Done… This is a practice version of Aegis, so no real block was made."* Log: `"mode": "resolve", "status": "executed"` |

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
| Alexa answers itself instead of calling Aegis | Mode must be **Isolation**, and the Add-on menu must show **Aegis** |
| Nothing appears in the log terminal | The tunnel is down or its URL changed. Restart cloudflared, re-run `scripts/render_addon.py`, then `alexa-ai deploy` |
| *"already blocked"* | Reset (step 0) and click **New Chat** |
| Alexa's wording differs from the script | Expected: Alexa+ composes its own wording. Caption what it actually says. |
| GitHub pages show old content | Push first; the README and feedback doc must be on `main` |
