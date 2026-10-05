"""Simulated Fire TV display: a static page that renders cards from the live /events/firetv stream."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

_ASSETS: Final = Path(__file__).resolve().parent
_FILES: Final = {
    "/display/firetv": ("firetv.html", "text/html; charset=utf-8"),
    "/display/firetv.js": ("firetv.js", "text/javascript; charset=utf-8"),
    "/display/firetv.css": ("firetv.css", "text/css; charset=utf-8"),
}
SECURITY_HEADERS: Final = {
    # Same-origin only: the page may load its own script and style and talk to /events/firetv, nothing else.
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def display_routes() -> list[Route]:
    """Static assets only: the page holds no secret. It reads the token from its own URL fragment."""
    bodies = {path: ((_ASSETS / name).read_bytes(), media) for path, (name, media) in _FILES.items()}

    async def serve(request: Request) -> Response:
        body, media_type = bodies[request.url.path]
        return Response(body, media_type=media_type, headers=SECURITY_HEADERS)

    return [Route(path, serve, methods=["GET"]) for path in bodies]
