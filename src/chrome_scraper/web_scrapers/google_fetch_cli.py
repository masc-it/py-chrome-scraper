"""`google-fetch` CLI: search Google and download result HTMLs.

Connects to a running browser-api server (start with: uv run browser-api).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from chrome_scraper.browser_api.client import BrowserAPIClient
from chrome_scraper.cli_output import emit_error
from chrome_scraper.web_scrapers.base import WebScraperError
from chrome_scraper.web_scrapers.google_fetch import fetch_query


_DEFAULT_OUT_ROOT = Path("data/research")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="google-fetch",
        description=(
            "Search Google and download result HTMLs. "
            "Requires a running browser-api server (uv run browser-api)."
        ),
    )
    p.add_argument("--query", required=True, help="Google search query.")
    p.add_argument(
        "--out-dir",
        help="Output directory (default: data/research/<query-slug>/<tag>/).",
    )
    p.add_argument(
        "--tag", help="Run tag for scoping output subfolder (default: timestamp)."
    )
    p.add_argument(
        "--num-pages",
        type=int,
        default=1,
        help="Google result pages to scrape (default: 1).",
    )
    p.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Stop after this many results (default: all).",
    )
    p.add_argument(
        "--results-per-page",
        type=int,
        default=10,
        help="Results per Google page (default: 10).",
    )
    p.add_argument(
        "--allowed-hosts", nargs="*", help="Only fetch from these hostnames."
    )
    p.add_argument(
        "--timeout", type=float, default=30.0, help="Per-operation timeout in seconds."
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Navigation poll interval in seconds.",
    )
    p.add_argument(
        "--browser-api", help="browser-api URL (default: http://localhost:9333)."
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    tag = args.tag or str(int(time.time()))
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else _DEFAULT_OUT_ROOT / _slugify(args.query) / tag
    )
    browser = BrowserAPIClient(base_url=args.browser_api, timeout=args.timeout)
    tab_label = f"google-{_slugify(args.query)[:30]}"

    try:
        with browser.tab(tab_label):
            fetched = fetch_query(
                browser=browser,
                tab_ref=tab_label,
                query=args.query,
                out_dir=out_dir,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
                num_pages=args.num_pages,
                max_results=args.max_results,
                allowed_hosts=set(args.allowed_hosts) if args.allowed_hosts else None,
                results_per_page=args.results_per_page,
            )
        print(
            json.dumps(
                {
                    "query": args.query,
                    "num_pages": args.num_pages,
                    "out_dir": str(out_dir),
                    "count": len(fetched),
                    "pages": fetched,
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
    return slug[:60] or "query"


if __name__ == "__main__":
    raise SystemExit(main())
