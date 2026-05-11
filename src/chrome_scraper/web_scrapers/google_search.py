from __future__ import annotations

import argparse
from importlib import resources
from typing import Any

from chrome_scraper.web_scrapers.base import (
    BrowserTool,
    ScrapedDocument,
    WebScraper,
    WebScraperError,
)


class GoogleSearchResultsScraper(WebScraper):
    name = "google"
    description = "Scrape Google Search result cards by extracting title and URL pairs."

    def __init__(
        self,
        *,
        query: str,
        results_per_page: int = 10,
        allowed_hosts: set[str] | None = None,
    ) -> None:
        self.query = query
        self.results_per_page = results_per_page
        self.allowed_hosts = {host.lower() for host in (allowed_hosts or set())}
        self.script_source = (
            resources.files("chrome_scraper.web_scrapers.scripts")
            .joinpath("google_search_results.js")
            .read_text(encoding="utf-8")
        )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("query", help="Google search query string.")
        parser.add_argument(
            "--results-per-page",
            type=int,
            default=10,
            help="Number of results per Google page.",
        )
        parser.add_argument(
            "--allowed-hosts",
            nargs="*",
            help="Only keep results whose hostname is in this list.",
        )

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "GoogleSearchResultsScraper":
        return cls(
            query=args.query,
            results_per_page=args.results_per_page,
            allowed_hosts=set(args.allowed_hosts) if args.allowed_hosts else None,
        )

    def context(self) -> dict[str, Any]:
        return {"query": self.query}

    def open_start_page(
        self, *, browser: BrowserTool, tab_ref: str, timeout: float
    ) -> None:
        browser.navigate(
            tab_ref=tab_ref,
            url=self.build_search_url(page=1),
            timeout=timeout,
            wait_until="load",
        )

    def go_to_page(
        self,
        *,
        browser: BrowserTool,
        tab_ref: str,
        page: int,
        timeout: float,
        poll_interval: float,
    ) -> None:
        if page <= 1:
            return
        previous_url = self.get_location_href(
            browser=browser, tab_ref=tab_ref, timeout=timeout
        )
        result = browser.eval_js(
            tab_ref=tab_ref,
            expression=self.build_click_page_script(page),
            timeout=timeout,
        )
        if not isinstance(result, dict) or not result.get("clicked"):
            raise WebScraperError(
                f"Could not click Google footer page number {page}. Payload: {result!r}"
            )

        self.wait_for(
            timeout=timeout,
            poll_interval=poll_interval,
            task=lambda: self._page_has_changed(
                browser=browser,
                tab_ref=tab_ref,
                page=page,
                previous_url=previous_url,
                timeout=timeout,
            ),
            error_message=f"Timed out waiting for Google to navigate to page {page} after clicking the footer.",
        )

    def scrape_current_page(
        self,
        *,
        browser: BrowserTool,
        tab_ref: str,
        timeout: float,
        poll_interval: float,
    ) -> list[ScrapedDocument]:
        all_results = self.wait_for(
            timeout=timeout,
            poll_interval=poll_interval,
            task=lambda: self._scrape_once(
                browser=browser, tab_ref=tab_ref, timeout=timeout
            ),
            error_message="Scraper did not return any Google results before timing out.",
        )
        return self.filter_results(all_results)

    def _scrape_once(
        self, *, browser: BrowserTool, tab_ref: str, timeout: float
    ) -> list[ScrapedDocument]:
        results = browser.eval_js(
            tab_ref=tab_ref,
            expression=self.script_source,
            timeout=timeout,
        )
        if not isinstance(results, list):
            return []
        return self.normalize_results(results)

    def filter_results(self, results: list[ScrapedDocument]) -> list[ScrapedDocument]:
        if not self.allowed_hosts:
            return results
        filtered: list[ScrapedDocument] = []
        for item in results:
            hostname = self.extract_hostname(item["url"])
            if hostname in self.allowed_hosts:
                filtered.append(item)
        return filtered

    def normalize_results(self, results: list[Any]) -> list[ScrapedDocument]:
        normalized: list[ScrapedDocument] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            url = item.get("url")
            if not isinstance(title, str) or not title.strip():
                continue
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            normalized.append({"title": title.strip(), "url": url.strip()})
        return normalized

    def build_search_url(self, *, page: int) -> str:
        if page < 1:
            raise WebScraperError("Google page numbers start at 1.")
        start = (page - 1) * self.results_per_page
        query = self.query.replace(" ", "+")
        return f"https://www.google.com/search?q={query}&hl=en&gl=us&num={self.results_per_page}&start={start}"

    def get_location_href(
        self, *, browser: BrowserTool, tab_ref: str, timeout: float
    ) -> str:
        href = browser.eval_js(
            tab_ref=tab_ref,
            expression="location.href",
            timeout=timeout,
        )
        if not isinstance(href, str) or not href:
            raise WebScraperError(
                "Could not read location.href from the Google results tab."
            )
        return href

    def _page_has_changed(
        self,
        *,
        browser: BrowserTool,
        tab_ref: str,
        page: int,
        previous_url: str,
        timeout: float,
    ) -> bool:
        try:
            href = self.get_location_href(
                browser=browser, tab_ref=tab_ref, timeout=timeout
            )
        except WebScraperError:
            return False
        return href != previous_url and self.url_matches_page(href, page)

    def url_matches_page(self, url: str, page: int) -> bool:
        expected_start = (page - 1) * self.results_per_page
        if "?" not in url:
            return page == 1
        query_string = url.split("?", 1)[1]
        params: dict[str, str] = {}
        for chunk in query_string.split("&"):
            if not chunk:
                continue
            key, _, value = chunk.partition("=")
            params[key] = value
        actual_start = int(params.get("start", "0") or "0")
        return actual_start == expected_start

    @staticmethod
    def extract_hostname(url: str) -> str:
        if "://" not in url:
            return ""
        return url.split("/")[2].lower()

    @staticmethod
    def build_click_page_script(page: int) -> str:
        return f"""
(() => {{
  const desired = {page};
  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
  const matches = (anchor) => {{
    const text = clean(anchor.textContent);
    const aria = clean(anchor.getAttribute("aria-label"));
    return text === String(desired) || aria === `Page ${{desired}}`;
  }};
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  const footerAnchors = anchors.filter((anchor) =>
    anchor.closest("#botstuff, footer, [role='navigation'], #foot")
  );
  const target = footerAnchors.find(matches) || anchors.find(matches);
  if (!target) {{
    return {{
      clicked: false,
      page: desired,
      available: footerAnchors.map((anchor) => clean(anchor.textContent)).filter(Boolean),
    }};
  }}
  target.scrollIntoView({{ block: "center", inline: "center" }});
  target.click();
  return {{
    clicked: true,
    page: desired,
    text: clean(target.textContent),
    ariaLabel: clean(target.getAttribute("aria-label")),
    href: target.href || null,
  }};
}})()
""".strip()
