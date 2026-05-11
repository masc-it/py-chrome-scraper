---
name: xcom-search
description: Search X.com for trends, breaking news, and real-time discussions. Use when user asks about what's trending, viral topics, or live commentary more likely found on X than web search.
---

# X.com search

Search X.com for tweets, trends, and real-time conversations. Uses `xcom-fetch` which scrapes X search results via a headless browser.

## When to use

User asks about:
- Current trends or trending topics (Italy, world, specific locale)
- Breaking news or live commentary
- What people are saying right now about an event
- Viral posts, memes, hot takes
- Real-time sentiment on a topic

**Not for:** evergreen information, documentation, academic papers, or topics better suited to web search.

## Inputs

- **query** — search term or topic (required)
- **out_dir** — where to save output (default: `data/research/xcom-<query>/`)

## Output structure

```
data/research/xcom-<slug>/
  results.json          ← index of all found tweets (permalink, author, text)
  01-<author>-<id>.html ← raw HTML per tweet page
  01-<author>-<id>.md   ← converted markdown (frontmatter: permalink, author, text)
  ...
  report.md             ← final report (you write this)
```

---

## Step 1 — Fetch

Run `xcom-fetch` to search and download tweets.

```bash
xcom-fetch --query "<query>" --max-tweets 20
```

Useful flags:
- `--max-tweets N` — how many tweets to fetch (default: 20)
- `--from <profile>` — restrict to a single X account
- `--out-dir <path>` — override output directory
- `--timeout N` — per-operation timeout in seconds (default: 30)

**Parallel runs:** launch multiple queries at once with different `--out-dir`:

```bash
xcom-fetch --query "trend italia" --out-dir data/research/xcom-trend-italia &
xcom-fetch --query "italia oggi" --out-dir data/research/xcom-italia-oggi &
wait
```

---

## Step 2 — Read results

Read `results.json`. It contains `permalink`, `author`, and `text` for each tweet.
Read the `.md` files for full tweet content and context.

---

## Step 3 — Compose `report.md`

Write a report (overwrite `report.md` in the output dir) synthesising findings:

```markdown
# Trends: <query>

## Summary
2-4 sentences capturing the main trends or themes found.

## Top tweets
- [@author](permalink): "excerpt..." — context/why relevant
- ...

## Themes
Group tweets by theme. Synthesise across sources, cite with permalinks.

## Raw data
See `results.json` for full tweet list.
```

# Summary for the user

When the process if complete, provide a summary for the user in form of bullet list, grouping the tweets in topics and pointing to the full report.md path.
