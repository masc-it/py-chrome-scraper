"""`url-to-md` CLI: render a URL as markdown via browser-api.

Uses the shared URL dispatcher, so known hosts get their custom scraper
preparation automatically before the generic layout-to-markdown renderer runs.
Requires a running browser-api server (start with: uv run browser-api).
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from chrome_scraper.browser_api.client import BrowserAPIClient
from chrome_scraper.cli_output import emit_error
from chrome_scraper.web_scrapers.base import WebScraperError
from chrome_scraper.web_scrapers.url_dispatch import render_url_as_markdown


_DEFAULT_OUT_DIR = Path("data/sources")
_DEFAULT_BROWSER_API = "http://localhost:9333"


def _slugify(value: str, max_len: int = 100) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug[:max_len] or "page"


def _slug_from_title(title: str, url: str) -> str:
    if title.strip():
        return _slugify(title.strip())
    parsed = urlparse(url)
    return _slugify(f"{parsed.netloc}{parsed.path}") or "page"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="url-to-md",
        description=(
            "Render a URL as markdown via browser-api, using custom site handlers "
            "automatically when available. Requires a running browser-api server "
            "(uv run browser-api)."
        ),
    )
    p.add_argument("url", help="URL to render.")
    p.add_argument(
        "-o",
        "--output",
        help=(
            "Write markdown here (default: data/sources/<title-slug>.md). "
            "Pass '-' to print to stdout instead."
        ),
    )
    p.add_argument("--timeout", type=float, default=60.0, help="Timeout in seconds.")
    p.add_argument(
        "--browser-api",
        default=_DEFAULT_BROWSER_API,
        help=f"browser-api URL (default: {_DEFAULT_BROWSER_API}).",
    )
    p.add_argument(
        "--no-scroll",
        action="store_true",
        help="Skip the top-to-bottom scroll pass.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    browser = BrowserAPIClient(base_url=args.browser_api, timeout=args.timeout + 5)
    tab_label = f"url-to-md-{uuid.uuid4().hex[:8]}"

    try:
        with browser.tab(tab_label):
            rendered = render_url_as_markdown(
                browser=browser,
                tab_ref=tab_label,
                url=args.url,
                timeout=args.timeout,
                scroll=not args.no_scroll,
            )
    except WebScraperError as exc:
        emit_error(str(exc))
        return 1

    if args.output == "-":
        sys.stdout.write(rendered.markdown)
    else:
        slug = _slug_from_title(rendered.title, args.url)
        out_path = Path(args.output) if args.output else _DEFAULT_OUT_DIR / f"{slug}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered.markdown, encoding="utf-8")
        print(f"wrote {out_path} ({out_path.stat().st_size} bytes)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
