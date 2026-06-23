#!/usr/bin/env python3
"""terminalgen progress dashboard generator.

Reads per-domain task data under artifacts/terminal_tasks/{domain}-tl/, emits a
static HTML summary, and appends a snapshot line to a JSONL state file. Mirrors
the swegen dashboard CLI surface (--output-html / --state-file / --cache-file /
--serve) but is scoped to terminalgen's domain model.

Defaults read this block's own artifacts/ (terminalgen runs locally, not from a
separate data root like swegen's $HOME/SWE-gen).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

DASHBOARD_ROOT = Path(__file__).resolve().parent
BLOCK_ROOT = DASHBOARD_ROOT.parent
TASKS_ROOT = BLOCK_ROOT / "artifacts" / "terminal_tasks"
QUESTIONS_ROOT = BLOCK_ROOT / "artifacts" / "collected_questions"
CONFIG = BLOCK_ROOT / "config.yaml"

DEFAULT_HTML = DASHBOARD_ROOT / "site" / "index.html"
DEFAULT_STATE = DASHBOARD_ROOT / "memory" / ".progress_monitor_all_state.jsonl"
DEFAULT_CACHE = DASHBOARD_ROOT / "memory" / ".progress_monitor_all_cache.json"


def _domains():
    try:
        import yaml
        cfg = yaml.safe_load(CONFIG.read_text()) or {}
        doms = cfg.get("runtime_info", {}).get("input", {}).get("domains", {})
        return list(doms.keys())
    except Exception:
        # Fall back to discovering {domain}-tl dirs on disk.
        if TASKS_ROOT.is_dir():
            return sorted(p.name[:-3] for p in TASKS_ROOT.glob("*-tl"))
        return []


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for ln in path.read_text().splitlines() if ln.strip())


def _count_questions(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(json.loads(path.read_text()).get("questions", []))
    except Exception:
        return 0


def collect():
    rows = []
    for dom in _domains():
        dom_dir = TASKS_ROOT / f"{dom}-tl"
        cand_dir = dom_dir / "_candidates"
        scraped = _count_questions(QUESTIONS_ROOT / f"{dom}_so_data.json")
        # Candidates live in per-chunk subdirs: _candidates/s<start>/task_*
        generated = len(list(cand_dir.glob("s*/task_*"))) if cand_dir.is_dir() else 0
        verified = _count_lines(dom_dir / "verifiable_tasks.txt")
        rate = (verified / generated) if generated else 0.0
        rows.append({
            "domain": dom, "scraped": scraped, "generated": generated,
            "verified": verified, "rate": round(rate, 3),
        })
    return rows


def render_html(rows) -> str:
    total_v = sum(r["verified"] for r in rows)
    total_g = sum(r["generated"] for r in rows)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    trs = "\n".join(
        f"<tr><td>{r['domain']}</td><td>{r['scraped']}</td><td>{r['generated']}</td>"
        f"<td>{r['verified']}</td><td>{r['rate']*100:.0f}%</td></tr>"
        for r in rows
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>terminalgen progress</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:4px 10px;text-align:right}}td:first-child,th:first-child{{text-align:left}}</style>
</head><body>
<h1>terminalgen progress</h1>
<p>generated {ts} &middot; total verified {total_v} / generated {total_g}</p>
<table><tr><th>Domain</th><th>Scraped</th><th>Generated</th><th>Verified</th><th>Rate</th></tr>
{trs}
</table></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-html", default=str(DEFAULT_HTML))
    ap.add_argument("--state-file", default=str(DEFAULT_STATE))
    ap.add_argument("--cache-file", default=str(DEFAULT_CACHE))
    ap.add_argument("--serve", action="store_true", help="serve the dashboard dir over HTTP")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    rows = collect()
    html = render_html(rows)

    out = Path(args.output_html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)

    state = Path(args.state_file)
    state.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": rows,
                "total_verified": sum(r["verified"] for r in rows)}
    with state.open("a") as f:
        f.write(json.dumps(snapshot) + "\n")

    # cache-file kept for CLI compatibility; we write the latest snapshot to it.
    Path(args.cache_file).parent.mkdir(parents=True, exist_ok=True)
    Path(args.cache_file).write_text(json.dumps(snapshot, indent=2))

    print(f"wrote {out} ({len(rows)} domains, {snapshot['total_verified']} verified)")

    if args.serve:
        import http.server
        import os
        os.chdir(out.parent)
        httpd = http.server.HTTPServer(("0.0.0.0", args.port),
                                       http.server.SimpleHTTPRequestHandler)
        print(f"serving {out.parent} at http://0.0.0.0:{args.port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
