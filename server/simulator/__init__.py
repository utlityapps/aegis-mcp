"""Aegis voice simulator: a web page that is a real MCP client of this server, standing in for Alexa+.

Alexa+ add-on tooling is preview-only, so the hackathon FAQ asks entrants to demo through a web page that
sends initialize, tools/list and tools/call over Streamable HTTP. The page is served from this server so its
Origin is our own; `same_origins` lists exactly those origins for the transport-security allowlist.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

SIMULATOR_PATH: Final = "/simulator"
_ASSETS: Final = Path(__file__).resolve().parent
_FILES: Final = {
    SIMULATOR_PATH: ("index.html", "text/html; charset=utf-8"),
    f"{SIMULATOR_PATH}/simulator.js": ("simulator.js", "text/javascript; charset=utf-8"),
    f"{SIMULATOR_PATH}/simulator.css": ("simulator.css", "text/css; charset=utf-8"),
}
SECURITY_HEADERS: Final = {
    # Same-origin only: the page loads its own script and style and talks to /mcp, nothing else.
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    # The microphone is optional (typing works too) and only ever for this page.
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
}


def same_origins(port: int, tunnel_hosts: Sequence[str] = ()) -> list[str]:
    """The browser origins the simulator page can have: loopback on `port`, plus each HTTPS tunnel host."""
    local = [f"http://{host}:{port}" for host in ("127.0.0.1", "localhost", "[::1]")]
    return [*local, *(f"https://{host}" for host in tunnel_hosts)]


def simulator_routes() -> list[Route]:
    """Static assets only: the page holds no secret and stores nothing."""
    bodies = {path: ((_ASSETS / name).read_bytes(), media) for path, (name, media) in _FILES.items()}

    async def serve(request: Request) -> Response:
        body, media_type = bodies[request.url.path]
        return Response(body, media_type=media_type, headers=SECURITY_HEADERS)

    async def home(request: Request) -> Response:
        return RedirectResponse(SIMULATOR_PATH, status_code=307)

    return [Route("/", home, methods=["GET"]), *(Route(path, serve, methods=["GET"]) for path in bodies)]
