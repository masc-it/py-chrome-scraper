"""`browser-api` CLI: start/stop/status for the shared browser server.

Usage:
    uv run browser-api                        # start with defaults (port 9333)
    uv run browser-api --port 8080            # custom port
    uv run browser-api --headless             # headless mode
    uv run browser-api --hide                 # hide Chrome window (macOS)
    uv run browser-api --browser-args="--disable-gpu --no-sandbox"
    uv run browser-api status                 # check if running
    uv run browser-api stop                   # shut down browser + server
"""

from __future__ import annotations

import argparse
import sys

import httpx

from chrome_scraper.cli_output import emit_error, emit_ok


def _ensure_background_patch() -> None:
    """Apply the Patchright crBrowser.js patch so new tabs stay in background."""
    import site
    from pathlib import Path

    for base in site.getsitepackages():
        if not base:
            continue
        target = (
            Path(base)
            / "patchright"
            / "driver"
            / "package"
            / "lib"
            / "server"
            / "chromium"
            / "crBrowser.js"
        )
        if target.exists():
            original = target.read_text(encoding="utf-8")
            if "background: true" in original:
                return
            needle = '{ url: "about:blank", browserContextId: this._browserContextId }'
            if needle in original:
                patched = original.replace(
                    needle,
                    '{ url: "about:blank", browserContextId: this._browserContextId, background: true }',
                )
                target.write_text(patched, encoding="utf-8")
                print(
                    "[browser-api] patched Patchright for background tabs",
                    file=sys.stderr,
                    flush=True,
                )
            return


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="browser-api",
        description="Shared browser server for chrome_scraper scrapers.",
    )
    p.add_argument(
        "--port", type=int, default=9333, help="Server port (default: 9333)."
    )
    p.add_argument("--channel", default="chrome", help="Chrome channel.")
    p.add_argument("--headless", action="store_true", help="Run Chrome headless.")
    p.add_argument("--chrome-path", help="Path to Chrome binary.")
    p.add_argument("--profile-dir", help="Chrome profile directory.")
    p.add_argument("--proxy", help="Proxy server URL.")
    p.add_argument(
        "--timeout", type=float, default=30.0, help="Default operation timeout."
    )
    p.add_argument(
        "--browser-args", default="", help="Extra Chrome CLI flags (space-separated)."
    )
    p.add_argument(
        "--hide", action="store_true", help="Hide Chrome after launch (macOS only)."
    )

    sub = p.add_subparsers(dest="command")
    sub.add_parser("status", help="Check if browser-api is running.")
    sub.add_parser("stop", help="Shut down browser and server.")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "status":
        return cmd_status(args)
    if args.command == "stop":
        return cmd_stop(args)
    # Default: start
    return cmd_start(args)


def cmd_start(args: argparse.Namespace) -> int:
    import uvicorn

    from chrome_scraper.browser_api.server import ServerConfig, create_app

    # Ensure Patchright is patched so new tabs don't bring Chrome to foreground.
    _ensure_background_patch()

    config = ServerConfig(
        port=args.port,
        channel=args.channel,
        headless=args.headless,
        chrome_path=args.chrome_path,
        profile_dir=args.profile_dir,
        proxy=args.proxy,
        timeout=args.timeout,
        browser_args=args.browser_args.split() if args.browser_args else [],
        hide=args.hide,
    )
    app = create_app(config)

    print(
        f"[browser-api] starting on :{config.port} ...",
        file=sys.stderr,
        flush=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="warning")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        r = httpx.get(f"http://127.0.0.1:{args.port}/status", timeout=3.0)
        r.raise_for_status()
        emit_ok(r.json())
        return 0
    except httpx.ConnectError:
        emit_error(f"browser-api not running on port {args.port}.")
        return 1
    except httpx.HTTPStatusError as exc:
        emit_error(f"Server error: {exc.response.text}")
        return 1


def cmd_stop(args: argparse.Namespace) -> int:
    try:
        r = httpx.post(f"http://127.0.0.1:{args.port}/shutdown", timeout=5.0)
        r.raise_for_status()
        emit_ok({"stopped": True})
        return 0
    except httpx.ConnectError:
        emit_error(f"browser-api not running on port {args.port}.")
        return 1
    except httpx.HTTPStatusError as exc:
        emit_error(f"Server error: {exc.response.text}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
