"""The Alexa+ deploy kit: manifest constraints, media assets and the pages the listing links to."""

from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("render_addon", ROOT / "scripts" / "render_addon.py")
assert _spec is not None and _spec.loader is not None
render_addon = importlib.util.module_from_spec(_spec)
sys.modules["render_addon"] = render_addon
_spec.loader.exec_module(render_addon)

REPO_BLOB = "https://github.com/utlityapps/aegis-mcp/blob/main/"


def test_rendered_manifest_meets_every_quickstart_constraint() -> None:
    manifest = render_addon.render("https://aegis-demo.example.com/mcp")
    assert render_addon.problems(manifest) == []


@pytest.mark.parametrize("url", ["http://aegis.example.com/mcp", "https://127.0.0.1:8766", "__MCP_SERVER_URL__", "https:///mcp"])
def test_non_public_or_unfilled_endpoints_are_rejected(url: str) -> None:
    assert render_addon.problems(render_addon.render(url))


def test_listing_text_stays_honest() -> None:
    entry = render_addon.render("https://x.example.com/mcp")["storeListing"]["locales"]["en-US"]
    full = entry["fullDescription"].lower()
    for disclosure in ("practice version", "sample voicemails", "simulated", "say yes", "does not record your voice"):
        assert disclosure in full


def test_every_asset_url_points_at_a_real_png_of_the_declared_size() -> None:
    entry = render_addon.render("https://x.example.com/mcp")["storeListing"]["locales"]["en-US"]
    images = [*entry["mediaAssets"]["icons"]["light"], *entry["mediaAssets"]["carouselImages"]]
    for image in images:
        path = ROOT / "alexa" / "assets" / image["uri"].rsplit("/", 1)[1]
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert f"{width}x{height}" == image["size"]


def test_privacy_and_terms_urls_point_at_files_in_this_repo() -> None:
    privacy = render_addon.render("https://x.example.com/mcp")["storeListing"]["locales"]["en-US"]["privacyAndCompliance"]
    for url in privacy.values():
        assert url.startswith(REPO_BLOB)
        assert (ROOT / url.removeprefix(REPO_BLOB)).is_file()


def test_privacy_policy_matches_the_code() -> None:
    from server import tools

    policy = (ROOT / "docs" / "PRIVACY.md").read_text()
    assert f"{int(tools.HINT_CACHE_TTL_SECONDS // 60)} minutes" in policy
    assert f"{tools.HINT_CACHE_MAX_ENTRIES} entries" in policy
    assert "expire after 10 minutes" in policy
