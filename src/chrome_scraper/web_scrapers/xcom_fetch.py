"""Search x.com and download each result tweet as raw HTML + markdown.

Single browser session, mirrors `google_fetch.py`. x.com is a React SPA that
ignores synthetic KeyboardEvents, so we drive it via Playwright's native
keyboard API.
"""

from __future__ import annotations

import random
import re
import sys
import time
from importlib import resources
from pathlib import Path
from typing import TypedDict

from chrome_scraper.web_scrapers._fetch_common import dump_html_and_md, spam_back_until
from chrome_scraper.web_scrapers.base import BrowserTool, WebScraperError, get_href, save_index, wait_for


SEARCH_INPUT_SELECTOR = 'input[data-testid="SearchBox_Search_Input"]'
EXPLORE_URL = "https://x.com/explore"

_RESULTS_JS = (
    resources.files("chrome_scraper.web_scrapers.scripts")
    .joinpath("xcom_search_results.js")
    .read_text(encoding="utf-8")
)


class FetchedTweet(TypedDict):
    permalink: str
    author: str
    text: str
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
    max_results: int = 20,
    from_profile: str | None = None,
) -> list[FetchedTweet]:
    """Search x.com and dump each tweet permalink as html+md.

    Output:
      <out_dir>/results.json     index of permalinks + author + text snippet
      <out_dir>/<NN>-<slug>.html raw outerHTML per tweet
      <out_dir>/<NN>-<slug>.md   layout-preserving markdown with frontmatter
    """
    if not _has_native_keyboard(browser):
        raise WebScraperError(
            "xcom-fetch requires a backend with native keyboard support "
            "(browser-api). x.com ignores synthetic KeyboardEvents."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    full_query = f"from:{from_profile} {query}" if from_profile else query

    # Step 1: open explore, type query into search bar, press Enter.
    browser.navigate(
        tab_ref=tab_ref, url=EXPLORE_URL, timeout=timeout, wait_until="load"
    )
    _submit_search(
        browser, tab_ref, full_query, timeout=timeout, poll_interval=poll_interval
    )

    # Step 2: scroll a few times so the virtualised list populates.
    _scroll_for_tweets(browser, tab_ref, timeout=timeout, target=max_results)

    # Step 3: extract permalinks.
    raw = (
        browser.eval_js(tab_ref=tab_ref, expression=_RESULTS_JS, timeout=timeout) or []
    )
    tweets = [t for t in raw if isinstance(t, dict) and t.get("permalink")][:max_results]
    if not tweets:
        raise WebScraperError(f"No tweets found for query: {full_query!r}")

    save_index(tweets, out_dir / "results.json")

    # Step 4: visit each tweet, dump html+md, navigate back to results.
    existing = len(list(out_dir.glob("*.html")))
    fetched: list[FetchedTweet] = []
    for idx, (i, tweet) in enumerate(
        zip(range(existing + 1, existing + 1 + len(tweets)), tweets)
    ):
        # Jitter between clicks to look less robotic; skip before the first.
        if idx > 0:
            time.sleep(random.uniform(0.1, 0.5))
        permalink = tweet["permalink"]
        html_path = out_dir / f"{i:02d}-{_slug_from_tweet(tweet)}.html"
        try:
            _fetch_one(
                browser=browser,
                tab_ref=tab_ref,
                tweet=tweet,
                position=i,
                html_path=html_path,
                timeout=timeout,
                poll_interval=poll_interval,
            )
            fetched.append(
                {
                    "permalink": permalink,
                    "author": tweet.get("author", ""),
                    "text": tweet.get("text", ""),
                    "html_file": str(html_path),
                    "md_file": str(html_path.with_suffix(".md")),
                }
            )
        except WebScraperError as exc:
            print(f"[xcom-fetch] skip {permalink}: {exc}", file=sys.stderr, flush=True)

    return fetched



def _submit_search(
    browser: BrowserTool,
    tab_ref: str,
    query: str,
    *,
    timeout: float,
    poll_interval: float,
) -> None:
    # Wait for the search input to mount before driving the keyboard.
    wait_for(
        timeout=timeout,
        poll_interval=poll_interval,
        task=lambda: browser.eval_js(
            tab_ref=tab_ref,
            expression=f"!!document.querySelector({SEARCH_INPUT_SELECTOR!r})",
            timeout=timeout,
        ),
        error_message="x.com search input never appeared",
    )

    browser.focus(tab_ref=tab_ref, selector=SEARCH_INPUT_SELECTOR, timeout=timeout)
    # Clear any pre-filled value (e.g. trending term) before typing.
    browser.eval_js(
        tab_ref=tab_ref,
        expression=f"document.querySelector({SEARCH_INPUT_SELECTOR!r}).select()",
        timeout=timeout,
    )
    browser.keyboard_press(tab_ref=tab_ref, key="Backspace")
    browser.keyboard_type(tab_ref=tab_ref, text=query, delay_ms=20)
    browser.keyboard_press(tab_ref=tab_ref, key="Enter")

    wait_for(
        timeout=timeout,
        poll_interval=poll_interval,
        task=lambda: "/search?q=" in get_href(browser, tab_ref, timeout),
        error_message=f"Search did not navigate to results page for query {query!r}",
    )


def _scroll_for_tweets(
    browser: BrowserTool, tab_ref: str, *, timeout: float, target: int
) -> None:
    # x.com virtualises the timeline; scroll until enough articles are mounted
    # or we hit a fixed pass count to bound runtime.
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
    browser: BrowserTool,
    tab_ref: str,
    tweet: dict,
    position: int,
    html_path: Path,
    timeout: float,
    poll_interval: float,
) -> None:
    permalink = tweet["permalink"]
    results_url = get_href(browser, tab_ref, timeout)

    # SPA route change: clicking the permalink anchor triggers client-side nav
    # without a full reload. Falls back to direct navigate if anchor is gone.
    clicked = browser.eval_js(
        tab_ref=tab_ref,
        expression=_click_tweet_script(permalink),
        timeout=timeout,
    )
    if not isinstance(clicked, dict) or not clicked.get("clicked"):
        browser.navigate(
            tab_ref=tab_ref, url=permalink, timeout=timeout, wait_until="load"
        )
    else:
        wait_for(
            timeout=timeout,
            poll_interval=poll_interval,
            task=lambda: permalink.split("?")[0] in get_href(browser, tab_ref, timeout),
            error_message=f"Tweet page never loaded for {permalink}",
        )

    # The tweet body renders progressively; wait for the article to appear.
    wait_for(
        timeout=timeout,
        poll_interval=poll_interval,
        task=lambda: browser.eval_js(
            tab_ref=tab_ref,
            expression="!!document.querySelector('article [data-testid=\"tweetText\"]') || !!document.querySelector('article')",
            timeout=timeout,
        ),
        error_message=f"Tweet article never rendered for {permalink}",
    )
    time.sleep(0.5)  # let media/quoted tweets paint before HTML dump

    md_body = _frontmatter(tweet, position)
    dump_html_and_md(
        browser=browser,
        tab_ref=tab_ref,
        url=permalink,
        md_body=md_body,
        html_path=html_path,
        timeout=timeout,
    )

    spam_back_until(
        browser=browser,
        tab_ref=tab_ref,
        timeout=timeout,
        poll_interval=poll_interval,
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
  a.removeAttribute("target");  // prevent target=_blank from spawning a new tab
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
    # Use status id for stability; tweet text is too noisy for a filename.
    m = re.search(r"/status/(\d+)", tweet.get("permalink", ""))
    status_id = m.group(1) if m else "unknown"
    handle = ""
    author = tweet.get("author", "")
    h = re.search(r"@([A-Za-z0-9_]+)", author)
    if h:
        handle = h.group(1).lower()
    return f"{handle}-{status_id}" if handle else status_id


def _has_native_keyboard(browser: BrowserTool) -> bool:
    return all(
        hasattr(browser, attr) for attr in ("keyboard_type", "keyboard_press", "focus")
    )



