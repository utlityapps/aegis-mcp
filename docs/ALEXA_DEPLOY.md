# Deploying Aegis to Alexa+ (development stage) and testing it

The hackathon rules ask for a video that *"shows the Project functioning on the device for which it was built"*. For Aegis that means **Alexa+ calling this server**, tested in the Alexa web simulator or on an Echo. This guide gets you there. It follows Amazon's [Set Up Your Development Environment](https://developer.amazon.com/en-US/docs/alexaplus/add-ons/set-up-your-development-environment.html), [Create an MCP Add-on](https://developer.amazon.com/en-US/docs/alexaplus/add-ons/mcp-toolkit-quickstart.html) and [Test in the Web Simulator](https://developer.amazon.com/en-US/docs/alexaplus/add-ons/test-with-web-simulator.html) pages.

Steps 1–3 need **your** accounts; nobody can do them for you. Everything Aegis-specific (the manifest, icons, privacy and terms URLs, and the server) is ready in this repo.

---

## 0. Check access first (5 minutes, do it today)

Amazon's setup page installs the Alexa AI CLI from a **private** registry, by assuming an Amazon-owned role, and refers to *"the AWS account that you provided to the Alexa Solutions Architect"*. Developer access may therefore need to be granted.

- [ ] Sign in at <https://developer.amazon.com/alexa/console/ask/addons>. Can you see **My Add-ons** and the **Simulator**?
- [ ] If not, or if Step 2's `aws sts get-caller-identity --profile alexa-ai` fails with *AccessDenied*, ask in the hackathon's support channel how participants get Alexa+ add-on access. Do this early; it's the only step that can block the video.

## 1. Accounts and tools (one-time)

| Need | How |
|---|---|
| Alexa developer account | <https://developer.amazon.com/alexa/>. Complete the profile in the Alexa app, and use a US marketplace (the MCP Toolkit is US-only). |
| AWS account | <https://console.aws.amazon.com/>. The free tier is fine. |
| Node.js 24+ | Already installed on this Mac (`node --version` shows v24). |
| AWS CLI | <https://aws.amazon.com/cli/> (macOS `.pkg` installer) |
| cloudflared | <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/> (macOS `.pkg`). Homebrew isn't installed on this Mac. |

## 2. Install the Alexa AI CLI

Follow Amazon's page, Steps 1–4 and 6 (Step 5 is only for Category Action add-ons):
1. Create an IAM user (e.g. `alexa-ai-tools`) with an access key, and an inline policy allowing `sts:AssumeRole` on `arn:aws:iam::372468808636:role/AddOn3PDeveloperToolsRead`. **`AdministratorAccess` is not needed:** Aegis is self-hosted, not deployed with CDK.
2. Set up the profiles:
   ```bash
   aws configure --profile alexa-ai-user
   aws configure set profile.alexa-ai.role_arn arn:aws:iam::372468808636:role/AddOn3PDeveloperToolsRead
   aws configure set profile.alexa-ai.source_profile alexa-ai-user
   aws configure set profile.alexa-ai.region us-west-2
   aws sts get-caller-identity --profile alexa-ai
   ```
3. Configure Git for CodeCommit, and npm for CodeArtifact (the token lasts 12 hours), exactly as on Amazon's page.
4. Install and sign in:
   ```bash
   npm install -g @alexa-ai/cli
   alexa-ai --version
   alexa-ai configure          # opens a browser for Login with Amazon
   ```

## 3. Start Aegis and a public HTTPS tunnel

Terminal 1, the server (read-only health checks, then a banner):
```bash
cd ~/Downloads/Aegis && scripts/demo_up.sh
```

Terminal 2, the tunnel. Keep it open:
```bash
cloudflared tunnel --url http://127.0.0.1:8766 --http-host-header 127.0.0.1:8766
# → https://<random-words>.trycloudflare.com
```

- **`--http-host-header`** makes the Host header match what Aegis allows. Without it, Aegis answers 421.
- **Quick-tunnel URLs change every time cloudflared restarts,** and Alexa+ reads your tools only on `alexa-ai deploy`. If the URL changes, re-run `render_addon.py` with the new URL and `alexa-ai deploy` again. You don't need a new add-on. For a stable URL, use a [named tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/) on a domain you own.

Check it from anywhere:
```bash
curl -s https://<random-words>.trycloudflare.com/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
# → … "protocolVersion":"2025-11-25" … "serverInfo":{"name":"aegis" …
```

## 4. Create the add-on and fill in the manifest

```bash
cd ~/Downloads/Aegis
alexa-ai new mcp --name "Aegis" --locale en-US --mcp-server-url "https://<random-words>.trycloudflare.com/mcp"
.venv/bin/python scripts/render_addon.py --mcp-url "https://<random-words>.trycloudflare.com/mcp"
```

`render_addon.py` overwrites `addon-package/addon.json` with Aegis's listing:
- descriptions and four example phrases;
- the privacy and terms URLs ([`PRIVACY.md`](PRIVACY.md), [`TERMS.md`](TERMS.md));
- the six icons and the 600×900 carousel image.

It checks every QuickStart field constraint first. `addon-package/` is git-ignored, because it holds your tunnel URL.

> The icons and carousel image are served from GitHub at `raw.githubusercontent.com/utlityapps/aegis-mcp/main/alexa/assets/…`, and the privacy and terms URLs point at GitHub too. **Push the repo before deploying**, or those URLs will 404.

## 5. Deploy to the development stage

```bash
cd addon-package && alexa-ai deploy
```

On success the CLI prints your **Add-on ID** and version. Only your developer account can see the development stage.

## 6. Test in the web simulator

1. Open <https://developer.amazon.com/alexa/console/ask/addons/simulator> (or **My Add-ons → Test in Simulator**).
2. Set **Add-on:** Aegis · **Mode:** *Isolation*, which sends every request to Aegis · **Stage:** *development*.
3. Type each line into **Ask Alexa** (no wake word needed):

| Type this | Expect (Alexa+ composes the final wording from Aegis's `say` text) |
|---|---|
| `Ask Aegis to check the voicemail I just got from the IRS` | It looks like a scam: gift-card payment, real agencies never ask for that. Offers to block or report. |
| `Why does it look like a scam?` | Warning signs read in plain words |
| `Block them` | A read-back: block calls from 2 0 2, 5 5 5, 0 1 4 7… *Should I go ahead?* |
| `Yes` | Done, with *"This is a practice version of Aegis, so no real block was made."* |

4. Watch the tool calls arrive live, in another terminal:
   ```bash
   tail -n 0 -f ~/Downloads/Aegis/logs/aegis-server.log | grep --line-buffered '"event": "tools/call"'
   ```
5. **Optional, on a real Echo:** in the simulator, open **Physical Device Config**, select your device and **Save**. Utterances are then routed to that device.

**If Alexa+ doesn't call Aegis:**
- Make sure Mode is *Isolation*.
- Check the tunnel is up (the step 3 `curl`).
- Redeploy after any URL change.
- Look at the simulator's **Device Logs** tab.

Before every new take, reset the demo state with `scripts/demo_down.sh && scripts/demo_up.sh`. The tunnel can stay up.

## 7. After it works

- [ ] Record the video with [`RECORDING_GUIDE.md`](RECORDING_GUIDE.md).
- [ ] **Update the feedback.** Add what happened in steps 0–6 (access, CLI setup, deploy, simulator) to section 1, question 4 ("Onboarding") of [`AMAZON_DEVELOPER_FEEDBACK.md`](../AMAZON_DEVELOPER_FEEDBACK.md). That first-hand onboarding experience is exactly what the feedback section asks for.
- [ ] Keep the tunnel and server running through judging if you want judges to try it live. Otherwise the repo and video are what's evaluated.
