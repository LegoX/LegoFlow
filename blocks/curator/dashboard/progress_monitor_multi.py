#!/usr/bin/env python3
"""Multi-dataset dashboard generator.

Read LLM tagging results from each dataset's datasets/<id>/tags.jsonl
(difficulty_score / difficulty_label / tags / bug_class),
aggregate statistics and render a single-page HTML, supporting switching between 4 datasets:

  - self_made         LegoFlow-Instances
  - swe_rebench        SWE-rebench (nebius/SWE-rebench)
  - openswe_filtered   OpenSWE-filtered (SWE-Lego/openswe_filtered_for_rl)
  - scale_swe          Scale-SWE (AweAI-Team/Scale-SWE)

All datasets use the same LLM tagging scheme for comparability.
"""
from __future__ import annotations

import html
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

DASHBOARD_ROOT = Path(__file__).parent
DATASETS_DIR = DASHBOARD_ROOT / "datasets"
DEFAULT_OUTPUT = DASHBOARD_ROOT / "site" / "index.html"

# (id, display name, description)
DATASETS = [
    ("self_made", "LegoFlow-Instances", "Curator self-made instances (swegen-selfmade non-top5k + top5k)"),
    ("swe_rebench", "SWE-rebench", "Open-source dataset nebius/SWE-rebench"),
    ("swe_rebench_v2", "SWE-rebench-V2", "Open-source dataset nebius/SWE-rebench-V2"),
    ("openswe_filtered", "OpenSWE-filtered", "Open-source dataset SWE-Lego/openswe_filtered_for_rl"),
    ("scale_swe", "Scale-SWE", "Open-source dataset AweAI-Team/Scale-SWE"),
]

def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def score_stats(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "min": 0.0, "p25": 0.0, "median": 0.0, "mean": 0.0, "p75": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "min": min(values),
        "p25": percentile(values, 0.25),
        "median": percentile(values, 0.50),
        "mean": sum(values) / len(values),
        "p75": percentile(values, 0.75),
        "max": max(values),
    }


def score_bins(values: list[float]) -> dict[str, int]:
    bins = {"<=3": 0, "3.1-5": 0, "5.1-7": 0, "7.1-8": 0, ">8": 0}
    for score in values:
        if score <= 3.0:
            bins["<=3"] += 1
        elif score <= 5.0:
            bins["3.1-5"] += 1
        elif score <= 7.0:
            bins["5.1-7"] += 1
        elif score <= 8.0:
            bins["7.1-8"] += 1
        else:
            bins[">8"] += 1
    return bins


def aggregate_dataset(dataset_id: str) -> dict[str, Any]:
    """Aggregate one dataset's statistics from tags.jsonl."""
    tags_file = DATASETS_DIR / dataset_id / "tags.jsonl"
    tasks_file = DATASETS_DIR / dataset_id / "tasks.jsonl"

    difficulty_scores: list[float] = []
    difficulty_labels: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    area_counts: Counter[str] = Counter()
    bug_classes: Counter[str] = Counter()
    lang_counts: Counter[str] = Counter()
    tasks_with_topic = 0
    tasks_with_bug_class = 0
    patch_lines = 0
    patch_hunks = 0
    patch_files = 0
    patch_denom = 0
    tagged = 0

    if tags_file.exists():
        with tags_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                tagged += 1

                score = rec.get("difficulty_score")
                if score is not None:
                    difficulty_scores.append(float(score))
                label = rec.get("difficulty_label")
                if label:
                    difficulty_labels[str(label)] += 1

                # harbor 4-tag schema: [language, area, topic, bug_class]
                raw_tags = [str(t).strip().lower() for t in rec.get("tags", []) if str(t).strip()]
                if len(raw_tags) >= 1:
                    lang_counts[raw_tags[0]] += 1
                if len(raw_tags) >= 2:
                    area_counts[raw_tags[1]] += 1
                if len(raw_tags) >= 3 and raw_tags[2]:
                    tasks_with_topic += 1
                    topic_counts[raw_tags[2]] += 1

                bug_class = rec.get("bug_class")
                if bug_class:
                    bug_class = str(bug_class).strip().lower()
                    if bug_class:
                        tasks_with_bug_class += 1
                        bug_classes[bug_class] += 1

                ps = rec.get("patch_stats") or {}
                patch_denom += 1
                patch_lines += int(ps.get("lines") or 0)
                patch_hunks += int(ps.get("hunks") or 0)
                patch_files += int(ps.get("files") or 0)

    # Total dataset size (tasks.jsonl line count)
    total = 0
    if tasks_file.exists():
        with tasks_file.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    total += 1

    denom = patch_denom or 1
    return {
        "id": dataset_id,
        "total": total,
        "tagged": tagged,
        "difficulty_scores": difficulty_scores,
        "difficulty_stats": score_stats(difficulty_scores),
        "difficulty_bins": score_bins(difficulty_scores),
        "difficulty_labels": dict(difficulty_labels),
        "topics": dict(topic_counts.most_common()),
        "tasks_with_topic": tasks_with_topic,
        "areas": dict(area_counts.most_common()),
        "bug_classes": dict(bug_classes.most_common()),
        "tasks_with_bug_class": tasks_with_bug_class,
        "languages": dict(lang_counts.most_common()),
        "patch": {
            "avg_lines": patch_lines / denom,
            "avg_hunks": patch_hunks / denom,
            "avg_files": patch_files / denom,
        },
    }



# None means "not available", and must never render as 0 — a zero beside real
# figures is indistinguishable from a measurement.
UNAVAILABLE = "&mdash;"


def fmt_int(v) -> str:
    if v is None:
        return UNAVAILABLE
    try:
        return f"{int(v):,}"
    except Exception:
        return "0"


def fmt_float(v, digits: int = 2) -> str:
    if v is None:
        return UNAVAILABLE
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return "0"


def label_count(data: dict[str, Any], label: str) -> int:
    return int(data.get("difficulty_labels", {}).get(label, 0))


def render_label_bar(data: dict[str, Any]) -> str:
    easy = label_count(data, "easy")
    medium = label_count(data, "medium")
    hard = label_count(data, "hard")
    total = easy + medium + hard
    if total <= 0:
        return '<div class="stacked empty">no data</div>'
    parts = []
    for name, count, cls in (("easy", easy, "easy"), ("medium", medium, "medium"), ("hard", hard, "hard")):
        width = count / total * 100.0
        if count:
            parts.append(f'<div class="seg {cls}" style="width:{width:.3f}%" title="{name}: {count}"></div>')
    return f'<div class="stacked">{"".join(parts)}</div><div class="mini">{easy} / {medium} / {hard}</div>'


def render_tags(tags: dict[str, int], denominator: int, limit: int = 20) -> str:
    if not tags or denominator <= 0:
        return '<div class="muted">no tags data</div>'
    rows = []
    max_count = max(tags.values()) if tags else 1
    for tag, count in list(tags.items())[:limit]:
        percent = count / denominator * 100.0
        width = count / max_count * 100.0
        rows.append(
            '<div class="tag-row">'
            f'<span class="tag-name">{html.escape(tag)}</span>'
            '<span class="tag-track">'
            f'<span class="tag-fill" style="width:{width:.2f}%"></span>'
            '</span>'
            f'<span class="tag-count">{count:,} ({percent:.1f}%)</span>'
            '</div>'
        )
    return '<div class="tag-rows">' + "\n".join(rows) + "</div>"


def render_score_bins(bins: dict[str, int]) -> str:
    total = sum(bins.values()) or 1
    order = ["<=3", "3.1-5", "5.1-7", "7.1-8", ">8"]
    rows = []
    max_count = max(bins.values()) if bins else 1
    for key in order:
        count = bins.get(key, 0)
        percent = count / total * 100.0
        width = count / max_count * 100.0 if max_count else 0
        rows.append(
            '<div class="tag-row">'
            f'<span class="tag-name">{html.escape(key)}</span>'
            '<span class="tag-track">'
            f'<span class="tag-fill" style="width:{width:.2f}%"></span>'
            '</span>'
            f'<span class="tag-count">{count:,} ({percent:.1f}%)</span>'
            '</div>'
        )
    return '<div class="tag-rows">' + "\n".join(rows) + "</div>"


CSS = """
/* Palette: .claude/plugins/root-plugin/resources/DASHBOARD_PALETTE.md.
   Light is the default and dark is opt-in via :root[data-theme="dark"], matching
   the tracer dashboard's theme model (and the docs site's own light-first look). */
:root {
  color-scheme: light;
  --font-sans: ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  --c-bg: #fafaf7; --c-bg-2: #ffffff; --c-panel: #ffffff; --c-border: #e6e3da;
  --c-fg: #111111; --c-fg-dim: #4a453e; --c-fg-mute: #6b6b66; --c-fg-faint: #8c8c85;
  --c-accent: #b3431f; --c-accent-soft: #b3431f1f; --c-accent-border: #b3431f80;
  --c-good: #3f8f2f; --c-bad: #d03b3b; --c-warn: #c8860d; --c-violet: #4a3aa7;
  --c-track: #e6e3da;
  --c-method-bg: #b3431f14; --c-method-head: #b3431f;
  font-size: 15px;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --c-bg: #14110e; --c-bg-2: #1a171480; --c-panel: #1a1714; --c-border: #302a2499;
  --c-fg: #f0ede7; --c-fg-dim: #c9c2b6; --c-fg-mute: #a9a297; --c-fg-faint: #8a847a;
  --c-accent: #efa07c; --c-accent-soft: #efa07c33; --c-accent-border: #efa07c80;
  --c-good: #4a9440; --c-bad: #d03b3b; --c-warn: #fab219; --c-violet: #9085e9;
  --c-track: #302a24;
  --c-method-bg: #efa07c1f; --c-method-head: #f5c9b4;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--c-bg); color: var(--c-fg);
  font-family: var(--font-sans); -webkit-font-smoothing: antialiased; height: 100vh; }
code, pre, .mono { font-family: var(--font-mono); }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-thumb { background: var(--c-fg-faint); border-radius: 3px; }
.layout { display: flex; height: 100vh; overflow: hidden; }
.sidebar { width: 240px; flex-shrink: 0; border-right: 1px solid var(--c-border);
  background: var(--c-panel); display: flex; flex-direction: column; overflow: hidden; }
:root[data-theme="dark"] .sidebar { background: linear-gradient(180deg, #12100d 0%, var(--c-bg) 100%); }
.sidebar-logo { padding: 14px 16px; border-bottom: 1px solid var(--c-border); display: flex; align-items: center; gap: 10px; }
.logo-mark { width: 32px; height: 28px; border-radius: 7px; background: var(--c-accent);
  display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; font-size: 13px; }
.logo-title { font-size: 15px; font-weight: 650; }
.logo-sub { font-size: 12px; color: var(--c-fg-mute); }

.nav { padding: 8px; border-bottom: 1px solid var(--c-border); display: flex; flex-direction: column; gap: 2px; }
.nav-item { width: 100%; display: flex; align-items: center; gap: 10px; text-align: left;
  background: transparent; border: 1px solid transparent; color: var(--c-fg-dim);
  padding: 8px 10px; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; }
.nav-item:hover { background: var(--c-accent-soft); color: var(--c-fg); }
.nav-item.active { background: var(--c-accent-soft); border-color: var(--c-accent-border); color: var(--c-accent); }
.nav-icon { flex: 0 0 auto; width: 18px; text-align: center; opacity: .85; }
.nav-count { margin-left: auto; font-family: var(--font-mono); font-size: 12px; color: var(--c-fg-mute); }
.sidebar-section { padding: 10px 8px; flex: 1; overflow: auto; }
.section-label { text-transform: uppercase; font-size: 11px; letter-spacing: .06em; color: var(--c-fg-mute); padding: 0 8px 6px; font-weight: 700; }
.sidebar-stat { display: flex; align-items: baseline; justify-content: space-between; padding: 5px 8px; font-size: 13px; }
.sidebar-stat .l { color: var(--c-fg-mute); }
.sidebar-stat .v { font-weight: 650; font-family: var(--font-mono); }

.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar { padding: 10px 24px; border-bottom: 1px solid var(--c-border); display: flex;
  align-items: center; justify-content: space-between; gap: 16px; min-height: 48px; }
.topbar h1 { font-size: 17px; margin: 0; font-weight: 650; }
.topbar .sub { font-size: 13px; color: var(--c-fg-mute); margin-top: 2px; }
.topbar-actions { display: flex; gap: 8px; align-items: center; justify-content: flex-end; }
.icon-btn { width: 36px; height: 36px; padding: 0; display: inline-flex; align-items: center;
  justify-content: center; flex: 0 0 36px; background: var(--c-bg-2); border: 1px solid var(--c-border);
  color: var(--c-fg-dim); border-radius: 8px; cursor: pointer; }
.icon-btn:hover { color: var(--c-fg); border-color: var(--c-accent-border); }
.icon { width: 18px; height: 18px; display: block; fill: none; stroke: currentColor;
  stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.theme-toggle .theme-sun { display: none; }
.theme-toggle .theme-moon { display: block; }
:root[data-theme="dark"] .theme-toggle .theme-sun { display: block; }
:root[data-theme="dark"] .theme-toggle .theme-moon { display: none; }

.content { flex: 1; overflow: auto; padding: 18px 24px 32px; }
.page { display: none; }
.page.active { display: block; }

.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 16px; }
.card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px; padding: 12px 14px; }
.card .k { font-size: 12px; color: var(--c-fg-mute); }
.card .v { font-size: 22px; font-weight: 700; font-family: var(--font-mono); margin-top: 2px; }
.card .v small { font-size: 12px; color: var(--c-fg-mute); font-weight: 500; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.panel { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.panel h2 { font-size: 15px; margin: 0 0 10px; font-weight: 650; }
.panel h2 span { color: var(--c-fg-mute); font-weight: 400; font-size: 13px; margin-left: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--c-border); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--c-fg-mute); font-weight: 650; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
.stacked { display: flex; height: 9px; border-radius: 5px; overflow: hidden; background: var(--c-track); min-width: 110px; }
.stacked.empty { color: var(--c-fg-mute); font-size: 12px; background: transparent; }
.seg.easy { background: var(--c-good); } .seg.medium { background: var(--c-warn); } .seg.hard { background: var(--c-bad); }
.mini { font-size: 12px; color: var(--c-fg-mute); margin-top: 2px; font-family: var(--font-mono); }

/* Distribution bars. The old layout was a flex row with a fixed 230px label and a
   fixed 150px count, which left a wide dead gutter on short labels; and every list
   ran as one tall single column. Now each row is a 3-column grid sized to content,
   and the list itself flows into as many columns as the card is wide. */
.tag-rows { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 2px 22px; align-content: start; }
.tag-row { display: grid; grid-template-columns: minmax(80px, 150px) minmax(90px, 1fr) 78px;
  gap: 8px; align-items: center; padding: 2px 0; font-size: 12.5px; }
.tag-name { font-family: var(--font-mono); font-size: 12px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.tag-track { height: 7px; background: var(--c-track); border-radius: 4px; overflow: hidden; }
.tag-fill { display: block; height: 100%; background: var(--c-accent); }
.tag-count { text-align: right; color: var(--c-fg-mute); font-family: var(--font-mono); font-size: 11.5px; }
.tag-card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px; padding: 12px 14px; }
.tag-card h3 { font-size: 14px; margin: 0 0 8px; font-weight: 650; }
.tag-card h3 span { color: var(--c-fg-mute); font-weight: 400; font-size: 12px; margin-left: 6px; }
.muted { color: var(--c-fg-dim); font-size: 13px; }

/* Task List: one row per dataset, tracer's Jobs list shape. */
.ds-list { display: flex; flex-direction: column; gap: 8px; }
.ds-row { display: grid; grid-template-columns: minmax(180px, 1.4fr) repeat(5, minmax(76px, 1fr)) minmax(130px, 1.2fr);
  gap: 12px; align-items: center; width: 100%; text-align: left; cursor: pointer;
  background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px;
  padding: 12px 14px; color: var(--c-fg); font: inherit; font-size: 13px; }
.ds-row:hover { border-color: var(--c-accent-border); }
.ds-row.active { border-color: var(--c-accent-border); background: var(--c-accent-soft); }
.ds-row .name { font-weight: 650; font-size: 14px; }
.ds-row .desc { color: var(--c-fg-mute); font-size: 11.5px; margin-top: 2px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ds-row .k { color: var(--c-fg-mute); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
.ds-row .v { font-family: var(--font-mono); font-weight: 650; font-size: 14px; margin-top: 2px; }
.ds-detail { margin-top: 14px; }
.ds-panel { display: none; }
.ds-panel.active { display: block; }
.back-link { background: transparent; border: 0; color: var(--c-accent); cursor: pointer;
  font: inherit; font-size: 13px; padding: 0 0 8px; }

.method-card { grid-column: 1 / -1; background: var(--c-method-bg);
  border: 1px solid var(--c-accent-border); border-radius: 10px; padding: 14px 18px; }
.method-card h3 { font-size: 15px; margin: 0 0 10px; font-weight: 700; color: var(--c-method-head); }
.method-card h3 span { color: var(--c-accent); font-weight: 500; font-size: 13px; margin-left: 6px; }
.method-body { font-size: 13px; line-height: 1.7; color: var(--c-fg); }
.method-body strong { color: var(--c-fg); font-weight: 700; }
.method-body .mh { display: inline-block; color: var(--c-method-head); font-weight: 800; font-size: 13.5px; margin: 3px 0 1px; }
.method-body .dim { color: var(--c-violet); font-weight: 700; }
.method-body code { color: var(--c-accent); background: var(--c-accent-soft); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.wide-card { grid-column: 1 / -1; }
"""


# Single source of truth for the difficulty & tagging methodology card, shared by
# render_panel() (full regenerate) and inject_v2.py (surgical injection) so every
# panel shows identical, prominent, English-only copy. Weights/thresholds here
# mirror the dashboard tagger repos/swegen/tools/tag_task_metadata.py.
METHODOLOGY_HTML = """    <section class="tag-card method-card"><h3>Methodology <span>unified difficulty &amp; tagging</span></h3>
      <div class="method-body">
      <span class="mh">Difficulty Scoring (1&ndash;10 scale)</span><br>
      Composite weighted score over 5 dimensions:<br>
      &bull; <span class="dim">Patch scope (30%)</span>: lines changed, files affected, hunks count<br>
      &bull; <span class="dim">Logic complexity (25%)</span>: control-flow depth, branching, algorithmic sophistication<br>
      &bull; <span class="dim">Context breadth (20%)</span>: cross-module dependencies, API surface understanding<br>
      &bull; <span class="dim">Test complexity (15%)</span>: fixture setup, mock requirements, edge-case coverage<br>
      &bull; <span class="dim">Instruction complexity (10%)</span>: problem-statement clarity, implicit requirements<br>
      Each dimension is log-scaled, then the weighted sum is mapped to 1&ndash;10 and binned into
      <strong>easy</strong> (&le;4.0), <strong>medium</strong> (4.1&ndash;7.0), <strong>hard</strong> (&gt;7.0).<br><br>
      <span class="mh">Semantic Tagging</span><br>
      Each task is labelled with a 4-tuple <code>[language, area, topic, bug_class]</code>:<br>
      &bull; <span class="dim">language</span>: primary programming language (python, javascript, go, &hellip;)<br>
      &bull; <span class="dim">area</span>: architectural tier &isin; {backend, frontend, fullstack, cli, library, framework}<br>
      &bull; <span class="dim">topic</span>: functional domain / library (auth, database, api, numpy, &hellip;)<br>
      &bull; <span class="dim">bug_class</span>: root-cause category (logic-error, type-mismatch, race-condition, &hellip;)</div>
    </section>"""


def render_panel(ds_meta: tuple[str, str, str], data: dict[str, Any], active: bool) -> str:
    ds_id, display, desc = ds_meta
    stats = data["difficulty_stats"]
    tagged = data["tagged"]
    total = data["total"]
    tag_pct = (tagged / total * 100.0) if total else 0.0

    lang_rows = render_tags(data["languages"], tagged, limit=15)
    area_rows = render_tags(data["areas"], tagged, limit=6)
    topic_rows = render_tags(data["topics"], data["tasks_with_topic"], limit=25)
    bug_rows = render_tags(data["bug_classes"], data["tasks_with_bug_class"], limit=20)
    bin_rows = render_score_bins(data["difficulty_bins"])

    return f"""
<div class="ds-panel{' active' if active else ''}" data-ds="{ds_id}">
  <div class="cards">
    <div class="card"><div class="k">Total tasks</div><div class="v">{fmt_int(total)}</div></div>
    <div class="card"><div class="k">Mean difficulty</div><div class="v">{fmt_float(stats['mean'], 2)}</div></div>
    <div class="card"><div class="k">Median difficulty</div><div class="v">{fmt_float(stats['median'], 1)}</div></div>
    <div class="card"><div class="k">Avg patch lines</div><div class="v">{fmt_float(data['patch']['avg_lines'], 1)}</div></div>
    <div class="card"><div class="k">Avg patch files</div><div class="v">{fmt_float(data['patch']['avg_files'], 2)}</div></div>
  </div>

  <div class="panel">
    <h2>Difficulty distribution <span>{display}</span></h2>
    <table>
      <tr><th>Label breakdown</th><th>easy</th><th>medium</th><th>hard</th></tr>
      <tr>
        <td>{render_label_bar(data)}</td>
        <td>{fmt_int(label_count(data, 'easy'))}</td>
        <td>{fmt_int(label_count(data, 'medium'))}</td>
        <td>{fmt_int(label_count(data, 'hard'))}</td>
      </tr>
    </table>
    <table style="margin-top:14px;">
      <tr><th>count</th><th>min</th><th>p25</th><th>median</th><th>mean</th><th>p75</th><th>max</th></tr>
      <tr>
        <td>{fmt_int(stats['count'])}</td>
        <td>{fmt_float(stats['min'], 1)}</td>
        <td>{fmt_float(stats['p25'], 1)}</td>
        <td>{fmt_float(stats['median'], 1)}</td>
        <td>{fmt_float(stats['mean'], 2)}</td>
        <td>{fmt_float(stats['p75'], 1)}</td>
        <td>{fmt_float(stats['max'], 1)}</td>
      </tr>
    </table>
  </div>

  <div class="grid2">
    <section class="tag-card"><h3>Score bins</h3>{bin_rows}</section>
    <section class="tag-card"><h3>Languages</h3>{lang_rows}</section>
  </div>

  <div class="grid2" style="margin-top:18px;">
    <section class="tag-card"><h3>Area / tier</h3>{area_rows}</section>
    <section class="tag-card"><h3>Top topics</h3>{topic_rows}</section>
  </div>

  <div class="grid2" style="margin-top:18px;">
    <section class="tag-card wide-card"><h3>Bug classes</h3>{bug_rows}</section>
  </div>

</div>
"""


def combine_datasets(datasets: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold every dataset into one aggregate in the same shape aggregate_dataset
    returns, so render_panel can draw the global view unchanged."""
    scores: list[float] = []
    labels: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    areas: Counter[str] = Counter()
    bugs: Counter[str] = Counter()
    langs: Counter[str] = Counter()
    total = tagged = with_topic = with_bug = 0
    lines = hunks = files = 0.0

    for d in datasets:
        scores.extend(d["difficulty_scores"])
        labels.update(d["difficulty_labels"])
        topics.update(d["topics"])
        areas.update(d["areas"])
        bugs.update(d["bug_classes"])
        langs.update(d["languages"])
        total += d["total"]
        tagged += d["tagged"]
        with_topic += d["tasks_with_topic"]
        with_bug += d["tasks_with_bug_class"]
        # patch averages are per-tagged-task, so re-weight by each dataset's tagged count
        lines += d["patch"]["avg_lines"] * d["tagged"]
        hunks += d["patch"]["avg_hunks"] * d["tagged"]
        files += d["patch"]["avg_files"] * d["tagged"]

    denom = tagged or 1
    return {
        "id": "all",
        "total": total,
        "tagged": tagged,
        "difficulty_scores": scores,
        "difficulty_stats": score_stats(scores),
        "difficulty_bins": score_bins(scores),
        "difficulty_labels": dict(labels),
        "topics": dict(topics.most_common()),
        "tasks_with_topic": with_topic,
        "areas": dict(areas.most_common()),
        "bug_classes": dict(bugs.most_common()),
        "tasks_with_bug_class": with_bug,
        "languages": dict(langs.most_common()),
        "patch": {"avg_lines": lines / denom, "avg_hunks": hunks / denom, "avg_files": files / denom},
    }


def render_overview(combined: dict[str, Any], datasets: list[dict[str, Any]]) -> str:
    """Overview is global only: every dataset folded into one set of numbers.
    Per-dataset figures live on the Task List, so nothing is duplicated here."""
    stats = combined["difficulty_stats"]
    tagged = combined["tagged"]
    body = render_panel(("all", "All datasets", ""), combined, True)
    # render_panel emits a .ds-panel (a Task List detail shape); Overview is always on
    body = body.replace('<div class="ds-panel active" data-ds="all">', "", 1).rstrip()
    if body.endswith("</div>"):
        body = body[: -len("</div>")]
    return f"""
<div class="page active" id="page-overview">
  {body}
  <div class="grid2" style="margin-top:14px;">
{METHODOLOGY_HTML}
  </div>
</div>
"""


def render_task_list(datasets: list[dict[str, Any]]) -> str:
    """One row per dataset, tracer's Jobs-list shape: pick a row, get its detail."""
    by_id = {d["id"]: d for d in datasets}
    rows, details = [], []
    for ds_id, display, desc in DATASETS:
        d = by_id.get(ds_id)
        if d is None:
            continue
        stats = d["difficulty_stats"]
        rows.append(f"""
      <button class="ds-row" data-ds="{ds_id}">
        <span><span class="name">{html.escape(display)}</span>
          <span class="desc">{html.escape(desc)}</span></span>
        <span><span class="k">Tasks</span><span class="v">{fmt_int(d['total'])}</span></span>
        <span><span class="k">Tagged</span><span class="v">{fmt_int(d['tagged'])}</span></span>
        <span><span class="k">Mean diff.</span><span class="v">{fmt_float(stats['mean'], 2)}</span></span>
        <span><span class="k">Median</span><span class="v">{fmt_float(stats['median'], 1)}</span></span>
        <span><span class="k">Avg lines</span><span class="v">{fmt_float(d['patch']['avg_lines'], 0)}</span></span>
        <span><span class="k">Easy / medium / hard</span>{render_label_bar(d)}</span>
      </button>""")
        details.append(render_panel((ds_id, display, desc), d, False))
    return f"""
<div class="page" id="page-tasks">
  <div class="ds-list">{''.join(rows)}</div>
  <div class="ds-detail" id="ds-detail" hidden>
    <button class="back-link" id="ds-back">&larr; All datasets</button>
    {''.join(details)}
  </div>
</div>
"""


MOON_SVG = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M20.5 14.5A8.7 8.7 0 0 1 9.5 3.5a7 7 0 1 0 11 11Z"></path></svg>')
SUN_SVG = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
           '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path>'
           '<path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path>'
           '<path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path>'
           '<path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path>'
           '<path d="m19.07 4.93-1.41 1.41"></path></svg>')


def render_html(
    datasets: list[dict[str, Any]],
    output_path: Path,
    manifest: dict[str, Any] | None = None,
    combined: dict[str, Any] | None = None,
) -> str:
    """`manifest` is accepted and ignored — kept so existing callers keep working.

    `combined` lets a caller supply the global aggregate when it cannot be pooled
    here — the frozen-snapshot path has per-dataset summaries but no raw scores."""
    combined = combined or combine_datasets(datasets)
    grand_total = combined["total"]
    grand_tagged = combined["tagged"]
    mean = combined["difficulty_stats"]["mean"]
    n_ds = len([d for d in datasets if d.get("total") or d.get("tagged")]) or len(datasets)

    overview_sub = f"{n_ds} datasets · {fmt_int(grand_total)} tasks aggregated"
    tasks_sub = "Per-dataset breakdown — pick a dataset for its full profile"

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SWE Datasets Dashboard</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-logo">
      <div class="logo-mark">SL</div>
      <div><div class="logo-title">SWE Databoard</div>
        <div class="logo-sub">multi-dataset · LLM-tagged</div></div>
    </div>
    <div class="nav">
      <div class="section-label">Views</div>
      <button class="nav-item active" data-page="overview">
        <span class="nav-icon">◧</span><span class="nav-label">Overview</span></button>
      <button class="nav-item" data-page="tasks">
        <span class="nav-icon">☰</span><span class="nav-label">Task List</span>
        <span class="nav-count">{n_ds}</span></button>
    </div>
    <div class="sidebar-section">
      <div class="section-label">Global</div>
      <div class="sidebar-stat"><span class="l">Datasets</span><span class="v">{n_ds}</span></div>
      <div class="sidebar-stat"><span class="l">Total tasks</span><span class="v">{fmt_int(grand_total)}</span></div>
      <div class="sidebar-stat"><span class="l">Tagged</span><span class="v">{fmt_int(grand_tagged)}</span></div>
      <div class="sidebar-stat"><span class="l">Mean difficulty</span><span class="v">{fmt_float(mean, 2)}</span></div>
    </div>
  </aside>
  <div class="main">
    <div class="topbar">
      <div>
        <h1 id="page-title">Overview</h1>
        <div class="sub" id="page-sub">{overview_sub}</div>
      </div>
      <div class="topbar-actions">
        <button id="themeToggle" class="icon-btn theme-toggle" type="button"
          aria-label="Toggle theme" title="Toggle theme"><span class="theme-moon">{MOON_SVG}</span><span class="theme-sun">{SUN_SVG}</span></button>
      </div>
    </div>
    <div class="content">
      {render_overview(combined, datasets)}
      {render_task_list(datasets)}
    </div>
  </div>
</div>
<script>
var PAGE_META = {{
  overview: {{title: 'Overview', sub: {json.dumps(overview_sub)}}},
  tasks: {{title: 'Task List', sub: {json.dumps(tasks_sub)}}}
}};

/* Light is the default; dark follows a saved choice, else the OS preference. */
(function () {{
  var saved = null;
  try {{ saved = localStorage.getItem('curator-theme'); }} catch (e) {{}}
  var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = saved || (prefersDark ? 'dark' : 'light');
}})();

document.getElementById('themeToggle').addEventListener('click', function () {{
  var el = document.documentElement;
  var next = el.dataset.theme === 'dark' ? 'light' : 'dark';
  el.dataset.theme = next;
  try {{ localStorage.setItem('curator-theme', next); }} catch (e) {{}}
}});

function showPage(name) {{
  if (name !== 'tasks') {{ name = 'overview'; }}
  document.querySelectorAll('.page').forEach(function (p) {{
    p.classList.toggle('active', p.id === 'page-' + name);
  }});
  document.querySelectorAll('.nav-item').forEach(function (b) {{
    b.classList.toggle('active', b.dataset.page === name);
  }});
  var meta = PAGE_META[name];
  document.getElementById('page-title').textContent = meta.title;
  document.getElementById('page-sub').textContent = meta.sub;
  location.hash = name;
}}

document.querySelectorAll('.nav-item').forEach(function (b) {{
  b.addEventListener('click', function () {{ showPage(b.dataset.page); }});
}});

/* Task List: a row opens that dataset's detail; Back returns to the list. */
var detail = document.getElementById('ds-detail');
var list = document.querySelector('#page-tasks .ds-list');

document.querySelectorAll('.ds-row').forEach(function (row) {{
  row.addEventListener('click', function () {{
    var id = row.dataset.ds;
    document.querySelectorAll('.ds-row').forEach(function (o) {{
      o.classList.toggle('active', o === row);
    }});
    document.querySelectorAll('#ds-detail .ds-panel').forEach(function (p) {{
      p.classList.toggle('active', p.dataset.ds === id);
    }});
    detail.hidden = false;
    list.hidden = true;
    document.getElementById('page-sub').textContent = row.querySelector('.name').textContent;
  }});
}});

var back = document.getElementById('ds-back');
if (back) {{
  back.addEventListener('click', function () {{
    detail.hidden = true;
    list.hidden = false;
    document.querySelectorAll('.ds-row').forEach(function (o) {{ o.classList.remove('active'); }});
    document.getElementById('page-sub').textContent = PAGE_META.tasks.sub;
  }});
}}

showPage((location.hash || '').slice(1));
</script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc, encoding="utf-8")
    return doc


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate multi-dataset dashboard")
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    print(f"{'='*70}")
    print("Generate multi-dataset dashboard")
    print(f"{'='*70}")

    datasets = []
    for ds_id, display, desc in DATASETS:
        data = aggregate_dataset(ds_id)
        datasets.append(data)
        print(f"  {display:26s}: total={data['total']:>7,}  tagged={data['tagged']:>7,}  "
              f"mean_diff={data['difficulty_stats']['mean']:.2f}")

    render_html(datasets, args.output_html)
    print(f"{'='*70}")
    print(f"✓ generated: {args.output_html}  ({args.output_html.stat().st_size/1024:.0f} KB)")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()

