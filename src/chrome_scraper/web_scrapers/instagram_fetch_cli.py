"""`insta-fetch` CLI: search Instagram and download post pages.

Connects to a running browser-api server (start with: uv run browser-api).
Login cookies persist in the browser-api profile — log in once interactively.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from chrome_scraper.browser_api.client import BrowserAPIClient
from chrome_scraper.cli_output import emit_error
from chrome_scraper.web_scrapers.base import WebScraperError
from chrome_scraper.web_scrapers.instagram_fetch import fetch_query


_DEFAULT_OUT_ROOT = Path("data/research")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="insta-fetch",
        description=(
            "Search Instagram and download each result post as html+md. "
            "Requires a running browser-api server (uv run browser-api). "
            "Login via non-headless browser-api first — cookies persist."
        ),
    )
    # Mutually exclusive source group
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--query",
        help="Keyword search against Instagram explore.",
    )
    src.add_argument(
        "--profile",
        help="Scrape posts from a profile (e.g. 'nasa').",
    )
    src.add_argument(
        "--hashtag",
        help="Scrape posts from a hashtag (e.g. 'space').",
    )
    src.add_argument(
        "--post",
        help="Fetch a single post or reel URL.",
    )
    p.add_argument(
        "--out-dir",
        help="Output directory (default: data/research/<slug>/).",
    )
    p.add_argument(
        "--max-results",
        type=int,
        default=12,
        help="Max posts to fetch (default: 12).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-operation timeout in seconds.",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Navigation poll interval.",
    )
    p.add_argument(
        "--browser-api",
        default="http://localhost:9333",
        help="browser-api URL (default: http://localhost:9333).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Derive output dir slug from the active source
    slug_source = args.query or args.profile or args.hashtag or args.post or "instagram"
    if args.post:
        slug_source = f"insta-post-{_slugify(args.post)}"
    elif args.profile:
        slug_source = f"insta-{args.profile}"
    elif args.hashtag:
        slug_source = f"insta-hashtag-{args.hashtag}"
    else:
        slug_source = f"insta-{args.query}"

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else _DEFAULT_OUT_ROOT / _slugify(slug_source)
    )
    browser = BrowserAPIClient(base_url=args.browser_api, timeout=args.timeout)

    tab_label = f"insta-{_slugify(slug_source)[:30]}"

    try:
        with browser.tab(tab_label):
            fetched = fetch_query(
                browser=browser,
                tab_ref=tab_label,
                query=args.query,
                profile=args.profile,
                hashtag=args.hashtag,
                post_url=args.post,
                out_dir=out_dir,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
                max_results=args.max_results,
            )
        print(
            json.dumps(
                {
                    "query": args.query,
                    "profile": args.profile,
                    "hashtag": args.hashtag,
                    "post": args.post,
                    "out_dir": str(out_dir),
                    "count": len(fetched),
                    "posts": fetched,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    except WebScraperError as exc:
        emit_error(str(exc))
        return 1


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug[:60] or "instagram"


if __name__ == "__main__":
    raise SystemExit(main())
