"""`url-to-md` CLI: render a URL as markdown via the browser-api /get-page-as-md endpoint.

Thin wrapper — delegates everything to the server. Requires a running
browser-api server (start with: uv run browser-api).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

from chrome_scraper.cli_output import emit_error


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
            "Render a URL as markdown via browser-api's /get-page-as-md endpoint. "
            "Requires a running browser-api server (uv run browser-api)."
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

    body = {"url": args.url, "timeout": args.timeout, "scroll": not args.no_scroll}

    try:
        r = httpx.post(
            f"{args.browser_api.rstrip('/')}/get-page-as-md",
            json=body,
            timeout=args.timeout + 5,
        )
        r.raise_for_status()
    except httpx.ConnectError:
        emit_error(
            f"Cannot connect to browser-api at {args.browser_api}. "
            "Start it with: uv run browser-api"
        )
        return 1
    except httpx.HTTPStatusError as exc:
        emit_error(f"browser-api error: {exc.response.text}")
        return 1
    except httpx.TimeoutException:
        emit_error("Request timed out.")
        return 1

    md = r.text
    title = r.headers.get("x-page-title", "")

    if args.output == "-":
        sys.stdout.write(md)
    else:
        slug = _slug_from_title(title, args.url)
        out_path = Path(args.output) if args.output else _DEFAULT_OUT_DIR / f"{slug}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"wrote {out_path} ({out_path.stat().st_size} bytes)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
