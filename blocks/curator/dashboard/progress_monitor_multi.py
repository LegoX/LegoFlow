#!/usr/bin/env python3
"""Curator databoard generator.

Reads the **live pipeline** under `artifacts/`, not an offline dataset export.
Sources are fixed, not configured: `artifacts/collected_prs/` for PR collection,
and every immediate subdirectory of `artifacts/swe_tasks/` as one batch on the
Task List, named after that directory. A symlink there is followed, which is how
a dataset this pipeline did not generate joins the board. Language, difficulty
and the semantic tags
`[language, area, topic, bug_class]` are read from every task's own `task.toml`
(see task_toml.py), so a task is never classified by the directory holding it.

Pools may overlap — `merged_swe_tasks` is a manifest-filtered copy of
`swe_tasks` — so the global Overview de-duplicates by task id while each batch
is still reported on its own.
"""
from __future__ import annotations

import html
import json
import base64
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from check_task_dir import check_task_dir, format_result  # noqa: E402
from collection_stats import collect_pr_stats_multi  # noqa: E402
from sample_tasks import write_samples  # noqa: E402
from task_toml import collect_task_dim, read_verified_ids  # noqa: E402

DASHBOARD_ROOT = Path(__file__).parent
BLOCK_ROOT = DASHBOARD_ROOT.parent
BLOCK_DIR = BLOCK_ROOT
CONFIG_PATH = BLOCK_ROOT / "config.yaml"
DEFAULT_OUTPUT = DASHBOARD_ROOT / "site" / "index.html"


def display_path(value: Any, block_dir: Path = BLOCK_DIR) -> str:
    """A path fit to publish: relative to the block, and never naming the host.

    A pool linked in from another checkout has no useful relative form, so keep
    the recognisable tail from `artifacts/` and drop everything above it.
    """
    text = str(value or "")
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        return text
    try:
        return str(path.relative_to(block_dir))
    except ValueError:
        pass
    parts = path.parts
    if "artifacts" in parts:
        return "external:" + str(Path(*parts[parts.index("artifacts"):]))
    return "external:" + "/".join(parts[-3:])


def _resolve(path_str: str, base: Path) -> Path:
    """Config paths may be absolute, or relative to the config file's own
    directory — which for the block's real config.yaml is the block root."""
    path = Path(str(path_str)).expanduser()
    return path if path.is_absolute() else (base / path)


def discover_sources(block_dir: Path = BLOCK_DIR) -> dict[str, Any]:
    """Resolve what the board reads. Fixed locations, no configuration.

    * PRs   — `artifacts/collected_prs/`, one collection directory whose
      per-language files are discovered inside.
    * Tasks — every immediate child of `artifacts/swe_tasks/`. Each child is one
      **batch**, named by its directory (`py-cc`, `go-cc`, ...), and must hold one
      harbor task per immediate child of its own.

    A third-party dataset joins the board by being symlinked in as another child
    of `swe_tasks/`; symlinks are followed, so it is listed like any other batch.
    """
    artifacts = block_dir / "artifacts"
    tasks_root = artifacts / "swe_tasks"

    batches: list[dict[str, Any]] = []
    if tasks_root.is_dir():
        for child in sorted(tasks_root.iterdir(), key=lambda c: c.name):
            # is_dir() follows symlinks, so a linked-in pool is walked exactly
            # like a local one. Whether it counts as third-party is decided below,
            # by where the link lands — not by the fact that it is a link.
            if not child.is_dir() or child.name.startswith("."):
                continue
            target = child.resolve()
            # "Third-party" means no PR provenance, which is what keeps a batch
            # out of the PR -> task funnel. A symlink alone does not imply that:
            # operators routinely link this pipeline's own pools in from another
            # checkout. What distinguishes them is where the link lands — inside
            # some `swe_tasks/` it is still this pipeline's output; anywhere else
            # it is a dataset we did not source from PRs.
            external = child.is_symlink() and target.parent.name != "swe_tasks"
            batches.append({
                "name": child.name,
                "path": target,
                "external": external,
            })

    prs = [{"name": "collected_prs", "path": artifacts / "collected_prs", "external": False}]
    return {"batches": batches, "prs": prs, "tasks_root": tasks_root,
            "pr_filters": _pr_filters(block_dir)}


def _pr_filters(block_dir: Path) -> dict[str, Any]:
    """Collection thresholds, shown on the board as configuration.

    These are still real inputs to the collector, so they keep coming from
    config.yaml — unlike the source paths, which are now fixed.
    """
    try:
        import yaml
    except ModuleNotFoundError:
        return {}
    try:
        cfg = yaml.safe_load((block_dir / "config.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    inp = ((cfg.get("runtime_info") or {}).get("input") or {})
    return (inp.get("pr_collection") or {}).get("filters") or {}


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


def score_stats(values: list[float]) -> dict[str, float | int | None]:
    # No scores means "not available", not zero — a 0.00 mean beside real figures
    # is indistinguishable from a measurement. fmt_* render None as an em dash.
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None,
                "mean": None, "p75": None, "max": None}
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


def aggregate_batch(name: str, path: Path, external: bool = False) -> dict[str, Any]:
    """Aggregate one configured batch directly from its task.toml files."""
    tasks = collect_task_dim(path)
    verified_ids = read_verified_ids(path)

    labels: Counter[str] = Counter()
    langs: Counter[str] = Counter()
    areas: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    bugs: Counter[str] = Counter()
    scores: list[float] = []
    tagged = 0

    for meta in tasks.values():
        langs[meta["language"]] += 1
        if meta["difficulty"] and meta["difficulty"] != "unknown":
            labels[meta["difficulty"]] += 1
        if meta["score"] is not None:
            scores.append(meta["score"])
        if meta["tagged"]:
            tagged += 1
        for key, counter in (("area", areas), ("topic", topics), ("bug_class", bugs)):
            if meta[key]:
                counter[meta[key]] += 1

    total = len(tasks)
    matched = verified_ids & set(tasks)
    verified = len(matched)
    # ids listed as verified whose directory is gone (tasks removed after
    # verification) — reported rather than silently dropped
    stale_verified = len(verified_ids - set(tasks))
    return {
        "id": name,
        "name": name,
        # Published, so block-relative: an absolute path names the operator's
        # home directory and checkout on a public URL, and tells a reader
        # nothing they can act on — the files are on the machine that built it.
        "path": display_path(path),
        "external": external,
        "exists": path.is_dir(),
        "total": total,
        "tagged": tagged,
        "verified": verified,
        "stale_verified": stale_verified,
        "verified_ids": matched,
        "yield": (verified / total) if total else None,
        "tasks": tasks,
        "difficulty_scores": scores,
        "difficulty_stats": score_stats(scores),
        "difficulty_bins": score_bins(scores),
        "difficulty_labels": dict(labels),
        "languages": dict(langs.most_common()),
        "areas": dict(areas.most_common()),
        "topics": dict(topics.most_common()),
        "bug_classes": dict(bugs.most_common()),
        "tasks_with_topic": sum(topics.values()),
        "tasks_with_bug_class": sum(bugs.values()),
        # task.toml carries no patch statistics; showing 0 would read as a
        # measurement, so these stay unavailable and render as em dashes.
        "patch": {"avg_lines": None, "avg_hunks": None, "avg_files": None},
    }


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
  /* bar fills carry no text and repeat dozens of times per page, so they use a
     softened tint of the accent; the brand colour itself is unchanged */
  --c-accent-fill: #c96a45;
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
  --c-accent-fill: #c9805f;
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
.logo-title { font-size: 15px; font-weight: 650; line-height: 1.25; }

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
/* Modal — a centred dialog over a dimmed page. Generous gutters, a quiet header
   rule, and one clear column of content: the dialog should read like a page, not
   a tooltip that grew. */
.modal { position: fixed; inset: 0; z-index: 200; display: flex; align-items: center;
  justify-content: center; padding: 32px 24px; background: rgba(17, 17, 17, .32); }
:root[data-theme="dark"] .modal { background: rgba(0, 0, 0, .5); }
.modal[hidden] { display: none; }
.modal-box { width: min(920px, 94vw); max-height: min(80vh, 780px); display: flex;
  flex-direction: column; background: var(--c-panel); border: 1px solid var(--c-border);
  border-radius: 16px; box-shadow: 0 24px 64px rgba(0, 0, 0, .18); overflow: hidden; }
:root[data-theme="dark"] .modal-box { box-shadow: 0 24px 64px rgba(0, 0, 0, .6); }
.modal-head { display: flex; align-items: flex-start; justify-content: space-between;
  gap: 20px; padding: 22px 28px 18px; border-bottom: 1px solid var(--c-border); }
.modal-head h3 { margin: 0; font-size: 17px; font-weight: 650; letter-spacing: -.01em;
  line-height: 1.3; }
.modal-head .sub { color: var(--c-fg-mute); font-size: 12.5px; margin-top: 5px;
  line-height: 1.5; }
.modal-close { background: transparent; border: 1px solid transparent; color: var(--c-fg-mute);
  border-radius: 9px; width: 30px; height: 30px; cursor: pointer; font-size: 19px;
  line-height: 1; flex: 0 0 30px; }
.modal-close:hover { color: var(--c-fg); border-color: var(--c-border);
  background: var(--c-bg-2); }
.modal-body { padding: 22px 28px 26px; overflow: auto; }

/* Definition rows read better than a bordered grid for a handful of settings. */
.modal-body table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.modal-body td { padding: 8px 0; border-bottom: 1px solid var(--c-border);
  vertical-align: top; text-align: left; line-height: 1.5; }
.modal-body tr:last-child td { border-bottom: 0; }
.modal-body td:first-child { color: var(--c-fg-mute); width: 27%; padding-right: 20px;
  white-space: nowrap; }
.modal-body td.mono { font-family: var(--font-mono); font-size: 11.5px; word-break: break-all;
  color: var(--c-fg); }
.modal-body .sub { color: var(--c-fg-faint); font-size: 11px; margin: 22px 0 6px;
  text-transform: uppercase; letter-spacing: .08em; font-weight: 700; }
.modal-body .sub:first-child { margin-top: 0; }
.modal-body .mini { margin-top: 14px; padding-top: 12px;
  border-top: 1px solid var(--c-border); line-height: 1.6; }
.modal-body .method-card { background: transparent; border: 0; padding: 0; }
.modal-body .method-card h3 { display: none; }
.modal-body .method-body { font-size: 13px; line-height: 1.75; }
.modal-body .method-body .mh { margin: 14px 0 4px; }
.section-head { font-size: 12px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .07em; color: var(--c-fg-mute); margin: 22px 0 10px; }
.section-head:first-child { margin-top: 4px; }

/* Collapsible sections. Statistics stays open — it is the reason to open the page;
   everything denser is one click away, via native details/summary so it works
   without JS. */
.fold { margin: 18px 0 0; }
.fold > summary { list-style: none; cursor: pointer; display: flex; align-items: center;
  gap: 8px; padding: 9px 12px; border: 1px solid var(--c-border); border-radius: 10px;
  background: var(--c-panel); font-size: 12px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .07em; color: var(--c-fg-mute); }
.fold > summary::-webkit-details-marker { display: none; }
.fold > summary:hover { color: var(--c-fg); border-color: var(--c-accent-border); }
.fold > summary .caret { transition: transform .15s ease; flex: 0 0 auto; }
.fold[open] > summary .caret { transform: rotate(90deg); }
.fold[open] > summary { border-bottom-left-radius: 0; border-bottom-right-radius: 0;
  color: var(--c-fg); }
.fold > summary .hint { margin-left: auto; text-transform: none; letter-spacing: 0;
  font-weight: 500; font-size: 11.5px; color: var(--c-fg-faint); }
.fold-body { border: 1px solid var(--c-border); border-top: 0;
  border-radius: 0 0 10px 10px; padding: 14px; }
.page { display: none; }
.page.active { display: block; }

.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 16px; }
.card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px; padding: 12px 14px; }
.card .k { font-size: 12px; color: var(--c-fg-mute); line-height: 1.35; }
.card .v { font-size: 22px; font-weight: 700; font-family: var(--font-mono);
  margin-top: 4px; line-height: 1.15; }
.card .v small { display: block; font-size: 11px; color: var(--c-fg-mute);
  font-weight: 500; margin-top: 3px; letter-spacing: .01em; }
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
.seg { opacity: .82; }
.seg.easy { background: var(--c-good); } .seg.medium { background: var(--c-warn); } .seg.hard { background: var(--c-bad); }
/* Label bar and its legend on one line, order statistics beside them: the two
   tables this replaces spent a full row each on seven short numbers. */
.diff-split { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(300px, 1.4fr);
  gap: 10px 28px; align-items: center; }
.diff-bar .stacked { height: 11px; }
.diff-bar .mini { display: none; }
.diff-legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 8px;
  font-size: 11.5px; color: var(--c-fg-mute); }
.diff-legend b { color: var(--c-fg); font-family: var(--font-mono); font-weight: 650;
  margin-left: 3px; }
.diff-legend .dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px;
  margin-right: 5px; vertical-align: -1px; opacity: .82; }
.diff-legend .dot.easy { background: var(--c-good); }
.diff-legend .dot.medium { background: var(--c-warn); }
.diff-legend .dot.hard { background: var(--c-bad); }
.diff-stats th, .diff-stats td { padding: 5px 8px; }
@media (max-width: 820px) { .diff-split { grid-template-columns: 1fr; } }
.mini { font-size: 12px; color: var(--c-fg-mute); margin-top: 2px; font-family: var(--font-mono); }

/* Distribution bars. The old layout was a flex row with a fixed 230px label and a
   fixed 150px count, which left a wide dead gutter on short labels; and every list
   ran as one tall single column. Now each row is a 3-column grid sized to content,
   and the list itself flows into as many columns as the card is wide. */
/* Wide gutters between column groups, tight ones inside a row: the label should
   read as attached to its own bar, not float between two of them. */
.tag-rows { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
  gap: 2px 44px; align-content: start; }
.tag-row { display: grid; grid-template-columns: minmax(64px, 118px) minmax(80px, 1fr) 74px;
  column-gap: 7px; align-items: center; padding: 2px 0; font-size: 12.5px; }
.tag-row .tag-count { padding-left: 4px; }
.tag-name { font-family: var(--font-mono); font-size: 12px; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag-track { height: 7px; background: var(--c-track); border-radius: 4px; overflow: hidden; }
.tag-fill { display: block; height: 100%; background: var(--c-accent-fill); }
.tag-count { text-align: right; color: var(--c-fg-mute); font-family: var(--font-mono); font-size: 11.5px; }
.tag-card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px; padding: 12px 14px; }
.tag-card h3 { font-size: 14px; margin: 0 0 8px; font-weight: 650; }
.tag-card h3 { display: flex; align-items: center; gap: 6px; }
.tag-card h3 span { color: var(--c-fg-mute); font-weight: 400; font-size: 12px; }
.help-btn { margin-left: auto; flex: 0 0 auto; width: 22px; height: 22px; padding: 0;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: 1px solid var(--c-border); border-radius: 999px;
  color: var(--c-fg-mute); cursor: pointer; }
.help-btn:hover { color: var(--c-accent); border-color: var(--c-accent-border); }
.help-btn .icon { width: 13px; height: 13px; }
.muted { color: var(--c-fg-dim); font-size: 13px; }

/* Task List: one row per dataset, tracer's Jobs list shape. */
.ds-list { display: flex; flex-direction: column; gap: 8px; }
.ds-line { display: flex; align-items: stretch; gap: 8px; }
.ds-line .ds-row { flex: 1; min-width: 0; }
.samples-btn { flex: 0 0 auto; align-self: stretch; background: var(--c-bg-2);
  border: 1px solid var(--c-border); color: var(--c-fg-dim); border-radius: 10px;
  padding: 0 14px; cursor: pointer; font: inherit; font-size: 12.5px; font-weight: 650;
  white-space: nowrap; }
.samples-btn:hover:not(:disabled) { color: var(--c-accent); border-color: var(--c-accent-border); }
.samples-btn:disabled { opacity: .45; cursor: default; }
.samples-btn .n { color: var(--c-fg-mute); font-weight: 500; margin-left: 5px; }
.modal-wide { width: min(1040px, 100%); max-height: min(86vh, 900px); }
/* Metric columns are sized to their own labels rather than sharing one fraction,
   so "Easy / medium / hard" cannot squeeze "Mean diff." into two lines. */
.ds-row { display: grid;
  grid-template-columns: minmax(200px, 2fr) repeat(5, max-content) minmax(150px, 1fr);
  gap: 10px 18px; align-items: center; width: 100%; text-align: left; cursor: pointer;
  background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px;
  padding: 12px 14px; color: var(--c-fg); font: inherit; font-size: 13px; }
.ds-row:hover { border-color: var(--c-accent-border); }
.ds-row.active { border-color: var(--c-accent-border); background: var(--c-accent-soft); }
/* Each cell stacks a label over a value. The inner spans are not themselves grid
   items, so they must be blockified explicitly — left inline they sit side by side,
   margin-top does nothing, and ellipsis never engages, so a long path overflows
   into the next column. min-width:0 lets a grid item shrink below its content. */
.ds-row > span { display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
.ds-row .name, .ds-row .desc, .ds-row .k, .ds-row .v { display: block; }
.ds-row .name { font-weight: 650; font-size: 14px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.ds-row .desc { color: var(--c-fg-mute); font-size: 11.5px; margin-top: 2px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; }
.ds-row .k { color: var(--c-fg-mute); font-size: 10.5px; text-transform: uppercase;
  letter-spacing: .04em; white-space: nowrap; line-height: 1.3; }
.ds-row .v { font-family: var(--font-mono); font-weight: 650; font-size: 14px;
  margin-top: 3px; line-height: 1.2; white-space: nowrap; }
@media (max-width: 1100px) {
  .ds-row { grid-template-columns: minmax(160px, 1.6fr) repeat(auto-fit, minmax(84px, max-content)); }
  .ds-line { flex-wrap: wrap; }
}
.ds-detail { margin-top: 14px; }
.ds-panel { display: none; }
.ds-panel.active { display: block; }
.back-link { background: transparent; border: 0; color: var(--c-accent); cursor: pointer;
  font: inherit; font-size: 13px; padding: 0 0 8px; }

/* Sample task viewer — a fixed-size two-pane dialog: the ten samples on the left,
   the selected task's files on the right. Fixed rather than content-sized so the
   dialog does not jump between a 600-byte instruction and a 24 KB patch. */
.samples-box { width: min(1160px, 95vw); height: min(760px, 88vh); max-height: none; }
.samples-body { padding: 0; display: grid; grid-template-columns: 280px minmax(0, 1fr);
  min-height: 0; flex: 1; overflow: hidden; }
.samples-side { border-right: 1px solid var(--c-border); overflow-y: auto;
  padding: 12px; background: var(--c-bg-2); min-height: 0; }
.samples-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }

.sample-list { display: flex; flex-direction: column; gap: 5px; }
.sample-row { display: flex; flex-direction: column; gap: 3px; align-items: flex-start;
  background: transparent; border: 1px solid transparent; border-radius: 8px;
  padding: 8px 10px; cursor: pointer; font: inherit; font-size: 12px; color: var(--c-fg);
  text-align: left; width: 100%; min-width: 0; }
.sample-row:hover { border-color: var(--c-border); background: var(--c-panel); }
.sample-row.active { background: var(--c-accent-soft); border-color: var(--c-accent-border); }
.sample-row .sid { font-family: var(--font-mono); font-size: 11.5px; width: 100%;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sample-row .smeta { color: var(--c-fg-mute); font-size: 10.5px; }

.sample-head { padding: 14px 20px 12px; border-bottom: 1px solid var(--c-border); }
.sample-head .t { font-family: var(--font-mono); font-size: 13px; font-weight: 650;
  word-break: break-all; line-height: 1.4; }
.sample-head .m { color: var(--c-fg-mute); font-size: 11.5px; margin-top: 4px; }
.tabs { display: flex; flex-wrap: wrap; gap: 4px; padding: 10px 20px 0; }
.tab { background: transparent; border: 1px solid transparent; color: var(--c-fg-dim);
  border-radius: 8px 8px 0 0; padding: 6px 12px; cursor: pointer; font: inherit;
  font-size: 12px; white-space: nowrap; }
.tab:hover { color: var(--c-fg); }
.tab.active { background: var(--code-bg); border-color: var(--code-border);
  border-bottom-color: var(--code-bg); color: var(--code-fg); font-weight: 650; }
.tab .sz { color: var(--c-fg-faint); font-size: 10.5px; margin-left: 6px; }

/* The file pane is an editor, not a page: its own dark surface in both themes,
   a line-number gutter, and syntax colour. */
:root { --code-bg: #1b1815; --code-border: #2b2621; --code-fg: #e8e2d8;
  --code-gutter: #6a6058; --code-comment: #8a7f72; --code-str: #c9a26a;
  --code-kw: #e0a06f; --code-var: #9ec7c2; --code-meta: #b48ead; }
.sample-body { flex: 1; min-height: 0; margin: 0 20px 20px; border: 1px solid var(--code-border);
  border-radius: 0 10px 10px 10px; background: var(--code-bg); overflow: hidden;
  display: flex; }
.sample-pre { margin: 0; padding: 14px 16px 14px 0; overflow: auto; flex: 1;
  font-family: var(--font-mono); font-size: 12px; line-height: 1.6; color: var(--code-fg);
  white-space: pre; tab-size: 4; }
.sample-pre code { display: block; min-width: max-content; }
.sample-pre .ln { display: inline-block; width: 3.2em; padding-right: 1em; margin-right: .9em;
  text-align: right; color: var(--code-gutter); border-right: 1px solid var(--code-border);
  user-select: none; }
.sample-pre .cm { color: var(--code-comment); font-style: italic; }
.sample-pre .st { color: var(--code-str); }
.sample-pre .kw { color: var(--code-kw); font-weight: 600; }
.sample-pre .va { color: var(--code-var); }
.sample-pre .mt { color: var(--code-meta); }
.sample-pre .add { color: #86b87a; }
.sample-pre .del { color: #d98080; }
.sample-pre .hunk { color: #7fa6c9; }
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


def render_panel(ds_meta: tuple[str, str, str], data: dict[str, Any], active: bool,
                 show_cards: bool = True) -> str:
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

    cards_block = ("" if not show_cards else f"""<div class="cards">
    <div class="card"><div class="k">Total tasks</div><div class="v">{fmt_int(total)}</div></div>
    <div class="card"><div class="k">Mean difficulty</div><div class="v">{fmt_float(stats['mean'], 2)}</div></div>
    <div class="card"><div class="k">Median difficulty</div><div class="v">{fmt_float(stats['median'], 1)}</div></div>
    <div class="card"><div class="k">Avg patch lines</div><div class="v">{fmt_float(data['patch']['avg_lines'], 1)}</div></div>
    <div class="card"><div class="k">Avg patch files</div><div class="v">{fmt_float(data['patch']['avg_files'], 2)}</div></div>
  </div>""")
    return f"""
<div class="ds-panel{' active' if active else ''}" data-ds="{ds_id}">
  {cards_block}

  <div class="panel">
    <h2>Difficulty distribution <span>{display}</span></h2>
    <div class="diff-split">
      <div>
        <div class="diff-bar">{render_label_bar(data)}</div>
        <div class="diff-legend">
          <span><i class="dot easy"></i>easy <b>{fmt_int(label_count(data, 'easy'))}</b></span>
          <span><i class="dot medium"></i>medium <b>{fmt_int(label_count(data, 'medium'))}</b></span>
          <span><i class="dot hard"></i>hard <b>{fmt_int(label_count(data, 'hard'))}</b></span>
        </div>
      </div>
      <table class="diff-stats">
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


def combine_datasets(batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold every batch into one global aggregate, in the shape render_panel
    consumes.

    De-duplicates by task id: merged_swe_tasks is a filtered copy of swe_tasks,
    so configuring both must not count the same task twice. Every distribution
    is recomputed from the de-duplicated task set rather than summed from the
    per-batch counters.
    """
    seen: dict[str, dict[str, Any]] = {}
    verified: set[str] = set()
    for batch in batches:
        for task_name, meta in batch.get("tasks", {}).items():
            seen.setdefault(task_name, meta)
        verified |= batch.get("verified_ids") or set()

    labels: Counter[str] = Counter()
    langs: Counter[str] = Counter()
    lang_verified: Counter[str] = Counter()
    areas: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    bugs: Counter[str] = Counter()
    scores: list[float] = []
    tagged = 0

    for task_name, meta in seen.items():
        langs[meta["language"]] += 1
        if task_name in verified:
            lang_verified[meta["language"]] += 1
        if meta["difficulty"] and meta["difficulty"] != "unknown":
            labels[meta["difficulty"]] += 1
        if meta["score"] is not None:
            scores.append(meta["score"])
        if meta["tagged"]:
            tagged += 1
        for key, counter in (("area", areas), ("topic", topics), ("bug_class", bugs)):
            if meta[key]:
                counter[meta[key]] += 1

    total = len(seen)
    return {
        "id": "all",
        "name": "All batches",
        "total": total,
        "tagged": tagged,
        "verified": len(verified),
        "yield": (len(verified) / total) if total else None,
        "tasks": seen,
        "difficulty_scores": scores,
        "difficulty_stats": score_stats(scores),
        "difficulty_bins": score_bins(scores),
        "difficulty_labels": dict(labels),
        "languages": dict(langs.most_common()),
        "languages_verified": dict(lang_verified),
        "areas": dict(areas.most_common()),
        "topics": dict(topics.most_common()),
        "bug_classes": dict(bugs.most_common()),
        "tasks_with_topic": sum(topics.values()),
        "tasks_with_bug_class": sum(bugs.values()),
        "patch": {"avg_lines": None, "avg_hunks": None, "avg_files": None},
    }


def render_rate_bars(rows: list[tuple[str, int, int]]) -> str:
    """name / numerator / denominator -> a bar per row, scaled 0-100%."""
    rows = [r for r in rows if r[2]]
    if not rows:
        return '<div class="muted">no data</div>'
    out = []
    for name, num, den in sorted(rows, key=lambda r: -(r[1] / r[2])):
        pct = num / den * 100.0
        out.append(
            '<div class="tag-row">'
            f'<span class="tag-name">{html.escape(name)}</span>'
            f'<span class="tag-track"><span class="tag-fill" style="width:{pct:.2f}%"></span></span>'
            f'<span class="tag-count">{pct:.1f}% ({num:,}/{den:,})</span>'
            '</div>'
        )
    return '<div class="tag-rows">' + "\n".join(out) + "</div>"


def render_info(batches: list[dict[str, Any]], prs: dict[str, Any],
                filters: dict[str, Any], tasks_root: Path) -> str:
    """What the board read, named exactly as config.yaml names it, so a surprising
    number can be traced back to a path without leaving the page."""
    def rows(items):
        return "".join(
            f"<tr><td>{html.escape(n)}</td><td class='mono'>{html.escape(pth)}</td>"
            f"<td>{extra}</td></tr>" for n, pth, extra in items
        )

    task_rows = rows([
        (b["name"], b["path"],
         f"{b['total']:,} tasks" if b["exists"] else "<em>not found</em>")
        for b in batches
    ]) or "<tr><td colspan='3'>none configured</td></tr>"

    pr_rows = rows([
        (d["name"], display_path(d["dir"]), "" if d["exists"] else "<em>not found</em>")
        for d in (prs.get("dirs") or [])
    ]) or "<tr><td colspan='3'>none configured</td></tr>"

    filter_rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td class='mono'>{html.escape(str(v))}</td>"
        f"<td></td></tr>" for k, v in (filters or {}).items()
    ) or "<tr><td colspan='3'>none</td></tr>"

    return (
        f"<div class='mini'>batches are the subdirectories of "
        f"{html.escape(display_path(tasks_root))}; a symlink there joins the board like any "
        f"other batch</div>"
        "<div class='sub'>Task batches</div>"
        f"<table>{task_rows}</table>"
        "<div class='sub'>PR collection</div>"
        f"<table>{pr_rows}</table>"
        f"<div class='mini'>Collected PR and repo counts are read from the id lists on "
        f"every render. The funnel above them — repos searched, PRs scanned, and the "
        f"drop reasons — counts rows the collector discarded and never wrote down, so "
        f"it can only come from its own report"
        + (f", generated {html.escape(str(prs.get('report_generated_at')))}."
           if prs.get('report_generated_at') else " (none found).")
        + "</div>"
        "<div class='sub'>Collection filters</div>"
        f"<table>{filter_rows}</table>"
        "<div class='mini'>language, difficulty and tags come from each task's "
        "task.toml, never from its directory name</div>"
    )


def render_overview(combined: dict[str, Any], batches: list[dict[str, Any]],
                    prs: dict[str, Any]) -> str:
    """Headline figures first, then the stage-by-stage funnel, then per-language
    task-creation success. Per-batch detail lives on the Task List."""
    body = render_panel(("all", "All batches", ""), combined, True, show_cards=False)
    body = body.replace('<div class="ds-panel active" data-ds="all">', "", 1).rstrip()
    if body.endswith("</div>"):
        body = body[: -len("</div>")]

    o = (prs or {}).get("overall") or {}
    searched = o.get("Repos Searched")
    kept = o.get("Repos with Qualifying PRs")
    scanned = o.get("PRs Scanned")
    merged = o.get("PRs Merged")
    qualifying = o.get("PRs Qualifying")
    collected = (prs or {}).get("total_prs") or 0
    repos = (prs or {}).get("total_repos") or 0

    pipeline_ids: set[str] = set()
    for b in batches:
        if not b.get("external"):
            pipeline_ids |= set(b.get("tasks", {}))
    generated = len(pipeline_ids)
    verified = combined["verified"]

    def rate(num, den):
        return (num / den) if (num is not None and den) else None

    # PR retention: of everything the collector scanned, what survived filtering.
    pr_retention = rate(qualifying, scanned)
    # Task creation success: verified out of every task the pipeline attempted.
    create_rate = rate(verified, combined["total"])

    funnel = ""
    if o:
        funnel = f"""
  <div class="panel">
    <h2>From repos to verified tasks <span>each stage against the one above</span></h2>
    <table>
      <tr><th>Stage</th><th>Count</th><th>Kept</th></tr>
      <tr><td>Repos searched</td><td>{fmt_int(searched)}</td><td>&mdash;</td></tr>
      <tr><td>Repos with qualifying PRs</td><td>{fmt_int(kept)}</td><td>{fmt_pct(rate(kept, searched))}</td></tr>
      <tr><td>PRs scanned</td><td>{fmt_int(scanned)}</td><td>&mdash;</td></tr>
      <tr><td>PRs merged</td><td>{fmt_int(merged)}</td><td>{fmt_pct(rate(merged, scanned))}</td></tr>
      <tr><td>PRs qualifying</td><td>{fmt_int(qualifying)}</td><td>{fmt_pct(rate(qualifying, scanned))}</td></tr>
      <tr><td>Tasks attempted</td><td>{fmt_int(generated)}</td><td>{fmt_pct(rate(generated, qualifying))}</td></tr>
      <tr><td>Tasks verified</td><td>{fmt_int(verified)}</td><td>{fmt_pct(rate(verified, generated))}</td></tr>
    </table>
    <div class="mini">imported datasets are excluded from the task rows &mdash; they have no PR provenance</div>
  </div>
"""

    lang_total = combined.get("languages", {})
    lang_ok = combined.get("languages_verified", {})
    rate_rows = [(name, lang_ok.get(name, 0), n) for name, n in lang_total.items()]

    stats = combined["difficulty_stats"]

    return f"""
<div class="page active" id="page-overview">
  <div class="section-head">Statistics</div>
  <div class="cards">
    <div class="card"><div class="k">Repos</div><div class="v">{fmt_int(repos)}</div></div>
    <div class="card"><div class="k">PRs collected</div><div class="v">{fmt_int(collected)}</div></div>
    <div class="card"><div class="k">Tasks</div><div class="v">{fmt_int(combined['total'])}</div></div>
    <div class="card"><div class="k">Verified</div><div class="v">{fmt_int(verified)}</div></div>
    <div class="card"><div class="k">PR retention</div><div class="v">{fmt_pct(pr_retention)}
      <small>qualifying / scanned</small></div></div>
    <div class="card"><div class="k">Task creation</div><div class="v">{fmt_pct(create_rate)}
      <small>verified / attempted</small></div></div>
    <div class="card"><div class="k">Mean difficulty</div><div class="v">{fmt_float(stats['mean'], 2)}</div></div>
    <div class="card"><div class="k">Median difficulty</div><div class="v">{fmt_float(stats['median'], 1)}</div></div>
    <div class="card"><div class="k">Tagged</div><div class="v">{fmt_int(combined['tagged'])}</div></div>
  </div>

  <details class="fold">
    <summary>{CARET_SVG}Pipeline funnel
      <span class="hint">repos &rarr; PRs &rarr; tasks, and success by language</span></summary>
    <div class="fold-body">
      {funnel}
      <div class="panel" style="margin-bottom:0">
        <h2>Task creation success <span>by language &mdash; verified / attempted</span></h2>
        {render_rate_bars(rate_rows)}
        <div class="mini">language is read from each task's task.toml, not its directory</div>
      </div>
    </div>
  </details>

  <details class="fold">
    <summary>{CARET_SVG}Task composition
      <span class="hint">difficulty, language, area, topic and bug-class distributions</span></summary>
    <div class="fold-body">{body}</div>
  </details>

</div>
"""


def fmt_pct(v) -> str:
    return UNAVAILABLE if v is None else f"{v * 100:.1f}%"


def render_task_list(batches: list[dict[str, Any]],
                     samples: dict[str, Any] | None = None) -> str:
    """One line per batch: the row opens its profile, Samples opens the viewer.

    The two are siblings rather than nested, because a <button> cannot legally
    contain another <button>.
    """
    lines, details = [], []
    for b in batches:
        stats = b["difficulty_stats"]
        missing = "" if b["exists"] else '<span class="desc">path not found</span>'
        origin = '<span class="desc">imported dataset</span>' if b.get("external") else ""
        idx = (samples or {}).get(b["name"])
        sample_btn = (
            f'<button class="samples-btn" data-samples="{html.escape(b["name"])}" '
            f'title="Open {idx["count"]} sample tasks">Samples <span class="n">{idx["count"]}</span></button>'
            if idx else '<button class="samples-btn" disabled title="no samples cached">Samples</button>'
        )
        lines.append(f"""
      <div class="ds-line">
        <button class="ds-row" data-ds="{html.escape(b['name'])}">
          <span><span class="name">{html.escape(b['name'])}</span>
            <span class="desc">{html.escape(b['path'])}</span>{origin}{missing}</span>
          <span><span class="k">Tasks</span><span class="v">{fmt_int(b['total'])}</span></span>
          <span><span class="k">Verified</span><span class="v">{fmt_int(b['verified'])}</span></span>
          <span><span class="k">Yield</span><span class="v">{fmt_pct(b['yield'])}</span></span>
          <span><span class="k">Tagged</span><span class="v">{fmt_int(b['tagged'])}</span></span>
          <span><span class="k">Mean diff.</span><span class="v">{fmt_float(stats['mean'], 2)}</span></span>
          <span><span class="k">Easy / medium / hard</span>{render_label_bar(b)}</span>
        </button>
        {sample_btn}
      </div>""")
        details.append(render_panel((b["name"], b["name"], b["path"]), b, False))

    body = "".join(lines) or (
        '<div class="empty-state">No batches found &mdash; each subdirectory of '
        'artifacts/swe_tasks/ is one batch. Symlink a dataset in to add it.</div>'
    )
    return f"""
<div class="page" id="page-tasks">
  <div class="ds-list">{body}</div>
  <div class="ds-detail" id="ds-detail" hidden>
    <button class="back-link" id="ds-back">&larr; All batches</button>
    {''.join(details)}
  </div>
</div>

<div class="modal" id="samplesPanel" role="dialog" aria-modal="true" hidden>
  <div class="modal-box samples-box">
    <div class="modal-head">
      <div><h3 id="samplesTitle">Sample tasks</h3>
        <div class="sub" id="samplesSub">pick one to see what a task contains</div></div>
      <button class="modal-close" data-close type="button" aria-label="Close">&times;</button>
    </div>
    <div class="samples-body">
      <div class="samples-side"><div class="sample-list" id="samplesList"></div></div>
      <div class="samples-main" id="samplesView"></div>
    </div>
  </div>
</div>
"""


# Which stage each threshold is applied at, and what it means. Definitions are
# lifted from blocks/curator/CLAUDE.md:355-362 rather than inferred from the names.
FILTER_DOCS: dict[str, tuple[str, str, str]] = {
    "min_stars": ("repo", "SWEGEN_PR_MIN_STARS",
                  "Repository must have at least this many GitHub stars."),
    "min_merged_prs": ("repo", "SWEGEN_PR_MIN_MERGED_PRS",
                       "Repository must already have at least this many merged pull requests."),
    "min_language_percentage": ("repo", "SWEGEN_PR_MIN_LANGUAGE_PERCENTAGE",
                                "The target language must make up at least this fraction of "
                                "the repository's code."),
    "max_days_since_push": ("repo", "SWEGEN_PR_MAX_DAYS_SINCE_PUSH",
                            "Skip repositories whose last push is older than this many days."),
    "min_issue_body_length": ("pr", "SWEGEN_PR_MIN_ISSUE_BODY_LENGTH",
                              "The pull request's linked issue must have a body at least this "
                              "many characters long — short issues rarely describe a task."),
    "min_files_changed": ("pr", "SWEGEN_PR_MIN_FILES_CHANGED",
                          "The pull request must touch at least this many files."),
    "max_files_changed": ("pr", "SWEGEN_PR_MAX_FILES_CHANGED",
                          "The pull request must touch no more than this many files."),
    "max_lines_changed": ("pr", "SWEGEN_PR_MAX_LINES_CHANGED",
                          "Additions plus deletions must not exceed this."),
}

HELP_SVG = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<circle cx="12" cy="12" r="9"></circle>'
            '<path d="M9.6 9.4a2.5 2.5 0 1 1 3.2 2.4c-.6.2-.8.7-.8 1.2v.5"></path>'
            '<path d="M12 17h.01"></path></svg>')


def render_filter_help() -> str:
    """Modal body: one definition list per stage."""
    out = []
    for stage, label, note in (
        ("repo", "Repository filters",
         "Applied while searching GitHub, before any pull request is looked at."),
        ("pr", "Pull request filters",
         "Applied to each pull request inside a repository that passed the stage above."),
    ):
        rows = "".join(
            f"<tr><td>{html.escape(k)}<div class='mini' style='margin:2px 0 0'>"
            f"{html.escape(env)}</div></td><td>{html.escape(desc)}</td></tr>"
            for k, (st, env, desc) in FILTER_DOCS.items() if st == stage
        )
        out.append(f"<div class='sub'>{label}</div>"
                   f"<div class='mini' style='margin:0 0 8px'>{note}</div>"
                   f"<table>{rows}</table>")
    out.append("<div class='mini'>Each threshold can also be set through the environment "
               "variable shown under its name; the value in config.yaml is used when that "
               "is unset. Per-language overrides live in the collector and win over both.</div>")
    return "".join(out)


def render_collection(prs: dict[str, Any], filters: dict[str, Any],
                      combined: dict[str, Any], batches: list[dict[str, Any]]) -> str:
    """Repo/PR collection and the funnel through to verified tasks.

    Funnel figures come from the collector's own filtering_report.md when present;
    the surviving PR/repo counts come from the {lang}_pr_ids.txt lists.
    """
    if not prs.get("exists"):
        where = html.escape(prs.get("dir") or "(unset)")
        return (f'<div class="page" id="page-collection"><div class="empty-state">'
                f'No PR collection directory at <code>{where}</code>.</div></div>')

    o = prs.get("overall") or {}
    langs = prs["languages"]
    # Imported datasets have no PR provenance, so counting them here would
    # inflate the PR -> task rate with tasks the collector never sourced.
    pipeline = [b for b in batches if not b.get("external")]
    seen: dict[str, dict[str, Any]] = {}
    for b in pipeline:
        for task_name, meta in b.get("tasks", {}).items():
            seen.setdefault(task_name, meta)
    task_langs: dict[str, int] = {}
    for meta in seen.values():
        task_langs[meta["language"]] = task_langs.get(meta["language"], 0) + 1
    external_n = sum(1 for b in batches if b.get("external"))

    rows = []
    for name, e in langs.items():
        produced = task_langs.get(name, 0)
        conv = (produced / e["prs"]) if e["prs"] else None
        rows.append(
            f"<tr><td><strong>{html.escape(name)}</strong></td>"
            f"<td>{fmt_int(e['repos_searched'])}</td>"
            f"<td>{fmt_int(e['repos_qualifying'])}</td>"
            f"<td>{fmt_int(e['prs_scanned'])}</td>"
            f"<td>{fmt_int(e['prs_qualifying'])}</td>"
            f"<td>{fmt_int(e['prs'])}</td>"
            f"<td>{fmt_int(e['repos'])}</td>"
            f"<td>{fmt_int(produced)}</td><td>{fmt_pct(conv)}</td></tr>"
        )

    total_produced = sum(task_langs.values())
    overall_conv = (total_produced / prs["total_prs"]) if prs["total_prs"] else None

    drop_r: Counter[str] = Counter()
    drop_p: Counter[str] = Counter()
    for e in langs.values():
        drop_r.update(e.get("repo_dropped") or {})
        drop_p.update(e.get("pr_dropped") or {})

    # Provenance and paths belong in the Info modal, not on the page itself.
    stamp = prs.get("report_generated_at")
    provenance = ("" if stamp else
                  '<div class="mini">no collection report — funnel columns unavailable</div>')

    # Filters are settings, not measurements — a table states that plainly, where a
    # bar invites reading them as quantities on the same scale as everything else.
    def filter_group(stage: str) -> str:
        rows = "".join(
            f"<tr><td>{html.escape(str(k))}</td><td class='mono'>{html.escape(str(v))}</td></tr>"
            for k, v in (filters or {}).items()
            if FILTER_DOCS.get(str(k), ("other",))[0] == stage
        )
        return f"<table>{rows}</table>" if rows else ""

    ungrouped = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td class='mono'>{html.escape(str(v))}</td></tr>"
        for k, v in (filters or {}).items() if str(k) not in FILTER_DOCS
    )
    repo_group, pr_group = filter_group("repo"), filter_group("pr")
    filter_table = "".join(filter(None, [
        f"<div class='sub'>Repository-level</div>{repo_group}" if repo_group else "",
        f"<div class='sub'>Pull-request-level</div>{pr_group}" if pr_group else "",
        f"<div class='sub'>Other</div><table>{ungrouped}</table>" if ungrouped else "",
    ])) or '<div class="muted">no filters configured</div>' 

    top_repos: Counter[str] = Counter()
    for e in langs.values():
        top_repos.update(e.get("top_repos") or {})
    repo_rows = "".join(
        f"<tr><td class='mono'>{html.escape(r)}</td><td>{n:,}</td></tr>"
        for r, n in top_repos.most_common(12)
    )
    repo_table = (f'<table><tr><th>Repository</th><th>PRs</th></tr>{repo_rows}</table>'
                  if repo_rows else '<div class="muted">no repositories collected</div>')

    return f"""
<div class="page" id="page-collection">
  <div class="cards">
    <div class="card"><div class="k">Repos searched</div><div class="v">{fmt_int(o.get('Repos Searched'))}</div></div>
    <div class="card"><div class="k">Repos with qualifying PRs</div><div class="v">{fmt_int(o.get('Repos with Qualifying PRs'))}</div></div>
    <div class="card"><div class="k">PRs scanned</div><div class="v">{fmt_int(o.get('PRs Scanned'))}</div></div>
    <div class="card"><div class="k">PRs qualifying</div><div class="v">{fmt_int(o.get('PRs Qualifying'))}</div></div>
    <div class="card"><div class="k">Tasks produced</div><div class="v">{fmt_int(total_produced)}</div></div>
    <div class="card"><div class="k">PR &rarr; task</div><div class="v">{fmt_pct(overall_conv)}</div></div>
  </div>

  <div class="panel">
    <h2>Collection funnel <span>by language</span></h2>
    <div class="tbl-wrap">
      <table>
        <tr><th>Language</th><th>Repos searched</th><th>Repos kept</th><th>PRs scanned</th>
            <th>PRs qualifying</th><th>PRs collected</th><th>Repos collected</th>
            <th>Tasks produced</th><th>PR &rarr; task</th></tr>
        {''.join(rows)}
      </table>
    </div>
    {provenance}
    {f'<div class="mini">{external_n} imported batch(es) excluded — no PR provenance</div>' if external_n else ''}
  </div>

  <div class="grid2">
    <section class="tag-card"><h3>Repos dropped <span>by reason</span></h3>
      {render_tags(dict(drop_r.most_common()), sum(drop_r.values()), limit=10)}
    </section>
    <section class="tag-card"><h3>PRs dropped <span>by reason</span></h3>
      {render_tags(dict(drop_p.most_common()), sum(drop_p.values()), limit=10)}
    </section>
    <section class="tag-card"><h3>Most-collected repositories <span>top 12 by PR count</span></h3>
      {repo_table}
    </section>
    <section class="tag-card"><h3>Collection filters <span>configuration, not measurements</span>
      <button class="help-btn" id="filterHelp" type="button" aria-label="What these mean"
        title="What each threshold means">{HELP_SVG}</button></h3>
      {filter_table}
    </section>
  </div>
</div>
"""


FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="14" fill="#b3431f"/>'
    '<text x="32" y="42" text-anchor="middle" font-family="Georgia,serif" '
    'font-weight="700" font-size="30" fill="#fafaf7">LF</text></svg>'
)
FAVICON_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(
    FAVICON_SVG.encode("utf-8")
).decode("ascii")

CARET_SVG = ('<svg class="caret icon" viewBox="0 0 24 24" aria-hidden="true" style="width:14px;height:14px"><path d="M9 6l6 6-6 6"></path></svg>')

METRICS_SVG = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
               '<path d="M4 19h16"></path><path d="M7 19v-7"></path>'
               '<path d="M12 19V5"></path><path d="M17 19v-10"></path></svg>')

INFO_SVG = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<circle cx="12" cy="12" r="9"></circle><path d="M12 16v-5"></path>'
            '<path d="M12 8h.01"></path></svg>')
MOON_SVG = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M20.5 14.5A8.7 8.7 0 0 1 9.5 3.5a7 7 0 1 0 11 11Z"></path></svg>')
SUN_SVG = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
           '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path>'
           '<path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path>'
           '<path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path>'
           '<path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path>'
           '<path d="m19.07 4.93-1.41 1.41"></path></svg>')


def render_html(
    batches: list[dict[str, Any]],
    output_path: Path,
    prs: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    samples: dict[str, Any] | None = None,
    tasks_root: Path = BLOCK_DIR / "artifacts" / "swe_tasks",
) -> str:
    info_html = render_info(batches, prs or {}, filters or {}, tasks_root)
    filter_help = render_filter_help()
    prs = prs or {"exists": False, "dir": "", "languages": {}, "total_prs": 0, "total_repos": 0}
    combined = combine_datasets(batches)
    grand_total = combined["total"]
    grand_tagged = combined["tagged"]
    mean = combined["difficulty_stats"]["mean"]
    n_ds = len(batches)

    overview_sub = f"{n_ds} batches · {fmt_int(grand_total)} unique tasks · {fmt_int(combined['verified'])} verified"
    tasks_sub = "Per-batch breakdown — pick a batch for its full profile"
    collection_sub = f"{fmt_int(prs.get('total_prs', 0))} PRs from {fmt_int(prs.get('total_repos', 0))} repos"

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LegoFlow-Curator</title>
<link rel="icon" type="image/svg+xml" href="{FAVICON_DATA_URI}">
<style>{CSS}</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-logo">
      <div class="logo-mark">LF</div>
      <div class="logo-title">Curator Dashboard</div>
    </div>
    <div class="nav">
      <div class="section-label">Views</div>
      <button class="nav-item active" data-page="overview">
        <span class="nav-icon">◧</span><span class="nav-label">Overview</span></button>
      <button class="nav-item" data-page="collection">
        <span class="nav-icon">⚑</span><span class="nav-label">PR Collection</span>
        <span class="nav-count">{fmt_int(prs.get('total_prs', 0))}</span></button>
      <button class="nav-item" data-page="tasks">
        <span class="nav-icon">☰</span><span class="nav-label">Task List</span>
        <span class="nav-count">{n_ds}</span></button>
    </div>
    <div class="sidebar-section">
      <div class="section-label">Global</div>
      <div class="sidebar-stat"><span class="l">Batches</span><span class="v">{n_ds}</span></div>
      <div class="sidebar-stat"><span class="l">Unique tasks</span><span class="v">{fmt_int(grand_total)}</span></div>
      <div class="sidebar-stat"><span class="l">Verified</span><span class="v">{fmt_int(combined['verified'])}</span></div>
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
        <button id="metricsToggle" class="icon-btn" type="button"
          aria-label="Scoring and tagging" title="How difficulty and tags are produced">{METRICS_SVG}</button>
        <button id="infoToggle" class="icon-btn" type="button"
          aria-label="Dashboard info" title="What this board is reading">{INFO_SVG}</button>
        <button id="themeToggle" class="icon-btn theme-toggle" type="button"
          aria-label="Toggle theme" title="Toggle theme"><span class="theme-moon">{MOON_SVG}</span><span class="theme-sun">{SUN_SVG}</span></button>
      </div>
    </div>
    <div class="content">
      {render_overview(combined, batches, prs)}
      {render_collection(prs, filters or {}, combined, batches)}
      {render_task_list(batches, samples)}
    </div>
  </div>
</div>

<div class="modal" id="metricsPanel" role="dialog" aria-modal="true" hidden>
  <div class="modal-box">
    <div class="modal-head">
      <div><h3>Difficulty scoring &amp; semantic tagging</h3>
        <div class="sub">how each task's difficulty and 4-tag metadata are produced</div></div>
      <button class="modal-close" data-close type="button" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">{METHODOLOGY_HTML}</div>
  </div>
</div>

<div class="modal" id="filterHelpPanel" role="dialog" aria-modal="true" hidden>
  <div class="modal-box">
    <div class="modal-head">
      <div><h3>Collection filters</h3>
        <div class="sub">the thresholds a repository and a pull request must clear to be collected</div></div>
      <button class="modal-close" data-close type="button" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">{filter_help}</div>
  </div>
</div>

<div class="modal" id="infoPanel" role="dialog" aria-modal="true" hidden>
  <div class="modal-box">
    <div class="modal-head">
      <div><h3>What this board is reading</h3>
        <div class="sub">resolved from config.yaml at render time</div></div>
      <button class="modal-close" data-close type="button" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">{info_html}</div>
  </div>
</div>
<script>
var SAMPLE_INDEX = {json.dumps(samples or {}, ensure_ascii=False)};
var PAGE_META = {{
  overview: {{title: 'Overview', sub: {json.dumps(overview_sub)}}},
  collection: {{title: 'PR Collection', sub: {json.dumps(collection_sub)}}},
  tasks: {{title: 'Task List', sub: {json.dumps(tasks_sub)}}}
}};

/* Light is the default; dark follows a saved choice, else the OS preference. */
(function () {{
  var saved = null;
  try {{ saved = localStorage.getItem('curator-theme'); }} catch (e) {{}}
  var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = saved || (prefersDark ? 'dark' : 'light');
}})();

/* Two modals, one at a time. Closed by the X, by the backdrop, or by Escape. */
var MODALS = [['infoToggle', 'infoPanel'], ['metricsToggle', 'metricsPanel'],
              ['filterHelp', 'filterHelpPanel']]
  .map(function (p) {{
    return {{btn: document.getElementById(p[0]), panel: document.getElementById(p[1])}};
  }})
  .filter(function (d) {{ return d.btn && d.panel; }});

function closeModals() {{
  MODALS.forEach(function (m) {{ m.panel.hidden = true; }});
  var sp = document.getElementById('samplesPanel');
  if (sp) {{ sp.hidden = true; }}
}}

MODALS.forEach(function (m) {{
  m.btn.addEventListener('click', function () {{
    var wasHidden = m.panel.hidden;
    closeModals();
    m.panel.hidden = !wasHidden;
  }});
  m.panel.addEventListener('click', function (e) {{
    // the backdrop is the panel itself; clicks inside .modal-box must not close it
    if (e.target === m.panel || (e.target.hasAttribute && e.target.hasAttribute('data-close'))) {{
      m.panel.hidden = true;
    }}
  }});
}});
document.addEventListener('keydown', function (e) {{
  if (e.key === 'Escape') {{ closeModals(); }}
}});

document.getElementById('themeToggle').addEventListener('click', function () {{
  var el = document.documentElement;
  var next = el.dataset.theme === 'dark' ? 'light' : 'dark';
  el.dataset.theme = next;
  try {{ localStorage.setItem('curator-theme', next); }} catch (e) {{}}
}});

function showPage(name) {{
  if (!PAGE_META[name]) {{ name = 'overview'; }}
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

/* Sample viewer. The cached JSON per batch is fetched once, then the modal shows
   a picker over the ten samples and the selected one's files as tabs. */
var sampleCache = {{}};
var samplesModal = document.getElementById('samplesPanel');

function escapeHtml(t) {{
  return String(t).replace(/[&<>]/g, function (c) {{
    return {{'&': '&amp;', '<': '&lt;', '>': '&gt;'}}[c];
  }});
}}

/* Highlighting tokenises the RAW text and escapes inside the callback, so a rule
   can never match markup an earlier rule inserted. Each character is consumed once. */
var RULES = {{
  sh: [
    ['cm', /#[^\\n]*/],
    ['st', /"(?:\\\\.|[^"\\\\])*"|'(?:[^'])*'/],
    ['va', /\\$\\{{[^}}]*\\}}|\\$[A-Za-z_][A-Za-z0-9_]*/],
    ['kw', /\\b(?:if|then|elif|else|fi|for|while|do|done|case|esac|function|return|export|source|local|set|cd|echo|exit|test)\\b/]
  ],
  docker: [
    ['cm', /#[^\\n]*/],
    ['st', /"(?:\\\\.|[^"\\\\])*"/],
    ['kw', /^[ \\t]*(?:FROM|RUN|CMD|LABEL|COPY|ADD|ENV|ARG|WORKDIR|ENTRYPOINT|USER|EXPOSE|VOLUME|SHELL|HEALTHCHECK)\\b/m],
    ['va', /\\$\\{{[^}}]*\\}}|\\$[A-Za-z_][A-Za-z0-9_]*/]
  ],
  md: [
    ['mt', /^#{{1,6}} [^\\n]*/m],
    ['st', /`[^`\\n]*`/],
    ['kw', /\\*\\*[^*\\n]+\\*\\*/]
  ]
}};

function kindOf(file) {{
  if (/\\.patch$/.test(file)) return 'diff';
  if (/Dockerfile$/i.test(file)) return 'docker';
  if (/\\.sh$/.test(file)) return 'sh';
  if (/\\.md$/.test(file)) return 'md';
  return 'plain';
}}

function highlightDiff(text) {{
  return text.split('\\n').map(function (line) {{
    var e = escapeHtml(line);
    if (line.charAt(0) === '+' && line.slice(0, 3) !== '+++') return '<span class="add">' + e + '</span>';
    if (line.charAt(0) === '-' && line.slice(0, 3) !== '---') return '<span class="del">' + e + '</span>';
    if (line.slice(0, 2) === '@@') return '<span class="hunk">' + e + '</span>';
    return e;
  }}).join('\\n');
}}

function highlightCode(text, rules) {{
  var parts = rules.map(function (r) {{ return '(' + r[1].source + ')'; }});
  var flags = 'g' + (rules.some(function (r) {{ return r[1].flags.indexOf('m') >= 0; }}) ? 'm' : '');
  var re = new RegExp(parts.join('|'), flags);
  var out = '', last = 0, m;
  while ((m = re.exec(text)) !== null) {{
    if (m.index > last) {{ out += escapeHtml(text.slice(last, m.index)); }}
    var cls = 'cm';
    for (var i = 1; i < m.length; i++) {{
      if (m[i] !== undefined) {{ cls = rules[i - 1][0]; break; }}
    }}
    out += '<span class="' + cls + '">' + escapeHtml(m[0]) + '</span>';
    last = m.index + m[0].length;
    if (m[0].length === 0) {{ re.lastIndex++; }}
  }}
  return out + escapeHtml(text.slice(last));
}}

function renderCode(text, file) {{
  var kind = kindOf(file);
  var body = kind === 'diff' ? highlightDiff(text)
           : RULES[kind] ? highlightCode(text, RULES[kind])
           : escapeHtml(text);
  return body.split('\\n').map(function (line, i) {{
    return '<span class="ln">' + (i + 1) + '</span>' + line;
  }}).join('\\n');
}}

function showSample(sample) {{
  var view = document.getElementById('samplesView');
  var meta = [sample.language, sample.difficulty, sample.area, sample.topic, sample.bug_class]
    .filter(Boolean).join(' · ');
  var tabs = sample.parts.map(function (p, i) {{
    var kb = p.bytes >= 1024 ? (p.bytes / 1024).toFixed(1) + ' KB' : p.bytes + ' B';
    return '<button class="tab' + (i === 0 ? ' active' : '') + '" data-i="' + i + '">' +
      escapeHtml(p.label) + '<span class="sz">' + kb + '</span></button>';
  }}).join('');

  view.innerHTML =
    '<div class="sample-head"><div class="t">' + escapeHtml(sample.task_name) + '</div>' +
    '<div class="m">' + escapeHtml(sample.repo || '') +
    (meta ? ' &nbsp;|&nbsp; ' + escapeHtml(meta) : '') + '</div></div>' +
    '<div class="tabs">' + tabs + '</div>' +
    '<div class="sample-body"><pre class="sample-pre"><code id="samplesPre"></code></pre></div>';

  function show(i) {{
    var part = sample.parts[i];
    var pre = document.getElementById('samplesPre');
    pre.innerHTML = renderCode(part.text, part.file);
    view.querySelectorAll('.tab').forEach(function (t) {{
      t.classList.toggle('active', t.dataset.i === String(i));
    }});
  }}
  view.querySelectorAll('.tab').forEach(function (t) {{
    t.addEventListener('click', function () {{ show(parseInt(t.dataset.i, 10)); }});
  }});
  if (sample.parts.length) {{ show(0); }}
  else {{ view.innerHTML += '<div class="empty-state">no readable files in this task</div>'; }}
}}

function openSamples(batch) {{
  var info = SAMPLE_INDEX[batch];
  var list = document.getElementById('samplesList');
  var view = document.getElementById('samplesView');
  document.getElementById('samplesTitle').textContent = 'Sample tasks — ' + batch;
  view.innerHTML = '';
  closeModals();
  samplesModal.hidden = false;

  if (!info) {{
    view.innerHTML = '<div class="empty-state">no samples cached for this batch</div>';
    return;
  }}
  document.getElementById('samplesSub').textContent =
    info.count + ' of this batch — pick one to see what a task contains';
  list.innerHTML = info.tasks.map(function (t) {{
    return '<button class="sample-row" data-task="' + escapeHtml(t.task_name) + '">' +
      '<span class="sid">' + escapeHtml(t.task_name) + '</span>' +
      '<span class="smeta">' + escapeHtml([t.language, t.difficulty].filter(Boolean).join(' · ')) +
      '</span></button>';
  }}).join('');

  function pick(row) {{
    list.querySelectorAll('.sample-row').forEach(function (o) {{
      o.classList.toggle('active', o === row);
    }});
    var s = (sampleCache[batch] || []).filter(function (x) {{
      return x.task_name === row.dataset.task;
    }})[0];
    if (s) {{ showSample(s); }}
  }}
  list.querySelectorAll('.sample-row').forEach(function (row) {{
    row.addEventListener('click', function () {{ pick(row); }});
  }});

  if (sampleCache[batch]) {{ return; }}
  view.innerHTML = '<div class="empty-state">loading…</div>';
  fetch(info.file).then(function (r) {{
    if (!r.ok) {{ throw new Error('HTTP ' + r.status); }}
    return r.json();
  }}).then(function (data) {{
    sampleCache[batch] = data;
    var first = list.querySelector('.sample-row');
    if (first) {{ first.click(); }} else {{ view.innerHTML = ''; }}
  }}).catch(function (e) {{
    view.innerHTML = '<div class="empty-state">could not load samples: ' +
      escapeHtml(e.message) + '</div>';
  }});
}}

document.querySelectorAll('.samples-btn[data-samples]').forEach(function (btn) {{
  btn.addEventListener('click', function () {{ openSamples(btn.dataset.samples); }});
}});
samplesModal.addEventListener('click', function (e) {{
  if (e.target === samplesModal || (e.target.hasAttribute && e.target.hasAttribute('data-close'))) {{
    samplesModal.hidden = true;
  }}
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
    parser = argparse.ArgumentParser(description="Generate the curator databoard")
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--block-dir", type=Path, default=BLOCK_DIR,
                        help="curator block root holding artifacts/ (default: this block)")
    parser.add_argument("--report-only", action="store_true",
                        help="print the source report and exit without rendering")
    args = parser.parse_args()

    cfg = discover_sources(args.block_dir)
    tasks_root = cfg["tasks_root"]
    if not cfg["batches"]:
        raise SystemExit(
            f"no task batches found under {tasks_root}\n"
            "Each immediate subdirectory there is one batch (py-cc, go-cc, ...).\n"
            "To put a third-party dataset on the board, symlink it in:\n"
            f"  ln -s /path/to/dataset {tasks_root}/<name>"
        )

    print("=" * 70)
    print("curator dashboard sources")
    print("=" * 70)
    print(f"  task batches           {tasks_root}/*")

    batches = []
    problems: list[str] = []
    for entry in cfg["batches"]:
        # A mistyped path renders a board that reports zero, which reads as "no
        # tasks" rather than "wrong directory". Classify the layout up front.
        layout = check_task_dir(entry["path"])
        if layout["status"] not in {"ok", "partial"}:
            problems.append(f"{entry['name']}: {format_result(layout)}")
        b = aggregate_batch(entry["name"], entry["path"], entry.get("external", False))
        b["layout"] = layout["status"]
        batches.append(b)
        state = "" if b["exists"] else "  [PATH NOT FOUND]"
        if layout["status"] not in {"ok", "partial"}:
            state = f"  [LAYOUT: {layout['status'].upper()}]"
        elif b.get("external"):
            state = "  [symlinked dataset]"
        langs = ", ".join(f"{k}={v}" for k, v in list(b["languages"].items())[:6]) or "-"
        print(f"  {b['name']:22s} {b['path']}{state}")
        stale = (f" · {b['stale_verified']:,} verified ids no longer on disk"
                 if b.get("stale_verified") else "")
        print(f"  {'':22s} {b['total']:>7,} tasks · {b['verified']:>7,} verified · "
              f"{b['tagged']:>7,} tagged{stale}")
        print(f"  {'':22s} languages: {langs}")

    # overlap is expected (merged_swe_tasks is a filtered copy of swe_tasks);
    # report it so the de-duplicated Overview totals are never a surprise
    for i, a_ in enumerate(batches):
        for b_ in batches[i + 1:]:
            shared = set(a_["tasks"]) & set(b_["tasks"])
            if shared:
                print(f"  overlap              {len(shared):,} task ids shared between "
                      f"{a_['name']} and {b_['name']} (counted once in Overview)")

    prs = collect_pr_stats_multi(cfg["prs"])
    for d in prs.get("dirs") or []:
        print(f"  {d['name']:22s} {d['dir']}{'' if d['exists'] else '  [NOT FOUND]'}")
    print(f"  {'':22s} {prs.get('total_prs', 0):,} PRs · {prs.get('total_repos', 0):,} repos "
          f"· {len(prs.get('languages', {}))} languages")

    combined = combine_datasets(batches)
    print(f"  global (de-duplicated) {combined['total']:,} unique tasks · "
          f"{combined['verified']:,} verified · {combined['tagged']:,} tagged")
    if problems:
        print("-" * 70)
        print("LAYOUT PROBLEMS — a batch must hold one harbor task per immediate child")
        print("(each with task.toml + instruction.md):")
        for line in problems:
            print("  " + line.replace("\n", "\n  "))
        print("  A batch is a directory of harbor tasks. Remove or fix the flagged")
        print("  entry under artifacts/swe_tasks/ before trusting these numbers.")
    print("=" * 70)

    if args.report_only:
        return 1 if problems else 0

    samples = write_samples(batches, args.output_html.parent)
    print(f"  samples                {sum(v['count'] for v in samples.values())} task(s) cached "
          f"across {len(samples)} batch(es)")
    render_html(batches, args.output_html, prs, cfg["pr_filters"], samples, tasks_root)
    print(f"✓ generated: {args.output_html}  ({args.output_html.stat().st_size/1024:.0f} KB)")
    print("=" * 70)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main() or 0)

