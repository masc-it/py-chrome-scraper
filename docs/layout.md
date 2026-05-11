# Layout-to-markdown algorithm

`html-to-md` converts a rendered webpage into Markdown while preserving the visual layout — columns, sidebar, headings, code blocks, links, and list structure. It does this by extracting every visible text **node** from Chrome's layout tree via CDP, then reconstructing structure from positions and sizes.

No DOM-tree heuristics (no guessing which `<div>` is the sidebar, no fragile CSS selectors). Everything flows from where text actually appears on screen.

## Pipeline

```
┌────────────┐    ┌───────────────┐    ┌──────────────────┐
│  extract.js │───▶│  extract.py   │───▶│   render.py      │
│  walk DOM   │    │  scroll page  │    │  gutter split    │
│  collect    │    │  inject JS    │    │  columns → rows  │
│  positions  │    │  return items │    │  lines → blocks  │
└────────────┘    └───────────────┘    └──────────────────┘
                                                     │
                                                     ▼
                                            layout-preserving
                                               Markdown
```

## 1. Text-node extraction (`extract.js`)

A plain JS function injected into the page. Uses a `TreeWalker` over `document.body` to visit every `#text` node, then:

1. **Skip invisible** — `display:none`, `visibility:hidden`, `opacity:0`, `clip:rect(0…)`, `aria-hidden`, common screen-reader-only CSS classes (`sr-only`, `visually-hidden`, etc.).
2. **Skip structural tags** — `<script>`, `<style>`, `<noscript>`, `<template>`.
3. **Get bounding rects** — uses `Range.getClientRects()` to get one rectangle per rendered line box. A short unwrapped string gets one rect; a wrapped paragraph gets one rect per line.
4. **Filter degenerate rects** — zero-area rects, rects narrower than 2px, rects where char-width ratio is below 2px/char (clipped/sr-only).
5. **Annotate** — tag name, heading level, link href, list-item flag, `<code>`/`<pre>` membership, font size/weight.

Each output item:

```json
{
  "x": 120.5,        // left edge (CSS pixels, scroll-adjusted)
  "y": 340.0,        // top edge
  "w": 480.0,        // width
  "h": 18.0,         // height
  "text": "Hello",   // trimmed text content
  "tag": "P",        // parent tag
  "heading": "H2",   // or null
  "href": null,      // or URL string
  "is_li": false,    // inside a <li>
  "is_code": false,  // inside <code> or <pre>
  "pre_id": 1,       // unique <pre> id, or null
  "pre_lang": "python", // language class from <pre>
  "font_size": 16,
  "font_weight": "400"
}
```

In addition the JS returns viewport metadata (`innerWidth`, `innerHeight`, `scrollWidth`, `scrollHeight`) and the page title/URL.

## 2. Page preparation (`extract.py`)

Two preparatory steps before extraction:

**Scroll pass.** Runs a JS loop that scrolls the page top-to-bottom in steps of `max(400, innerHeight)` pixels, waiting 120ms per step. Stalls after 3 consecutive same-height readings (lazy-loaded content has settled). Scrolls back to top. This triggers lazy-loading images, ads, comments, and infinite-scroll content.

**Navigation.** Navigate to URL, wait for `load` event, then 500ms grace for JS frameworks to hydrate. Optional `--no-scroll` flag skips the scroll pass (useful for already-scrolled SPAs).

## 3. Gutter detection (`detect_widest_gutter`)

Before any column logic, split the page into **main content** and **right sidebar** by finding the widest empty vertical strip in the page's middle band (y-range covering the 15th–85th percentile of items).

Procedure:

1. Filter to items in the middle 70% of y-range, narrower than 55% of page width (excludes full-width banners and footers).
2. Build a histogram of item density across x-buckets (10px wide).
3. Find the longest contiguous run of low-density buckets between 30% and 85% of page width.
4. If the gap is wider than 40px, its midpoint is the gutter.

Why a dedicated gutter pass? The multi-column grid logic (next step) can fragment a long main-column paragraph when a dense sidebar has many small items at similar y-values. Splitting at the gutter first isolates each region, then each region runs its own column detection independently.

```
│                          │          │
│   main content           │  gutter  │  sidebar
│                          │          │
│  ┌──────────────────┐    │    ┌─────┴──┐
│  │ long paragraph   │    │    │ item 1 │
│  │ stays intact     │    │    │ item 2 │
│  │ because we split │    │    │ item 3 │
│  │ at the gutter    │    │    │ item 4 │
│  └──────────────────┘    │    └────────┘
│                          │          │
```

If no gutter is found, the whole page is treated as one region.

When a gutter split happens, sidebar content is rendered after a `---` horizontal rule.

## 4. Column detection (`detect_column_starts`)

Within each region, find the left edges of text columns. This handles two-column layouts, three-column footers, and everything in between.

Procedure:

1. Filter items narrower than 55% of page width (excludes full-width banners).
2. Restrict to the middle 80% of y-range (excludes headers/footers that might create false columns).
3. Build a histogram of item x-start positions (20px buckets).
4. For each bucket, track how many distinct **y-buckets** (80px tall) have items starting at that x-position.
5. A bucket counts as a column start only if its items span at least 4 distinct y-buckets. This filters out horizontal nav bars and toolbar rows (many items at the same y producing a false x-peak).
6. Merge peaks within 150px of each other (same column, slight x-jitter).

Returns a list of x-coordinates like `[0.0, 640.0]` for a two-column layout.

## 5. Column assignment (`assign_columns`)

Each item is assigned to the nearest column whose start is ≤ its left edge + 15px. If an item's right edge extends past the next column's start + 30px, it's marked as **spanning** (`col: -1`).

Special handling for `<pre>` blocks: all tokens in the same `<pre>` share the column of the leftmost token. This prevents syntax-highlighted code from being torn apart by slight x-variations in token positions.

```
col 0              col 1
│                  │
│  Text here...    │  Sidebar
│  More text       │  item
│  ┌─────────┐     │
│  │ code    │     │  ← all tokens assigned
│  │ block   │     │     to col 0
│  └─────────┘     │
│  Still col 0     │
```

## 6. Row boundaries (`find_row_boundaries`)

This is the most important step for preserving paragraph integrity. The algorithm finds y-coordinates where **every column has a vertical gap simultaneously**.

Procedure:

1. For each column (including spanning items which block boundaries everywhere), compute merged y-intervals.
2. Convert intervals to gap sets (vertical stripes with no items).
3. Intersect the gap sets across all columns — a common gap wider than 6px becomes a boundary.

This means a long main-column paragraph stays intact even when a right sidebar has many small items at closely-spaced y-values. The sidebar's many small gaps don't create boundaries because the main column doesn't have gaps at those same y-values.

```
Without intersection:        With intersection:
col 0       col 1           col 0       col 1
┌──────┐    ┌────┐          ┌──────┐    ┌────┐
│ para │    │  a │          │ para │    │  a │
│      │    │  b │          │      │    │  b │
│      │    │  c │          │      │    ├────┤ ← boundary only where
│      │    ├────┤ ← false  │      │    │  d │    BOTH columns gap
│      │    │  d │   bound  │      │    │  e │
│      │    │  e │          └──────┘    └────┘
└──────┘    └────┘
```

Boundaries are emitted as blank lines in the output.

## 7. Line grouping (`group_into_lines`)

Within each row-column cell, items are sorted by y then x, then clustered into visual lines using a tolerance-based algorithm:

1. Compute median item height across the whole page.
2. Tolerance = median height × 0.55 (a line can vary by about half a line-height).
3. Walk sorted items, tracking a running center. If a new item's center is within tolerance of the running center, it joins the current line. Otherwise, start a new line.

This handles sub/superscripts, multi-size inline elements, and slight baseline misalignment from different font sizes.

## 8. Code block detection

Within each column, consecutive lines whose items all belong to the same `<pre>` are grouped into a fenced code block:

1. If all items on a line share the same `pre_id` (and it's not null), it's a code line.
2. Extend the block forward while lines share that same `pre_id`.
3. Render as ```` ```<lang> ```` with:
   - Indent preserved by measuring x-origin offset relative to the block's left edge, divided by median character width.
   - Inter-token spacing reconstructed from pixel gaps between adjacent items on the same line.
   - Language from CSS class `language-*` or `data-lang` attribute.

```python
# Example: x-origin tracking preserves indentation
def render_code_block(block_lines, lang):
    # ...
```

## 9. Inline rendering

For non-code lines, each item is rendered as:

- **Plain text** — trimmed whitespace, single-spaced.
- **Inline code** — backtick-wrapped if `is_code` is true.
- **Links** — `[text](href)` format. Adjacent items sharing the same href are coalesced into one link.
- **Headings** — prepended with `#` × heading level.
- **List items** — prepended with `- `.

Adjacent items with a horizontal gap larger than 40px get a double-space separator (preserves visual separation without adding column breaks).

## 10. Row-column rendering

When a row has multiple columns, each column is rendered as a separate block (paragraph) separated by a blank line. This produces a vertical stacking that approximates the original multi-column layout without attempting CSS-level column positioning (which Markdown doesn't support).

## Limitations

| Scenario | Behavior |
|---|---|
| Wrapped inline text | Full text attached to first line rect; other line rects contribute position only. Acceptable fidelity loss. |
| CSS `columns` property | Treated as a single column (browser handles the column balancing; CDP doesn't expose the column breaks). |
| Absolute/fixed positioned overlays | Included if not clipped; may interleave with main content. |
| RTL text | Positions are correct but no `dir` annotation; rendering assumes LTR. |
| MathML / SVG text | Not extracted (TreeWalker only covers DOM text nodes). |
| Images | Not included in output. Only alt-text from `alt` attributes would appear (as text nodes adjacent to `<img>`). |
| Tables | Cells rendered as inline text in reading order; no table structure preserved. |
| Dynamically loaded content after scroll | Mitigated by the scroll pass; infinite-scroll beyond 50 passes or 3 stalls may miss content. |

## Diagnostics

Pass `-v` / `--verbose` to `html-to-md`:

```text
  page_width=1440 gutter=960.0
    region cols=[0, 640] rows=12
    region cols=[0] rows=8
```

This prints detected page width, gutter position (if any), column starts per region, and row count per region — useful for tuning or debugging layout splits.
