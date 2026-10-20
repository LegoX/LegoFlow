#!/usr/bin/env python3
"""Curator-style terminal task dashboard with an allowlisted artifact projection."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import sys
import tomllib
import xml.etree.ElementTree as ET

import yaml

BLOCK = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).resolve().parent
sys.path.insert(0, str(BLOCK / "scripts"))
from terminal_curator import redact

MAX_BYTES = 4_000_000
MAX_TASKS = 3000
MAX_BATCHES = 100
MAX_HISTORY = 100
STATUSES = {"never-run", "running", "completed", "failed", "interrupted", "unavailable"}
STAGES = {"idle", "preflight", "check", "collect", "generate", "normalize", "validate-oracle",
          "validate-nop", "publish", "completed", "failed", "interrupted"}
SOURCES = {"stackoverflow", "json", "tasks"}
TOKEN_KEYS = {"total_prompt_tokens", "total_completion_tokens", "total_tokens", "call_count"}


def _enum(value, choices, default=None):
    return value if isinstance(value, str) and value in choices else default


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _sha(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) else None


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def _identifier(value):
    if not isinstance(value, str) or redact(value) != value:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", value) else None


def _ids(value):
    return list(dict.fromkeys(v for item in value if (v := _identifier(item)))) if isinstance(value, list) else []


def _labels(value):
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value
        if isinstance(item, str) and 0 < len(item.strip()) <= 120
        and not any(ord(char) < 32 for char in item) and redact(item) == item))


def _time(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _duration(start, end):
    if not start or not end:
        return None
    try:
        seconds = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        return round(seconds, 1) if seconds >= 0 else None
    except (TypeError, ValueError):
        return None


def _inside(path: Path, root: Path):
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def _read(path: Path, kind="json", root=None):
    """Never expose parse errors, file names, config values, or raw content."""
    try:
        if root is not None and not _inside(path, root):
            return None, "unavailable"
        if not path.exists():
            return None, "missing"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None, "unavailable"
        value = path.read_text(encoding="utf-8")
        if kind == "json":
            value = json.loads(value)
        elif kind == "yaml":
            value = yaml.safe_load(value)
        elif kind == "toml":
            value = tomllib.loads(value)
        return value, "ok"
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return None, "unavailable"


def _hide_config_values(value, config):
    hidden = set()
    def collect(node):
        if not isinstance(node, dict):
            return
        for key, item in node.items():
            if isinstance(item, dict):
                collect(item)
            elif isinstance(item, str) and len(item) >= 4 and item != "local" and (
                any(word in str(key).lower() for word in ("api_key", "token", "password", "secret", "base_url"))
                or key in {"ip", "directory"}
            ):
                hidden.add(item)
                if "base_url" in str(key):
                    try:
                        host = urlsplit(item).hostname
                        if host:
                            hidden.add(host)
                    except ValueError:
                        pass
    collect(config)
    def clean(node):
        if isinstance(node, str):
            node = redact(node)
            for secret in sorted(hidden, key=len, reverse=True):
                node = node.replace(secret, "[REDACTED]")
            return node
        if isinstance(node, list):
            return [clean(item) for item in node]
        if isinstance(node, dict):
            return {key: clean(item) for key, item in node.items()}
        return node
    return clean(value)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else BLOCK / path


def _reason(value):
    """Raw backend exceptions and rejection text stay on the execution host."""
    if not isinstance(value, str):
        return None
    if "expected reward" in value:
        return "reward-mismatch"
    if "trial exception" in value:
        return "trial-exception"
    if "duplicate trial" in value:
        return "duplicate-trial"
    if "missing Harbor trial" in value:
        return "missing-trial"
    if "JUnit" in value or "pytest" in value:
        return "verifier-evidence"
    return "validation-rejected" if value else None


def _trial(entry, expected, task_id, artifacts, checksum=None):
    entry = _mapping(entry)
    passed = entry.get("passed")
    result = {"status": "passed" if passed is True else "failed" if passed is False else "pending",
              "basis": "report" if type(passed) is bool else "none", "expected_reward": expected,
              "reward": None, "tests": None, "failures": None, "errors": None, "skipped": None,
              "reason": _reason(entry.get("reason")), "task_checksum": None, "checksum_matches": None}
    trial_path = entry.get("trial_path")
    if not isinstance(trial_path, str):
        return result
    path = _path(trial_path)
    # Only a run's validation directory can supply detailed verifier evidence.
    if not _inside(path, artifacts / "runs") or "validation" not in path.parts:
        return result
    raw, availability = _read(path / "result.json", root=artifacts)
    if availability != "ok" or not isinstance(raw, dict) or raw.get("task_name") != task_id:
        return result
    reward = _mapping(_mapping(raw.get("verifier_result")).get("rewards")).get("reward")
    result["reward"] = _number(reward)
    result["basis"] = "partial"
    result["task_checksum"] = _sha(raw.get("task_checksum"))
    if checksum and result["task_checksum"]:
        result["checksum_matches"] = checksum == result["task_checksum"]
    junit, availability = _read(path / "verifier/junit.xml", kind="text", root=artifacts)
    try:
        node = ET.fromstring(junit) if availability == "ok" else None
        if node is not None:
            suites = [node] if node.tag == "testsuite" else list(node.iter("testsuite"))
            counts = {key: sum(int(s.get(key, 0)) for s in suites)
                      for key in ("tests", "errors", "failures", "skipped")}
            if suites and all(0 <= value <= 1_000_000_000 for value in counts.values()):
                result.update(counts)
                result["basis"] = "raw"
    except (ET.ParseError, TypeError, ValueError):
        pass
    if result["checksum_matches"] is False:
        result["status"], result["reason"] = "conflict", "checksum-mismatch"
    if passed is True and (raw.get("exception_info") or result["reward"] != expected or
                           (result["basis"] == "raw" and
                            (result["tests"] <= result["skipped"] or result["errors"] or
                             (expected == 1 and result["failures"]) or
                             (expected == 0 and not result["failures"])))):
        result["status"], result["reason"] = "conflict", "evidence-conflict"
    return result


def _manifest(pool, artifacts):
    raw, state = _read(pool / "verifiable_tasks.txt", kind="text", root=artifacts)
    if state != "ok":
        return [], state, 0
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    ids = _ids(lines)
    return ids, state, len(lines) - len(ids)


def _task(task_id, batch_id, pool, report, artifacts, current=False):
    path = pool / task_id
    valid = not path.is_symlink() and _inside(path, pool) and path.is_dir()
    toml, availability = _read(path / "task.toml", kind="toml", root=artifacts) if valid else (None, "missing")
    valid = valid and availability == "ok" and all((path / name).is_file() for name in (
        "instruction.md", "environment/Dockerfile", "solution/solve.sh", "tests/test_outputs.py"))
    provenance, _ = _read(path / "provenance.json", root=artifacts) if valid else (None, "missing")
    provenance, metadata = _mapping(provenance), _mapping(_mapping(toml).get("metadata"))
    validation = _mapping(report.get("validation"))
    checksum = _sha(_mapping(report.get("harbor_task_checksums")).get(task_id))
    difficulty = metadata.get("difficulty")
    tags = _labels(metadata.get("tags"))[:12]
    source = _enum(provenance.get("kind"), SOURCES) or _enum(report.get("source"), SOURCES)
    return {"id": task_id, "batch_id": batch_id, "run_id": _identifier(provenance.get("run_id")) or _identifier(report.get("run_id")),
            "status": ("published" if current else "retained") if valid else "missing",
            "source": _enum(source, SOURCES),
            "difficulty": _enum(difficulty, {"easy", "medium", "hard"}), "tags": tags,
            "question_id": _count(provenance.get("question_id")),
            "harbor_checksum": checksum, "block_checksum": _sha(_mapping(report.get("task_checksums")).get(task_id)),
            "oracle": _trial(_mapping(validation.get("oracle")).get(task_id), 1, task_id, artifacts, checksum),
            "nop": _trial(_mapping(validation.get("nop")).get(task_id), 0, task_id, artifacts, checksum)}


def snapshot(config_path: Path) -> dict:
    """Project measured metadata only; task text, logs, endpoints and paths never leave here."""
    artifacts = BLOCK / "artifacts"
    config, config_state = _read(config_path, kind="yaml")
    config = _mapping(config)
    output = _mapping(_mapping(_mapping(config.get("runtime_info")).get("output")).get("harbor_tasks_dir"))
    if not isinstance(output.get("path"), str) or not output.get("path"):
        config_state = "unavailable" if config_state == "ok" else config_state
    pool = _path(output.get("path")) if isinstance(output.get("path"), str) else artifacts / "tasks"
    if not _inside(pool, artifacts):
        pool, config_state = artifacts / "tasks", "unavailable"
    live, live_state = _read(artifacts / "state.json", root=artifacts)
    latest_location = _mapping(_mapping(_mapping(config.get("runtime_info")).get("output")).get("report")).get("path")
    latest_path = _path(latest_location) if isinstance(latest_location, str) else artifacts / "reports/latest.json"
    latest, latest_state = _read(latest_path, root=artifacts)
    live, latest = _mapping(live), _mapping(latest)
    if live_state == "missing" and latest_state == "ok":
        live = latest
    report = {**latest, **live} if latest.get("run_id") and latest.get("run_id") == live.get("run_id") else live
    current_id = _identifier(report.get("run_id"))
    reports = {current_id: report} if current_id else {}
    index, index_state = _read(artifacts / "index.yaml", kind="yaml", root=artifacts)
    entries = index.get("runs", []) if isinstance(index, dict) else index if isinstance(index, list) else []
    if not isinstance(entries, list):
        entries, index_state = [], "unavailable"
    history = []
    for entry in reversed(entries[-MAX_HISTORY:]):
        entry = _mapping(entry)
        archive_id = _identifier(entry.get("id"))
        if not archive_id:
            continue
        archived, _ = _read(artifacts / "archives" / archive_id / "report.json", root=artifacts)
        archived = _mapping(archived)
        run_id = _identifier(archived.get("run_id"))
        expected_run = _identifier(entry.get("run_id"))
        start, end = _time(entry.get("started_at")), _time(entry.get("completed_at"))
        report_start = _time(archived.get("started_at"))
        stale = bool(expected_run and archived and run_id != expected_run)
        if start and report_start:
            try:
                stale = stale or datetime.fromisoformat(report_start) < datetime.fromisoformat(start)
                if end:
                    stale = stale or datetime.fromisoformat(report_start) > datetime.fromisoformat(end)
            except TypeError:
                stale = True
        if stale:
            archived, run_id = {}, None
        elif not run_id:
            run_id = expected_run
        if run_id and run_id not in reports:
            reports[run_id] = archived
        history.append({"id": archive_id, "run_id": run_id,
                        "status": _enum(entry.get("status"), STATUSES, "unavailable"),
                        "source": _enum(archived.get("source"), SOURCES),
                        "started_at": start, "completed_at": end, "duration_sec": _duration(start, end),
                        "candidates": _count(archived.get("total_candidates")),
                        "verified_count": len(_ids(archived.get("verified_tasks"))) if "verified_tasks" in archived else _count(entry.get("verified_count")) if not stale else None,
                        "rejected_count": len(_mapping(archived.get("rejected"))) if "rejected" in archived else _count(entry.get("rejected_count")) if not stale else None,
                        "exit_code": entry.get("exit_code") if type(entry.get("exit_code")) is int else None})
    # Freeze the publication pointer once: a concurrent publish cannot mix one
    # batch manifest with another batch's metadata. Keep pool for version discovery.
    frozen_pool = pool.resolve() if config_state == "ok" else pool
    manifest_ids, manifest_state, ignored = _manifest(frozen_pool, artifacts) if config_state == "ok" else ([], "unavailable", 0)
    versions = pool.parent / (pool.name + "-versions")
    try:
        dirs = [p for p in versions.iterdir() if p.is_dir() and not p.is_symlink() and _identifier(p.name)] if config_state == "ok" and versions.is_dir() and not versions.is_symlink() and _inside(versions, artifacts) else []
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        dirs = []
    # Current pool is always included, even when an operator uses a custom unversioned source.
    paths = [frozen_pool] if manifest_state == "ok" else []
    paths += [p for p in dirs if p.resolve() != frozen_pool][:MAX_BATCHES - len(paths)]
    batches, tasks, seen = [], [], set()
    for path in paths:
        current = path.resolve() == frozen_pool
        batch_id = _identifier(path.resolve().name) or "current"
        ids = manifest_ids if current else _manifest(path, artifacts)[0]
        rows = []
        for task_id in ids:
            if len(tasks) >= MAX_TASKS:
                break
            provenance, _ = _read(path / task_id / "provenance.json", root=artifacts)
            run_id = _identifier(_mapping(provenance).get("run_id"))
            row = _task(task_id, batch_id, path, reports.get(run_id, {}), artifacts, current)
            rows.append(row)
            tasks.append(row)
            seen.add((row["run_id"], task_id))
        available = sum(row["status"] != "missing" for row in rows)
        batches.append({"id": batch_id, "current": current, "listed_count": len(ids),
                        "available_count": available, "shown_count": len(rows),
                        "run_id": next((row["run_id"] for row in rows if row["run_id"]), None),
                        "source_counts": dict(Counter(row["source"] or "unknown" for row in rows)),
                        "difficulty_counts": dict(Counter(row["difficulty"] or "unknown" for row in rows))})
    validation = _mapping(report.get("validation"))
    rejected = _mapping(report.get("rejected"))
    task_progress = _mapping(report.get("task_progress"))
    latest_ids = set(_ids(report.get("candidate_ids"))) | {v for k in task_progress if (v := _identifier(k))} | set(_ids(report.get("verified_tasks"))) | {v for k in rejected if (v := _identifier(k))}
    for agent in ("oracle", "nop"):
        latest_ids.update(v for k in _mapping(validation.get(agent)) if (v := _identifier(k)))
    for task_id in sorted(latest_ids):
        if (current_id, task_id) in seen or len(tasks) >= MAX_TASKS:
            continue
        checksum = _sha(_mapping(report.get("harbor_task_checksums")).get(task_id))
        oracle = _trial(_mapping(validation.get("oracle")).get(task_id), 1, task_id, artifacts, checksum)
        nop = _trial(_mapping(validation.get("nop")).get(task_id), 0, task_id, artifacts, checksum)
        progress = _mapping(task_progress.get(task_id))
        status = "rejected" if task_id in rejected else "validated" if oracle["status"] == nop["status"] == "passed" else "running" if progress.get("status") == "running" else "pending"
        tasks.append({"id": task_id, "batch_id": None, "run_id": current_id, "status": status,
                      "source": _enum(report.get("source"), SOURCES),
                      "difficulty": None, "tags": [], "question_id": None,
                      "phase": _enum(progress.get("stage"), STAGES), "harbor_checksum": checksum,
                      "block_checksum": _sha(_mapping(report.get("task_checksums")).get(task_id)), "oracle": oracle, "nop": nop})
    published = manifest_ids
    status = _enum(report.get("status"), STATUSES, "never-run" if live_state == latest_state == "missing" else "unavailable")
    stage = _enum(report.get("stage"), STAGES, _enum(status, STAGES, "idle" if status == "never-run" else None))
    source = _enum(report.get("source"), SOURCES)
    token_usage = {key: value for key, value in _mapping(report.get("token_usage")).items()
                   if key in TOKEN_KEYS and _number(value) is not None}
    # Upstream records usage after each successful API response, before a whole
    # question completes. Project counters only while this exact run is active.
    if current_id and status in {"running", "failed", "interrupted"} and source in {"stackoverflow", "json"}:
        measured, measured_state = _read(artifacts / "runs" / current_id / "api_token_usage.json", root=artifacts)
        if measured_state == "ok":
            token_usage.update({key: value for key, value in _mapping(measured).items()
                                if key in TOKEN_KEYS and _number(value) is not None})
    trial_summary = {}
    for agent in ("oracle", "nop"):
        rows = [row for row in tasks if row["run_id"] == current_id] if current_id else []
        trials = [row[agent] for row in rows if row[agent]["status"] != "pending"]
        trial_summary[agent] = {"recorded": len(trials), "passed": sum(t["status"] == "passed" for t in trials),
                                "failed": sum(t["status"] in {"failed", "conflict"} for t in trials)}
    public = {"schema_version": 2, "block": "terminal-curator", "run_id": current_id, "status": status,
            "stage": stage, "source": source, "updated_at": _time(report.get("updated_at")),
            "started_at": _time(report.get("started_at")), "completed_at": _time(report.get("completed_at")),
            "total_candidates": _count(report.get("total_candidates")),
            "collected_questions": _count(report.get("collected_questions")),
            "generation_completed": _count(report.get("generation_completed")),
            "generation_failed": _count(report.get("generation_failed")),
            "verified_tasks": _ids(report.get("verified_tasks")), "rejected_count": len(rejected),
            "published_task_count": len(published) if manifest_state in {"ok", "missing"} else None, "published_tasks": published, "token_usage": token_usage,
            "tasks": tasks, "batches": batches, "history": history, "validation": trial_summary,
            "archive_count": len(entries) if index_state in {"ok", "missing"} else None,
            "batch_count": (len(dirs) if dirs else len(batches)) if config_state == "ok" else None,
            "availability": {"config": config_state, "state": live_state, "report": latest_state,
                             "manifest": manifest_state, "index": index_state,
                             "ignored_manifest_entries": ignored,
                             "limited": len(tasks) >= MAX_TASKS or len(dirs) > MAX_BATCHES or len(entries) > MAX_HISTORY},
            "snapshot_at": datetime.now(timezone.utc).isoformat()}
    return _hide_config_values(public, config)


def render(data: dict, live: bool = False) -> str:
    esc = lambda value: html.escape(redact(str(value if value is not None else "—")))
    # JSON is inert and cannot close its script element, even for direct render callers.
    payload = redact(json.dumps(data, ensure_ascii=False, allow_nan=False)).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (ASSETS / "template.html").read_text()
    replacements = {"STYLES": (ASSETS / "styles.css").read_text(),
                    "APP_SCRIPT": (ASSETS / "dashboard.js").read_text(), "PAYLOAD": payload,
                    "INITIAL_RUN": esc(data.get("run_id")), "INITIAL_STATUS": esc(data.get("status")),
                    "LIVE": str(live).lower()}
    return re.sub(r"__(STYLES|APP_SCRIPT|PAYLOAD|INITIAL_RUN|INITIAL_STATUS|LIVE)__",
                  lambda match: replacements[match[1]], template)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("TERMINAL_CURATOR_CONFIG", BLOCK / "config.yaml")))
    parser.add_argument("--html", nargs="?", const=str(BLOCK / "dashboard/site/index.html"))
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8086)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    config_path = _path(str(args.config))
    if args.html:
        destination = Path(args.html)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render(snapshot(config_path)))
        print(f"Wrote {destination}")
    elif not args.serve:
        print(json.dumps(snapshot(config_path), ensure_ascii=False, indent=2, allow_nan=False))
    if args.serve:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/", "/index.html", "/api/status"):
                    self.send_error(404)
                    return
                data = snapshot(config_path)
                raw = json.dumps(data, allow_nan=False).encode() if self.path == "/api/status" else render(data, live=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if self.path == "/api/status" else "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass  # Do not log arbitrary request paths or query strings.
        print(f"Terminal Curator dashboard: http://{args.host}:{args.port}", flush=True)
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
