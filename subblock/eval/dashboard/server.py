"""Harbor Job Dashboard server.

Stdlib-only HTTP server that surfaces job artifacts under
``$HARBOR_ROOT/jobs/<job_name>/`` (and per-trial ``analysis/``) as a JSON API,
plus a single-page UI styled after LLaMA-Factory/webui (slate-950 + indigo,
sidebar + tabs).

Run:
    python webui/server.py --port 8092
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import threading
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

LOG = logging.getLogger("harbor.webui")

# -- repo paths ---------------------------------------------------------------

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent
DEFAULT_JOBS = DEFAULT_REPO / "jobs"
STATIC_DIR = HERE / "static"


# -- helpers ------------------------------------------------------------------


def resolve_within(base: Path, name: str) -> Path:
    """Resolve ``name`` under ``base`` and reject any escape outside ``base``.

    Uses ``Path.relative_to`` on the resolved paths rather than a string prefix
    check, so a sibling directory that merely shares the prefix (``/x/jobs`` vs
    ``/x/jobs-secret``) cannot pass.
    """
    base_resolved = base.resolve()
    target = (base_resolved / name).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError:
        raise PermissionError("path escape") from None
    return target


def safe_load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Failed to read %s: %s", path, exc)
        return None


def safe_load_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if limit is not None and i >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Failed to read %s: %s", path, exc)
    return out


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _job_sort_key(path: Path) -> tuple[int, str]:
    name = path.name
    if name.startswith("swebench-verified"):
        group = 0
    elif "swebench_multilingual" in name or "swebench-multilingual" in name:
        group = 1
    elif "swebenchpro" in name or "swebench-pro" in name:
        group = 2
    else:
        group = 3
    return (group, name)


def duration_secs(start: str | None, end: str | None) -> float | None:
    a = parse_iso(start)
    b = parse_iso(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def is_trial_dir(p: Path) -> bool:
    if not p.is_dir():
        return False
    return (p / "result.json").exists() or (p / "config.json").exists() or (p / "agent").is_dir()


def percent(n: int, total: int) -> float:
    return round(100 * n / total, 2) if total else 0.0


DEFAULT_RULE_SCORE_COMPONENTS = (
    ("sub_score", 0.33, "SUB"),
    ("stp_score", 0.27, "STP"),
    ("tvr_score", 0.23, "TVR"),
    ("fec_score", 0.10, "FEC"),
    ("dpi_score", 0.07, "DPI"),
)

EXTRACTED_TRACE_FILES = (
    "output.critic_attempt_1.jsonl",
    "output.jsonl",
)
EXTRACTED_REPORT_FILES = (
    "output.critic_attempt_1.report.json",
    "output.report.json",
)


# -- model --------------------------------------------------------------------


class JobsRepo:
    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self._extracted_trace_cache: dict[Path, dict[str, Any]] = {}
        # per-job trial listing cache keyed by job dir signature (immutable
        # once a job finishes); avoids re-reading hundreds of trial files.
        self._trials_cache: dict[str, tuple[tuple[int, int], list[dict]]] = {}
        self._exc_cache: dict[str, tuple[tuple[int, int], dict[str, int]]] = {}

    def list_job_dirs(self) -> list[Path]:
        if not self.jobs_dir.exists():
            return []
        out = []
        for p in sorted(self.jobs_dir.iterdir(), key=_job_sort_key):
            if p.is_dir() and p.name != "bak":
                out.append(p)
        return out

    def list_jobs(self, detail: str = "full") -> list[dict]:
        out = []
        for p in self.list_job_dirs():
            if detail == "lite":
                out.append(self.job_summary(p, include_config=False, include_analysis=False, include_trial_count=False))
            else:
                out.append(self.job_summary(p, include_trial_count=False))
        return out

    def job_dir(self, job_name: str) -> Path:
        # protect against traversal (rejects escapes and sibling-prefix dirs)
        p = resolve_within(self.jobs_dir, job_name)
        if not p.is_dir():
            raise FileNotFoundError(f"job not found: {job_name}")
        return p

    def job_summary(
        self,
        p: Path,
        *,
        include_config: bool = True,
        include_analysis: bool = True,
        include_trial_count: bool = True,
    ) -> dict:
        cfg = (safe_load_json(p / "config.json") or {}) if include_config else {}
        if include_config and not cfg:
            cfg = self._extracted_trace_config(p) or {}
        agents = cfg.get("agents") or []
        agent = agents[0] if agents else {}
        datasets = cfg.get("datasets") or []
        dataset = datasets[0] if datasets else {}
        analysis = self.analysis_summary(p) if include_analysis else None
        trial_count = self._count_trials(p) if include_trial_count else None
        mtime = None
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
        except Exception:  # noqa: BLE001
            pass
        return {
            "name": p.name,
            "scaffold": _guess_scaffold(p.name, agent.get("import_path") or ""),
            "model": agent.get("model_name") if include_config else None,
            "dataset": dataset.get("name") or _guess_dataset(p.name),
            "n_concurrent_trials": cfg.get("n_concurrent_trials") if include_config else None,
            "started_at": _job_timestamp(p.name),
            "modified_at": mtime,
            "trial_count": trial_count,
            "exception_stats": self.exception_stats(p) if include_analysis else {},
            "analysis": analysis,
            "has_analysis": analysis is not None if include_analysis else self._has_analysis_artifacts(p),
        }

    def _count_trials(self, p: Path) -> int:
        n = 0
        for child in p.iterdir():
            if child.is_dir() and child.name != "analysis" and is_trial_dir(child):
                n += 1
        if n == 0:
            trace_path = _extracted_trace_path(p)
            if trace_path:
                return _count_jsonl_records(trace_path)
        return n

    def _has_analysis_artifacts(self, p: Path) -> bool:
        a = p / "analysis"
        if not a.is_dir():
            return False
        return any(
            path.exists()
            for path in (
                a / "score_comparison.json",
                a / "report_task_analysis.json",
                a / "report_failed.json",
                a / "report_resolved.json",
                a / "traj_analysis" / "score_comparison.json",
            )
        )

    @staticmethod
    def _trial_exception_type(child: Path) -> str | None:
        res = safe_load_json(child / "result.json") or {}
        return _exception_type(res.get("exception_info"))

    def exception_stats(self, p: Path) -> dict[str, int]:
        try:
            st = p.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        cached = self._exc_cache.get(str(p))
        if sig is not None and cached is not None and cached[0] == sig:
            return cached[1]

        children = [
            child
            for child in p.iterdir()
            if child.name != "analysis" and is_trial_dir(child)
        ]
        counts: Counter[str] = Counter()
        if children:
            with ThreadPoolExecutor(max_workers=min(32, len(children))) as pool:
                for exc_type in pool.map(self._trial_exception_type, children):
                    if exc_type:
                        counts[exc_type] += 1
        result = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
        if sig is not None:
            self._exc_cache[str(p)] = (sig, result)
        return result

    def analysis_summary(self, p: Path) -> dict | None:
        a = p / "analysis"
        if not a.is_dir():
            return None
        score_comp = safe_load_json(a / "score_comparison.json")
        task_analysis = safe_load_json(a / "report_task_analysis.json")
        rule_score = safe_load_json(a / "traj_analysis" / "score_comparison.json")
        report_failed = None
        report_resolved = None
        if not any([
            score_comp,
            task_analysis,
            rule_score,
            (a / "report_failed.json").exists(),
            (a / "report_resolved.json").exists(),
        ]):
            return None
        resolved_total = (score_comp or {}).get("resolved_total") if score_comp else None
        failed_total = (score_comp or {}).get("failed_total") if score_comp else None
        if resolved_total is None and task_analysis:
            resolved_total = task_analysis.get("summary", {}).get("resolved_instances")
        if failed_total is None and task_analysis:
            failed_total = task_analysis.get("summary", {}).get("failed_instances")
        if resolved_total is None:
            report_resolved = safe_load_json(a / "report_resolved.json")
            resolved_total = (report_resolved or {}).get("summary", {}).get("total_instances")
        if failed_total is None:
            report_failed = safe_load_json(a / "report_failed.json")
            failed_total = (report_failed or {}).get("summary", {}).get("total_instances")
        total = (resolved_total or 0) + (failed_total or 0)
        rate = percent(resolved_total or 0, total)
        return {
            "resolved_total": resolved_total,
            "failed_total": failed_total,
            "total": total or None,
            "resolve_rate": rate,
            "has_score_comparison": bool(score_comp),
            "has_task_analysis": bool(task_analysis),
            "has_rule_score": bool(rule_score),
        }

    # -- single job views

    def job_detail(self, job_name: str) -> dict:
        p = self.job_dir(job_name)
        summary = self.job_summary(p)
        a = p / "analysis"
        rs = a / "traj_analysis"
        ta = a / "instance_analysis"
        rule_score = _augment_rule_score(
            safe_load_json(rs / "score_comparison.json"),
            _read_text(rs / "report_comparison.txt"),
        )
        return {
            "summary": summary,
            "config": safe_load_json(p / "config.json"),
            "analysis_config": _read_text(a / "analysis_config.yaml"),
            "report_failed": safe_load_json(a / "report_failed.json"),
            "report_resolved": safe_load_json(a / "report_resolved.json"),
            "score_comparison": safe_load_json(a / "score_comparison.json"),
            "task_analysis": safe_load_json(a / "report_task_analysis.json"),
            "rule_score": rule_score,
            "tag_analysis_summary": safe_load_json(ta / "summary.json"),
            "tag_correlations": safe_load_json(ta / "correlations.json"),
            "contingency_difficulty": _read_text(ta / "contingency_difficulty_label.txt"),
            "contingency_tag1": _read_text(ta / "contingency_tag1.txt"),
            "contingency_tag2": _read_text(ta / "contingency_tag2.txt"),
            "trials": self.list_trials(job_name),
        }

    @staticmethod
    def _build_trial_entry(child: Path) -> dict:
        res = safe_load_json(child / "result.json") or {}
        verifier = res.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        reward = rewards.get("reward")
        duration = duration_secs(res.get("started_at"), res.get("finished_at"))
        agent_result = res.get("agent_result") or {}
        exception_info = res.get("exception_info")
        token_summary = _trial_token_summary(child)
        hit_max_turn = _hit_max_turn(child)
        hit_context_window = (
            _hit_context_window_exceeded(child, exception_info) if exception_info else False
        )
        hit_max_length = bool(token_summary.get("hit_max_length")) or hit_context_window
        return {
            "trial_name": child.name,
            "task_name": res.get("task_name"),
            "resolved": bool(reward and reward >= 1) if reward is not None else None,
            "reward": reward,
            "duration_sec": duration,
            "n_input_tokens": agent_result.get("n_input_tokens"),
            "n_output_tokens": agent_result.get("n_output_tokens"),
            "n_tokens": token_summary.get("total_tokens"),
            "token_summary": token_summary,
            "truncation": _truncation_label(hit_max_turn, hit_max_length),
            "hit_max_turn": hit_max_turn,
            "hit_max_length": hit_max_length,
            "litellm_finish_reason": token_summary.get("finish_reason"),
            "cost_usd": agent_result.get("cost_usd"),
            "exception_info": exception_info,
            "exception_type": _exception_type(exception_info),
            "turn_count": _count_trajectory_turns(child / "agent" / "trajectory.json"),
            "has_trajectory": (child / "agent" / "trajectory.json").exists(),
            "started_at": res.get("started_at"),
            "finished_at": res.get("finished_at"),
        }

    def list_trials(self, job_name: str) -> list[dict]:
        p = self.job_dir(job_name)
        try:
            st = p.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        cached = self._trials_cache.get(str(p))
        if sig is not None and cached is not None and cached[0] == sig:
            return cached[1]

        children = [
            child
            for child in sorted(p.iterdir())
            if child.name != "analysis" and is_trial_dir(child)
        ]
        # Per-trial work is I/O bound (reads result.json, tails the litellm
        # jsonl, reads trajectory.json / openhands_sdk.txt); fan out across
        # threads so hundreds of trials don't serialize.
        if children:
            with ThreadPoolExecutor(max_workers=min(32, len(children))) as pool:
                out = list(pool.map(self._build_trial_entry, children))
        else:
            out = []
        if not out and _extracted_trace_path(p):
            return self._extracted_trace_index(p)["trials"]
        if sig is not None:
            self._trials_cache[str(p)] = (sig, out)
        return out

    def trial_detail(self, job_name: str, trial_name: str) -> dict:
        p = self.job_dir(job_name)
        record = self._extracted_trace_record(p, trial_name)
        if record is not None:
            instance_id = record.get("instance_id")
            analysis = self._analysis_instance_for(p, instance_id)
            return _extracted_trial_detail(
                record,
                trial_name,
                analysis,
                self._extracted_trace_config(p),
                _extracted_trace_path(p),
                verifier_error=self._is_verifier_error(p, instance_id),
            )
        t = resolve_within(p, trial_name)
        if not t.is_dir():
            raise FileNotFoundError(f"trial not found: {trial_name}")
        result = safe_load_json(t / "result.json") or {}
        agent_dir = t / "agent"
        verifier_dir = t / "verifier"
        token_summary = _trial_token_summary(t)
        hit_max_turn = _hit_max_turn(t)
        exception_info = result.get("exception_info")
        hit_context_window = (
            _hit_context_window_exceeded(t, exception_info) if exception_info else False
        )
        hit_max_length = bool(token_summary.get("hit_max_length")) or hit_context_window
        return {
            "result": result,
            "config": safe_load_json(t / "config.json"),
            "verifier_report": safe_load_json(verifier_dir / "report.json"),
            "verifier_reward": _read_text(verifier_dir / "reward.txt"),
            "verifier_test_stdout": _read_text(verifier_dir / "test-stdout.txt", limit=200_000),
            "trajectory_present": (agent_dir / "trajectory.json").exists(),
            "litellm_present": (agent_dir / "litellm-trajectory.jsonl").exists(),
            "token_summary": token_summary,
            "truncation": _truncation_label(hit_max_turn, hit_max_length),
            "hit_max_turn": hit_max_turn,
            "hit_max_length": hit_max_length,
            "agent_files": [f.name for f in agent_dir.iterdir() if f.is_file()] if agent_dir.is_dir() else [],
        }

    def trajectory(self, job_name: str, trial_name: str, kind: str) -> dict:
        p = self.job_dir(job_name)
        record = self._extracted_trace_record(p, trial_name)
        if kind == "agent" and record is not None:
            instance_id = record.get("instance_id")
            analysis = self._analysis_instance_for(p, instance_id)
            return _extracted_trajectory(
                record,
                trial_name,
                analysis,
                self._extracted_trace_config(p),
                verifier_error=self._is_verifier_error(p, instance_id),
            )
        t = resolve_within(p, trial_name)
        agent_dir = t / "agent"
        if kind == "agent":
            data = safe_load_json(agent_dir / "trajectory.json")
            if data is None:
                raise FileNotFoundError("trajectory.json missing")
            payload = _trim_trajectory(data)
            token_summary = _trial_token_summary(t)
            hit_max_turn = _hit_max_turn(t)
            result = safe_load_json(t / "result.json") or {}
            exception_info = result.get("exception_info")
            hit_context_window = (
                _hit_context_window_exceeded(t, exception_info) if exception_info else False
            )
            hit_max_length = bool(token_summary.get("hit_max_length")) or hit_context_window
            payload["token_summary"] = token_summary
            payload["truncation"] = _truncation_label(hit_max_turn, hit_max_length)
            payload["hit_max_turn"] = hit_max_turn
            payload["hit_max_length"] = hit_max_length
            return payload
        if kind == "litellm":
            rows = safe_load_jsonl(agent_dir / "litellm-trajectory.jsonl")
            return _summarize_litellm(rows)
        raise ValueError(f"unknown kind: {kind}")

    def _extracted_trace_config(self, job_dir: Path) -> dict | None:
        trace_path = _extracted_trace_path(job_dir)
        if not trace_path:
            return None
        record = _load_jsonl_record_at(trace_path, 0)
        if not record:
            return None
        model_name = _extracted_model_name(record)
        dataset_name = _extracted_dataset_name(record) or _guess_dataset(job_dir.name)
        agent: dict[str, Any] = {"import_path": "openhands-sdk", "model_name": model_name}
        return {
            "agents": [agent],
            "datasets": [{"name": dataset_name}] if dataset_name else [],
            "trace_file": trace_path.name,
        }

    def _extracted_trace_index(self, job_dir: Path) -> dict[str, Any]:
        trace_path = _extracted_trace_path(job_dir)
        if not trace_path:
            raise FileNotFoundError("extracted trace JSONL missing")
        report_path = _extracted_report_path(job_dir)
        signature = (_file_signature(trace_path), _file_signature(report_path) if report_path else None)
        cached = self._extracted_trace_cache.get(trace_path)
        if cached and cached.get("signature") == signature:
            return cached

        analysis_by_id = _analysis_instances_by_id(job_dir)
        verifier_error_ids = _extracted_verifier_error_ids(job_dir)
        trials: list[dict[str, Any]] = []
        offsets: dict[str, int] = {}
        with trace_path.open("rb") as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("Failed to parse %s at byte %s: %s", trace_path, offset, exc)
                    continue
                instance_id = _extracted_instance_id(record)
                if not instance_id:
                    continue
                trial_name = instance_id
                if trial_name in offsets:
                    attempt = record.get("attempt")
                    suffix = f"attempt_{attempt}" if attempt is not None else str(len(offsets))
                    trial_name = f"{trial_name}__{suffix}"
                offsets[trial_name] = offset
                trials.append(
                    _extracted_trial_summary(
                        record,
                        trial_name,
                        analysis_by_id.get(instance_id),
                        verifier_error=instance_id in verifier_error_ids,
                    )
                )
        trials.sort(key=lambda trial: (str(trial.get("task_name") or trial.get("trial_name") or ""), str(trial.get("trial_name") or "")))

        cached = {
            "path": trace_path,
            "signature": signature,
            "trials": trials,
            "offsets": offsets,
        }
        self._extracted_trace_cache[trace_path] = cached
        return cached

    def _extracted_trace_record(self, job_dir: Path, trial_name: str) -> dict | None:
        trace_path = _extracted_trace_path(job_dir)
        if not trace_path:
            return None
        index = self._extracted_trace_index(job_dir)
        offset = index["offsets"].get(trial_name)
        if offset is None:
            return None
        return _load_jsonl_record_at(trace_path, offset)

    def _analysis_instance_for(self, job_dir: Path, instance_id: Any) -> dict | None:
        if not instance_id:
            return None
        return _analysis_instances_by_id(job_dir).get(str(instance_id))

    def _is_verifier_error(self, job_dir: Path, instance_id: Any) -> bool:
        if not instance_id:
            return False
        return str(instance_id) in _extracted_verifier_error_ids(job_dir)

    def rule_score_instances(self, job_name: str, kind: str, limit: int) -> dict:
        p = self.job_dir(job_name)
        rs = p / "analysis" / "traj_analysis"
        if kind == "resolved":
            rows = safe_load_jsonl(rs / "resolved_im.jsonl", limit=limit)
        elif kind == "unresolved":
            rows = safe_load_jsonl(rs / "unresolved_im.jsonl", limit=limit)
        else:
            raise ValueError(f"unknown kind: {kind}")
        return {"kind": kind, "count": len(rows), "instances": rows}


def _extracted_trace_path(job_dir: Path) -> Path | None:
    for name in EXTRACTED_TRACE_FILES:
        path = job_dir / name
        if path.is_file():
            return path
    return None


def _extracted_report_path(job_dir: Path) -> Path | None:
    for name in EXTRACTED_REPORT_FILES:
        path = job_dir / name
        if path.is_file():
            return path
    trace_path = _extracted_trace_path(job_dir)
    if trace_path and trace_path.name.endswith(".jsonl"):
        path = trace_path.with_name(trace_path.name[: -len(".jsonl")] + ".report.json")
        if path.is_file():
            return path
    return None


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _count_jsonl_records(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return sum(1 for line in f if line.strip())
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Failed to count %s: %s", path, exc)
        return 0


def _load_jsonl_record_at(path: Path, offset: int) -> dict | None:
    try:
        with path.open("rb") as f:
            f.seek(offset)
            line = f.readline()
        if not line:
            return None
        data = json.loads(line)
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Failed to read %s at byte %s: %s", path, offset, exc)
        return None


def _analysis_instances_by_id(job_dir: Path) -> dict[str, dict]:
    rows = safe_load_jsonl(job_dir / "analysis" / "instances.jsonl")
    if not rows:
        rows = safe_load_jsonl(job_dir / "analysis" / "instances_resolved.jsonl")
        rows.extend(safe_load_jsonl(job_dir / "analysis" / "instances_failed.jsonl"))
    out: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("instance_id"):
            out[str(row["instance_id"])] = row
    return out


def _extracted_verifier_error_ids(job_dir: Path) -> set[str]:
    report_path = _extracted_report_path(job_dir)
    if not report_path:
        return set()
    report = safe_load_json(report_path)
    if not isinstance(report, dict):
        return set()
    ids = report.get("error_ids")
    if not isinstance(ids, list):
        return set()
    return {str(item) for item in ids if item}


def _extracted_instance_id(record: dict) -> str | None:
    value = record.get("instance_id") or record.get("task_name") or record.get("id")
    return str(value) if value else None


def _extracted_model_name(record: dict) -> str | None:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    llm = metadata.get("llm")
    if isinstance(llm, dict) and llm.get("model"):
        return str(llm["model"])
    if isinstance(llm, str) and llm:
        return llm
    metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    latencies = metrics.get("response_latencies")
    if isinstance(latencies, list) and latencies:
        first = latencies[0]
        if isinstance(first, dict) and first.get("model"):
            return str(first["model"])
    model_name = metrics.get("model_name")
    if model_name and model_name != "default":
        return str(model_name)
    return None


def _extracted_dataset_name(record: dict) -> str | None:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    dataset = metadata.get("dataset")
    if isinstance(dataset, dict):
        for key in ("name", "path", "id"):
            if dataset.get(key):
                return str(dataset[key])
    if dataset:
        return str(dataset)
    split = metadata.get("dataset_split")
    return str(split) if split else None


def _trace_timestamp_range(history: list) -> tuple[str | None, str | None]:
    timestamps = [
        str(event.get("timestamp"))
        for event in history
        if isinstance(event, dict) and event.get("timestamp")
    ]
    return (timestamps[0], timestamps[-1]) if timestamps else (None, None)


def _token_summary_from_metrics(metrics: Any) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        metrics = {}
    usage = metrics.get("accumulated_token_usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("per_turn_token")
    if total is None:
        total = usage.get("total_tokens")
    if total is None:
        parts = [n for n in (prompt, completion) if isinstance(n, (int, float))]
        total = sum(parts) if parts else None
    return {
        "total_tokens": total,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "finish_reason": None,
        "hit_max_length": False,
    }


def _extracted_trial_summary(
    record: dict,
    trial_name: str,
    analysis: dict | None,
    *,
    verifier_error: bool = False,
) -> dict:
    history = record.get("history") if isinstance(record.get("history"), list) else []
    metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    token_summary = _token_summary_from_metrics(metrics)
    started_at, finished_at = _trace_timestamp_range(history)
    resolved = analysis.get("resolved") if isinstance(analysis, dict) else None
    reward = 1 if resolved is True else 0 if resolved is False else None
    action_count = sum(1 for event in history if isinstance(event, dict) and event.get("kind") == "ActionEvent")
    error = record.get("error")
    hit_max_turn = _hit_max_turn_error(error)
    hit_max_length = bool(token_summary.get("hit_max_length")) or _hit_context_window_error(error)
    exception_info = "Verifier error" if verifier_error and not error else error
    exception_type = "Verifier error" if verifier_error else _exception_type(error)
    return {
        "trial_name": trial_name,
        "task_name": _extracted_instance_id(record),
        "resolved": resolved,
        "reward": reward,
        "duration_sec": duration_secs(started_at, finished_at),
        "n_input_tokens": token_summary.get("prompt_tokens"),
        "n_output_tokens": token_summary.get("completion_tokens"),
        "n_tokens": token_summary.get("total_tokens"),
        "token_summary": token_summary,
        "truncation": _truncation_label(hit_max_turn, hit_max_length),
        "hit_max_turn": hit_max_turn,
        "hit_max_length": hit_max_length,
        "litellm_finish_reason": None,
        "cost_usd": metrics.get("accumulated_cost"),
        "exception_info": exception_info,
        "exception_type": exception_type,
        "turn_count": action_count,
        "has_trajectory": bool(history),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _extracted_trial_detail(
    record: dict,
    trial_name: str,
    analysis: dict | None,
    config: dict | None,
    trace_path: Path | None,
    *,
    verifier_error: bool = False,
) -> dict:
    summary = _extracted_trial_summary(record, trial_name, analysis, verifier_error=verifier_error)
    reward = summary.get("reward")
    result = {
        "task_name": summary.get("task_name"),
        "started_at": summary.get("started_at"),
        "finished_at": summary.get("finished_at"),
        "exception_info": summary.get("exception_info"),
        "agent_result": {
            "n_input_tokens": summary.get("n_input_tokens"),
            "n_output_tokens": summary.get("n_output_tokens"),
            "cost_usd": summary.get("cost_usd"),
        },
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
    }
    test_result = record.get("test_result")
    if isinstance(test_result, dict) and isinstance(test_result.get("git_patch"), str):
        test_result = {**test_result, "git_patch": _trim_message(test_result["git_patch"], limit=50_000)}
    return {
        "result": result,
        "config": config,
        "verifier_report": analysis,
        "verifier_reward": str(reward) if reward is not None else None,
        "verifier_test_stdout": None,
        "trajectory_present": bool(record.get("history")),
        "litellm_present": False,
        "token_summary": summary.get("token_summary"),
        "truncation": summary.get("truncation"),
        "hit_max_turn": summary.get("hit_max_turn"),
        "hit_max_length": summary.get("hit_max_length"),
        "agent_files": [trace_path.name] if trace_path else [],
        "test_result": test_result,
    }


def _exception_type(exception_info: Any) -> str | None:
    if not exception_info:
        return None
    if isinstance(exception_info, dict):
        for key in ("exception_type", "type", "class", "name"):
            value = exception_info.get(key)
            if value:
                return str(value)
        return "Exception"
    if isinstance(exception_info, str):
        head = exception_info.strip().splitlines()[0] if exception_info.strip() else ""
        if head.startswith("Instance failed after 3 retries. Last error"):
            return "Failed after 3 retries"
        return head.split(":", 1)[0] or None
    return type(exception_info).__name__


MAX_LENGTH_TOTAL_TOKENS = 131_072
MAX_TURN_RE = re.compile(
    r"MaxIterationsReached|Agent reached maximum.*?iterations limit \(\d+\)|maximum.*?iterations limit",
    re.IGNORECASE | re.DOTALL,
)
CONTEXT_WINDOW_EXCEEDED_RE = re.compile(
    r"ContextWindowExceededError|LLMContextWindowExceedError|maximum context length",
    re.IGNORECASE,
)


def _exception_text(exception_info: Any) -> str:
    if not exception_info:
        return ""
    if isinstance(exception_info, str):
        return exception_info
    try:
        return json.dumps(exception_info, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(exception_info)


def _hit_context_window_exceeded(trial_dir: Path, exception_info: Any = None) -> bool:
    if _hit_context_window_error(exception_info):
        return True

    # OpenHands SDK can fail before Harbor captures the inner LiteLLM exception
    # in result.json, so inspect the agent log for the context-window error.
    path = trial_dir / "agent" / "openhands_sdk.txt"
    if not path.exists():
        return False
    try:
        return bool(CONTEXT_WINDOW_EXCEEDED_RE.search(path.read_text(encoding="utf-8", errors="replace")))
    except Exception:  # noqa: BLE001
        return False


def _hit_context_window_error(exception_info: Any = None) -> bool:
    return bool(CONTEXT_WINDOW_EXCEEDED_RE.search(_exception_text(exception_info)))


def _hit_max_turn_error(exception_info: Any = None) -> bool:
    return bool(MAX_TURN_RE.search(_exception_text(exception_info)))


def _litellm_usage(row: dict) -> dict:
    candidates = []
    if isinstance(row.get("usage"), dict):
        candidates.append(row.get("usage"))
    response_body = row.get("response_body")
    if isinstance(response_body, dict) and isinstance(response_body.get("usage"), dict):
        candidates.append(response_body.get("usage"))
    response = row.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        candidates.append(response.get("usage"))
    return next((usage for usage in candidates if isinstance(usage, dict)), {})


def _litellm_finish_reason(row: dict) -> str | None:
    for key in ("response_body", "response"):
        response = row.get(key)
        if not isinstance(response, dict):
            continue
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            reason = choices[0].get("finish_reason")
            if reason:
                return str(reason)
    return None


def _last_jsonl_record(path: Path) -> dict | None:
    """Return only the last JSON object of a (possibly huge) jsonl file.

    litellm-trajectory.jsonl logs run to tens of MB each; parsing the whole
    file just to read the final usage record dominates job-listing latency.
    Seek from the end in chunks and decode the last complete line instead.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    chunk = 65536
    buf = b""
    pos = size
    try:
        with path.open("rb") as f:
            while pos > 0:
                read = min(chunk, pos)
                pos -= read
                f.seek(pos)
                buf = f.read(read) + buf
                stripped = buf.rstrip()
                parts = stripped.split(b"\n")
                # parts[0] may be a partial line unless we've read the whole file
                start_idx = 0 if pos == 0 else 1
                for line in reversed(parts[start_idx:]):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if isinstance(rec, dict):
                        return rec
                if pos == 0:
                    return None
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Failed to tail-read %s: %s", path, exc)
    return None


def _trial_token_summary(trial_dir: Path) -> dict[str, Any]:
    last = _last_jsonl_record(trial_dir / "agent" / "litellm-trajectory.jsonl")
    if not last:
        return {
            "total_tokens": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "finish_reason": None,
            "hit_max_length": False,
        }
    usage = _litellm_usage(last)
    finish_reason = _litellm_finish_reason(last)
    total_tokens = usage.get("total_tokens")
    return {
        "total_tokens": total_tokens,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "finish_reason": finish_reason,
        "hit_max_length": total_tokens == MAX_LENGTH_TOTAL_TOKENS and finish_reason == "length",
    }


def _hit_max_turn(trial_dir: Path) -> bool:
    path = trial_dir / "agent" / "openhands_sdk.txt"
    if not path.exists():
        return False
    try:
        return bool(MAX_TURN_RE.search(path.read_text(encoding="utf-8", errors="replace")))
    except Exception:  # noqa: BLE001
        return False


def _truncation_label(hit_max_turn: bool, hit_max_length: bool) -> str | None:
    if hit_max_turn:
        return "max turn"
    if hit_max_length:
        return "max length"
    return None


def _count_trajectory_turns(path: Path) -> int | None:
    data = safe_load_json(path)
    if not isinstance(data, dict):
        return None
    turns = 0
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool_calls = step.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            turns += 1
            continue
        if step.get("source") == "agent" and step.get("message"):
            turns += 1
    return turns


def _read_text(path: Path, limit: int = 200_000) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > limit:
            return text[:limit] + "\n... [truncated] ..."
        return text
    except Exception:  # noqa: BLE001
        return None


def _parse_rule_score_active_components(report_text: str | None) -> list[dict[str, Any]]:
    if not report_text:
        return []

    active_components: list[dict[str, Any]] = []
    in_glossary = False
    pending: dict[str, Any] | None = None

    for line in report_text.splitlines():
        stripped = line.strip()
        if stripped == "-- Metric glossary --":
            in_glossary = True
            continue
        if not in_glossary:
            continue
        if stripped.startswith("-- Composite score formula"):
            break

        match = re.match(r"^\s*([a-z_]+_score) \(w=([0-9.]+)\)\s+(.+?)\s*$", line)
        if match:
            key, weight_text, label = match.groups()
            pending = None
            try:
                weight = float(weight_text)
            except ValueError:
                continue
            if weight <= 0:
                continue
            pending = {"key": key, "weight": weight, "label": label.strip()}
            active_components.append(pending)
            continue

        if pending and stripped:
            pending["description"] = stripped
            pending = None

    active_components.sort(key=lambda item: (-item["weight"], item["key"]))
    return active_components


def _augment_rule_score(rule_score: dict | None, report_text: str | None) -> dict | None:
    if not rule_score:
        return None

    augmented = dict(rule_score)
    active_components = _parse_rule_score_active_components(report_text)
    if not active_components:
        metrics = {
            *(((rule_score.get("resolved") or {}).get("metrics") or {}).keys()),
            *(((rule_score.get("unresolved") or {}).get("metrics") or {}).keys()),
        }
        active_components = [
            {"key": key, "weight": weight, "label": label}
            for key, weight, label in DEFAULT_RULE_SCORE_COMPONENTS
            if key in metrics
        ]
    augmented["active_components"] = active_components
    return augmented


def _job_timestamp(name: str) -> str | None:
    m = re.search(r"-(\d{14})$", name)
    if not m:
        return None
    s = m.group(1)
    try:
        return datetime.strptime(s, "%Y%m%d%H%M%S").isoformat(timespec="seconds")
    except ValueError:
        return None


def _guess_scaffold(job_name: str, import_path: str) -> str | None:
    if "openhands-sdk" in job_name or "openhands_sdk" in import_path:
        return "openhands-sdk"
    if "openhands" in job_name and "sdk" not in job_name:
        return "openhands"
    if "claude-code" in job_name:
        return "claude-code"
    if "opencode" in job_name:
        return "opencode"
    if "openhands" in import_path:
        return "openhands-sdk"
    return None


def _guess_dataset(job_name: str) -> str | None:
    for prefix in (
        "swebench-verified",
        "swebench_multilingual",
        "swebenchpro",
        "swebench-pro",
    ):
        if job_name.startswith(prefix):
            return prefix
    return None


# -- trajectory helpers -------------------------------------------------------


def _trim_message(text: Any, limit: int = 8000) -> Any:
    if isinstance(text, str) and len(text) > limit:
        return text[:limit] + f"\n... [truncated {len(text) - limit} chars] ..."
    return text


def _strip_ansi(s: str) -> str:
    if not isinstance(s, str):
        return s
    # Remove ANSI escape sequences (CSI, terminal mode toggles, color codes)
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", s)


def _trim_tool_call(tc: Any) -> dict:
    if not isinstance(tc, dict):
        return {"name": None, "arguments": None}
    # ATIF v1.5 shape: tool_call_id / function_name / arguments(dict)
    # OpenAI shape: id / function: {name, arguments(str)}
    # OpenHands history exports may use id / name / arguments directly.
    name = tc.get("function_name")
    args = tc.get("arguments")
    tc_id = tc.get("tool_call_id") or tc.get("id")
    if name is None and tc.get("name"):
        name = tc.get("name")
    if name is None:
        f = tc.get("function")
        if isinstance(f, dict):
            name = f.get("name")
            if f.get("arguments") is not None:
                args = f.get("arguments")
    # normalize arguments to a pretty-printed string for display, but keep dict separately
    args_dict = None
    args_str = None
    if isinstance(args, dict):
        args_dict = args
        try:
            args_str = json.dumps(args, indent=2, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            args_str = str(args)
    elif isinstance(args, str):
        args_str = args
        try:
            args_dict = json.loads(args)
        except Exception:  # noqa: BLE001
            pass
    else:
        args_str = "" if args is None else str(args)
    return {
        "id": tc_id,
        "name": name,
        "arguments": _trim_message(args_str),
        "arguments_dict": args_dict,
    }


def _trim_observation(obs: Any) -> Any:
    if obs is None:
        return None
    # ATIF v1.5: {"results": [{"source_call_id": ..., "content": str}]}
    if isinstance(obs, dict) and isinstance(obs.get("results"), list):
        return {
            "results": [
                {
                    "source_call_id": r.get("source_call_id"),
                    "content": _trim_message(_strip_ansi(r.get("content", ""))),
                    "exit_code": r.get("exit_code"),
                    "metadata": r.get("metadata"),
                }
                for r in obs["results"]
                if isinstance(r, dict)
            ]
        }
    if isinstance(obs, dict):
        return {k: _trim_message(_strip_ansi(v) if isinstance(v, str) else v) for k, v in obs.items()}
    if isinstance(obs, str):
        return _trim_message(_strip_ansi(obs))
    return obs


def _trim_step(step: dict) -> dict:
    out = {
        "step_id": step.get("step_id"),
        "timestamp": step.get("timestamp"),
        "source": step.get("source"),
        "model_name": step.get("model_name"),
    }
    msg = step.get("message")
    out["message"] = _trim_message(msg) if msg else ""
    tool_calls = step.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        out["tool_calls"] = [_trim_tool_call(tc) for tc in tool_calls]
    obs = step.get("observation")
    if obs is not None:
        out["observation"] = _trim_observation(obs)
    if step.get("usage"):
        out["usage"] = step["usage"]
    return out


def _trim_trajectory(data: dict) -> dict:
    steps = data.get("steps") or []
    agent = data.get("agent") or {}
    tools = agent.get("tool_definitions") or []
    tool_names = []
    for t in tools:
        if isinstance(t, dict):
            f = t.get("function") or {}
            if f.get("name"):
                tool_names.append(f["name"])
    return {
        "schema_version": data.get("schema_version"),
        "session_id": data.get("session_id"),
        "agent": {
            "name": agent.get("name"),
            "version": agent.get("version"),
            "tool_count": len(tools),
            "tool_names": tool_names,
        },
        "final_metrics": data.get("final_metrics"),
        "step_count": len(steps),
        "steps": [_trim_step(s) for s in steps],
    }


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_content_to_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if "content" in value:
            content = _content_to_text(value.get("content"))
            if content:
                return content
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)


def _tool_call_from_event(event: dict) -> dict | None:
    tool_call = event.get("tool_call")
    if isinstance(tool_call, dict):
        return tool_call
    action = event.get("action")
    if not isinstance(action, dict):
        return None
    args = {k: v for k, v in action.items() if k not in {"kind"}}
    name = event.get("tool_name") or _tool_name_from_action(action)
    if not name:
        return None
    try:
        args_text = json.dumps(args, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        args_text = str(args)
    return {
        "id": event.get("tool_call_id") or event.get("id"),
        "name": name,
        "arguments": args_text,
    }


def _tool_name_from_action(action: dict) -> str | None:
    kind = action.get("kind")
    if not isinstance(kind, str):
        return None
    if kind.endswith("Action"):
        kind = kind[: -len("Action")]
    mapping = {
        "Terminal": "terminal",
        "FileEdit": "file_editor",
        "Think": "think",
        "Finish": "finish",
    }
    return mapping.get(kind, kind[:1].lower() + kind[1:] if kind else None)


def _usage_by_response_id(metrics: Any) -> dict[str, dict]:
    if not isinstance(metrics, dict):
        return {}
    out: dict[str, dict] = {}
    token_usages = metrics.get("token_usages")
    if not isinstance(token_usages, list):
        return out
    for usage in token_usages:
        if isinstance(usage, dict) and usage.get("response_id"):
            prompt = usage.get("prompt_tokens") or 0
            completion = usage.get("completion_tokens") or 0
            out[str(usage["response_id"])] = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens") or prompt + completion,
            }
    return out


def _observation_result_from_event(event: dict) -> dict:
    obs = event.get("observation")
    obs_dict = obs if isinstance(obs, dict) else {}
    content = _content_to_text(obs_dict.get("content") if obs_dict else obs)
    metadata = obs_dict.get("metadata") if isinstance(obs_dict.get("metadata"), dict) else obs_dict.get("metadata")
    exit_code = obs_dict.get("exit_code")
    if exit_code is None and isinstance(metadata, dict):
        exit_code = metadata.get("exit_code")
    if not content and isinstance(metadata, dict):
        suffix = metadata.get("suffix")
        if isinstance(suffix, str) and suffix.strip():
            content = suffix.strip()
    if not content and obs_dict:
        content = _content_to_text({k: v for k, v in obs_dict.items() if k not in {"metadata"}})
    return {
        "source_call_id": event.get("tool_call_id") or event.get("action_id"),
        "content": content,
        "exit_code": exit_code,
        "metadata": metadata,
    }


def _tool_definitions_from_steps(steps: list[dict]) -> list[dict]:
    names: list[str] = []
    for step in steps:
        for tool_call in step.get("tool_calls") or []:
            name = tool_call.get("name")
            if name and name not in names:
                names.append(name)
    return [{"function": {"name": name}} for name in names]


def _extracted_trajectory(
    record: dict,
    trial_name: str,
    analysis: dict | None,
    config: dict | None,
    *,
    verifier_error: bool = False,
) -> dict:
    history = record.get("history") if isinstance(record.get("history"), list) else []
    usage_by_id = _usage_by_response_id(record.get("metrics"))
    steps: list[dict[str, Any]] = []
    step_id = 1

    for event in history:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        source = event.get("source")
        timestamp = event.get("timestamp")

        if kind == "SystemPromptEvent":
            message = _content_to_text(event.get("system_prompt"))
            if message:
                steps.append({
                    "step_id": step_id,
                    "timestamp": timestamp,
                    "source": "system",
                    "message": message,
                })
                step_id += 1
            continue

        if kind == "MessageEvent":
            llm_message = event.get("llm_message") if isinstance(event.get("llm_message"), dict) else {}
            message = _content_to_text(llm_message.get("content"))
            if message:
                steps.append({
                    "step_id": step_id,
                    "timestamp": timestamp,
                    "source": source or llm_message.get("role") or "user",
                    "message": message,
                })
                step_id += 1
            continue

        if kind == "ActionEvent":
            tool_call = _tool_call_from_event(event)
            message = event.get("reasoning_content") or _content_to_text(event.get("thought"))
            step: dict[str, Any] = {
                "step_id": step_id,
                "timestamp": timestamp,
                "source": "agent",
                "model_name": _extracted_model_name(record),
                "message": message or "",
            }
            if tool_call:
                step["tool_calls"] = [tool_call]
            usage = usage_by_id.get(str(event.get("llm_response_id")))
            if usage:
                step["usage"] = usage
            steps.append(step)
            step_id += 1
            continue

        if kind == "ObservationEvent":
            steps.append({
                "step_id": step_id,
                "timestamp": timestamp,
                "source": "environment",
                "message": "",
                "observation": {"results": [_observation_result_from_event(event)]},
            })
            step_id += 1
            continue

        if kind == "AgentErrorEvent":
            steps.append({
                "step_id": step_id,
                "timestamp": timestamp,
                "source": "agent",
                "message": _content_to_text(event),
            })
            step_id += 1

    agent_cfg = ((config or {}).get("agents") or [{}])[0]
    data = {
        "schema_version": "openhands-history-jsonl",
        "session_id": trial_name,
        "agent": {
            "name": _guess_scaffold("", agent_cfg.get("import_path") or "") or "openhands-sdk",
            "version": "unknown",
            "tool_definitions": _tool_definitions_from_steps(steps),
        },
        "final_metrics": record.get("metrics"),
        "steps": steps,
    }
    payload = _trim_trajectory(data)
    summary = _extracted_trial_summary(record, trial_name, analysis, verifier_error=verifier_error)
    payload["token_summary"] = summary.get("token_summary")
    payload["truncation"] = summary.get("truncation")
    payload["hit_max_turn"] = summary.get("hit_max_turn")
    payload["hit_max_length"] = summary.get("hit_max_length")
    return payload


def _summarize_litellm(rows: list[dict]) -> dict:
    if not rows:
        return {"count": 0, "calls": []}
    calls = []
    total_in = 0
    total_out = 0
    durations = []
    for r in rows:
        usage = _litellm_usage(r) if isinstance(r, dict) else {}
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        total_tokens = usage.get("total_tokens")
        total_in += prompt
        total_out += completion
        dur = r.get("duration_ms")
        if isinstance(dur, (int, float)):
            durations.append(dur)
        calls.append(
            {
                "ts": r.get("timestamp"),
                "duration_ms": dur,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total_tokens,
                "finish_reason": _litellm_finish_reason(r) if isinstance(r, dict) else None,
                "success": r.get("success"),
            }
        )
    return {
        "count": len(rows),
        "total_prompt_tokens": total_in,
        "total_completion_tokens": total_out,
        "final_total_tokens": calls[-1].get("total_tokens") if calls else None,
        "final_finish_reason": calls[-1].get("finish_reason") if calls else None,
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "calls": calls,
    }


# -- compare ------------------------------------------------------------------


def compare_jobs(repo: JobsRepo, names: list[str]) -> dict:
    rows = []
    for name in names:
        try:
            p = repo.job_dir(name)
        except Exception as exc:  # noqa: BLE001
            rows.append({"name": name, "error": str(exc)})
            continue
        summ = repo.job_summary(p, include_trial_count=False)
        a = p / "analysis"
        score_comp = safe_load_json(a / "score_comparison.json") or {}
        rule_score = _augment_rule_score(
            safe_load_json(a / "traj_analysis" / "score_comparison.json"),
            _read_text(a / "traj_analysis" / "report_comparison.txt"),
        ) or {}
        task_analysis = safe_load_json(a / "report_task_analysis.json") or {}
        tag_analysis_summary = safe_load_json(a / "instance_analysis" / "summary.json") or {}
        report_failed = safe_load_json(a / "report_failed.json") or {}
        report_resolved = safe_load_json(a / "report_resolved.json") or {}
        trials = [
            {
                "resolved": trial.get("resolved"),
                "turn_count": trial.get("turn_count"),
                "duration_sec": trial.get("duration_sec"),
                "n_tokens": trial.get("n_tokens"),
                "hit_max_turn": trial.get("hit_max_turn"),
                "hit_max_length": trial.get("hit_max_length"),
                "truncation": trial.get("truncation"),
                "exception_type": trial.get("exception_type"),
            }
            for trial in repo.list_trials(name)
        ]
        rows.append(
            {
                "name": name,
                "summary": summ,
                "primary_axes": {
                    "failed": (report_failed.get("primary_distribution") or {}).get("rows") or [],
                    "resolved": (report_resolved.get("primary_distribution") or {}).get("rows")
                    or [],
                },
                "axis_distributions_failed": report_failed.get("axis_distributions") or {},
                "axis_distributions_resolved": report_resolved.get("axis_distributions") or {},
                "score_comparison": score_comp,
                "rule_score": rule_score,
                "task_analysis": task_analysis,
                "tag_analysis_summary": tag_analysis_summary,
                "trials": trials,
            }
        )
    return {"jobs": rows}


# -- HTTP server --------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "HarborWebUI/1.0"
    repo: JobsRepo  # set on subclass below

    # silence default access logging; route through logging
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        LOG.debug("%s - %s", self.address_string(), fmt % args)

    # ---- helpers

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200, ctype: str = "text/plain") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str) -> None:
        if not path.exists():
            self._send_text("not found", status=404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ---- routing

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path = unquote(url.path)
        try:
            if path == "/" or path == "/index.html":
                self._send_file(STATIC_DIR / "index.html", "text/html")
                return
            if path == "/app.js":
                self._send_file(STATIC_DIR / "app.js", "application/javascript")
                return
            if path == "/styles.css":
                self._send_file(STATIC_DIR / "styles.css", "text/css")
                return
            if path == "/favicon.svg":
                self._send_file(STATIC_DIR / "favicon.svg", "image/svg+xml")
                return
            if path.startswith("/api/"):
                self._handle_api(path, parse_qs(url.query))
                return
            self._send_text("not found", status=404)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, status=403)
        except Exception:  # noqa: BLE001
            # Log the full trace server-side, but never leak internals (paths,
            # stack frames) to unauthenticated clients.
            LOG.error("api error: %s", traceback.format_exc())
            self._send_json({"error": "internal server error"}, status=500)

    def _handle_api(self, path: str, qs: dict[str, list[str]]) -> None:
        if path == "/api/jobs":
            detail = (qs.get("detail") or ["full"])[0]
            if detail not in {"lite", "full"}:
                detail = "full"
            self._send_json({"jobs": self.repo.list_jobs(detail=detail), "jobs_dir": str(self.repo.jobs_dir)})
            return
        if path == "/api/overview":
            self._send_json(self._overview())
            return
        if path == "/api/compare":
            names = qs.get("name", [])
            self._send_json(compare_jobs(self.repo, names))
            return
        m = re.match(r"^/api/jobs/([^/]+)$", path)
        if m:
            self._send_json(self.repo.job_detail(m.group(1)))
            return
        m = re.match(r"^/api/jobs/([^/]+)/trials$", path)
        if m:
            self._send_json({"trials": self.repo.list_trials(m.group(1))})
            return
        m = re.match(r"^/api/jobs/([^/]+)/trials/([^/]+)$", path)
        if m:
            self._send_json(self.repo.trial_detail(m.group(1), m.group(2)))
            return
        m = re.match(r"^/api/jobs/([^/]+)/trials/([^/]+)/trajectory$", path)
        if m:
            kind = (qs.get("kind") or ["agent"])[0]
            self._send_json(self.repo.trajectory(m.group(1), m.group(2), kind))
            return
        m = re.match(r"^/api/jobs/([^/]+)/rule_score_instances$", path)
        if m:
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            limit = max(1, min(limit, 1000))
            kind = (qs.get("kind") or ["resolved"])[0]
            self._send_json(self.repo.rule_score_instances(m.group(1), kind, limit))
            return
        self._send_json({"error": f"no route for {path}"}, status=404)

    def _overview(self) -> dict:
        jobs = self.repo.list_jobs(detail="full")
        scaffolds: Counter[str] = Counter()
        datasets: Counter[str] = Counter()
        models: Counter[str] = Counter()
        analyzed = 0
        total_resolved = 0
        total_failed = 0
        for j in jobs:
            if j.get("scaffold"):
                scaffolds[j["scaffold"]] += 1
            if j.get("dataset"):
                datasets[j["dataset"]] += 1
            if j.get("model"):
                models[j["model"]] += 1
            an = j.get("analysis") or {}
            if j.get("has_analysis"):
                analyzed += 1
                total_resolved += an.get("resolved_total") or 0
                total_failed += an.get("failed_total") or 0
        return {
            "job_count": len(jobs),
            "analyzed_count": analyzed,
            "scaffolds": scaffolds.most_common(),
            "datasets": datasets.most_common(),
            "models": models.most_common(),
            "total_resolved": total_resolved,
            "total_failed": total_failed,
            "overall_resolve_rate": percent(total_resolved, total_resolved + total_failed),
        }


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    raise TypeError(f"unserialisable {type(obj).__name__}")


def make_handler(repo: JobsRepo) -> type[Handler]:
    class _H(Handler):
        pass

    _H.repo = repo
    return _H


def main() -> None:
    parser = argparse.ArgumentParser(description="Harbor Job Dashboard server")
    # Default to loopback: the server has no auth/TLS, so binding 0.0.0.0 would
    # expose all job results/trajectories to anyone on the network. Opt into a
    # wider bind explicitly via --host or HARBOR_WEBUI_HOST (put a reverse proxy
    # with auth in front for public exposure).
    parser.add_argument("--host", default=os.environ.get("HARBOR_WEBUI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path(os.environ.get("HARBOR_JOBS_DIR", str(DEFAULT_JOBS))),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    jobs_dir = args.jobs_dir.resolve()
    if not jobs_dir.exists():
        LOG.warning("jobs dir %s does not exist; UI will be empty", jobs_dir)
    repo = JobsRepo(jobs_dir)

    def _warm_caches() -> None:
        # Pre-populate per-job caches so the first browser request is fast
        # instead of paying the full trial scan on demand.
        try:
            for p in repo.list_job_dirs():
                try:
                    repo.exception_stats(p)
                    repo.list_trials(p.name)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("warm cache failed for %s: %s", p.name, exc)
            LOG.info("cache warm-up complete")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("cache warm-up aborted: %s", exc)

    threading.Thread(target=_warm_caches, name="cache-warmup", daemon=True).start()

    handler_cls = make_handler(repo)
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    LOG.info("Harbor webui serving %s on http://%s:%s", jobs_dir, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
