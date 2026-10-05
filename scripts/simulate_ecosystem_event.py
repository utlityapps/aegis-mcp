"""Fire one end-to-end ecosystem chain at a running Aegis server, over real HTTP:

    Ring doorbell press  ->  wearable (Bee) context  ->  deterministic assessment  ->  Fire TV card

Usage (from the repo root, with the server started using the same two secrets):

    export AEGIS_WEBHOOK_SECRET=... AEGIS_DISPLAY_TOKEN=...   # 32+ characters each
    python scripts/simulate_ecosystem_event.py --scenario scam
    python scripts/simulate_ecosystem_event.py --scenario benign --base-url http://127.0.0.1:8000

Standard library only. Exits non-zero if any step fails or a card doesn't arrive in time.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.ecosystem.schemas import RING_SCHEMA_ID, WEARABLE_SCHEMA_ID  # noqa: E402
from server.ecosystem.security import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign  # noqa: E402

SCENARIOS: dict[str, tuple[str, str]] = {
    "scam": (
        "Hi, I'm from the electric company. Your power will be shut off today unless you pay the overdue bill "
        "right now. We only take gift cards or Zelle. Don't tell anyone, it's a special arrangement.",
        "alert",
    ),
    "benign": ("Hi, it's Dave from next door. I brought back your ladder. Have a good one!", "info"),
}
SCENARIOS["suspected_doorstep_scam"] = SCENARIOS["scam"]  # the name used in the demo script


@dataclass
class Step:
    name: str
    status: int
    ms: float
    detail: str = ""


@dataclass
class ChainResult:
    steps: list[Step] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class CardListener(threading.Thread):
    """Reads the Fire TV SSE stream on a background thread and queues each card."""

    def __init__(self, base_url: str, token: str) -> None:
        super().__init__(daemon=True)
        self.cards: queue.Queue[dict[str, Any]] = queue.Queue()
        self.connected = threading.Event()
        self.failure: str | None = None
        url = urllib.parse.urlsplit(base_url)
        self._conn = http.client.HTTPConnection(url.hostname or "127.0.0.1", url.port or 80, timeout=30)
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}

    def run(self) -> None:
        try:
            self._conn.request("GET", "/events/firetv", headers=self._headers)
            response = self._conn.getresponse()
            if response.status != 200:
                self.failure = f"display stream refused: HTTP {response.status}"
                self.connected.set()
                return
            data: list[str] = []
            for raw in response:
                line = raw.decode().rstrip("\n")
                if line == ": connected":
                    self.connected.set()
                elif line.startswith("data: "):
                    data.append(line[6:])
                elif line == "" and data:
                    self.cards.put(json.loads("".join(data)))
                    data = []
        except Exception as exc:  # the stream closing at shutdown also lands here
            if not self.connected.is_set():
                self.failure = f"display stream failed: {exc}"
                self.connected.set()

    def close(self) -> None:
        """Shut the socket down so the reader thread returns now, not at the next 15 s heartbeat."""
        if self._conn.sock is not None:
            try:
                self._conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._conn.close()


def post_signed(base_url: str, path: str, secret: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", TIMESTAMP_HEADER: timestamp, SIGNATURE_HEADER: sign(secret, timestamp, body)},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def run_chain(
    base_url: str, secret: str, token: str, scenario: str, card_timeout: float = 5.0, gap: float = 0.0
) -> ChainResult:
    transcript, expected_severity = SCENARIOS[scenario]
    result = ChainResult()
    listener = CardListener(base_url, token)
    listener.start()
    listener.connected.wait(timeout=card_timeout)
    if listener.failure or not listener.connected.is_set():
        result.error = listener.failure or "display stream did not connect"
        return result

    def next_card(step: str) -> dict[str, Any] | None:
        try:
            card = listener.cards.get(timeout=card_timeout)
        except queue.Empty:
            result.error = f"no Fire TV card after {step}"
            return None
        result.cards.append(card)
        return card

    now = datetime.now(UTC).isoformat()
    try:
        started = time.perf_counter()
        status, reply = post_signed(base_url, "/webhooks/ring", secret, {
            "schema": RING_SCHEMA_ID,
            "event_id": f"ring-{secrets.token_hex(8)}",
            "event_type": "doorbell_press",
            "device": {"device_id": "front-doorbell", "location": "front_door"},
            "occurred_at": now,
            "person_detected": True,
        })
        result.steps.append(Step("Ring doorbell press", status, (time.perf_counter() - started) * 1000, str(reply)))
        if status != 202 or next_card("the doorbell event") is None:
            result.error = result.error or f"ring webhook returned {status}: {reply}"
            return result
        time.sleep(gap)  # lets the info card stay on screen before the visitor's words arrive

        started = time.perf_counter()
        status, reply = post_signed(base_url, "/webhooks/bee", secret, {
            "schema": WEARABLE_SCHEMA_ID,
            "event_id": f"bee-{secrets.token_hex(8)}",
            "device_id": "bee-pendant",
            "captured_at": now,
            "consent": {"all_parties_consented": True, "basis": "verbal"},
            "speaker": "other",
            "language": "en",
            "transcript": transcript,
        })
        card = next_card("the wearable context") if status == 202 else None
        result.steps.append(Step("Bee context -> assessment -> Fire TV card", status,
                                 (time.perf_counter() - started) * 1000, str(reply)))
        if card is None:
            result.error = result.error or f"wearable webhook returned {status}: {reply}"
        elif card["severity"] != expected_severity:
            result.error = f"expected a {expected_severity} card, got {card['severity']}"
        return result
    finally:
        listener.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{os.environ.get('AEGIS_PORT', '8000')}")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="scam")
    parser.add_argument("--gap", type=float, default=0.0, help="seconds between the doorbell and the wearable event")
    args = parser.parse_args()
    secret, token = os.environ.get("AEGIS_WEBHOOK_SECRET"), os.environ.get("AEGIS_DISPLAY_TOKEN")
    if not secret or not token:
        print("Set AEGIS_WEBHOOK_SECRET and AEGIS_DISPLAY_TOKEN (the same values the server uses).", file=sys.stderr)
        return 2

    result = run_chain(args.base_url.rstrip("/"), secret, token, args.scenario, gap=max(0.0, min(args.gap, 30.0)))
    for step in result.steps:
        print(f"{'OK ' if step.status == 202 else 'ERR'} {step.name:44} HTTP {step.status}  {step.ms:6.1f} ms")
    for card in result.cards:
        threat = card["threat"] or {}
        print(f"    Fire TV card [{card['severity']:7}] {card['title']}"
              + (f"  ({threat['verdict']}, risk {threat['risk_score']})" if threat else ""))
        for line in card["evidence"]:
            print(f"        - {line}")
    if result.error:
        print(f"FAILED: {result.error}", file=sys.stderr)
        return 1
    print(f"Chain complete: {len(result.steps)} events -> {len(result.cards)} Fire TV cards (simulated).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
