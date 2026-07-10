"""URL-level dispatch for site-specific preparation plus generic rendering.

The browser-api server intentionally stays low-level.  This module is the
client-side routing layer used by CLIs that want a single URL rendered as
markdown: known hosts get a site preparer first (YouTube transcript expansion,
Instagram SPA wait, x.com tweet wait, ...), then every page flows through the
same layout-preserving HTML-to-markdown extractor.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chrome_scraper.html_to_md.extract import extract_current_page, scroll_full_page
from chrome_scraper.html_to_md.render import render_page
from chrome_scraper.pdf_to_text import is_pdf_page, pdf_bytes_to_text
from chrome_scraper.web_scrapers.base import BrowserTool, WebScraperError, get_href
from chrome_scraper.web_scrapers.url_page import (
    PagePreparer,
    PreparedPage,
    RenderedPage,
)

FetchToFile = Callable[
    [BrowserTool, str, str, str, int, Path, float, float],
    None,
]
UrlPredicate = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class SiteHandler:
    """Registered behavior for one or more hostnames."""

    name: str
    prepare: PagePreparer
    fetch_to_file: FetchToFile | None = None
    url_predicate: UrlPredicate | None = None

    def matches(self, url: str) -> bool:
        return self.url_predicate(url) if self.url_predicate is not None else True


_SITE_HANDLERS: dict[str, list[SiteHandler]] = {}


def register_site_handler(
    *,
    name: str,
    domains: tuple[str, ...],
    prepare: PagePreparer,
    fetch_to_file: FetchToFile | None = None,
    url_predicate: UrlPredicate | None = None,
) -> None:
    """Register a site-specific URL handler for exact host matches."""
    handler = SiteHandler(
        name=name,
        prepare=prepare,
        fetch_to_file=fetch_to_file,
        url_predicate=url_predicate,
    )
    for domain in domains:
        host = _normalise_host(domain)
        if host:
            _SITE_HANDLERS.setdefault(host, []).append(handler)


def site_handler_for_url(url: str) -> SiteHandler | None:
    """Return the registered handler for ``url``'s host/path, if any."""
    host = _host_from_url(url)
    for handler in reversed(_SITE_HANDLERS.get(host, [])):
        if handler.matches(url):
            return handler
    return None


def has_site_handler(url: str) -> bool:
    """Whether a URL has a site-specific handler registered."""
    return site_handler_for_url(url) is not None


def prepare_url(
    *,
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    timeout: float,
    poll_interval: float = 0.5,
) -> PreparedPage:
    """Navigate and prepare ``url`` for markdown extraction.

    Known hosts are delegated to their registered preparer. Unknown hosts use a
    fast generic navigation + title read.
    """
    handler = site_handler_for_url(url)
    if handler is not None:
        return handler.prepare(
            browser,
            tab_ref,
            url,
            timeout=timeout,
            poll_interval=poll_interval,
        )
    return prepare_generic_page(
        browser,
        tab_ref,
        url,
        timeout=timeout,
        poll_interval=poll_interval,
    )


def prepare_generic_page(
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    *,
    timeout: float,
    poll_interval: float = 0.5,
) -> PreparedPage:
    """Generic navigation for hosts without a custom handler."""
    del poll_interval  # kept for signature compatibility with PagePreparer
    browser.navigate(tab_ref=tab_ref, url=url, timeout=timeout, wait_until="load")
    # Give hydrated pages a short chance to paint before layout extraction.
    time.sleep(0.5)
    return PreparedPage(
        requested_url=url,
        page_url=get_href(browser, tab_ref, timeout) or url,
        title=_read_page_title(browser, tab_ref, timeout),
        handler_name="generic",
    )


def render_url_as_markdown(
    *,
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    timeout: float,
    poll_interval: float = 0.5,
    scroll: bool = True,
    markdown_prefix: str = "",
    include_title_heading: bool = True,
    verbose: bool = False,
) -> RenderedPage:
    """Prepare a URL, extract its rendered text nodes, and return markdown."""
    prepared = prepare_url(
        browser=browser,
        tab_ref=tab_ref,
        url=url,
        timeout=timeout,
        poll_interval=poll_interval,
    )
    if is_pdf_page(
        browser,
        tab_ref,
        url=prepared.page_url or prepared.requested_url,
        timeout=timeout,
    ):
        data = browser.document(
            tab_ref=tab_ref,
            timeout=timeout,
            url=prepared.page_url or prepared.requested_url,
        )
        text = pdf_bytes_to_text(data)
        parts: list[str] = []
        if include_title_heading and prepared.title:
            parts.append(f"# {prepared.title}\n\n")
        parts.append(_markdown_block(markdown_prefix))
        parts.append(_markdown_block(prepared.markdown_prefix))
        parts.append(text)
        return RenderedPage(
            requested_url=prepared.requested_url,
            page_url=prepared.page_url,
            title=prepared.title,
            markdown="".join(parts),
            extract_count=0,
            handler_name="pdf",
            source_bytes=data,
        )
    return render_prepared_page(
        browser=browser,
        tab_ref=tab_ref,
        prepared=prepared,
        timeout=timeout,
        scroll=scroll,
        markdown_prefix=markdown_prefix,
        include_title_heading=include_title_heading,
        verbose=verbose,
    )


def render_prepared_page(
    *,
    browser: BrowserTool,
    tab_ref: str,
    prepared: PreparedPage,
    timeout: float,
    scroll: bool = True,
    markdown_prefix: str = "",
    include_title_heading: bool = True,
    verbose: bool = False,
) -> RenderedPage:
    """Extract and render markdown from the already-prepared tab."""
    payload = extract_current_page(browser, tab_ref, timeout=timeout, scroll=scroll)
    return render_prepared_payload(
        prepared=prepared,
        payload=payload,
        markdown_prefix=markdown_prefix,
        include_title_heading=include_title_heading,
        verbose=verbose,
    )


def dump_prepared_page(
    *,
    browser: BrowserTool,
    tab_ref: str,
    prepared: PreparedPage,
    html_path: Path,
    timeout: float,
    scroll: bool = True,
    markdown_prefix: str = "",
    include_title_heading: bool = False,
    verbose: bool = False,
) -> RenderedPage:
    """Write raw HTML and rendered markdown for an already-prepared tab."""
    html_path.parent.mkdir(parents=True, exist_ok=True)

    if scroll:
        try:
            scroll_full_page(browser, tab_ref, timeout=timeout)
        except WebScraperError:
            pass

    html = browser.html(tab_ref=tab_ref, timeout=timeout)
    if not html:
        raise WebScraperError(
            f"Empty HTML from {prepared.page_url or prepared.requested_url}"
        )
    html_path.write_text(html, encoding="utf-8")

    payload = extract_current_page(browser, tab_ref, timeout=timeout, scroll=False)
    rendered = render_prepared_payload(
        prepared=prepared,
        payload=payload,
        markdown_prefix=markdown_prefix,
        include_title_heading=include_title_heading,
        verbose=verbose,
    )
    html_path.with_suffix(".md").write_text(rendered.markdown, encoding="utf-8")
    return rendered


def fetch_url_to_file(
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
    """Dispatch a known URL to its file-writing handler.

    This keeps google-fetch compatible with richer site fetchers (notably x.com,
    which may write quoted tweets as sibling files) while sharing the same
    hostname registry used by single-URL rendering.
    """
    handler = site_handler_for_url(url)
    if handler is None:
        raise WebScraperError(f"No site handler registered for {url}")

    if handler.fetch_to_file is not None:
        handler.fetch_to_file(
            browser,
            tab_ref,
            url,
            title,
            position,
            html_path,
            timeout,
            poll_interval,
        )
        return

    prepared = handler.prepare(
        browser,
        tab_ref,
        url,
        timeout=timeout,
        poll_interval=poll_interval,
    )
    dump_prepared_page(
        browser=browser,
        tab_ref=tab_ref,
        prepared=prepared,
        html_path=html_path,
        timeout=timeout,
        markdown_prefix=_frontmatter(title=title, url=url, position=position),
        include_title_heading=False,
    )


def render_prepared_payload(
    *,
    prepared: PreparedPage,
    payload: dict,
    markdown_prefix: str = "",
    include_title_heading: bool = True,
    verbose: bool = False,
) -> RenderedPage:
    """Render an extracted payload with caller and handler markdown prefixes."""
    items = payload.get("items") or []
    viewport = payload.get("viewport") or {}
    page_width = float(viewport.get("scroll_w") or viewport.get("w") or 1280)
    title = (prepared.title or payload.get("title") or "").strip()
    page_url = (
        payload.get("url") or prepared.page_url or prepared.requested_url
    ).strip()

    parts: list[str] = []
    if include_title_heading and title:
        parts.append(f"# {title}\n\n")
    parts.append(_markdown_block(markdown_prefix))
    parts.append(_markdown_block(prepared.markdown_prefix))
    parts.append(render_page(items, page_width, verbose=verbose))

    return RenderedPage(
        requested_url=prepared.requested_url,
        page_url=page_url,
        title=title,
        markdown="".join(parts),
        extract_count=len(items),
        handler_name=prepared.handler_name,
    )


def _read_page_title(browser: BrowserTool, tab_ref: str, timeout: float) -> str:
    try:
        title = browser.eval_js(
            tab_ref=tab_ref,
            expression="document.title || document.querySelector('h1')?.textContent || ''",
            timeout=timeout,
        )
    except WebScraperError:
        return ""
    return title.strip() if isinstance(title, str) else ""


def _frontmatter(*, title: str, url: str, position: int) -> str:
    return f"---\ntitle: {title!r}\nurl: {url}\nposition: {position}\n---\n\n"


def _markdown_block(markdown: str) -> str:
    if not markdown:
        return ""
    return markdown if markdown.endswith("\n\n") else markdown.rstrip("\n") + "\n\n"


def _host_from_url(url: str) -> str:
    return _normalise_host(urllib.parse.urlparse(url).hostname or "")


def _normalise_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _is_youtube_video_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = _normalise_host(parsed.hostname or "")
    if host == "youtu.be":
        return bool(parsed.path.strip("/"))
    return parsed.path == "/watch" or parsed.path.startswith("/shorts/")


def _is_instagram_post_url(url: str) -> bool:
    path = urllib.parse.urlparse(url).path
    return path.startswith(("/p/", "/reel/", "/reels/"))


def _is_xcom_status_url(url: str) -> bool:
    return "/status/" in urllib.parse.urlparse(url).path


def _register_bundled_handlers() -> None:
    # Imports are deliberately local: the registry is shared, but the low-level
    # browser-api server and generic html-to-md modules do not need to import
    # heavyweight site scrapers unless URL dispatch is used.
    from chrome_scraper.web_scrapers import instagram_fetch, xcom_fetch, youtube_fetch

    register_site_handler(
        name="instagram",
        domains=("instagram.com", "www.instagram.com"),
        prepare=instagram_fetch.prepare_post_page,
        fetch_to_file=instagram_fetch.fetch_post_url,
        url_predicate=_is_instagram_post_url,
    )
    register_site_handler(
        name="youtube",
        domains=("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"),
        prepare=youtube_fetch.prepare_video_page,
        fetch_to_file=youtube_fetch.fetch_post_url,
        url_predicate=_is_youtube_video_url,
    )
    register_site_handler(
        name="xcom",
        domains=("x.com", "www.x.com"),
        prepare=xcom_fetch.prepare_post_page,
        fetch_to_file=xcom_fetch.fetch_post_url,
        url_predicate=_is_xcom_status_url,
    )


_register_bundled_handlers()
