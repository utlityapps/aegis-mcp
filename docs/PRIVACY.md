# Aegis Privacy Policy

**Effective:** 2026-10-05
**Applies to:** the Aegis Alexa+ add-on and the Aegis server in this repository

Aegis is a practice version built for the Amazon Developer Hackathon "Build, Ship, Shape". It checks a fixed set of **sample voicemails bundled with the add-on**. It does not access your real voicemail, phone, contacts or accounts.

## What Aegis receives

When you talk to Aegis through Alexa+, Alexa+ sends the Aegis server **tool requests**, not your voice. A request can contain:
- the name of an action, such as "check a voicemail" or "block a caller";
- what you said about the caller (for example "the IRS", or part of a phone number), so Aegis can find the matching sample voicemail;
- identifiers that Aegis itself created earlier in the conversation, such as a sample-voicemail id, a list position, or a one-time approval code.

Aegis never receives audio recordings of your voice. Speech recognition happens in Alexa, under Amazon's privacy terms.

## What Aegis keeps

| Data | Kept? | Details |
|---|---|---|
| Your words about a caller | **Not stored** | Used to find a matching sample voicemail. A normalized copy stays in an in-memory lookup cache for at most 5 minutes (at most 256 entries), then is dropped. Never logged or written to disk. |
| Approval codes | **In memory only** | Stored as one-way hashes, single-use, expire after 10 minutes, and are lost when the server restarts. Never logged. |
| "Blocked" and "reported" records | **In memory only** | Simulated actions. They are lost when the server restarts, and nothing is sent to any phone carrier or authority. |
| Operational logs | **Yes, on the server host** | The action name, the result (for example "checked" or "staged"), how long it took, the sample-voicemail id, and the network address of the calling service (for Alexa+ requests, Amazon's servers, not your device). Phone numbers, email addresses and secrets are automatically redacted from log lines. |
| Metrics | **Yes, on the server host** | Counts and timings by action and outcome. They never include identifiers, phone numbers or what you said. |

Aegis does not sell, share or use data for advertising, and does not build profiles of people.

## Third parties

The add-on runs through Amazon Alexa+, and Amazon's own privacy notice applies to your use of Alexa. If the server is reached through a tunnel provider (for example Cloudflare Tunnel), that provider carries the encrypted traffic.

## Children

Aegis is not directed at children under 13 and does not knowingly collect information from them.

## Changes and contact

Changes to this policy are published in this file, and its history is visible in the repository. Questions: open an issue at <https://github.com/utlityapps/aegis-mcp/issues>.
