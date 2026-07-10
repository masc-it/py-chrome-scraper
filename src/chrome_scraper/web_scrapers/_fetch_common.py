"""Shared helpers for the fetch-one pipeline used by google_fetch and xcom_fetch.

Both scrapers follow the same 7-step pattern (click → wait nav → wait ready →
scroll → dump HTML → extract MD → back), differing only in the click script,
nav-wait condition, frontmatter, and back-check predicate.  This module
extracts steps 4–6 (scroll + dump + extract) and step 7 (back-spam) so each
scraper only needs its unique click/nav/frontmatter logic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from chrome_scraper.html_to_md.extract import extract_current_page, scroll_full_page
from chrome_scraper.html_to_md.render import render_page
from chrome_scraper.pdf_to_text import is_pdf_page, pdf_bytes_to_text
from chrome_scraper.web_scrapers.base import BrowserTool, WebScraperError


def dump_html_and_md(
    *,
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    md_body: str,
    html_path: Path,
    timeout: float,
) -> None:
    """Scroll, dump outerHTML, extract items, render + write markdown.

    Writes both ``html_path`` (raw outerHTML) and ``html_path.with_suffix('.md')``
    (``md_body`` + rendered items).  ``md_body`` is the per-scraper frontmatter
    (YAML + extra metadata) that precedes the rendered page content.
    """
    try:
        scroll_full_page(browser, tab_ref, timeout=timeout)
    except WebScraperError:
        pass

    html = browser.html(tab_ref=tab_ref, timeout=timeout)
    if not html:
        raise WebScraperError(f"Empty HTML from {url}")
    html_path.write_text(html, encoding="utf-8")

    payload = extract_current_page(browser, tab_ref, timeout=timeout, scroll=False)
    items = payload.get("items") or []
    page_width = float((payload.get("viewport") or {}).get("scroll_w") or 1280)
    html_path.with_suffix(".md").write_text(
        md_body + render_page(items, page_width), encoding="utf-8"
    )


def dump_pdf_and_md(
    *,
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    md_body: str,
    html_path: Path,
    timeout: float,
) -> bool:
    """Save a PDF and its extracted text, returning whether the page was a PDF."""
    if not is_pdf_page(browser, tab_ref, url=url, timeout=timeout):
        return False
    data = browser.document(tab_ref=tab_ref, timeout=timeout, url=url)
    text = pdf_bytes_to_text(data)
    pdf_path = html_path.with_suffix(".pdf")
    pdf_path.write_bytes(data)
    html_path.with_suffix(".md").write_text(md_body + text, encoding="utf-8")
    return True


def spam_back_until(
    *,
    browser: BrowserTool,
    tab_ref: str,
    timeout: float,
    poll_interval: float,
    predicate: Callable[[], bool],
    error_message: str,
) -> None:
    """Spam ``history.back()`` until *predicate* returns ``True`` or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        browser.eval_js(tab_ref=tab_ref, expression="history.back()", timeout=timeout)
        time.sleep(poll_interval)
        if predicate():
            break
    else:
        raise WebScraperError(error_message)
