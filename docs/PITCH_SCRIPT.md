# Aegis: 2:45 executive pitch (Alexa+ track)

**Runtime:** 2:45. The rules require *"less than three (3) minutes"*, public, English, on YouTube or Vimeo.
**Must show:** the Aegis MCP server working through a client that speaks MCP. Alexa+'s own developer tools are preview-only, so the hackathon FAQ says a web page that is *"an actual MCP client (sending initialize, tools-list, and tools-call requests over Streamable HTTP)"* satisfies this. Ours is the **Aegis Voice Simulator** at <http://127.0.0.1:8766> (run `scripts/demo_up.sh`).
**Format:** 16:9 with captions burned in. Pace: about 150 spoken words per minute; word counts are checked against that.
**Recording steps:** [`RECORDING_GUIDE.md`](RECORDING_GUIDE.md) maps every line to the exact action.

| # | Segment | Time | Spoken words |
|---|---|---|---|
| 1 | The problem | 0:00–0:30 | VO 57 |
| 2 | Who it's for and the solution | 0:30–1:00 | VO 59 |
| 3 | Track tool | 1:00–1:15 | VO 25 |
| 4 | Working demo in the voice simulator | 1:15–2:15 | VO 39 + user 19 + Aegis 91 (about 60 s with the replies read aloud) |
| 5 | Developer feedback and close | 2:15–2:45 | VO 55 |

**Legend:** **VO** = narrator. **USER** = spoken into the simulator's microphone (or typed). **AEGIS** = the reply the simulator shows and reads aloud. *Italics* = on-screen text.

> **The replies below are Aegis's exact `say` text,** which the simulator reads word for word. On real Alexa+, Alexa would phrase the reply itself from the same text.

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
| 1:08–1:15 | The README topology diagram: voice simulator (and Alexa+) → Aegis server → deterministic engine. | **VO:** "The client handles the conversation. Aegis's fixed rules decide the risk." |

## 4. Working demo in the voice simulator (1:15–2:15)

Screen: the **Aegis Voice Simulator**, full screen. **Left**, the conversation. **Right**, the live MCP traffic: each JSON-RPC request and response as it happens.

| Time | Visual / on-screen | Audio |
|---|---|---|
| 1:15–1:19 | The simulator, top bar reading *Connected to Aegis 0.1.0 · MCP 2025-11-25 · 5 tools*. Caption: *Live: web MCP client → Aegis MCP server. Alexa+ developer tools are preview-only.* | **VO:** "This is a real MCP client, standing in for Alexa+, calling our server live." |
| 1:19–1:33 | **USER** (microphone): *"Check the voicemail I just got from the IRS."* Traffic: `tools/call · check_voicemail`. Red **SCAM** label. Caption: *Verdict from 12 fixed rules.* | **AEGIS** (`say`): "This message looks like a scam. The caller wants payment by gift card, wire transfer or cryptocurrency. Real agencies never ask for that. Would you like me to block this number or report it?" |
| 1:33–1:43 | **USER:** *"Why does it look like a scam?"* Traffic: `explain_red_flags`. The warning signs appear. | **VO:** "Every warning sign comes in words a grandparent can act on." |
| 1:43–1:57 | **USER:** *"Block them."* Traffic: `block_number`, response `"status": "staged"` with an `approval_token`. Caption: *Staged, not done. A single-use approval code, never spoken.* | **AEGIS** (`say`): "I can block calls from 2 0 2, 5 5 5, 0 1 4 7. This is the caller whose message looked like a scam. Should I go ahead?" |
| 1:57–2:15 | **USER:** *"Yes."* Traffic: the token goes back with `"decision": "approve"`; response `"status": "executed"`, `"simulated": true`. Caption: *Practice run, honestly labelled.* | **AEGIS** (`say`): "Done. I've blocked calls from 2 0 2, 5 5 5, 0 1 4 7. This is a practice version of Aegis, so no real block was made." Then **VO:** "Nothing happens until she says yes. And Aegis tells her when it's only practice." |

## 5. Developer feedback and close (2:15–2:45)

| Time | Visual / on-screen | Audio |
|---|---|---|
| 2:15–2:22 | `AMAZON_DEVELOPER_FEEDBACK.md` on GitHub, scrolling through the 5-question sections. | **VO:** "We're sending Amazon's Alexa+ team direct feedback, in the five-question format, for every tool we used." |
| 2:22–2:30 | Callout: *Docs promise MCP `2025-11-25`; the documented handshake sends `2025-03-26`.* | **VO:** "The documented handshake asks for an older protocol than the docs promise, so we support both." |
| 2:30–2:38 | Callout: *Tools can't tell an Echo Dot from an Echo Show.* | **VO:** "And tools can't tell when there's no screen, which a voice-first product for seniors needs." |
| 2:38–2:45 | End card: **Aegis: Voice-First Scam Defense on Alexa+**, repo URL, *Built for the Alexa+ track.* | **VO:** "Aegis. Protection that speaks plainly, and asks first." |

---

## Recording checklist

- [ ] `scripts/demo_up.sh` prints **AEGIS READY**, and <http://127.0.0.1:8766> shows *Connected*.
- [ ] Reset before **every** take: `scripts/demo_down.sh && scripts/demo_up.sh`, then reload the page.
- [ ] Say on screen that the client is a stand-in for Alexa+ (the 1:15 caption). Don't call it Alexa+ or imply an Amazon integration.
- [ ] No statistics are used. If you add one, cite a primary source on screen (for example the FBI IC3 Elder Fraud Report) and check the figure first.
- [ ] Tests green first: `pytest tests/ -q` and `python server/smoke_test.py`.
