"""Fill in alexa/addon.template.json and check it against the Alexa+ MCP QuickStart constraints.

    python scripts/render_addon.py --mcp-url https://<your-tunnel-host>/mcp
    python scripts/render_addon.py --mcp-url https://<host>/mcp --out addon-package/addon.json

Run it after `alexa-ai new mcp` has created addon-package/, then `alexa-ai deploy`.
Standard library only. Exits non-zero, listing every problem, if the manifest would be rejected.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "alexa" / "addon.template.json"
DEFAULT_ASSETS_BASE = "https://raw.githubusercontent.com/utlityapps/aegis-mcp/main/alexa/assets"
ICON_SIZES = ("72x72", "64x64", "88x88", "126x126", "180x180", "241x241")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _https(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and bool(parts.hostname) and "__" not in url


def render(mcp_url: str, assets_base: str = DEFAULT_ASSETS_BASE) -> dict[str, Any]:
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("__MCP_SERVER_URL__", mcp_url).replace("__ASSETS_BASE__", assets_base.rstrip("/"))
    return json.loads(text)


def problems(manifest: dict[str, Any]) -> list[str]:
    """Every QuickStart 'Field Constraints' rule this manifest breaks."""
    found: list[str] = []

    def check(ok: bool, message: str) -> None:
        if not ok:
            found.append(message)

    check(manifest.get("manifestVersion") == "1.0", 'manifestVersion must be "1.0"')
    listing = manifest["storeListing"]
    check(bool(listing.get("distributionCountries")), "distributionCountries must list at least one country")
    for locale, entry in listing["locales"].items():
        where = f"locales.{locale}"
        check(0 < len(entry["name"]["value"]) <= 30, f"{where}.name.value must be 1-30 characters")
        check(0 < len(entry["shortDescription"]) <= 123, f"{where}.shortDescription must be 1-123 characters")
        check(0 < len(entry["fullDescription"]) <= 4000, f"{where}.fullDescription must be 1-4000 characters")
        phrases = entry["examplePhrases"]
        check(3 <= len(phrases) <= 4, f"{where}.examplePhrases needs 3-4 items")
        check(all(0 < len(p) <= 200 for p in phrases), f"{where}.examplePhrases items must be 1-200 characters")
        check(len({p.lower() for p in phrases}) == len(phrases), f"{where}.examplePhrases must be distinct")
        privacy = entry["privacyAndCompliance"]
        check(_https(privacy["privacyPolicyUrl"]), f"{where}.privacyPolicyUrl must be an https URL")
        check(_https(privacy["termsOfUseUrl"]), f"{where}.termsOfUseUrl must be an https URL")
        icons = entry["mediaAssets"]["icons"]["light"]
        check(sorted(i["size"] for i in icons) == sorted(ICON_SIZES), f"{where} needs light icons in all 6 sizes")
        carousel = entry["mediaAssets"]["carouselImages"]
        check(len(carousel) >= 1, f"{where} needs at least one carousel image")
        check(all(c["size"] == "600x900" and 0 < len(c["altText"]) <= 250 for c in carousel),
              f"{where} carousel images must be 600x900 with altText of 1-250 characters")
        for image in [*icons, *carousel]:
            check(_https(image["uri"]) and image["uri"].lower().endswith(IMAGE_SUFFIXES),
                  f"image {image['uri']} must be an https PNG/JPG/JPEG/WEBP URL")
    for integration in manifest["integrations"]:
        endpoint = integration["config"]["endpoints"]["default"]
        check(integration["type"] == "MCP" and endpoint["type"] == "HTTPS", 'integration must be type "MCP" over "HTTPS"')
        check(_https(endpoint["uri"]), f"MCP endpoint {endpoint['uri']!r} must be a public https URL")
        check(endpoint["uri"].rstrip("/").endswith("/mcp"), "MCP endpoint should end in /mcp (Aegis serves MCP there)")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Render and validate the Alexa+ add-on manifest.")
    parser.add_argument("--mcp-url", required=True, help="public https URL of the Aegis /mcp endpoint")
    parser.add_argument("--assets-base", default=DEFAULT_ASSETS_BASE)
    parser.add_argument("--out", type=Path, default=ROOT / "addon-package" / "addon.json")
    args = parser.parse_args()

    manifest = render(args.mcp_url, args.assets_base)
    issues = problems(manifest)
    if issues:
        print("addon.json would be rejected:", *(f"  - {i}" for i in issues), sep="\n", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} (all QuickStart constraints pass). Next: alexa-ai deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
