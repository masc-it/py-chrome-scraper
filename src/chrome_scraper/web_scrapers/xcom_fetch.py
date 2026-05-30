"""Search x.com and download each result tweet as raw HTML + markdown.

Single browser session, mirrors ``google_fetch.py``.  x.com is a React SPA
that ignores synthetic KeyboardEvents, so we drive it via Playwright's native
keyboard API.

**Plugin for google-fetch**

``fetch_post_url`` is the public handler that ``google_fetch.py`` calls for
``x.com`` / ``www.x.com`` results.  It fetches the tweet, then recursively
detects and fetches any quoted/embedded tweets inside it.
"""

from __future__ import annotations

import random
import re
import sys
import time
from importlib import resources
from pathlib import Path
from typing import Any, TypedDict

from chrome_scraper.web_scrapers._fetch_common import dump_html_and_md, spam_back_until
from chrome_scraper.web_scrapers.base import (
    BrowserTool,
    WebScraperError,
    get_href,
    save_index,
    wait_for,
)

SEARCH_INPUT_SELECTOR = 'input[data-testid="SearchBox_Search_Input"]'
EXPLORE_URL = "https://x.com/explore"

_RESULTS_JS = (
    resources.files("chrome_scraper.web_scrapers.scripts")
    .joinpath("xcom_search_results.js")
    .read_text(encoding="utf-8")
)

# Detect a quoted tweet on the current page.
# Returns the quoted tweet URL or null.
# Logic: the main tweet article has >1 User-Name elements (quoting + quoted
# author).  We extract the quoted author's handle from the second User-Name,
# then find the status link belonging to that handle.
# Find quote tweets in the current page by looking for status links whose
# author handle also appears in a [data-testid="User-Name"] element.
# This works on both search results pages and tweet detail pages.
# Returns an array of quoted tweet URLs (max 3).
_FIND_QUOTED_JS = r"""
() => {
  const art = document.querySelector('article[data-testid="tweet"]');
  if (!art) return [];
  const curId = (window.location.pathname.match(/\/status\/(\d+)/) || [])[1];

  // Compute depth of each User-Name relative to article root.
  const depth = (el) => { let d=0, c=el; while(c&&c!==art){d++;c=c.parentElement} return d; };
  const names = Array.from(art.querySelectorAll('[data-testid="User-Name"]'));
  if (names.length < 2) return [];
  const baseDepth = depth(names[0]);

  // Collect handles from names that are at a similar depth (within 3 levels).
  // This filters out handles in deeply nested reply containers.
  const handles = new Set();
  handles.add((window.location.pathname.match(/\/([^/]+)\/status\//) || [])[1]?.toLowerCase());
  for (const n of names) {
    if (depth(n) > baseDepth + 3) continue;
    const m = (n.textContent || '').match(/@([A-Za-z0-9_]+)/);
    if (m) handles.add(m[1].toLowerCase());
  }

  // Find status links that match one of the handles.
  const found = [];
  const seen = new Set();
  const links = art.querySelectorAll('a[href*="/status/"]');
  for (const a of links) {
    const m = a.href.match(/https:\/\/x\.com\/([^/]+)\/status\/(\d+)(?:\/|$)/);
    if (!m) continue;
    const handle = m[1].toLowerCase();
    const id = m[2];
    if (id === curId) continue;
    if (seen.has(id)) continue;
    seen.add(id);
    if (handles.has(handle)) {
      found.push(m[0]);
      if (found.length >= 3) break;
    }
  }
  return found;
}
"""


class FetchedTweet(TypedDict):
    permalink: str
    author: str
    text: str
    html_file: str
    md_file: str


# ── Public plugin interface (consumed by google_fetch.py) ─────────────────


def fetch_post_url(
    browser: BrowserTool, tab_ref: str,
    url: str, title: str, position: int, html_path: Path,
    timeout: float, poll_interval: float,
) -> None:
    """Fetch a single X.com tweet, including any quoted tweet inside."""
    _fetch_tweet_and_quoted(
        browser, tab_ref, url, title, position, html_path,
        timeout, poll_interval, is_standalone=False,
    )


# ── Full fetch_query (consumed by xcom-fetch CLI) ────────────────────────


def fetch_query(
    *,
    browser: BrowserTool,
    tab_ref: str,
    query: str,
    out_dir: Path,
    timeout: float,
    poll_interval: float,
    max_results: int = 20,
    from_profile: str | None = None,
) -> list[FetchedTweet]:
    """Search x.com and dump each tweet permalink as html+md."""
    if not _has_native_keyboard(browser):
        raise WebScraperError(
            "xcom-fetch requires a backend with native keyboard support "
            "(browser-api). x.com ignores synthetic KeyboardEvents."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    full_query = f"from:{from_profile} {query}" if from_profile else query

    browser.navigate(
        tab_ref=tab_ref, url=EXPLORE_URL, timeout=timeout, wait_until="load"
    )
    _submit_search(
        browser, tab_ref, full_query, timeout=timeout, poll_interval=poll_interval,
    )

    _scroll_for_tweets(browser, tab_ref, timeout=timeout, target=max_results)

    raw = browser.eval_js(tab_ref=tab_ref, expression=_RESULTS_JS, timeout=timeout) or []
    tweets = [t for t in raw if isinstance(t, dict) and t.get("permalink")][:max_results]
    if not tweets:
        raise WebScraperError(f"No tweets found for query: {full_query!r}")

    save_index(tweets, out_dir / "results.json")

    existing = len(list(out_dir.glob("*.html")))
    fetched: list[FetchedTweet] = []
    for idx, (i, tweet) in enumerate(
        zip(range(existing + 1, existing + 1 + len(tweets)), tweets)
    ):
        if idx > 0:
            time.sleep(random.uniform(0.1, 0.5))
        permalink = tweet["permalink"]
        html_path = out_dir / f"{i:02d}-{_slug_from_tweet(tweet)}.html"
        try:
            _fetch_one(
                browser=browser, tab_ref=tab_ref,
                tweet=tweet, position=i, html_path=html_path,
                timeout=timeout, poll_interval=poll_interval,
            )
            fetched.append({
                "permalink": permalink,
                "author": tweet.get("author", ""),
                "text": tweet.get("text", ""),
                "html_file": str(html_path),
                "md_file": str(html_path.with_suffix(".md")),
            })
        except WebScraperError as exc:
            print(f"[xcom-fetch] skip {permalink}: {exc}", file=sys.stderr, flush=True)

    return fetched


def fetch_single_post(
    *,
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    out_dir: Path,
    timeout: float,
    poll_interval: float,
    max_quoted: int = 3,
) -> list[dict[str, Any]]:
    """Fetch a single tweet URL, plus any quoted tweets inside it.

    Returns a list of ``{permalink, html_file, md_file}`` dicts.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    m = re.search(r"/status/(\d+)", url)
    status_id = m.group(1) if m else "tweet"

    results: list[dict[str, Any]] = []

    def _do_fetch(url: str, pos: int, label: str) -> dict[str, Any] | None:
        hp = out_dir / f"{pos:02d}-{label}.html"
        try:
            pl = _normalise_url(url)
            _fetch_one(
                browser=browser, tab_ref=tab_ref,
                tweet={"permalink": pl, "author": "", "text": ""},
                position=pos, html_path=hp,
                timeout=timeout, poll_interval=poll_interval,
                skip_back=True,
            )
            r = {"permalink": pl, "html_file": str(hp), "md_file": str(hp.with_suffix(".md"))}
            results.append(r)
            return r
        except WebScraperError as exc:
            print(f"[xcom-fetch] skip {url}: {exc}", file=sys.stderr, flush=True)
            return None

    # Fetch the main tweet.
    _do_fetch(url, 1, status_id)

    # Check for quoted tweets and fetch them too.
    for qurl in _find_quoted_tweets(browser, tab_ref, timeout, max_quoted):
        qm = re.search(r"/status/(\d+)", qurl)
        qid = qm.group(1) if qm else "quoted"
        _do_fetch(qurl, len(results) + 1, qid)

    return results


# ── Internal helpers ─────────────────────────────────────────────────────


def _fetch_tweet_and_quoted(
    browser: BrowserTool, tab_ref: str,
    url: str, title: str, position: int, html_path: Path,
    timeout: float, poll_interval: float,
    *,
    is_standalone: bool = False,
) -> None:
    """Fetch a single tweet.  After the main tweet, recurse into quote tweets."""
    _fetch_one(
        browser=browser, tab_ref=tab_ref,
        tweet={"permalink": _normalise_url(url), "author": "", "text": ""},
        position=position, html_path=html_path,
        timeout=timeout, poll_interval=poll_interval,
        skip_back=True,
    )

    for qi, qurl in enumerate(
        _find_quoted_tweets(browser, tab_ref, timeout, max_quoted=3),
        start=position + 1,
    ):
        qm = re.search(r"/status/(\d+)", qurl)
        qid = qm.group(1) if qm else f"quoted-{qi}"
        qhtml = html_path.parent / f"{qi:02d}-{qid}.html"
        try:
            _fetch_one(
                browser=browser, tab_ref=tab_ref,
                tweet={"permalink": qurl, "author": "", "text": ""},
                position=qi, html_path=qhtml,
                timeout=timeout, poll_interval=poll_interval,
                skip_back=True,
            )
        except WebScraperError as exc:
            print(f"[xcom-fetch] skip quoted {qurl}: {exc}", file=sys.stderr, flush=True)


def _normalise_url(url: str) -> str:
    m = re.search(r"(https?://(?:www\.)?x\.com/[^/]+/status/\d+)", url)
    return m.group(1) if m else url


def _find_quoted_tweets(
    browser: BrowserTool, tab_ref: str, timeout: float, max_quoted: int = 3,
) -> list[str]:
    """Detect any quoted/embedded tweets in the current page."""
    try:
        urls = browser.eval_js(
            tab_ref=tab_ref, expression=_FIND_QUOTED_JS, timeout=timeout,
        )
    except WebScraperError:
        return []
    if not urls or not isinstance(urls, list):
        return []
    return urls[:max_quoted]


def _submit_search(
    browser: BrowserTool, tab_ref: str, query: str, *,
    timeout: float, poll_interval: float,
) -> None:
    wait_for(
        timeout=timeout, poll_interval=poll_interval,
        task=lambda: browser.eval_js(
            tab_ref=tab_ref,
            expression=f"!!document.querySelector({SEARCH_INPUT_SELECTOR!r})",
            timeout=timeout,
        ),
        error_message="x.com search input never appeared",
    )
    browser.focus(tab_ref=tab_ref, selector=SEARCH_INPUT_SELECTOR, timeout=timeout)
    browser.eval_js(
        tab_ref=tab_ref,
        expression=f"document.querySelector({SEARCH_INPUT_SELECTOR!r}).select()",
        timeout=timeout,
    )
    browser.keyboard_press(tab_ref=tab_ref, key="Backspace")
    browser.keyboard_type(tab_ref=tab_ref, text=query, delay_ms=20)
    browser.keyboard_press(tab_ref=tab_ref, key="Enter")
    wait_for(
        timeout=timeout, poll_interval=poll_interval,
        task=lambda: "/search?q=" in get_href(browser, tab_ref, timeout),
        error_message=f"Search did not navigate to results page for query {query!r}",
    )


def _scroll_for_tweets(
    browser: BrowserTool, tab_ref: str, *, timeout: float, target: int,
) -> None:
    for _ in range(6):
        count = browser.eval_js(
            tab_ref=tab_ref,
            expression="document.querySelectorAll('article').length",
            timeout=timeout,
        )
        if isinstance(count, int) and count >= target:
            return
        browser.eval_js(
            tab_ref=tab_ref,
            expression="window.scrollBy(0, window.innerHeight * 2)",
            timeout=timeout,
        )
        time.sleep(1.0)


def _fetch_one(
    *,
    browser: BrowserTool, tab_ref: str,
    tweet: dict, position: int, html_path: Path,
    timeout: float, poll_interval: float,
    skip_back: bool = False,
) -> None:
    permalink = tweet["permalink"]
    results_url = get_href(browser, tab_ref, timeout)

    clicked = browser.eval_js(
        tab_ref=tab_ref,
        expression=_click_tweet_script(permalink),
        timeout=timeout,
    )
    if not isinstance(clicked, dict) or not clicked.get("clicked"):
        browser.navigate(
            tab_ref=tab_ref, url=permalink, timeout=timeout, wait_until="load",
        )
    else:
        wait_for(
            timeout=timeout, poll_interval=poll_interval,
            task=lambda: permalink.split("?")[0] in get_href(browser, tab_ref, timeout),
            error_message=f"Tweet page never loaded for {permalink}",
        )

    wait_for(
        timeout=timeout, poll_interval=poll_interval,
        task=lambda: browser.eval_js(
            tab_ref=tab_ref,
            expression="!!document.querySelector('article [data-testid=\"tweetText\"]') || !!document.querySelector('article')",
            timeout=timeout,
        ),
        error_message=f"Tweet article never rendered for {permalink}",
    )
    time.sleep(0.5)

    md_body = _frontmatter(tweet, position)
    dump_html_and_md(
        browser=browser, tab_ref=tab_ref,
        url=permalink, md_body=md_body, html_path=html_path,
        timeout=timeout,
    )

    if not skip_back:
        spam_back_until(
            browser=browser, tab_ref=tab_ref,
            timeout=timeout, poll_interval=poll_interval,
            predicate=lambda: "/search?q=" in get_href(browser, tab_ref, timeout),
            error_message="Did not return to x.com search results after history.back()",
        )


def _click_tweet_script(permalink: str) -> str:
    base = permalink.split("?")[0]
    escaped = base.replace("\\", "\\\\").replace('"', '\\"')
    return f"""
(() => {{
  const target = "{escaped}";
  const anchors = Array.from(document.querySelectorAll('article a[href*="/status/"]'));
  const a = anchors.find(x => x.href.split("?")[0] === target);
  if (!a) return {{ clicked: false }};
  a.removeAttribute("target");
  a.scrollIntoView({{ block: "center" }});
  a.click();
  return {{ clicked: true }};
}})()
""".strip()


def _frontmatter(tweet: dict, position: int) -> str:
    permalink = tweet["permalink"]
    author = tweet.get("author") or ""
    text = (tweet.get("text") or "")[:200]
    fm = f"---\npermalink: {permalink}\nposition: {position}\n"
    if author:
        fm += f"author: {author!r}\n"
    if text:
        fm += f"text: {text!r}\n"
    fm += "---\n\n"
    return fm


def _slug_from_tweet(tweet: dict) -> str:
    m = re.search(r"/status/(\d+)", tweet.get("permalink", ""))
    status_id = m.group(1) if m else "unknown"
    handle = ""
    author = tweet.get("author", "")
    h = re.search(r"@([A-Za-z0-9_]+)", author)
    if h:
        handle = h.group(1).lower()
    return f"{handle}-{status_id}" if handle else status_id


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug[:80] or "tweet"


def _has_native_keyboard(browser: BrowserTool) -> bool:
    return all(
        hasattr(browser, attr) for attr in ("keyboard_type", "keyboard_press", "focus")
    )
