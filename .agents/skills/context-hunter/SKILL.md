---
name: context-hunter
description: Gather material from the web and produce a comprehensive report in markdown.
---

# Context Hunter

Gather web sources for a topic, extract semantic keywords, and compose a structured report.

## When to use

User asks to research a topic, understand a technology, find examples, or needs a report on something they don't have local material for.

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
Read the `.md` files (frontmatter + content) for results that look most relevant and specific to the query.
Skip pages that are clearly off-topic (ads, unrelated domains, login walls with no content).

---

## Step 3 — Extract keywords → `keywords.md`

Read all selected `.md` frontmatters and start fuzzy grepping the files to get text fragments. Use these to extract and collect semantic keywords grouped by theme.
Groups should reflect the conceptual structure of the topic, not just word frequency.

Write `data/research/<slug>/keywords.md`:

```markdown
# Keywords: <query>

## <Group 1 name>
- keyword
- keyword
- ...

## <Group 2 name>
- ...
```

---

## Step 4 — Grep for evidence

Use the keywords to pull supporting excerpts from the `.md` files.

```bash
grep -r -i -h "<keyword>" data/research/<slug>/*.md --include="*.md"
```

Run for the most important keywords across groups. Collect the strongest excerpts — these are the evidence base for the report.

---

## Step 5 — Iterative query refinement

After grepping, assess the evidence base against the original query.
Ask: *are there gaps, unexplained terms, or subtopics that the current sources don't cover well enough?*

If yes, fire one or more focused follow-up queries:

```bash
uv run google-fetch --query "<refined query>" --tag <tag>
```

Refined queries with the **same `--tag`** go into the same folder so files accumulate across iterations.
After each new fetch, re-run Steps 2–4 on the new `.md` files and merge findings into `keywords.md`.

Repeat until one of these is true:
- The evidence base answers all major subtopics without obvious gaps
- A new query returns sources already seen or adds nothing new
- Three refinement rounds have been completed (hard stop to avoid loops)

---

## Step 6 — Compose `report.md`

Write `data/research/<slug>/report.md`, grounded in the md sources, refining it in terms of style and clarity.

Structure:
1. **Title** — `# <query>`
2. **Summary** — 2–4 sentences answering the query directly
3. **Sections** — one per keyword group; synthesise across sources, cite with `[Title](url)`
4. **Code examples** — if the topic is a programming technology, include practical usage examples extracted or synthesised from sources