from __future__ import annotations

import argparse
import json
import random
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Protocol, TypedDict


class WebScraperError(RuntimeError):
    """Raised when a scraper cannot complete its work."""


# Map from our cross-backend wait_until vocabulary to Playwright's.
# Defined once here so both the browser-api server and client agree on the
# same mapping without duplication.
WAIT_UNTIL_MAP: dict[str, str] = {
    "load": "load",
    "domcontentloaded": "domcontentloaded",
    "networkidle": "networkidle",
    "none": "commit",
}


def _build_headless_launch_kwargs(
    channel: str, chrome_path: str | None
) -> dict[str, Any]:
    """Common launch kwargs for a throwaway headless Chromium probe."""
    kw: dict[str, Any] = {"channel": channel, "headless": True}
    if chrome_path:
        kw["executable_path"] = chrome_path
    return kw


def probe_chrome_identity_sync(
    pw: Any, *, channel: str, chrome_path: str | None
) -> str:
    """Launch throwaway headless Chromium to read real UA (sync)."""
    browser = pw.chromium.launch(**_build_headless_launch_kwargs(channel, chrome_path))
    try:
        page = browser.new_page()
        ua = page.evaluate("navigator.userAgent")
        return str(ua).replace("HeadlessChrome", "Chrome")
    finally:
        try:
            browser.close()
        except Exception:
            pass


async def probe_chrome_identity_async(
    pw: Any, *, channel: str, chrome_path: str | None
) -> str:
    """Launch throwaway headless Chromium to read real UA (async)."""
    browser = await pw.chromium.launch(
        **_build_headless_launch_kwargs(channel, chrome_path)
    )
    try:
        page = await browser.new_page()
        ua = await page.evaluate("navigator.userAgent")
        return str(ua).replace("HeadlessChrome", "Chrome")
    finally:
        await browser.close()


def wait_for(
    *,
    timeout: float,
    poll_interval: float,
    task: Callable[[], Any],
    error_message: str,
) -> Any:
    """Poll ``task`` until it returns a truthy value or ``timeout`` elapses.

    Raises ``WebScraperError`` on timeout. Free function so callers outside the
    ``WebScraper`` hierarchy (e.g. ``html_to_md.extract``) can reuse the same
    polling pattern without inheriting from a base class.
    """
    deadline = time.monotonic() + timeout
    last_value: Any = None
    while time.monotonic() < deadline:
        last_value = task()
        if last_value:
            return last_value
        time.sleep(poll_interval)
    raise WebScraperError(
        error_message
        if last_value is None
        else f"{error_message} Last value: {last_value!r}"
    )


def get_href(browser: BrowserTool, tab_ref: str, timeout: float) -> str:
    """Return current tab URL, or "" on error."""
    try:
        href = browser.eval_js(
            tab_ref=tab_ref, expression="location.href", timeout=timeout
        )
        return href if isinstance(href, str) else ""
    except WebScraperError:
        return ""


def save_index(data: list[Any], path: Path) -> None:
    """Write a JSON index file."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class BrowserTab(TypedDict, total=False):
    target_id: str
    title: str
    url: str
    label: str | None
    websocket_url: str | None


class ScrapedDocument(TypedDict):
    title: str
    url: str


class BrowserTool(Protocol):
    def attach(
        self,
        *,
        port: int | None = None,
        user_data_dir: str | None = None,
        timeout: float,
    ) -> dict[str, Any]: ...

    def launch(
        self,
        *,
        port: int | None = None,
        user_data_dir: str | None = None,
        chrome_path: str | None = None,
        headless: bool = False,
        timeout: float,
    ) -> dict[str, Any]: ...

    def stop(self, *, timeout: float = 5.0) -> dict[str, Any]: ...

    def list_tabs(self) -> list[BrowserTab]: ...

    def open_tab(
        self, url: str = "about:blank", *, label: str | None = None
    ) -> BrowserTab: ...

    def activate_tab(self, tab_ref: str) -> dict[str, Any]: ...

    def navigate(
        self, *, tab_ref: str, url: str, timeout: float, wait_until: str = "load"
    ) -> dict[str, Any]: ...

    def eval_js(self, *, tab_ref: str, expression: str, timeout: float) -> Any: ...

    def eval_js_file(
        self, *, tab_ref: str, script_path: str, timeout: float
    ) -> Any: ...

    def html(self, *, tab_ref: str, timeout: float) -> str: ...

    def document(
        self, *, tab_ref: str, timeout: float, url: str | None = None
    ) -> bytes: ...


class WebScraper(ABC):
    name: str = "web-scraper"
    description: str = ""
    default_max_page: int = 5

    def run(
        self,
        *,
        browser: BrowserTool,
        tab_ref: str,
        max_page: int,
        timeout: float,
        poll_interval: float,
    ) -> list[ScrapedDocument]:
        if max_page < 1:
            raise WebScraperError("max_page must be at least 1.")

        self.open_start_page(browser=browser, tab_ref=tab_ref, timeout=timeout)

        merged: dict[str, ScrapedDocument] = {}
        for page in range(1, max_page + 1):
            if page > 1:
                time.sleep(random.uniform(0.1, 0.5))
                self.go_to_page(
                    browser=browser,
                    tab_ref=tab_ref,
                    page=page,
                    timeout=timeout,
                    poll_interval=poll_interval,
                )
            for item in self.scrape_current_page(
                browser=browser,
                tab_ref=tab_ref,
                timeout=timeout,
                poll_interval=poll_interval,
            ):
                merged.setdefault(item["url"], item)
        return list(merged.values())

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Register scraper-specific CLI arguments on the given subparser."""

    @classmethod
    @abstractmethod
    def from_args(cls, args: argparse.Namespace) -> "WebScraper":
        """Build an instance from CLI args produced by `add_arguments`."""
        raise NotImplementedError

    def context(self) -> dict[str, Any]:
        """Scraper-specific metadata to merge into the runner's JSON output."""
        return {}

    def post_process(self, results: list[ScrapedDocument]) -> list[ScrapedDocument]:
        """Hook called by the runner after scraping. Default: identity."""
        return results

    @abstractmethod
    def open_start_page(
        self, *, browser: BrowserTool, tab_ref: str, timeout: float
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def go_to_page(
        self,
        *,
        browser: BrowserTool,
        tab_ref: str,
        page: int,
        timeout: float,
        poll_interval: float,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def scrape_current_page(
        self,
        *,
        browser: BrowserTool,
        tab_ref: str,
        timeout: float,
        poll_interval: float,
    ) -> list[ScrapedDocument]:
        raise NotImplementedError

    def wait_for(
        self,
        *,
        timeout: float,
        poll_interval: float,
        task: Callable[[], Any],
        error_message: str,
    ) -> Any:
        return wait_for(
            timeout=timeout,
            poll_interval=poll_interval,
            task=task,
            error_message=error_message,
        )
