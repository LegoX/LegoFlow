#!/usr/bin/env -S uv run --no-project --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Local HTML dashboard for the trajgen subblock.

Scans:
  * artifacts/jobs/<job>/result.json   - Harbor rollout summary (only jobs
    that actually have a result.json are shown).
  * artifacts/sft_data/<job>/lf.stats.json - swe_data_process LF conversion
    statistics produced by scripts/convert_trajectories.sh.

Renders a single self-contained HTML file at dashboard/site/index.html with
two sections (Harbor Jobs, SFT Datasets) plus a small KPI strip. Modeled
after dashboard/swegen/progress_monitor_all.py on the swegen branch but
stripped to stdlib-only. The generated site/ can be published to Cloudflare
Pages by dashboard/run_cloudflare_pages_sync.sh (not done by this script).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

BJT = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).resolve().parent
BLOCK_DIR = SCRIPT_DIR.parent
DEFAULT_JOBS = BLOCK_DIR / "artifacts" / "jobs"
DEFAULT_SFT = BLOCK_DIR / "artifacts" / "sft_data"
DEFAULT_HTML = SCRIPT_DIR / "site" / "index.html"
DEFAULT_CACHE = SCRIPT_DIR / "memory" / ".progress_monitor_cache.json"
CACHE_VERSION = 1

SCAFFOLD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"openhands[-_]sdk", re.I), "openhands_sdk"),
    (re.compile(r"claude[-_]code", re.I), "claude_code"),
    (re.compile(r"open[-_]?code", re.I), "open_code"),
    (re.compile(r"terminus[-_]?2", re.I), "terminus2"),
    (re.compile(r"openhands(?![-_]sdk)", re.I), "openhands"),
]


def now_bjt() -> datetime:
    return datetime.now(tz=BJT)


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M:%S")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:,.1f} {u}" if u != "B" else f"{int(f):,} B"
        f /= 1024
    return f"{n} B"


def fmt_tokens_b(n: int | None) -> str:
    """Format a token count using B (10^9) as the unit. e.g. 63_948_329 -> 0.064B."""
    if n is None:
        return "-"
    b = n / 1_000_000_000
    if b >= 100:
        return f"{b:,.1f}B"
    if b >= 10:
        return f"{b:,.2f}B"
    return f"{b:,.3f}B"


def fmt_num(v: Any, digits: int = 0) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        if digits == 0:
            return f"{v:,.0f}"
        return f"{v:,.{digits}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def fmt_pct(v: Any, digits: int = 2) -> str:
    """Format a 0..1 ratio as a percentage, e.g. 0.064787 -> 6.48%."""
    if not isinstance(v, (int, float)):
        return "-"
    return f"{v * 100:.{digits}f}%"


def derive_scaffold(job_name: str) -> str:
    for pat, name in SCAFFOLD_PATTERNS:
        if pat.search(job_name):
            return name
    return "unknown"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": CACHE_VERSION, "jobs": {}, "sft": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": CACHE_VERSION, "jobs": {}, "sft": {}}
    if data.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "jobs": {}, "sft": {}}
    data.setdefault("jobs", {})
    data.setdefault("sft", {})
    return data


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(cache, ensure_ascii=False, indent=2))


def stat_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (int(st.st_mtime_ns), st.st_size)


def parse_job_result(job_dir: Path) -> dict[str, Any] | None:
    result_file = job_dir / "result.json"
    if not result_file.is_file():
        return None
    try:
        data = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARN: failed to parse {result_file}: {exc}", file=sys.stderr)
        return None

    stats = data.get("stats") or {}
    evals_raw = stats.get("evals") or {}
    evals: list[dict[str, Any]] = []
    for ek, ev in evals_raw.items():
        if not isinstance(ev, dict):
            continue
        metrics = ev.get("metrics") or []
        mean = None
        if metrics and isinstance(metrics[0], dict):
            mean = metrics[0].get("mean")
        reward_stats = ((ev.get("reward_stats") or {}).get("reward") or {}) if isinstance(ev.get("reward_stats"), dict) else {}
        reward_1 = reward_stats.get("1.0") or reward_stats.get(1.0) or []
        reward_0 = reward_stats.get("0.0") or reward_stats.get(0.0) or []
        exc_stats = ev.get("exception_stats") or {}
        evals.append({
            "name": ek,
            "n_trials": ev.get("n_trials"),
            "n_errors": ev.get("n_errors"),
            "mean": mean,
            "reward_1_count": len(reward_1) if isinstance(reward_1, list) else None,
            "reward_0_count": len(reward_0) if isinstance(reward_0, list) else None,
            "exception_summary": {k: (len(v) if isinstance(v, list) else v) for k, v in exc_stats.items()},
        })

    primary_mean = evals[0]["mean"] if evals else None
    primary_reward_1 = evals[0]["reward_1_count"] if evals else None
    return {
        "id": data.get("id"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
        "n_total_trials": data.get("n_total_trials"),
        "n_trials": stats.get("n_trials"),
        "n_errors": stats.get("n_errors"),
        "evals": evals,
        "primary_mean": primary_mean,
        "primary_reward_1_count": primary_reward_1,
    }


def collect_jobs(jobs_dir: Path, cache: dict[str, Any]) -> list[dict[str, Any]]:
    if not jobs_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    jobs_cache: dict[str, Any] = cache["jobs"]
    seen: set[str] = set()
    for entry in sorted(jobs_dir.iterdir()):
        if not entry.is_dir():
            continue
        result_file = entry / "result.json"
        sig = stat_sig(result_file)
        if sig is None:
            continue
        seen.add(entry.name)
        key = entry.name
        cached = jobs_cache.get(key)
        if cached and tuple(cached.get("sig") or ()) == sig:
            parsed = cached["parsed"]
        else:
            parsed = parse_job_result(entry)
            if parsed is None:
                continue
            jobs_cache[key] = {"sig": list(sig), "parsed": parsed}
        out.append({"job": key, "scaffold": derive_scaffold(key), **parsed})
    for stale in list(jobs_cache.keys()):
        if stale not in seen:
            jobs_cache.pop(stale, None)
    return out


def collect_sft(sft_dir: Path, cache: dict[str, Any]) -> list[dict[str, Any]]:
    if not sft_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    sft_cache: dict[str, Any] = cache["sft"]
    seen: set[str] = set()
    for entry in sorted(sft_dir.iterdir()):
        if not entry.is_dir():
            continue
        stats_file = entry / "lf.stats.json"
        sig = stat_sig(stats_file)
        if sig is None:
            continue
        seen.add(entry.name)
        im_file = entry / "im.jsonl"
        lf_file = entry / "lf.json"
        im_size = im_file.stat().st_size if im_file.is_file() else None
        lf_size = lf_file.stat().st_size if lf_file.is_file() else None
        cache_key = entry.name
        cached = sft_cache.get(cache_key)
        if cached and tuple(cached.get("sig") or ()) == sig:
            stats = cached["stats"]
        else:
            try:
                stats = json.loads(stats_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"WARN: failed to parse {stats_file}: {exc}", file=sys.stderr)
                continue
            sft_cache[cache_key] = {"sig": list(sig), "stats": stats}
        out.append({
            "job": cache_key,
            "scaffold": derive_scaffold(cache_key),
            "count": stats.get("count"),
            "token_lens": stats.get("token_lens") or {},
            "n_turns": stats.get("n_turns") or {},
            "scores": stats.get("scores") or {},
            "total_tokens": stats.get("total_tokens"),
            "tool_call_errors": stats.get("tool_call_errors") or {},
            "im_size": im_size,
            "lf_size": lf_size,
        })
    for stale in list(sft_cache.keys()):
        if stale not in seen:
            sft_cache.pop(stale, None)
    return out


def compute_totals(jobs: list[dict[str, Any]], sft: list[dict[str, Any]]) -> dict[str, Any]:
    n_jobs = len(jobs)
    n_done = sum(1 for j in jobs if j.get("finished_at"))
    n_running = n_jobs - n_done
    trials_done = sum((j.get("n_trials") or 0) for j in jobs)
    trials_total = sum((j.get("n_total_trials") or 0) for j in jobs)
    sft_count = len(sft)
    sft_records = sum((s.get("count") or 0) for s in sft)
    sft_total_tokens = sum((s.get("total_tokens") or 0) for s in sft)
    return {
        "n_jobs": n_jobs,
        "n_done": n_done,
        "n_running": n_running,
        "trials_done": trials_done,
        "trials_total": trials_total,
        "sft_count": sft_count,
        "sft_records": sft_records,
        "sft_total_tokens": sft_total_tokens,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

CSS = """
:root {
  --bg: #f5f7fb;
  --panel: #ffffff;
  --text: #1f2937;
  --muted: #667085;
  --line: #e5e7eb;
  --blue: #2563eb;
  --green: #16a34a;
  --amber: #d97706;
  --red: #dc2626;
  --purple: #7c3aed;
  --shadow: 0 12px 30px rgba(15, 23, 42, .08);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: var(--bg); color: var(--text); font-size: 16px; line-height: 1.5; }
header { padding: 28px 36px 22px; background: linear-gradient(135deg, #172554, #1d4ed8 48%, #0891b2); color: white; }
header h1 { margin: 0 0 8px; font-size: 32px; letter-spacing: -.02em; }
header p { margin: 4px 0; color: rgba(255,255,255,.86); font-size: 15px; }
header code { background: rgba(255,255,255,.16); padding: 2px 6px; border-radius: 5px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
main { padding: 24px 36px 56px; max-width: 1780px; margin: 0 auto; }
.grid { display: grid; gap: 16px; }
.kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 22px; }
.card, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; box-shadow: var(--shadow); }
.card { padding: 20px; }
.card .label { color: var(--muted); font-size: 14px; }
.card .value { font-size: 32px; font-weight: 700; margin-top: 8px; }
.card .sub { color: var(--muted); margin-top: 6px; font-size: 13px; }
.panel { padding: 22px; margin-top: 22px; overflow: hidden; }
.panel h2 { margin: 0 0 16px; font-size: 22px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--muted); font-weight: 650; background: #f8fafc; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: middle; }
tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.job { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; max-width: 460px; word-break: break-all; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 650; }
.badge.running { background: #fef3c7; color: #92400e; }
.badge.done { background: #dcfce7; color: #166534; }
.badge.scaffold { background: #ede9fe; color: #5b21b6; }
.bar { position: relative; height: 18px; min-width: 140px; background: #e8eefc; border-radius: 999px; overflow: hidden; }
.bar-fill { position: absolute; inset: 0 auto 0 0; background: linear-gradient(90deg, var(--blue), #06b6d4); border-radius: inherit; }
.bar span { position: relative; z-index: 1; display: block; line-height: 18px; text-align: center; font-size: 12px; color: #0f172a; font-weight: 650; }
.muted { color: var(--muted); }
details { margin: 8px 0 0; }
details > summary { cursor: pointer; color: var(--blue); font-size: 13px; padding: 4px 0; user-select: none; }
.eval-table { margin-top: 8px; }
.eval-table th { background: #fafafa; font-size: 13px; }
.eval-table td { font-size: 13px; }
.footer { color: var(--muted); font-size: 12px; padding: 24px 0 0; text-align: center; }
.empty { color: var(--muted); padding: 24px; text-align: center; font-style: italic; }
"""


def status_badge(finished_at: str | None) -> str:
    if finished_at:
        return '<span class="badge done">done</span>'
    return '<span class="badge running">running</span>'


def progress_bar(done: int | None, total: int | None) -> str:
    if not total:
        return '<span class="muted">-</span>'
    done = done or 0
    pct = max(0.0, min(100.0, 100.0 * done / total))
    return (
        f'<div class="bar"><div class="bar-fill" style="width:{pct:.1f}%"></div>'
        f'<span>{done:,} / {total:,} ({pct:.1f}%)</span></div>'
    )


def render_job_row(job: dict[str, Any]) -> str:
    started = parse_iso(job.get("started_at"))
    finished = parse_iso(job.get("finished_at"))
    if started and finished:
        runtime = (finished - started).total_seconds()
    elif started:
        runtime = (datetime.now(tz=timezone.utc) - started).total_seconds()
    else:
        runtime = None
    mean = job.get("primary_mean")
    mean_str = f"{mean:.4f}" if isinstance(mean, (int, float)) else "-"
    return (
        "<tr>"
        f'<td class="job">{html.escape(job["job"])}'
        f'<div class="muted">id: {html.escape(str(job.get("id") or "-"))}</div></td>'
        f'<td><span class="badge scaffold">{html.escape(job.get("scaffold") or "unknown")}</span></td>'
        f"<td>{status_badge(job.get('finished_at'))}</td>"
        f"<td>{html.escape(fmt_dt(started))}</td>"
        f"<td>{html.escape(fmt_dt(finished))}</td>"
        f'<td class="num">{html.escape(fmt_duration(runtime))}</td>'
        f"<td>{progress_bar(job.get('n_trials'), job.get('n_total_trials'))}</td>"
        f'<td class="num">{fmt_num(job.get("n_errors"))}</td>'
        f'<td class="num">{mean_str}</td>'
        f'<td class="num">{fmt_num(job.get("primary_reward_1_count"))}</td>'
        "</tr>"
    )


def render_sft_row(s: dict[str, Any]) -> str:
    tl = s.get("token_lens") or {}
    nt = s.get("n_turns") or {}
    sc = s.get("scores") or {}
    tce = s.get("tool_call_errors") or {}
    if tce:
        tce_cell = (
            f'{fmt_pct(tce.get("error_rate"))}'
            f'<div class="muted">{fmt_num(tce.get("error_tool_calls"))} / {fmt_num(tce.get("total_tool_calls"))} calls'
            f' &middot; traj: {fmt_pct(tce.get("trajectory_error_rate"))}</div>'
        )
    else:
        tce_cell = '<span class="muted">-</span>'
    return (
        "<tr>"
        f'<td class="job">{html.escape(s["job"])}</td>'
        f'<td><span class="badge scaffold">{html.escape(s.get("scaffold") or "unknown")}</span></td>'
        f'<td class="num">{fmt_num(s.get("count"))}</td>'
        f'<td class="num">{fmt_num(tl.get("min"))} / {fmt_num(tl.get("mean"))} / {fmt_num(tl.get("max"))}'
        f'<div class="muted">gt_128k: {fmt_num(tl.get("gt_128k"))} &middot; total: {fmt_num(s.get("total_tokens"))}</div></td>'
        f'<td class="num">{fmt_num(nt.get("min"))} / {fmt_num(nt.get("mean"))} / {fmt_num(nt.get("max"))}'
        f'<div class="muted">gte_100: {fmt_num(nt.get("gte_100"))}</div></td>'
        f'<td class="num">{fmt_num(sc.get("min"), 4)} / {fmt_num(sc.get("mean"), 4)} / {fmt_num(sc.get("max"), 4)}</td>'
        f'<td class="num">{tce_cell}</td>'
        f'<td class="num">{html.escape(fmt_bytes(s.get("im_size")))}</td>'
        f'<td class="num">{html.escape(fmt_bytes(s.get("lf_size")))}</td>'
        "</tr>"
    )


def render_html(
    jobs: list[dict[str, Any]],
    sft: list[dict[str, Any]],
    totals: dict[str, Any],
    refresh_seconds: int,
    jobs_dir: Path,
    sft_dir: Path,
) -> str:
    jobs_sorted = sorted(jobs, key=lambda j: j.get("started_at") or "", reverse=True)
    sft_sorted = sorted(sft, key=lambda s: s["job"])
    now_str = now_bjt().strftime("%Y-%m-%d %H:%M:%S BJT")

    if jobs_sorted:
        job_rows = "".join(render_job_row(j) for j in jobs_sorted)
        jobs_table = (
            '<div class="table-wrap"><table>'
            "<thead><tr><th>Job</th><th>Scaffold</th><th>Status</th><th>Started (BJT)</th>"
            "<th>Finished (BJT)</th><th class='num'>Runtime</th><th>Progress</th>"
            "<th class='num'>Errors</th><th class='num'>Mean (primary)</th>"
            "<th class='num'>Reward=1 (primary)</th></tr></thead>"
            f"<tbody>{job_rows}</tbody></table></div>"
        )
    else:
        jobs_table = '<div class="empty">No jobs with result.json found under artifacts/jobs/.</div>'

    if sft_sorted:
        sft_rows = "".join(render_sft_row(s) for s in sft_sorted)
        sft_table = (
            '<div class="table-wrap"><table>'
            "<thead><tr><th>Job</th><th>Scaffold</th><th class='num'>Records</th>"
            "<th class='num'>Token len (min/mean/max)</th>"
            "<th class='num'>Turns (min/mean/max)</th>"
            "<th class='num'>Scores (min/mean/max)</th>"
            "<th class='num'>Tool-call errors</th>"
            "<th class='num'>im.jsonl</th><th class='num'>lf.json</th></tr></thead>"
            f"<tbody>{sft_rows}</tbody></table></div>"
        )
    else:
        sft_table = '<div class="empty">No lf.stats.json found under artifacts/sft_data/.</div>'

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>Trajgen Dashboard</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>Trajgen Progress Dashboard</h1>
  <p>Last updated: <strong>{html.escape(now_str)}</strong> &middot; auto-refresh every {refresh_seconds}s</p>
  <p>jobs: <code>{html.escape(str(jobs_dir))}</code></p>
  <p>sft_data: <code>{html.escape(str(sft_dir))}</code></p>
</header>
<main>
  <div class="grid kpis">
    <div class="card"><div class="label">SFT 轨迹数据量</div>
      <div class="value">{totals['sft_records']:,}</div>
      <div class="sub">LF records across {totals['sft_count']:,} dataset(s)</div></div>
    <div class="card"><div class="label">SFT Token 总数</div>
      <div class="value">{fmt_tokens_b(totals['sft_total_tokens'])}</div>
      <div class="sub">{totals['sft_total_tokens']:,} tokens &middot; summed from lf.stats.json</div></div>
    <div class="card"><div class="label">Harbor trials processed</div>
      <div class="value">{totals['trials_done']:,}</div>
      <div class="sub">of {totals['trials_total']:,} planned &middot; {totals['n_jobs']:,} job(s) tracked</div></div>
  </div>

  <section class="panel">
    <h2>Harbor Jobs</h2>
    {jobs_table}
  </section>

  <section class="panel">
    <h2>SFT Datasets</h2>
    {sft_table}
  </section>

  <div class="footer">Generated by dashboard/progress_monitor.py &middot; published to Cloudflare Pages via dashboard/run_cloudflare_pages_sync.sh.</div>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-html", type=Path, default=DEFAULT_HTML)
    p.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOBS)
    p.add_argument("--sft-dir", type=Path, default=DEFAULT_SFT)
    p.add_argument("--refresh", type=int, default=60, help="HTML auto-refresh interval (seconds).")
    p.add_argument("--loop", nargs="?", const=60, type=int, default=None,
                   help="Regenerate on a loop; defaults to 60s when --loop is given without value.")
    p.add_argument("--serve", action="store_true", help="Start a local HTTP server on --host:--port.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="Open the page in a browser after the first write.")
    p.add_argument("--force-full-scan", action="store_true", help="Ignore cache and re-parse every file.")
    return p.parse_args(argv)


def run_once(args: argparse.Namespace, refresh_seconds: int) -> dict[str, Any]:
    cache = {"version": CACHE_VERSION, "jobs": {}, "sft": {}} if args.force_full_scan else load_cache(args.cache_file)
    jobs = collect_jobs(args.jobs_dir.resolve(), cache)
    sft = collect_sft(args.sft_dir.resolve(), cache)
    totals = compute_totals(jobs, sft)
    save_cache(args.cache_file, cache)
    html_doc = render_html(jobs, sft, totals, refresh_seconds, args.jobs_dir.resolve(), args.sft_dir.resolve())
    atomic_write_text(args.output_html, html_doc)
    return totals


def start_http_server(directory: Path, host: str, port: int) -> ThreadingHTTPServer:
    directory.mkdir(parents=True, exist_ok=True)
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    Thread(target=server.serve_forever, name="trajgen-dashboard-http", daemon=True).start()
    return server


def maybe_open_browser(path: Path) -> None:
    try:
        import webbrowser
        webbrowser.open(path.resolve().as_uri())
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    interval = int(args.loop) if args.loop is not None else args.refresh
    server: ThreadingHTTPServer | None = None
    if args.serve:
        server = start_http_server(args.output_html.parent, args.host, args.port)
        print(f"serving {args.output_html.parent} at http://{args.host}:{args.port}/{args.output_html.name}")
    first = True
    try:
        while True:
            totals = run_once(args, args.refresh)
            print(
                f"[{now_bjt().strftime('%Y-%m-%d %H:%M:%S BJT')}] wrote {args.output_html} "
                f"| jobs={totals['n_jobs']} (running={totals['n_running']}, done={totals['n_done']}) "
                f"trials={totals['trials_done']}/{totals['trials_total']} "
                f"sft={totals['sft_count']}"
            )
            if first and args.open:
                maybe_open_browser(args.output_html)
            first = False
            if args.loop is None:
                break
            time.sleep(interval)
        return 0
    finally:
        if server is not None:
            server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
