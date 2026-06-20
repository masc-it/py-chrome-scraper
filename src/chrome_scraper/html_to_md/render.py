"""Pure layout-to-markdown rendering for html-to-md.

Input: a list of text-node items (each with x/y/w/h and metadata about the
containing element) plus the page width. Output: a markdown string that tries
to preserve the visual layout of the page.
"""

from __future__ import annotations

import bisect
import re
import sys
from collections import defaultdict


def detect_widest_gutter(
    items: list[dict],
    page_width: float,
    *,
    min_gutter: float = 40.0,
    full_width_frac: float = 0.55,
    bucket: float = 10.0,
) -> float | None:
    """Find the x-midpoint of the widest empty strip in the middle band.

    Used as the primary main-vs-sidebar split so the multi-column grid logic
    doesn't accidentally fragment a long main-column paragraph when the
    sidebar on the right has many densely-stacked small items.
    """
    if not items:
        return None
    ys = sorted(it["y"] for it in items)
    y_lo = ys[int(len(ys) * 0.15)]
    y_hi = ys[int(len(ys) * 0.85)]
    content = [
        it
        for it in items
        if y_lo <= it["y"] <= y_hi and 2 < it["w"] < full_width_frac * page_width
    ]
    if not content:
        return None
    n = int(page_width / bucket) + 2
    hist = [0] * n
    for it in content:
        a = max(0, int(it["x"] / bucket))
        b = min(n - 1, int((it["x"] + it["w"]) / bucket))
        for k in range(a, b + 1):
            hist[k] += 1
    search_lo = int(0.30 * n)
    search_hi = int(0.85 * n)
    peak = max(hist[search_lo:search_hi], default=0)
    if peak < 6:
        return None
    threshold = max(3.0, peak * 0.15)
    best_len = 0
    best_mid: float | None = None
    run_start: int | None = None
    for i in range(search_lo, search_hi):
        if hist[i] < threshold:
            if run_start is None:
                run_start = i
            run_len = i - run_start + 1
            if run_len > best_len:
                best_len = run_len
                best_mid = (run_start + i) / 2 * bucket
        else:
            run_start = None
    if best_len * bucket < min_gutter:
        return None
    return best_mid


def detect_column_starts(
    items: list[dict],
    page_width: float,
    *,
    bucket: float = 20.0,
    merge_within: float = 150.0,
    full_width_frac: float = 0.55,
    min_y_spread: int = 4,
    y_bucket: float = 80.0,
) -> list[float]:
    """Detect column left edges by finding x-start histogram peaks.

    For a peak at x-bucket B to count as a real column, items at B must span
    at least ``min_y_spread`` distinct y-buckets. This filters out false peaks
    from horizontal nav bars or toolbar rows (many items at the same y).
    """
    narrow = [it for it in items if 4 < it["w"] < full_width_frac * page_width]
    if not narrow:
        return [0.0]
    ys = sorted(it["y"] for it in narrow)
    y_lo = ys[int(len(ys) * 0.10)]
    y_hi = ys[int(len(ys) * 0.90)]
    content = [it for it in narrow if y_lo <= it["y"] <= y_hi]
    if not content:
        return [0.0]
    bucket_to_ys: dict[int, set[int]] = defaultdict(set)
    for it in content:
        bucket_to_ys[int(it["x"] / bucket)].add(int(it["y"] / y_bucket))
    min_count = max(5, int(len(content) * 0.02))
    peaks = sorted(
        k
        for k, ys_set in bucket_to_ys.items()
        if len(ys_set) >= min(min_y_spread, min_count)
    )
    if not peaks:
        return [0.0]
    merge_buckets = max(1, int(merge_within / bucket))
    merged: list[int] = [peaks[0]]
    for k in peaks[1:]:
        if k - merged[-1] < merge_buckets:
            continue
        merged.append(k)
    return [k * bucket for k in merged]


def assign_item_column(it: dict, col_starts: list[float]) -> int:
    """Pick the column index for an item, or -1 if it spans multiple columns."""
    if not col_starts:
        return 0
    left = it["x"]
    right = left + it["w"]
    best = 0
    for i, cs in enumerate(col_starts):
        if cs <= left + 15:
            best = i
    if best + 1 < len(col_starts):
        next_cs = col_starts[best + 1]
        if right > next_cs + 30:
            return -1
    return best


def assign_columns(items: list[dict], col_starts: list[float]) -> None:
    # All tokens inside the same <pre> share the column of its leftmost token,
    # so a syntax-highlighted block isn't torn apart by per-token x-positions.
    pre_leftmost: dict[int, float] = {}
    for it in items:
        pre_id = it.get("pre_id")
        if pre_id is None:
            continue
        lx = it["x"]
        if pre_id not in pre_leftmost or lx < pre_leftmost[pre_id]:
            pre_leftmost[pre_id] = lx
    pre_col: dict[int, int] = {
        pid: assign_item_column({"x": lx, "w": 1.0}, col_starts)
        for pid, lx in pre_leftmost.items()
    }
    for it in items:
        pre_id = it.get("pre_id")
        if pre_id is not None and pre_id in pre_col:
            it["col"] = pre_col[pre_id]
        else:
            it["col"] = assign_item_column(it, col_starts)


def _merge_y_intervals(items: list[dict]) -> list[tuple[float, float]]:
    intervals = sorted((it["y"], it["y"] + it["h"]) for it in items)
    merged: list[list[float]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1] + 2:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def _gaps_from_merged(
    merged: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if not merged:
        return [(float("-inf"), float("inf"))]
    gaps: list[tuple[float, float]] = [(float("-inf"), merged[0][0])]
    for a, b in zip(merged, merged[1:]):
        gaps.append((a[1], b[0]))
    gaps.append((merged[-1][1], float("inf")))
    return gaps


def _intersect_gap_sets(
    gap_sets: list[list[tuple[float, float]]],
) -> list[tuple[float, float]]:
    if not gap_sets:
        return []
    result = list(gap_sets[0])
    for gaps in gap_sets[1:]:
        new: list[tuple[float, float]] = []
        i = j = 0
        while i < len(result) and j < len(gaps):
            a, b = result[i]
            c, d = gaps[j]
            lo, hi = max(a, c), min(b, d)
            if lo < hi:
                new.append((lo, hi))
            if b < d:
                i += 1
            else:
                j += 1
        result = new
    return result


def find_row_boundaries(items: list[dict], *, min_gap: float = 6.0) -> list[float]:
    """Row boundaries where *every* column has a vertical gap simultaneously.

    For each column compute merged y-intervals (including spanning items, so
    they block boundaries everywhere), then intersect gap sets across cols.
    A common gap wider than ``min_gap`` becomes a boundary. This lets a long
    main-column paragraph stay intact even when a right-sidebar column has
    many small items at aligned y-values.
    """
    if not items:
        return [float("-inf"), float("inf")]
    spanning = [it for it in items if it.get("col") == -1]
    by_col: dict[int, list[dict]] = defaultdict(list)
    for it in items:
        if it.get("col", 0) != -1:
            by_col[it.get("col", 0)].append(it)
    if not by_col:
        merged = _merge_y_intervals(spanning)
        bounds: list[float] = [float("-inf")]
        for a, b in zip(merged, merged[1:]):
            if b[0] - a[1] >= min_gap:
                bounds.append((a[1] + b[0]) / 2)
        bounds.append(float("inf"))
        return bounds
    gap_sets: list[list[tuple[float, float]]] = []
    for col_items in by_col.values():
        merged = _merge_y_intervals(col_items + spanning)
        gap_sets.append(_gaps_from_merged(merged))
    common = _intersect_gap_sets(gap_sets)
    bounds = [float("-inf")]
    for s, e in common:
        if s == float("-inf") or e == float("inf"):
            continue
        if e - s >= min_gap:
            bounds.append((s + e) / 2)
    bounds.append(float("inf"))
    return bounds


def assign_row(y: float, bounds: list[float]) -> int:
    return bisect.bisect_right(bounds, y) - 1


def group_into_lines(
    items: list[dict], *, y_tolerance_ratio: float = 0.55
) -> list[list[dict]]:
    if not items:
        return []
    heights = sorted(max(it["h"], 1.0) for it in items)
    med_h = heights[len(heights) // 2]
    tol = med_h * y_tolerance_ratio
    sorted_items = sorted(items, key=lambda it: (it["y"], it["x"]))
    lines: list[list[dict]] = []
    cur: list[dict] = []
    cur_center: float | None = None
    for it in sorted_items:
        c = it["y"] + it["h"] / 2
        if cur_center is None or abs(c - cur_center) <= tol:
            cur.append(it)
            cur_center = (
                c
                if cur_center is None
                else (cur_center * (len(cur) - 1) + c) / len(cur)
            )
        else:
            lines.append(sorted(cur, key=lambda x: x["x"]))
            cur = [it]
            cur_center = c
    if cur:
        lines.append(sorted(cur, key=lambda x: x["x"]))
    return lines


def _coalesce_same_href(line: list[dict]) -> list[dict]:
    """Merge adjacent items sharing the same non-empty href into one item."""
    out: list[dict] = []
    for it in line:
        if (
            out
            and it.get("href")
            and out[-1].get("href") == it["href"]
            and not it.get("is_code")
        ):
            merged = dict(out[-1])
            merged["text"] = f"{merged['text']} {it['text']}"
            merged["w"] = (it["x"] + it["w"]) - merged["x"]
            out[-1] = merged
        else:
            out.append(it)
    return out


def render_item(it: dict) -> str:
    text = it["text"]
    if it.get("is_code") and not it.get("heading"):
        text = f"`{text}`"
    if it.get("href"):
        text = f"[{text}]({it['href']})"
    return text


def render_inline_line(line: list[dict]) -> str:
    line = _coalesce_same_href(line)
    parts: list[str] = []
    last_right: float | None = None
    for it in line:
        chunk = render_item(it)
        if last_right is not None and it["x"] - last_right > 40:
            parts.append("  ")
        parts.append(chunk)
        last_right = it["x"] + it["w"]
    joined = " ".join(p for p in parts if p)
    return re.sub(r"[ \t]+", " ", joined).strip()


def line_prefix(line: list[dict]) -> str:
    for it in line:
        if it.get("heading"):
            return "#" * int(it["heading"][1]) + " "
    if any(it.get("is_li") for it in line):
        return "- "
    return ""


def render_code_block(block_lines: list[list[dict]], lang: str) -> str:
    """Emit a fenced code block. Preserve indent and inter-token spacing via x."""
    all_items = [it for line in block_lines for it in line]
    if not all_items:
        return ""
    x_origin = min(it["x"] for it in all_items)
    widths = [
        it["w"] / max(len(it["text"]), 1) for it in all_items if it["text"].strip()
    ]
    char_w = sorted(widths)[len(widths) // 2] if widths else 8.0
    char_w = max(char_w, 4.0)

    lines_out: list[str] = []
    for line in block_lines:
        if not line:
            lines_out.append("")
            continue
        line = sorted(line, key=lambda it: it["x"])
        first_x = line[0]["x"]
        indent = max(0, round((first_x - x_origin) / char_w))
        buf: list[str] = [" " * indent]
        prev_right: float | None = None
        for it in line:
            if prev_right is not None:
                gap_px = it["x"] - prev_right
                # Near-zero gap = no source whitespace (adjacent syntax spans).
                spaces = 0 if gap_px < char_w * 0.4 else max(1, round(gap_px / char_w))
                buf.append(" " * spaces)
            buf.append(it["text"])
            prev_right = it["x"] + it["w"]
        lines_out.append("".join(buf).rstrip())
    body = "\n".join(lines_out)
    return f"```{lang}\n{body}\n```"


def render_column(lines: list[list[dict]]) -> str:
    if not lines:
        return ""
    heights = [max(it["h"], 1.0) for line in lines for it in line]
    med_h = sorted(heights)[len(heights) // 2]

    out: list[str] = []
    prev_bottom: float | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        pre_ids = {it.get("pre_id") for it in line}
        if len(pre_ids) == 1 and None not in pre_ids:
            pre_id = next(iter(pre_ids))
            lang = next(
                (it.get("pre_lang", "") for it in line if it.get("pre_lang")), ""
            )
            block = [line]
            j = i + 1
            while j < len(lines):
                ids = {it.get("pre_id") for it in lines[j]}
                if ids == {pre_id}:
                    block.append(lines[j])
                    j += 1
                else:
                    break
            top = min(it["y"] for it in block[0])
            bottom = max(it["y"] + it["h"] for it in block[-1])
            if prev_bottom is not None and top - prev_bottom > med_h * 0.6:
                out.append("")
            out.append(render_code_block(block, lang))
            prev_bottom = bottom
            i = j
            continue

        top = min(it["y"] for it in line)
        bottom = max(it["y"] + it["h"] for it in line)
        rendered = render_inline_line(line)
        if not rendered:
            i += 1
            continue
        if prev_bottom is not None and top - prev_bottom > med_h * 0.8:
            out.append("")
        out.append(line_prefix(line) + rendered)
        prev_bottom = bottom
        i += 1

    collapsed: list[str] = []
    for ln in out:
        if ln == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(ln)
    return "\n".join(collapsed).strip()


def _split_line_at_gaps(line: list[dict], gap_px: float) -> list[list[dict]]:
    """Split a visually-single line into sub-blocks where either:

    - there's a very large horizontal gap (distant regions sharing a y),
    - the heading status flips between adjacent items (typical when a main
      content item happens to share a y with a sidebar <h2>).
    """
    if not line:
        return []
    line = sorted(line, key=lambda it: it["x"])
    groups = [[line[0]]]
    for prev, curr in zip(line, line[1:]):
        gap = curr["x"] - (prev["x"] + prev["w"])
        heading_flip = bool(prev.get("heading")) != bool(curr.get("heading"))
        if gap > gap_px or (heading_flip and gap > 40):
            groups.append([curr])
        else:
            groups[-1].append(curr)
    return groups


def render_page(items: list[dict], page_width: float, *, verbose: bool = False) -> str:
    """Render the full set of text-node items to layout-preserving markdown."""
    # Primary split: widest vertical gutter separates main content from a
    # right-hand sidebar when one exists. Each side is then rendered as its
    # own multi-column region.
    gutter = detect_widest_gutter(items, page_width)
    heights = sorted(max(it["h"], 1.0) for it in items)
    med_h = heights[len(heights) // 2] if heights else 16.0
    if verbose:
        print(
            f"  page_width={page_width:.0f} gutter={gutter!r}",
            file=sys.stderr,
        )
    if gutter is not None:
        main_items: list[dict] = []
        right_items: list[dict] = []
        for it in items:
            mid = it["x"] + it["w"] / 2
            (main_items if mid < gutter else right_items).append(it)
        main_md = _render_region(main_items, page_width, med_h, verbose=verbose)
        right_md = _render_region(right_items, page_width, med_h, verbose=verbose)
        if not right_md:
            return main_md + "\n"
        return f"{main_md}\n\n---\n\n{right_md}\n"
    return _render_region(items, page_width, med_h, verbose=verbose) + "\n"


def _render_region(
    items: list[dict],
    page_width: float,
    med_h: float,
    *,
    verbose: bool = False,
) -> str:
    if not items:
        return ""
    col_starts = detect_column_starts(items, page_width)
    assign_columns(items, col_starts)
    bounds = find_row_boundaries(items, min_gap=max(6.0, med_h * 0.45))
    if verbose:
        print(
            f"    region cols={[int(c) for c in col_starts]} rows={len(bounds) - 1}",
            file=sys.stderr,
        )

    rows: dict[int, list[dict]] = defaultdict(list)
    for it in items:
        row_idx = assign_row(it["y"] + it["h"] / 2, bounds)
        rows[row_idx].append(it)

    narrow_row_split = max(500.0, page_width * 0.4)

    blocks: list[str] = []
    for row_idx in sorted(rows.keys()):
        row_items = rows[row_idx]
        lines = group_into_lines(row_items)
        if len(lines) <= 1 or len(col_starts) < 2:
            if lines:
                for sub in _split_line_at_gaps(lines[0], narrow_row_split):
                    block = render_column([sub])
                    if block:
                        blocks.append(block)
            continue
        col_cells: dict[int, list[dict]] = defaultdict(list)
        for it in row_items:
            col_cells[it["col"]].append(it)
        for col in sorted(col_cells.keys(), key=lambda c: -1 if c == -1 else c):
            cell_lines = group_into_lines(col_cells[col])
            block = render_column(cell_lines)
            if block:
                blocks.append(block)
    return "\n\n".join(b for b in blocks if b) + "\n"
