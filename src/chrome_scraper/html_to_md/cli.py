"""`html-to-md` CLI: render a webpage as layout-preserving markdown.

Connects to a running browser-api server (start with: uv run browser-api).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from chrome_scraper.cli_output import emit_error
from chrome_scraper.html_to_md.extract import extract_from_url
from chrome_scraper.html_to_md.render import render_page
from chrome_scraper.web_scrapers.base import WebScraperError


_DEFAULT_OUT_DIR = Path("data/sources")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug or "page"


def _slug_from_payload(payload: dict, url: str) -> str:
    title = (payload.get("title") or "").strip()
    if title:
        return _slugify(title)[:100]
    parsed = urlparse(url)
    return _slugify(f"{parsed.netloc}{parsed.path}") or "page"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="html-to-md",
        description=(
            "Render a webpage as layout-preserving markdown via browser-api. "
            "Requires a running browser-api server (uv run browser-api)."
        ),
    )
    p.add_argument("url", nargs="?", help="URL to render. Omit when using --from-file.")
    p.add_argument(
        "--from-file",
        metavar="PATH",
        help="Convert a pre-downloaded HTML file instead of fetching a live URL.",
    )
    p.add_argument(
        "-o",
        "--output",
        help=(
            "Write markdown here (default: data/sources/<title-slug>.md). "
            "Pass '-' to print to stdout instead."
        ),
    )
    p.add_argument(
        "--save-items",
        action="store_true",
        help="Also write the raw text-node payload as <slug>.items.json next to the markdown.",
    )
    p.add_argument(
        "--timeout", type=float, default=30.0, help="Per-operation timeout in seconds."
    )
    p.add_argument("--label", default="html-to-md", help="Tab label.")
    p.add_argument(
        "--no-scroll", action="store_true", help="Skip the top-to-bottom scroll pass."
    )
    p.add_argument(
        "--browser-api",
        default="http://localhost:9333",
        help="browser-api URL (default: http://localhost:9333).",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print layout diagnostics (gutter, cols, rows) to stderr.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Resolve the URL: either a live URL or a local file passed via --from-file.
    if args.from_file:
        url = Path(args.from_file).resolve().as_uri()  # → file:///abs/path.html
    elif args.url:
        url = args.url
    else:
        emit_error("Provide a URL positional argument or --from-file PATH.")
        return 1

    try:
        payload = extract_from_url(
            url,
            browser_api_url=args.browser_api,
            timeout=args.timeout,
            tab_label=args.label,
            scroll=not args.no_scroll,
            verbose=args.verbose,
        )
    except WebScraperError as exc:
        emit_error(str(exc))
        return 1

    items = payload.get("items") or []
    viewport = payload.get("viewport") or {}
    page_width = float(viewport.get("scroll_w") or viewport.get("w") or 1280)
    if args.verbose:
        print(
            f"extracted {len(items)} text nodes from {payload.get('url')}"
            f" page_width={page_width:.0f}",
            file=sys.stderr,
        )

    md = render_page(items, page_width, verbose=args.verbose)

    slug = _slug_from_payload(payload, args.url)
    out_path: Path | None
    if args.output == "-":
        out_path = None
        sys.stdout.write(md)
    else:
        out_path = Path(args.output) if args.output else _DEFAULT_OUT_DIR / f"{slug}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"wrote {out_path} ({out_path.stat().st_size} bytes)", file=sys.stderr)

    if args.save_items:
        if out_path is not None:
            items_path = out_path.parent / f"{out_path.stem}.items.json"
        else:
            items_path = _DEFAULT_OUT_DIR / f"{slug}.items.json"
            items_path.parent.mkdir(parents=True, exist_ok=True)
        items_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {items_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
