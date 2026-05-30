"""`xcom-fetch` CLI: search x.com and download tweet pages.

Connects to a running browser-api server (start with: uv run browser-api).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from chrome_scraper.browser_api.client import BrowserAPIClient
from chrome_scraper.cli_output import emit_error
from chrome_scraper.web_scrapers.base import WebScraperError
from chrome_scraper.web_scrapers.xcom_fetch import fetch_query


_DEFAULT_OUT_ROOT = Path("data/research")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xcom-fetch",
        description=(
            "Search x.com and download each result tweet as html+md. "
            "Requires a running browser-api server (uv run browser-api)."
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--query", help="Search keyword(s).")
    src.add_argument(
        "--post",
        help="Fetch a single tweet URL (including any quoted tweets inside).",
    )
    p.add_argument(
        "--from",
        dest="from_profile",
        help="Restrict search to a single account: prepends 'from:<profile>' to the query.",
    )
    p.add_argument(
        "--out-dir", help="Output directory (default: data/research/<slug>/)."
    )
    p.add_argument(
        "--max-results", type=int, default=20, help="Max results to fetch (default: 20)."
    )
    p.add_argument(
        "--timeout", type=float, default=30.0, help="Per-operation timeout in seconds."
    )
    p.add_argument(
        "--poll-interval", type=float, default=1.0, help="Navigation poll interval."
    )
    p.add_argument(
        "--browser-api", help="browser-api URL (default: http://localhost:9333)."
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    browser = BrowserAPIClient(base_url=args.browser_api, timeout=args.timeout)

    if args.post:
        # ── Single tweet fetch (with quote tweet recursion) ──
        m = re.search(r"/status/(\d+)", args.post)
        status_id = m.group(1) if m else "tweet"
        slug_source = f"xcom-post-{status_id}"
        out_dir = (
            Path(args.out_dir)
            if args.out_dir
            else _DEFAULT_OUT_ROOT / _slugify(slug_source)
        )
        tab_label = f"xcom-post-{status_id}"
        try:
            with browser.tab(tab_label):
                from chrome_scraper.web_scrapers.xcom_fetch import fetch_single_post
                fetched = fetch_single_post(
                    browser=browser,
                    tab_ref=tab_label,
                    url=args.post,
                    out_dir=out_dir,
                    timeout=args.timeout,
                    poll_interval=args.poll_interval,
                )
            print(
                json.dumps(
                    {
                        "post": args.post,
                        "out_dir": str(out_dir),
                        "count": len(fetched),
                        "tweets": fetched,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        except WebScraperError as exc:
            emit_error(str(exc))
            return 1

    # ── Search mode ──
    slug_source = (
        f"xcom-from-{args.from_profile}-{args.query}"
        if args.from_profile
        else f"xcom-{args.query}"
    )
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else _DEFAULT_OUT_ROOT / _slugify(slug_source)
    )

    tab_label = f"xcom-{_slugify(args.query)[:30]}"

    try:
        with browser.tab(tab_label):
            fetched = fetch_query(
                browser=browser,
                tab_ref=tab_label,
                query=args.query,
                from_profile=args.from_profile,
                out_dir=out_dir,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
                max_results=args.max_results,
            )
        print(
            json.dumps(
                {
                    "query": args.query,
                    "from": args.from_profile,
                    "out_dir": str(out_dir),
                    "count": len(fetched),
                    "tweets": fetched,
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
