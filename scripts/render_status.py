#!/usr/bin/env python3
"""Render status.yaml + meta-info.yaml + artifacts/index.yaml into a self-contained status.html.

Usage:
    python scripts/render_status.py              # writes status.html
    python scripts/render_status.py --open       # also opens in browser
    python scripts/render_status.py --out /tmp/status.html
"""

import argparse
import html
import pathlib
import sys
import webbrowser
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")


PHASE_COLORS = {
    "idle":      ("#6b7280", "#334155"),
    "running":   ("#fbbf24", "#334155"),
    "paused":    ("#a78bfa", "#334155"),
    "completed": ("#34d399", "#334155"),
    "failed":    ("#f87171", "#334155"),
    "blocked":   ("#f87171", "#334155"),
}

RUN_STATUS_COLORS = {
    "completed": "#34d399",
    "running":   "#fbbf24",
    "failed":    "#f87171",
}


def load_yaml(path):
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def read_log_tail(log_path, n=50):
    if not log_path:
        return None
    p = pathlib.Path(log_path)
    if not p.exists():
        return None
    lines = p.read_text().splitlines()
    return lines[-n:]


def badge(text, fg, bg):
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 10px;border-radius:12px;'
        f'font-size:0.78em;font-weight:700;letter-spacing:0.06em;text-transform:uppercase">'
        f'{html.escape(str(text))}</span>'
    )


def progress_bar(value):
    if value is None:
        pct, label = 0, "unknown"
    else:
        pct = max(0, min(100, int(float(value) * 100)))
        label = f"{pct}%"
    return (
        f'<div style="background:#e5e7eb;border-radius:6px;height:16px;width:100%;margin:8px 0 4px">'
        f'<div style="background:#3b82f6;width:{pct}%;height:100%;border-radius:6px"></div></div>'
        f'<div style="font-size:0.8em;color:#6b7280">{label} complete</div>'
    )


def section(title, body, extra_style=""):
    return (
        f'<div class="card" style="{extra_style}">'
        f'<div class="card-title">{html.escape(title)}</div>'
        f'{body}</div>'
    )


def render_runs(runs):
    if not runs:
        return '<div style="color:#9ca3af;font-style:italic">No runs recorded yet.</div>'

    header = (
        '<table style="border-collapse:collapse;width:100%;font-size:0.88em">'
        '<thead><tr style="border-bottom:2px solid #e5e7eb">'
        + "".join(
            f'<th style="padding:6px 12px 6px 0;text-align:left;color:#6b7280;font-weight:600">{h}</th>'
            for h in ["Run", "Status", "Started", "Notes", "Params", "Metrics", "Log"]
        )
        + "</tr></thead><tbody>"
    )

    rows = ""
    for r in reversed(runs):
        run_id  = r.get("id", "")
        status  = r.get("status", "")
        started = r.get("started_at", "")
        notes   = r.get("notes", "")
        params  = r.get("params") or ""
        metrics = r.get("metrics") or ""
        log     = r.get("log") or ""
        color   = RUN_STATUS_COLORS.get(status, "#6b7280")

        def cell(content):
            return f'<td style="padding:7px 12px 7px 0;vertical-align:top">{content}</td>'

        rows += (
            "<tr style='border-bottom:1px solid #f1f5f9'>"
            + cell(f'<strong>{html.escape(str(run_id))}</strong>')
            + cell(f'<span style="color:{color};font-weight:600">{html.escape(str(status))}</span>')
            + cell(f'<span style="color:#6b7280">{html.escape(str(started))}</span>')
            + cell(html.escape(str(notes)))
            + cell(f'<code>{html.escape(str(params))}</code>' if params else "—")
            + cell(f'<code>{html.escape(str(metrics))}</code>' if metrics else "—")
            + cell(f'<code>{html.escape(str(log))}</code>' if log else "—")
            + "</tr>"
        )

    return header + rows + "</tbody></table>"


def render(status, meta, artifacts, log_lines, source_files):
    block_name = meta.get("label") or meta.get("name") or status.get("block") or "Block"
    role       = meta.get("role", "")
    phase      = (status.get("phase") or "idle").lower()
    stage      = status.get("stage") or ""
    updated_at = status.get("updated_at") or ""

    phase_fg, phase_bg = PHASE_COLORS.get(phase, ("#6b7280", "#334155"))

    job        = status.get("current_job") or {}
    history    = status.get("history") or []
    next_steps = status.get("next_steps") or []
    blockers   = status.get("blockers") or []
    metrics    = status.get("metrics") or {}
    runs       = artifacts.get("runs") or []

    parts = []

    # header
    role_badge  = badge(role, "#94a3b8", "#1e293b") + "&nbsp;" if role else ""
    phase_badge = badge(phase, phase_fg, phase_bg)
    stage_span  = (f'&nbsp;<span style="color:#94a3b8;font-size:0.85em">'
                   f'stage: {html.escape(stage)}</span>') if stage else ""
    parts.append(f"""
<div style="background:#1e293b;color:#f8fafc;padding:20px 32px;
            display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
  <div>
    <div style="font-size:1.4em;font-weight:700">{html.escape(block_name)}</div>
    <div style="margin-top:4px">{role_badge}{phase_badge}{stage_span}</div>
  </div>
  <div style="font-size:0.8em;color:#94a3b8;text-align:right">
    last updated<br>
    <strong style="color:#e2e8f0">{html.escape(str(updated_at))}</strong>
  </div>
</div>""")

    # current job
    job_name     = job.get("name") or "—"
    job_detail   = job.get("detail") or ""
    job_started  = job.get("started_at") or ""
    job_progress = job.get("progress")

    job_body = (
        f'<div style="font-size:1.05em;font-weight:600;margin-bottom:4px">'
        f'{html.escape(str(job_name))}</div>'
        + progress_bar(job_progress)
        + (f'<div style="margin-top:10px;color:#374151">{html.escape(job_detail)}</div>'
           if job_detail else "")
        + (f'<div style="margin-top:8px;font-size:0.8em;color:#6b7280">'
           f'Started: {html.escape(str(job_started))}</div>' if job_started else "")
    )
    parts.append(section("Current Job", job_body))

    # experiment runs
    parts.append(section("Experiment Runs", render_runs(runs)))

    # next steps
    if next_steps:
        rows = ""
        for s in next_steps:
            blocked = s.get("blocked_by")
            opacity = "0.4" if blocked else "1"
            rows += (
                f'<div style="display:flex;gap:14px;padding:10px 0;'
                f'border-bottom:1px solid #f1f5f9;opacity:{opacity}">'
                f'<div style="font-size:0.85em;color:#9ca3af;min-width:22px;'
                f'text-align:right;padding-top:2px">{s.get("order", "")}</div>'
                f'<div><div style="font-weight:600">{html.escape(str(s.get("label", "")))}</div>'
                + (f'<div style="font-size:0.85em;color:#6b7280">{html.escape(str(s.get("detail", "")))}</div>'
                   if s.get("detail") else "")
                + (f'<div style="font-size:0.78em;color:#ef4444;margin-top:2px">'
                   f'blocked by: {html.escape(str(blocked))}</div>' if blocked else "")
                + "</div></div>"
            )
        parts.append(section("Next Steps", rows))

    # blockers
    if blockers:
        rows = ""
        for b in blockers:
            rows += (
                f'<div style="padding:8px 0;border-bottom:1px solid #fecaca">'
                f'<div style="font-weight:600">{html.escape(str(b.get("label", "")))}</div>'
                f'<div style="font-size:0.8em;color:#b91c1c">'
                + ("Since: " + html.escape(str(b.get("since", ""))) if b.get("since") else "")
                + ("&nbsp;&nbsp;Owner: " + html.escape(str(b.get("owner", ""))) if b.get("owner") else "")
                + "</div></div>"
            )
        parts.append(section("Blockers", rows, "border-left:4px solid #ef4444;background:#fff5f5"))

    # metrics
    if metrics:
        rows = "".join(
            f'<tr><td style="padding:5px 16px 5px 0;color:#6b7280">{html.escape(str(k))}</td>'
            f'<td style="padding:5px 0;font-weight:600">{html.escape(str(v))}</td></tr>'
            for k, v in metrics.items()
        )
        parts.append(section("Metrics", f'<table style="border-collapse:collapse">{rows}</table>'))

    # history
    if history:
        rows = ""
        for h in reversed(history):
            artifact = h.get("artifact")
            rows += (
                f'<div style="display:flex;gap:16px;padding:10px 0;border-bottom:1px solid #f1f5f9">'
                f'<div style="font-size:0.75em;color:#9ca3af;white-space:nowrap;min-width:170px;padding-top:2px">'
                f'{html.escape(str(h.get("timestamp", "")))}</div>'
                f'<div>'
                f'<div style="font-weight:600">{html.escape(str(h.get("label", "")))}</div>'
                + (f'<div style="font-size:0.85em;color:#6b7280">{html.escape(str(h.get("detail", "")))}</div>'
                   if h.get("detail") else "")
                + (f'<div style="font-size:0.8em;color:#3b82f6;margin-top:2px">'
                   f'<code>{html.escape(str(artifact))}</code></div>' if artifact else "")
                + "</div></div>"
            )
        parts.append(section("History", rows))

    # log tail
    if log_lines is not None:
        log_html = html.escape("\n".join(log_lines))
        log_body = (
            f'<pre style="background:#0f172a;color:#e2e8f0;padding:16px;border-radius:6px;'
            f'overflow:auto;max-height:320px;font-size:0.78em;margin:0;'
            f'white-space:pre-wrap;word-break:break-all">{log_html}</pre>'
        )
        parts.append(section(f"Current Job Log (last {len(log_lines)} lines)", log_body))

    # footer
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts.append(
        f'<div style="text-align:center;padding:24px;font-size:0.75em;color:#9ca3af">'
        f'Rendered {now} &nbsp;·&nbsp; {html.escape(", ".join(source_files))}</div>'
    )

    body = "\n".join(parts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(block_name)} — status</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f8fafc; color: #1e293b; margin: 0; padding-bottom: 40px; }}
  .card {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.08);
           padding: 20px 24px; margin: 16px auto; max-width: 900px; }}
  .card-title {{ font-size: 0.7em; font-weight: 700; letter-spacing: .1em;
                 text-transform: uppercase; color: #94a3b8; margin-bottom: 12px; }}
  code {{ background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 0.88em; }}
  table {{ overflow-x: auto; display: block; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Render status.yaml to status.html")
    parser.add_argument("--out", default="status.html", help="Output HTML path (default: status.html)")
    parser.add_argument("--open", action="store_true", dest="open_browser",
                        help="Open rendered page in default browser")
    args = parser.parse_args()

    root = pathlib.Path(".")
    status_path    = root / "status.yaml"
    meta_path      = root / "meta-info.yaml"
    artifacts_path = root / "artifacts" / "index.yaml"

    if not status_path.exists():
        sys.exit(f"error: status.yaml not found in {root.resolve()}")

    status    = load_yaml(status_path)
    meta      = load_yaml(meta_path)
    artifacts = load_yaml(artifacts_path)

    # log tail: look for the most recent run's log in artifacts
    log_lines = None
    runs = artifacts.get("runs") or []
    if runs:
        latest_log = runs[-1].get("log")
        log_lines = read_log_tail(latest_log)

    source_files = ["status.yaml"]
    if meta_path.exists():
        source_files.append("meta-info.yaml")
    if artifacts_path.exists():
        source_files.append("artifacts/index.yaml")

    out_path = pathlib.Path(args.out)
    out_path.write_text(render(status, meta, artifacts, log_lines, source_files))
    print(f"wrote {out_path.resolve()}")

    if args.open_browser:
        webbrowser.open(out_path.resolve().as_uri())


if __name__ == "__main__":
    main()
