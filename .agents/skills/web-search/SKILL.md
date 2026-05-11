---
name: web-search
description: Answer user queries using up-to-date web sources via google.
---

# Web search

Gather web sources for a topic, extract semantic keywords, and compose a structured report.

## When to use

User asks for a topic or something that requires online google search.

## Inputs

- **query** — the search topic (required)
- **out_dir** — where to save everything (default: `data/research/<query-slug>/`)

## Output structure

```
data/research/<query-slug>/<tag>/
  results.json          ← index of all found pages (title, url)
  01-<slug>.html        ← raw HTML per result
  01-<slug>.md          ← converted markdown (frontmatter: title, url, position, description)
  ...
  keywords.md           ← semantic keyword groups (you write this)
  report.md             ← final report (you write this)
```

---

## Step 1 — Fetch

Run `google-fetch` to search and download results.

```bash
uv run google-fetch --query "<query>" --tag <tag>
```

Useful flags:
- `--tag <tag>` — scope output into a subfolder (default: auto timestamp). Use a fixed tag for iterative refinement into the same folder, or unique tags for parallel runs.
- `--num-pages N` — scrape N Google result pages (default: 1, ~10 results each)
- `--max-results N` — stop after N results total
- `--allowed-hosts arxiv.org github.com` — only fetch from specific domains
- `--results-per-page 20` — override results per Google page
- `--out-dir` — override the entire output path (bypasses `<query-slug>/<tag>` nesting)

Produces `results.json` and one `.html` + `.md` pair per result.

**Parallel runs:** launch up to 6 queries at once with different `--tag` values so outputs don't collide:

```bash
uv run google-fetch --query "DQN paper" --tag dqn &
uv run google-fetch --query "Double DQN" --tag ddqn &
wait
```

---

## Step 2 — Select relevant sources

Read `results.json`. It contains title, url, position for each result.
Read the `.md` files (frontmatter + content 10 lines head) for results that look most relevant and specific to the query.
Skip pages that are clearly off-topic (ads, unrelated domains, login walls with no content).

---

## Step 3 — Iterative query refinement

Assess the evidence base against the original query.
Ask: *are there gaps, unexplained terms, or subtopics that the current sources don't cover well enough?*

If yes, fire one or more focused follow-up queries:

```bash
uv run google-fetch --query "<refined query>" --tag <tag>
```

Refined queries with the **same `--tag`** go into the same folder so files accumulate across iterations.
After each new fetch, re-run Steps 2-3 and at the end, answer to the user.

Repeat until one of these is true:
- The evidence base answers all major subtopics without obvious gaps
- A new query returns sources already seen or adds nothing new
- Three refinement rounds have been completed (hard stop to avoid loops)

# Summary for the user

When the process if complete, provide a summary for the user in form of bullet list, grouping the information and pointing to the full report.md path.