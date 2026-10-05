"""Presenter-paced stand-in for Alexa+ in the demo video: check -> block -> yes, against the running server.

Use this only when the Alexa+ add-on isn't connected. It is labelled on screen as a simulated client.
Press Enter after speaking each line; the reply printed is the server's real `say` text.

    python scripts/demo_voice_flow.py            # uses AEGIS_PORT (default 8000)
    python scripts/demo_voice_flow.py --no-pause # run straight through (rehearsal)
    python scripts/demo_voice_flow.py --speak    # read Alexa's replies aloud with macOS `say`

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
import urllib.request
from typing import Any

BOLD, RED, GREEN, DIM, RESET = "\033[1m", "\033[31m", "\033[32m", "\033[2m", "\033[0m"


class Mcp:
    def __init__(self, base_url: str) -> None:
        self.url = f"{base_url}/mcp"
        self._id = 0

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}}).encode()
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read())["result"]
        if result.get("isError"):
            raise SystemExit(f"\n{RED}Server answered with an error:{RESET} {result['structuredContent']['error']['say']}\n"
                             "Reset the demo (scripts/demo_down.sh && scripts/demo_up.sh) and try again.")
        return result["structuredContent"]


SPEAK = False


def say(speaker: str, text: str, colour: str) -> None:
    wrapped = textwrap.fill(text, width=68, initial_indent="    ", subsequent_indent="    ")
    print(f"\n  {colour}{BOLD}{speaker}{RESET}\n{wrapped}", flush=True)
    if SPEAK and speaker == "ALEXA":
        subprocess.run(["say", "-r", "175", text], check=False)  # macOS text-to-speech stands in for Alexa's voice


def wait(pause: bool) -> None:
    if pause:
        input(f"\n  {DIM}[Enter]{RESET} ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulated Alexa+ client for the demo video.")
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{os.environ.get('AEGIS_PORT', '8000')}")
    parser.add_argument("--no-pause", action="store_true")
    parser.add_argument("--speak", action="store_true", help="read Alexa's replies aloud with macOS `say`")
    args = parser.parse_args()
    global SPEAK
    SPEAK = args.speak and shutil.which("say") is not None
    pause = not args.no_pause
    mcp = Mcp(args.base_url.rstrip("/"))

    print(f"\n  {BOLD}SIMULATED CLIENT{RESET} {DIM}· Alexa+ connection pending · replies are the live Aegis server's{RESET}")

    say("YOU", "Alexa, ask Aegis to check the voicemail I just got from the IRS.", GREEN)
    wait(pause)
    checked = mcp.call("check_voicemail", {"caller_hint": "IRS"})
    print(f"\n  {RED}{BOLD} {checked['verdict']} {RESET} {DIM}risk {checked['risk_score']}/100 · decided by 12 fixed rules{RESET}")
    say("ALEXA", checked["say"], BOLD)
    wait(pause)

    say("YOU", "Block them.", GREEN)
    wait(pause)
    staged = mcp.call("block_number", {"voicemail_id": checked["voicemail"]["voicemail_id"]})
    if staged["status"] != "staged":
        print(f"\n  {RED}This number is already blocked from an earlier take.{RESET}\n"
              "  Reset first: scripts/demo_down.sh && scripts/demo_up.sh\n")
        return 1
    print(f"\n  {DIM}staged · waiting for your yes · single-use approval token (never spoken){RESET}")
    say("ALEXA", staged["say"], BOLD)
    wait(pause)

    say("YOU", "Yes.", GREEN)
    wait(pause)
    done = mcp.call("block_number", {"approval_token": staged["approval_token"], "decision": "approve"})
    say("ALEXA", done["say"], BOLD)
    print(f"\n  {DIM}receipt:{RESET} {json.dumps(done['receipt'])}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
