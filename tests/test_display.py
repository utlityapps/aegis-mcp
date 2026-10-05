"""Simulated Fire TV display page: served safely, gated with the ecosystem, and its JS parses and renders cards."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from server.server import build_app

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-webhook-secret-0123456789abcdef"
TOKEN = "test-display-token-0123456789abcdefg"


async def get(app: Any, path: str) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [], "scheme": "http", "server": ("127.0.0.1", 8766), "client": ("127.0.0.1", 5000),
        "http_version": "1.1", "root_path": "",
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


@pytest.mark.parametrize(
    ("path", "media"),
    [("/display/firetv", "text/html"), ("/display/firetv.js", "text/javascript"), ("/display/firetv.css", "text/css")],
)
def test_assets_are_served_with_strict_headers(path: str, media: str) -> None:
    status, headers, body = asyncio.run(get(build_app(webhook_secret=SECRET, display_token=TOKEN), path))
    assert status == 200 and headers["content-type"].startswith(media) and body
    assert "default-src 'none'" in headers["content-security-policy"]
    assert "connect-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff" and headers["referrer-policy"] == "no-referrer"
    assert TOKEN.encode() not in body and SECRET.encode() not in body


def test_page_marks_itself_as_simulated() -> None:
    _, _, body = asyncio.run(get(build_app(webhook_secret=SECRET, display_token=TOKEN), "/display/firetv"))
    assert b"SIMULATED FIRE TV DISPLAY" in body


def test_display_is_absent_without_ecosystem_secrets() -> None:
    assert asyncio.run(get(build_app(), "/display/firetv"))[0] == 404


def test_script_never_injects_html() -> None:
    script = (ROOT / "server/ecosystem/display/firetv.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in script


NODE_HARNESS = r"""
const lib = require(process.argv[2]);
function el(tag) {
  return { tag, textContent: "", dataset: {}, hidden: true, style: {}, offsetWidth: 0, children: [],
           replaceChildren(...kids) { this.children = kids; } };
}
const nodes = Object.fromEntries(["card","card-badge","card-title","card-body","card-evidence","card-threat",
  "card-buttons","status","help"].map((id) => [id, el("div")]));
const doc = { getElementById: (id) => nodes[id], createElement: (tag) => el(tag) };

const card = JSON.parse(process.argv[3]);
const stream = `retry: 5000\n: connected\n\nevent: card\nid: ${card.card_id}\ndata: ${JSON.stringify(card)}\n\n: keepalive\n\nevent: card\ndata: {"partial`;
const [events, leftover] = lib.parseSse(stream);
lib.renderCard(doc, JSON.parse(events[0].data));
console.log(JSON.stringify({
  token: lib.tokenFromFragment("#token=abc-123_XYZ"),
  noToken: lib.tokenFromFragment(""),
  events: events.map((e) => e.event),
  leftover,
  isCard: lib.isCard(card), rejectsJunk: lib.isCard({ title: "x" }),
  hidden: nodes.card.hidden, severity: nodes.card.dataset.severity, badge: nodes["card-badge"].textContent,
  title: nodes["card-title"].textContent, evidence: nodes["card-evidence"].children.map((c) => c.textContent),
  buttons: nodes["card-buttons"].children.map((c) => c.textContent), threat: nodes["card-threat"].textContent,
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_display_script_parses_stream_and_renders_card_as_text(tmp_path: Path) -> None:
    from server.ecosystem.pipeline import EcosystemPipeline

    pipeline = EcosystemPipeline()
    pipeline.on_ring("doorbell_press", "front_door")
    card = pipeline.on_wearable(
        "other", "Pay me in gift cards right now. Don't tell anyone. <img src=x onerror=alert(1)>"
    ).card
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS)
    script = ROOT / "server/ecosystem/display/firetv.js"
    run = subprocess.run(
        ["node", str(harness), str(script), json.dumps(card)], capture_output=True, text=True, timeout=30, check=True
    )
    out = json.loads(run.stdout)
    assert out["token"] == "abc-123_XYZ" and out["noToken"] == ""
    assert out["events"] == ["card"] and out["leftover"].startswith("event: card")
    assert out["isCard"] is True and out["rejectsJunk"] is False
    assert out["hidden"] is False and out["severity"] == "alert" and out["badge"] == "SCAM WARNING"
    assert out["title"] == "Warning: this visitor sounds like a scam"
    assert len(out["evidence"]) == 3 and out["buttons"] == ["Ask Alexa", "Dismiss"]
    assert "decided by fixed rules" in out["threat"]
