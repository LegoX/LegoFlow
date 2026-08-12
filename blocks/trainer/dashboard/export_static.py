#!/usr/bin/env python3
"""Snapshot the trainer dashboard into a directory of static files.

`server.py` answers every request by reading `artifacts/model/<run>/` off the
local disk. Cloudflare Pages has no such disk, so a live server cannot be
published there. This script writes the same payloads to files, laid out at the
paths the frontend asks for, so the built bundle runs unchanged against a static
host.

    python3 export_static.py --output-dir site
    python3 export_static.py --output-dir site --save-dir ../artifacts/model

Layout (mirrors what `src/api/staticMode.ts` requests):

    site/index.html                       bundle, with the static flag injected
    site/api/config.json
    site/api/runs.json
    site/api/log-files.json
    site/api/analysis/prompt.json
    site/api/analysis/demo-reports.json
    site/api/runs/<run>/metrics.json      full series, every key
    site/api/runs/<run>/latest.json
    site/api/runs/<run>/keys.json
    site/api/runs/<run>/config.json
    site/api/runs/<run>/logs.json         bounded tail, see --log-tail

The `.json` suffix is not cosmetic: a directory cannot also be a file, and the
frontend needs both `api/runs` (the list) and `api/runs/<id>/` (per-run data).

Payloads come from importing `server.py` and calling the same functions the
request handlers call, so the two modes cannot drift apart.

Deliberately not exported:
  * POST /api/analysis/generate — calls an LLM at request time; the frontend
    disables the button in static mode.
  * the wandb proxy — it exists to keep the API key server-side. Baking wandb
    history into public files would leak the runs it was meant to protect; a
    published snapshot shows log-derived metrics only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402  (path is set up above)

STATIC_FLAG = '<script>window.__TRAINER_STATIC__=true;</script>'


def log(msg: str) -> None:
    print(f"INFO: {msg}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, default=str)


def copy_bundle(dist_dir: Path, out_dir: Path) -> None:
    """Copy the built frontend, then mark index.html as a static export.

    Everything under api/ is regenerated below, so it is dropped first — a run
    left over from a previous export would otherwise resurface a deleted run.
    """
    if not (dist_dir / "index.html").is_file():
        raise SystemExit(
            f"ERROR: no built frontend at {dist_dir}/index.html — run `npm run build` first"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in out_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    shutil.copytree(dist_dir, out_dir, dirs_exist_ok=True)

    index = out_dir / "index.html"
    html = index.read_text(encoding="utf-8")
    if STATIC_FLAG not in html:
        if "</head>" in html:
            html = html.replace("</head>", f"  {STATIC_FLAG}\n</head>", 1)
        else:
            html = STATIC_FLAG + html
        index.write_text(html, encoding="utf-8")
    log(f"copied bundle from {dist_dir} and injected the static flag")


def safe_segment(run_id: str) -> str | None:
    """Reject run ids that would escape the output dir when used as a path."""
    if not run_id or run_id in (".", "..") or "/" in run_id or "\\" in run_id:
        return None
    return run_id


def export_run(
    out_api: Path,
    run: dict[str, Any],
    run_dir: str,
    log_dirs: list[str],
    log_tail: int,
) -> bool:
    run_id = safe_segment(str(run.get("id", "")))
    if run_id is None:
        print(f"WARN: skipping run with unusable id {run.get('id')!r}")
        return False

    base = out_api / "runs" / run_id
    metrics = server.parse_trainer_log(run_dir)

    all_keys: set[str] = set()
    for point in metrics:
        all_keys.update(k for k in point if k != "step")

    run_info = {k: v for k, v in run.items() if k != "path"}
    write_json(
        base / "metrics.json",
        {"run": run_info, "metrics": metrics, "available_keys": sorted(all_keys)},
    )
    write_json(base / "latest.json", metrics[-1] if metrics else None)
    write_json(base / "keys.json", sorted(all_keys))
    write_json(
        base / "config.json",
        {
            "run_id": run_id,
            "summary": server.run_summary(run_dir),
            "model_config": server._read_json(os.path.join(run_dir, "config.json")),
        },
    )

    # The live endpoint pages through the file by offset; a static host cannot
    # vary on the query string, so publish one bounded tail.
    path = server._log_for_run(run_dir, log_dirs) or server._latest_console_log(log_dirs)
    if path:
        with open(path, "r", errors="replace") as fh:
            lines = fh.readlines()
        total = len(lines)
        start = max(0, total - log_tail)
        write_json(
            base / "logs.json",
            {
                "lines": [l.rstrip("\n") for l in lines[start:]],
                "total_lines": total,
                "offset": start,
                "file": os.path.basename(path),
            },
        )
    else:
        write_json(
            base / "logs.json",
            {"lines": [], "total_lines": 0, "offset": 0, "file": ""},
        )

    log(f"exported run {run_id}: {len(metrics)} points, {len(all_keys)} metric keys")
    return True


def main() -> None:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Export the trainer dashboard as static files")
    p.add_argument("--output-dir", default=str(here / "site"))
    p.add_argument("--dist-dir", default=str(here / "dist"))
    p.add_argument("--save-dir", default=str(here.parent / "artifacts" / "model"))
    p.add_argument(
        "--extra-save-dir", action="append", default=[], help="Additional runs dirs (repeatable)"
    )
    p.add_argument("--log-dir", default=str(here.parent / "artifacts" / "logs"))
    p.add_argument(
        "--log-tail",
        type=int,
        default=2000,
        help="Console log lines to publish per run (default 2000, matching the live cap)",
    )
    args = p.parse_args()

    out_dir = Path(args.output_dir).resolve()
    out_api = out_dir / "api"

    save_dirs = [os.path.abspath(args.save_dir)] if os.path.isdir(args.save_dir) else []
    for extra in args.extra_save_dir:
        d = os.path.abspath(extra)
        if os.path.isdir(d) and d not in save_dirs:
            save_dirs.append(d)
    log_dirs = [os.path.abspath(args.log_dir)] if os.path.isdir(args.log_dir) else []

    copy_bundle(Path(args.dist_dir).resolve(), out_dir)

    runs = list(server.discover_runs(save_dirs))
    for r in runs:
        r.setdefault("path", "")

    write_json(
        out_api / "config.json",
        {
            "save_dirs": save_dirs,
            "log_dirs": log_dirs,
            # wandb is never proxied from a published snapshot (see module docstring).
            "wandb_entity": "",
            "wandb_project": "",
            "data_source": "log",
        },
    )
    write_json(
        out_api / "log-files.json",
        [{"name": f["name"], "size": f["size"]} for f in server.list_console_logs(log_dirs)],
    )
    write_json(out_api / "analysis" / "prompt.json", {"template": server._load_prompt_template()})
    write_json(out_api / "analysis" / "demo-reports.json", [])

    exported = 0
    for run in runs:
        run_dir = run.get("path") or ""
        if not run_dir or not os.path.isdir(run_dir):
            continue
        if export_run(out_api, run, run_dir, log_dirs, args.log_tail):
            exported += 1

    write_json(out_api / "runs.json", [{k: v for k, v in r.items() if k != "path"} for r in runs])

    log(f"exported {exported} run(s) to {out_dir}")
    if not exported:
        print(
            "WARN: no runs found — the published board will be empty. "
            f"Checked: {save_dirs or '(no runs dir)'}"
        )


if __name__ == "__main__":
    main()
