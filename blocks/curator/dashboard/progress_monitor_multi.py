#!/usr/bin/env python3
"""Validate prepared tags.jsonl files and render the multi-dataset dashboard.

The renderer is deliberately read-only. Self-made metadata is copied from each
task.toml by export_self_made.py; external metadata is prepared by the canonical
tag_task_metadata.py tool before this script runs.
"""
from __future__ import annotations

import html
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from dataset_registry import DATASETS as DATASET_REGISTRY
from dataset_registry import DatasetSpec

DASHBOARD_ROOT = Path(__file__).parent
DATASETS_DIR = DASHBOARD_ROOT / "datasets"
DEFAULT_OUTPUT = DASHBOARD_ROOT / "site" / "index.html"

DATASETS = DATASET_REGISTRY


class DashboardDataError(ValueError):
    """Raised when a prepared tags.jsonl file is missing or malformed."""

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


def load_tag_records(dataset_id: str, datasets_dir: Path = DATASETS_DIR) -> list[dict[str, Any]]:
    """Load and validate one prepared metadata record per task."""
    tags_file = datasets_dir / dataset_id / "tags.jsonl"
    if not tags_file.is_file():
        raise DashboardDataError(
            f"{dataset_id}: missing {tags_file}; prepare tags.jsonl before rendering"
        )

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    errors: list[str] = []
    with tags_file.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            prefix = f"{tags_file}:{line_number}"
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{prefix}: invalid JSON: {exc.msg}")
                continue
            if not isinstance(record, dict):
                errors.append(f"{prefix}: expected a JSON object")
                continue

            instance_id = record.get("instance_id")
            if not isinstance(instance_id, str) or not instance_id.strip():
                errors.append(f"{prefix}: missing instance_id")
            elif instance_id in seen_ids:
                errors.append(f"{prefix}: duplicate instance_id {instance_id!r}")
            else:
                seen_ids.add(instance_id)

            score = record.get("difficulty_score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                errors.append(f"{prefix}: difficulty_score must be a finite number")

            label = record.get("difficulty_label")
            if not isinstance(label, str) or label.strip().lower() not in {
                "easy",
                "medium",
                "hard",
            }:
                errors.append(f"{prefix}: difficulty_label must be easy, medium, or hard")

            raw_tags = record.get("tags")
            if not isinstance(raw_tags, list) or len(raw_tags) != 4 or not all(
                isinstance(tag, str) and tag.strip() for tag in raw_tags
            ):
                errors.append(f"{prefix}: tags must contain four non-empty strings")

            patch = record.get("patch_stats")
            if patch is not None and not isinstance(patch, dict):
                errors.append(f"{prefix}: patch_stats must be an object")

            records.append(record)

    if not records:
        errors.append(f"{tags_file}: no metadata records")
    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise DashboardDataError(
            f"{dataset_id}: invalid tags.jsonl ({len(errors)} error(s)):\n{details}"
        )
    return records


def aggregate_dataset(
    dataset_id: str, datasets_dir: Path = DATASETS_DIR
) -> dict[str, Any]:
    """Aggregate one dataset using only its validated tags.jsonl."""
    records = load_tag_records(dataset_id, datasets_dir)
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
    tagged = len(records)

    for record in records:
        difficulty_scores.append(float(record["difficulty_score"]))
        difficulty_labels[str(record["difficulty_label"]).strip().lower()] += 1

        raw_tags = [str(tag).strip().lower() for tag in record["tags"]]
        lang_counts[raw_tags[0]] += 1
        area_counts[raw_tags[1]] += 1
        tasks_with_topic += 1
        topic_counts[raw_tags[2]] += 1

        bug_class = str(record.get("bug_class") or raw_tags[3]).strip().lower()
        tasks_with_bug_class += 1
        bug_classes[bug_class] += 1

        patch = record.get("patch_stats") or {}
        patch_denom += 1
        patch_lines += int(patch.get("lines") or 0)
        patch_hunks += int(patch.get("hunks") or 0)
        patch_files += int(patch.get("files") or 0)

    denom = patch_denom or 1
    return {
        "id": dataset_id,
        "total": tagged,
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


def fmt_int(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError, OverflowError):
        return "0"


def fmt_float(v, digits: int = 2) -> str:
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError, OverflowError):
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
    return "\n".join(rows)


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
    return "\n".join(rows)


CSS = """
:root {
  --font-sans: ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  --c-bg: #020617; --c-bg-2: #0f172a80; --c-panel: #0b1226; --c-border: #1e293b99;
  --c-fg: #e2e8f0; --c-fg-dim: #94a3b8; --c-fg-mute: #64748b; --c-fg-faint: #475569;
  --c-accent: #6366f1; --c-accent-soft: #6366f133; --c-accent-border: #6366f180;
  --c-good: #34d399; --c-bad: #f87171; --c-warn: #fbbf24; --c-violet: #a78bfa;
  --c-method-bg: #6366f11f; --c-method-head: #a5b4fc;
  font-size: 18px;
}
[data-theme="light"] {
  --c-bg: #f8fafc; --c-bg-2: #ffffff; --c-panel: #ffffff; --c-border: #e2e8f0;
  --c-fg: #0f172a; --c-fg-dim: #475569; --c-fg-mute: #64748b; --c-fg-faint: #94a3b8;
  --c-accent: #4f46e5; --c-accent-soft: #6366f122; --c-accent-border: #6366f1;
  --c-violet: #7c3aed; --c-method-bg: #eef2ff; --c-method-head: #4338ca;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--c-bg); color: var(--c-fg);
  font-family: var(--font-sans); -webkit-font-smoothing: antialiased; height: 100vh; }
code, pre, .mono { font-family: var(--font-mono); }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-thumb { background: var(--c-fg-faint); border-radius: 3px; }
.layout { display: flex; height: 100vh; overflow: hidden; }
.sidebar { width: 300px; flex-shrink: 0; border-right: 1px solid var(--c-border);
  background: linear-gradient(180deg, #0a1226 0%, var(--c-bg) 100%);
  display: flex; flex-direction: column; overflow: hidden; }
[data-theme="light"] .sidebar { background: var(--c-bg-2); }
.sidebar-logo { padding: 16px; border-bottom: 1px solid var(--c-border); display: flex; align-items: center; gap: 10px; }
.logo-mark { width: 38px; height: 32px; border-radius: 8px; background: var(--c-accent);
  display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 800; font-size: 16px; }
.logo-title { font-size: 20px; font-weight: 600; }
.logo-sub { font-size: 16px; color: var(--c-fg-mute); }
.ds-nav { padding: 10px 8px; border-bottom: 1px solid var(--c-border); display: flex; flex-direction: column; gap: 3px; }
.ds-item { background: transparent; border: 1px solid transparent; color: var(--c-fg-dim);
  padding: 10px 12px; border-radius: 8px; text-align: left; cursor: pointer; font-size: 19px;
  display: flex; flex-direction: column; gap: 2px; width: 100%; }
.ds-item:hover { background: #1e293b40; color: var(--c-fg); }
.ds-item.active { background: var(--c-accent-soft); border-color: var(--c-accent-border); color: #c7d2fe; }
[data-theme="light"] .ds-item.active { color: var(--c-accent); }
.ds-item .ds-name { font-weight: 600; font-size: 19px; }
.ds-item .ds-count { font-size: 17px; color: var(--c-fg-mute); }
.sidebar-section { padding: 12px 8px 8px; flex: 1; overflow: auto; }
.section-label { text-transform: uppercase; font-size: 16px; letter-spacing: .06em; color: var(--c-fg-mute); padding: 0 8px 6px; font-weight: 600; }
.sidebar-stat { display: flex; align-items: baseline; justify-content: space-between; padding: 6px 8px; font-size: 19px; }
.sidebar-stat .l { color: var(--c-fg-mute); }
.sidebar-stat .v { font-weight: 600; font-family: var(--font-mono); }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar { padding: 14px 24px; border-bottom: 1px solid var(--c-border); display: flex; align-items: center; justify-content: space-between; }
.topbar h1 { font-size: 20px; margin: 0; font-weight: 650; }
.topbar .sub { font-size: 17px; color: var(--c-fg-mute); margin-top: 3px; }
.theme-btn { background: var(--c-bg-2); border: 1px solid var(--c-border); color: var(--c-fg-dim);
  border-radius: 8px; padding: 7px 14px; cursor: pointer; font-size: 17px; }
.content { flex: 1; overflow: auto; padding: 24px; }
.ds-panel { display: none; }
.ds-panel.active { display: block; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin-bottom: 22px; }
.card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 12px; padding: 16px 18px; }
.card .k { font-size: 18px; color: var(--c-fg-mute); }
.card .v { font-size: 30px; font-weight: 700; font-family: var(--font-mono); margin-top: 4px; }
.card .v small { font-size: 18px; color: var(--c-fg-mute); font-weight: 500; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 18px; }
.panel { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 12px; padding: 18px 20px; margin-bottom: 18px; }
.panel h2 { font-size: 21px; margin: 0 0 14px; font-weight: 600; letter-spacing: .01em; }
.panel h2 span { color: var(--c-fg-mute); font-weight: 400; font-size: 18px; margin-left: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 19px; }
th, td { text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--c-border); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--c-fg-mute); font-weight: 600; font-size: 17px; text-transform: uppercase; letter-spacing: .04em; }
td strong { font-weight: 600; }
.stacked { display: flex; height: 11px; border-radius: 5px; overflow: hidden; background: #1e293b; min-width: 120px; }
.stacked.empty { color: var(--c-fg-mute); font-size: 16px; background: transparent; }
.seg.easy { background: var(--c-good); } .seg.medium { background: var(--c-warn); } .seg.hard { background: var(--c-bad); }
.mini { font-size: 16px; color: var(--c-fg-mute); margin-top: 3px; font-family: var(--font-mono); }
.tag-row { display: flex; align-items: center; gap: 10px; padding: 4px 0; font-size: 18px; }
.tag-name { flex: 0 0 230px; font-family: var(--font-mono); font-size: 17px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag-track { flex: 1; height: 9px; background: #1e293b; border-radius: 4px; overflow: hidden; }
.tag-fill { display: block; height: 100%; background: var(--c-accent); }
.tag-count { flex: 0 0 150px; text-align: right; color: var(--c-fg-mute); font-family: var(--font-mono); font-size: 17px; }
.tag-card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 12px; padding: 16px 18px; }
.tag-card h3 { font-size: 20px; margin: 0 0 12px; font-weight: 600; }
.tag-card h3 span { color: var(--c-fg-mute); font-weight: 400; font-size: 17px; margin-left: 6px; }
.muted { color: var(--c-fg-dim); font-size: 18px; }
/* Prominent methodology card (difficulty & tagging) - stands out in both themes */
.method-card { grid-column: 1 / -1; background: var(--c-method-bg);
  border: 1px solid var(--c-accent-border); border-radius: 12px; padding: 20px 24px; }
.method-card h3 { font-size: 22px; margin: 0 0 14px; font-weight: 700; color: var(--c-method-head); }
.method-card h3 span { color: var(--c-accent); font-weight: 500; font-size: 18px; margin-left: 6px; }
.method-body { font-size: 19px; line-height: 1.85; color: var(--c-fg); }
.method-body strong { color: var(--c-fg); font-weight: 700; }
.method-body .mh { display: inline-block; color: var(--c-method-head); font-weight: 800;
  font-size: 20px; letter-spacing: .01em; margin: 4px 0 2px; }
.method-body .dim { color: var(--c-violet); font-weight: 700; }
.method-body code { color: var(--c-accent); background: var(--c-accent-soft);
  padding: 1px 6px; border-radius: 5px; font-size: 17px; }
/* Full-width card: spans the whole grid row (e.g. Bug classes = Area/tier + Top topics width) */
.wide-card { grid-column: 1 / -1; }
"""


# Single source of truth for the difficulty & tagging methodology card, shared by
# render_panel() (full regenerate) and inject_v2.py (surgical injection) so every
# panel shows identical, prominent, English-only copy. Weights/thresholds here
# mirror the dashboard tagger repos/legoflow-curator/tools/tag_task_metadata.py.
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


def render_panel(dataset: DatasetSpec, data: dict[str, Any], active: bool) -> str:
    ds_id = dataset.id
    display = dataset.display_name
    stats = data["difficulty_stats"]
    tagged = data["tagged"]
    total = data["total"]

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

  <div class="grid2" style="margin-top:18px;">
{METHODOLOGY_HTML}
  </div>
</div>
"""


def render_html(datasets: list[dict[str, Any]], output_path: Path) -> str:
    by_id = {d["id"]: d for d in datasets}
    grand_total = sum(d["total"] for d in datasets)

    ds_nav = []
    panels = []
    for dataset in DATASETS:
        ds_id = dataset.id
        display = dataset.display_name
        data = by_id.get(ds_id)
        if data is None:
            continue
        active = not panels
        ds_nav.append(
            f'<button class="ds-item{" active" if active else ""}" data-ds="{ds_id}" onclick="switchDs(\'{ds_id}\')">'
            f'<span class="ds-name">{html.escape(display)}</span>'
            f'<span class="ds-count">{fmt_int(data["total"])} tasks</span>'
            f'</button>'
        )
        panels.append(render_panel(dataset, data, active))

    sidebar_stats = f"""
      <div class="section-label">Global</div>
      <div class="sidebar-stat"><span class="l">Datasets</span><span class="v">{len(datasets)}</span></div>
      <div class="sidebar-stat"><span class="l">Total tasks</span><span class="v">{fmt_int(grand_total)}</span></div>
    """

    doc = f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>legoflow-databoard</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-logo">
      <div class="logo-mark">LF</div>
      <div><div class="logo-title">legoflow-databoard</div></div>
    </div>
    <div class="ds-nav">
      <div class="section-label">Datasets</div>
      {''.join(ds_nav)}
    </div>
    <div class="sidebar-section">{sidebar_stats}</div>
  </aside>
  <div class="main">
    <div class="topbar">
      <div>
        <h1>Dataset Analytics</h1>
      </div>
      <button class="theme-btn" onclick="toggleTheme()">Theme</button>
    </div>
    <div class="content">
      {''.join(panels)}
    </div>
  </div>
</div>
<script>
function switchDs(id) {{
  document.querySelectorAll('.ds-panel').forEach(function(p) {{ p.classList.toggle('active', p.dataset.ds === id); }});
  document.querySelectorAll('.ds-item').forEach(function(b) {{ b.classList.toggle('active', b.dataset.ds === id); }});
  location.hash = id;
}}
function toggleTheme() {{
  var el = document.documentElement;
  el.dataset.theme = el.dataset.theme === 'dark' ? 'light' : 'dark';
}}
if (location.hash) {{ switchDs(location.hash.slice(1)); }}
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
    parser.add_argument("--datasets-dir", type=Path, default=DATASETS_DIR)
    parser.add_argument(
        "--dataset",
        action="append",
        choices=[dataset.id for dataset in DATASETS],
        help="Render only this prepared dataset; repeat to select multiple",
    )
    args = parser.parse_args()

    print(f"{'='*70}")
    print("Generate multi-dataset dashboard")
    print(f"{'='*70}")

    selected_ids = set(args.dataset or ())
    selected = [
        dataset for dataset in DATASETS if not selected_ids or dataset.id in selected_ids
    ]
    datasets = []
    for dataset in selected:
        data = aggregate_dataset(dataset.id, args.datasets_dir)
        datasets.append(data)
        print(
            f"  {dataset.display_name:26s}: total={data['total']:>7,}  "
            f"tagged={data['tagged']:>7,}  "
            f"mean_diff={data['difficulty_stats']['mean']:.2f}"
        )

    render_html(datasets, args.output_html)
    print(f"{'='*70}")
    print(f"✓ generated: {args.output_html}  ({args.output_html.stat().st_size/1024:.0f} KB)")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()

