#!/usr/bin/env python3
"""Read terminal-curator progress. Never reads or serves API credentials."""
from __future__ import annotations
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

BLOCK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOCK / "scripts"))
from terminal_curator import load_config, resolve, redact


def snapshot(config_path: Path) -> dict:
    config = load_config(config_path)
    path = BLOCK / "artifacts/state.json"
    state = json.loads(path.read_text()) if path.exists() else {"status": "never-run", "stage": "idle"}
    pool = resolve(config["runtime_info"]["output"]["harbor_tasks_dir"]["path"])
    manifest = pool / "verifiable_tasks.txt"
    tasks = manifest.read_text().splitlines() if manifest.exists() else []
    # Explicit allowlist: no raw exception, prompt, endpoint, config or logs in a dashboard response.
    return {"block": "terminal-curator", "run_id": state.get("run_id"), "status": state.get("status"),
        "stage": state.get("stage"), "updated_at": state.get("updated_at"), "source": state.get("source"),
        "total_candidates": state.get("total_candidates", 0), "verified_tasks": state.get("verified_tasks", []),
        "rejected_count": len(state.get("rejected", {})), "published_task_count": len(tasks),
        "published_tasks": tasks, "token_usage": {key: value for key, value in state.get("token_usage", {}).items()
            if key in {"total_prompt_tokens", "total_completion_tokens", "total_tokens", "call_count"} and type(value) in (int, float)}}


def render(data: dict) -> str:
    esc = lambda value: html.escape(redact(str(value)))
    cards = "".join(f'<article><small>{esc(k)}</small><strong>{esc(data.get(k, "-"))}</strong></article>'
        for k in ("status", "stage", "total_candidates", "published_task_count", "rejected_count"))
    tasks = "".join(f"<li>{esc(t)}</li>" for t in data.get("published_tasks", [])) or "<li>No verified tasks published yet.</li>"
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terminal Curator · LegoFlow</title><style>
body{{margin:0;background:#f5f4ef;color:#202724;font:16px system-ui,sans-serif}}main{{max-width:1080px;margin:auto;padding:48px 24px}}
header{{border-bottom:1px solid #d7dcd5;padding-bottom:24px}}h1{{font-size:36px;margin:8px 0}}p,small{{color:#59665c}}section{{display:flex;gap:12px;flex-wrap:wrap;margin:28px 0}}
article{{background:white;border:1px solid #d7dcd5;border-radius:12px;padding:20px;min-width:145px}}strong{{display:block;font-size:24px;margin-top:12px}}li{{padding:8px 0}}code{{background:#e8ede5;padding:3px 6px}}
</style><main><header><small>LEGOFLOW / TERMINAL TASKS</small><h1>Terminal Curator</h1><p>StackOverflow → generation → Harbor Oracle + NOP → verified tasks</p></header>
<section>{cards}</section><h2>Published tasks</h2><ul>{tasks}</ul><p>Run: <code>{esc(data.get('run_id') or '—')}</code> · Updated: {esc(data.get('updated_at') or '—')}</p>
<p>Validation details and local job paths: artifacts/reports/latest.json. This page refreshes every 10 seconds while served.</p></main><script>if(location.protocol.startsWith('http'))setTimeout(()=>location.reload(),10000)</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=BLOCK / "config.yaml")
    parser.add_argument("--html", nargs="?", const=str(BLOCK / "dashboard/site/index.html"))
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8086)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    config_path = resolve(str(args.config))
    if args.html:
        destination = Path(args.html); destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render(snapshot(config_path)))
        print(f"Wrote {destination}")
    elif not args.serve:
        print(json.dumps(snapshot(config_path), ensure_ascii=False, indent=2))
    if args.serve:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/", "/index.html", "/api/status"):
                    self.send_error(404); return
                data = snapshot(config_path)
                raw = json.dumps(data).encode() if self.path == "/api/status" else render(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if self.path == "/api/status" else "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(raw)
        print(f"Terminal Curator dashboard: http://{args.host}:{args.port}", flush=True)
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
