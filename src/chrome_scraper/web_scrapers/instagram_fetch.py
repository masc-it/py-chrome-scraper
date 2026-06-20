"""Search Instagram and download each post / reel as raw HTML + markdown.

Single browser session, mirrors ``xcom_fetch.py``.  Instagram is a React SPA;
search is URL-driven (no native keyboard needed), but the grid uses infinite
scroll.  The persistent profile from browser-api handles login cookies.

Metadata extraction follows the xcom-fetch pattern: frontmatter is built from
grid-level data (username, caption snippet from thumbnail alt text), and the
full rendered markdown from ``dump_html_and_md`` captures everything on the
post page — no brittle DOM selectors needed.

**Plugin interface for google-fetch**

``fetch_post_url`` is the public handler that ``google_fetch.py`` calls when
a Google result points to instagram.com.  It normalises the URL, navigates
directly (SPA-safe), waits for render, and dumps HTML+MD.  Back-navigation
is handled by the caller (``google_fetch._fetch_one``).
"""

from __future__ import annotations

import random
import re
import sys
import time
from importlib import resources
from pathlib import Path
from typing import Any, TypedDict

from chrome_scraper.web_scrapers._fetch_common import dump_html_and_md
from chrome_scraper.web_scrapers.base import (
    BrowserTool,
    WebScraperError,
    get_href,
    save_index,
    wait_for,
)
from chrome_scraper.web_scrapers.url_page import PreparedPage

_BASE = "https://www.instagram.com"
_SEARCH_URL = f"{_BASE}/explore/search/keyword/?q="
_TAGS_URL = f"{_BASE}/explore/tags/"

_RESULTS_JS = (
    resources.files("chrome_scraper.web_scrapers.scripts")
    .joinpath("insta_search_results.js")
    .read_text(encoding="utf-8")
)


class FetchedPost(TypedDict):
    permalink: str
    shortcode: str
    type: str  # "p" | "reel" | "reels"
    caption: str
    username: str
    html_file: str
    md_file: str


# ---------------------------------------------------------------------------
# URL-dispatch preparer + legacy file fetcher
# ---------------------------------------------------------------------------


def prepare_post_page(
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    *,
    timeout: float,
    poll_interval: float,
) -> PreparedPage:
    """Prepare a single Instagram post/reel URL for markdown extraction."""
    clean_url = _normalise_url(url)

    browser.navigate(tab_ref=tab_ref, url=clean_url, timeout=timeout, wait_until="load")

    wait_for(
        timeout=timeout,
        poll_interval=poll_interval,
        task=lambda: _post_rendered(browser, tab_ref, timeout),
        error_message=f"Instagram page never rendered for {clean_url}",
    )
    time.sleep(1.0)  # let images / carousels paint

    return PreparedPage(
        requested_url=url,
        page_url=get_href(browser, tab_ref, timeout) or clean_url,
        title=_read_page_title(browser, tab_ref, timeout),
        handler_name="instagram",
    )


def fetch_post_url(
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    title: str,
    position: int,
    html_path: Path,
    timeout: float,
    poll_interval: float,
) -> None:
    """Fetch a single Instagram post/reel URL and write HTML + markdown."""
    prepared = prepare_post_page(
        browser,
        tab_ref,
        url,
        timeout=timeout,
        poll_interval=poll_interval,
    )
    frontmatter = f"---\ntitle: {title!r}\nurl: {url}\nposition: {position}\n---\n\n"

    dump_html_and_md(
        browser=browser,
        tab_ref=tab_ref,
        url=prepared.page_url,
        md_body=frontmatter,
        html_path=html_path,
        timeout=timeout,
    )

    # The caller (google_fetch) handles the back-navigation via spam_back_until,
    # so we leave the page as-is and return.


def _normalise_url(url: str) -> str:
    """Strip tracking params and normalise to a clean Instagram URL."""
    m = re.search(r"https?://(?:www\.)?instagram\.com/(p|reel|reels)/([^/?]+)", url)
    if m:
        return f"{_BASE}/{m.group(1)}/{m.group(2)}/"
    return url


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


# ---------------------------------------------------------------------------
# Full fetch_query (consumed by insta-fetch CLI)
# ---------------------------------------------------------------------------


def fetch_query(
    *,
    browser: BrowserTool,
    tab_ref: str,
    out_dir: Path,
    timeout: float,
    poll_interval: float,
    query: str | None = None,
    profile: str | None = None,
    hashtag: str | None = None,
    post_url: str | None = None,
    max_results: int = 12,
) -> list[FetchedPost]:
    """Search Instagram and dump each post as html+md.

    Exactly one of *query*, *profile*, *hashtag*, or *post_url* must be set.

    Output:
      <out_dir>/results.json   index of permalinks + metadata
      <out_dir>/<NN>-<slug>.html raw outerHTML per post
      <out_dir>/<NN>-<slug>.md   layout-preserving markdown with frontmatter
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = [query, profile, hashtag, post_url]
    active = sum(1 for s in sources if s is not None)
    if active != 1:
        raise WebScraperError(
            "Exactly one of query=, profile=, hashtag=, or post_url= is required."
        )

    # --- Route to the right strategy ---
    if post_url:
        posts = _resolve_single_post(post_url)
        save_index(posts, out_dir / "results.json")
    elif profile:
        posts = _collect_grid(
            browser=browser,
            tab_ref=tab_ref,
            url=f"{_BASE}/{profile}/",
            timeout=timeout,
            poll_interval=poll_interval,
            max_results=max_results,
        )
        save_index(posts, out_dir / "results.json")
    elif hashtag:
        posts = _collect_grid(
            browser=browser,
            tab_ref=tab_ref,
            url=f"{_TAGS_URL}{hashtag}/",
            timeout=timeout,
            poll_interval=poll_interval,
            max_results=max_results,
        )
        save_index(posts, out_dir / "results.json")
    else:
        import urllib.parse

        search_url = _SEARCH_URL + urllib.parse.quote(query)
        posts = _collect_grid(
            browser=browser,
            tab_ref=tab_ref,
            url=search_url,
            timeout=timeout,
            poll_interval=poll_interval,
            max_results=max_results,
        )
        save_index(posts, out_dir / "results.json")

    if not posts:
        slug = query or profile or hashtag or post_url or ""
        raise WebScraperError(f"No posts found for {slug!r}")

    # --- Visit each post and dump HTML+MD ---
    existing = len(list(out_dir.glob("*.html")))
    fetched: list[FetchedPost] = []
    for idx, (i, post) in enumerate(
        zip(range(existing + 1, existing + 1 + len(posts)), posts)
    ):
        if idx > 0:
            time.sleep(random.uniform(0.3, 0.8))
        permalink = post["permalink"]
        html_path = out_dir / f"{i:02d}-{_slug_from_post(post)}.html"
        try:
            _fetch_one(
                browser=browser,
                tab_ref=tab_ref,
                post=post,
                position=i,
                html_path=html_path,
                timeout=timeout,
                poll_interval=poll_interval,
            )
            fetched.append(
                {
                    "permalink": permalink,
                    "shortcode": post.get("shortcode", ""),
                    "type": post.get("type", "p"),
                    "caption": post.get("caption", ""),
                    "username": post.get("username", ""),
                    "html_file": str(html_path),
                    "md_file": str(html_path.with_suffix(".md")),
                }
            )
        except WebScraperError as exc:
            print(f"[insta-fetch] skip {permalink}: {exc}", file=sys.stderr, flush=True)

    return fetched


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_single_post(post_url: str) -> list[dict[str, Any]]:
    m = re.search(r"/(p|reel|reels)/([^/?]+)", post_url)
    if not m:
        raise WebScraperError(f"Not a valid Instagram post/reel URL: {post_url}")
    ptype, code = m.group(1), m.group(2)
    permalink = f"{_BASE}/{ptype}/{code}/"
    return [
        {
            "type": ptype,
            "shortcode": code,
            "permalink": permalink,
            "username": "",
            "caption": "",
        }
    ]


def _collect_grid(
    *,
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    timeout: float,
    poll_interval: float,
    max_results: int,
) -> list[dict[str, Any]]:
    browser.navigate(tab_ref=tab_ref, url=url, timeout=timeout, wait_until="load")
    time.sleep(2.0)
    _check_login_wall(browser, tab_ref, timeout)
    _scroll_for_posts(browser, tab_ref, timeout=timeout, target=max_results)
    raw = (
        browser.eval_js(tab_ref=tab_ref, expression=_RESULTS_JS, timeout=timeout) or []
    )
    return [p for p in raw if isinstance(p, dict) and p.get("permalink")][:max_results]


def _scroll_for_posts(
    browser: BrowserTool, tab_ref: str, *, timeout: float, target: int
) -> None:
    for _ in range(10):
        count = browser.eval_js(
            tab_ref=tab_ref,
            expression='document.querySelectorAll(\'a[href*="/p/"], a[href*="/reel/"]\').length',
            timeout=timeout,
        )
        if isinstance(count, int) and count >= target:
            return
        browser.eval_js(
            tab_ref=tab_ref,
            expression="window.scrollBy(0, window.innerHeight * 2.5)",
            timeout=timeout,
        )
        time.sleep(1.5)


def _fetch_one(
    *,
    browser: BrowserTool,
    tab_ref: str,
    post: dict[str, Any],
    position: int,
    html_path: Path,
    timeout: float,
    poll_interval: float,
) -> None:
    permalink = post["permalink"]

    browser.navigate(tab_ref=tab_ref, url=permalink, timeout=timeout, wait_until="load")

    wait_for(
        timeout=timeout,
        poll_interval=poll_interval,
        task=lambda: _post_rendered(browser, tab_ref, timeout),
        error_message=f"Post never rendered for {permalink}",
    )
    time.sleep(1.0)

    md_body = _frontmatter(post, position)
    dump_html_and_md(
        browser=browser,
        tab_ref=tab_ref,
        url=permalink,
        md_body=md_body,
        html_path=html_path,
        timeout=timeout,
    )


def _post_rendered(browser: BrowserTool, tab_ref: str, timeout: float) -> bool:
    try:
        result = browser.eval_js(
            tab_ref=tab_ref,
            expression=(
                '!!document.querySelector(\'img[alt*="Photo" i], img[alt*="Video" i]\') '
                "|| !!document.querySelector('main') "
                "|| !!document.querySelector('[role=\"main\"]')"
            ),
            timeout=timeout,
        )
        return bool(result)
    except WebScraperError:
        return False


def _check_login_wall(browser: BrowserTool, tab_ref: str, timeout: float) -> None:
    try:
        has_wall = browser.eval_js(
            tab_ref=tab_ref,
            expression="!!document.querySelector('form input[name=\"username\"]')",
            timeout=timeout,
        )
        if has_wall:
            print(
                "[insta-fetch] Instagram login wall detected. "
                "Log in interactively via non-headless browser-api first:\n"
                "  uv run browser-api  # no --headless\n"
                "  then visit https://www.instagram.com and log in.\n"
                "  Cookies persist in the profile for future runs.",
                file=sys.stderr,
                flush=True,
            )
            raise WebScraperError("Login required")
    except WebScraperError:
        raise


def _frontmatter(post: dict[str, Any], position: int) -> str:
    permalink = post.get("permalink", "")
    shortcode = post.get("shortcode", "")
    ptype = post.get("type", "p")
    username = post.get("username", "")
    caption = post.get("caption", "")

    fm = f"---\npermalink: {permalink}\nshortcode: {shortcode}\ntype: {ptype}\nposition: {position}\n"
    if username:
        fm += f"username: {username!r}\n"
    if caption:
        fm += f"caption: {caption[:200]!r}\n"
    fm += "---\n\n"
    return fm


def _slug_from_post(post: dict[str, Any]) -> str:
    shortcode = post.get("shortcode", "unknown")
    username = post.get("username", "")
    if username:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "", username.lower().strip())[:40]
        if safe:
            return f"{safe}-{shortcode}"
    return shortcode
