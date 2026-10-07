"""Web voice simulator: served safely, allowed only from its own origin, and its MCP client drives real conversations."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from server.server import build_app
from server.simulator import same_origins

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "server/simulator/simulator.js"
PING = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()


async def request(app: Any, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in {"host": "127.0.0.1:8000", **(headers or {})}.items()]
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": raw_headers, "scheme": "http", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 5000),
        "http_version": "1.1", "root_path": "",
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    out_headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], out_headers, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


@pytest.mark.parametrize(
    ("path", "media"),
    [("/simulator", "text/html"), ("/simulator/simulator.js", "text/javascript"), ("/simulator/simulator.css", "text/css")],
)
def test_assets_are_served_with_strict_headers(path: str, media: str) -> None:
    status, headers, body = asyncio.run(request(build_app(), "GET", path))
    assert status == 200 and headers["content-type"].startswith(media) and body
    csp = headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "script-src 'self'" in csp
    assert headers["x-content-type-options"] == "nosniff" and headers["referrer-policy"] == "no-referrer"


def test_root_redirects_to_the_simulator() -> None:
    status, headers, _ = asyncio.run(request(build_app(), "GET", "/"))
    assert status == 307 and headers["location"] == "/simulator"


def test_simulator_can_be_turned_off() -> None:
    assert asyncio.run(request(build_app(simulator=False), "GET", "/simulator"))[0] == 404


def test_page_says_what_is_simulated() -> None:
    _, _, body = asyncio.run(request(build_app(), "GET", "/simulator"))
    assert b"What's simulated:" in body and b"Alexa+" in body


def test_same_origins_are_exact() -> None:
    assert same_origins(8766, ["aegis.example.com"]) == [
        "http://127.0.0.1:8766", "http://localhost:8766", "http://[::1]:8766", "https://aegis.example.com",
    ]


def test_script_never_injects_html() -> None:
    script = SCRIPT.read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in script


# --- the phrase matcher and MCP client, run under node against a live server -------------------------------

ROUTE_HARNESS = r"""
const lib = require(process.argv[2]);
const s = lib.freshState();
const out = {};
for (const text of JSON.parse(process.argv[3])) out[text] = lib.route(text, s);
out.hint = lib.callerHint("check the voicemail i just got from the irs please");
out.withPending = lib.route("yes", Object.assign(lib.freshState(), { pending: { tool: "block_number", token: "t" } }));
out.noPending = lib.route("no", Object.assign(lib.freshState(), { pending: { tool: "report_scam", token: "t" } }));
out.candidate = lib.route("The one from Dr. Patel's Office", Object.assign(lib.freshState(), {
  candidates: [{ voicemail_id: "vm-003", caller_label: "Medicare Benefits" }, { voicemail_id: "vm-007", caller_label: "Dr. Patel's Office" }],
}));
console.log(JSON.stringify(out));
"""

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@node
def test_phrase_matcher_picks_the_right_tool(tmp_path: Path) -> None:
    phrases = [
        "What voicemails do I have?",
        "Check the voicemail I just got from the IRS",
        "Is my last voicemail a scam?",
        "Block them",
        "Hello there",
    ]
    harness = tmp_path / "route.js"
    harness.write_text(ROUTE_HARNESS)
    run = subprocess.run(["node", str(harness), str(SCRIPT), json.dumps(phrases)], capture_output=True, text=True, timeout=30, check=True)
    out = json.loads(run.stdout)
    assert out[phrases[0]] == {"tool": "list_voicemails", "args": {}}
    assert out[phrases[1]] == {"tool": "check_voicemail", "args": {"caller_hint": "the irs"}}
    assert out[phrases[2]] == {"tool": "check_voicemail", "args": {}}
    assert "local" in out[phrases[3]]  # nothing checked yet: asks which voicemail instead of guessing
    assert "local" in out[phrases[4]]
    assert out["hint"] == "the irs"
    assert out["withPending"] == {"tool": "block_number", "args": {"approval_token": "t", "decision": "approve"}}
    assert out["noPending"] == {"tool": "report_scam", "args": {"approval_token": "t", "decision": "reject"}}
    assert out["candidate"] == {"tool": "check_voicemail", "args": {"voicemail_id": "vm-007"}}


CONVERSATION_HARNESS = r"""
const lib = require(process.argv[2]);
const traffic = [];
const client = new lib.McpClient(process.argv[3], (t) => traffic.push(t));
const s = lib.freshState();
(async () => {
  const info = await client.connect(process.argv[4]);
  const turns = [];
  for (const text of JSON.parse(process.argv[5])) {
    const d = lib.route(text, s);
    if (d.local) { turns.push({ text, local: d.local }); continue; }
    if (!d.args.approval_token) s.pending = null;
    const result = await client.callTool(d.tool, d.args);
    const sc = result.structuredContent;
    if (!result.isError) lib.remember(s, d.tool, sc);
    turns.push({ text, tool: d.tool, isError: !!result.isError, status: sc.status, verdict: sc.verdict, say: result.isError ? sc.error.say : sc.say, simulated: sc.receipt && sc.receipt.simulated });
  }
  console.log(JSON.stringify({ info, turns, methods: traffic.map((t) => t.message.method), statuses: traffic.map((t) => t.status) }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server() -> Iterator[str]:
    port = _free_port()
    env = {**os.environ, "AEGIS_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-m", "server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/simulator", timeout=1).close()
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def post_mcp(base: str, origin: str | None) -> int:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if origin:
        headers["Origin"] = origin
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{base}/mcp", data=PING, headers=headers), timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_only_the_servers_own_origin_may_call_mcp(live_server: str) -> None:
    port = live_server.rsplit(":", 1)[1]
    assert post_mcp(live_server, f"http://127.0.0.1:{port}") == 200
    assert post_mcp(live_server, f"http://localhost:{port}") == 200
    assert post_mcp(live_server, None) == 200  # non-browser clients send no Origin
    assert post_mcp(live_server, "http://127.0.0.1:1") == 403  # another local page
    assert post_mcp(live_server, "http://evil.test") == 403


@node
@pytest.mark.parametrize("version", ["2025-11-25", "2025-03-26"])
def test_demo_conversation_end_to_end(tmp_path: Path, live_server: str, version: str) -> None:
    turns = [
        "Check the voicemail I just got from the IRS",
        "Why does it look like a scam?",
        "Block them",
        "Yes",
        "Report it",
        "No",
        "Check the one from the office",
        "The one from Dr. Patel's Office",
    ]
    harness = tmp_path / "conversation.js"
    harness.write_text(CONVERSATION_HARNESS)
    run = subprocess.run(
        ["node", str(harness), str(SCRIPT), f"{live_server}/mcp", version, json.dumps(turns)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    out = json.loads(run.stdout)
    assert out["info"]["protocolVersion"] == version and len(out["info"]["tools"]) == 5
    assert out["methods"][:3] == ["initialize", "notifications/initialized", "tools/list"]
    assert all(status in (200, 202) for status in out["statuses"])

    check, explain, stage, approve, report, reject, ambiguous, chosen = out["turns"]
    assert check["tool"] == "check_voicemail" and check["verdict"] == "SCAM"
    assert explain["tool"] == "explain_red_flags" and "gift card" in explain["say"]
    assert stage["tool"] == "block_number" and stage["status"] == "staged"
    assert approve["status"] == "executed" and approve["simulated"] is True
    assert report["tool"] == "report_scam" and report["status"] == "staged"
    assert reject["tool"] == "report_scam" and not reject["isError"] and reject["status"] != "executed"
    assert ambiguous["status"] == "ambiguous"
    assert chosen["status"] == "checked" and chosen["verdict"] == "LEGITIMATE"
    for turn in out["turns"]:
        assert "vm-" not in turn["say"] and "approval" not in turn["say"].lower()
