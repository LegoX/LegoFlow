#!/usr/bin/env python3
"""Re-wrap the frozen site/index.html in the current shell.

progress_monitor_multi.py owns the layout, but it needs datasets/<id>/tasks.jsonl
to render anything, and those inputs are not in this checkout — the five dataset
panels only survive as rendered HTML inside the deployed snapshot. This script
keeps those panels verbatim and swaps everything around them for the current
shell, so the published page shows the current layout instead of the retired
dataset-as-navigation one.

It is a stopgap for exactly that gap. Once datasets/ is available, run
`progress_monitor_multi.py` instead and delete this file — a full regenerate also
gives the combined Overview aggregate and a populated Task List, neither of which
can be recovered from rendered HTML.

Usage: python3 migrate_frozen_shell.py [--in site/index.html] [--out site/index.html]
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

import progress_monitor_multi as pm

DASH = Path(__file__).parent
PANEL_RE = re.compile(r'<div class="ds-panel[^"]*" data-ds="([^"]+)">')
NAV_RE = re.compile(
    r'data-ds="([^"]+)"[^>]*>\s*<span class="ds-name">([^<]+)</span>'
    r'<span class="ds-count">([\d,]+) tasks</span>'
)


def extract(doc: str) -> tuple[list[tuple[str, str, int]], dict[str, str]]:
    """Return [(ds_id, display, total)] from the old nav, and {ds_id: panel_html}."""
    datasets = [
        (m.group(1), m.group(2), int(m.group(3).replace(",", "")))
        for m in NAV_RE.finditer(doc)
    ]
    if not datasets:
        raise SystemExit("no dataset nav buttons found — is this the frozen snapshot?")

    starts = [(m.start(), m.group(1)) for m in PANEL_RE.finditer(doc)]
    if not starts:
        raise SystemExit("no .ds-panel blocks found")
    script_at = doc.find("<script>")
    if script_at == -1:
        raise SystemExit("no <script> block found — cannot bound the last panel")

    panels: dict[str, str] = {}
    for i, (pos, ds_id) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else script_at
        block = doc[pos:end]
        # the final panel trails the .content/.main/.layout closers
        if i + 1 == len(starts):
            block = re.sub(r"(?:\s*</div>){1,3}\s*$", "", block)
        # normalise: the shell decides which panel is active
        panels[ds_id] = block.replace('class="ds-panel active"', 'class="ds-panel"', 1)
    return datasets, panels


def build(doc: str) -> str:
    datasets, panels = extract(doc)
    grand_total = sum(t for _, _, t in datasets)

    # Generate the current shell, then transplant the frozen panels into Overview.
    tmp = DASH / ".migrate-shell.tmp.html"
    shell = pm.render_html([], tmp, {"total": 0, "per_dataset": {}, "shards": [], "languages": []})
    tmp.unlink(missing_ok=True)

    chips, blocks = [], []
    for i, (ds_id, display, total) in enumerate(datasets):
        block = panels.get(ds_id)
        if block is None:
            continue
        first = not blocks
        chips.append(
            f'<button class="chip{" active" if first else ""}" data-ds="{ds_id}">'
            f'{html.escape(display)}<span class="n">{total:,}</span></button>'
        )
        blocks.append(
            block.replace('class="ds-panel"', 'class="ds-panel active"', 1) if first else block
        )

    note = (
        '<div class="panel"><h2>Overview <span>per-dataset only</span></h2>'
        '<div class="muted">This page was re-wrapped from a rendered snapshot, so the '
        'combined cross-dataset aggregate and the Task List are unavailable. Run '
        '<code>progress_monitor_multi.py</code> against <code>datasets/</code> for the '
        'full Overview and a populated Task List.</div></div>'
    )
    overview = (
        '<div class="page active" id="page-overview">\n'
        f'  <div class="filters">{"".join(chips)}</div>\n'
        f'  {note}\n{"".join(blocks)}\n</div>\n'
    )

    start = shell.find('<div class="page active" id="page-overview">')
    end = shell.find('<div class="page" id="page-tasks">')
    if start == -1 or end == -1:
        raise SystemExit("shell markers not found — did render_html's structure change?")
    out = shell[:start] + overview + shell[end:]

    # sidebar/topbar counters the empty render could not know
    out = out.replace(
        '<span class="l">Total tasks</span><span class="v">0</span>',
        f'<span class="l">Total tasks</span><span class="v">{grand_total:,}</span>',
    ).replace(
        '<span class="l">Datasets</span><span class="v">0</span>',
        f'<span class="l">Datasets</span><span class="v">{len(datasets)}</span>',
    )
    out = re.sub(
        r"(id=\"page-sub\">)0 datasets · 0 tasks",
        rf"\g<1>{len(datasets)} datasets · {grand_total:,} tasks",
        out,
    )
    out = out.replace(
        "overview: {title: 'Overview', sub: '0 datasets · 0 tasks'}",
        f"overview: {{title: 'Overview', sub: '{len(datasets)} datasets · {grand_total:,} tasks'}}",
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="src", type=Path, default=DASH / "site" / "index.html")
    ap.add_argument("--out", dest="dst", type=Path, default=DASH / "site" / "index.html")
    args = ap.parse_args()

    out = build(args.src.read_text(encoding="utf-8"))
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(out, encoding="utf-8")
    print(f"wrote {args.dst} ({len(out)/1024:.0f} KB)")
    for marker, label in (
        ('data-page="overview"', "sidebar Overview"),
        ('data-page="tasks"', "sidebar Task List"),
        ('class="ds-panel', "dataset panels"),
        ('<button class="ds-item', "legacy dataset nav (expect 0)"),
    ):
        print(f"  {label}: {out.count(marker)}")


if __name__ == "__main__":
    main()
