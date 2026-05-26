"""YouTube video page scraper — expands description, opens transcript, dumps HTML+MD.

Single browser session.  Opens a YouTube watch page, clicks the description
"more" expander, opens the transcript panel, scrolls the transcript content,
then dumps the full page via ``dump_html_and_md``.

The transcript lives inside a scrollable side-panel whose children are mostly
below the scroll fold.  ``getBoundingClientRect`` returns zero-height rects for
those nodes, so the generic html-to-md CDP extraction misses them.  We extract
the transcript text via a separate JS snippet and prepend it to the markdown.

**Plugin for google-fetch**

``fetch_post_url`` is the public handler that ``google_fetch.py`` calls for
``www.youtube.com`` results.  It performs the same expand + transcript flow
before dumping.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from chrome_scraper.web_scrapers._fetch_common import dump_html_and_md
from chrome_scraper.web_scrapers.base import (
    BrowserTool,
    WebScraperError,
    wait_for,
)


# ── JS snippets ──────────────────────────────────────────────────────────

_EXPAND_DESCRIPTION_JS = """
() => {
  const expander = document.querySelector('#description-inline-expander');
  if (!expander) return false;
  const link = expander.querySelector('#expand');
  if (link) { link.click(); return true; }
  return false;
}
"""

# Click "Show transcript" — locale-agnostic keyword list.
_SHOW_TRANSCRIPT_JS = """
() => {
  const kw = ['show transcript', 'mostra trascrizione', 'transcript anzeigen',
              'mostrar transcripción', 'afficher la transcription',
              'transcrição', 'transkript'];
  const buttons = Array.from(document.querySelectorAll('button'));
  const btn = buttons.find(b => {
    const t = (b.textContent || '').trim().toLowerCase();
    const a = (b.getAttribute('aria-label') || '').toLowerCase();
    return kw.some(k => t.includes(k) || a.includes(k));
  });
  if (!btn) return false;
  btn.click();
  return true;
}
"""

# Returns true only when a visible engagement panel exists with substantial
# text AND no active spinner inside it.  Returns false if no such panel has
# appeared yet (avoids vacuous-truth early exit).
_TRANSCRIPT_READY_JS = """
() => {
  const panels = document.querySelectorAll('ytd-engagement-panel-section-list-renderer');
  for (const p of panels) {
    const bcr = p.getBoundingClientRect();
    if (bcr.width < 100 || bcr.height < 100) continue;
    if ((p.textContent || '').length < 300) continue;
    // Found a visible panel with content — now check spinner is gone.
    const spinner = p.querySelector('tp-yt-paper-spinner[active]');
    return !spinner;
  }
  // No qualifying panel yet.
  return false;
}
"""

# Extract the transcript text from the visible engagement panel.
# Returns an array of {time, text} objects, or null if not found.
# Uses no specific tag names — finds the visible panel with lots of
# text, then walks its text nodes.
_EXTRACT_TRANSCRIPT_JS = """
() => {
  // Find the visible engagement panel with substantial content.
  const panels = document.querySelectorAll('ytd-engagement-panel-section-list-renderer');
  let panel = null;
  for (const p of panels) {
    const bcr = p.getBoundingClientRect();
    if (bcr.width > 100 && bcr.height > 100 && (p.textContent || '').length > 500) {
      panel = p;
      break;
    }
  }
  if (!panel) return null;

  // Find the scrollable child inside it.
  let scrollable = null;
  for (const el of panel.querySelectorAll('*')) {
    if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 50) {
      scrollable = el;
      break;
    }
  }

  // Scroll the container from top to bottom to force all segments to render.
  const target = scrollable || panel;
  if (scrollable) {
    const step = scrollable.scrollHeight / 10;
    for (let i = 0; i <= 10; i++) {
      scrollable.scrollTop = i * step;
    }
    scrollable.scrollTop = 0;
  }

  // Collect text from the panel.  Each segment is typically a clickable div
  // with a timestamp string + body text.  We grab them as (time, text) pairs.
  // Strategy: iterate direct children of the scrollable list, extract the
  // first "short" string (<= 8 chars, likely a timestamp) and the rest as text.
  const segments = [];
  const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
  let node;
  let current = { time: '', text: '' };

  while ((node = walker.nextNode())) {
    const t = (node.nodeValue || '').trim();
    if (!t) continue;

    // Is this a timestamp? (e.g. "0:00", "12:34", "1:23:45")
    if (/^\\d{1,2}(:\\d{2}){1,2}$/.test(t)) {
      // Flush previous segment.
      if (current.time || current.text) segments.push({ ...current });
      current = { time: t, text: '' };
    } else {
      // Append to current segment text.
      current.text += (current.text ? ' ' : '') + t;
    }
  }
  if (current.time || current.text) segments.push(current);

  return segments.length > 0 ? segments : null;
}
"""


# ── Public plugin interface (consumed by google_fetch.py) ─────────────────


def fetch_post_url(
    browser: BrowserTool, tab_ref: str,
    url: str, title: str, position: int, html_path: Path,
    timeout: float, poll_interval: float,
) -> None:
    """Fetch a YouTube video page — expand description, open transcript, dump.

    Signature matches ``FetchHandler`` in ``google_fetch.py``.
    """
    _expand_and_open_transcript(browser, tab_ref, url, timeout, poll_interval)

    transcript_md = _extract_transcript_md(browser, tab_ref, timeout)

    frontmatter = f"---\ntitle: {title!r}\nurl: {url}\nposition: {position}\n---\n\n"

    dump_html_and_md(
        browser=browser, tab_ref=tab_ref,
        url=url, md_body=frontmatter + transcript_md, html_path=html_path,
        timeout=timeout,
    )


# ── Internal helpers ─────────────────────────────────────────────────────


def _expand_and_open_transcript(
    browser: BrowserTool, tab_ref: str,
    url: str, timeout: float, poll_interval: float,
) -> None:
    """Navigate to a YouTube video, expand description, open transcript panel,
    and scroll the transcript content to ensure it's fully rendered."""
    browser.navigate(tab_ref=tab_ref, url=url, timeout=timeout, wait_until="load")
    time.sleep(3)  # let the SPA hydrate

    # Step 1: expand the description
    browser.eval_js(tab_ref=tab_ref, expression=_EXPAND_DESCRIPTION_JS, timeout=timeout)
    time.sleep(0.5)

    # Step 2: click "Show transcript"
    browser.eval_js(tab_ref=tab_ref, expression=_SHOW_TRANSCRIPT_JS, timeout=timeout)

    # Step 3: wait for a visible panel with content and no active spinner
    wait_for(
        timeout=timeout,
        poll_interval=poll_interval,
        task=lambda: _transcript_panel_ready(browser, tab_ref, timeout),
        error_message="Transcript panel never loaded",
    )


def _transcript_panel_ready(browser: BrowserTool, tab_ref: str, timeout: float) -> bool:
    try:
        return bool(browser.eval_js(
            tab_ref=tab_ref, expression=_TRANSCRIPT_READY_JS, timeout=timeout,
        ))
    except WebScraperError:
        return False


def _extract_transcript_md(browser: BrowserTool, tab_ref: str, timeout: float) -> str:
    """Extract transcript segments via JS and return as a markdown section."""
    try:
        segments = browser.eval_js(
            tab_ref=tab_ref, expression=_EXTRACT_TRANSCRIPT_JS, timeout=timeout,
        )
    except WebScraperError:
        return ""
    if not segments:
        return ""

    lines = ["## Transcript\n"]
    for seg in segments:
        ts = seg.get("time", "")
        text = seg.get("text", "").strip()
        if not text:
            continue
        if ts:
            lines.append(f"**{ts}** {text}\n")
        else:
            lines.append(f"{text}\n")
    lines.append("")
    return "\n".join(lines)


# ── Full fetch_query (consumed by yt-fetch CLI) ──────────────────────────


def fetch_video(
    *,
    browser: BrowserTool,
    tab_ref: str,
    url: str,
    out_dir: Path,
    timeout: float,
    poll_interval: float,
) -> Path:
    """Fetch a single YouTube video page after expanding transcript.

    Returns the path to the dumped HTML file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    m = re.search(r"(?:v=|/shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
    video_id = m.group(1) if m else "video"
    html_path = out_dir / f"{video_id}.html"

    _expand_and_open_transcript(browser, tab_ref, url, timeout, poll_interval)

    transcript_md = _extract_transcript_md(browser, tab_ref, timeout)

    title = ""
    try:
        title = browser.eval_js(
            tab_ref=tab_ref,
            expression="document.querySelector('h1 yt-formatted-string')?.textContent || ''",
            timeout=timeout,
        ) or ""
    except WebScraperError:
        pass

    md_body = (
        f"---\nurl: {url}\nvideo_id: {video_id}\ntitle: {title!r}\n---\n\n"
        + transcript_md
    )

    dump_html_and_md(
        browser=browser, tab_ref=tab_ref,
        url=url, md_body=md_body, html_path=html_path,
        timeout=timeout,
    )

    return html_path
