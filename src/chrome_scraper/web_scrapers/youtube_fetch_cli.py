"""`yt-fetch` CLI: fetch a YouTube video page with expanded transcript.

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
from chrome_scraper.web_scrapers.youtube_fetch import fetch_video


_DEFAULT_OUT_ROOT = Path("data/youtube")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt-fetch",
        description=(
            "Fetch a YouTube video page with expanded description and transcript. "
            "Requires a running browser-api server (uv run browser-api)."
        ),
    )
    p.add_argument("url", help="YouTube video URL.")
    p.add_argument(
        "--out-dir",
        help="Output directory (default: data/youtube/<video-id>/).",
    )
    p.add_argument(
        "--timeout", type=float, default=45.0, help="Per-operation timeout in seconds."
    )
    p.add_argument(
        "--poll-interval", type=float, default=1.0, help="Navigation poll interval."
    )
    p.add_argument(
        "--browser-api",
        default="http://localhost:9333",
        help="browser-api URL (default: http://localhost:9333).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Derive output dir from video id
    m = re.search(r"(?:v=|/shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", args.url)
    video_id = m.group(1) if m else "video"

    out_dir = Path(args.out_dir) if args.out_dir else _DEFAULT_OUT_ROOT / video_id
    browser = BrowserAPIClient(base_url=args.browser_api, timeout=args.timeout)

    tab_label = f"yt-{video_id}"

    try:
        with browser.tab(tab_label):
            html_path = fetch_video(
                browser=browser,
                tab_ref=tab_label,
                url=args.url,
                out_dir=out_dir,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
            )
        result = {
            "url": args.url,
            "video_id": video_id,
            "out_dir": str(out_dir),
            "html_file": str(html_path),
            "md_file": str(html_path.with_suffix(".md")),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except WebScraperError as exc:
        emit_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
