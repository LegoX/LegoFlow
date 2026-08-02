#!/usr/bin/env python3
"""Inject the swe_rebench_v2 panel into the already-deployed dashboard HTML,
and unify all panels to the detailed-methodology style.

Why surgery instead of a full regenerate: the 4 original datasets
(self_made, swe_rebench, openswe_filtered, scale_swe) were tagged on another
host; their tags.jsonl are NOT present locally, only their rendered panels
survive inside the frozen base snapshot base_4ds.html. So we keep those 4
rendered panels verbatim and only:
  1. render a fresh swe_rebench_v2 panel from its freshly-tagged tags.jsonl,
     inserting it right after swe_rebench (matching DATASETS order),
  2. replace every brief "Tag schema" block with the prominent Methodology,
  3. drop the "Tagged (LLM)" card and "· N tagged" / "<span>N tagged</span>"
     counts (unified "N tasks" style),
  4. swap in the current (enlarged, English-only) CSS from progress_monitor_multi
     and fix the page language + subtitle.

Input : base_4ds.html (frozen 4-dataset snapshot; falls back to /tmp/deployed_index.html)
Output: site/index.html
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import progress_monitor_multi as pm

DASH = Path(__file__).parent
BASE = DASH / "base_4ds.html"
DEPLOYED = BASE if BASE.exists() else Path("/tmp/deployed_index.html")
OUTPUT = DASH / "site" / "index.html"

V2_ID = "swe_rebench_v2"
V2_DISPLAY = "SWE-rebench-V2"

# Prominent methodology block, shared with progress_monitor_multi (single source).
METHODOLOGY = pm.METHODOLOGY_HTML


def render_v2_panel(data: dict) -> str:
    """Render the swe_rebench_v2 panel in the unified new style (no tagged card,
    detailed methodology), matching the deployed panels' class structure."""
    stats = data["difficulty_stats"]
    total = data["total"]
    tagged = data["tagged"]

    lang_rows = pm.render_tags(data["languages"], tagged, limit=15)
    area_rows = pm.render_tags(data["areas"], tagged, limit=6)
    topic_rows = pm.render_tags(data["topics"], data["tasks_with_topic"], limit=25)
    bug_rows = pm.render_tags(data["bug_classes"], data["tasks_with_bug_class"], limit=20)
    bin_rows = pm.render_score_bins(data["difficulty_bins"])

    return f"""<div class="ds-panel" data-ds="{V2_ID}">
  <div class="cards">
    <div class="card"><div class="k">Total tasks</div><div class="v">{pm.fmt_int(total)}</div></div>
    <div class="card"><div class="k">Mean difficulty</div><div class="v">{pm.fmt_float(stats['mean'], 2)}</div></div>
    <div class="card"><div class="k">Median difficulty</div><div class="v">{pm.fmt_float(stats['median'], 1)}</div></div>
    <div class="card"><div class="k">Avg patch lines</div><div class="v">{pm.fmt_float(data['patch']['avg_lines'], 1)}</div></div>
    <div class="card"><div class="k">Avg patch files</div><div class="v">{pm.fmt_float(data['patch']['avg_files'], 2)}</div></div>
  </div>

  <div class="panel">
    <h2>Difficulty distribution <span>{html.escape(V2_DISPLAY)}</span></h2>
    <table>
      <tr><th>Label breakdown</th><th>easy</th><th>medium</th><th>hard</th></tr>
      <tr>
        <td>{pm.render_label_bar(data)}</td>
        <td>{pm.fmt_int(pm.label_count(data, 'easy'))}</td>
        <td>{pm.fmt_int(pm.label_count(data, 'medium'))}</td>
        <td>{pm.fmt_int(pm.label_count(data, 'hard'))}</td>
      </tr>
    </table>
    <table style="margin-top:14px;">
      <tr><th>count</th><th>min</th><th>p25</th><th>median</th><th>mean</th><th>p75</th><th>max</th></tr>
      <tr>
        <td>{pm.fmt_int(stats['count'])}</td>
        <td>{pm.fmt_float(stats['min'], 1)}</td>
        <td>{pm.fmt_float(stats['p25'], 1)}</td>
        <td>{pm.fmt_float(stats['median'], 1)}</td>
        <td>{pm.fmt_float(stats['mean'], 2)}</td>
        <td>{pm.fmt_float(stats['p75'], 1)}</td>
        <td>{pm.fmt_float(stats['max'], 1)}</td>
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
{METHODOLOGY}
  </div>
</div>
"""


def unify_existing(doc: str) -> str:
    """Transform the 4 deployed panels to the unified new style."""
    # 1. drop "Tagged (LLM)" cards
    doc = re.sub(
        r'\s*<div class="card"><div class="k">Tagged \(LLM\)</div>.*?</div></div>',
        "",
        doc,
    )
    # 2. replace every brief "Tag schema" block with detailed methodology
    doc = re.sub(
        r'    <section class="tag-card"><h3>Tag schema.*?</section>',
        METHODOLOGY,
        doc,
        flags=re.DOTALL,
    )
    # 3. strip "<span>N tagged</span>" from card headers
    doc = re.sub(r' <span>\d[\d,]* tagged</span>', "", doc)
    # 4. strip "· N tagged" from nav counts -> keep "N tasks"
    doc = re.sub(r'(\d[\d,]* tasks) &middot; \d[\d,]* tagged', r"\1", doc)
    doc = re.sub(r'(\d[\d,]* tasks) · \d[\d,]* tagged', r"\1", doc)
    # 5. drop the sidebar "Total tagged" global stat
    doc = re.sub(
        r'\s*<div class="sidebar-stat"><span class="l">Total tagged</span>.*?</div>',
        "",
        doc,
    )
    # 6. make the Bug classes card span the full grid width (= Area/tier + Top topics)
    doc = doc.replace(
        '<section class="tag-card"><h3>Bug classes',
        '<section class="tag-card wide-card"><h3>Bug classes',
    )
    return doc


def fix_sidebar_totals(doc: str, v2_total: int) -> str:
    """Bump the sidebar Global stats to include the injected v2 dataset."""
    # Datasets: 4 -> 5
    doc = re.sub(
        r'(<span class="l">Datasets</span><span class="v">)(\d+)(</span>)',
        lambda m: f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}",
        doc,
    )
    # Total tasks: add v2_total
    doc = re.sub(
        r'(<span class="l">Total tasks</span><span class="v">)([\d,]+)(</span>)',
        lambda m: f"{m.group(1)}{int(m.group(2).replace(',', '')) + v2_total:,}{m.group(3)}",
        doc,
    )
    return doc


def apply_style_and_i18n(doc: str) -> str:
    """Swap in the current enlarged CSS and make the page English-only."""
    # 1. replace the entire <style>...</style> block with the shared CSS
    doc = re.sub(
        r"<style>.*?</style>",
        f"<style>{pm.CSS}</style>",
        doc,
        count=1,
        flags=re.DOTALL,
    )
    # 2. page language -> English
    doc = doc.replace('<html lang="zh"', '<html lang="en"')
    # 3. remove the subtitle line entirely
    doc = re.sub(r'\s*<div class="sub">.*?</div>', "", doc, count=1, flags=re.DOTALL)
    return doc


def main() -> None:
    doc = DEPLOYED.read_text(encoding="utf-8")

    # --- aggregate + render v2 ---
    data = pm.aggregate_dataset(V2_ID)
    if data["tagged"] == 0:
        raise SystemExit(f"{V2_ID}: tags.jsonl empty; run tagging first")
    print(f"{V2_ID}: total={data['total']} tagged={data['tagged']}")
    v2_panel = render_v2_panel(data)
    v2_count = pm.fmt_int(data["total"])
    v2_nav = (
        f'<button class="ds-item" data-ds="{V2_ID}" onclick="switchDs(\'{V2_ID}\')">'
        f'<span class="ds-name">{html.escape(V2_DISPLAY)}</span>'
        f'<span class="ds-count">{v2_count} tasks</span></button>'
    )

    # --- unify the 4 existing panels first (before injection) ---
    doc = unify_existing(doc)
    doc = fix_sidebar_totals(doc, data["total"])
    doc = apply_style_and_i18n(doc)

    # --- inject v2 nav button right after the swe_rebench nav button ---
    nav_anchor_re = re.compile(
        r'(<button class="ds-item" data-ds="swe_rebench" onclick="switchDs\(\'swe_rebench\'\)">.*?</button>)',
        re.DOTALL,
    )
    m = nav_anchor_re.search(doc)
    if not m:
        raise SystemExit("could not find swe_rebench nav button anchor")
    doc = doc[: m.end()] + v2_nav + doc[m.end():]

    # --- inject v2 panel right after the swe_rebench panel closes ---
    # find <div class="ds-panel" data-ds="swe_rebench"> ... its matching close
    panel_start = doc.find('<div class="ds-panel" data-ds="swe_rebench">')
    if panel_start == -1:
        raise SystemExit("could not find swe_rebench panel")
    # next panel starts at openswe_filtered
    next_panel = doc.find('<div class="ds-panel" data-ds="openswe_filtered">', panel_start)
    if next_panel == -1:
        raise SystemExit("could not find openswe_filtered panel (v2 insert point)")
    doc = doc[:next_panel] + v2_panel + "\n" + doc[next_panel:]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    # sanity checks
    for did in ("self_made", "swe_rebench", "swe_rebench_v2", "openswe_filtered", "scale_swe"):
        n = doc.count(f'data-ds="{did}"')
        print(f"  {did}: {n} refs (expect 2: nav+panel)")
    print(f"  Tagged (LLM) cards remaining: {doc.count('Tagged (LLM)')}")
    print(f"  Tag schema blocks remaining: {doc.count('Tag schema')}")
    print(f"  Methodology blocks: {doc.count('>Methodology ')}")
    print(f"  method-card blocks: {doc.count('method-card')}")
    chinese = re.findall(r"[一-鿿]", doc)
    print(f"  Chinese chars remaining: {len(chinese)}")
    print(f"  lang=en: {'<html lang=\"en\"' in doc}")


if __name__ == "__main__":
    main()
