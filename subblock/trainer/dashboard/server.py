"""
Lightweight API server for the LegoFactory-Trainer training dashboard.

Data sources:
  1. Local run directories under saves/<run>/ — parses HuggingFace Trainer
     output: trainer_log.jsonl (live, per logging step) merged with
     trainer_state.json (log_history, includes eval) and the *_results.json
     summaries.
  2. Console logs under logs/ (train_<ts>_node*.log) — raw log viewer.
  3. wandb API — proxied to avoid exposing API keys in the browser.

Usage:
  python server.py                          # auto-detect ../artifacts/model and ../artifacts/logs
  python server.py --save-dir /path/to/saves
  python server.py --wandb-entity X --wandb-project Y  # enable wandb
  python server.py --static-dir dist        # serve built frontend
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Any
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs
import threading

# ---------------------------------------------------------------------------
# Trainer-log parser
# ---------------------------------------------------------------------------

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Keys we surface from a raw HF log entry, mapped to a normalized name.
# (LLaMA-Factory's own callback uses current_steps/lr; HF Trainer's
#  log_history uses step/learning_rate — we accept both.)
_KEY_ALIASES = {
    "learning_rate": "lr",
    "lr": "lr",
    "loss": "loss",
    "eval_loss": "eval_loss",
    "grad_norm": "grad_norm",
    "epoch": "epoch",
    "percentage": "percentage",
    "total_steps": "total_steps",
}

_NUMERIC_PASSTHROUGH = ("loss", "eval_loss", "lr", "grad_norm", "epoch",
                        "percentage", "total_steps")


def _parse_hf_duration(raw: Any) -> float | None:
    """Parse HF Trainer duration strings into seconds.

    Handles "0:01:15", "1:02:03", "2 days, 1:55:46", and "1 day, 0:00:01".
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    days = 0.0
    if "day" in s:
        m = re.match(r"\s*(\d+)\s*days?,?\s*(.*)$", s)
        if m:
            days = float(m.group(1))
            s = m.group(2).strip()
    parts = s.split(":")
    try:
        parts = [float(p) for p in parts if p != ""]
    except ValueError:
        return None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return days * 86400 + secs


def _normalize_entry(entry: dict) -> dict | None:
    """Convert a raw HF/LLaMA-Factory log dict into a normalized point.

    Returns None if no step can be determined.
    """
    step = entry.get("current_steps", entry.get("step", entry.get("global_step")))
    if step is None:
        return None
    point: dict[str, Any] = {"step": int(step)}
    for src, dst in _KEY_ALIASES.items():
        if src in entry and isinstance(entry[src], (int, float)):
            v = entry[src]
            if v == v:  # filter NaN
                point[dst] = float(v)
    es = _parse_hf_duration(entry.get("elapsed_time"))
    if es is not None:
        point["elapsed_sec"] = es
    rs = _parse_hf_duration(entry.get("remaining_time"))
    if rs is not None:
        point["remaining_sec"] = rs
    return point


def _merge_point(by_step: dict[int, dict], point: dict) -> None:
    step = point["step"]
    if step in by_step:
        by_step[step].update({k: v for k, v in point.items() if k != "step"})
    else:
        by_step[step] = point


def _add_step_time(points: list[dict]) -> None:
    """Derive a per-step wall-clock time curve from elapsed_sec deltas."""
    prev = None
    for p in points:
        es = p.get("elapsed_sec")
        if es is not None and prev is not None:
            d_step = p["step"] - prev[0]
            d_time = es - prev[1]
            if d_step > 0 and d_time >= 0:
                p["step_time_sec"] = d_time / d_step
        if es is not None:
            prev = (p["step"], es)


def parse_trainer_log(run_dir: str) -> list[dict[str, Any]]:
    """Build a normalized metric timeline for a run directory.

    Prefers the live trainer_log.jsonl, then merges any eval points and
    extra keys found in trainer_state.json's log_history.
    """
    by_step: dict[int, dict] = {}

    jsonl = os.path.join(run_dir, "trainer_log.jsonl")
    if os.path.isfile(jsonl):
        with open(jsonl, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pt = _normalize_entry(entry)
                if pt:
                    _merge_point(by_step, pt)

    state = os.path.join(run_dir, "trainer_state.json")
    if os.path.isfile(state):
        try:
            with open(state, "r", errors="replace") as f:
                sdata = json.load(f)
            for entry in sdata.get("log_history", []):
                pt = _normalize_entry(entry)
                if pt:
                    _merge_point(by_step, pt)
        except (json.JSONDecodeError, OSError):
            pass

    points = [by_step[s] for s in sorted(by_step)]
    _add_step_time(points)
    return points


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", errors="replace") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def run_summary(run_dir: str) -> dict[str, Any]:
    """Final-summary scalars from all_results.json / train_results.json."""
    summary: dict[str, Any] = {}
    for name in ("train_results.json", "all_results.json"):
        summary.update(_read_json(os.path.join(run_dir, name)))
    return summary


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------

RUN_MARKERS = ("trainer_log.jsonl", "trainer_state.json")


def _is_run_dir(path: str) -> bool:
    return any(os.path.isfile(os.path.join(path, m)) for m in RUN_MARKERS)


def _run_state(run_dir: str) -> str:
    if os.path.isfile(os.path.join(run_dir, "all_results.json")):
        return "finished"
    jsonl = os.path.join(run_dir, "trainer_log.jsonl")
    if os.path.isfile(jsonl):
        age = time.time() - os.path.getmtime(jsonl)
        # peek last line for completion
        try:
            with open(jsonl, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 4096))
                last = f.read().decode("utf-8", "replace").strip().splitlines()
            if last:
                pct = json.loads(last[-1]).get("percentage", 0)
                if pct and pct >= 100:
                    return "finished"
        except Exception:
            pass
        return "running" if age < 180 else "unknown"
    return "unknown"


def discover_runs(save_dirs: list[str]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in save_dirs:
        if not base or not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            run_dir = os.path.join(base, name)
            if not os.path.isdir(run_dir) or not _is_run_dir(run_dir):
                continue
            if name in seen:
                continue
            seen.add(name)
            runs.append(
                {
                    "id": name,
                    "name": name,
                    "state": _run_state(run_dir),
                    "created_at": datetime.fromtimestamp(
                        os.path.getctime(run_dir), tz=timezone.utc
                    ).isoformat(),
                    "source": "log",
                    "path": run_dir,
                }
            )
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    return runs


# ---------------------------------------------------------------------------
# Console log discovery (raw log viewer)
# ---------------------------------------------------------------------------


def list_console_logs(log_dirs: list[str]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for d in log_dirs:
        if not d or not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "train_*.log")):
            files.append(
                {
                    "name": os.path.basename(path),
                    "path": path,
                    "mtime": os.path.getmtime(path),
                    "size": os.path.getsize(path),
                }
            )
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


def _latest_console_log(log_dirs: list[str], prefer_node0: bool = True) -> str | None:
    files = list_console_logs(log_dirs)
    if not files:
        return None
    if prefer_node0:
        node0 = [f for f in files if "node0" in f["name"]]
        if node0:
            return node0[0]["path"]
    return files[0]["path"]


_LOG_NAME_RE = re.compile(r"train_(\d{8})_(\d{6})_node0")


def _log_for_run(run_dir: str, log_dirs: list[str]) -> str | None:
    """Match a run directory to its train_*_node0.log via timestamp.

    Anchor on trainer_log.jsonl's mtime — that's the last moment the
    *training process* wrote to the run, and the console log for that
    same process will have been written to at roughly the same time.

    Avoid run_dir ctime: it gets bumped any time we add/remove a file
    inside the dir post-training (writing all_results.json, deleting
    checkpoints), which can push it hours after the actual training.

    Selection: pick the node0 log whose start time (parsed from filename)
    is ≤ jsonl mtime and whose own mtime is within ~1h of jsonl mtime.
    Tiebreak by minimum |log.mtime − jsonl.mtime|.
    """
    if not run_dir or not os.path.isdir(run_dir):
        return None
    jsonl = os.path.join(run_dir, "trainer_log.jsonl")
    if not os.path.isfile(jsonl):
        return None
    run_end = os.path.getmtime(jsonl)

    best: tuple[float, str] | None = None
    for f in list_console_logs(log_dirs):
        m = _LOG_NAME_RE.search(f["name"])
        if not m:
            continue
        try:
            log_start = datetime.strptime(
                m.group(1) + m.group(2), "%Y%m%d%H%M%S"
            ).timestamp()
        except ValueError:
            continue
        # Log must have started before the training finished (with a
        # small buffer for clock skew between machines).
        if log_start > run_end + 300:
            continue
        # Log's last write should be close to training's last write.
        # 1 h is comfortably wider than typical post-jsonl-close drain
        # (a few seconds) but tight enough to distinguish runs that
        # follow each other an hour+ apart.
        dist = abs(f["mtime"] - run_end)
        if dist > 3600:
            continue
        if best is None or dist < best[0]:
            best = (dist, f["path"])
    return best[1] if best else None


# ---------------------------------------------------------------------------
# wandb proxy
# ---------------------------------------------------------------------------


def wandb_api(entity: str, project: str, api_key: str, path: str):
    url = f"https://api.wandb.ai/api/v1/{entity}/{project}/{path}"
    req = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def wandb_runs(entity: str, project: str, api_key: str) -> list[dict[str, str]]:
    data = wandb_api(entity, project, api_key, "runs?per_page=50")
    runs = []
    for r in data if isinstance(data, list) else data.get("runs", data.get("data", [])):
        runs.append(
            {
                "id": r.get("id", r.get("name", "")),
                "name": r.get("displayName", r.get("name", "")),
                "state": r.get("state", "unknown"),
                "created_at": r.get("createdAt", ""),
                "source": "wandb",
            }
        )
    return runs


def wandb_history(entity: str, project: str, api_key: str, run_id: str) -> list[dict[str, Any]]:
    data = wandb_api(entity, project, api_key, f"runs/{run_id}/history?samples=1500")
    rows = data if isinstance(data, list) else data.get("history", data.get("data", []))
    cleaned = []
    for row in rows:
        point: dict[str, Any] = {}
        for k, v in row.items():
            if k.startswith("_") and k != "_step":
                continue
            if isinstance(v, (int, float)) and v == v:
                point[k.replace("_step", "step")] = v
        if point:
            cleaned.append(point)
    return cleaned


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class MetricsCache:
    def __init__(self, ttl: float = 10.0):
        self._cache: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry and (time.time() - entry[0]) < self._ttl:
                return entry[1]
        return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = (time.time(), value)


_cache = MetricsCache(ttl=10)

# ---------------------------------------------------------------------------
# Analysis report generation
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "analysis_prompt.md")


def _load_prompt_template() -> str:
    try:
        with open(PROMPT_TEMPLATE_PATH, "r") as f:
            return f.read()
    except FileNotFoundError:
        return "Analyze the following SFT training metrics and provide recommendations:\n\n{{metrics_summary}}"


def _build_metrics_summary(metrics: list[dict]) -> str:
    if not metrics:
        return "(no metrics)"
    all_keys: set[str] = set()
    for m in metrics:
        all_keys.update(k for k in m if k != "step")

    lines = [f"{'Metric':<24} {'First':>14} {'Last':>14} {'Min':>14} {'Max':>14} {'Trend':>8}"]
    lines.append("-" * 92)
    for key in sorted(all_keys):
        vals = [m[key] for m in metrics if key in m and m[key] is not None]
        if not vals:
            continue
        first, last = vals[0], vals[-1]
        vmin, vmax = min(vals), max(vals)
        if len(vals) >= 2 and first != 0:
            trend = f"{((last - first) / abs(first)) * 100:+.1f}%"
        else:
            trend = "—"
        lines.append(
            f"{key:<24} {first:>14.6g} {last:>14.6g} {vmin:>14.6g} {vmax:>14.6g} {trend:>8}"
        )
    return "\n".join(lines)


def _extract_training_config(run_dir: str | None) -> str:
    if not run_dir or not os.path.isdir(run_dir):
        return "(not available)"
    parts: list[str] = []
    summary = run_summary(run_dir)
    if summary:
        parts.append("## Final summary (all_results.json)")
        parts.append(json.dumps(summary, indent=2, default=str))
    cfg = _read_json(os.path.join(run_dir, "config.json"))
    if cfg:
        keep = {
            k: cfg[k]
            for k in (
                "model_type", "architectures", "hidden_size", "num_hidden_layers",
                "num_attention_heads", "vocab_size", "max_position_embeddings",
                "torch_dtype", "num_experts", "num_experts_per_tok",
            )
            if k in cfg
        }
        if keep:
            parts.append("## Model config (config.json)")
            parts.append(json.dumps(keep, indent=2, default=str))
    return "\n\n".join(parts) if parts else "(config dump not found)"


def _extract_val_results(metrics: list[dict]) -> str:
    evals = [m for m in metrics if "eval_loss" in m]
    if not evals:
        return "(no evaluation results found)"
    lines = []
    for m in evals:
        lines.append(f"Step {m.get('step', '?')}: eval_loss={m['eval_loss']:.6g}, epoch={m.get('epoch', '?')}")
    return "\n".join(lines)


def _build_analysis_prompt(run_id, metrics, run_dir, custom_prompt=""):
    template = _load_prompt_template()
    summary = _build_metrics_summary(metrics)
    config = _extract_training_config(run_dir)
    val = _extract_val_results(metrics)
    step_json = json.dumps(metrics[-15:], indent=1, default=str)

    if custom_prompt.strip():
        custom_section = (
            "#### 7. User-Directed Analysis\n\n"
            "The user has requested focused analysis on the following directions. "
            "Provide a dedicated section addressing each point with specific metric "
            "evidence and actionable recommendations.\n\n"
            f"**User directions:**\n\n{custom_prompt.strip()}\n"
        )
    else:
        custom_section = ""

    prompt = template
    prompt = prompt.replace("{{run_id}}", run_id)
    prompt = prompt.replace("{{num_steps}}", str(len(metrics)))
    prompt = prompt.replace("{{metrics_summary}}", summary)
    prompt = prompt.replace("{{training_config}}", config)
    prompt = prompt.replace("{{val_results}}", val)
    prompt = prompt.replace("{{step_data}}", step_json[:30000])
    prompt = prompt.replace("{{custom_directions}}", custom_section)
    return prompt


def call_llm_api(api_key: str, base_url: str, model: str, prompt: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16000,
            "temperature": 0.3,
        }
    ).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    req = Request(url, data=payload, headers=headers, method="POST")
    with urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    choices = data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class DashboardHandler(SimpleHTTPRequestHandler):
    save_dirs: list[str] = []
    log_dirs: list[str] = []
    static_dir: str = ""
    wandb_entity: str = ""
    wandb_project: str = ""
    wandb_api_key: str = ""
    _run_dir_cache: dict[str, str] = {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/analysis/generate":
            return self._handle_analysis_generate()
        self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/config":
            has_runs = any(self.save_dirs)
            return self._json(
                {
                    "save_dirs": self.save_dirs,
                    "log_dirs": self.log_dirs,
                    "wandb_entity": self.wandb_entity,
                    "wandb_project": self.wandb_project,
                    "data_source": "both"
                    if self.wandb_api_key and has_runs
                    else ("wandb" if self.wandb_api_key else "log"),
                }
            )

        if path == "/api/runs":
            return self._handle_runs()
        if path == "/api/log-files":
            return self._json(
                [
                    {"name": f["name"], "size": f["size"]}
                    for f in list_console_logs(self.log_dirs)
                ]
            )
        if path == "/api/analysis/prompt":
            return self._json({"template": _load_prompt_template()})
        if path == "/api/analysis/demo-reports":
            return self._json([])

        m = re.match(r"^/api/runs/([^/]+)/metrics$", path)
        if m:
            return self._handle_metrics(m.group(1), qs)
        m = re.match(r"^/api/runs/([^/]+)/latest$", path)
        if m:
            return self._handle_latest(m.group(1))
        m = re.match(r"^/api/runs/([^/]+)/logs$", path)
        if m:
            return self._handle_logs(m.group(1), qs)
        m = re.match(r"^/api/runs/([^/]+)/keys$", path)
        if m:
            return self._handle_keys(m.group(1))
        m = re.match(r"^/api/runs/([^/]+)/config$", path)
        if m:
            return self._handle_run_config(m.group(1))
        m = re.match(r"^/api/runs/([^/]+)/analysis-context$", path)
        if m:
            return self._handle_analysis_context(m.group(1))

        if self.static_dir:
            self._serve_static(path)
        else:
            self.send_error(404)

    def _json(self, data: Any, status: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _resolve_run_dir(self, run_id: str) -> str | None:
        cached = self._run_dir_cache.get(run_id)
        if cached and os.path.isdir(cached):
            return cached
        for d in self.save_dirs:
            if not d:
                continue
            candidate = os.path.join(d, run_id)
            if os.path.isdir(candidate) and _is_run_dir(candidate):
                self._run_dir_cache[run_id] = candidate
                return candidate
        return None

    def _handle_runs(self):
        runs: list[dict] = list(discover_runs(self.save_dirs))
        if self.wandb_api_key and self.wandb_entity and self.wandb_project:
            try:
                runs.extend(wandb_runs(self.wandb_entity, self.wandb_project, self.wandb_api_key))
            except Exception:
                pass
        for r in runs:
            r.pop("path", None)
        self._json(runs)

    def _load_metrics(self, run_id: str) -> list[dict]:
        run_dir = self._resolve_run_dir(run_id)
        if run_dir:
            return parse_trainer_log(run_dir)
        if self.wandb_api_key and self.wandb_entity and self.wandb_project:
            try:
                return wandb_history(self.wandb_entity, self.wandb_project, self.wandb_api_key, run_id)
            except Exception:
                return []
        return []

    def _handle_metrics(self, run_id: str, qs: dict):
        cache_key = f"metrics:{run_id}"
        metrics = _cache.get(cache_key)
        if metrics is None:
            metrics = self._load_metrics(run_id)
            _cache.put(cache_key, metrics)

        if not metrics:
            return self._json({"error": "run not found"}, 404)

        all_keys: set[str] = set()
        for p in metrics:
            all_keys.update(k for k in p if k != "step")

        key_filter = qs.get("keys", [None])[0]
        if key_filter:
            wanted = set(key_filter.split(","))
            metrics = [
                {k: v for k, v in p.items() if k == "step" or k in wanted}
                for p in metrics
            ]

        run_info = next((r for r in discover_runs(self.save_dirs) if r["id"] == run_id), None)
        if not run_info:
            run_info = {"id": run_id, "name": run_id, "state": "unknown", "created_at": "", "source": "log"}
        run_info.pop("path", None)

        self._json({"run": run_info, "metrics": metrics, "available_keys": sorted(all_keys)})

    def _handle_latest(self, run_id: str):
        metrics = self._load_metrics(run_id)
        self._json(metrics[-1] if metrics else None)

    def _handle_keys(self, run_id: str):
        metrics = self._load_metrics(run_id)
        all_keys: set[str] = set()
        for p in metrics:
            all_keys.update(k for k in p if k != "step")
        self._json(sorted(all_keys))

    def _handle_run_config(self, run_id: str):
        run_dir = self._resolve_run_dir(run_id)
        if not run_dir:
            return self._json({"error": "run not found"}, 404)
        self._json(
            {
                "run_id": run_id,
                "summary": run_summary(run_dir),
                "model_config": _read_json(os.path.join(run_dir, "config.json")),
            }
        )

    def _handle_logs(self, run_id: str, qs: dict):
        name = qs.get("file", [None])[0]
        path = None
        if name:
            for f in list_console_logs(self.log_dirs):
                if f["name"] == name:
                    path = f["path"]
                    break
        if not path:
            run_dir = self._resolve_run_dir(run_id)
            if run_dir:
                path = _log_for_run(run_dir, self.log_dirs)
        if not path:
            path = _latest_console_log(self.log_dirs)
        if not path:
            return self._json({"error": "no log files found"}, 404)
        try:
            tail = min(int(qs.get("tail", ["500"])[0]), 2000)
            offset = int(qs.get("offset", ["0"])[0])
        except (ValueError, IndexError):
            tail, offset = 500, 0
        with open(path, "r", errors="replace") as f:
            all_lines = f.readlines()
        total = len(all_lines)
        if offset == 0:
            start = max(0, total - tail)
            lines = [l.rstrip("\n") for l in all_lines[start:]]
            self._json({"lines": lines, "total_lines": total, "offset": start, "file": os.path.basename(path)})
        else:
            lines = [l.rstrip("\n") for l in all_lines[offset:]]
            self._json({"lines": lines, "total_lines": total, "offset": offset, "file": os.path.basename(path)})

    def _handle_analysis_context(self, run_id: str):
        metrics = self._load_metrics(run_id)
        if not metrics:
            return self._json({"error": "run not found"}, 404)
        prompt = _build_analysis_prompt(run_id, metrics, self._resolve_run_dir(run_id))
        self._json({"run_id": run_id, "prompt": prompt, "num_steps": len(metrics)})

    def _handle_analysis_generate(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        run_id = body.get("run_id", "")
        api_key = body.get("api_key", "")
        base_url = body.get("base_url", "https://api.openai.com/v1")
        model = body.get("model", "gpt-4o")
        custom_prompt = body.get("custom_prompt", "")

        if not run_id:
            return self._json({"error": "run_id is required"}, 400)
        if not api_key:
            return self._json({"error": "api_key is required"}, 400)

        metrics = self._load_metrics(run_id)
        if not metrics:
            return self._json({"error": "run not found"}, 404)

        prompt = _build_analysis_prompt(run_id, metrics, self._resolve_run_dir(run_id), custom_prompt)
        try:
            report = call_llm_api(api_key, base_url, model, prompt)
            self._json({"report": report, "run_id": run_id, "model": model})
        except Exception as e:
            self._json({"error": f"LLM API call failed: {e}"}, 502)

    def _serve_static(self, path: str):
        if path == "/":
            path = "/index.html"
        fpath = os.path.join(self.static_dir, path.lstrip("/"))
        if not os.path.isfile(fpath):
            fpath = os.path.join(self.static_dir, "index.html")
        try:
            with open(fpath, "rb") as f:
                data = f.read()
            ext = os.path.splitext(fpath)[1]
            ct = {
                ".html": "text/html",
                ".js": "application/javascript",
                ".css": "text/css",
                ".json": "application/json",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
            }.get(ext, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

    def log_message(self, format, *args):
        pass  # silence per-request logs


def main():
    p = argparse.ArgumentParser(description="LegoFactory-Trainer Training Dashboard Server")
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--save-dir", default="", help="Directory containing saves/<run>/ output dirs")
    p.add_argument("--extra-save-dir", action="append", default=[], help="Additional saves dirs (repeatable)")
    p.add_argument("--log-dir", default="", help="Directory with train_*.log console logs")
    p.add_argument("--static-dir", default="")
    p.add_argument("--wandb-entity", default="")
    p.add_argument("--wandb-project", default="")
    p.add_argument("--wandb-api-key", default="")
    args = p.parse_args()

    here = os.path.dirname(__file__)

    def _default(rel: str) -> str:
        cand = os.path.join(here, "..", rel)
        return os.path.abspath(cand) if os.path.isdir(cand) else ""

    # Trainer block layout: runs land in artifacts/model/<run>/, logs in artifacts/logs/.
    save_dir = args.save_dir or _default("artifacts/model")
    log_dir = args.log_dir or _default("artifacts/logs")
    static_dir = args.static_dir or _default_local(here, "dist")

    save_dirs = [os.path.abspath(save_dir)] if save_dir else []
    for extra in args.extra_save_dir:
        d = os.path.abspath(extra)
        if os.path.isdir(d) and d not in save_dirs:
            save_dirs.append(d)

    DashboardHandler.save_dirs = save_dirs
    DashboardHandler.log_dirs = [os.path.abspath(log_dir)] if log_dir else []
    DashboardHandler.static_dir = static_dir
    DashboardHandler.wandb_entity = args.wandb_entity or os.environ.get("WANDB_ENTITY", "")
    DashboardHandler.wandb_project = args.wandb_project or os.environ.get("WANDB_PROJECT", "llama-factory")
    DashboardHandler.wandb_api_key = args.wandb_api_key or os.environ.get("WANDB_API_KEY", "")

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True

    srv = ReusableHTTPServer((args.host, args.port), DashboardHandler)
    print(f"LegoFactory-Trainer Dashboard on http://{args.host}:{args.port}")
    print(f"  Save dirs:  {save_dirs or '(none)'}")
    print(f"  Log dirs:   {DashboardHandler.log_dirs or '(none)'}")
    print(f"  Static dir: {static_dir or '(none — API only, use vite dev for frontend)'}")
    print(f"  wandb:      {'enabled' if DashboardHandler.wandb_api_key else 'disabled'}")
    srv.serve_forever()


def _default_local(here: str, rel: str) -> str:
    cand = os.path.join(here, rel)
    return os.path.abspath(cand) if os.path.isdir(cand) else ""


if __name__ == "__main__":
    main()
