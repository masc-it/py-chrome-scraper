"""Chrome CDP -> text-node extraction for html-to-md."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from chrome_scraper.browser_api.client import BrowserAPIClient
from chrome_scraper.web_scrapers.base import BrowserTool

_EXTRACT_JS = (Path(__file__).parent / "extract.js").read_text(encoding="utf-8")

_SCROLL_JS = r"""
(async () => {
  const step = Math.max(400, window.innerHeight);
  const MAX_PASSES = 50;
  const STALL_LIMIT = 3;
  let y = 0;
  let stall_count = 0;
  let last_height = 0;
  for (let i = 0; i < MAX_PASSES; i++) {
    window.scrollTo(0, y);
    await new Promise(r => setTimeout(r, 120));
    const h = document.documentElement.scrollHeight;
    if (h === last_height) {
      stall_count++;
      if (stall_count >= STALL_LIMIT) break;
    } else {
      stall_count = 0;
      last_height = h;
    }
    y += step;
    if (y >= h) break;
  }
  window.scrollTo(0, 0);
  await new Promise(r => setTimeout(r, 200));
  return true;
})()
"""


def scroll_full_page(browser: BrowserTool, tab: str, *, timeout: float) -> None:
    """Scroll the active tab top-to-bottom to trigger lazy-loaded content."""
    browser.eval_js(tab_ref=tab, expression=_SCROLL_JS, timeout=timeout)


def extract_current_page(
    browser: BrowserTool, tab: str, *, timeout: float, scroll: bool = True
) -> dict:
    """Extract text-node payload from whatever page is already open in the tab."""
    if scroll:
        try:
            scroll_full_page(browser, tab, timeout=timeout)
        except Exception:
            pass
    return browser.eval_js(tab_ref=tab, expression=_EXTRACT_JS, timeout=timeout) or {}


def extract_page(
    url: str,
    browser: BrowserTool,
    *,
    tab_ref: str,
    timeout: float = 30.0,
    scroll: bool = True,
    verbose: bool = False,
) -> dict[str, Any]:
    """Navigate to ``url`` in an already-open tab and return the text-node payload.

    Tab lifecycle is owned by the caller — open via ``browser.tab()`` before
    calling, close after.
    """
    browser.navigate(tab_ref=tab_ref, url=url, timeout=timeout, wait_until="load")
    time.sleep(0.5)
    if scroll:
        try:
            scroll_full_page(browser, tab_ref, timeout=timeout)
        except Exception as exc:
            if verbose:
                print(f"scroll warning: {exc}", file=sys.stderr)
    payload = (
        browser.eval_js(tab_ref=tab_ref, expression=_EXTRACT_JS, timeout=timeout)
        or {}
    )
    return payload


def extract_from_url(
    url: str,
    *,
    browser: BrowserTool | None = None,
    browser_api_url: str | None = None,
    timeout: float = 30.0,
    tab_label: str = "html-to-md",
    scroll: bool = True,
    verbose: bool = False,
) -> dict[str, Any]:
    """Render a URL to text-node payload via browser-api.

    Requires a running browser-api server (``uv run browser-api``).

    If ``browser`` is provided, delegates to ``extract_page`` directly.
    Otherwise creates a ``BrowserAPIClient`` connected to
    ``browser_api_url`` (default: http://localhost:9333).
    """
    if browser is not None:
        return extract_page(
            url, browser, tab_ref=tab_label, timeout=timeout, scroll=scroll, verbose=verbose
        )

    client = BrowserAPIClient(base_url=browser_api_url, timeout=timeout)
    with client.tab(tab_label):
        return extract_page(
            url, client, tab_ref=tab_label, timeout=timeout, scroll=scroll, verbose=verbose
        )
