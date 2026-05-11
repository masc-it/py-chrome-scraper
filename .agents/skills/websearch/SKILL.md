---
name: websearch
description: Gather up-to-date sources via google.
---

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

Run `google-fetch` to search and download results. It's a global cli command.

```bash
google-fetch --query "<query>" --tag <tag> --max-results 3
```

Useful flags:
- `--tag <tag>` — scope output into a subfolder (default: auto timestamp). Use a fixed tag for iterative refinement into the same folder, or unique tags for parallel runs.

Produces `results.json` and one `.html` + `.md` pair per result.

**Parallel runs:** launch up to 6 queries at once with different `--tag` values so outputs don't collide:

```bash
google-fetch --query "DQN paper" --tag dqn &
google-fetch --query "Double DQN" --tag ddqn &
wait
```

You can then use the gathered material to proceed with your task at hand as usual.