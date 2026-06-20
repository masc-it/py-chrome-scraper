"""Shared types for URL-specific page preparation and rendering.

A site-specific preparer owns only the work that must happen before generic
HTML-to-markdown extraction: navigate to a canonical URL, expand hidden content,
wait for SPA render, and optionally provide markdown that should be prepended to
that page's rendered body (for example a YouTube transcript extracted from a
virtualized panel).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from chrome_scraper.web_scrapers.base import BrowserTool


@dataclass(frozen=True, slots=True)
class PreparedPage:
    """A browser tab that has been prepared for generic markdown extraction."""

    requested_url: str
    page_url: str
    title: str = ""
    markdown_prefix: str = ""
    handler_name: str = "generic"


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """Markdown rendered from a prepared browser tab."""

    requested_url: str
    page_url: str
    title: str
    markdown: str
    extract_count: int
    handler_name: str


class PagePreparer(Protocol):
    """Callable contract implemented by bundled and third-party URL handlers."""

    def __call__(
        self,
        browser: BrowserTool,
        tab_ref: str,
        url: str,
        *,
        timeout: float,
        poll_interval: float,
    ) -> PreparedPage: ...
