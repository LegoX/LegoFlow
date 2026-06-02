#!/usr/bin/env python3
"""Unified local HTML dashboard for March SWE-gen progress.

The dashboard is intentionally dependency-free: it scans the local task
directories, writes one auto-refreshing HTML file, and keeps a small JSONL
state file for 1h/24h deltas.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import tiktoken

try:
    import tomllib
except Exception:  # pragma: no cover - Python < 3.11 fallback path
    tomllib = None  # type: ignore[assignment]


BJT = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).resolve().parent
HOME_DIR = Path(os.environ.get("SWEGEN_HOME", str(Path.home()))).expanduser()
DASHBOARD_ROOT = Path(os.environ.get("SWEGEN_DASHBOARD_ROOT", str(SCRIPT_DIR))).expanduser().resolve()
REPO_ROOT = Path(os.environ.get("SWEGEN_DATA_ROOT", str(HOME_DIR / "SWE-gen"))).expanduser().resolve()
ROOT = Path(os.environ.get("SWEGEN_TASK_ROOT", str(REPO_ROOT / "tasks" / "March"))).expanduser().resolve()
PR_DIR = Path(os.environ.get("SWEGEN_PR_DIR", str(REPO_ROOT / "collected_prs"))).expanduser().resolve()
DEFAULT_HTML = DASHBOARD_ROOT / "site" / "index.html"
DEFAULT_STATE = DASHBOARD_ROOT / "memory" / ".progress_monitor_all_state.jsonl"
DEFAULT_CACHE = DASHBOARD_ROOT / "memory" / ".progress_monitor_all_cache.json"
CACHE_VERSION = 2

TRAJ_DIR = Path(
    os.environ.get(
        "SWEGEN_TRAJ_DIR",
        "/home/ywxzml3j/ywxzml3juser57/LLaMA-Factory/data/chaofan_jierun_traj",
    )
).expanduser()

SCAFFOLD_ALIASES: dict[str, str] = {
    "cc": "Claude Code",
    "oc": "OpenCode",
    "t2": "Terminus-2",
    "oh": "OpenHands-AI",
    "ohsdk": "OpenHands SDK",
    "oh_sdk": "OpenHands SDK",
}

SCORE_FIELDS = ("composite_score", "efficiency_score", "style_score",
                "tool_mastery_score", "completion_score", "precision_score")

_tiktoken_enc = tiktoken.get_encoding("cl100k_base")

LANGS: list[tuple[str, str, str, str]] = [
    ("c", "C", "c-cc", "c"),
    ("cpp", "C++", "cpp-cc", "cpp"),
    ("go", "Go", "go-cc", "go"),
    ("java", "Java", "java-cc", "java"),
    ("js", "JavaScript", "js-cc", "javascript"),
    ("py", "Python", "py-cc", "python"),
    ("rust", "Rust", "rust-cc", "rust"),
    ("ts", "TypeScript", "ts-cc", "typescript"),
]

CODE_EXTS_BY_LANG = {
    "c": {".c", ".h"},
    "cpp": {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ipp", ".inl", ".tpp"},
    "go": {".go"},
    "java": {".java"},
    "js": {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"},
    "py": {".py", ".pyi", ".pyw"},
    "rust": {".rs"},
    "ts": {".ts", ".tsx", ".cts", ".mts", ".js", ".jsx", ".mjs", ".cjs"},
}

LANGUAGE_TAG_ALIASES = {
    "c": {"c"},
    "cpp": {"cpp", "c++", "cplusplus", "cxx", "c/c++", "c plus plus", "c-plus-plus"},
    "go": {"go", "golang"},
    "java": {"java"},
    "js": {"js", "javascript", "ecmascript"},
    "py": {"py", "python"},
    "rust": {"rust", "rs"},
    "ts": {"ts", "typescript"},
}


def now_bjt() -> datetime:
    return datetime.now(BJT)


def read_nonempty_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return [line.strip() for line in f if line.strip()]


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def append_state(path: Path, snap: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False, sort_keys=True) + "\n")


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": CACHE_VERSION, "files": {}, "langs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": CACHE_VERSION, "files": {}, "langs": {}}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "files": {}, "langs": {}}
    data.setdefault("files", {})
    data.setdefault("langs", {})
    return data


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(cache, ensure_ascii=False, sort_keys=True))


def stat_sig(path: Path) -> dict[str, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def read_nonempty_lines_cached(path: Path, cache: dict[str, Any], *, keep_items: bool) -> list[str]:
    sig = stat_sig(path)
    if sig is None:
        return []

    key = str(path)
    files = cache.setdefault("files", {})
    prev = files.get(key)
    if isinstance(prev, dict) and prev.get("sig") == sig:
        if keep_items and isinstance(prev.get("items"), list):
            return [str(item) for item in prev["items"]]
        if not keep_items and isinstance(prev.get("count"), int):
            return [""] * int(prev["count"])

    lines = read_nonempty_lines(path)
    files[key] = {"sig": sig, "count": len(lines)}
    if keep_items:
        files[key]["items"] = lines
    return lines


def task_sig(task_dir: Path) -> dict[str, dict[str, int] | None]:
    return {
        "task_toml": stat_sig(task_dir / "task.toml"),
        "fix_patch": stat_sig(task_dir / "solution" / "fix.patch"),
    }


def find_prev_snapshot(rows: list[dict[str, Any]], hours: float) -> dict[str, Any] | None:
    target = now_bjt() - timedelta(hours=hours)
    best: dict[str, Any] | None = None
    for row in rows:
        try:
            dt = datetime.fromisoformat(str(row["ts"]))
        except Exception:
            continue
        if dt <= target:
            best = row
    return best


def unique_preserve_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def is_language_tag(lang: str, tag: str) -> bool:
    return re.sub(r"\s+", " ", tag.strip().casefold()) in LANGUAGE_TAG_ALIASES.get(lang, set())


def build_dir_index(lang_dir: Path) -> dict[str, Path]:
    if not lang_dir.exists():
        return {}
    out: dict[str, Path] = {}
    with os.scandir(lang_dir) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                out[entry.name.lower()] = Path(entry.path)
    return out


def count_top_level_dirs(lang_dir: Path) -> int:
    if not lang_dir.exists():
        return 0
    count = 0
    with os.scandir(lang_dir) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                count += 1
    return count


def locate_task_dir(lang_dir: Path, task_id: str, lower_index: dict[str, Path]) -> Path | None:
    direct = lang_dir / task_id
    if direct.exists() and direct.is_dir():
        return direct
    return lower_index.get(task_id.lower())


def is_code_file(lang: str, file_path: str) -> bool:
    return Path(file_path).suffix.lower() in CODE_EXTS_BY_LANG.get(lang, set())


def count_patch_stats_code_only(patch_path: Path, lang: str) -> tuple[int, int, int]:
    """Return changed lines, hunks, and files for language code files only."""
    if not patch_path.exists():
        return (0, 0, 0)

    line_count = 0
    hunk_count = 0
    seen_files: set[str] = set()
    current_is_code = False

    with patch_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = re.match(r"^diff --git a/(.*?) b/(.*)$", line)
            if m:
                a_path = m.group(1)
                b_path = m.group(2)
                chosen = b_path if b_path != "/dev/null" else a_path
                current_is_code = is_code_file(lang, chosen)
                if current_is_code:
                    seen_files.add(chosen)
                continue

            if current_is_code and line.startswith("@@"):
                hunk_count += 1
            elif current_is_code and line.startswith("+") and not line.startswith("+++"):
                line_count += 1
            elif current_is_code and line.startswith("-") and not line.startswith("---"):
                line_count += 1

    return (line_count, hunk_count, len(seen_files))


SCORE_RE = re.compile(r'(?m)^difficulty_score\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$')
LABEL_RE = re.compile(r'(?m)^difficulty_label\s*=\s*"([^"]*)"\s*$')
TAGS_RE = re.compile(r"(?ms)^tags\s*=\s*\[(.*?)\]")
STRING_RE = re.compile(r'"([^"]*)"')


def parse_task_toml_metadata(toml_path: Path) -> dict[str, Any]:
    if not toml_path.exists():
        return {}

    text = toml_path.read_text(encoding="utf-8", errors="replace")
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
            metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
            scoring = data.get("scoring", {}) if isinstance(data, dict) else {}
            out: dict[str, Any] = {}
            if isinstance(scoring, dict):
                if "difficulty_score" in scoring:
                    out["difficulty_score"] = float(scoring["difficulty_score"])
                if "difficulty_label" in scoring:
                    out["difficulty_label"] = str(scoring["difficulty_label"])
            if isinstance(metadata, dict) and isinstance(metadata.get("tags"), list):
                out["tags"] = [str(t) for t in metadata["tags"] if str(t)]
            return out
        except Exception:
            pass

    out = {}
    m = SCORE_RE.search(text)
    if m:
        try:
            out["difficulty_score"] = float(m.group(1))
        except ValueError:
            pass
    m = LABEL_RE.search(text)
    if m:
        out["difficulty_label"] = m.group(1)
    m = TAGS_RE.search(text)
    if m:
        out["tags"] = [t for t in STRING_RE.findall(m.group(1)) if t]
    return out


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def score_stats(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "min": 0.0, "p25": 0.0, "median": 0.0, "mean": 0.0, "p75": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "min": min(values),
        "p25": percentile(values, 0.25),
        "median": percentile(values, 0.50),
        "mean": sum(values) / len(values),
        "p75": percentile(values, 0.75),
        "max": max(values),
    }


def score_bins(values: list[float]) -> dict[str, int]:
    bins = {"<=3": 0, "3.1-5": 0, "5.1-7": 0, "7.1-8": 0, ">8": 0}
    for score in values:
        if score <= 3.0:
            bins["<=3"] += 1
        elif score <= 5.0:
            bins["3.1-5"] += 1
        elif score <= 7.0:
            bins["5.1-7"] += 1
        elif score <= 8.0:
            bins["7.1-8"] += 1
        else:
            bins[">8"] += 1
    return bins


def collect_task_stats(task_dir: Path, lang: str) -> dict[str, Any]:
    lines, hunks, files = count_patch_stats_code_only(task_dir / "solution" / "fix.patch", lang)
    meta = parse_task_toml_metadata(task_dir / "task.toml")
    task_tags = sorted(
        {
            str(t).strip()
            for t in meta.get("tags", [])
            if str(t).strip() and not is_language_tag(lang, str(t))
        }
    )
    return {
        "patch_lines": lines,
        "patch_hunks": hunks,
        "patch_files": files,
        "difficulty_score": float(meta["difficulty_score"]) if "difficulty_score" in meta else None,
        "difficulty_label": str(meta["difficulty_label"]) if meta.get("difficulty_label") else None,
        "tags": task_tags,
    }


def empty_lang_aggregate() -> dict[str, Any]:
    return {
        "patch_lines": 0,
        "patch_hunks": 0,
        "patch_files": 0,
        "patch_denominator": 0,
        "difficulty_scores": [],
        "difficulty_labels": {},
        "tags": {},
        "tasks_with_tags": 0,
    }


def copy_lang_aggregate(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "patch_lines": int(raw.get("patch_lines") or 0),
        "patch_hunks": int(raw.get("patch_hunks") or 0),
        "patch_files": int(raw.get("patch_files") or 0),
        "patch_denominator": int(raw.get("patch_denominator") or 0),
        "difficulty_scores": [float(v) for v in raw.get("difficulty_scores", [])],
        "difficulty_labels": {str(k): int(v) for k, v in raw.get("difficulty_labels", {}).items()},
        "tags": {str(k): int(v) for k, v in raw.get("tags", {}).items()},
        "tasks_with_tags": int(raw.get("tasks_with_tags") or 0),
    }


def add_task_stats_to_aggregate(agg: dict[str, Any], stats: dict[str, Any]) -> None:
    agg["patch_denominator"] += 1
    agg["patch_lines"] += int(stats.get("patch_lines") or 0)
    agg["patch_hunks"] += int(stats.get("patch_hunks") or 0)
    agg["patch_files"] += int(stats.get("patch_files") or 0)

    if stats.get("difficulty_score") is not None:
        agg["difficulty_scores"].append(float(stats["difficulty_score"]))
    if stats.get("difficulty_label"):
        label = str(stats["difficulty_label"])
        agg["difficulty_labels"][label] = int(agg["difficulty_labels"].get(label, 0)) + 1

    task_tags = [str(t) for t in stats.get("tags", []) if str(t)]
    if task_tags:
        agg["tasks_with_tags"] += 1
        for tag in task_tags:
            agg["tags"][tag] = int(agg["tags"].get(tag, 0)) + 1


def collect_lang(
    lang: str,
    display: str,
    lang_dir_name: str,
    pr_prefix: str,
    cache: dict[str, Any],
    force_full_scan: bool,
) -> dict[str, Any]:
    lang_dir = ROOT / lang_dir_name
    pr_count = len(read_nonempty_lines_cached(PR_DIR / f"{pr_prefix}_pr_ids.txt", cache, keep_items=False))
    raw_task_ids = read_nonempty_lines_cached(lang_dir / "verifiable_tasks.txt", cache, keep_items=True)
    task_ids = unique_preserve_order(raw_task_ids)
    lang_cache = cache.setdefault("langs", {}).setdefault(lang, {})
    cache_hits = 0
    cache_misses = 0
    old_task_ids = lang_cache.get("task_ids")
    old_aggregate = lang_cache.get("aggregate")
    lower_index: dict[str, Path] | None = None
    dir_count_cache_hit = False

    can_increment = (
        not force_full_scan
        and isinstance(old_task_ids, list)
        and isinstance(old_aggregate, dict)
        and len(task_ids) >= len(old_task_ids)
        and task_ids[: len(old_task_ids)] == [str(t) for t in old_task_ids]
    )

    def locate_current_task_dir(task_id: str) -> Path | None:
        nonlocal lower_index
        direct = lang_dir / task_id
        if direct.exists() and direct.is_dir():
            return direct
        if lower_index is None:
            lower_index = build_dir_index(lang_dir)
        return lower_index.get(task_id.lower())

    dir_sig = stat_sig(lang_dir)
    cached_dir = lang_cache.get("dir")
    if can_increment and isinstance(cached_dir, dict) and cached_dir.get("sig") == dir_sig:
        processed_count = int(cached_dir.get("processed_count") or 0)
        dir_count_cache_hit = True
    elif can_increment:
        processed_count = count_top_level_dirs(lang_dir)
    else:
        lower_index = build_dir_index(lang_dir)
        processed_count = len(lower_index)

    if can_increment:
        aggregate = copy_lang_aggregate(old_aggregate)
        ids_to_collect = task_ids[len(old_task_ids) :]
        cache_hits = len(old_task_ids)
    else:
        aggregate = empty_lang_aggregate()
        ids_to_collect = task_ids
        legacy_task_cache = lang_cache.get("tasks") if not force_full_scan else None
        if isinstance(legacy_task_cache, dict):
            for task_id in task_ids:
                task_dir = locate_current_task_dir(task_id)
                if task_dir is None:
                    continue
                cached = legacy_task_cache.get(task_dir.name)
                if isinstance(cached, dict) and isinstance(cached.get("stats"), dict):
                    add_task_stats_to_aggregate(aggregate, cached["stats"])
                    cache_hits += 1
                else:
                    stats = collect_task_stats(task_dir, lang)
                    add_task_stats_to_aggregate(aggregate, stats)
                    cache_misses += 1
            ids_to_collect = []
        lang_cache.pop("tasks", None)

    for task_id in ids_to_collect:
        task_dir = locate_current_task_dir(task_id)
        if task_dir is None:
            continue
        stats = collect_task_stats(task_dir, lang)
        add_task_stats_to_aggregate(aggregate, stats)
        cache_misses += 1

    lang_cache["dir"] = {"sig": dir_sig, "processed_count": processed_count}
    lang_cache["task_ids"] = task_ids
    lang_cache["aggregate"] = aggregate
    valid_count = len(task_ids)
    patch_denominator = int(aggregate["patch_denominator"])
    denom = patch_denominator or 1
    difficulty_scores = [float(v) for v in aggregate["difficulty_scores"]]
    difficulty_labels = Counter({str(k): int(v) for k, v in aggregate["difficulty_labels"].items()})
    tags = Counter({str(k): int(v) for k, v in aggregate["tags"].items()})
    tasks_with_tags = int(aggregate["tasks_with_tags"])
    return {
        "lang": lang,
        "display": display,
        "pr_count": pr_count,
        "valid_count": valid_count,
        "processed_count": processed_count,
        "success_rate": (valid_count / processed_count * 100.0) if processed_count else 0.0,
        "patch": {
            "avg_lines": int(aggregate["patch_lines"]) / denom,
            "avg_hunks": int(aggregate["patch_hunks"]) / denom,
            "avg_files": int(aggregate["patch_files"]) / denom,
            "denominator": patch_denominator,
        },
        "difficulty_scores": difficulty_scores,
        "difficulty_stats": score_stats(difficulty_scores),
        "difficulty_bins": score_bins(difficulty_scores),
        "difficulty_labels": dict(difficulty_labels),
        "tags": dict(tags.most_common()),
        "tasks_with_tags": tasks_with_tags,
    }


SCRIPTS_DIR = REPO_ROOT / "scripts"
N_CONCURRENT_RE = re.compile(r'N_CONCURRENT="\$\{N_CONCURRENT:-(\d+)\}"')


def parse_n_concurrent_from_script(lang: str) -> int:
    script = SCRIPTS_DIR / f"create_{lang}.sh"
    if not script.exists():
        return 16
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
        m = N_CONCURRENT_RE.search(text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 16


def parse_run_fingerprint(fp: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in fp.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def collect_batch_stats(lang: str, lang_dir_name: str, cache: dict[str, Any]) -> dict[str, Any]:
    batch_dir = ROOT / lang_dir_name / ".swegen-create-batch"
    if not batch_dir.exists():
        return {"params": {}, "status_counts": {}, "error_type_counts": {}}

    batch_cache = cache.setdefault("batch", {}).setdefault(lang, {"files": {}})
    cached_files = batch_cache.setdefault("files", {})

    json_files: list[tuple[str, Path]] = []
    with os.scandir(batch_dir) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".json"):
                json_files.append((entry.name, Path(entry.path)))

    latest_mtime = 0.0
    latest_params: dict[str, str] = {}
    all_file_cases: list[dict[str, tuple[str, str]]] = []

    for fname, fpath in json_files:
        sig = stat_sig(fpath)
        if sig is None:
            continue

        prev = cached_files.get(fname)
        if isinstance(prev, dict) and prev.get("sig") == sig:
            file_cases = prev.get("cases", {})
            file_params = prev.get("params", {})
            file_mtime = float(prev.get("mtime", 0))
        else:
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception:
                continue
            cases = data.get("cases", {})
            file_cases: dict[str, tuple[str, str]] = {}
            for key, case in cases.items():
                status = case.get("status")
                if status is None:
                    continue
                error_type = case.get("error_type", "") if status == "failed" else ""
                file_cases[key] = (status, error_type)

            fp = data.get("run_fingerprint", "")
            file_params = parse_run_fingerprint(fp)
            file_mtime = fpath.stat().st_mtime

            cached_files[fname] = {
                "sig": sig,
                "cases": file_cases,
                "params": file_params,
                "mtime": file_mtime,
            }

        all_file_cases.append(file_cases)
        if float(file_mtime) > latest_mtime:
            latest_mtime = float(file_mtime)
            latest_params = dict(file_params) if isinstance(file_params, dict) else {}

    merged_cases: dict[str, tuple[str, str]] = {}
    for fc in all_file_cases:
        merged_cases.update(fc)

    merged_status: Counter[str] = Counter()
    merged_errors: Counter[str] = Counter()
    for status, error_type in merged_cases.values():
        merged_status[status] += 1
        if status == "failed" and error_type:
            merged_errors[error_type] += 1

    n_concurrent = parse_n_concurrent_from_script(lang)
    latest_params["n_concurrent"] = str(n_concurrent)

    result = {
        "params": latest_params,
        "status_counts": dict(merged_status),
        "error_type_counts": dict(merged_errors),
    }
    batch_cache["merged"] = result
    return result


def parse_traj_filename(filename: str) -> dict[str, str]:
    stem = filename.rsplit(".", 1)[0]
    parts = stem.split("_")
    # Last part is count (numeric)
    count = parts[-1] if parts[-1].isdigit() else "0"
    rest = parts[:-1]
    # First part is agent, second is model
    agent = rest[0] if len(rest) > 0 else "unknown"
    model = rest[1] if len(rest) > 1 else "unknown"
    # Last remaining part is scaffold (may be multi-word like oh_sdk)
    remaining = rest[2:]
    scaffold = ""
    dataset_parts: list[str] = []
    # Try known scaffolds from the end
    for n in (2, 1):
        if len(remaining) >= n:
            candidate = "_".join(remaining[-n:])
            if candidate in SCAFFOLD_ALIASES:
                scaffold = candidate
                dataset_parts = remaining[:-n]
                break
    if not scaffold and remaining:
        scaffold = remaining[-1]
        dataset_parts = remaining[:-1]
    dataset = "_".join(dataset_parts) if dataset_parts else "unknown"
    return {
        "agent": agent,
        "model": model,
        "dataset": dataset,
        "scaffold": scaffold,
        "scaffold_display": SCAFFOLD_ALIASES.get(scaffold, scaffold),
        "count": count,
    }


def collect_single_traj_file(fpath: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "main_count": 0, "subagent_count": 0,
        "main_messages": 0, "subagent_messages": 0,
        "main_tokens": 0, "subagent_tokens": 0,
        "main_tool_calls": 0, "subagent_tool_calls": 0,
        "scores": {f: [] for f in SCORE_FIELDS},
        "think_modes": {},
    }
    with fpath.open("r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            try:
                obj = json.loads(raw_line)
            except Exception:
                continue
            agent_type = obj.get("_agent_type")
            is_sub = agent_type == "subagent"
            prefix = "subagent" if is_sub else "main"
            stats[f"{prefix}_count"] += 1
            msgs = obj.get("messages") or []
            stats[f"{prefix}_messages"] += len(msgs)
            tokens = 0
            tc = 0
            for m in msgs:
                text = (m.get("content") or "") + (m.get("reasoning_content") or "")
                tokens += len(_tiktoken_enc.encode(text, disallowed_special=()))
                if m.get("tool_calls"):
                    tc += len(m["tool_calls"])
                elif m.get("role") == "assistant":
                    content = m.get("content") or ""
                    json_str = None
                    think_end = content.find("</think>")
                    if think_end >= 0:
                        json_str = content[think_end + 8:].strip()
                    elif content.startswith("{"):
                        json_str = content
                    if json_str:
                        try:
                            parsed = json.loads(json_str)
                            if isinstance(parsed.get("commands"), list):
                                tc += len(parsed["commands"])
                        except (json.JSONDecodeError, ValueError):
                            pass
            stats[f"{prefix}_tokens"] += tokens
            stats[f"{prefix}_tool_calls"] += tc
            score = obj.get("_score")
            if isinstance(score, dict):
                for field in SCORE_FIELDS:
                    val = score.get(field)
                    if isinstance(val, (int, float)):
                        stats["scores"][field].append(float(val))
            tm = obj.get("think_mode")
            if tm:
                stats["think_modes"][str(tm)] = stats["think_modes"].get(str(tm), 0) + 1
    return stats


def collect_trajectory_stats(cache: dict[str, Any], force_full_scan: bool) -> list[dict[str, Any]]:
    if not TRAJ_DIR.exists():
        return []
    traj_cache = cache.setdefault("traj", {"files": {}})
    cached_files = traj_cache.setdefault("files", {})
    results: list[dict[str, Any]] = []

    jsonl_files: list[tuple[str, Path]] = []
    with os.scandir(TRAJ_DIR) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".jsonl"):
                jsonl_files.append((entry.name, Path(entry.path)))

    for fname, fpath in sorted(jsonl_files):
        sig = stat_sig(fpath)
        if sig is None:
            continue
        metadata = parse_traj_filename(fname)
        prev = cached_files.get(fname)
        if not force_full_scan and isinstance(prev, dict) and prev.get("sig") == sig:
            file_stats = prev.get("stats", {})
        else:
            file_stats = collect_single_traj_file(fpath)
            cached_files[fname] = {"sig": sig, "metadata": metadata, "stats": file_stats}
        file_size = sig["size"] if sig else 0
        results.append({
            "filename": fname,
            "filepath": str(fpath),
            "file_size": file_size,
            "metadata": metadata,
            "stats": file_stats,
        })
    return results


def delta(current: dict[str, Any], prev: dict[str, Any] | None, lang: str, key: str) -> int | None:
    if prev is None:
        return 0
    try:
        return int(current["langs"][lang][key]) - int(prev["langs"][lang][key])
    except Exception:
        return 0


def collect_dashboard(rows: list[dict[str, Any]], cache: dict[str, Any], force_full_scan: bool) -> dict[str, Any]:
    langs = {
        lang: collect_lang(lang, display, lang_dir, pr_prefix, cache, force_full_scan)
        for lang, display, lang_dir, pr_prefix in LANGS
    }

    batch_stats = {
        lang: collect_batch_stats(lang, lang_dir, cache)
        for lang, _, lang_dir, _ in LANGS
    }
    for lang, bs in batch_stats.items():
        langs[lang]["batch"] = bs

    state_snap = {
        "ts": now_bjt().isoformat(),
        "langs": {
            lang: {
                "pr_count": data["pr_count"],
                "valid_count": data["valid_count"],
                "processed_count": data["processed_count"],
            }
            for lang, data in langs.items()
        },
    }
    rows_with_current = rows + [state_snap]
    prev_1h = find_prev_snapshot(rows, 1.0)
    prev_24h = find_prev_snapshot(rows, 24.0)

    total_pr = sum(d["pr_count"] for d in langs.values())
    total_valid = sum(d["valid_count"] for d in langs.values())
    total_processed = sum(
        int(d.get("batch", {}).get("status_counts", {}).get("success", 0))
        + int(d.get("batch", {}).get("status_counts", {}).get("failed", 0))
        for d in langs.values()
    )
    total_scores: list[float] = []
    global_tags: Counter[str] = Counter()
    label_totals: Counter[str] = Counter()
    for data in langs.values():
        total_scores.extend(data["difficulty_scores"])
        global_tags.update(data["tags"])
        label_totals.update(data["difficulty_labels"])

    for lang, data in langs.items():
        data["delta_1h_pr"] = delta(state_snap, prev_1h, lang, "pr_count")
        data["delta_24h_pr"] = delta(state_snap, prev_24h, lang, "pr_count")
        data["delta_1h_valid"] = delta(state_snap, prev_1h, lang, "valid_count")
        data["delta_24h_valid"] = delta(state_snap, prev_24h, lang, "valid_count")

    return {
        "ts": state_snap["ts"],
        "state_snap": state_snap,
        "langs": langs,
        "totals": {
            "pr_count": total_pr,
            "valid_count": total_valid,
            "processed_count": total_processed,
            "success_rate": (total_valid / total_processed * 100.0) if total_processed else 0.0,
            "delta_1h_pr": sum_delta(langs, "delta_1h_pr"),
            "delta_24h_pr": sum_delta(langs, "delta_24h_pr"),
            "delta_1h_valid": sum_delta(langs, "delta_1h_valid"),
            "delta_24h_valid": sum_delta(langs, "delta_24h_valid"),
            "difficulty_stats": score_stats(total_scores),
            "difficulty_labels": dict(label_totals),
            "global_tags": dict(global_tags.most_common(30)),
        },
        "traj": collect_trajectory_stats(cache, force_full_scan),
        "history_count": len(rows_with_current),
    }


def sum_delta(langs: dict[str, dict[str, Any]], key: str) -> int | None:
    values = [data.get(key) for data in langs.values()]
    return int(sum(int(v) for v in values))


def fmt_int(value: int | float | None) -> str:
    if value is None:
        return "N/A"
    return f"{int(value):,}"


def fmt_delta(value: int | None) -> str:
    if value is None:
        return "N/A"
    if value > 0:
        return f"+{value:,}"
    return f"{value:,}"


def fmt_float(value: float | int, digits: int = 2) -> str:
    return f"{float(value):,.{digits}f}"


def pct_bar(value: float, label: str = "") -> str:
    width = max(0.0, min(100.0, value))
    label_html = html.escape(label or f"{value:.1f}%")
    return (
        '<div class="bar"><div class="bar-fill" style="width: '
        f'{width:.2f}%"></div><span>{label_html}</span></div>'
    )


def label_count(data: dict[str, Any], label: str) -> int:
    return int(data.get("difficulty_labels", {}).get(label, 0))


def render_label_bar(data: dict[str, Any]) -> str:
    easy = label_count(data, "easy")
    medium = label_count(data, "medium")
    hard = label_count(data, "hard")
    total = easy + medium + hard
    if total <= 0:
        return '<div class="stacked empty">无数据</div>'
    parts = []
    for name, count, cls in (("easy", easy, "easy"), ("medium", medium, "medium"), ("hard", hard, "hard")):
        width = count / total * 100.0
        if count:
            parts.append(f'<div class="seg {cls}" style="width:{width:.3f}%" title="{name}: {count}"></div>')
    return f'<div class="stacked">{"".join(parts)}</div><div class="mini">{easy} / {medium} / {hard}</div>'


def render_tags(tags: dict[str, int], denominator: int, limit: int = 20) -> str:
    if not tags or denominator <= 0:
        return '<div class="muted">无 tags 数据</div>'
    rows = []
    max_count = max(tags.values()) if tags else 1
    for tag, count in list(tags.items())[:limit]:
        percent = count / denominator * 100.0
        width = count / max_count * 100.0
        rows.append(
            '<div class="tag-row">'
            f'<span class="tag-name">{html.escape(tag)}</span>'
            '<span class="tag-track">'
            f'<span class="tag-fill" style="width:{width:.2f}%"></span>'
            '</span>'
            f'<span class="tag-count">{count:,} ({percent:.1f}%)</span>'
            '</div>'
        )
    return "\n".join(rows)


def render_trajectory_html(traj_data: list[dict[str, Any]]) -> str:
    if not traj_data:
        return '<div class="muted">无轨迹数据</div>'

    total_files = len(traj_data)
    total_lines = sum(d["stats"]["main_count"] + d["stats"]["subagent_count"] for d in traj_data)
    total_main_msgs = sum(d["stats"]["main_messages"] for d in traj_data)
    total_main_count = sum(d["stats"]["main_count"] for d in traj_data)
    all_composite = []
    for d in traj_data:
        all_composite.extend(d["stats"].get("scores", {}).get("composite_score", []))
    avg_msgs = total_main_msgs / total_main_count if total_main_count else 0
    avg_score = sum(all_composite) / len(all_composite) if all_composite else 0

    # KPI cards
    kpis = (
        '<section class="grid kpis">'
        f'<div class="card"><div class="label">轨迹文件数</div><div class="value">{total_files}</div></div>'
        f'<div class="card"><div class="label">轨迹总条数</div><div class="value">{total_lines:,}</div></div>'
        f'<div class="card"><div class="label">平均消息轮数</div><div class="value">{avg_msgs:.1f}</div></div>'
        f'<div class="card"><div class="label">平均 composite_score</div><div class="value">{avg_score:.4f}</div>'
        f'<div class="sub">基于 {len(all_composite):,} 条有分数的轨迹</div></div>'
        '</section>'
    )

    # Overview table
    overview_rows = []
    for d in traj_data:
        meta = d["metadata"]
        st = d["stats"]
        mc = st["main_count"]
        size_mb = d["file_size"] / 1024 / 1024
        avg_m = st["main_messages"] / mc if mc else 0
        avg_tok = st["main_tokens"] / mc if mc else 0
        avg_tc = st["main_tool_calls"] / mc if mc else 0
        scores = st.get("scores", {}).get("composite_score", [])
        avg_s = sum(scores) / len(scores) if scores else None
        score_cell = f"{avg_s:.4f}" if avg_s is not None else "—"
        overview_rows.append(
            "<tr>"
            f"<td>{html.escape(meta['dataset'])}</td>"
            f"<td>{html.escape(meta['scaffold_display'])}</td>"
            f"<td><code>{html.escape(meta['model'])}</code></td>"
            f"<td>{html.escape(meta['agent'])}</td>"
            f"<td>{mc:,}</td>"
            f"<td>{size_mb:.0f} MB</td>"
            f"<td>{avg_m:.1f}</td><td>{avg_tok:,.0f}</td><td>{avg_tc:.1f}</td>"
            f"<td>{score_cell}</td>"
            "</tr>"
        )

    overview_table = (
        '<section class="panel"><h2>轨迹文件总览</h2><div class="table-wrap"><table>'
        '<thead><tr><th>数据集</th><th>脚手架</th><th>模型</th><th>Owner</th>'
        '<th>轨迹数</th><th>文件大小</th>'
        '<th>平均轮数</th><th>平均 Token</th><th>平均 Tool Calls</th><th>平均 Score</th></tr></thead>'
        f'<tbody>{"".join(overview_rows)}</tbody></table></div></section>'
    )

    # Quality scores table (only files with scores)
    quality_rows = []
    for d in traj_data:
        scores = d["stats"].get("scores", {})
        if not scores.get("composite_score"):
            continue
        meta = d["metadata"]
        row_cells = [f"<td>{html.escape(meta['dataset'])}</td>", f"<td>{html.escape(meta['scaffold_display'])}</td>"]
        for field in SCORE_FIELDS:
            vals = scores.get(field, [])
            avg = sum(vals) / len(vals) if vals else 0
            row_cells.append(f"<td>{avg:.4f}</td>")
        quality_rows.append(f"<tr>{''.join(row_cells)}</tr>")

    quality_table = ""
    if quality_rows:
        quality_table = (
            '<section class="panel"><h2>质量评分统计</h2><div class="table-wrap"><table>'
            '<thead><tr><th>数据集</th><th>脚手架</th><th>composite</th><th>efficiency</th>'
            '<th>style</th><th>tool_mastery</th><th>completion</th><th>precision</th></tr></thead>'
            f'<tbody>{"".join(quality_rows)}</tbody></table></div></section>'
        )

    # By dataset comparison
    dataset_agg: dict[str, dict[str, Any]] = {}
    for d in traj_data:
        key = d["metadata"]["dataset"]
        agg = dataset_agg.setdefault(key, {"count": 0, "msgs": 0, "tokens": 0, "tc": 0, "scores": []})
        agg["count"] += d["stats"]["main_count"]
        agg["msgs"] += d["stats"]["main_messages"]
        agg["tokens"] += d["stats"]["main_tokens"]
        agg["tc"] += d["stats"]["main_tool_calls"]
        agg["scores"].extend(d["stats"].get("scores", {}).get("composite_score", []))
    dataset_rows = []
    for name, agg in sorted(dataset_agg.items()):
        c = agg["count"] or 1
        avg_s = sum(agg["scores"]) / len(agg["scores"]) if agg["scores"] else None
        score_cell = f"{avg_s:.4f}" if avg_s else "—"
        dataset_rows.append(
            f"<tr><td><strong>{html.escape(name)}</strong></td>"
            f"<td>{agg['count']:,}</td><td>{agg['msgs']/c:.1f}</td>"
            f"<td>{agg['tokens']/c:,.0f}</td><td>{agg['tc']/c:.1f}</td>"
            f"<td>{score_cell}</td></tr>"
        )
    dataset_table = (
        '<section class="panel"><h2>按数据集对比</h2><div class="table-wrap"><table>'
        '<thead><tr><th>数据集</th><th>总轨迹数</th><th>平均轮数</th><th>平均 Token</th>'
        '<th>平均 Tool Calls</th><th>平均 Score</th></tr></thead>'
        f'<tbody>{"".join(dataset_rows)}</tbody></table></div></section>'
    )

    # File path directory
    path_by_dataset: dict[str, list[dict[str, Any]]] = {}
    for d in traj_data:
        ds = d["metadata"]["dataset"]
        path_by_dataset.setdefault(ds, []).append(d)
    path_rows = []
    for ds in sorted(path_by_dataset.keys()):
        files = path_by_dataset[ds]
        for f in sorted(files, key=lambda x: (x["metadata"]["scaffold"], x["metadata"]["agent"])):
            meta = f["metadata"]
            size_mb = f["file_size"] / 1024 / 1024
            count = f["stats"]["main_count"] + f["stats"]["subagent_count"]
            path_rows.append(
                f"<tr><td>{html.escape(ds)}</td>"
                f"<td>{html.escape(meta['scaffold_display'])}</td>"
                f"<td>{html.escape(meta['agent'])}</td>"
                f"<td><code>{html.escape(f['filepath'])}</code></td>"
                f"<td>{size_mb:.0f} MB</td><td>{count:,}</td></tr>"
            )
    path_table = (
        '<section class="panel"><h2>源数据路径目录</h2><div class="table-wrap"><table>'
        '<thead><tr><th>数据集</th><th>脚手架</th><th>Owner</th><th>文件路径</th>'
        '<th>大小</th><th>条数</th></tr></thead>'
        f'<tbody>{"".join(path_rows)}</tbody></table></div></section>'
    )

    # Method explanation
    method_section = (
        '<section class="panel"><h2>统计方法说明</h2><div class="method-grid">'
        '<div class="method-card"><h3>平均轮数 / Token / Tool Calls</h3>'
        '<p><strong>平均轮数</strong>：每条轨迹的 <code>messages</code> 数组长度的平均值。</p>'
        '<p><strong>平均 Token</strong>：使用 tiktoken cl100k_base tokenizer 对所有 message 的 content + reasoning_content 精确编码计数的平均值。</p>'
        '<p><strong>平均 Tool Calls</strong>：assistant 消息中 <code>tool_calls</code> 数组长度之和的平均值。对 Terminus-2 脚手架，统计 assistant 消息 JSON content 中 <code>commands</code> 数组的长度。</p></div>'
        '<div class="method-card"><h3>质量评分</h3>'
        '<p><code>composite_score</code>（0-1）由五个维度加权：efficiency（效率）、style（风格）、tool_mastery（工具掌握）、completion（完成度）、precision（精确度）。</p>'
        '<p>仅部分文件包含 <code>_score</code> 字段，无分数的文件显示 "—"。</p></div>'
        '</div></section>'
    )

    return kpis + overview_table + quality_table + dataset_table + path_table + method_section


def render_html(data: dict[str, Any], refresh_seconds: int, output_path: Path) -> str:
    updated = datetime.fromisoformat(data["ts"]).strftime("%Y-%m-%d %H:%M:%S BJT")
    next_refresh = (now_bjt() + timedelta(seconds=refresh_seconds)).strftime("%Y-%m-%d %H:%M:%S BJT")
    totals = data["totals"]
    langs = data["langs"]

    progress_rows = []
    patch_rows = []
    difficulty_rows = []
    score_rows = []
    tag_sections = []
    params_rows = []
    failure_rows = []
    traj_html = render_trajectory_html(data.get("traj", []))

    for lang, _, _, _ in LANGS:
        row = langs[lang]
        batch = row.get("batch", {})
        params = batch.get("params", {})
        sc = batch.get("status_counts", {})
        ec = batch.get("error_type_counts", {})
        b_success = int(sc.get("success", 0))
        b_failed = int(sc.get("failed", 0))
        b_total = b_success + b_failed
        batch_rate = (row['valid_count'] / b_total * 100.0) if b_total else 0.0
        progress_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(row['display'])}</strong><span class='code'>{lang}</span></td>"
            f"<td>{fmt_int(row['pr_count'])}</td>"
            f"<td class='delta'>{fmt_delta(row['delta_1h_pr'])}</td>"
            f"<td class='delta'>{fmt_delta(row['delta_24h_pr'])}</td>"
            f"<td>{fmt_int(row['valid_count'])}</td>"
            f"<td class='delta'>{fmt_delta(row['delta_1h_valid'])}</td>"
            f"<td class='delta'>{fmt_delta(row['delta_24h_valid'])}</td>"
            f"<td>{fmt_int(b_total)}</td>"
            f"<td>{pct_bar(batch_rate)}</td>"
            "</tr>"
        )
        patch_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(row['display'])}</strong></td>"
            f"<td>{fmt_int(row['valid_count'])}</td>"
            f"<td>{fmt_float(row['patch']['avg_lines'])}</td>"
            f"<td>{fmt_float(row['patch']['avg_hunks'])}</td>"
            f"<td>{fmt_float(row['patch']['avg_files'])}</td>"
            "</tr>"
        )
        difficulty_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(row['display'])}</strong></td>"
            f"<td>{render_label_bar(row)}</td>"
            f"<td>{fmt_int(label_count(row, 'easy'))}</td>"
            f"<td>{fmt_int(label_count(row, 'medium'))}</td>"
            f"<td>{fmt_int(label_count(row, 'hard'))}</td>"
            "</tr>"
        )
        stats = row["difficulty_stats"]
        score_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(row['display'])}</strong></td>"
            f"<td>{fmt_int(stats['count'])}</td>"
            f"<td>{fmt_float(stats['min'], 1)}</td>"
            f"<td>{fmt_float(stats['p25'], 1)}</td>"
            f"<td>{fmt_float(stats['median'], 1)}</td>"
            f"<td>{fmt_float(stats['mean'], 2)}</td>"
            f"<td>{fmt_float(stats['p75'], 1)}</td>"
            f"<td>{fmt_float(stats['max'], 1)}</td>"
            "</tr>"
        )
        tag_sections.append(
            '<section class="tag-card">'
            f"<h3>{html.escape(row['display'])} <span>{lang}</span></h3>"
            f"{render_tags(row['tags'], int(row['tasks_with_tags']), 20)}"
            "</section>"
        )
        params_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(row['display'])}</strong></td>"
            f"<td><code>{html.escape(params.get('OPENAI_MODEL', 'N/A'))}</code></td>"
            f"<td><code>{html.escape(params.get('ANTHROPIC_MODEL', 'N/A'))}</code></td>"
            f"<td>{html.escape(params.get('n_concurrent', 'N/A'))}</td>"
            f"<td>{html.escape(params.get('min_source_files', 'N/A'))}</td>"
            f"<td>{html.escape(params.get('max_source_files', 'N/A'))}</td>"
            "</tr>"
        )
        other_errors = b_failed - sum(int(ec.get(k, 0)) for k in ("trivial_pr", "validation", "infra_error", "timeout", "workflow_error"))
        failure_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(row['display'])}</strong></td>"
            f"<td>{fmt_int(b_total)}</td>"
            f"<td>{fmt_int(row['valid_count'])}</td>"
            f"<td>{fmt_int(b_total - row['valid_count'])}</td>"
            f"<td>{fmt_int(ec.get('trivial_pr', 0))}</td>"
            f"<td>{fmt_int(ec.get('validation', 0))}</td>"
            f"<td>{fmt_int(ec.get('infra_error', 0))}</td>"
            f"<td>{fmt_int(ec.get('timeout', 0))}</td>"
            f"<td>{fmt_int(ec.get('workflow_error', 0))}</td>"
            f"<td>{fmt_int(max(0, other_errors))}</td>"
            "</tr>"
        )

    css = """
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
    body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); font-size: 18px; line-height: 1.5; }
    header { padding: 34px 40px 24px; background: linear-gradient(135deg, #172554, #1d4ed8 48%, #0891b2); color: white; }
    header h1 { margin: 0 0 10px; font-size: 40px; letter-spacing: -.02em; }
    header p { margin: 6px 0; color: rgba(255,255,255,.84); font-size: 17px; }
    main { padding: 30px 40px 56px; max-width: 1780px; margin: 0 auto; }
    .grid { display: grid; gap: 18px; }
    .kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 24px; }
    .card, .panel, .tag-card { background: var(--panel); border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); }
    .card { padding: 22px; }
    .card .label { color: var(--muted); font-size: 16px; }
    .card .value { font-size: 40px; font-weight: 780; margin-top: 10px; }
    .card .sub { color: var(--muted); margin-top: 8px; font-size: 15px; }
    .panel { padding: 24px; margin-top: 22px; overflow: hidden; }
    .panel h2 { margin: 0 0 18px; font-size: 26px; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 17px; }
    th { text-align: left; color: var(--muted); font-weight: 650; background: #f8fafc; }
    th, td { padding: 14px 16px; border-bottom: 1px solid var(--line); vertical-align: middle; }
    tr:last-child td { border-bottom: 0; }
    td:not(:first-child), th:not(:first-child) { text-align: right; }
    .code { display: inline-block; margin-left: 8px; color: var(--muted); font-size: 14px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .delta { color: var(--blue); font-variant-numeric: tabular-nums; }
    .muted, .mini { color: var(--muted); font-size: 14px; }
    .bar { position: relative; height: 28px; min-width: 132px; background: #e8eefc; border-radius: 999px; overflow: hidden; }
    .bar-fill { position: absolute; inset: 0 auto 0 0; background: linear-gradient(90deg, var(--blue), #06b6d4); border-radius: inherit; }
    .bar span { position: relative; z-index: 1; display: block; line-height: 28px; text-align: center; font-size: 15px; color: #0f172a; font-weight: 650; }
    .stacked { display: flex; height: 24px; min-width: 220px; overflow: hidden; border-radius: 999px; background: #eef2f7; }
    .stacked.empty { display: block; height: auto; background: transparent; color: var(--muted); }
    .seg.easy { background: var(--green); }
    .seg.medium { background: var(--amber); }
    .seg.hard { background: var(--red); }
    .tags-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
    .tag-card { padding: 20px; box-shadow: none; }
    .tag-card h3 { margin: 0 0 14px; font-size: 20px; }
    .tag-card h3 span { color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 14px; }
    .tag-row { display: grid; grid-template-columns: 180px 1fr 132px; align-items: center; gap: 12px; margin: 10px 0; font-size: 16px; }
    .tag-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
    .tag-track { height: 12px; background: #ede9fe; border-radius: 999px; overflow: hidden; }
    .tag-fill { display: block; height: 100%; background: linear-gradient(90deg, var(--purple), #2563eb); border-radius: inherit; }
    .tag-count { color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
    .method-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
    .method-card { padding: 18px; border: 1px solid var(--line); border-radius: 16px; background: #f8fafc; }
    .method-card h3 { margin: 0 0 10px; font-size: 20px; }
    .method-card p { margin: 8px 0; color: #374151; font-size: 16px; }
    .method-note { margin-top: 16px; padding: 14px 18px; background: #f8fafc; border-radius: 12px; border: 1px solid var(--line); }
    .method-note p { margin: 6px 0; color: var(--muted); font-size: 15px; }
    code { padding: 2px 6px; border-radius: 6px; background: #e5e7eb; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .92em; }
    .footer { margin-top: 22px; color: var(--muted); font-size: 14px; text-align: center; }
    .tabs { display: flex; gap: 0; padding: 0 40px; background: #1e3a5f; }
    .tab { padding: 14px 28px; border: none; background: transparent; color: rgba(255,255,255,.7); font-size: 17px; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent; transition: all .15s; }
    .tab:hover { color: rgba(255,255,255,.9); }
    .tab.active { color: white; border-bottom-color: #06b6d4; }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    @media (max-width: 1200px) { .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } .tags-grid, .method-grid { grid-template-columns: 1fr; } }
    @media (max-width: 760px) { main, header, .tabs { padding-left: 16px; padding-right: 16px; } .kpis { grid-template-columns: 1fr; } }
    """

    global_tags = render_tags(totals["global_tags"], max(1, sum(int(d["tasks_with_tags"]) for d in langs.values())), 30)
    total_stats = totals["difficulty_stats"]
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{int(refresh_seconds)}">
  <title>SWE 任务和轨迹进度看板</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <h1>SWE 任务和轨迹进度看板</h1>
    <p>最后更新时间：{html.escape(updated)}　|　下次刷新：{html.escape(next_refresh)}　|　刷新间隔：{int(refresh_seconds)} 秒</p>
  </header>
  <nav class="tabs">
    <button class="tab active" onclick="switchTab('instance')">Instance</button>
    <button class="tab" onclick="switchTab('trajectory')">Trajectory</button>
  </nav>
  <main>
  <div id="tab-instance" class="tab-content active">
    <section class="grid kpis">
      <div class="card"><div class="label">收集 PR 总数</div><div class="value">{fmt_int(totals['pr_count'])}</div><div class="sub">1h {fmt_delta(totals['delta_1h_pr'])} / 24h {fmt_delta(totals['delta_24h_pr'])}</div></div>
      <div class="card"><div class="label">有效 SWE 总数</div><div class="value">{fmt_int(totals['valid_count'])}</div><div class="sub">1h {fmt_delta(totals['delta_1h_valid'])} / 24h {fmt_delta(totals['delta_24h_valid'])}</div></div>
      <div class="card"><div class="label">整体处理成功率</div><div class="value">{fmt_float(totals['success_rate'], 1)}%</div><div class="sub">Valid SWE / 已处理 {fmt_int(totals['processed_count'])}</div></div>
      <div class="card"><div class="label">difficulty_score 均值</div><div class="value">{fmt_float(total_stats['mean'], 2)}</div><div class="sub">median {fmt_float(total_stats['median'], 1)}，count {fmt_int(total_stats['count'])}</div></div>
    </section>

    <section class="panel">
      <h2>语言进度</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>语言</th><th>收集 PR</th><th>过去 1h</th><th>过去 24h</th><th>有效 SWE</th><th>过去 1h</th><th>过去 24h</th><th>已处理</th><th>处理成功率</th></tr></thead>
          <tbody>{''.join(progress_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>运行参数</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>语言</th><th>评估模型 (OPENAI)</th><th>填充模型 (ANTHROPIC)</th><th>并发数</th><th>min_source_files</th><th>max_source_files</th></tr></thead>
          <tbody>{''.join(params_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>失败原因统计</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>语言</th><th>已处理</th><th>有效 SWE</th><th>失败</th><th>trivial_pr</th><th>validation</th><th>infra_error</th><th>timeout</th><th>workflow_error</th><th>其他</th></tr></thead>
          <tbody>{''.join(failure_rows)}</tbody>
        </table>
      </div>
      <div class="method-note">
        <p><strong>trivial_pr</strong>：PR 被 LLM 评估为过于简单（如仅修改配置、文档、依赖版本等），不适合作为 SWE 任务。</p>
        <p><strong>validation</strong>：任务生成后验证失败（NOP agent 未返回 reward=0 或 ORACLE agent 未返回 reward=1）。</p>
        <p><strong>infra_error</strong>：基础设施错误（Docker 构建失败、网络超时、磁盘空间不足等）。</p>
        <p><strong>timeout</strong>：处理超时（单个 PR 总超时或 Claude Code session 超时）。</p>
        <p><strong>workflow_error</strong>：工作流程错误（PR 元数据获取失败、worktree 创建失败、patch 生成失败等）。</p>
      </div>
    </section>

    <section class="panel">
      <h2>fix.patch 复杂度</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>语言</th><th>Valid SWE Count</th><th>Avg fix.patch lines</th><th>Avg fix.patch hunks</th><th>Avg fix.patch files</th></tr></thead>
          <tbody>{''.join(patch_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>统计方法说明</h2>
      <div class="method-grid">
        <div class="method-card">
          <h3>难度打分 difficulty_score</h3>
          <p>读取每个有效任务目录的 <code>solution/fix.patch</code>、<code>tests/</code> 和 <code>instruction.md</code>，由 <code>src/swegen/scoring.py</code> 使用零 API 静态评分。</p>
          <p>当前公式采用 log-scale 连续评分，避免中等规模 patch 过早变成 hard。权重为：<code>patch_scope 38%</code>、<code>logic_complexity 32%</code>、<code>context_breadth 15%</code>、<code>test_complexity 10%</code>、<code>instruction_complexity 5%</code>。</p>
          <p>label 阈值：<code>easy &lt;= 4.0</code>，<code>medium &lt;= 7.0</code>，<code>hard &gt; 7.0</code>。</p>
        </div>
        <div class="method-card">
          <h3>Tags 生成与展示</h3>
          <p><code>tags</code> 不是看板现场计算的，而是在 swegen 构建任务时由 LLM 根据 PR 信息生成，并写入 <code>task.toml</code> 的 <code>[metadata].tags</code>。</p>
          <p>prompt 要求 tags 按三段式生成：编程语言、项目层级/领域、框架/库名或具体主题。看板只读取已有 <code>task.toml</code> 并统计每个语言的 tag 出现次数和占比。</p>
        </div>
        <div class="method-card">
          <h3>fix.patch 统计</h3>
          <p>patch 统计来自每个有效任务的 <code>solution/fix.patch</code>，并按语言扩展名过滤代码文件，口径与 <code>upload_march_swe_to_hf.py</code> 的 code-only 统计保持一致。</p>
          <p><code>Avg fix.patch lines</code> 统计代码文件 diff 中新增/删除行数；<code>Avg fix.patch hunks</code> 统计 <code>@@</code> hunk 数；<code>Avg fix.patch files</code> 统计涉及的代码文件数。</p>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>difficulty_label 分布</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>语言</th><th>easy / medium / hard</th><th>easy</th><th>medium</th><th>hard</th></tr></thead>
          <tbody>{''.join(difficulty_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>difficulty_score 概览</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>语言</th><th>count</th><th>min</th><th>p25</th><th>median</th><th>mean</th><th>p75</th><th>max</th></tr></thead>
          <tbody>{''.join(score_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>全局 Top Tags</h2>
      {global_tags}
    </section>

    <section class="panel">
      <h2>每语言 Tags 分布</h2>
      <div class="tags-grid">{''.join(tag_sections)}</div>
    </section>

    <div class="footer">由 progress_monitor_all.py 生成。页面会自动刷新；数据来自 collected_prs、各语言输出目录、verifiable_tasks.txt、task.toml 和 solution/fix.patch。</div>
  </div>
  <div id="tab-trajectory" class="tab-content">
    {traj_html}
    <div class="footer">轨迹数据来自 {html.escape(str(TRAJ_DIR))} 目录下的 .jsonl 文件。</div>
  </div>
  </main>
  <script>
  function switchTab(name) {{
    document.querySelectorAll('.tab-content').forEach(function(el) {{ el.classList.remove('active'); }});
    document.querySelectorAll('.tab').forEach(function(el) {{ el.classList.remove('active'); }});
    document.getElementById('tab-' + name).classList.add('active');
    document.querySelector('[onclick*="' + name + '"]').classList.add('active');
  }}
  </script>
</body>
</html>
"""
    return html_doc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local HTML dashboard for March SWE-gen progress.")
    parser.add_argument("--loop", nargs="?", const=3600, type=int, default=None, help="Loop interval in seconds; default is 3600 when omitted.")
    parser.add_argument("--output-html", type=Path, default=DEFAULT_HTML, help="HTML output path.")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE, help="JSONL state file for 1h/24h deltas.")
    parser.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE, help="JSON cache for incremental task statistics.")
    parser.add_argument("--force-full-scan", action="store_true", help="Ignore cached task statistics for this run.")
    parser.add_argument("--serve", action="store_true", help="Serve the HTML directory over HTTP while updating.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host for --serve.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port for --serve.")
    parser.add_argument("--open", action="store_true", help="Open the generated HTML in a browser after the first write.")
    return parser.parse_args(argv)


def start_http_server(directory: Path, host: str, port: int) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    thread = Thread(target=server.serve_forever, name="progress-dashboard-http", daemon=True)
    thread.start()
    return server


def maybe_open_browser(path: Path) -> None:
    try:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())
    except Exception:
        pass


def run_once(args: argparse.Namespace, refresh_seconds: int) -> dict[str, Any]:
    rows = load_state(args.state_file)
    cache = load_cache(args.cache_file)
    data = collect_dashboard(rows, cache, bool(args.force_full_scan))
    save_cache(args.cache_file, cache)
    append_state(args.state_file, data["state_snap"])
    html_doc = render_html(data, refresh_seconds, args.output_html)
    atomic_write_text(args.output_html, html_doc)
    return data


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    interval = int(args.loop) if args.loop is not None else 3600
    first = True
    server: ThreadingHTTPServer | None = None

    if args.serve:
        server = start_http_server(args.output_html.parent, args.host, args.port)
        print(f"serving {args.output_html.parent} at http://{args.host}:{args.port}/{args.output_html.name}")

    try:
        while True:
            data = run_once(args, interval)
            totals = data["totals"]
            print(
                f"[{now_bjt().strftime('%Y-%m-%d %H:%M:%S BJT')}] "
                f"wrote {args.output_html} | PRs={totals['pr_count']:,} "
                f"Valid SWE={totals['valid_count']:,} Success={totals['success_rate']:.1f}%"
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
