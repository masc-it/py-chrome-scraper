# chrome-scraper

Stealth and stateful Chrome scraper, built to let AI agents easily grab up-to-date information in markdown.

Shared browser server with profile persistence, with pre-built CLI scrapers for Google and x.com that dump content in HTML + clean markdown.


`chrome-scraper` is a Python package built on [Patchright](https://pypi.org/project/patchright/) (a maintained Playwright fork) that runs a single long-lived Chrome instance behind a FastAPI HTTP API. Clients can connect concurrently, share cookies/sessions via a persistent profile, and avoid the cold-start/teardown cost of per-task browser launch.

You can find some prebuild skills under [.agents/skills](.agents/skills)

## Install

```bash
# Core package
uv tool install chrome-scraper

# Or install from source
uv sync
```

Supports Python 3.10–3.13.

## Quickstart

```bash
# 1. Start the shared browser server (keep running)
uv run browser-api [--headless]

# 2. In another terminal — render a URL to markdown
html-to-md https://example.com --output out/example.md

# 3. Search Google and download results
google-fetch --query "latest pydantic version" --max-results 2 --num-pages 1 --out-dir data/searches/latest-pydantic-version

# 4. Search x.com and download tweets
xcom-fetch --query "latest ai news" --max-results 10 --out-dir data/research/xcom-latest-ai-news
```

`browser-api` is a persistent server — start it once, run scrapers against it from multiple terminals or scripts. Chrome keeps cookies, logins, and profile state across sessions.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                browser-api (port 9333)                │
│  ┌──────────────────────────────────────────────────┐ │
│  │  FastAPI server                                  │ │
│  │  /status /tabs /goto /eval /html /type /press │ │
│  └──────────┬───────────────────────────────────────┘ │
│             │ owns                                    │
│  ┌──────────▼───────────────────────────────────────┐ │
│  │  Chrome (Patchright persistent context)          │ │
│  │  Profile                                         │ │
│  │  Tabs: separate label-keyed sandboxes            │ │
│  └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
         ▲ HTTP                         ▲ HTTP
         │                              │
┌────────┴──────────┐    ┌──────────────┴──────────────┐
│  html-to-md       │    │  google-fetch / xcom-fetch  │
│  short-lived      │    │  short-lived CLIs           │
│  open tab →       │    │  open tab → search →        │
│  extract → close  │    │  fetch each result → close  │
└───────────────────┘    └─────────────────────────────┘
```

The server patches Patchright's `crBrowser.js` so new tabs open in background — Chrome never steals focus during concurrent scraping.

## CLI reference

### browser-api

Start, stop, and check status of the shared browser server.

```bash
uv run browser-api                       # start on :9333 (default)
uv run browser-api --port 8080           # custom port
uv run browser-api --headless            # headless mode
uv run browser-api --chrome-path /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
uv run browser-api --profile-dir ~/my-chrome-profile
uv run browser-api --hide                # hide Chrome window (macOS)
uv run browser-api --proxy http://proxy:8080
uv run browser-api --browser-args="--disable-gpu --no-sandbox"
uv run browser-api status                # check if running
uv run browser-api stop                  # shut down
```

Stateful features:

- **Persistent profile** — cookies, localStorage, and logins survive restarts. Profile lives at `~/Library/Application Support/thebase/playwright/profile/` on macOS.
- **Headless identity** — when `--headless` is used, the server automatically probes a throwaway headless instance, strips the `HeadlessChrome` token from the User-Agent, and passes the clean UA to the real persistent context.
- **Hide macOS window** — `--hide` runs `osascript` to hide Chrome from sight without quitting (non-headless only).

### url-to-md

Render a live URL as layout-preserving markdown. Site-specific handlers are used automatically when available — for example, YouTube watch URLs are expanded and transcript markdown is prepended before the generic page render.

```bash
# Generic page
url-to-md https://example.com --output out/page.md

# Custom handler kicks in automatically for supported URL patterns
url-to-md "https://www.youtube.com/watch?v=JVTUtdzqeGI" --output out/video.md

# Print to stdout
url-to-md https://example.com --output -
```

### html-to-md

Render any URL (or local HTML file) as layout-preserving markdown.

```bash
# Live URL
html-to-md https://example.com --output out/page.md

# Local HTML file
html-to-md --from-file page.html --output out/page.md

# Print to stdout
html-to-md https://example.com --output -

# Save raw text-node payload alongside markdown
html-to-md https://example.com --save-items

# Skip scroll pass (for already-scrolled SPAs)
html-to-md https://example.com --no-scroll

# Verbose layout diagnostics
html-to-md https://example.com -v

# Custom browser-api URL
html-to-md https://example.com --browser-api http://localhost:9333
```

Rendering preserves multi-column layout, code blocks, headings, links, inline code, and list structure. Sidebar content is separated by a `---` rule. See [`docs/layout.md`](docs/layout.md) for details on the layout-to-markdown algorithm.

### google-fetch

Search Google and download each result as HTML + markdown.

```bash
# Basic search, 1 page
google-fetch --query "quantum computing"

# Multi-page with output dir
google-fetch --query "machine learning transformers" \
  --num-pages 3 --out-dir data/research/transformers

# Filter by hostname
google-fetch --query "python typing" \
  --allowed-hosts docs.python.org peps.python.org

# Limit total results
google-fetch --query "Rust async" --max-results 5

# Custom browser-api server
google-fetch --query "agents" --browser-api http://localhost:9333
```

Output layout:

```
data/research/<query-slug>/<tag>/
├── results.json           # title + URL index
├── 01-introduction-to.html
├── 01-introduction-to.md   # frontmatter + rendered markdown
├── 02-advanced-topics.html
├── 02-advanced-topics.md
└── ...
```

### xcom-fetch

Search x.com and download tweets as HTML + markdown.

```bash
# Keyword search
xcom-fetch --query "reinforcement learning"

# Restrict to one account
xcom-fetch --query "safety" --from "Anthropic"

# Limit results
xcom-fetch --query "alignment" --max-results 5

# Custom output dir
xcom-fetch --query "scaling laws" --out-dir data/tweets
```

Output layout:

```
data/research/xcom-<query>/
├── results.json           # permalink + author + text snippet
├── 01-anthropic-12345.html
├── 01-anthropic-12345.md   # frontmatter + rendered markdown
└── ...
```

### yt-fetch

Fetch a YouTube video page with expanded description and transcript as HTML + markdown.

```bash
# Fetch video by URL
yt-fetch "https://www.youtube.com/watch?v=JVTUtdzqeGI"

# Short URL
yt-fetch "https://youtu.be/JVTUtdzqeGI"

# Custom output dir
yt-fetch "https://www.youtube.com/watch?v=JVTUtdzqeGI" --out-dir data/youtube/video
```

Output layout:

```
data/youtube/<video-id>/
├── <video-id>.html        # raw outerHTML with expanded transcript
├── <video-id>.md          # frontmatter + rendered markdown
```

### insta-fetch

Search Instagram and download posts/reels as HTML + markdown.

Requires login — start `browser-api` non-headless once, log in at instagram.com.
Cookies persist in the profile for future runs.

```bash
# Keyword search
insta-fetch --query "aurora borealis"

# Profile posts
insta-fetch --profile "nasa"

# Hashtag search
insta-fetch --hashtag "space"

# Single post by URL
insta-fetch --post "https://www.instagram.com/p/CxYZ12345/"

# Limit results
insta-fetch --query "machine learning" --max-results 5
```

Output layout:

```
data/research/insta-<query>/
├── results.json           # permalink + shortcode + type + caption snippet
├── 01-username-ABC123.html
├── 01-username-ABC123.md   # frontmatter + rendered markdown
└── ...
```

## Python API

```python
from pathlib import Path
from chrome_scraper.html_to_md import extract_from_url, render_page

# Extract text-node payload from a URL
payload = extract_from_url(
    "https://example.com",
    browser_api_url="http://localhost:9333",
    timeout=30.0,
    scroll=True,
)

# Render to layout-preserving markdown
items = payload.get("items", [])
page_width = (payload.get("viewport") or {}).get("scroll_w", 1280)
md = render_page(items, page_width)
Path("out/example.md").write_text(md, encoding="utf-8")
```

Or manage the browser lifecycle yourself:

```python
from chrome_scraper.browser_api.client import BrowserAPIClient
from chrome_scraper.html_to_md.extract import extract_page

client = BrowserAPIClient(timeout=30.0)
with client.tab("my-tab"):
    payload = extract_page(
        "https://example.com",
        client,
        tab_ref="my-tab",
        timeout=30.0,
        scroll=True,
    )
```

## At a glance

**browser-api** — shared Chrome behind HTTP:

- Persistent profile with cookies/logins.
- Label-keyed tabs for concurrent clients.
- Tab lifecycle isolated per client (open → use → close).
- Background-tab patch so Chrome stays out of the way.
- Headless-mode UA cleaning (strips `HeadlessChrome`).
- macOS hide support (`--hide`).

**url-to-md** — single live URL to markdown:

- Uses the shared URL dispatcher and custom handlers automatically when a URL pattern is supported.
- Falls back to generic layout-preserving extraction for unknown sites.

**html-to-md** — layout-preserving markdown via Chrome CDP:

- Extracts every rendered text node with position and styling.
- Detects columns via x-start histogram peaks.
- Splits main/sidebar content via widest vertical gutter.
- Preserves code blocks, headings, links, inline code, lists.
- Row boundaries computed from *intersecting* column gap sets — long main-column paragraphs stay intact regardless of sidebar density.

**google-fetch** — multi-page Google scraping:

- Paginates through Google result pages.
- Visits each result link, dumps outerHTML + rendered markdown.
- Navigates back to search results after each fetch.
- Optional hostname filtering and result count limits.

**xcom-fetch** — x.com tweet scraping:

- Drives x.com's React search UI via native keyboard (Patchright).
- Virtual list scrolling to populate results.
- Visits each tweet permalink, dumps HTML + markdown.
- SPA-safe navigation; falls back to direct URL navigation if anchor click fails.
