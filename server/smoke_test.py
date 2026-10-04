"""End-to-end MCP smoke checks over real Streamable HTTP. Run from the repo root:

    python server/smoke_test.py

Starts the server on a free local port, speaks raw JSON-RPC the way Alexa+ does,
and prints one PASS/FAIL line per check. Exits non-zero if any check fails.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
WEBHOOK_SECRET = secrets.token_urlsafe(32)
DISPLAY_TOKEN = secrets.token_urlsafe(32)
EXPECTED_TOOLS = ["list_voicemails", "check_voicemail", "explain_red_flags", "block_number", "report_scam"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Wire:
    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}/mcp"
        self._next_id = 0

    def http(
        self, method: str = "POST", body: Any = None, raw: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, str]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.url, data=data, method=method, headers={**HEADERS, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params
        status, text = self.http(body=body)
        if status != 200:
            raise AssertionError(f"{method} returned HTTP {status}: {text[:200]}")
        return json.loads(text)

    def initialize(self, version: str) -> dict[str, Any]:
        return self.rpc(
            "initialize",
            {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "aegis-smoke", "version": "1.0.0"}},
        )["result"]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rpc("tools/call", {"name": name, "arguments": arguments})["result"]


def _expect(condition: bool, detail: str) -> None:
    if not condition:
        raise AssertionError(detail)


def run_checks(wire: Wire) -> list[tuple[str, str | None]]:
    state: dict[str, Any] = {}

    def init_latest() -> None:
        result = wire.initialize("2025-11-25")
        _expect(result["protocolVersion"] >= "2025-11-25", f"negotiated {result['protocolVersion']}")
        _expect(result["serverInfo"]["name"] == "aegis", "serverInfo.name")

    def init_alexa() -> None:
        result = wire.initialize("2025-03-26")
        _expect(result["protocolVersion"] == "2025-03-26", f"negotiated {result['protocolVersion']}")
        _expect(set(result["capabilities"]) - {"experimental"} == {"tools"}, f"capabilities {result['capabilities']}")

    def tools_list() -> None:
        tools = wire.rpc("tools/list")["result"]["tools"]
        _expect([t["name"] for t in tools] == EXPECTED_TOOLS, "tool names")
        _expect(all({"inputSchema", "outputSchema", "annotations", "title"} <= set(t) for t in tools), "tool fields")

    def demo_beat() -> None:
        result = wire.call("check_voicemail", {"caller_hint": "IRS"})
        structured = result["structuredContent"]
        _expect(not result["isError"] and structured["verdict"] == "SCAM", "IRS voicemail should be SCAM")
        _expect(json.loads(result["content"][0]["text"]) == structured, "text content mirrors structuredContent")
        state["voicemail_id"] = structured["voicemail"]["voicemail_id"]

    def explain() -> None:
        structured = wire.call("explain_red_flags", {"voicemail_id": state["voicemail_id"]})["structuredContent"]
        _expect(len(structured["flags"]) == 3 and structured["next_start"] == 3, "first page of flags")

    def stage_block() -> None:
        structured = wire.call("block_number", {"voicemail_id": state["voicemail_id"]})["structuredContent"]
        _expect(structured["status"] == "staged", "status staged")
        _expect(structured["approval_token"] not in structured["say"], "token must not be spoken")
        state["token"] = structured["approval_token"]

    def approve_block() -> None:
        result = wire.call("block_number", {"approval_token": state["token"], "decision": "approve"})
        receipt = result["structuredContent"]["receipt"]
        _expect(receipt["simulated"] is True, "receipt must be simulated")

    def reused_token() -> None:
        result = wire.call("block_number", {"approval_token": state["token"], "decision": "approve"})
        _expect(result["isError"] and result["structuredContent"]["error"]["code"] == "permission_denied", "reuse")

    def wrong_kind() -> None:
        staged = wire.call("report_scam", {"voicemail_id": state["voicemail_id"]})["structuredContent"]
        result = wire.call("block_number", {"approval_token": staged["approval_token"], "decision": "approve"})
        _expect(result["isError"] and result["structuredContent"]["error"]["code"] == "permission_denied", "kind")
        state["report_token"] = staged["approval_token"]

    def reject() -> None:
        result = wire.call("report_scam", {"approval_token": state["report_token"], "decision": "reject"})
        _expect(result["structuredContent"]["status"] == "rejected", "reject drops the action")

    def invalid_input() -> None:
        result = wire.call("list_voicemails", {"limit": 99})
        _expect(result["isError"] and result["structuredContent"]["error"]["code"] == "invalid_input", "limit=99")

    def unknown_tool() -> None:
        reply = wire.rpc("tools/call", {"name": "delete_everything", "arguments": {}})
        _expect(reply.get("error", {}).get("code") == -32602, f"reply {reply}")

    def get_not_allowed() -> None:
        status, _ = wire.http("GET")
        _expect(status == 405, f"GET returned {status}")

    def delete_not_allowed() -> None:
        status, _ = wire.http("DELETE")
        _expect(status == 405, f"DELETE returned {status}")

    def origin_rejected() -> None:
        status, _ = wire.http(body={"jsonrpc": "2.0", "id": 99, "method": "ping"}, headers={"Origin": "http://evil.test"})
        _expect(status == 403, f"foreign Origin returned {status}")

    def body_limit() -> None:
        status, _ = wire.http(raw=b'{"pad":"' + b"a" * (65 * 1024) + b'"}')
        _expect(status == 413, f"oversized body returned {status}")

    def bad_version_header() -> None:
        body = {"jsonrpc": "2.0", "id": 100, "method": "tools/list"}
        status, _ = wire.http(body=body, headers={"MCP-Protocol-Version": "1999-01-01"})
        _expect(status == 400, f"unsupported MCP-Protocol-Version returned {status}")

    def invalid_id_rejected() -> None:
        status, text = wire.http(body={"jsonrpc": "2.0", "id": 1.5, "method": "ping"})
        _expect(status == 400 and json.loads(text)["error"]["code"] == -32600, f"float id returned {status}: {text}")

    def non_object_arguments() -> None:
        reply = wire.rpc("tools/call", {"name": "list_voicemails", "arguments": [1]})
        error = reply.get("result", {}).get("structuredContent", {}).get("error", {})
        _expect(reply.get("result", {}).get("isError") is True and error.get("code") == "invalid_input", f"{reply}")

    def half_close_gets_response() -> None:
        payload = json.dumps({"jsonrpc": "2.0", "id": 77, "method": "ping"}).encode()
        head = (
            f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{wire.port}\r\nAccept: {HEADERS['Accept']}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n"
        ).encode()
        with socket.create_connection(("127.0.0.1", wire.port), timeout=10) as sock:
            sock.sendall(head + payload)
            sock.shutdown(socket.SHUT_WR)
            received = b""
            while chunk := sock.recv(4096):
                received += chunk
        _expect(received.startswith(b"HTTP/1.1 200") and b'"id":77' in received, f"got {received[:80]!r}")

    def spelled_acronym() -> None:
        structured = wire.call("check_voicemail", {"caller_hint": "the I. R. S."})["structuredContent"]
        _expect(structured.get("voicemail", {}).get("voicemail_id") == state["voicemail_id"], f"{structured}")

    def ecosystem_chain() -> None:
        spec = importlib.util.spec_from_file_location("simulator", ROOT / "scripts" / "simulate_ecosystem_event.py")
        assert spec is not None and spec.loader is not None
        simulator = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = simulator  # dataclasses resolve their module through sys.modules
        spec.loader.exec_module(simulator)
        base = f"http://127.0.0.1:{wire.port}"
        chain = simulator.run_chain(base, WEBHOOK_SECRET, DISPLAY_TOKEN, "scam")
        _expect(chain.ok and [c["severity"] for c in chain.cards] == ["info", "alert"], f"{chain.error} {chain.cards}")

    def notification_accepted() -> None:
        status, _ = wire.http(body={"jsonrpc": "2.0", "method": "notifications/initialized"})
        _expect(status == 202, f"notification returned {status}")

    checks: list[tuple[str, Callable[[], None]]] = [
        ("initialize negotiates 2025-11-25 or later", init_latest),
        ("initialize accepts Alexa+'s 2025-03-26", init_alexa),
        ("tools/list returns exactly the five agreed tools", tools_list),
        ("demo beat: IRS voicemail is a SCAM", demo_beat),
        ("explain_red_flags pages warning signs", explain),
        ("block_number stages with a read-back", stage_block),
        ("approval executes a simulated block", approve_block),
        ("reused approval token is denied", reused_token),
        ("block_number cannot approve a staged report", wrong_kind),
        ("reject drops the staged report", reject),
        ("bad arguments return isError invalid_input", invalid_input),
        ("unknown tool is a JSON-RPC -32602 error", unknown_tool),
        ("GET /mcp is 405 in stateless mode", get_not_allowed),
        ("DELETE /mcp is 405 in stateless mode", delete_not_allowed),
        ("foreign Origin is rejected", origin_rejected),
        ("request bodies over 64 KiB are rejected", body_limit),
        ("unsupported MCP-Protocol-Version header is 400", bad_version_header),
        ("notifications get 202 Accepted", notification_accepted),
        ("non-integer request id gets -32600, not silence", invalid_id_rejected),
        ("non-object arguments return isError invalid_input", non_object_arguments),
        ("half-closed client still receives its response", half_close_gets_response),
        ("spelled-out 'I. R. S.' finds the IRS voicemail", spelled_acronym),
        ("Ring -> Bee -> assessment -> Fire TV card over HTTP/SSE", ecosystem_chain),
    ]
    results: list[tuple[str, str | None]] = []
    for label, check in checks:
        try:
            check()
            results.append((label, None))
        except Exception as exc:  # each check reports its own failure and the run continues
            results.append((label, f"{type(exc).__name__}: {exc}"))
    return results


def _wait_until_up(wire: Wire, process: subprocess.Popen[bytes], deadline: float) -> None:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with code {process.returncode}")
        try:
            wire.http(body={"jsonrpc": "2.0", "id": 0, "method": "ping"})
            return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.2)
    raise RuntimeError("server did not start within 15 seconds")


def main() -> int:
    port = _free_port()
    env = {
        **os.environ,
        "AEGIS_HOST": "127.0.0.1",
        "AEGIS_PORT": str(port),
        "AEGIS_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "AEGIS_DISPLAY_TOKEN": DISPLAY_TOKEN,
    }
    with tempfile.TemporaryFile() as server_log:
        process = subprocess.Popen(
            [sys.executable, "-m", "server"], cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT
        )
        wire = Wire(port)
        try:
            _wait_until_up(wire, process, time.monotonic() + 15)
            results = run_checks(wire)
        except RuntimeError as exc:
            print(f"FAIL  server startup: {exc}")
            server_log.seek(0)
            print(server_log.read().decode(errors="replace")[-2000:])
            return 1
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    for label, error in results:
        print(f"{'PASS' if error is None else 'FAIL'}  {label}" + (f"  ({error})" if error else ""))
    failed = sum(1 for _, error in results if error is not None)
    print(f"\n{len(results) - failed}/{len(results)} MCP smoke checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
