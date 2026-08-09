#!/usr/bin/env python3
"""Rebuild site/index.html from the frozen snapshot's own numbers.

progress_monitor_multi.py owns the layout but needs datasets/<id>/{tasks,tags}.jsonl,
and those inputs are not in this checkout — the five datasets survive only as
rendered HTML inside the deployed snapshot. Rather than transplanting that markup,
this script *parses* it back into the dicts aggregate_dataset() would have
returned, then hands them to the normal renderer. The published page is therefore
produced by the same code path as a real run, and every figure on it is a real
measurement lifted from the snapshot.

What the snapshot does not carry is the per-task difficulty scores, so pooled
order statistics (median, quartiles, min/max) cannot be recomputed across
datasets. Those are passed as None and render as an em dash. The pooled **mean**
is recoverable: it is the per-dataset means weighted by each dataset's tagged
count, which the difficulty bars do carry.

Delete this once datasets/ is available — a full regenerate supersedes it.

Usage: python3 migrate_frozen_shell.py [--in site/index.html] [--out site/index.html]
"""
from __future__ import annotations

import argparse
import html as html_mod
import re
from pathlib import Path

import progress_monitor_multi as pm

DASH = Path(__file__).parent

PANEL_RE = re.compile(r'<div class="ds-panel[^"]*" data-ds="([^"]+)">')
CARD_RE = re.compile(r'<div class="card"><div class="k">([^<]+)</div><div class="v">([^<]*?)(?:<|$)')
SEG_RE = re.compile(r'title="(easy|medium|hard): (\d+)"')
TAGCARD_RE = re.compile(r'<h3>([^<]+?)(?:\s*<span>.*?</span>)?</h3>(.*?)(?=<h3>|$)', re.S)
TAGROW_RE = re.compile(
    r'<span class="tag-name">([^<]*)</span>.*?<span class="tag-count">([\d,]+)', re.S
)

# tag-card heading -> the aggregate_dataset() key it populates
SECTION_KEYS = {
    "language": "languages",
    "area": "areas",
    "topic": "topics",
    "bug class": "bug_classes",
    "score": "difficulty_bins",
    "difficulty": "difficulty_bins",
}


def _num(text: str) -> float | None:
    text = text.replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_panel(block: str, ds_id: str) -> dict:
    cards = {k.strip().lower(): v.strip() for k, v in CARD_RE.findall(block)}
    labels = {k: int(v) for k, v in SEG_RE.findall(block)}
    tagged = sum(labels.values())

    sections: dict[str, dict[str, int]] = {}
    for heading, body in TAGCARD_RE.findall(block):
        key = None
        h = heading.strip().lower()
        for needle, mapped in SECTION_KEYS.items():
            if needle in h:
                key = mapped
                break
        if key is None:
            continue
        rows = {
            html_mod.unescape(name): int(count.replace(",", ""))
            for name, count in TAGROW_RE.findall(body)
        }
        if rows:
            sections.setdefault(key, {}).update(rows)

    total = _num(cards.get("total tasks", "")) or 0
    mean = _num(cards.get("mean difficulty", ""))
    median = _num(cards.get("median difficulty", ""))
    topics = sections.get("topics", {})
    bugs = sections.get("bug_classes", {})

    return {
        "id": ds_id,
        "total": int(total),
        "tagged": tagged,
        # no raw scores in rendered HTML — only the summary figures survive
        "difficulty_scores": [],
        "difficulty_stats": {
            "count": tagged,
            "min": None, "p25": None, "median": median,
            "mean": mean, "p75": None, "max": None,
        },
        "difficulty_bins": sections.get("difficulty_bins", {}),
        "difficulty_labels": labels,
        "topics": topics,
        "tasks_with_topic": sum(topics.values()),
        "areas": sections.get("areas", {}),
        "bug_classes": bugs,
        "tasks_with_bug_class": sum(bugs.values()),
        "languages": sections.get("languages", {}),
        "patch": {
            "avg_lines": _num(cards.get("avg patch lines", "")),
            "avg_hunks": None,
            "avg_files": _num(cards.get("avg patch files", "")),
        },
    }


def parse_snapshot(doc: str) -> list[dict]:
    starts = [(m.start(), m.group(1)) for m in PANEL_RE.finditer(doc)]
    if not starts:
        raise SystemExit("no .ds-panel blocks found — is this the frozen snapshot?")
    script_at = doc.find("<script>")
    if script_at == -1:
        script_at = len(doc)

    out = []
    for i, (pos, ds_id) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else script_at
        out.append(parse_panel(doc[pos:end], ds_id))
    return out


def pool(datasets: list[dict]) -> dict:
    """Global aggregate. Counts add; the mean is weighted by tagged count; order
    statistics need raw scores and so stay unavailable."""
    from collections import Counter

    labels, topics, areas, bugs, langs, bins = (Counter() for _ in range(6))
    total = tagged = 0
    mean_num = line_num = file_num = 0.0
    mean_den = line_den = file_den = 0

    for d in datasets:
        total += d["total"]
        tagged += d["tagged"]
        labels.update(d["difficulty_labels"])
        topics.update(d["topics"])
        areas.update(d["areas"])
        bugs.update(d["bug_classes"])
        langs.update(d["languages"])
        bins.update(d["difficulty_bins"])
        w = d["tagged"]
        if d["difficulty_stats"]["mean"] is not None and w:
            mean_num += d["difficulty_stats"]["mean"] * w
            mean_den += w
        if d["patch"]["avg_lines"] is not None and w:
            line_num += d["patch"]["avg_lines"] * w
            line_den += w
        if d["patch"]["avg_files"] is not None and w:
            file_num += d["patch"]["avg_files"] * w
            file_den += w

    return {
        "id": "all",
        "total": total,
        "tagged": tagged,
        "difficulty_scores": [],
        "difficulty_stats": {
            "count": tagged,
            "min": None, "p25": None, "median": None,
            "mean": (mean_num / mean_den) if mean_den else None,
            "p75": None, "max": None,
        },
        "difficulty_bins": dict(bins),
        "difficulty_labels": dict(labels),
        "topics": dict(topics.most_common()),
        "tasks_with_topic": sum(topics.values()),
        "areas": dict(areas.most_common()),
        "bug_classes": dict(bugs.most_common()),
        "tasks_with_bug_class": sum(bugs.values()),
        "languages": dict(langs.most_common()),
        "patch": {
            "avg_lines": (line_num / line_den) if line_den else None,
            "avg_hunks": None,
            "avg_files": (file_num / file_den) if file_den else None,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="src", type=Path, default=DASH / "site" / "index.html")
    ap.add_argument("--out", dest="dst", type=Path, default=DASH / "site" / "index.html")
    args = ap.parse_args()

    datasets = parse_snapshot(args.src.read_text(encoding="utf-8"))
    combined = pool(datasets)

    for d in datasets:
        print(f"  {d['id']:20s} total={d['total']:>7,} tagged={d['tagged']:>7,} "
              f"mean={d['difficulty_stats']['mean']} langs={len(d['languages'])}")
    print(f"  {'POOLED':20s} total={combined['total']:>7,} tagged={combined['tagged']:>7,} "
          f"mean={combined['difficulty_stats']['mean']}")

    pm.render_html(datasets, args.dst, combined=combined)
    print(f"wrote {args.dst} ({args.dst.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
