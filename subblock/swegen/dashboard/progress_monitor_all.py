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
    data.pop("traj", None)
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


def pr_repo_from_id(pr_id: str) -> str | None:
    """`owner/repo:pr-N` -> `owner/repo`."""
    pr_id = pr_id.strip()
    if "/" not in pr_id:
        return None
    return pr_id.split(":", 1)[0].strip() or None


def task_repo_from_id(task_id: str) -> str | None:
    """`owner__repo-N` -> `owner/repo` (repo may contain dashes; strip only the trailing -N)."""
    task_id = task_id.strip()
    if "__" not in task_id:
        return None
    owner, rest = task_id.split("__", 1)
    base = rest.rsplit("-", 1)[0] if "-" in rest else rest
    owner, base = owner.strip(), base.strip()
    return f"{owner}/{base}" if owner and base else None


def count_pr_repos_cached(path: Path, cache: dict[str, Any]) -> int:
    """Count unique `owner/repo` in a PR-ids file, cached by file signature."""
    sig = stat_sig(path)
    if sig is None:
        return 0
    key = str(path)
    repos_cache = cache.setdefault("pr_repos", {})
    prev = repos_cache.get(key)
    if isinstance(prev, dict) and prev.get("sig") == sig and isinstance(prev.get("count"), int):
        return int(prev["count"])
    repos = {r for line in read_nonempty_lines(path) if (r := pr_repo_from_id(line))}
    repos_cache[key] = {"sig": sig, "count": len(repos)}
    return len(repos)


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
    pr_ids_path = PR_DIR / f"{pr_prefix}_pr_ids.txt"
    pr_count = len(read_nonempty_lines_cached(pr_ids_path, cache, keep_items=False))
    pr_repo_count = count_pr_repos_cached(pr_ids_path, cache)
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
        "pr_repo_count": pr_repo_count,
        "valid_count": valid_count,
        "valid_repos": sorted({r for t in task_ids if (r := task_repo_from_id(t))}),
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
    total_pr_repos = sum(int(d.get("pr_repo_count", 0)) for d in langs.values())
    total_valid_repos = len({r for d in langs.values() for r in d.get("valid_repos", [])})
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
            "pr_repo_count": total_pr_repos,
            "valid_count": total_valid,
            "valid_repo_count": total_valid_repos,
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
        return '<div class="stacked empty">no data</div>'
    parts = []
    for name, count, cls in (("easy", easy, "easy"), ("medium", medium, "medium"), ("hard", hard, "hard")):
        width = count / total * 100.0
        if count:
            parts.append(f'<div class="seg {cls}" style="width:{width:.3f}%" title="{name}: {count}"></div>')
    return f'<div class="stacked">{"".join(parts)}</div><div class="mini">{easy} / {medium} / {hard}</div>'


def render_tags(tags: dict[str, int], denominator: int, limit: int = 20) -> str:
    if not tags or denominator <= 0:
        return '<div class="muted">no tags data</div>'
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
      --font-sans: ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji";
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --c-bg: #020617;
      --c-bg-2: #0f172a80;
      --c-panel: #0b1226;
      --c-border: #1e293b99;
      --c-fg: #e2e8f0;
      --c-fg-dim: #94a3b8;
      --c-fg-mute: #64748b;
      --c-fg-faint: #475569;
      --c-accent: #6366f1;
      --c-accent-soft: #6366f133;
      --c-accent-border: #6366f180;
      --c-good: #34d399;
      --c-bad: #f87171;
      --c-warn: #fbbf24;
      --c-violet: #a78bfa;
      font-size: 14px;
    }
    [data-theme="light"] {
      --c-bg: #f8fafc;
      --c-bg-2: #ffffff;
      --c-panel: #ffffff;
      --c-border: #e2e8f0;
      --c-fg: #0f172a;
      --c-fg-dim: #475569;
      --c-fg-mute: #64748b;
      --c-fg-faint: #94a3b8;
      --c-accent: #4f46e5;
      --c-accent-soft: #6366f122;
      --c-accent-border: #6366f1;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      background: var(--c-bg); color: var(--c-fg);
      font-family: var(--font-sans);
      -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
      height: 100vh;
    }
    code, pre, .mono { font-family: var(--font-mono); }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-thumb { background: var(--c-fg-faint); border-radius: 3px; }
    ::-webkit-scrollbar-track { background: transparent; }

    /* shell: sidebar + topbar + content (harbor-dashboard layout) */
    .layout { display: flex; height: 100vh; overflow: hidden; }
    .sidebar {
      width: 280px; flex-shrink: 0;
      border-right: 1px solid var(--c-border);
      background: linear-gradient(180deg, #0a1226 0%, var(--c-bg) 100%);
      display: flex; flex-direction: column; overflow: hidden;
    }
    [data-theme="light"] .sidebar { background: var(--c-bg-2); }
    .sidebar-logo { padding: 16px; border-bottom: 1px solid var(--c-border); display: flex; align-items: center; gap: 10px; }
    .logo-mark {
      width: 38px; height: 32px; border-radius: 8px; background: var(--c-accent);
      display: flex; align-items: center; justify-content: center;
      color: #fff; font-weight: 800; font-size: 11px; letter-spacing: -.02em;
    }
    .logo-title { font-size: 13px; font-weight: 600; }
    .logo-sub { font-size: 10px; color: var(--c-fg-mute); }
    .nav { padding: 8px; display: flex; flex-direction: column; gap: 2px; border-bottom: 1px solid var(--c-border); }
    .nav-item {
      background: transparent; border: 1px solid transparent; color: var(--c-fg-dim);
      padding: 7px 10px; border-radius: 8px; text-align: left; cursor: pointer;
      font-size: 13px; display: flex; align-items: center; gap: 8px; text-decoration: none;
    }
    .nav-item:hover { background: #1e293b40; color: var(--c-fg); }
    .nav-item.active { background: var(--c-accent-soft); border-color: var(--c-accent-border); color: #c7d2fe; }
    [data-theme="light"] .nav-item.active { color: var(--c-accent); }
    .sidebar-section { padding: 12px 8px 8px; flex: 1; overflow: auto; }
    .section-label { text-transform: uppercase; font-size: 10px; letter-spacing: .06em; color: var(--c-fg-mute); padding: 0 8px 6px; font-weight: 600; }
    .sidebar-stat { display: flex; align-items: baseline; justify-content: space-between; padding: 6px 8px; font-size: 12px; }
    .sidebar-stat .l { color: var(--c-fg-mute); }
    .sidebar-stat .v { color: var(--c-fg); font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
    .sidebar-footer { padding: 10px 12px; border-top: 1px solid var(--c-border); font-size: 11px; color: var(--c-fg-mute); }
    .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
    .topbar {
      height: 48px; flex-shrink: 0; border-bottom: 1px solid var(--c-border);
      padding: 0 16px; display: flex; align-items: center; justify-content: space-between; background: var(--c-bg);
    }
    .crumbs { font-size: 13px; color: var(--c-fg-dim); display: flex; align-items: center; gap: 6px; }
    .crumbs .sep { color: var(--c-fg-faint); }
    .crumbs .here { color: var(--c-fg); font-family: var(--font-mono); font-size: 12px; }
    .top-actions { display: flex; align-items: center; gap: 8px; }
    .icon-btn {
      background: transparent; border: 1px solid var(--c-border); border-radius: 6px;
      width: 28px; height: 28px; color: var(--c-fg-dim); cursor: pointer;
    }
    .icon-btn:hover { color: var(--c-fg); border-color: var(--c-accent); }
    .update-status {
      display: inline-flex; align-items: center; gap: 7px;
      font-size: 13px; font-weight: 600; font-family: var(--font-mono);
      color: var(--c-accent);
      background: var(--c-accent-soft);
      border: 1px solid var(--c-accent-border);
      border-radius: 999px; padding: 4px 12px;
      letter-spacing: .01em; white-space: nowrap;
    }
    .update-status .dot {
      width: 7px; height: 7px; border-radius: 50%;
      background: var(--c-good); flex-shrink: 0;
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--c-good) 25%, transparent);
    }
    [data-theme="light"] .update-status { color: var(--c-accent); }
    .content { flex: 1; overflow-y: auto; padding: 20px; }

    /* sections */
    .panel { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px; padding: 16px; margin-bottom: 16px; overflow: hidden; }
    .eyebrow { color: var(--c-fg-mute); font-size: 10px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; margin-bottom: 6px; }
    .panel h2 { margin: 0 0 12px; font-size: 18px; line-height: 1.3; letter-spacing: -.01em; }
    .panel h3 { margin: 0 0 8px; font-size: 14px; }
    .panel p { margin: 8px 0; color: var(--c-fg-dim); font-size: 13px; }

    .grid { display: grid; gap: 14px; }
    .kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 16px; }
    .card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px; padding: 16px; }
    .card .label { color: var(--c-fg-mute); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; }
    .card .value { font-size: 26px; line-height: 1.1; font-weight: 600; margin-top: 8px; font-family: var(--font-mono); color: var(--c-fg); }
    .card .sub { color: var(--c-fg-mute); margin-top: 8px; font-size: 11px; }

    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--c-border); vertical-align: middle; }
    th { font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: var(--c-fg-mute); font-weight: 600; background: #1e293b22; }
    [data-theme="light"] th { background: #f1f5f9; }
    tr:last-child td { border-bottom: 0; }
    tbody tr:hover { background: #1e293b22; }
    [data-theme="light"] tbody tr:hover { background: #f1f5f9; }
    td:not(:first-child), th:not(:first-child) { text-align: right; }
    .code { display: inline-block; margin-left: 8px; color: var(--c-fg-mute); font-size: 11px; font-family: var(--font-mono); }
    .delta { color: var(--c-accent); font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
    .muted, .mini { color: var(--c-fg-mute); font-size: 11px; }

    .bar { position: relative; height: 22px; min-width: 118px; background: var(--c-accent-soft); border-radius: 999px; overflow: hidden; }
    .bar-fill { position: absolute; inset: 0 auto 0 0; background: linear-gradient(90deg, var(--c-accent), var(--c-violet)); border-radius: inherit; }
    .bar span { position: relative; z-index: 1; display: block; line-height: 22px; text-align: center; font-size: 12px; color: var(--c-fg); font-weight: 600; }
    .stacked { display: flex; height: 20px; min-width: 180px; overflow: hidden; border-radius: 999px; background: #1e293b55; }
    .stacked.empty { display: block; height: auto; background: transparent; color: var(--c-fg-mute); }
    .seg.easy { background: var(--c-good); }
    .seg.medium { background: var(--c-warn); }
    .seg.hard { background: var(--c-bad); }
    .tags-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .tag-card { padding: 16px; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 10px; }
    .tag-card h3 { margin: 0 0 12px; font-size: 14px; }
    .tag-card h3 span { color: var(--c-fg-mute); font-family: var(--font-mono); font-size: 11px; }
    .tag-row { display: grid; grid-template-columns: 160px 1fr 118px; align-items: center; gap: 10px; margin: 8px 0; font-size: 12px; }
    .tag-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
    .tag-track { height: 10px; background: #1e293b55; border-radius: 999px; overflow: hidden; }
    .tag-fill { display: block; height: 100%; background: linear-gradient(90deg, var(--c-violet), var(--c-accent)); border-radius: inherit; }
    .tag-count { color: var(--c-fg-mute); text-align: right; font-variant-numeric: tabular-nums; font-family: var(--font-mono); }
    .method-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
    .method-card { padding: 16px; border: 1px solid var(--c-border); border-radius: 10px; background: var(--c-bg-2); }
    .method-card p { margin: 7px 0; font-size: 12px; color: var(--c-fg-dim); }
    .method-note { margin-top: 14px; padding: 14px 16px; background: var(--c-bg-2); border-radius: 10px; border: 1px solid var(--c-border); }
    .method-note p { margin: 5px 0; font-size: 12px; color: var(--c-fg-mute); }
    .io-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .io-card { background: var(--c-bg-2); border: 1px solid var(--c-border); border-radius: 10px; padding: 14px; }
    .io-card ul { margin: 8px 0 0; padding-left: 18px; color: var(--c-fg-dim); font-size: 12px; }
    code { padding: 2px 5px; border-radius: 5px; background: #1e293b55; font-family: var(--font-mono); font-size: .9em; }
    [data-theme="light"] code { background: #e2e8f0; }
    .footer { margin-top: 8px; color: var(--c-fg-mute); font-size: 11px; text-align: center; }
    @media (max-width: 1100px) { .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } .method-grid { grid-template-columns: 1fr; } }
    @media (max-width: 760px) {
      .sidebar { display: none; }
      .tags-grid, .io-grid, .kpis { grid-template-columns: 1fr; }
      .tag-row { grid-template-columns: 1fr; }
      td:not(:first-child), th:not(:first-child) { text-align: left; }
    }
    """

    global_tags = render_tags(totals["global_tags"], max(1, sum(int(d["tasks_with_tags"]) for d in langs.values())), 30)
    total_stats = totals["difficulty_stats"]
    html_doc = f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{int(refresh_seconds)}">
  <title>SWE Task Progress Dashboard</title>
  <style>{css}</style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-logo">
        <div class="logo-mark">SWE</div>
        <div>
          <div class="logo-title">SWE-gen Progress</div>
          <div class="logo-sub">Instance Dashboard</div>
        </div>
      </div>
      <nav class="nav">
        <a class="nav-item active" href="#overview">Overview</a>
        <a class="nav-item" href="#language-progress">Language Progress</a>
        <a class="nav-item" href="#run-parameters">Run Parameters</a>
        <a class="nav-item" href="#failures">Failure Breakdown</a>
        <a class="nav-item" href="#patch-complexity">fix.patch Complexity</a>
        <a class="nav-item" href="#method-notes">Method Notes</a>
        <a class="nav-item" href="#difficulty">Difficulty</a>
        <a class="nav-item" href="#tags">Tags</a>
      </nav>
      <div class="sidebar-section">
        <div class="section-label">Summary</div>
        <div class="sidebar-stat"><span class="l">PRs collected</span><span class="v">{fmt_int(totals['pr_count'])}</span></div>
        <div class="sidebar-stat"><span class="l">PR unique repos</span><span class="v">{fmt_int(totals['pr_repo_count'])}</span></div>
        <div class="sidebar-stat"><span class="l">Valid SWE</span><span class="v">{fmt_int(totals['valid_count'])}</span></div>
        <div class="sidebar-stat"><span class="l">SWE unique repos</span><span class="v">{fmt_int(totals['valid_repo_count'])}</span></div>
        <div class="sidebar-stat"><span class="l">Success rate</span><span class="v">{fmt_float(totals['success_rate'], 1)}%</span></div>
        <div class="sidebar-stat"><span class="l">Mean difficulty</span><span class="v">{fmt_float(total_stats['mean'], 2)}</span></div>
      </div>
      <div class="sidebar-footer">Generated by progress_monitor_all.py</div>
    </aside>
    <main class="main">
      <header class="topbar">
        <div class="crumbs"><span>SWE-gen</span><span class="sep">/</span><span class="here">instance</span></div>
        <div class="top-actions">
          <span class="update-status"><span class="dot"></span>Updated {html.escape(updated)} · next {html.escape(next_refresh)}</span>
          <button class="icon-btn" title="Reload" onclick="location.reload()">↻</button>
          <button class="icon-btn" id="theme-toggle" title="Toggle theme">☀</button>
        </div>
      </header>
      <section class="content">

    <section class="panel" id="overview">
      <div class="eyebrow">Overview</div>
      <h2>SWE-gen Task Generation Overview</h2>
      <p>This dashboard reads <code>collected_prs</code>, the per-language output directories, <code>verifiable_tasks.txt</code>, <code>task.toml</code>, and <code>solution/fix.patch</code> to track production of verifiable SWE tasks.</p>
      <section class="grid kpis">
        <div class="card"><div class="label">Total PRs collected</div><div class="value">{fmt_int(totals['pr_count'])}</div><div class="sub">{fmt_int(totals['pr_repo_count'])} unique repos · 1h {fmt_delta(totals['delta_1h_pr'])} / 24h {fmt_delta(totals['delta_24h_pr'])}</div></div>
        <div class="card"><div class="label">Total valid SWE</div><div class="value">{fmt_int(totals['valid_count'])}</div><div class="sub">{fmt_int(totals['valid_repo_count'])} unique repos · 1h {fmt_delta(totals['delta_1h_valid'])} / 24h {fmt_delta(totals['delta_24h_valid'])}</div></div>
        <div class="card"><div class="label">Overall success rate</div><div class="value">{fmt_float(totals['success_rate'], 1)}%</div><div class="sub">Valid SWE / processed {fmt_int(totals['processed_count'])}</div></div>
        <div class="card"><div class="label">Mean difficulty_score</div><div class="value">{fmt_float(total_stats['mean'], 2)}</div><div class="sub">median {fmt_float(total_stats['median'], 1)}, count {fmt_int(total_stats['count'])}</div></div>
      </section>
    </section>

    <section class="panel" id="inputs-outputs">
      <div class="eyebrow">Inputs &amp; Outputs</div>
      <h2>Data Sources and Artifacts</h2>
      <div class="io-grid">
        <div class="io-card">
          <h3>Inputs</h3>
          <ul>
            <li>PR ID files under <code>{html.escape(str(PR_DIR))}</code></li>
            <li>Per-language task directories under <code>{html.escape(str(ROOT))}</code></li>
            <li><code>verifiable_tasks.txt</code>, <code>task.toml</code>, <code>solution/fix.patch</code></li>
          </ul>
        </div>
        <div class="io-card">
          <h3>Outputs</h3>
          <ul>
            <li>Static HTML: <code>{html.escape(str(output_path))}</code></li>
            <li>Incremental snapshot: <code>{html.escape(str(DEFAULT_STATE))}</code></li>
            <li>Scan cache: <code>{html.escape(str(DEFAULT_CACHE))}</code></li>
          </ul>
        </div>
      </div>
    </section>

    <section class="panel" id="language-progress">
      <h2>Language Progress</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Language</th><th>PRs collected</th><th>Last 1h</th><th>Last 24h</th><th>Valid SWE</th><th>Last 1h</th><th>Last 24h</th><th>Processed</th><th>Success rate</th></tr></thead>
          <tbody>{''.join(progress_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel" id="run-parameters">
      <h2>Run Parameters</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Language</th><th>Eval model (OPENAI)</th><th>Completion model (ANTHROPIC)</th><th>Concurrency</th><th>min_source_files</th><th>max_source_files</th></tr></thead>
          <tbody>{''.join(params_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel" id="failures">
      <h2>Failure Reason Breakdown</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Language</th><th>Processed</th><th>Valid SWE</th><th>Failed</th><th>trivial_pr</th><th>validation</th><th>infra_error</th><th>timeout</th><th>workflow_error</th><th>Other</th></tr></thead>
          <tbody>{''.join(failure_rows)}</tbody>
        </table>
      </div>
      <div class="method-note">
        <p><strong>trivial_pr</strong>: the PR was judged by the LLM as too trivial (e.g. only config, docs, or dependency-version changes) and unsuitable as a SWE task.</p>
        <p><strong>validation</strong>: validation failed after task generation (the NOP agent did not return reward=0, or the ORACLE agent did not return reward=1).</p>
        <p><strong>infra_error</strong>: infrastructure error (Docker build failure, network timeout, insufficient disk space, etc.).</p>
        <p><strong>timeout</strong>: processing timed out (per-PR total timeout or Claude Code session timeout).</p>
        <p><strong>workflow_error</strong>: workflow error (PR metadata fetch failure, worktree creation failure, patch generation failure, etc.).</p>
      </div>
    </section>

    <section class="panel" id="patch-complexity">
      <h2>fix.patch Complexity</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Language</th><th>Valid SWE Count</th><th>Avg fix.patch lines</th><th>Avg fix.patch hunks</th><th>Avg fix.patch files</th></tr></thead>
          <tbody>{''.join(patch_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel" id="method-notes">
      <div class="eyebrow">Method Notes</div>
      <h2>Metric Definitions</h2>
      <div class="method-grid">
        <div class="method-card">
          <h3>Difficulty score (difficulty_score)</h3>
          <p>Reads each valid task directory's <code>solution/fix.patch</code>, <code>tests/</code>, and <code>instruction.md</code>, scored statically with zero API calls by <code>src/swegen/scoring.py</code>.</p>
          <p>The current formula uses log-scale continuous scoring to avoid mid-sized patches becoming hard too early. Weights: <code>patch_scope 38%</code>, <code>logic_complexity 32%</code>, <code>context_breadth 15%</code>, <code>test_complexity 10%</code>, <code>instruction_complexity 5%</code>.</p>
          <p>Label thresholds: <code>easy &lt;= 4.0</code>, <code>medium &lt;= 7.0</code>, <code>hard &gt; 7.0</code>.</p>
        </div>
        <div class="method-card">
          <h3>Tag generation and display</h3>
          <p><code>tags</code> are not computed live by the dashboard; they are generated by the LLM from PR information when swegen builds the task, and written to <code>[metadata].tags</code> in <code>task.toml</code>.</p>
          <p>The prompt asks for tags in three parts: programming language, project layer/domain, and framework/library name or specific topic. The dashboard only reads existing <code>task.toml</code> files and counts each language's tag occurrences and share.</p>
        </div>
        <div class="method-card">
          <h3>fix.patch statistics</h3>
          <p>Patch stats come from each valid task's <code>solution/fix.patch</code>, filtering code files by language extension, consistent with the code-only stats in <code>upload_march_swe_to_hf.py</code>.</p>
          <p><code>Avg fix.patch lines</code> counts added/removed lines in code-file diffs; <code>Avg fix.patch hunks</code> counts <code>@@</code> hunks; <code>Avg fix.patch files</code> counts the code files involved.</p>
        </div>
      </div>
    </section>

    <section class="panel" id="difficulty">
      <h2>difficulty_label Distribution</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Language</th><th>easy / medium / hard</th><th>easy</th><th>medium</th><th>hard</th></tr></thead>
          <tbody>{''.join(difficulty_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>difficulty_score Overview</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Language</th><th>count</th><th>min</th><th>p25</th><th>median</th><th>mean</th><th>p75</th><th>max</th></tr></thead>
          <tbody>{''.join(score_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel" id="tags">
      <h2>Global Top Tags</h2>
      {global_tags}
    </section>

    <section class="panel">
      <h2>Per-Language Tag Distribution</h2>
      <div class="tags-grid">{''.join(tag_sections)}</div>
    </section>

    <div class="footer">Generated by progress_monitor_all.py. The page auto-refreshes; data comes from collected_prs, the per-language output directories, verifiable_tasks.txt, task.toml, and solution/fix.patch.</div>
      </section>
    </main>
  </div>
  <script>
    (function() {{
      var KEY = "swegen.theme";
      var btn = document.getElementById("theme-toggle");
      function apply(theme) {{
        document.documentElement.setAttribute("data-theme", theme);
        if (btn) btn.textContent = theme === "dark" ? "☀" : "☾";
      }}
      var saved = null;
      try {{ saved = localStorage.getItem(KEY); }} catch (e) {{}}
      apply(saved || "dark");
      if (btn) btn.addEventListener("click", function() {{
        var cur = document.documentElement.getAttribute("data-theme");
        var next = cur === "dark" ? "light" : "dark";
        apply(next);
        try {{ localStorage.setItem(KEY, next); }} catch (e) {{}}
      }});
    }})();
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
