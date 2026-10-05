# Aegis: 2:45 executive pitch (Alexa+ track)

**Runtime:** 2:45. The rules require *"less than three (3) minutes"*, public, English, on YouTube or Vimeo.
**Must show:** Aegis *"functioning on the device for which it was built"*, meaning **Alexa+ calling the Aegis MCP server**, in the Alexa web simulator or on an Echo. Set that up first: [`ALEXA_DEPLOY.md`](ALEXA_DEPLOY.md).
**Format:** 16:9 with captions burned in. Pace: about 150 spoken words per minute; word counts are checked against that.
**Recording steps:** [`RECORDING_GUIDE.md`](RECORDING_GUIDE.md) maps every line to the exact action.

| # | Segment | Time | Spoken words |
|---|---|---|---|
| 1 | The problem | 0:00–0:30 | VO 57 |
| 2 | Who it's for and the solution | 0:30–1:00 | VO 59 |
| 3 | Track tool | 1:00–1:15 | VO 24 |
| 4 | Working demo on Alexa+ | 1:15–2:15 | VO 32 + user 22 + Alexa 91 (about 58 s if Alexa's replies play aloud) |
| 5 | Developer feedback and close | 2:15–2:45 | VO 55 |

**Legend:** **VO** = narrator. **USER** = typed into the simulator or spoken to an Echo. **ALEXA** = what appears or plays. *Italics* = on-screen text.

> **Alexa+ writes its own final wording** from Aegis's `say` text, so its replies may not match the lines below word for word. Caption what Alexa *actually* says. The lines below are Aegis's exact `say` text, for reference.

---

## 1. The problem (0:00–0:30)

| Time | Visual / on-screen | Audio |
|---|---|---|
| 0:00–0:10 | Close-up: a phone buzzing on a kitchen table, then a voicemail notification. A transcript types on, with three lines highlighted red: *"warrant for your arrest"*, *"Google Play gift cards"*, *"Do not tell anyone"*. Caption: *(scripted demo voicemail)* | **VO:** "Every day, someone's mom or grandfather gets a voicemail from the 'IRS': there's a warrant, pay today, with gift cards." |
| 0:10–0:20 | The same phone, unanswered. A notification badge counts up. | **VO:** "These scams don't need to fool everyone. Just one trusting person, once, alone, and in a hurry." |
| 0:20–0:30 | Hard cut to black. White text, one line at a time: *Pay now. · By gift card. · Don't tell anyone.* | **VO:** "They're built to stop you from checking with anyone. By the time the family finds out, the money is gone." |

## 2. Who it's for and the solution (0:30–1:00)

| Time | Visual / on-screen | Audio |
|---|---|---|
| 0:30–0:42 | An older woman in an armchair near an Echo. Title: ***Aegis**: Voice-First Scam Defense on Alexa+.* Subtitle: *Named for the shield of Greek myth.* | **VO:** "Aegis is for older adults living independently, and the families who worry about them. It's named for the shield of Greek myth: protection that's always there." |
| 0:42–0:54 | Three captions animate in: *Ask in one sentence · Plain-word warning signs · Fixed rules, not AI guesses.* | **VO:** "Just ask Alexa. Aegis checks the voicemail, says whether it looks like a scam, and explains the warning signs in plain words." |
| 0:54–1:00 | A shield with a check mark. Caption: *Never acts without a clear "yes".* | **VO:** "And it never blocks or reports anything without a clear yes." |

## 3. Track tool (1:00–1:15)

| Time | Visual / on-screen | Audio |
|---|---|---|
| 1:00–1:08 | The README **Built With** table on GitHub, with the ⭐ track-tool row highlighted. | **VO:** "Our track tool: a self-hosted Alexa+ MCP server over Streamable HTTP, implementing MCP 2025-11-25." |
| 1:08–1:15 | The README topology diagram: Alexa+ → tunnel → Aegis server → deterministic engine. | **VO:** "Alexa+ handles the conversation. Aegis's fixed rules decide the risk." |

## 4. Working demo on Alexa+ (1:15–2:15)

Screen: **left**, the Alexa web simulator (Mode: *Isolation*, Stage: *development*) or an Echo. **Right**, the Aegis server log, showing each tool call as it arrives.

| Time | Visual / on-screen | Audio |
|---|---|---|
| 1:15–1:19 | The simulator, with **Aegis** selected in the Add-on menu. Caption: *Live: Alexa+ → Aegis MCP server.* | **VO:** "This is Alexa+, calling our server live." |
| 1:19–1:33 | **USER:** *"Ask Aegis to check the voicemail I just got from the IRS."* Log: `check_voicemail … "status": "checked"`. Alexa's reply appears. Caption: *Verdict from 12 fixed rules.* | **ALEXA** (Aegis's `say`): "This message looks like a scam. The caller wants payment by gift card, wire transfer or cryptocurrency. Real agencies never ask for that. Would you like me to block this number or report it?" |
| 1:33–1:43 | **USER:** *"Why does it look like a scam?"* Log: `explain_red_flags`. The warning signs appear. | **VO:** "Every warning sign comes in words a grandparent can act on." |
| 1:43–1:57 | **USER:** *"Block them."* Log: `block_number … "mode": "stage", "status": "staged"`. Caption: *Staged, not done. A single-use approval code, never spoken.* | **ALEXA** (`say`): "I can block calls from 2 0 2, 5 5 5, 0 1 4 7. This is the caller whose message looked like a scam. Should I go ahead?" |
| 1:57–2:15 | **USER:** *"Yes."* Log: `"mode": "resolve", "status": "executed"`. Caption: *Practice run, honestly labelled.* | **ALEXA** (`say`): "Done. I've blocked calls from 2 0 2, 5 5 5, 0 1 4 7. This is a practice version of Aegis, so no real block was made." Then **VO:** "Nothing happens until she says yes. And Aegis tells her when it's only practice." |

## 5. Developer feedback and close (2:15–2:45)

| Time | Visual / on-screen | Audio |
|---|---|---|
| 2:15–2:22 | `AMAZON_DEVELOPER_FEEDBACK.md` on GitHub, scrolling through the 5-question sections. | **VO:** "We're sending Amazon's Alexa+ team direct feedback, in the five-question format, for every tool we used." |
| 2:22–2:30 | Callout: *Docs promise MCP `2025-11-25`; the documented handshake sends `2025-03-26`.* | **VO:** "The documented handshake asks for an older protocol than the docs promise, so we support both." |
| 2:30–2:38 | Callout: *Tools can't tell an Echo Dot from an Echo Show.* | **VO:** "And tools can't tell when there's no screen, which a voice-first product for seniors needs." |
| 2:38–2:45 | End card: **Aegis: Voice-First Scam Defense on Alexa+**, repo URL, *Built for the Alexa+ track.* | **VO:** "Aegis. Protection that speaks plainly, and asks first." |

---

## Recording checklist

- [ ] Aegis is deployed to the development stage and answers in the simulator ([`ALEXA_DEPLOY.md`](ALEXA_DEPLOY.md)).
- [ ] Reset before **every** take: `scripts/demo_down.sh && scripts/demo_up.sh`. The tunnel can stay up.
- [ ] Caption Alexa's *actual* replies; they can differ from Aegis's `say` text.
- [ ] No statistics are used. If you add one, cite a primary source on screen (for example the FBI IC3 Elder Fraud Report) and check the figure first.
- [ ] Tests green first: `pytest tests/ -q` and `python server/smoke_test.py`.
- [ ] **Only if Alexa+ access is blocked:** `scripts/demo_voice_flow.py --speak` can stand in, labelled *Simulated client · Alexa+ connection pending*. Be aware this may not satisfy the rule that the video show the project on its device.
