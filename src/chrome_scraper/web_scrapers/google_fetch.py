"""Search Google and download each result as raw HTML + markdown.

Single browser session: scrape results → click each link → dump outerHTML → back.
Supports multi-page scraping and result filtering.
The caller owns the browser lifecycle; this module only drives the tab.

**Site-specific plugins**

Certain domains (e.g. www.instagram.com, www.youtube.com) need a different
fetch flow because their SPA intercepts anchor clicks or their DOM doesn't
match the generic readyState=complete wait.  Host dispatch lives in
``web_scrapers.url_dispatch`` so single-URL tools use the same handlers.
"""

from __future__ import annotations

import random
import re
import sys
import time
from pathlib import Path
from typing import TypedDict

from chrome_scraper.web_scrapers._fetch_common import (
    dump_html_and_md,
    dump_pdf_and_md,
    spam_back_until,
)
from chrome_scraper.web_scrapers.base import (
    BrowserTool,
    ScrapedDocument,
    WebScraperError,
    get_href,
    save_index,
    wait_for,
)
from chrome_scraper.web_scrapers.google_search import GoogleSearchResultsScraper
from chrome_scraper.web_scrapers.url_dispatch import (
    fetch_url_to_file,
    has_site_handler,
)


class FetchedPage(TypedDict):
    title: str
    url: str
    html_file: str
    md_file: str


def fetch_query(
    *,
    browser: BrowserTool,
    tab_ref: str,
    query: str,
    out_dir: Path,
    timeout: float,
    poll_interval: float,
    num_pages: int = 1,
    max_results: int | None = None,
    allowed_hosts: set[str] | None = None,
    results_per_page: int = 10,
) -> list[FetchedPage]:
    """Search Google, then visit and dump each result HTML.

    Args:
        num_pages: How many Google result pages to scrape (default 1).
        max_results: Stop after this many results total (default: all).
        allowed_hosts: Only fetch results from these hostnames.
        results_per_page: Results per Google page (default 10).

    Saves results.json (title+url index) and <N>-<slug>.html per page.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    scraper = GoogleSearchResultsScraper(
        query=query,
        results_per_page=results_per_page,
        allowed_hosts=allowed_hosts,
    )

    all_results: list[ScrapedDocument] = []
    for page_num in range(1, num_pages + 1):
        if page_num == 1:
            scraper.open_start_page(browser=browser, tab_ref=tab_ref, timeout=timeout)
        else:
            scraper.go_to_page(
                browser=browser,
                tab_ref=tab_ref,
                page=page_num,
                timeout=timeout,
                poll_interval=poll_interval,
            )

        page_results = scraper.scrape_current_page(
            browser=browser,
            tab_ref=tab_ref,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        all_results.extend(page_results)

        if max_results and len(all_results) >= max_results:
            all_results = all_results[:max_results]
            break

    save_index(all_results, out_dir / "results.json")

    existing = len(list(out_dir.glob("*.md")))

    fetched: list[FetchedPage] = []
    for idx, (i, item) in enumerate(
        zip(range(existing + 1, existing + 1 + len(all_results)), all_results)
    ):
        if max_results and len(fetched) >= max_results:
            break
        if idx > 0:
            time.sleep(random.uniform(0.1, 0.5))
        url, title = item["url"], item["title"]
        html_path = out_dir / f"{i:02d}-{_slugify(title)}.html"
        try:
            _fetch_one(
                browser=browser,
                tab_ref=tab_ref,
                url=url,
                title=title,
                position=i,
                html_path=html_path,
                timeout=timeout,
                poll_interval=poll_interval,
            )
            source_path = (
                html_path.with_suffix(".pdf")
                if html_path.with_suffix(".pdf").exists()
                else html_path
            )
            fetched.append(
                {
                    "title": title,
                    "url": url,
                    "html_file": str(source_path),
                    "md_file": str(html_path.with_suffix(".md")),
                }
            )
        except WebScraperError as exc:
            print(f"[google-fetch] skip {url}: {exc}", file=sys.stderr, flush=True)

    return fetched


# ---------------------------------------------------------------------------
# Core: fetch-one with site-specific plugin dispatch
# ---------------------------------------------------------------------------


def _fetch_one(
    *,
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    title: str,
    position: int,
    html_path: Path,
    timeout: float,
    poll_interval: float,
) -> None:
    """Fetch a single result URL and dump its HTML + markdown.

    Dispatches to a registered site handler if the result domain has one.
    Otherwise uses the generic click-dump-back flow.
    """
    if has_site_handler(url):
        fetch_url_to_file(
            browser=browser,
            tab_ref=tab_ref,
            url=url,
            title=title,
            position=position,
            html_path=html_path,
            timeout=timeout,
            poll_interval=poll_interval,
        )
    else:
        # ── Generic flow: click from Google results → dump ──
        google_url = get_href(browser, tab_ref, timeout)

        result = browser.eval_js(
            tab_ref=tab_ref,
            expression=_click_result_script(url),
            timeout=timeout,
        )
        if not isinstance(result, dict) or not result.get("clicked"):
            raise WebScraperError(
                f"Anchor not found for {url}. JS returned: {result!r}"
            )

        wait_for(
            timeout=timeout,
            poll_interval=poll_interval,
            task=lambda: _href_changed(browser, tab_ref, timeout, from_url=google_url),
            error_message=f"Navigation never left Google after clicking {url}",
        )

        wait_for(
            timeout=timeout,
            poll_interval=poll_interval,
            task=lambda: _page_ready(browser, tab_ref, timeout),
            error_message=f"Page never reached readyState=complete for {url}",
        )

        meta_desc = (
            browser.eval_js(
                tab_ref=tab_ref,
                expression="document.querySelector('meta[name=\"description\"]')?.content || ''",
                timeout=timeout,
            )
            or ""
        )
        frontmatter = f"---\ntitle: {title!r}\nurl: {url}\nposition: {position}\n"
        if meta_desc:
            frontmatter += f"description: {meta_desc!r}\n"
        frontmatter += "---\n\n"

        if not dump_pdf_and_md(
            browser=browser,
            tab_ref=tab_ref,
            url=url,
            md_body=frontmatter,
            html_path=html_path,
            timeout=timeout,
        ):
            dump_html_and_md(
                browser=browser,
                tab_ref=tab_ref,
                url=url,
                md_body=frontmatter,
                html_path=html_path,
                timeout=timeout,
            )

    # ── Both handler and generic flow: navigate back to Google ──
    spam_back_until(
        browser=browser,
        tab_ref=tab_ref,
        timeout=timeout,
        poll_interval=poll_interval,
        predicate=lambda: _on_google_and_ready(browser, tab_ref, timeout),
        error_message="Timed out waiting to return to Google results",
    )


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _click_result_script(url: str) -> str:
    escaped = url.replace("\\", "\\\\").replace('"', '\\"')
    return f"""
(() => {{
  const target = "{escaped}";
  const base = (href) => {{ try {{ const u = new URL(href); return u.origin + u.pathname; }} catch {{ return href; }} }};
  const anchor = Array.from(document.querySelectorAll("#search a[href], #rso a[href]"))
    .find(a => a.href === target || base(a.href) === base(target));
  if (!anchor) return {{ clicked: false, url: target }};
  anchor.removeAttribute("target");
  anchor.scrollIntoView({{ block: "center" }});
  anchor.click();
  return {{ clicked: true, url: target }};
}})()
""".strip()


def _href_changed(
    browser: BrowserTool, tab_ref: str, timeout: float, *, from_url: str
) -> bool:
    href = get_href(browser, tab_ref, timeout)
    return bool(href) and href != from_url


def _page_ready(browser: BrowserTool, tab_ref: str, timeout: float) -> bool:
    try:
        state = browser.eval_js(
            tab_ref=tab_ref, expression="document.readyState", timeout=timeout
        )
        return state == "complete"
    except WebScraperError:
        return False


def _on_google_and_ready(browser: BrowserTool, tab_ref: str, timeout: float) -> bool:
    href = get_href(browser, tab_ref, timeout)
    return "google.com/search" in href and _page_ready(browser, tab_ref, timeout)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug[:80] or "page"
