#!/usr/bin/env -S uv run --no-project --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Local HTML dashboard for the tracer subblock.

Scans:
  * artifacts/index.yaml                - latest archived run state.
  * artifacts/jobs/<job>/result.json   - Harbor rollout summary.
  * artifacts/sft_data/<job>/lf.stats.json - swe_data_process LF conversion
    statistics produced by scripts/convert_trajectories.sh.
  * artifacts/sft_data/<job>/im.jsonl or lf.json - bounded sample previews.

Renders a single self-contained HTML file at dashboard/site/index.html with
operator status, artifact tables, and an interactive sample browser. The
generated site/ can be published to Cloudflare Pages by
dashboard/run_cloudflare_pages_sync.sh (not done by this script).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import tomllib
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
DEFAULT_TASKS = BLOCK_DIR / "artifacts" / "tasks"
DEFAULT_HARBOR_JOBS = Path(os.environ.get("HARBOR_JOBS_DIR", "/storage/jierun/code/harbor/jobs"))
DEFAULT_HTML = SCRIPT_DIR / "site" / "index.html"
DEFAULT_CACHE = SCRIPT_DIR / ".cache" / ".progress_monitor_cache.json"
DEFAULT_INDEX = BLOCK_DIR / "artifacts" / "index.yaml"
CACHE_VERSION = 1
DEFAULT_EMBEDDED_TRAJ_LIMIT = 120
DEFAULT_EMBEDDED_TRAJ_MAX_BYTES = 40_000_000
EMBEDDED_TRAJ_SHARD_BYTES = 8_000_000

SCAFFOLD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"openhands[-_]sdk", re.I), "openhands_sdk"),
    (re.compile(r"claude[-_]code", re.I), "claude_code"),
    (re.compile(r"open[-_]?code", re.I), "open_code"),
    (re.compile(r"terminus[-_]?2", re.I), "terminus2"),
    (re.compile(r"openhands(?![-_]sdk)", re.I), "openhands"),
]

LANGUAGE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("Python", ("python", "py", "django", "flask", "fastapi", "pytest", "pandas", "numpy", "scipy", "sklearn", "jupyter", "sqlalchemy")),
    ("JavaScript/TypeScript", ("javascript", "typescript", "node", "npm", "react", "vue", "angular", "next", "vite", "webpack", "eslint", "babel", "ts", "js")),
    ("Go", ("go", "golang", "kubernetes", "k8s", "prometheus", "terraform", "helm")),
    ("Rust", ("rust", "cargo", "tokio", "serde")),
    ("Java", ("java", "maven", "gradle", "spring", "android")),
    ("C/C++", ("c++", "cpp", "cmake", "clang", "llvm")),
    ("C#", ("csharp", "c#", "dotnet")),
    ("Shell", ("shell", "bash", "sh")),
    ("Ruby", ("ruby", "rails")),
    ("PHP", ("php", "laravel")),
    ("Swift", ("swift",)),
    ("Kotlin", ("kotlin",)),
    ("Elixir", ("elixir", "phoenix")),
    ("R", ("rstats", "tidyverse")),
]

LANGUAGE_ALIASES = {
    "py": "Python",
    "python": "Python",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "go": "Go",
    "golang": "Go",
    "rs": "Rust",
    "rust": "Rust",
    "java": "Java",
    "c": "C",
    "cpp": "C++",
    "c++": "C++",
    "cc": "C++",
    "cxx": "C++",
    "cs": "C#",
    "c#": "C#",
    "csharp": "C#",
    "sh": "Shell",
    "shell": "Shell",
    "bash": "Shell",
    "rb": "Ruby",
    "ruby": "Ruby",
    "php": "PHP",
    "swift": "Swift",
    "kt": "Kotlin",
    "kotlin": "Kotlin",
    "ex": "Elixir",
    "elixir": "Elixir",
    "r": "R",
}

SEGMENT_DIMS = ["job", "language", "model", "scaffold", "domain", "category", "difficulty", "source"]
SEGMENT_LABELS = {
    "job": "Job",
    "language": "Programming language",
    "model": "Model",
    "scaffold": "Scaffold",
    "domain": "Domain",
    "category": "Category",
    "difficulty": "Difficulty",
    "source": "Source",
}
NATURAL_LANGUAGE_CODES = {"en", "zh", "zh-cn", "zh_cn", "ja", "ko", "fr", "de", "es", "ru"}
DIFFICULTY_SCORES = {
    "easy": 1.0,
    "medium": 2.0,
    "hard": 3.0,
}
# Trajectory quality subscores aligned with swe_data_process rule_score / oh dashboard.
SUBSCORE_KEYS = [
    "oec_score",
    "iac_score",
    "dpi_score",
    "ped_score",
    "psn_score",
    "tte_score",
    "scp_score",
    "sub_score",
    "fec_score",
    "stp_score",
    "tvr_score",
]
# Non-zero TQS weights only (Σw=1). Zero-weight diagnostic dims are omitted from the matrix.
TQS_WEIGHTS = {
    "sub_score": 0.33,
    "stp_score": 0.27,
    "tvr_score": 0.23,
    "fec_score": 0.10,
    "dpi_score": 0.07,
}
WEIGHTED_SUBSCORE_KEYS = sorted(
    (key for key, weight in TQS_WEIGHTS.items() if weight > 0),
    key=lambda key: (-TQS_WEIGHTS[key], key),
)
SUBSCORE_LABELS = {
    "composite_score": "Trajectory score",
    "sub_score": "sub submission completeness",
    "stp_score": "stp step efficiency",
    "tvr_score": "tvr test verification",
    "fec_score": "fec file-edit concentration",
    "dpi_score": "dpi dirty-pattern penalty",
}
IDENTITY_COLORS = [
    "#2563eb",
    "#059669",
    "#dc2626",
    "#7c3aed",
    "#0891b2",
    "#d97706",
    "#be185d",
    "#4f46e5",
    "#16a34a",
    "#ea580c",
    "#0d9488",
    "#9333ea",
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


def seconds_between(start: str | None, end: str | None) -> float | None:
    started = parse_iso(start)
    finished = parse_iso(end)
    if not started or not finished:
        return None
    seconds = (finished - started).total_seconds()
    return seconds if seconds >= 0 else None


def safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def compact_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    if isinstance(value, str) and value:
        return [value]
    return []


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def mean(values: list[float]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def maybe_round(value: Any, digits: int = 4) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    return value


def stable_identity_color(value: Any) -> str:
    text = str(value or "")
    seed = 0
    for ch in text:
        seed = (seed * 31 + ord(ch)) & 0xFFFFFFFF
    return IDENTITY_COLORS[seed % len(IDENTITY_COLORS)]


def normalize_difficulty_label(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if text in DIFFICULTY_SCORES:
        return text
    return "unknown"


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


def fmt_token_units(value: Any) -> str:
    n = safe_float(value)
    if n is None:
        return "-"
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B" if abs_n >= 10_000_000_000 else f"{n / 1_000_000_000:.2f}B"
    if abs_n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M" if abs_n >= 10_000_000 else f"{n / 1_000_000:.2f}M"
    if abs_n >= 1_000:
        return f"{n / 1_000:.1f}K" if abs_n >= 10_000 else f"{n / 1_000:.2f}K"
    return f"{int(n):,}" if float(n).is_integer() else f"{n:.1f}"


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


def normalize_scaffold(raw: Any, fallback: str) -> str:
    raw_text = str(raw or "")
    if raw_text:
        derived = derive_scaffold(raw_text)
        if derived != "unknown":
            return derived
    return derive_scaffold(fallback)


def derive_model_name(source_name: Any, fallback: Any = None) -> str:
    """Prefer the model encoded in trajgen job/dataset names over stale metadata."""
    text = str(source_name or "")
    scaffold_re = r"(?:openhands[-_]sdk|claude[-_]code|open[-_]?code|terminus[-_]?2|openhands)"
    match = re.search(
        rf"(?:^|-)custom-{scaffold_re}(?:-[0-9][A-Za-z0-9_.]*)?-(?P<model>.+?)(?:-\d{{14}})?$",
        text,
        re.I,
    )
    if match:
        model = match.group("model").strip("-_ ")
        if model:
            return model
    fallback_text = str(fallback or "").strip()
    return fallback_text or "unknown"


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


def parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value in {"", "null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def read_status(index_path: Path) -> dict[str, Any]:
    """Read the latest run from artifacts/index.yaml without a YAML dependency."""
    if not index_path.is_file():
        return {"_error": f"{index_path} not found"}
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"_error": f"failed to read {index_path}: {exc}"}

    start: int | None = None
    for i, line in enumerate(lines):
        if line.rstrip() == "runs:":
            start = i + 1
            break
    if start is None:
        return {"_error": "top-level runs list not found"}

    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines[start:]:
        if line and not line.startswith((" ", "\t", "- ")):
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        item_match = re.match(r"^\s*-\s+([^:]+):\s*(.*)$", line)
        if item_match:
            if current is not None:
                runs.append(current)
            current = {item_match.group(1).strip(): parse_scalar(item_match.group(2))}
            continue

        field_match = re.match(r"^\s+([^:]+):\s*(.*)$", line)
        if current is not None and field_match:
            current[field_match.group(1).strip()] = parse_scalar(field_match.group(2))

    if current is not None:
        runs.append(current)
    if not runs:
        return {"_error": "artifacts/index.yaml contains no archived runs"}
    return runs[-1]


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
    out: list[dict[str, Any]] = []
    jobs_cache: dict[str, Any] = cache["jobs"]
    seen: set[str] = set()
    if jobs_dir.is_dir():
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
            out.append({
                "job": key,
                "scaffold": derive_scaffold(key),
                "source_exists": True,
                "path": str(entry),
                **parsed,
            })
    for stale in sorted(set(jobs_cache.keys()) - seen):
        parsed = jobs_cache.get(stale, {}).get("parsed")
        if isinstance(parsed, dict):
            out.append({
                "job": stale,
                "scaffold": derive_scaffold(stale),
                "source_exists": False,
                "path": str(jobs_dir / stale),
                **parsed,
            })
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
            "path": str(entry),
            "im_path": str(im_file) if im_file.is_file() else "",
            "lf_path": str(lf_file) if lf_file.is_file() else "",
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


def task_repo(task_name: str) -> str:
    parts = task_name.split("__")
    if len(parts) < 2:
        return task_name
    repo = re.sub(r"-\d+$", "", parts[1])
    return f"{parts[0]}/{repo}"


def normalize_instance_id(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"__mirror.*$", "", text)
    text = re.sub(r"__mirro.*$", "", text)
    return text


def task_lookup_keys(value: Any) -> list[str]:
    text = str(value or "")
    keys: list[str] = []
    for candidate in (text, normalize_instance_id(text)):
        candidate = candidate.strip()
        if candidate and candidate not in keys:
            keys.append(candidate)
        match = re.match(r"^(.*__.*-\d+)", candidate)
        if match and match.group(1) not in keys:
            keys.append(match.group(1))
    return keys


def normalize_language(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return LANGUAGE_ALIASES.get(text.lower(), text)


def infer_language(task_name: str, metadata: dict[str, Any]) -> str:
    explicit = metadata.get("language")
    if explicit and str(explicit).lower() not in NATURAL_LANGUAGE_CODES:
        return normalize_language(explicit)
    hay = " ".join([
        task_name,
        str(metadata.get("category") or ""),
        " ".join(compact_list(metadata.get("tags"))),
    ]).lower()
    for language, hints in LANGUAGE_HINTS:
        if any(re.search(rf"(^|[^a-z0-9+]){re.escape(hint)}([^a-z0-9+]|$)", hay) for hint in hints):
            return language
    return "unknown"


def infer_domain(metadata: dict[str, Any]) -> str:
    tags = compact_list(metadata.get("tags"))
    if len(tags) >= 2:
        return tags[1]
    for key in ("domain", "benchmark"):
        value = metadata.get(key)
        if value:
            return str(value)
    return "unknown"


def infer_difficulty(data: dict[str, Any], metadata: dict[str, Any]) -> str:
    scoring = data.get("scoring") if isinstance(data.get("scoring"), dict) else {}
    for source, key in (
        (metadata, "difficulty"),
        (metadata, "difficulty_label"),
        (scoring, "difficulty_label"),
        (scoring, "difficulty"),
        (scoring, "difficulty_score"),
    ):
        value = source.get(key) if isinstance(source, dict) else None
        if value not in {None, ""}:
            return str(value)
    return "unknown"


def iter_task_toml_files(tasks_dir: Path) -> list[Path]:
    if not tasks_dir.is_dir():
        return []
    paths: list[Path] = []
    try:
        children = sorted(tasks_dir.iterdir(), key=lambda path: str(path))
    except OSError:
        return []
    for child in children:
        direct_task = child / "task.toml"
        if direct_task.is_file():
            paths.append(direct_task)
        if not child.is_dir():
            continue
        try:
            grandchildren = sorted(child.iterdir(), key=lambda path: str(path))
        except OSError:
            continue
        for grandchild in grandchildren:
            nested_task = grandchild / "task.toml"
            if nested_task.is_file():
                paths.append(nested_task)
    return sorted(paths, key=lambda path: str(path))


def fallback_source_language(*values: Any) -> str | None:
    hay = " ".join(str(value or "") for value in values).lower()
    if "openswe" in hay:
        return "Python"
    if "alexa-skills-kit-sdk-for" in hay or "microsoft-authenticatio" in hay:
        return "JavaScript"
    return None


def collect_task_dim(tasks_dir: Path) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    if not tasks_dir.is_dir():
        return tasks
    language_map = load_language_map(tasks_dir)
    for task_file in iter_task_toml_files(tasks_dir):
        try:
            data = tomllib.loads(task_file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"WARN: failed to parse {task_file}: {exc}", file=sys.stderr)
            continue
        task_name = task_file.parent.name
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        tags = compact_list(metadata.get("tags"))
        language = normalize_language(language_map.get(normalize_instance_id(task_name)) or infer_language(task_name, metadata))
        domain = infer_domain(metadata)
        tasks[task_name] = {
            "task_name": task_name,
            "repo": task_repo(task_name),
            "language": language,
            "domain": domain,
            "category": str(metadata.get("category") or domain or "unknown"),
            "difficulty": infer_difficulty(data, metadata),
            "tags": tags,
            "author_name": metadata.get("author_name"),
            "path": str(task_file),
        }
        for alias in task_lookup_keys(task_name):
            tasks.setdefault(alias, tasks[task_name])
    task_prefix_map = build_task_prefix_map(tasks)
    language_prefix_map = build_language_prefix_map(language_map)
    for task_name, language in language_map.items():
        if any(key in tasks for key in task_lookup_keys(task_name)):
            continue
        tasks[task_name] = {
            "task_name": task_name,
            "repo": task_repo(task_name),
            "language": normalize_language(language),
            "domain": "unknown",
            "category": "unknown",
            "difficulty": "unknown",
            "tags": [],
            "author_name": None,
            "path": "",
        }
    tasks["__language_prefix_map__"] = {
        "task_name": "__language_prefix_map__",
        "prefixes": language_prefix_map,
    }
    tasks["__task_prefix_map__"] = {
        "task_name": "__task_prefix_map__",
        "prefixes": task_prefix_map,
    }
    return tasks


def load_language_map(tasks_dir: Path) -> dict[str, str]:
    paths = [tasks_dir / "language_map.json"]
    paths.extend(sorted(tasks_dir.glob("*/language_map.json")))
    out: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: failed to parse language map {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            out[normalize_instance_id(key)] = normalize_language(value)
    return out


def build_language_prefix_map(language_map: dict[str, str], min_len: int = 24) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for task_name, language in language_map.items():
        normalized = normalize_language(language)
        text = normalize_instance_id(task_name)
        for length in range(min_len, len(text)):
            candidates.setdefault(text[:length], set()).add(normalized)
    return {prefix: next(iter(languages)) for prefix, languages in candidates.items() if len(languages) == 1}


def build_task_prefix_map(task_dim: dict[str, dict[str, Any]], min_len: int = 24) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for task_name, info in task_dim.items():
        if not info.get("path"):
            continue
        canonical = str(info.get("task_name") or task_name)
        text = normalize_instance_id(canonical)
        for length in range(min_len, len(text) + 1):
            candidates.setdefault(text[:length], set()).add(canonical)
    return {prefix: next(iter(names)) for prefix, names in candidates.items() if len(names) == 1}


def task_info(task_dim: dict[str, dict[str, Any]], task_name: str) -> dict[str, Any]:
    for key in task_lookup_keys(task_name):
        base = task_dim.get(key)
        if base:
            return base
    task_prefix_map = task_dim.get("__task_prefix_map__", {}).get("prefixes")
    if isinstance(task_prefix_map, dict):
        for key in task_lookup_keys(task_name):
            canonical = task_prefix_map.get(key)
            if canonical and canonical in task_dim:
                return task_dim[canonical]
    prefix_map = task_dim.get("__language_prefix_map__", {}).get("prefixes")
    if isinstance(prefix_map, dict):
        for key in task_lookup_keys(task_name):
            language = prefix_map.get(key)
            if language:
                return {
                    "task_name": task_name,
                    "repo": task_repo(task_name),
                    "language": normalize_language(language),
                    "domain": "unknown",
                    "category": "unknown",
                    "difficulty": "unknown",
                    "tags": [],
                    "author_name": None,
                    "path": "",
                }
    return {
        "task_name": task_name,
        "repo": task_repo(task_name),
        "language": infer_language(task_name, {}),
        "domain": "unknown",
        "category": "unknown",
        "difficulty": "unknown",
        "tags": [],
        "author_name": None,
        "path": "",
    }


def trial_status(reward: float | None, exception_info: Any) -> str:
    if exception_info:
        return "error"
    if reward is None:
        return "unknown"
    return "pass" if reward >= 1 else "fail"


def extract_reward(data: dict[str, Any]) -> float | None:
    verifier_result = data.get("verifier_result") if isinstance(data.get("verifier_result"), dict) else {}
    rewards = verifier_result.get("rewards") if isinstance(verifier_result.get("rewards"), dict) else {}
    return safe_float(rewards.get("reward"))


def collect_trial_facts(
    harbor_jobs_dir: Path,
    job_names: list[str],
    task_dim: dict[str, dict[str, Any]],
    *,
    max_trials_per_job: int,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen_jobs: set[str] = set()
    for job_name in job_names:
        if not job_name or job_name in seen_jobs:
            continue
        seen_jobs.add(job_name)
        job_dir = harbor_jobs_dir / job_name
        if not job_dir.is_dir():
            continue
        count = 0
        for trial_dir in sorted(p for p in job_dir.iterdir() if p.is_dir()):
            if max_trials_per_job > 0 and count >= max_trials_per_job:
                break
            result_file = trial_dir / "result.json"
            if not result_file.is_file():
                continue
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"WARN: failed to parse {result_file}: {exc}", file=sys.stderr)
                continue
            task_name = str(data.get("task_name") or data.get("task_id") or trial_dir.name)
            info = task_info(task_dim, task_name)
            agent_info = data.get("agent_info") if isinstance(data.get("agent_info"), dict) else {}
            model_info = agent_info.get("model_info") if isinstance(agent_info.get("model_info"), dict) else {}
            agent_result = data.get("agent_result") if isinstance(data.get("agent_result"), dict) else {}
            exception_info = data.get("exception_info")
            reward = extract_reward(data)
            tokens = sum(
                safe_int(agent_result.get(key)) or 0
                for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
            )
            fact = {
                "job": job_name,
                "trial": str(data.get("trial_name") or trial_dir.name),
                "task_name": task_name,
                "repo": info.get("repo"),
                "language": (
                    info.get("language")
                    if info.get("language") not in {None, "", "unknown"}
                    else fallback_source_language(job_name, data.get("source"), task_name, info.get("repo"))
                ) or "unknown",
                "domain": info.get("domain"),
                "category": info.get("category"),
                "difficulty": info.get("difficulty"),
                "source": str(data.get("source") or "unknown"),
                "scaffold": normalize_scaffold(agent_info.get("name"), job_name),
                "model": str(model_info.get("name") or "unknown"),
                "provider": str(model_info.get("provider") or "unknown"),
                "status": trial_status(reward, exception_info),
                "reward": reward,
                "exception_type": (
                    str(exception_info.get("type") or exception_info.get("class") or "exception")
                    if isinstance(exception_info, dict) else ("exception" if exception_info else "")
                ),
                "duration_sec": seconds_between(data.get("started_at"), data.get("finished_at")),
                "tokens": tokens or None,
                "cost_usd": safe_float(agent_result.get("cost_usd")),
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
                "path": str(result_file),
                "trajectory_path": str(trial_dir / "agent" / "trajectory.json") if (trial_dir / "agent" / "trajectory.json").is_file() else "",
            }
            facts.append(fact)
            count += 1
    return facts


def extract_cot_stats(messages: list[Any]) -> dict[str, Any]:
    """Aggregate assistant reasoning_content stats (same口径 as the oh_for_experiments dashboard).

    COT turn ratio = nonempty reasoning_content / assistant turns.
    COT chars/turn (mean) = mean length over nonempty reasoning_content only.
    """
    reason_turns = 0
    reason_nonempty_turns = 0
    reason_chars_sum = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        reason_turns += 1
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            reason_nonempty_turns += 1
            reason_chars_sum += len(reasoning)
    return {
        "cot_turns": reason_turns,
        "cot_nonempty_turns": reason_nonempty_turns,
        "cot_chars_sum": reason_chars_sum,
        "cot_rate": maybe_round(reason_nonempty_turns / reason_turns if reason_turns else None),
        "cot_chars_mean": maybe_round(
            reason_chars_sum / reason_nonempty_turns if reason_nonempty_turns else None,
            1,
        ),
    }


def collect_quality_facts(
    sft_dir: Path,
    task_dim: dict[str, dict[str, Any]],
    *,
    max_records_per_dataset: int,
    preview_chars: int,
    include_previews: bool,
) -> list[dict[str, Any]]:
    if not sft_dir.is_dir():
        return []
    facts: list[dict[str, Any]] = []
    for dataset_dir in sorted(p for p in sft_dir.iterdir() if p.is_dir()):
        im_file = dataset_dir / "im.jsonl"
        if not im_file.is_file():
            continue
        try:
            f = im_file.open("r", encoding="utf-8", errors="ignore")
        except OSError as exc:
            print(f"WARN: failed to read quality facts from {im_file}: {exc}", file=sys.stderr)
            continue
        with f:
            for idx, line in enumerate(f):
                if max_records_per_dataset > 0 and idx >= max_records_per_dataset:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                meta = row.get("meta_info") if isinstance(row.get("meta_info"), dict) else {}
                unique = meta.get("unique_info") if isinstance(meta.get("unique_info"), dict) else {}
                score = unique.get("_score") if isinstance(unique.get("_score"), dict) else {}
                usage = unique.get("_usage") if isinstance(unique.get("_usage"), dict) else {}
                instance_id = str(unique.get("_instance_id") or row.get("_instance_id") or f"{dataset_dir.name}#{idx + 1}")
                task_name = normalize_instance_id(instance_id)
                info = task_info(task_dim, task_name)
                composite = safe_float(score.get("composite_score"))
                if composite is None:
                    composite_v4 = safe_float(score.get("composite_score_v4"))
                    composite = (
                        composite_v4
                        if composite_v4 is not None
                        else safe_float(score.get("composite_score_v3"))
                    )
                messages = row.get("messages") if isinstance(row.get("messages"), list) else []
                cot = extract_cot_stats(messages)
                preview = ""
                if include_previews and messages:
                    preview = content_preview(messages[-1].get("content") if isinstance(messages[-1], dict) else messages[-1], preview_chars)
                source = str(meta.get("query_source") or "unknown")
                subs = {key: safe_float(score.get(key)) for key in SUBSCORE_KEYS}
                facts.append({
                    "dataset": dataset_dir.name,
                    "job": dataset_dir.name,
                    "index": idx,
                    "instance_id": instance_id,
                    "task_name": task_name,
                    "repo": info.get("repo"),
                    "language": (
                        str(info.get("language") or "")
                        if str(info.get("language") or "").lower() != "unknown"
                        else fallback_source_language(dataset_dir.name, source, task_name, info.get("repo"))
                    ) or "unknown",
                    "domain": info.get("domain"),
                    "category": str(meta.get("category") or info.get("category") or "unknown"),
                    "difficulty": info.get("difficulty"),
                    "source": source,
                    "scaffold": normalize_scaffold(score.get("scaffold"), dataset_dir.name),
                    "model": derive_model_name(dataset_dir.name, meta.get("teacher")),
                    "teacher_raw": str(meta.get("teacher") or "unknown"),
                    "score": composite,
                    "score_v3": safe_float(score.get("composite_score_v3")),
                    "score_v4": safe_float(score.get("composite_score_v4")),
                    "efficiency_score": safe_float(score.get("efficiency_score")),
                    "style_score": safe_float(score.get("style_score")),
                    "tool_mastery_score": safe_float(score.get("tool_mastery_score")),
                    "completion_score": safe_float(score.get("completion_score")),
                    "precision_score": safe_float(score.get("precision_score")),
                    "turns": safe_int(score.get("assistant_turns") or meta.get("rounds")),
                    "tool_calls": safe_int(score.get("total_tool_calls")),
                    "tool_success_rate": safe_float(score.get("c1_tool_success_rate")),
                    "tokens": safe_int(usage.get("total_tokens")),
                    "cost_usd": safe_float(usage.get("cost")),
                    "subs": subs,
                    "reproduce_first": safe_float(score.get("reproduce_first")),
                    "cot_turns": cot["cot_turns"],
                    "cot_nonempty_turns": cot["cot_nonempty_turns"],
                    "cot_chars_sum": cot["cot_chars_sum"],
                    "cot_rate": cot["cot_rate"],
                    "cot_chars_mean": cot["cot_chars_mean"],
                    "preview": preview,
                })
    return facts


def bucket_add(bucket: dict[str, Any], fact: dict[str, Any], *, kind: str) -> None:
    if kind == "trial":
        task_key = fact_instance_key(fact)
        difficulty = normalize_difficulty_label(fact.get("difficulty"))
        existing_difficulty = bucket["task_difficulties"].get(task_key)
        if existing_difficulty is None or (existing_difficulty == "unknown" and difficulty != "unknown"):
            bucket["task_difficulties"][task_key] = difficulty
        bucket["trials"] += 1
        if fact.get("status") == "pass":
            bucket["passed"] += 1
        if fact.get("status") == "error":
            bucket["errors"] += 1
        for key, target in (("reward", "rewards"), ("duration_sec", "durations"), ("cost_usd", "costs"), ("tokens", "tokens")):
            val = safe_float(fact.get(key))
            if val is not None:
                bucket[target].append(val)
    else:
        bucket["quality_records"] += 1
        for key, target in (("score", "scores"), ("tokens", "quality_tokens"), ("turns", "turns"), ("tool_success_rate", "tool_success")):
            val = safe_float(fact.get(key))
            if val is not None:
                bucket[target].append(val)


def make_bucket(dim: str, value: str) -> dict[str, Any]:
    return {
        "dim": dim,
        "value": value or "unknown",
        "trials": 0,
        "passed": 0,
        "errors": 0,
        "rewards": [],
        "durations": [],
        "costs": [],
        "tokens": [],
        "quality_records": 0,
        "scores": [],
        "quality_tokens": [],
        "turns": [],
        "tool_success": [],
        "task_difficulties": {},
    }


def finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    scores = bucket["scores"]
    rewards = bucket["rewards"]
    trials = bucket["trials"]
    task_difficulties = bucket.get("task_difficulties") or {}
    difficulty_scores = [
        DIFFICULTY_SCORES[label]
        for label in task_difficulties.values()
        if label in DIFFICULTY_SCORES
    ]
    difficulty_unknown = sum(1 for label in task_difficulties.values() if label not in DIFFICULTY_SCORES)
    return {
        "dim": bucket["dim"],
        "value": bucket["value"],
        "trials": trials,
        "passed": bucket["passed"],
        "errors": bucket["errors"],
        "pass_rate": maybe_round(bucket["passed"] / trials if trials else None),
        "avg_reward": maybe_round(mean(rewards)),
        "avg_duration_sec": maybe_round(mean(bucket["durations"]), 1),
        "avg_trial_tokens": maybe_round(mean(bucket["tokens"]), 1),
        "avg_trial_cost_usd": maybe_round(mean(bucket["costs"]), 6),
        "quality_records": bucket["quality_records"],
        "avg_score": maybe_round(mean(scores)),
        "p25_score": maybe_round(percentile(scores, 0.25)),
        "p50_score": maybe_round(percentile(scores, 0.50)),
        "p75_score": maybe_round(percentile(scores, 0.75)),
        "avg_quality_tokens": maybe_round(mean(bucket["quality_tokens"]), 1),
        "avg_turns": maybe_round(mean(bucket["turns"]), 1),
        "avg_tool_success_rate": maybe_round(mean(bucket["tool_success"])),
        "task_difficulty_avg": maybe_round(mean(difficulty_scores), 2),
        "task_difficulty_count": len(difficulty_scores),
        "task_difficulty_unknown": difficulty_unknown,
        "task_difficulty_total": len(task_difficulties),
    }


def job_finished_fields(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {"job_finished_at": None, "job_finished_ts": None}
    finished_at = job.get("finished_at")
    finished_dt = parse_iso(str(finished_at) if finished_at else None)
    return {
        "job_finished_at": finished_at,
        "job_finished_ts": int(finished_dt.timestamp()) if finished_dt else None,
    }


def build_analysis(
    task_dim: dict[str, dict[str, Any]],
    trial_facts: list[dict[str, Any]],
    quality_facts: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for fact in trial_facts:
        for dim in SEGMENT_DIMS:
            value = str(fact.get(dim) or "unknown")
            key = (dim, value)
            buckets.setdefault(key, make_bucket(dim, value))
            bucket_add(buckets[key], fact, kind="trial")
    for fact in quality_facts:
        for dim in SEGMENT_DIMS:
            value = str(fact.get(dim) or "unknown")
            key = (dim, value)
            buckets.setdefault(key, make_bucket(dim, value))
            bucket_add(buckets[key], fact, kind="quality")
    jobs_by_name = {str(job.get("job") or ""): job for job in jobs}
    segments = []
    for bucket in buckets.values():
        segment = finalize_bucket(bucket)
        if segment.get("dim") == "job":
            segment.update(job_finished_fields(jobs_by_name.get(str(segment.get("value") or ""))))
        segments.append(segment)
    segments.sort(key=lambda x: (x["dim"], -(x.get("quality_records") or 0), -(x.get("trials") or 0), x["value"]))

    quality_examples = sorted(
        quality_facts,
        key=lambda f: (safe_float(f.get("score")) if safe_float(f.get("score")) is not None else 999, str(f.get("instance_id"))),
    )[:1500]
    trial_examples = sorted(
        trial_facts,
        key=lambda f: (
            0 if f.get("status") == "error" else 1 if f.get("status") == "fail" else 2,
            safe_float(f.get("reward")) if safe_float(f.get("reward")) is not None else 999,
            str(f.get("task_name")),
        ),
    )[:1500]
    scores = [safe_float(f.get("score")) for f in quality_facts if safe_float(f.get("score")) is not None]
    rewards = [safe_float(f.get("reward")) for f in trial_facts if safe_float(f.get("reward")) is not None]
    return {
        "dims": SEGMENT_DIMS,
        "dim_labels": SEGMENT_LABELS,
        "summary": {
            "task_count": sum(
                1
                for key, row in task_dim.items()
                if not key.startswith("__") and row.get("path")
            ),
            "trial_count": len(trial_facts),
            "quality_count": len(quality_facts),
            "avg_score": maybe_round(mean(scores)),
            "p25_score": maybe_round(percentile(scores, 0.25)),
            "p50_score": maybe_round(percentile(scores, 0.50)),
            "p75_score": maybe_round(percentile(scores, 0.75)),
            "avg_reward": maybe_round(mean(rewards)),
            "pass_rate": maybe_round(
                sum(1 for f in trial_facts if f.get("status") == "pass") / len(trial_facts)
                if trial_facts else None
            ),
        },
        "segments": segments,
        "quality_examples": quality_examples,
        "trial_examples": trial_examples,
    }


def safe_slug(value: Any, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[^A-Za-z0-9_.=-]+", "-", text).strip("-._")
    return text[:180] or fallback


def fact_instance_key(fact: dict[str, Any]) -> str:
    for candidate in (fact.get("task_name"), fact.get("instance_id")):
        normalized = normalize_instance_id(candidate).strip()
        if normalized:
            return normalized
    return "unknown"


def score_histogram(values: list[float], bins: int = 10) -> list[dict[str, Any]]:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return []
    buckets = [{"lo": i / bins, "hi": (i + 1) / bins, "count": 0} for i in range(bins)]
    for value in vals:
        idx = min(bins - 1, max(0, int(value * bins)))
        buckets[idx]["count"] += 1
    return [
        {
            "lo": maybe_round(b["lo"], 1),
            "hi": maybe_round(b["hi"], 1),
            "label": f"{b['lo']:.1f}-{b['hi']:.1f}",
            "count": b["count"],
        }
        for b in buckets
    ]


def build_instance_index(
    task_dim: dict[str, dict[str, Any]],
    trial_facts: list[dict[str, Any]],
    quality_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}

    def ensure(key: str, fact: dict[str, Any]) -> dict[str, Any]:
        info = task_info(task_dim, key)
        bucket = buckets.setdefault(key, {
            "instance_id": key,
            "task_name": key,
            "repo": fact.get("repo") or info.get("repo"),
            "language": fact.get("language") or info.get("language") or "unknown",
            "domain": fact.get("domain") or info.get("domain") or "unknown",
            "category": fact.get("category") or info.get("category") or "unknown",
            "difficulty": fact.get("difficulty") or info.get("difficulty") or "unknown",
            "sources": set(),
            "jobs": set(),
            "models": set(),
            "scaffolds": set(),
            "trial_count": 0,
            "pass_count": 0,
            "fail_count": 0,
            "error_count": 0,
            "quality_count": 0,
            "scores": [],
            "rewards": [],
            "tokens": [],
            "costs": [],
            "turns": [],
            "tool_success": [],
            "trajectory_paths": [],
        })
        for field in ("repo", "language", "domain", "category", "difficulty"):
            if bucket.get(field) in {None, "", "unknown"} and fact.get(field):
                bucket[field] = fact.get(field)
        return bucket

    for fact in trial_facts:
        key = fact_instance_key(fact)
        bucket = ensure(key, fact)
        bucket["trial_count"] += 1
        status = fact.get("status")
        if status == "pass":
            bucket["pass_count"] += 1
        elif status == "fail":
            bucket["fail_count"] += 1
        elif status == "error":
            bucket["error_count"] += 1
        for set_key, fact_key in (("sources", "source"), ("jobs", "job"), ("models", "model"), ("scaffolds", "scaffold")):
            if fact.get(fact_key):
                bucket[set_key].add(str(fact.get(fact_key)))
        for key_name, target in (("reward", "rewards"), ("tokens", "tokens"), ("cost_usd", "costs")):
            value = safe_float(fact.get(key_name))
            if value is not None:
                bucket[target].append(value)
        if fact.get("trajectory_path"):
            bucket["trajectory_paths"].append(str(fact.get("trajectory_path")))

    for fact in quality_facts:
        key = fact_instance_key(fact)
        bucket = ensure(key, fact)
        bucket["quality_count"] += 1
        for set_key, fact_key in (("sources", "source"), ("jobs", "job"), ("models", "model"), ("scaffolds", "scaffold")):
            if fact.get(fact_key):
                bucket[set_key].add(str(fact.get(fact_key)))
        for key_name, target in (("score", "scores"), ("tokens", "tokens"), ("cost_usd", "costs"), ("turns", "turns"), ("tool_success_rate", "tool_success")):
            value = safe_float(fact.get(key_name))
            if value is not None:
                bucket[target].append(value)

    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        trials = bucket["trial_count"]
        scores = bucket["scores"]
        rewards = bucket["rewards"]
        rows.append({
            "instance_id": bucket["instance_id"],
            "task_name": bucket["task_name"],
            "repo": bucket["repo"],
            "language": bucket["language"],
            "domain": bucket["domain"],
            "category": bucket["category"],
            "difficulty": bucket["difficulty"],
            "sources": sorted(bucket["sources"]),
            "jobs": sorted(bucket["jobs"]),
            "models": sorted(bucket["models"]),
            "scaffolds": sorted(bucket["scaffolds"]),
            "trial_count": trials,
            "pass_count": bucket["pass_count"],
            "fail_count": bucket["fail_count"],
            "error_count": bucket["error_count"],
            "pass_rate": maybe_round(bucket["pass_count"] / trials if trials else None),
            "quality_count": bucket["quality_count"],
            "avg_score": maybe_round(mean(scores)),
            "p25_score": maybe_round(percentile(scores, 0.25)),
            "p50_score": maybe_round(percentile(scores, 0.5)),
            "p75_score": maybe_round(percentile(scores, 0.75)),
            "best_score": maybe_round(max(scores) if scores else None),
            "best_reward": maybe_round(max(rewards) if rewards else None),
            "avg_tokens": maybe_round(mean(bucket["tokens"]), 1),
            "avg_cost_usd": maybe_round(mean(bucket["costs"]), 6),
            "avg_turns": maybe_round(mean(bucket["turns"]), 1),
            "avg_tool_success_rate": maybe_round(mean(bucket["tool_success"])),
            "trajectory_paths": bucket["trajectory_paths"][:5],
        })
    rows.sort(key=lambda row: (
        -(row.get("quality_count") or 0),
        -(row.get("trial_count") or 0),
        str(row.get("instance_id")),
    ))
    return rows


def traj_r2_key(card: dict[str, Any]) -> str:
    job = safe_slug(card.get("job") or card.get("dataset") or "unknown-job")
    instance = safe_slug(card.get("instance_id") or card.get("task_name") or "unknown-instance")
    traj = safe_slug(card.get("trial") or card.get("index") or card.get("id") or "record")
    return f"trajs/{job}/{instance}/{traj}.json"


def build_traj_cards(
    trial_facts: list[dict[str, Any]],
    quality_facts: list[dict[str, Any]],
    *,
    embed_limit: int = 6000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cards: list[dict[str, Any]] = []
    quality_by_task: dict[str, list[float]] = {}
    for fact in quality_facts:
        score = safe_float(fact.get("score"))
        if score is not None:
            quality_by_task.setdefault(fact_instance_key(fact), []).append(score)

    for fact in trial_facts:
        key = fact_instance_key(fact)
        scores = quality_by_task.get(key) or []
        card = {
            "id": f"trial:{fact.get('job')}:{fact.get('trial')}",
            "kind": "trial",
            "job": fact.get("job"),
            "trial": fact.get("trial"),
            "instance_id": key,
            "task_name": fact.get("task_name"),
            "repo": fact.get("repo"),
            "language": fact.get("language"),
            "domain": fact.get("domain"),
            "category": fact.get("category"),
            "difficulty": fact.get("difficulty"),
            "source": fact.get("source"),
            "scaffold": fact.get("scaffold"),
            "model": fact.get("model"),
            "status": fact.get("status"),
            "reward": fact.get("reward"),
            "score": maybe_round(mean(scores)),
            "tokens": fact.get("tokens"),
            "cost_usd": fact.get("cost_usd"),
            "duration_sec": fact.get("duration_sec"),
            "exception_type": fact.get("exception_type"),
            "path": fact.get("path"),
            "trajectory_path": fact.get("trajectory_path"),
            "full_available": bool(fact.get("trajectory_path")),
        }
        card["r2_key"] = traj_r2_key(card)
        cards.append(card)

    for fact in quality_facts:
        key = fact_instance_key(fact)
        card = {
            "id": f"quality:{fact.get('dataset')}:{fact.get('index')}",
            "kind": "quality",
            "job": fact.get("job"),
            "dataset": fact.get("dataset"),
            "index": fact.get("index"),
            "instance_id": key,
            "task_name": fact.get("task_name"),
            "repo": fact.get("repo"),
            "language": fact.get("language"),
            "domain": fact.get("domain"),
            "category": fact.get("category"),
            "difficulty": fact.get("difficulty"),
            "source": fact.get("source"),
            "scaffold": fact.get("scaffold"),
            "model": fact.get("model"),
            "status": "scored",
            "score": fact.get("score"),
            "score_v3": fact.get("score_v3"),
            "score_v4": fact.get("score_v4"),
            "efficiency_score": fact.get("efficiency_score"),
            "style_score": fact.get("style_score"),
            "tool_mastery_score": fact.get("tool_mastery_score"),
            "completion_score": fact.get("completion_score"),
            "precision_score": fact.get("precision_score"),
            "turns": fact.get("turns"),
            "tool_calls": fact.get("tool_calls"),
            "tool_success_rate": fact.get("tool_success_rate"),
            "tokens": fact.get("tokens"),
            "cost_usd": fact.get("cost_usd"),
            "preview": fact.get("preview"),
            "full_available": False,
        }
        card["r2_key"] = traj_r2_key(card)
        cards.append(card)

    def card_sort(card: dict[str, Any]) -> tuple[int, float, str]:
        if card.get("status") == "error":
            status_rank = 0
        elif card.get("status") == "fail":
            status_rank = 1
        elif card.get("kind") == "quality":
            status_rank = 2
        else:
            status_rank = 3
        score = safe_float(card.get("score"))
        return (status_rank, score if score is not None else 999.0, str(card.get("id")))

    cards.sort(key=card_sort)
    embedded = cards[:embed_limit]
    return cards, embedded


def build_traj_source_summary(
    quality_facts: list[dict[str, Any]],
    sft: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sft_by_job = {str(row.get("job") or ""): row for row in sft}
    jobs_by_name = {str(row.get("job") or ""): row for row in jobs}

    def top_label(counts: dict[str, int]) -> str:
        if not counts:
            return "-"
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    groups: dict[str, dict[str, Any]] = {}
    for fact in quality_facts:
        source = str(fact.get("job") or fact.get("dataset") or fact.get("source") or "unknown")
        row = groups.setdefault(
            source,
            {
                "source": source,
                "n": 0,
                "scores": [],
                "turns": [],
                "calls": [],
                "tokens": [],
                "cot_turns": 0,
                "cot_nonempty_turns": 0,
                "cot_chars_sum": 0,
                "difficulty_scores": [],
                "subs": {key: [] for key in WEIGHTED_SUBSCORE_KEYS},
                "reproduce_first": [],
                "scaffolds": {},
                "models": {},
            },
        )
        row["n"] += 1

        for out_key, *in_keys in (
            ("scores", "score"),
            ("turns", "turns"),
            ("calls", "tool_calls"),
            ("tokens", "tokens"),
        ):
            value = None
            for in_key in in_keys:
                value = safe_float(fact.get(in_key))
                if value is not None:
                    break
            if value is not None:
                row[out_key].append(value)

        cot_turns = safe_int(fact.get("cot_turns")) or 0
        cot_nonempty = safe_int(fact.get("cot_nonempty_turns")) or 0
        cot_chars_sum = safe_int(fact.get("cot_chars_sum")) or 0
        row["cot_turns"] += cot_turns
        row["cot_nonempty_turns"] += cot_nonempty
        row["cot_chars_sum"] += cot_chars_sum

        difficulty_label = normalize_difficulty_label(fact.get("difficulty"))
        if difficulty_label in DIFFICULTY_SCORES:
            row["difficulty_scores"].append(DIFFICULTY_SCORES[difficulty_label])

        fact_subs = fact.get("subs") if isinstance(fact.get("subs"), dict) else {}
        for key in WEIGHTED_SUBSCORE_KEYS:
            value = safe_float(fact_subs.get(key) if fact_subs else fact.get(key))
            if value is not None:
                row["subs"][key].append(value)
        reproduce_first = safe_float(fact.get("reproduce_first"))
        if reproduce_first is not None:
            row["reproduce_first"].append(reproduce_first)

        for count_key, card_key in (
            ("scaffolds", "scaffold"),
            ("models", "model"),
        ):
            label = str(fact.get(card_key) or "unknown")
            row[count_key][label] = row[count_key].get(label, 0) + 1

    rows: list[dict[str, Any]] = []
    for row in groups.values():
        n = int(row["n"])
        sft_row = sft_by_job.get(str(row["source"] or ""))
        job_row = jobs_by_name.get(str(row["source"] or ""))
        tool_call_errors = sft_row.get("tool_call_errors") if isinstance(sft_row, dict) else {}
        error_rate = safe_float(tool_call_errors.get("error_rate")) if isinstance(tool_call_errors, dict) else None
        reward_1_count = safe_int(job_row.get("primary_reward_1_count")) if isinstance(job_row, dict) else None
        conversion_drop_count = reward_1_count - n if reward_1_count is not None else None
        cot_turns = int(row["cot_turns"])
        cot_nonempty = int(row["cot_nonempty_turns"])
        cot_chars_sum = int(row["cot_chars_sum"])
        rows.append(
            {
                "source": row["source"],
                "n": n,
                "reward_1_count": reward_1_count,
                "conversion_drop_count": conversion_drop_count,
                "pass": n,
                "fail": 0,
                "error": 0,
                "embedded": 0,
                "comp": maybe_round(mean(row["scores"])),
                "avg_turns": maybe_round(mean(row["turns"])),
                "avg_calls": maybe_round(mean(row["calls"])),
                "avg_tokens": maybe_round(mean(row["tokens"])),
                "error_rate": maybe_round(error_rate),
                "cot_rate": maybe_round(cot_nonempty / cot_turns if cot_turns else None),
                "cot_chars_mean": maybe_round(
                    cot_chars_sum / cot_nonempty if cot_nonempty else None,
                    1,
                ),
                "task_difficulty_avg": maybe_round(mean(row["difficulty_scores"]), 2),
                "subs": {
                    key: maybe_round(mean(values))
                    for key, values in row["subs"].items()
                },
                "reproduce_first": maybe_round(mean(row["reproduce_first"])),
                "pass_rate": 1.0 if n else None,
                "embedded_rate": None,
                "scaffold": top_label(row["scaffolds"]),
                "model": top_label(row["models"]),
                "difficulty": "-",
            }
        )
    rows.sort(key=lambda row: (-(safe_float(row.get("comp")) or -1), str(row.get("source") or "")))
    return rows


def build_error_summary(
    trial_facts: list[dict[str, Any]],
    quality_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    exception_counts: dict[str, int] = {}
    by_job: dict[str, dict[str, int]] = {}
    by_language: dict[str, dict[str, int]] = {}
    low_tool_success = 0
    for fact in trial_facts:
        status = str(fact.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        job = str(fact.get("job") or "unknown")
        lang = str(fact.get("language") or "unknown")
        by_job.setdefault(job, {"total": 0, "pass": 0, "fail": 0, "error": 0})
        by_language.setdefault(lang, {"total": 0, "pass": 0, "fail": 0, "error": 0})
        by_job[job]["total"] += 1
        by_language[lang]["total"] += 1
        if status in by_job[job]:
            by_job[job][status] += 1
        if status in by_language[lang]:
            by_language[lang][status] += 1
        exc = str(fact.get("exception_type") or "")
        if exc:
            exception_counts[exc] = exception_counts.get(exc, 0) + 1
    for fact in quality_facts:
        tool = safe_float(fact.get("tool_success_rate"))
        if tool is not None and tool < 0.95:
            low_tool_success += 1
    scores = [safe_float(f.get("score")) for f in quality_facts if safe_float(f.get("score")) is not None]
    return {
        "status_counts": status_counts,
        "exception_counts": dict(sorted(exception_counts.items(), key=lambda item: (-item[1], item[0]))[:50]),
        "by_job": by_job,
        "by_language": by_language,
        "low_tool_success_records": low_tool_success,
        "score_histogram": score_histogram([v for v in scores if v is not None]),
    }


def json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, default=json_default) + "\n" for row in rows)
    atomic_write_text(path, text)


def write_jsonl_shards(path: Path, rows: list[dict[str, Any]], *, max_bytes: int = 20_000_000) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    for stale in path.parent.glob(f"{path.stem}.*{path.suffix}"):
        stale.unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    shards: list[str] = []
    current: list[str] = []
    current_bytes = 0
    shard_index = 0

    def flush() -> None:
        nonlocal shard_index, current, current_bytes
        if not current:
            return
        shard_path = path.with_name(f"{path.stem}.{shard_index:03d}{path.suffix}")
        atomic_write_text(shard_path, "".join(current))
        shards.append(str(Path("data") / shard_path.name))
        shard_index += 1
        current = []
        current_bytes = 0

    for row in rows:
        line = json.dumps(row, ensure_ascii=False, default=json_default) + "\n"
        line_bytes = len(line.encode("utf-8"))
        if current and current_bytes + line_bytes > max_bytes:
            flush()
        current.append(line)
        current_bytes += line_bytes
    flush()
    if not shards:
        atomic_write_text(path, "")
        return [str(Path("data") / path.name)]
    return shards


def select_embedded_traj_cards(cards: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    candidates = [
        card for card in cards
        if card.get("kind") == "trial" and card.get("trajectory_path") and Path(str(card.get("trajectory_path"))).is_file()
    ]
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_many(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if len(selected) >= limit:
                return
            card_id = str(row.get("id") or "")
            if not card_id or card_id in seen:
                continue
            seen.add(card_id)
            selected.append(row)

    by_job: dict[str, list[dict[str, Any]]] = {}
    for card in candidates:
        by_job.setdefault(str(card.get("job") or card.get("dataset") or "unknown"), []).append(card)
    jobs = sorted(by_job, key=lambda name: name.lower())
    for idx in range(max((len(rows) for rows in by_job.values()), default=0)):
        if len(selected) >= max(1, limit // 3):
            break
        add_many([by_job[job][idx] for job in jobs if idx < len(by_job[job])])

    def numeric(card: dict[str, Any], *keys: str, default: float) -> float:
        for key in keys:
            value = safe_float(card.get(key))
            if value is not None:
                return value
        return default

    add_many(sorted(candidates, key=lambda c: (c.get("status") != "error", numeric(c, "score", "reward", default=999), str(c.get("id")))))
    add_many(sorted(candidates, key=lambda c: (-numeric(c, "score", "reward", default=-1), str(c.get("id")))))
    add_many(sorted([c for c in candidates if c.get("status") in {"pass", "scored"}], key=lambda c: str(c.get("id"))))
    add_many(sorted(candidates, key=lambda c: str(c.get("id"))))
    return selected[:limit]


def write_embedded_trajectory_shards(
    data_dir: Path,
    cards: list[dict[str, Any]],
    *,
    limit: int,
    max_total_bytes: int,
    shard_max_bytes: int = EMBEDDED_TRAJ_SHARD_BYTES,
) -> list[str]:
    for stale in data_dir.glob("traj_embedded.*.jsonl"):
        stale.unlink(missing_ok=True)
    if limit <= 0 or max_total_bytes <= 0:
        return []

    exports: list[str] = []
    current: list[str] = []
    current_bytes = 0
    total_bytes = 0
    shard_index = 0

    def shard_rel(index: int) -> str:
        return str(Path("data") / f"traj_embedded.{index:03d}.jsonl")

    def flush() -> None:
        nonlocal current, current_bytes, shard_index
        if not current:
            return
        path = data_dir / f"traj_embedded.{shard_index:03d}.jsonl"
        atomic_write_text(path, "".join(current))
        exports.append(shard_rel(shard_index))
        current = []
        current_bytes = 0
        shard_index += 1

    for card in select_embedded_traj_cards(cards, limit=limit):
        trajectory_path = Path(str(card.get("trajectory_path") or ""))
        try:
            record = json.loads(trajectory_path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: failed to embed trajectory {trajectory_path}: {exc}", file=sys.stderr)
            continue
        row = {"id": card.get("id"), "record": record}
        line = json.dumps(row, ensure_ascii=False, default=json_default) + "\n"
        line_bytes = len(line.encode("utf-8"))
        if current and current_bytes + line_bytes > shard_max_bytes:
            flush()
        if total_bytes and total_bytes + line_bytes > max_total_bytes:
            break
        if line_bytes > max_total_bytes:
            continue
        card["embedded_available"] = True
        card["embedded_path"] = shard_rel(shard_index)
        card["embedded_bytes"] = line_bytes
        current.append(line)
        current_bytes += line_bytes
        total_bytes += line_bytes
    flush()
    return exports


WORKER_JS = """export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/traj") {
      const r2Key = url.searchParams.get("r2_key") || "";
      if (!r2Key.startsWith("trajs/") || r2Key.includes("..")) {
        return json({ error: "invalid r2_key" }, 400);
      }
      const bucket = env.TRACER_TRAJ_BUCKET;
      if (!bucket) {
        return json({ error: "TRACER_TRAJ_BUCKET binding is not configured", r2_key: r2Key }, 503);
      }
      const object = await bucket.get(r2Key);
      if (!object) {
        return json({ error: "trajectory object not found", r2_key: r2Key }, 404);
      }
      const text = await object.text();
      try {
        return json({ r2_key: r2Key, record: JSON.parse(text) }, 200);
      } catch (err) {
        return json({ r2_key: r2Key, text }, 200);
      }
    }
    return env.ASSETS.fetch(request);
  },
};

function json(payload, status) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "public, max-age=60",
    },
  });
}
"""


def write_worker_script(output_html: Path) -> None:
    atomic_write_text(output_html.parent / "_worker.js", WORKER_JS)


def write_data_exports(
    output_html: Path,
    *,
    task_dim: dict[str, dict[str, Any]],
    trial_facts: list[dict[str, Any]],
    quality_facts: list[dict[str, Any]],
    analysis: dict[str, Any],
    instances: list[dict[str, Any]],
    traj_cards: list[dict[str, Any]],
    error_summary: dict[str, Any],
    totals: dict[str, Any],
    harbor_jobs_dir: Path,
    embedded_traj_limit: int,
    embedded_traj_max_bytes: int,
) -> None:
    data_dir = output_html.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    task_rows_by_name = {
        str(row.get("task_name") or key): row
        for key, row in task_dim.items()
        if not key.startswith("__") and row.get("path")
    }
    task_rows = sorted(task_rows_by_name.values(), key=lambda x: str(x.get("task_name")))
    trial_fact_exports = write_jsonl_shards(data_dir / "trial_fact.jsonl", trial_facts)
    instance_exports = write_jsonl_shards(data_dir / "instances.jsonl", instances)
    embedded_traj_exports = write_embedded_trajectory_shards(
        data_dir,
        traj_cards,
        limit=embedded_traj_limit,
        max_total_bytes=embedded_traj_max_bytes,
    )
    summary = {
        "generated_at": now_bjt().isoformat(),
        "harbor_jobs_dir": str(harbor_jobs_dir),
        **(analysis.get("summary") or {}),
        "coverage": totals.get("coverage") or {},
        "segment_count": len(analysis.get("segments") or []),
        "instance_export_count": len(instances),
        "traj_card_export_count": len(traj_cards),
        "embedded_traj_export_count": sum(1 for card in traj_cards if card.get("embedded_available")),
        "exports": {
            "task_dim": "data/task_dim.json",
            "trial_fact": trial_fact_exports,
            "quality_fact": "data/quality_fact.jsonl",
            "segments": "data/segments.json",
            "instances": instance_exports,
            "traj_cards": "data/traj_cards.jsonl",
            "traj_embedded": embedded_traj_exports,
            "error_summary": "data/error_summary.json",
        },
    }
    atomic_write_text(data_dir / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    atomic_write_text(data_dir / "task_dim.json", json.dumps(task_rows, ensure_ascii=False, indent=2, default=json_default))
    atomic_write_text(data_dir / "segments.json", json.dumps(analysis.get("segments") or [], ensure_ascii=False, indent=2, default=json_default))
    atomic_write_text(data_dir / "error_summary.json", json.dumps(error_summary, ensure_ascii=False, indent=2, default=json_default))
    write_jsonl(data_dir / "quality_fact.jsonl", quality_facts)
    write_jsonl(data_dir / "traj_cards.jsonl", traj_cards)


def content_preview(value: Any, limit: int) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def summarize_sample(row: dict[str, Any], dataset: str, source: str, idx: int, preview_chars: int, message_limit: int) -> dict[str, Any]:
    meta = row.get("meta_info") if isinstance(row.get("meta_info"), dict) else {}
    unique = meta.get("unique_info") if isinstance(meta.get("unique_info"), dict) else row
    if not isinstance(unique, dict):
        unique = {}
    score = unique.get("_score") if isinstance(unique.get("_score"), dict) else {}
    usage = unique.get("_usage") if isinstance(unique.get("_usage"), dict) else {}
    messages_raw = row.get("messages") if isinstance(row.get("messages"), list) else []
    messages: list[dict[str, str]] = []
    for msg in messages_raw[:message_limit]:
        if not isinstance(msg, dict):
            continue
        messages.append({
            "role": str(msg.get("role") or "unknown"),
            "content": content_preview(msg.get("content"), preview_chars),
        })
    instance_id = unique.get("_instance_id") or row.get("_instance_id") or f"{dataset}#{idx + 1}"
    composite = score.get("composite_score")
    if composite is None:
        composite = score.get("composite_score_v4") or score.get("composite_score_v3")
    return {
        "dataset": dataset,
        "source": source,
        "index": idx,
        "instance_id": str(instance_id),
        "score": composite,
        "turns": score.get("assistant_turns") or meta.get("rounds") or len(messages_raw),
        "tokens": usage.get("total_tokens"),
        "tool_calls": score.get("total_tool_calls"),
        "tool_success_rate": score.get("c1_tool_success_rate"),
        "roles": [m.get("role") for m in messages],
        "messages": messages,
    }


def collect_samples(
    sft_dir: Path,
    *,
    include_samples: bool,
    sample_limit: int,
    preview_chars: int,
    message_limit: int,
) -> list[dict[str, Any]]:
    if not include_samples or sample_limit <= 0 or not sft_dir.is_dir():
        return []
    samples: list[dict[str, Any]] = []
    for dataset_dir in sorted(p for p in sft_dir.iterdir() if p.is_dir()):
        im_file = dataset_dir / "im.jsonl"
        lf_file = dataset_dir / "lf.json"
        if im_file.is_file():
            try:
                with im_file.open("r", encoding="utf-8", errors="ignore") as f:
                    for idx, line in enumerate(f):
                        if idx >= sample_limit:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(row, dict):
                            samples.append(summarize_sample(row, dataset_dir.name, "im.jsonl", idx, preview_chars, message_limit))
            except OSError as exc:
                print(f"WARN: failed to read samples from {im_file}: {exc}", file=sys.stderr)
            continue
        if lf_file.is_file():
            try:
                data = json.loads(lf_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"WARN: failed to read samples from {lf_file}: {exc}", file=sys.stderr)
                continue
            if not isinstance(data, list):
                continue
            for idx, row in enumerate(data[:sample_limit]):
                if isinstance(row, dict):
                    samples.append(summarize_sample(row, dataset_dir.name, "lf.json", idx, preview_chars, message_limit))
    return samples


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


def meaningful_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    missing = {"", "-", "none", "null", "unknown"}
    values = {
        str(row.get(key)).strip()
        for row in rows
        if str(row.get(key) or "").strip().lower() not in missing
    }
    return sorted(values, key=lambda x: x.lower())


def ranked_values(rows: list[dict[str, Any]], key: str) -> tuple[list[str], list[dict[str, Any]]]:
    missing = {"", "-", "none", "null", "unknown"}
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value.lower() in missing:
            continue
        counts[value] = counts.get(value, 0) + 1
    total = sum(counts.values())
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    return [value for value, _ in ranked], [
        {
            "value": value,
            "count": count,
            "share": maybe_round(count / total if total else None),
        }
        for value, count in ranked
    ]


def compact_values(values: list[str], limit: int = 3) -> str:
    if not values:
        return "-"
    shown = values[:limit]
    suffix = f" +{len(values) - limit} more" if len(values) > limit else ""
    return ", ".join(shown) + suffix


def build_coverage_summary(
    sft: list[dict[str, Any]],
    trial_facts: list[dict[str, Any]],
    quality_facts: list[dict[str, Any]],
    instances: list[dict[str, Any]],
) -> dict[str, Any]:
    languages, language_counts = ranked_values(quality_facts, "language")
    dataset_jobs = meaningful_values(sft, "job")
    models = meaningful_values(quality_facts, "model")
    scaffolds = meaningful_values(quality_facts, "scaffold")
    processed_instances = {fact_instance_key(fact) for fact in trial_facts}
    return {
        "valid_trajs": sum((row.get("count") or 0) for row in sft),
        "valid_tokens": sum((row.get("total_tokens") or 0) for row in sft),
        "data_sources_count": len(dataset_jobs),
        "data_sources": dataset_jobs,
        "languages_count": len(languages),
        "languages": languages,
        "language_counts": language_counts,
        "models_count": len(models),
        "models": models,
        "teacher_models_count": len(models),
        "teacher_models": models,
        "scaffolds_count": len(scaffolds),
        "scaffolds": scaffolds,
        "instances_processed": len(processed_instances),
        "instances_indexed": len(instances),
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

CSS = """
:root {
  color-scheme: light;
  --bg: #f8fafc;
  --panel: #fff;
  --panel-soft: #f8fafc;
  --panel-softer: #f1f5f9;
  --text: #0f172a;
  --muted: #64748b;
  --line: #e2e8f0;
  --soft: #f1f5f9;
  --ink: #4f46e5;
  --ink-contrast: #fff;
  --accent-soft: #6366f122;
  --accent-border: #6366f1;
  --active-text: #4f46e5;
  --button-bg: #fff;
  --button-hover: #f1f5f9;
  --bar-bg: #e2e8f0;
  --bar-text: #0f172a;
  --active-row: #f1f5f9;
  --warn-bg: #fbbf2422;
  --warn-line: #fbbf2455;
  --warn-text: #92400e;
  --blue: #4f46e5;
  --green: #059669;
  --amber: #d97706;
  --red: #dc2626;
  --purple: #7c3aed;
  --cyan: #0891b2;
  --badge-running-bg: #fbbf2422;
  --badge-running-text: #b45309;
  --badge-done-bg: #10b98122;
  --badge-done-text: #047857;
  --badge-missing-bg: #ef444422;
  --badge-missing-text: #b91c1c;
  --badge-scaffold-bg: #6366f122;
  --badge-scaffold-text: #4f46e5;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #020617;
  --panel: #0b1226;
  --panel-soft: #0f172a;
  --panel-softer: #111a2e;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --line: #1e293b99;
  --soft: #0f172a;
  --ink: #6366f1;
  --ink-contrast: #fff;
  --accent-soft: #6366f133;
  --accent-border: #6366f180;
  --active-text: #c7d2fe;
  --button-bg: #0f172a80;
  --button-hover: #1e293b40;
  --bar-bg: #1e293b66;
  --bar-text: #e2e8f0;
  --active-row: #1e293b40;
  --warn-bg: #fbbf2422;
  --warn-line: #fbbf2455;
  --warn-text: #fbbf24;
  --blue: #6366f1;
  --green: #34d399;
  --amber: #fbbf24;
  --red: #f87171;
  --purple: #a78bfa;
  --cyan: #22d3ee;
  --badge-running-bg: #fbbf2422;
  --badge-running-text: #fbbf24;
  --badge-done-bg: #10b98122;
  --badge-done-text: #34d399;
  --badge-missing-bg: #ef444422;
  --badge-missing-text: #f87171;
  --badge-scaffold-bg: #6366f133;
  --badge-scaffold-text: #c7d2fe;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.45; }
header { padding: 24px 32px 18px; background: var(--panel); border-bottom: 1px solid var(--line); }
.header-top { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
header h1 { margin: 0 0 6px; font-size: 26px; font-weight: 720; letter-spacing: 0; }
header p { margin: 4px 0; color: var(--muted); }
header code, code { background: var(--soft); padding: 2px 6px; border-radius: 5px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
main { padding: 22px 32px 48px; max-width: 1760px; margin: 0 auto; }
.app-shell { min-height: 100vh; display: grid; grid-template-columns: 248px minmax(0, 1fr); }
.sidebar { position: sticky; top: 0; height: 100vh; padding: 20px 14px; border-right: 1px solid var(--line); background: linear-gradient(180deg, #0a1226 0%, var(--bg) 100%); overflow-y: auto; }
:root:not([data-theme="dark"]) .sidebar { background: var(--panel); }
.brand { padding: 2px 8px 16px; border-bottom: 1px solid var(--line); margin-bottom: 14px; }
.brand-lockup { display: flex; align-items: center; gap: 10px; min-width: 0; }
.brand-mark { display: inline-flex; align-items: center; justify-content: center; width: 38px; height: 32px; flex: 0 0 38px; border-radius: 8px; background: var(--ink); color: var(--ink-contrast); font-size: 11px; font-weight: 800; letter-spacing: -0.02em; }
.brand-title { min-width: 0; }
.brand h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
.brand p { margin: 4px 0 0; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.side-nav { display: grid; gap: 6px; }
.nav-item { width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; border-color: transparent; background: transparent; color: var(--muted); border-radius: 8px; font-weight: 650; padding: 8px 10px; }
.nav-item:hover { background: var(--button-hover); color: var(--text); border-color: transparent; }
.nav-item.active { background: var(--accent-soft); color: var(--active-text); border-color: var(--accent-border); }
.nav-icon { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; flex: 0 0 20px; }
.nav-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.side-meta { margin-top: 18px; padding: 12px 8px 0; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.side-meta p { margin: 0 0 8px; }
.main-shell { min-width: 0; }
.topbar { display: flex; justify-content: space-between; gap: 16px; align-items: center; min-height: 48px; padding: 10px 28px; border-bottom: 1px solid var(--line); background: var(--bg); }
.topbar h2 { margin: 0; font-size: 18px; }
.topbar p { margin: 4px 0 0; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.topbar-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.update-status { color: var(--muted); font-size: 11px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.content { padding: 22px 28px 48px; max-width: 1760px; margin: 0 auto; }
button, input, select { font: inherit; }
button { border: 1px solid var(--line); background: var(--button-bg); color: var(--text); border-radius: 6px; padding: 7px 10px; cursor: pointer; }
button:hover { border-color: var(--accent-border); background: var(--button-hover); color: var(--text); }
.icon { width: 18px; height: 18px; display: block; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.icon-btn { width: 36px; height: 36px; padding: 0; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 36px; }
.theme-toggle { min-width: 0; white-space: nowrap; }
.theme-toggle .theme-sun { display: none; }
.theme-toggle .theme-moon { display: block; }
:root[data-theme="dark"] .theme-toggle .theme-sun { display: block; }
:root[data-theme="dark"] .theme-toggle .theme-moon { display: none; }
.refresh-btn.refreshing .icon { animation: spin .75s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.grid { display: grid; gap: 12px; }
.kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 18px 0; }
.card, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
.card { padding: 14px 16px; min-height: 104px; }
.card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.card .value { font-size: 28px; font-weight: 720; margin-top: 8px; font-variant-numeric: tabular-nums; }
.card .sub { color: var(--muted); margin-top: 4px; font-size: 12px; }
.panel { padding: 18px; margin-top: 18px; overflow: hidden; }
.panel-head { display: flex; align-items: start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.panel h2 { margin: 0; font-size: 18px; }
.panel .hint { color: var(--muted); margin: 4px 0 0; font-size: 12px; }
.tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 18px; }
.tab { font-weight: 650; }
.tab.active { background: var(--ink); color: var(--ink-contrast); border-color: var(--ink); }
.section { display: none; }
.section.active { display: block; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.toolbar input, .toolbar select { border: 1px solid var(--line); border-radius: 6px; background: var(--button-bg); color: var(--text); padding: 7px 9px; min-width: 190px; }
.segment-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; align-items: start; margin-top: 18px; }
.segment-grid .panel { margin-top: 0; height: 100%; }
.segment-panel h2 { font-size: 16px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; background: var(--panel); }
th { text-align: left; color: var(--muted); font-weight: 650; background: color-mix(in srgb, var(--panel-soft) 78%, transparent); white-space: nowrap; }
th.sortable { cursor: pointer; }
th.sortable::after { content: " ↕"; color: #98a2b3; font-size: 11px; }
th.segment-sort { cursor: pointer; user-select: none; }
th.segment-sort::after { content: " ↕"; color: #98a2b3; font-size: 11px; }
th.segment-sort.active.asc::after { content: " ↑"; color: var(--blue); }
th.segment-sort.active.desc::after { content: " ↓"; color: var(--blue); }
th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }
tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.job { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; max-width: 460px; word-break: break-all; }
.identity-name { display: inline-flex; align-items: center; gap: 8px; max-width: 100%; min-width: 0; }
.identity-name strong, .identity-text { min-width: 0; overflow-wrap: anywhere; }
.identity-dot, .source-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 9px; box-shadow: 0 0 0 2px color-mix(in srgb, currentColor 10%, transparent); }
.badge { display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 12px; font-weight: 650; white-space: nowrap; }
.badge.running { background: var(--badge-running-bg); color: var(--badge-running-text); }
.badge.done { background: var(--badge-done-bg); color: var(--badge-done-text); }
.badge.missing { background: var(--badge-missing-bg); color: var(--badge-missing-text); }
.badge.scaffold { background: var(--badge-scaffold-bg); color: var(--badge-scaffold-text); }
.bar { position: relative; height: 18px; min-width: 140px; background: var(--bar-bg); border-radius: 999px; overflow: hidden; }
.bar-fill { position: absolute; inset: 0 auto 0 0; background: var(--blue); border-radius: inherit; }
.bar span { position: relative; z-index: 1; display: block; line-height: 18px; text-align: center; font-size: 12px; color: var(--bar-text); font-weight: 650; }
.muted { color: var(--muted); }
.difficulty-warning { color: var(--amber); font-size: 12px; margin-top: 2px; }
details > summary { cursor: pointer; color: var(--blue); font-size: 13px; padding: 4px 0; user-select: none; }
.eval-table { margin-top: 8px; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.eval-table th { background: var(--panel-softer); font-size: 13px; }
.eval-table td { font-size: 13px; }
.footer { color: var(--muted); font-size: 12px; padding: 24px 0 0; text-align: center; }
.empty { color: var(--muted); padding: 24px; text-align: center; font-style: italic; }
.status-grid { grid-template-columns: 280px 1fr 1fr; }
.kv { display: grid; grid-template-columns: 120px 1fr; gap: 8px 12px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.pre { white-space: pre-wrap; background: var(--panel-soft); border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin: 0; color: var(--text); }
.warn { border-color: var(--warn-line); background: var(--warn-bg); color: var(--warn-text); }
.samples-layout { display: grid; grid-template-columns: minmax(280px, 420px) minmax(0, 1fr); gap: 14px; }
.sample-list { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; max-height: 720px; overflow-y: auto; background: var(--panel); }
.sample-item { display: block; width: 100%; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; text-align: left; padding: 10px 12px; background: var(--panel); }
.sample-item.active { background: var(--active-row); }
.sample-title { font-weight: 680; word-break: break-all; }
.sample-meta { color: var(--muted); font-size: 12px; margin-top: 3px; }
.sample-detail { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); min-height: 420px; padding: 14px; }
.message { border: 1px solid var(--line); border-radius: 8px; margin-top: 10px; overflow: hidden; }
.message-role { padding: 6px 9px; background: var(--panel-soft); color: var(--muted); font-weight: 650; font-size: 12px; text-transform: uppercase; }
.message-content { padding: 10px; white-space: pre-wrap; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.5; }
.heat { min-width: 140px; }
.heat-track { height: 8px; background: var(--bar-bg); border-radius: 999px; overflow: hidden; margin-top: 4px; }
.heat-fill { height: 100%; background: var(--blue); border-radius: inherit; }
.metric-stack { display: flex; flex-direction: column; gap: 2px; }
.detail-preview { margin-top: 8px; padding: 8px; border-radius: 6px; background: var(--panel-soft); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 160px; overflow: auto; }
.actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.copy-btn { padding: 4px 7px; font-size: 12px; }
.mini-bars { display: grid; gap: 8px; }
.mini-bar-row { display: grid; grid-template-columns: minmax(110px, 220px) minmax(120px, 1fr) 80px; gap: 10px; align-items: center; }
.mini-bar-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 650; }
.mini-bar-track { height: 10px; border-radius: 999px; background: var(--bar-bg); overflow: hidden; }
.mini-bar-fill { height: 100%; background: var(--ink); border-radius: inherit; }
.mini-bar-value { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.split-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, .72fr); gap: 14px; align-items: start; }
.source-link { border: 0; background: transparent; color: inherit; padding: 0; text-align: left; font: inherit; max-width: 100%; }
.source-link:hover { color: var(--active-text); background: transparent; border-color: transparent; }
.source-name { display: inline-flex; align-items: center; gap: 7px; max-width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }
.source-sub { color: var(--muted); font-size: 12px; margin-top: 3px; overflow-wrap: anywhere; }
.metric-pill { display: inline-flex; align-items: center; min-height: 22px; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); background: var(--panel-soft); font-variant-numeric: tabular-nums; }
.metric-pill.good { background: #10b98122; color: var(--green); border-color: #10b98155; }
.metric-pill.warn { background: #fbbf2422; color: var(--amber); border-color: #fbbf2455; }
.metric-pill.bad { background: #ef444422; color: var(--red); border-color: #ef444455; }
.subscore-matrix-table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.subscore-matrix-table th, .subscore-matrix-table td { padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }
.subscore-matrix-table th { position: sticky; top: 0; background: var(--panel); z-index: 1; font-size: 12px; }
.subscore-matrix-table th .sub-weight { display: block; margin-top: 2px; color: var(--muted); font-size: 10px; font-weight: 500; }
.subscore-matrix-table td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; max-width: 360px; overflow-wrap: anywhere; }
.subscore-matrix-table td.num { text-align: right; }
.subscore-matrix-table th.score-sep, .subscore-matrix-table td.score-sep { border-right: 2px solid var(--line); }
.subscore-matrix-table th.segment-sort { cursor: pointer; }
.traj-layout { display: grid; grid-template-columns: minmax(300px, 460px) minmax(0, 1fr); gap: 14px; align-items: start; }
.traj-list { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; max-height: 760px; overflow-y: auto; background: var(--panel); }
.traj-card { display: block; width: 100%; text-align: left; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; padding: 11px 12px; background: var(--panel); }
.traj-card.active { background: var(--active-row); }
.traj-card-title { font-weight: 680; overflow-wrap: anywhere; }
.traj-card-meta { color: var(--muted); font-size: 12px; margin-top: 3px; overflow-wrap: anywhere; }
.traj-view { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); min-height: 520px; padding: 14px; }
.step-row { width: 100%; display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 8px; padding: 9px 11px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; text-align: left; background: var(--panel); }
.step-row:hover, .step-row.active { background: var(--active-row); border-color: var(--line); }
.step-row .id { color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; padding-top: 2px; }
.step-row-body { min-width: 0; display: grid; gap: 5px; }
.step-row .desc { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-badges { display: flex; gap: 5px; flex-wrap: wrap; }
.step-badge { display: inline-flex; align-items: center; min-height: 20px; padding: 1px 6px; border: 1px solid var(--line); border-radius: 999px; font-size: 10.5px; color: var(--muted); background: var(--panel-soft); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.step-badge.good { background: #10b98122; color: var(--green); border-color: #10b98155; }
.step-badge.bad { background: #ef444422; color: var(--red); border-color: #ef444455; }
.step-badge.warn { background: #fbbf2422; color: var(--amber); border-color: #fbbf2455; }
.step-badge.accent { background: var(--accent-soft); color: var(--active-text); border-color: var(--accent-border); }
.trace-header { margin-bottom: 12px; }
.trace-header-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.trace-title { margin: 0; font-size: 16px; overflow-wrap: anywhere; }
.turns-list { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }
.turn-card, .preface-card { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); overflow: hidden; }
.turn-card.open { border-color: var(--accent-border); }
.turn-head { width: 100%; border: 0; border-radius: 0; background: transparent; color: var(--text); display: flex; align-items: center; gap: 8px; padding: 10px 12px; text-align: left; }
.turn-head:hover { background: var(--button-hover); border-color: transparent; }
.turn-chev { color: var(--muted); width: 12px; }
.turn-num { font-weight: 700; }
.turn-tools { display: inline-flex; gap: 4px; flex-wrap: wrap; color: var(--amber); }
.turn-spacer { flex: 1; }
.turn-body { display: flex; flex-direction: column; gap: 10px; padding: 0 12px 12px; border-top: 1px solid var(--line); }
.trace-block { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: var(--panel-soft); }
.trace-block.thought { border-color: #3b82f655; background: #3b82f60a; }
.trace-block.action { border-color: #f59e0b55; background: #f59e0b0a; }
.trace-block.observation { border-color: #10b98155; background: #10b9810a; }
.trace-block.error { border-color: #ef444455; background: #ef44440d; }
.block-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.block-label { color: var(--muted); font-size: 10.5px; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }
.block-tool-name { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: var(--amber); background: #fbbf2422; padding: 1px 7px; border-radius: 4px; }
.block-pre, .preface-pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; background: #0f172a; color: #e2e8f0; border: 1px solid #1e293b; border-radius: 6px; padding: 10px; max-height: 420px; overflow-y: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.45; }
.preface-card { margin-top: 8px; }
.preface-card > summary { cursor: pointer; padding: 10px 12px; display: flex; gap: 8px; align-items: center; }
.preface-card[open] > summary { border-bottom: 1px solid var(--line); }
.arg-pill { display: inline-flex; gap: 6px; margin: 0 6px 6px 0; padding: 3px 8px; background: var(--panel-soft); border: 1px solid var(--line); border-radius: 5px; font-size: 12px; }
.arg-key { color: var(--muted); font-weight: 700; }
.arg-block { margin-top: 6px; }
.arg-block .arg-key { display: block; margin-bottom: 4px; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
.json-block { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--panel-soft); border: 1px solid var(--line); border-radius: 8px; padding: 12px; max-height: 520px; overflow: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.pill-row { display: flex; gap: 6px; flex-wrap: wrap; }
.pill { display: inline-flex; align-items: center; min-height: 24px; padding: 2px 7px; border: 1px solid var(--line); border-radius: 999px; font-size: 12px; color: var(--muted); background: var(--panel-soft); }
.hidden { display: none !important; }
@media (max-width: 900px) {
  header, main { padding-left: 16px; padding-right: 16px; }
  .app-shell { display: block; }
  .sidebar { position: relative; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
  .side-nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .topbar { display: block; padding: 16px; }
  .topbar-actions { justify-content: flex-start; margin-top: 10px; }
  .update-status { white-space: normal; flex-basis: 100%; }
  .content { padding: 16px; }
  .header-top { display: block; }
  .theme-toggle { margin-top: 10px; }
  .kpis, .status-grid, .samples-layout, .split-layout, .traj-layout { grid-template-columns: 1fr; }
  .panel-head { display: block; }
  .mini-bar-row { grid-template-columns: 1fr; gap: 4px; }
  .mini-bar-value { text-align: left; }
}
"""


def status_badge(finished_at: str | None) -> str:
    if finished_at:
        return '<span class="badge done">done</span>'
    return '<span class="badge running">running</span>'


def source_badge(source_exists: bool) -> str:
    if source_exists:
        return '<span class="badge done">source ok</span>'
    return '<span class="badge missing">cache only</span>'


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
    source_exists = bool(job.get("source_exists", True))
    eval_rows = []
    for ev in job.get("evals") or []:
        exc = ev.get("exception_summary") or {}
        exc_text = ", ".join(f"{k}: {v}" for k, v in sorted(exc.items())) or "-"
        mean = ev.get("mean")
        mean_text = f"{mean:.4f}" if isinstance(mean, (int, float)) else "-"
        eval_rows.append(
            "<tr>"
            f"<td>{html.escape(str(ev.get('name') or '-'))}</td>"
            f'<td class="num">{fmt_num(ev.get("n_trials"))}</td>'
            f'<td class="num">{fmt_num(ev.get("n_errors"))}</td>'
            f'<td class="num">{mean_text}</td>'
            f'<td class="num">{fmt_num(ev.get("reward_1_count"))}</td>'
            f'<td class="num">{fmt_num(ev.get("reward_0_count"))}</td>'
            f"<td>{html.escape(exc_text)}</td>"
            "</tr>"
        )
    eval_table = (
        "<details><summary>Eval breakdown</summary>"
        '<div class="eval-table"><table><thead><tr><th>Eval</th><th class="num">Trials</th>'
        '<th class="num">Errors</th><th class="num">Mean</th><th class="num">Reward=1</th>'
        '<th class="num">Reward=0</th><th>Exceptions</th></tr></thead>'
        f"<tbody>{''.join(eval_rows) or '<tr><td colspan=\"7\" class=\"muted\">No eval details</td></tr>'}</tbody></table></div>"
        "</details>"
    )
    search_text = f"{job.get('job')} {job.get('scaffold')} {job.get('id')} {job.get('path')}"
    return (
        f'<tr class="job-row" data-search="{html.escape(search_text.lower())}" '
        f'data-scaffold="{html.escape(job.get("scaffold") or "unknown")}" '
        f'data-status="{"done" if job.get("finished_at") else "running"}" '
        f'data-source="{"ok" if source_exists else "missing"}">'
        f'<td class="job">{html.escape(job["job"])}'
        f'<div class="muted">id: {html.escape(str(job.get("id") or "-"))}</div>'
        f'<div class="actions"><button class="copy-btn" data-copy="{html.escape(str(job.get("path") or ""))}">Copy path</button></div>'
        f"{eval_table}</td>"
        f'<td><span class="badge scaffold">{html.escape(job.get("scaffold") or "unknown")}</span></td>'
        f"<td>{status_badge(job.get('finished_at'))}</td>"
        f"<td>{source_badge(source_exists)}</td>"
        f"<td>{html.escape(fmt_dt(started))}</td>"
        f"<td>{html.escape(fmt_dt(finished))}</td>"
        f'<td class="num">{html.escape(fmt_duration(runtime))}</td>'
        f"<td>{progress_bar(job.get('n_trials'), job.get('n_total_trials'))}</td>"
        f'<td class="num">{fmt_num(job.get("n_errors"))}</td>'
        f'<td class="num">{mean_str}</td>'
        f'<td class="num">{fmt_num(job.get("primary_reward_1_count"))}</td>'
        "</tr>"
    )


def render_task_difficulty_cell(segment: dict[str, Any]) -> str:
    avg = safe_float(segment.get("task_difficulty_avg"))
    valid = safe_int(segment.get("task_difficulty_count")) or 0
    unknown = safe_int(segment.get("task_difficulty_unknown")) or 0
    total = safe_int(segment.get("task_difficulty_total")) or 0
    if avg is None:
        value = "-"
        note = f"{fmt_num(unknown)} unknown" if unknown and total else ""
    else:
        value = f"{avg:.2f}"
        note = f"{fmt_num(valid)} scored"
    if unknown:
        suffix = f"{fmt_num(unknown)} unknown"
        if avg is not None:
            note = f"{note} · {suffix}" if note else suffix
    note_html = f'<div class="difficulty-warning">{html.escape(note)}</div>' if unknown else (f'<div class="muted">{html.escape(note)}</div>' if note else "")
    return f"{html.escape(value)}{note_html}"


def render_job_segment_row(job: dict[str, Any], segment: dict[str, Any] | None = None) -> str:
    segment = segment or {}
    job_name = str(job.get("job") or segment.get("value") or "unknown")
    job_id = str(job.get("id") or "-")
    job_path = str(job.get("path") or "")
    job_color = stable_identity_color(job_name)
    scaffold = str(job.get("scaffold") or "unknown")
    status = status_badge(job.get("finished_at")) if job else '<span class="muted">-</span>'
    progress = progress_bar(job.get("n_trials"), job.get("n_total_trials")) if job else '<span class="muted">-</span>'
    pass_rate = fmt_pct(segment.get("pass_rate"))
    token_cell = (
        f'valid {html.escape(fmt_token_units(segment.get("avg_quality_tokens")))}'
        f'<div class="muted">total {html.escape(fmt_token_units(segment.get("avg_trial_tokens")))}</div>'
    )
    task_difficulty = render_task_difficulty_cell(segment)
    copy_button = (
        f'<div class="actions"><button class="copy-btn" data-copy="{html.escape(job_path)}">Copy path</button></div>'
        if job_path else ""
    )
    return (
        "<tr>"
        f'<td class="job"><div class="identity-name"><span class="identity-dot" style="background:{html.escape(job_color)}"></span>'
        f'<strong>{html.escape(job_name)}</strong></div>'
        f'<div class="muted">id: {html.escape(job_id)}</div>'
        f"{copy_button}</td>"
        f'<td><span class="badge scaffold">{html.escape(scaffold)}</span></td>'
        f"<td>{status}</td>"
        f"<td>{progress}</td>"
        f'<td class="num">{fmt_num(segment.get("trials"))}</td>'
        f'<td class="num">{fmt_num(segment.get("passed"))}</td>'
        f'<td class="num">{fmt_num(segment.get("errors"))}</td>'
        f'<td class="num">{pass_rate}</td>'
        f'<td class="num">{task_difficulty}</td>'
        f'<td class="num">{fmt_num(segment.get("quality_records"))}</td>'
        f'<td class="num">{token_cell}</td>'
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
    search_text = f"{s.get('job')} {s.get('scaffold')} {s.get('path')}"
    return (
        f'<tr class="sft-row" data-search="{html.escape(search_text.lower())}" data-scaffold="{html.escape(s.get("scaffold") or "unknown")}">'
        f'<td class="job">{html.escape(s["job"])}'
        f'<div class="actions"><button class="copy-btn" data-copy="{html.escape(str(s.get("path") or ""))}">Copy dir</button>'
        f'<button class="copy-btn" data-copy="{html.escape(str(s.get("im_path") or s.get("lf_path") or ""))}">Copy data</button></div></td>'
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


def svg_icon(name: str) -> str:
    icons = {
        "overview": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="14" width="7" height="7" rx="1.5"></rect><rect x="3" y="14" width="7" height="7" rx="1.5"></rect></svg>',
        "instances": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="5" rx="8" ry="3"></ellipse><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"></path><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"></path></svg>',
        "trajectories": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="3"></circle><circle cx="18" cy="6" r="3"></circle><circle cx="18" cy="18" r="3"></circle><path d="M9 6h6"></path><path d="M6 9v2a7 7 0 0 0 7 7h2"></path></svg>',
        "operations": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="m8 9 3 3-3 3"></path><path d="M13 15h4"></path></svg>',
        "moon": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 14.5A8.7 8.7 0 0 1 9.5 3.5a7 7 0 1 0 11 11Z"></path></svg>',
        "sun": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path></svg>',
        "refresh": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6v5h-5"></path><path d="M4 18v-5h5"></path><path d="M18.9 11A7 7 0 0 0 7.1 6.1L4 9"></path><path d="M5.1 13A7 7 0 0 0 16.9 17.9L20 15"></path></svg>',
    }
    return icons[name]


def render_html(
    jobs: list[dict[str, Any]],
    sft: list[dict[str, Any]],
    status: dict[str, Any],
    samples: list[dict[str, Any]],
    totals: dict[str, Any],
    analysis: dict[str, Any],
    instances: list[dict[str, Any]],
    traj_cards: list[dict[str, Any]],
    traj_source_summary: list[dict[str, Any]],
    error_summary: dict[str, Any],
    refresh_seconds: int,
    jobs_dir: Path,
    sft_dir: Path,
    harbor_jobs_dir: Path,
    index_path: Path,
) -> str:
    jobs_sorted = sorted(jobs, key=lambda j: j.get("started_at") or "", reverse=True)
    sft_sorted = sorted(sft, key=lambda s: s["job"])
    now_str = now_bjt().strftime("%Y-%m-%d %H:%M:%S BJT")

    missing_jobs = sum(1 for j in jobs_sorted if not j.get("source_exists", True))
    if sft_sorted:
        sft_rows = "".join(render_sft_row(s) for s in sft_sorted)
        sft_table = (
            '<div class="table-wrap"><table>'
            "<thead><tr><th class='sortable'>Job</th><th class='sortable'>Scaffold</th><th class='num sortable'>Records</th>"
            "<th class='num'>Token len (min/mean/max)</th>"
            "<th class='num'>Turns (min/mean/max)</th>"
            "<th class='num'>Scores (min/mean/max)</th>"
            "<th class='num'>Tool-call errors</th>"
            "<th class='num'>im.jsonl</th><th class='num'>lf.json</th></tr></thead>"
            f"<tbody>{sft_rows}</tbody></table></div>"
        )
    else:
        sft_table = '<div class="empty">No lf.stats.json found under artifacts/sft_data/.</div>'

    scaffold_options = "".join(
        f'<option value="{html.escape(v)}">{html.escape(v)}</option>'
        for v in sorted({str(x.get("scaffold") or "unknown") for x in jobs_sorted + sft_sorted})
    )
    sample_dataset_options = "".join(
        f'<option value="{html.escape(v)}">{html.escape(v)}</option>'
        for v in sorted({str(x.get("dataset")) for x in samples})
    )
    samples_json = json.dumps(samples, ensure_ascii=False).replace("</", "<\\/")
    analysis_json = json.dumps(analysis, ensure_ascii=False, default=json_default).replace("</", "<\\/")
    traj_cards_json = json.dumps(traj_cards, ensure_ascii=False, default=json_default).replace("</", "<\\/")
    traj_source_summary_json = json.dumps(traj_source_summary, ensure_ascii=False, default=json_default).replace("</", "<\\/")
    error_summary_json = json.dumps(error_summary, ensure_ascii=False, default=json_default).replace("</", "<\\/")
    analysis_summary = analysis.get("summary") if isinstance(analysis.get("summary"), dict) else {}
    coverage = totals.get("coverage") if isinstance(totals.get("coverage"), dict) else {}
    avg_score = analysis_summary.get("avg_score")
    p50_score = analysis_summary.get("p50_score")
    pass_rate = analysis_summary.get("pass_rate")
    avg_score_text = f"{avg_score:.4f}" if isinstance(avg_score, (int, float)) else "-"
    p50_score_text = f"{p50_score:.4f}" if isinstance(p50_score, (int, float)) else "-"
    pass_rate_text = fmt_pct(pass_rate) if isinstance(pass_rate, (int, float)) else "-"
    language_hint = html.escape(compact_values(coverage.get("languages") or []))
    model_hint = html.escape(compact_values(coverage.get("models") or coverage.get("teacher_models") or []))
    scaffold_hint = html.escape(compact_values(coverage.get("scaffolds") or []))
    segment_dims = [dim for dim in analysis.get("dims", SEGMENT_DIMS) if dim in SEGMENT_DIMS]
    job_segments = {
        str(segment.get("value") or ""): segment
        for segment in analysis.get("segments") or []
        if segment.get("dim") == "job"
    }
    for dataset in sft_sorted:
        job_name = str(dataset.get("job") or "")
        if not job_name:
            continue
        tool_error_rate = safe_float((dataset.get("tool_call_errors") or {}).get("error_rate"))
        if tool_error_rate is None:
            continue
        job_segments.setdefault(job_name, {"dim": "job", "value": job_name})["tool_error_rate"] = tool_error_rate
    job_by_name = {str(job.get("job") or ""): job for job in jobs_sorted}
    job_segment_names = sorted(
        set(job_by_name) | set(job_segments),
        key=lambda name: -safe_float(job_segments.get(name, {}).get("job_finished_ts") or 0),
    )
    job_segment_rows = "".join(
        render_job_segment_row(job_by_name.get(name) or {"job": name}, job_segments.get(name))
        for name in job_segment_names
    )
    difficulty_title = html.escape("easy=1, medium=2, hard=3; unknown is excluded from the score and shown as a warning.")
    job_segment_panel = (
        '<section class="panel segment-panel">'
        '<div class="panel-head"><div><h2>Job</h2>'
        '<p class="hint">Harbor job status and progress with SFT/pass metrics.</p></div></div>'
        '<div class="table-wrap"><table class="segment-job-table">'
        "<thead><tr>"
        "<th class='sortable'>Job</th><th class='sortable'>Scaffold</th><th class='sortable'>Status</th><th>Progress</th>"
        "<th class='num sortable'>Instances</th><th class='num sortable'>Passed</th><th class='num sortable'>Errors</th>"
        f"<th class='num sortable'>Pass Rate</th><th class='num sortable' title='{difficulty_title}'>Task Difficulty</th>"
        "<th class='num sortable'>Valid Trajs</th><th class='num sortable'>Tokens</th>"
        "</tr></thead>"
        f"<tbody>{job_segment_rows or '<tr><td colspan=\"11\" class=\"empty\">No job segments available.</td></tr>'}</tbody>"
        "</table></div></section>"
    )
    segment_header = (
        '<thead><tr>'
        '<th class="segment-sort" data-segment-sort="value">Segment</th>'
        '<th class="num segment-sort" data-segment-sort="trials">Instances</th>'
        '<th class="num segment-sort" data-segment-sort="passed">Passed</th>'
        '<th class="num segment-sort" data-segment-sort="errors">Errors</th>'
        '<th class="num segment-sort" data-segment-sort="pass_rate">Pass Rate</th>'
        f'<th class="num segment-sort" data-segment-sort="task_difficulty_avg" title="{difficulty_title}">Task Difficulty</th>'
        '<th class="num segment-sort" data-segment-sort="quality_records">Valid Trajs</th>'
        '<th class="num segment-sort" data-segment-sort="avg_quality_tokens">Tokens</th>'
        '</tr></thead>'
    )
    difficulty_segment_header = segment_header.replace(
        f'<th class="num segment-sort" data-segment-sort="task_difficulty_avg" title="{difficulty_title}">Task Difficulty</th>',
        "",
    )
    segment_panels = job_segment_panel + "".join(
        '<section class="panel segment-panel">'
        f'<div class="panel-head"><div><h2>{html.escape(SEGMENT_LABELS.get(dim, dim))}</h2>'
        f'<p class="hint">Segmented by {html.escape(SEGMENT_LABELS.get(dim, dim))}.</p></div></div>'
        f'<div class="table-wrap"><table class="segment-table" data-segment-dim="{html.escape(dim)}">'
        f'{difficulty_segment_header if dim == "difficulty" else segment_header}<tbody data-segment-rows="{html.escape(dim)}"></tbody></table></div>'
        '</section>'
        for dim in segment_dims if dim != "job"
    )
    status_error = status.get("_error")
    icon_overview = svg_icon("overview")
    icon_instances = svg_icon("instances")
    icon_trajectories = svg_icon("trajectories")
    icon_operations = svg_icon("operations")
    icon_moon = svg_icon("moon")
    icon_sun = svg_icon("sun")
    icon_refresh = svg_icon("refresh")
    status_block = (
        f'<div class="panel warn"><strong>Status unavailable.</strong> {html.escape(str(status_error))}</div>'
        if status_error else
        '<div class="grid status-grid">'
        '<div class="card"><div class="label">Latest Run</div>'
        f'<div class="value">{html.escape(str(status.get("id") or "-"))}</div>'
        f'<div class="sub">completed_at: {html.escape(str(status.get("completed_at") or "-"))}</div></div>'
        '<div class="card"><div class="label">Status</div>'
        f'<div class="sub">{html.escape(str(status.get("status") or "-"))}</div></div>'
        '<div class="card"><div class="label">Archive</div>'
        f'<div class="sub">{html.escape(str(status.get("archive") or "-"))}</div></div>'
        '</div>'
        '<section class="panel"><div class="panel-head"><div><h2>Run Notes</h2>'
        '<p class="hint">Source of truth: artifacts/index.yaml -> latest run</p></div>'
        f'<button class="copy-btn" data-copy="{html.escape(str(index_path))}">Copy index path</button></div>'
        f'<pre class="pre">{html.escape(str(status.get("notes") or "-"))}</pre></section>'
    )
    stale_warning = (
        f'<section class="panel warn"><strong>{missing_jobs} cached job(s)</strong> no longer have result.json under the current jobs directory. '
        'They are shown as cache-only so old dashboard data is not mistaken for fresh source files.</section>'
        if missing_jobs else ""
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>tracer</title>
<script>
(() => {{
  const saved = localStorage.getItem('tracer-theme');
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = saved || (prefersDark ? 'dark' : 'light');
}})();
</script>
<style>{CSS}</style>
</head>
<body>
<div class="app-shell">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-lockup">
        <div class="brand-mark" aria-hidden="true">SWE</div>
        <div class="brand-title">
          <h1>tracer</h1>
        </div>
      </div>
    </div>
    <nav class="side-nav" aria-label="Dashboard sections">
      <button class="nav-item active" data-page="overview"><span class="nav-icon">{icon_overview}</span><span class="nav-label">Overview</span></button>
      <button class="nav-item" data-page="instances"><span class="nav-icon">{icon_instances}</span><span class="nav-label">Jobs</span></button>
      <button class="nav-item" data-page="trajectories"><span class="nav-icon">{icon_trajectories}</span><span class="nav-label">Trajectories</span></button>
      <button class="nav-item" data-page="operations"><span class="nav-icon">{icon_operations}</span><span class="nav-label">Operations</span></button>
    </nav>
    <div class="side-meta">
      <p><strong>Jobs</strong><br><code>{html.escape(str(jobs_dir))}</code></p>
      <p><strong>SFT</strong><br><code>{html.escape(str(sft_dir))}</code></p>
      <p><strong>Harbor</strong><br><code>{html.escape(str(harbor_jobs_dir))}</code></p>
    </div>
  </aside>
  <div class="main-shell">
    <div class="topbar">
      <div>
        <h2 id="pageTitle">Overview</h2>
        <p id="pageSubtitle">Monitor generation health, pass rate, quality score, and data coverage.</p>
      </div>
      <div class="topbar-actions">
        <span class="update-status">Updated {html.escape(now_str)} &middot; refresh {refresh_seconds}s</span>
        <button class="copy-btn" data-copy="data/summary.json">Copy summary</button>
        <button id="refreshNow" class="icon-btn refresh-btn" type="button" aria-label="Refresh dashboard" title="Refresh dashboard">{icon_refresh}</button>
        <button id="themeToggle" class="icon-btn theme-toggle" type="button" aria-label="Toggle black and white theme" title="Toggle theme"><span class="theme-moon">{icon_moon}</span><span class="theme-sun">{icon_sun}</span></button>
      </div>
    </div>
<main class="content">
  <section id="overview" class="section active" data-title="Overview" data-subtitle="Monitor generation health, pass rate, quality score, and data coverage.">
    <div class="grid kpis">
    <div class="card"><div class="label">Valid Trajs</div>
      <div class="value">{fmt_num(coverage.get('valid_trajs'))}</div>
      <div class="sub">for SFT in LF format</div></div>
    <div class="card"><div class="label">Valid Tokens</div>
      <div class="value">{fmt_tokens_b(coverage.get('valid_tokens'))}</div>
      <div class="sub">for SFT in LF format</div></div>
    <div class="card"><div class="label">Data Sources</div>
      <div class="value">{fmt_num(coverage.get('data_sources_count'))}</div>
      <div class="sub">SFT dataset jobs</div></div>
    <div class="card"><div class="label">Languages</div>
      <div class="value">{fmt_num(coverage.get('languages_count'))}</div>
      <div class="sub">{language_hint}</div></div>
    </div>
    <div class="grid kpis">
    <div class="card"><div class="label">Models</div>
      <div class="value">{fmt_num(coverage.get('models_count', coverage.get('teacher_models_count')))}</div>
      <div class="sub">{model_hint}</div></div>
    <div class="card"><div class="label">Scaffolds</div>
      <div class="value">{fmt_num(coverage.get('scaffolds_count'))}</div>
      <div class="sub">{scaffold_hint}</div></div>
    <div class="card"><div class="label">Instances Processed</div>
      <div class="value">{fmt_num(coverage.get('instances_processed'))}</div>
      <div class="sub">from {totals['n_jobs']:,} jobs</div></div>
    <div class="card"><div class="label">Pass Rate</div>
      <div class="value">{pass_rate_text}</div>
      <div class="sub">for all instances processed</div></div>
    </div>
    {stale_warning}
    <div class="split-layout">
      <section class="panel">
        <div class="panel-head"><div><h2>Programming Language Distribution</h2>
          <p class="hint">Based on valid SFT/LF trajectories with task language labels.</p></div></div>
        <div id="languageBars" class="mini-bars"></div>
      </section>
      <section class="panel">
        <div class="panel-head"><div><h2>Trajectory Quality Score Distribution</h2>
          <p class="hint">Score histogram from exported SFT quality facts.</p></div></div>
        <div id="scoreHistogram" class="mini-bars"></div>
      </section>
    </div>
  </section>

  <section id="instances" class="section" data-title="Jobs" data-subtitle="Slice jobs and trajectory sources by job, programming language, model, scaffold, domain, and quality/pass metrics.">
    <div class="segment-grid">{segment_panels}</div>
  </section>

  <section id="trajectories" class="section" data-title="Trajectories" data-subtitle="Compare trajectory sources, sample concrete runs, and inspect turn-by-turn tool behavior.">
    <section class="panel">
      <div class="panel-head"><div><h2>Valid Trajectories</h2>
        <p class="hint">Valid reward=1 trajectories converted for SFT, grouped by source job.</p></div>
        <div class="actions"><button class="copy-btn" data-copy="data/traj_cards.jsonl">Copy cards</button></div></div>
      <div class="table-wrap">
        <table id="trajSourceTable">
          <thead><tr>
            <th data-source-sort="source">Data Source</th>
            <th class="num" data-source-sort="n" title="Converted valid trajectories used for SFT. The subtext shows reward=1 trajectories before conversion/filtering.">n</th>
            <th data-source-sort="scaffold">Scaffold</th>
            <th class="num" data-source-sort="comp">Score</th>
            <th class="num" data-source-sort="turns">Turns</th>
            <th class="num" data-source-sort="calls">Calls/Traj</th>
            <th class="num" data-source-sort="error_rate">Error Rate</th>
            <th class="num" data-source-sort="tokens">Tokens</th>
            <th class="num" data-source-sort="cot_rate" title="Share of assistant turns with nonempty reasoning_content.">COT Turn Ratio</th>
            <th class="num" data-source-sort="cot_chars" title="Mean character length of nonempty assistant reasoning_content.">COT Chars/Turn</th>
            <th class="num" data-source-sort="task_difficulty" title="easy=1, medium=2, hard=3; unknown excluded.">Task Difficulty</th>
          </tr></thead>
          <tbody id="trajSourceRows"></tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h2>Trajectory Quality Score Matrix</h2>
        <p class="hint">Click column headers to sort. Only non-zero trajectory-score weights (Σw=1.00).</p></div></div>
      <div class="table-wrap">
        <table id="subscoreMatrixTable" class="subscore-matrix-table">
          <thead id="subscoreMatrixHead"></thead>
          <tbody id="subscoreMatrixRows"></tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h2>Trajectory Sampler</h2>
        <p class="hint">Embedded full trajectories load locally from data/traj_embedded shards; other cards fall back to /api/traj and the local path.</p></div>
        <div class="actions"><button id="trajResample" type="button">Resample</button></div></div>
      <div class="toolbar">
        <input id="trajSearch" placeholder="Search instance, source, language, status">
        <select id="trajSource"><option value="">All sources</option></select>
        <select id="trajLanguage"><option value="">All programming languages</option></select>
        <select id="trajMode">
          <option value="low">Lowest score / failures</option>
          <option value="high">Highest score</option>
          <option value="error">Most errors</option>
          <option value="clean">Clean / pass</option>
          <option value="random">Random sample</option>
        </select>
        <select id="trajSampleSize">
          <option value="10">10 cards</option>
          <option value="20" selected>20 cards</option>
          <option value="50">50 cards</option>
          <option value="100">100 cards</option>
        </select>
      </div>
      <div class="traj-layout">
        <div>
          <div class="card-title">Sampled Trajectories <span id="trajSampleInfo" class="hint"></span></div>
          <div id="trajList" class="traj-list step-list"></div>
        </div>
        <div id="trajView" class="traj-view empty">Select a trajectory card to inspect metrics, preview text, and full turn-by-turn trace when embedded or available through R2.</div>
      </div>
    </section>
  </section>

  <section id="operations" class="section" data-title="Operations" data-subtitle="Operator status, generated artifacts, and sync traceability.">
    {status_block}

    <section class="panel">
      <div class="panel-head"><div><h2>SFT Datasets</h2>
        <p class="hint">Stats from lf.stats.json with quick artifact path copy actions.</p></div></div>
      <div class="toolbar">
        <input id="sftSearch" placeholder="Search datasets, scaffolds, paths">
        <select id="sftScaffold"><option value="">All scaffolds</option>{scaffold_options}</select>
      </div>
      {sft_table}
    </section>
  </section>

  <div class="footer">Generated by dashboard/progress_monitor.py</div>
</main>
  </div>
</div>
<script id="sampleData" type="application/json">{samples_json}</script>
<script id="analysisData" type="application/json">{analysis_json}</script>
<script id="trajCardData" type="application/json">{traj_cards_json}</script>
<script id="trajSourceSummaryData" type="application/json">{traj_source_summary_json}</script>
<script id="errorSummaryData" type="application/json">{error_summary_json}</script>
<script>
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const sampleData = JSON.parse($('#sampleData').textContent || '[]');
const analysisData = JSON.parse($('#analysisData').textContent || '{{}}');
const trajCardData = JSON.parse($('#trajCardData').textContent || '[]');
const trajSourceSummaryData = JSON.parse($('#trajSourceSummaryData').textContent || '[]');
const errorSummaryData = JSON.parse($('#errorSummaryData').textContent || '{{}}');
const weightedSubscoreKeys = {json.dumps(WEIGHTED_SUBSCORE_KEYS, ensure_ascii=False)};
const tqsWeights = {json.dumps(TQS_WEIGHTS, ensure_ascii=False)};
const subscoreLabels = {json.dumps(SUBSCORE_LABELS, ensure_ascii=False)};
let currentSample = null;
let currentTraj = null;
let subscoreSort = {{field: 'composite_score', dir: 'desc'}};

function currentTheme() {{
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}}

function renderThemeToggle() {{
  const btn = $('#themeToggle');
  if (!btn) return;
  const theme = currentTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  btn.title = next === 'dark' ? 'Switch to black theme' : 'Switch to white theme';
  btn.setAttribute('aria-label', btn.title);
  btn.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
}}

function setTheme(theme) {{
  const next = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('tracer-theme', next);
  renderThemeToggle();
}}

function setPage(name) {{
  $$('.nav-item').forEach(btn => btn.classList.toggle('active', btn.dataset.page === name));
  $$('.section').forEach(section => section.classList.toggle('active', section.id === name));
  const section = $('#' + name);
  $('#pageTitle').textContent = section?.dataset.title || name;
  $('#pageSubtitle').textContent = section?.dataset.subtitle || '';
}}

function copyText(text) {{
  if (!text) return;
  if (navigator.clipboard) navigator.clipboard.writeText(text);
}}

function applyFilters() {{
  const sftQ = ($('#sftSearch')?.value || '').toLowerCase();
  const sftScaffold = $('#sftScaffold')?.value || '';
  $$('.sft-row').forEach(row => {{
    const ok = (!sftQ || row.dataset.search.includes(sftQ)) &&
      (!sftScaffold || row.dataset.scaffold === sftScaffold);
    row.classList.toggle('hidden', !ok);
  }});
}}

function cellValue(row, index) {{
  const text = row.children[index]?.innerText.trim() || '';
  const num = Number(text.replace(/[^0-9.-]/g, ''));
  return Number.isFinite(num) && /[0-9]/.test(text) ? num : text.toLowerCase();
}}

function sortTable(th) {{
  const table = th.closest('table');
  const tbody = table.querySelector('tbody');
  const index = Array.from(th.parentElement.children).indexOf(th);
  const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
  th.dataset.dir = dir;
  const rows = Array.from(tbody.querySelectorAll('tr')).filter(row => row.children.length === th.parentElement.children.length);
  rows.sort((a, b) => {{
    const av = cellValue(a, index);
    const bv = cellValue(b, index);
    if (av === bv) return 0;
    return (av > bv ? 1 : -1) * (dir === 'asc' ? 1 : -1);
  }});
  rows.forEach(row => tbody.appendChild(row));
}}

function sampleMatches(sample, query, dataset) {{
  if (dataset && sample.dataset !== dataset) return false;
  if (!query) return true;
  const hay = [
    sample.dataset, sample.instance_id, sample.source,
    ...(sample.messages || []).map(m => `${{m.role}} ${{m.content}}`)
  ].join(' ').toLowerCase();
  return hay.includes(query);
}}

function renderSampleList() {{
  const list = $('#sampleList');
  if (!list) return;
  const q = ($('#sampleSearch')?.value || '').toLowerCase();
  const dataset = $('#sampleDataset')?.value || '';
  const filtered = sampleData.filter(sample => sampleMatches(sample, q, dataset));
  if (!filtered.length) {{
    list.innerHTML = '<div class="empty">No sample previews match the current filters.</div>';
    $('#sampleDetail').innerHTML = 'No sample selected.';
    currentSample = null;
    return;
  }}
  list.innerHTML = filtered.map((sample, idx) => `
    <button class="sample-item ${{idx === 0 ? 'active' : ''}}" data-instance="${{escapeHtml(sample.instance_id)}}">
      <div class="sample-title">${{escapeHtml(sample.instance_id)}}</div>
      <div class="sample-meta">${{escapeHtml(sample.dataset)}} · score ${{formatMaybe(sample.score)}} · turns ${{formatMaybe(sample.turns)}} · tokens ${{formatMaybe(sample.tokens)}}</div>
    </button>
  `).join('');
  $$('.sample-item', list).forEach((btn, idx) => btn.addEventListener('click', () => selectSample(filtered[idx], btn)));
  selectSample(filtered[0], $('.sample-item', list));
}}

function formatMaybe(value) {{
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(4);
  return String(value);
}}

function formatPercent(value) {{
  if (value === null || value === undefined || value === '') return '-';
  return `${{(Number(value) * 100).toFixed(2)}}%`;
}}

function formatTokenUnits(value) {{
  if (value === null || value === undefined || value === '') return '-';
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return `${{(n / 1_000_000_000).toFixed(abs >= 10_000_000_000 ? 1 : 2)}}B`;
  if (abs >= 1_000_000) return `${{(n / 1_000_000).toFixed(abs >= 10_000_000 ? 1 : 2)}}M`;
  if (abs >= 1_000) return `${{(n / 1_000).toFixed(abs >= 10_000 ? 1 : 2)}}K`;
  return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1);
}}

function stableIdentityColor(value) {{
  const colors = {json.dumps(IDENTITY_COLORS)};
  const text = String(value || '');
  let seed = 0;
  for (let i = 0; i < text.length; i += 1) seed = (seed * 31 + text.charCodeAt(i)) >>> 0;
  return colors[seed % colors.length];
}}

function formatToolErrorRate(successRate) {{
  if (successRate === null || successRate === undefined || successRate === '') return '-';
  const n = Number(successRate);
  if (!Number.isFinite(n)) return '-';
  return formatPercent(Math.max(0, Math.min(1, 1 - n)));
}}

function formatDateTime(value) {{
  if (!value) return 'not finished';
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {{
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }});
}}

function populateSelect(id, values) {{
  const el = $('#' + id);
  if (!el) return;
  const first = el.querySelector('option')?.outerHTML || '<option value="">All</option>';
  el.innerHTML = first + Array.from(values).filter(Boolean).sort().map(v => `<option value="${{escapeHtml(v)}}">${{escapeHtml(v)}}</option>`).join('');
}}

function renderMiniBars(id, rows, formatter = formatMaybe) {{
  const el = $('#' + id);
  if (!el) return;
  if (!rows.length) {{
    el.innerHTML = '<div class="empty">No data available.</div>';
    return;
  }}
  const maxValue = Math.max(1, ...rows.map(row => Number(row.value) || 0));
  el.innerHTML = rows.slice(0, 14).map(row => `
    <div class="mini-bar-row">
      <div class="mini-bar-label" title="${{escapeHtml(row.label)}}">${{escapeHtml(row.label)}}</div>
      <div class="mini-bar-track"><div class="mini-bar-fill" style="width:${{Math.max(2, (Number(row.value) || 0) / maxValue * 100).toFixed(1)}}%"></div></div>
      <div class="mini-bar-value">${{formatter(row.value)}}</div>
    </div>
  `).join('');
}}

function renderOverviewCharts() {{
  const languageRows = (analysisData.segments || [])
    .filter(row => row.dim === 'language' && (row.quality_records || 0) > 0)
    .map(row => ({{label: row.value || 'unknown', value: row.quality_records || 0}}))
    .sort((a, b) => b.value - a.value || String(a.label).localeCompare(String(b.label)));
  renderMiniBars('languageBars', languageRows);
  const hist = (errorSummaryData.score_histogram || []).map(bin => ({{
    label: bin.label || `${{Number(bin.lo).toFixed(1)}}-${{Number(bin.hi).toFixed(1)}}`,
    value: bin.count || 0,
  }}));
  renderMiniBars('scoreHistogram', hist);
}}

let trajSampleSeed = 1;
const embeddedShardCache = {{}};
let trajSourceSort = {{field: 'comp', dir: 'desc'}};

function trajSourceName(card) {{
  return card.job || card.dataset || card.source || 'unknown';
}}

function trajNumeric(card, keys, fallback) {{
  for (const key of keys) {{
    const value = Number(card[key]);
    if (Number.isFinite(value)) return value;
  }}
  return fallback;
}}

function trajErrorValue(card) {{
  if (card.status === 'error') return 1;
  const success = Number(card.tool_success_rate);
  if (Number.isFinite(success)) return Math.max(0, Math.min(1, 1 - success));
  return 0;
}}

function trajAvailabilityRank(card) {{
  if (card.embedded_available) return 0;
  if (card.full_available) return 1;
  return 2;
}}

function seededShuffle(rows, seed) {{
  const arr = rows.slice();
  let s = (seed * 2654435761) >>> 0;
  const rnd = () => {{
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    return s / 0x7fffffff;
  }};
  for (let i = arr.length - 1; i > 0; i--) {{
    const j = Math.floor(rnd() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }}
  return arr;
}}

function trajSortCards(cards, mode) {{
  const available = cards.filter(card => trajAvailabilityRank(card) < 2);
  const pool = available.length ? available : cards;
  const availCmp = (a, b) => trajAvailabilityRank(a) - trajAvailabilityRank(b);
  if (mode === 'random') return seededShuffle(pool, trajSampleSeed);
  if (mode === 'high') {{
    return pool.slice().sort((a, b) => availCmp(a, b) || trajNumeric(b, ['score', 'reward'], -1) - trajNumeric(a, ['score', 'reward'], -1) || String(a.id).localeCompare(String(b.id)));
  }}
  if (mode === 'error') {{
    return pool.slice().sort((a, b) => availCmp(a, b) || trajErrorValue(b) - trajErrorValue(a) || String(a.id).localeCompare(String(b.id)));
  }}
  if (mode === 'clean') {{
    return pool.filter(card => card.status === 'pass' || card.status === 'scored' || trajErrorValue(card) === 0)
      .sort((a, b) => availCmp(a, b) || trajNumeric(b, ['score', 'reward'], -1) - trajNumeric(a, ['score', 'reward'], -1) || String(a.id).localeCompare(String(b.id)));
  }}
  return pool.slice().sort((a, b) => {{
    const availability = availCmp(a, b);
    if (availability) return availability;
    const statusRank = card => card.status === 'error' ? 0 : card.status === 'fail' ? 1 : card.kind === 'quality' ? 2 : 3;
    const ar = statusRank(a), br = statusRank(b);
    if (ar !== br) return ar - br;
    return trajNumeric(a, ['score', 'reward'], 999) - trajNumeric(b, ['score', 'reward'], 999) || String(a.id).localeCompare(String(b.id));
  }});
}}

function topCountLabel(counts) {{
  const entries = Object.entries(counts || {{}}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return '-';
  return entries[0][0];
}}

function sourceComparisonRows() {{
  if (Array.isArray(trajSourceSummaryData) && trajSourceSummaryData.length) {{
    const numeric = value => {{
      const n = Number(value);
      return Number.isFinite(n) ? n : null;
    }};
    return trajSourceSummaryData.map(row => ({{
      ...row,
      n: Number(row.n) || 0,
      reward_1_count: numeric(row.reward_1_count),
      conversion_drop_count: numeric(row.conversion_drop_count),
      pass: Number(row.pass) || 0,
      fail: Number(row.fail) || 0,
      error: Number(row.error) || 0,
      embedded: Number(row.embedded) || 0,
      comp: numeric(row.comp),
      avg_turns: numeric(row.avg_turns),
      avg_calls: numeric(row.avg_calls),
      avg_tokens: numeric(row.avg_tokens),
      error_rate: numeric(row.error_rate),
      cot_rate: numeric(row.cot_rate),
      cot_chars_mean: numeric(row.cot_chars_mean),
      task_difficulty_avg: numeric(row.task_difficulty_avg),
      subs: row.subs && typeof row.subs === 'object' ? Object.fromEntries(
        Object.entries(row.subs).map(([key, value]) => [key, numeric(value)])
      ) : {{}},
      pass_rate: numeric(row.pass_rate),
      embedded_rate: numeric(row.embedded_rate),
      scaffold: row.scaffold || '-',
      model: row.model || '-',
      difficulty: row.difficulty || '-',
    }}));
  }}
  const difficultyScore = {{easy: 1, medium: 2, hard: 3}};
  const groups = new Map();
  for (const card of trajCardData.filter(card => card.kind === 'quality')) {{
    const key = trajSourceName(card);
    if (!groups.has(key)) groups.set(key, {{
      source: key,
      n: 0,
      pass: 0,
      fail: 0,
      error: 0,
      embedded: 0,
      scores: [],
      turns: [],
      calls: [],
      tokens: [],
      cot_turns: 0,
      cot_nonempty_turns: 0,
      cot_chars_sum: 0,
      difficulty_scores: [],
      scaffolds: {{}},
      models: {{}},
      difficulties: {{}},
    }});
    const row = groups.get(key);
    row.n += 1;
    if (card.status === 'pass' || card.status === 'scored') row.pass += 1;
    else if (card.status === 'error') row.error += 1;
    else if (card.status === 'fail') row.fail += 1;
    if (card.embedded_available) row.embedded += 1;
    const score = Number(card.score ?? card.reward);
    if (Number.isFinite(score)) row.scores.push(score);
    const turns = Number(card.turns);
    if (Number.isFinite(turns)) row.turns.push(turns);
    const calls = Number(card.tool_calls);
    if (Number.isFinite(calls)) row.calls.push(calls);
    const tokens = Number(card.tokens);
    if (Number.isFinite(tokens)) row.tokens.push(tokens);
    const cotTurns = Number(card.cot_turns);
    const cotNonempty = Number(card.cot_nonempty_turns);
    const cotCharsSum = Number(card.cot_chars_sum);
    if (Number.isFinite(cotTurns)) row.cot_turns += cotTurns;
    if (Number.isFinite(cotNonempty)) row.cot_nonempty_turns += cotNonempty;
    if (Number.isFinite(cotCharsSum)) row.cot_chars_sum += cotCharsSum;
    const scaffold = card.scaffold || 'unknown';
    row.scaffolds[scaffold] = (row.scaffolds[scaffold] || 0) + 1;
    const model = card.model || 'unknown';
    row.models[model] = (row.models[model] || 0) + 1;
    const difficulty = String(card.difficulty || 'unknown').toLowerCase();
    row.difficulties[difficulty] = (row.difficulties[difficulty] || 0) + 1;
    if (difficultyScore[difficulty] != null) row.difficulty_scores.push(difficultyScore[difficulty]);
  }}
  return Array.from(groups.values()).map(row => {{
    const avg = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
    return {{
      ...row,
      comp: avg(row.scores),
      avg_turns: avg(row.turns),
      avg_calls: avg(row.calls),
      avg_tokens: avg(row.tokens),
      error_rate: row.n ? row.error / row.n : null,
      cot_rate: row.cot_turns ? row.cot_nonempty_turns / row.cot_turns : null,
      cot_chars_mean: row.cot_nonempty_turns ? row.cot_chars_sum / row.cot_nonempty_turns : null,
      task_difficulty_avg: avg(row.difficulty_scores),
      pass_rate: row.n ? row.pass / row.n : null,
      embedded_rate: row.n ? row.embedded / row.n : null,
      scaffold: topCountLabel(row.scaffolds),
      model: topCountLabel(row.models),
      difficulty: topCountLabel(row.difficulties),
    }};
  }});
}}

function sourceSortValue(row, field) {{
  if (field === 'source' || field === 'scaffold' || field === 'difficulty') return String(row[field] || '').toLowerCase();
  if (field === 'n') return row.n || 0;
  if (field === 'comp') return row.comp ?? -Infinity;
  if (field === 'turns') return row.avg_turns ?? -Infinity;
  if (field === 'calls') return row.avg_calls ?? -Infinity;
  if (field === 'error_rate') return row.error_rate ?? -Infinity;
  if (field === 'pass_rate') return row.pass_rate ?? -Infinity;
  if (field === 'tokens') return row.avg_tokens ?? -Infinity;
  if (field === 'cot_rate') return row.cot_rate ?? -Infinity;
  if (field === 'cot_chars') return row.cot_chars_mean ?? -Infinity;
  if (field === 'task_difficulty') return row.task_difficulty_avg ?? -Infinity;
  if (field === 'embedded') return row.embedded_rate ?? -Infinity;
  return row[field] ?? -Infinity;
}}

function compareSourceRows(a, b) {{
  const field = trajSourceSort.field;
  const av = sourceSortValue(a, field);
  const bv = sourceSortValue(b, field);
  let result;
  if (typeof av === 'string' || typeof bv === 'string') result = String(av || '').localeCompare(String(bv || ''));
  else result = av === bv ? 0 : av > bv ? 1 : -1;
  if (result === 0) result = String(a.source).localeCompare(String(b.source));
  return result * (trajSourceSort.dir === 'asc' ? 1 : -1);
}}

function sourceRatePill(value, reverse = false) {{
  const n = Number(value);
  if (!Number.isFinite(n)) return '<span class="muted">-</span>';
  let cls = 'good';
  if (reverse) cls = n >= 0.15 ? 'bad' : n >= 0.08 ? 'warn' : 'good';
  else cls = n >= 0.6 ? 'good' : n >= 0.3 ? 'warn' : 'bad';
  return `<span class="metric-pill ${{cls}}">${{formatPercent(n)}}</span>`;
}}

function renderSourceN(row) {{
  const lines = [];
  if (Number.isFinite(Number(row.reward_1_count))) {{
    lines.push(`from ${{formatMaybe(row.reward_1_count)}} reward=1`);
  }}
  if (Number.isFinite(Number(row.conversion_drop_count)) && Number(row.conversion_drop_count) > 0) {{
    lines.push(`${{formatMaybe(row.conversion_drop_count)}} filtered`);
  }}
  return `${{formatMaybe(row.n)}}${{lines.map(line => `<div class="muted">${{escapeHtml(line)}}</div>`).join('')}}`;
}}

function updateSourceSortHeaders() {{
  $$('[data-source-sort]').forEach(th => {{
    const active = th.dataset.sourceSort === trajSourceSort.field;
    th.classList.toggle('segment-sort', true);
    th.classList.toggle('active', active);
    th.classList.toggle('asc', active && trajSourceSort.dir === 'asc');
    th.classList.toggle('desc', active && trajSourceSort.dir === 'desc');
    th.setAttribute('aria-sort', active ? (trajSourceSort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
  }});
}}

function renderTrajectorySourceTable() {{
  const root = $('#trajSourceRows');
  if (!root) return;
  const rows = sourceComparisonRows().sort(compareSourceRows);
  updateSourceSortHeaders();
  if (!rows.length) {{
    root.innerHTML = '<tr><td colspan="11" class="empty">No trajectory sources available.</td></tr>';
    return;
  }}
  root.innerHTML = rows.map(row => {{
    const difficulty = Number(row.task_difficulty_avg);
    const difficultyText = Number.isFinite(difficulty) ? difficulty.toFixed(2) : '-';
    const cotChars = Number(row.cot_chars_mean);
    const cotCharsText = Number.isFinite(cotChars) ? Math.round(cotChars).toLocaleString() : '-';
    return `
      <tr>
        <td>
          <button class="source-link" type="button" data-source-filter="${{escapeHtml(row.source)}}">
            <span class="source-name"><span class="source-dot" style="background:${{stableIdentityColor(row.source)}}"></span>${{escapeHtml(row.source)}}</span>
            <div class="source-sub">${{escapeHtml(row.model)}} · ${{formatMaybe(row.pass)}} pass · ${{formatMaybe(row.fail)}} fail · ${{formatMaybe(row.error)}} error</div>
          </button>
        </td>
        <td class="num">${{renderSourceN(row)}}</td>
        <td>${{escapeHtml(row.scaffold)}}</td>
        <td class="num">${{formatMaybe(row.comp)}}</td>
        <td class="num">${{formatMaybe(row.avg_turns)}}</td>
        <td class="num">${{formatMaybe(row.avg_calls)}}</td>
        <td class="num">${{sourceRatePill(row.error_rate, true)}}</td>
        <td class="num">${{formatTokenUnits(row.avg_tokens)}}</td>
        <td class="num">${{formatPercent(row.cot_rate)}}</td>
        <td class="num">${{escapeHtml(cotCharsText)}}</td>
        <td class="num">${{escapeHtml(difficultyText)}}</td>
      </tr>
    `;
  }}).join('');
}}

function subscoreMatrixValue(row, field) {{
  if (field === 'source') return String(row.source || '').toLowerCase();
  if (field === 'composite_score') return Number(row.comp);
  const subs = row.subs || {{}};
  return Number(subs[field]);
}}

function compareSubscoreRows(a, b) {{
  const field = subscoreSort.field;
  const av = subscoreMatrixValue(a, field);
  const bv = subscoreMatrixValue(b, field);
  let result;
  if (field === 'source') result = String(av || '').localeCompare(String(bv || ''));
  else {{
    const an = Number.isFinite(av) ? av : Number.NEGATIVE_INFINITY;
    const bn = Number.isFinite(bv) ? bv : Number.NEGATIVE_INFINITY;
    result = an === bn ? 0 : an > bn ? 1 : -1;
  }}
  if (result === 0) result = String(a.source || '').localeCompare(String(b.source || ''));
  return result * (subscoreSort.dir === 'asc' ? 1 : -1);
}}

function subscoreHeatCell(value, extraClass = '') {{
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) {{
    return `<td class="num ${{extraClass}}">-</td>`;
  }}
  const n = Math.max(0, Math.min(1, Number(value)));
  const alpha = (n * 0.5).toFixed(3);
  return `<td class="num ${{extraClass}}" style="background:rgba(99,102,241,${{alpha}})">${{Number(value).toFixed(3)}}</td>`;
}}

function renderSubscoreMatrix() {{
  const head = $('#subscoreMatrixHead');
  const body = $('#subscoreMatrixRows');
  if (!head || !body) return;
  const keys = Array.isArray(weightedSubscoreKeys) ? weightedSubscoreKeys : [];
  const arrow = subscoreSort.dir === 'asc' ? ' ▴' : ' ▾';
  const th = (label, field, {{cls = '', title = '', weight = ''}} = {{}}) => {{
    const active = subscoreSort.field === field;
    const weightHtml = weight ? `<span class="sub-weight">${{escapeHtml(weight)}}</span>` : '';
    return `<th class="num segment-sort ${{cls}} ${{active ? 'active ' + subscoreSort.dir : ''}}" data-subscore-sort="${{escapeHtml(field)}}" title="${{escapeHtml(title)}}"><div>${{escapeHtml(label)}}${{active ? arrow : ''}}</div>${{weightHtml}}</th>`;
  }};
  head.innerHTML = `<tr>
    <th class="segment-sort ${{subscoreSort.field === 'source' ? 'active ' + subscoreSort.dir : ''}}" data-subscore-sort="source">Data Source${{subscoreSort.field === 'source' ? arrow : ''}}</th>
    ${{th('Score', 'composite_score', {{cls: 'score-sep', title: subscoreLabels.composite_score || 'Trajectory score', weight: 'composite'}})}}
    ${{keys.map(key => th(key.replace('_score', ''), key, {{
      title: subscoreLabels[key] || key,
      weight: `w=${{Number(tqsWeights[key] || 0).toFixed(2)}}`,
    }})).join('')}}
  </tr>`;
  const rows = sourceComparisonRows().sort(compareSubscoreRows);
  if (!rows.length) {{
    body.innerHTML = `<tr><td colspan="${{keys.length + 2}}" class="empty">No trajectory sources available.</td></tr>`;
    return;
  }}
  body.innerHTML = rows.map(row => {{
    const subs = row.subs || {{}};
    return `<tr>
      <td class="mono">${{escapeHtml(row.source || '-')}}</td>
      ${{subscoreHeatCell(row.comp, 'score-sep')}}
      ${{keys.map(key => subscoreHeatCell(subs[key])).join('')}}
    </tr>`;
  }}).join('');
  $$('[data-subscore-sort]', head).forEach(thEl => {{
    thEl.addEventListener('click', () => {{
      const field = thEl.dataset.subscoreSort;
      subscoreSort = {{
        field,
        dir: subscoreSort.field === field && subscoreSort.dir === 'desc' ? 'asc' : 'desc',
      }};
      renderSubscoreMatrix();
    }});
  }});
}}

function renderTrajectoryList() {{
  const list = $('#trajList');
  if (!list) return;
  const q = ($('#trajSearch')?.value || '').toLowerCase();
  const lang = $('#trajLanguage')?.value || '';
  const source = $('#trajSource')?.value || '';
  const mode = $('#trajMode')?.value || 'low';
  const sampleSize = Number($('#trajSampleSize')?.value || 20);
  let cards = trajCardData
    .filter(card => !lang || card.language === lang)
    .filter(card => !source || trajSourceName(card) === source)
    .filter(card => {{
      if (!q) return true;
      const hay = [card.id, card.instance_id, card.task_name, card.job, card.dataset, card.source, card.language, card.status, card.model, card.scaffold, card.exception_type].join(' ').toLowerCase();
      return hay.includes(q);
    }});
  const total = cards.length;
  cards = trajSortCards(cards, mode).slice(0, Number.isFinite(sampleSize) ? sampleSize : 20);
  const info = $('#trajSampleInfo');
  if (info) info.textContent = `${{cards.length}} / ${{total}} shown`;
  if (!cards.length) {{
    list.innerHTML = '<div class="empty">No trajectory cards match the current filters.</div>';
    $('#trajView').innerHTML = 'No trajectory selected.';
    currentTraj = null;
    return;
  }}
  list.innerHTML = cards.map((card, idx) => `
    <button class="step-row traj-card ${{idx === 0 ? 'active' : ''}}" data-id="${{escapeHtml(card.id)}}">
      <span class="id">#${{idx + 1}}</span>
      <span class="step-row-body">
        <span class="desc" title="${{escapeHtml(card.instance_id || card.task_name || card.id)}}">${{escapeHtml(card.instance_id || card.task_name || card.id)}}</span>
        <span class="step-badges">
          <span class="step-badge ${{card.status === 'error' ? 'bad' : card.status === 'fail' ? 'warn' : 'good'}}">${{escapeHtml(card.status || card.kind || '-')}}</span>
          <span class="step-badge accent">score ${{formatMaybe(card.score)}}</span>
          <span class="step-badge">reward ${{formatMaybe(card.reward)}}</span>
          <span class="step-badge">${{formatMaybe(card.turns)}} turns</span>
          <span class="step-badge">${{formatMaybe(card.tool_calls)}} calls</span>
          ${{card.embedded_available ? '<span class="step-badge good">embedded</span>' : ''}}
        </span>
        <span class="traj-card-meta">${{escapeHtml(trajSourceName(card))}} · ${{escapeHtml(card.language || 'unknown')}} · ${{escapeHtml(card.model || '-')}}</span>
      </span>
    </button>
  `).join('');
  $$('.traj-card', list).forEach((btn, idx) => btn.addEventListener('click', () => selectTrajectory(cards[idx], btn)));
  selectTrajectory(cards[0], $('.traj-card', list));
}}

function scoreBreakdown(card) {{
  const keys = ['score_v3', 'score_v4', 'efficiency_score', 'style_score', 'tool_mastery_score', 'completion_score', 'precision_score'];
  const rows = keys.filter(key => card[key] !== undefined && card[key] !== null).map(key => `<dt>${{escapeHtml(key)}}</dt><dd>${{formatMaybe(card[key])}}</dd>`).join('');
  return rows ? `<h2>Score Breakdown</h2><dl class="kv">${{rows}}</dl>` : '';
}}

function selectTrajectory(card, button) {{
  currentTraj = card;
  $$('.traj-card').forEach(item => item.classList.remove('active'));
  if (button) button.classList.add('active');
  const view = $('#trajView');
  view.classList.remove('empty');
  const preview = card.preview ? `<h2>Preview</h2><div class="detail-preview">${{escapeHtml(card.preview)}}</div>` : '';
  const error = card.exception_type ? `<dt>Exception</dt><dd>${{escapeHtml(card.exception_type)}}</dd>` : '';
  const loadAction = card.embedded_available || card.full_available
    ? `<button id="loadFullTraj" type="button">${{card.embedded_available ? 'Open embedded trace' : 'Load full'}}</button>`
    : '';
  view.innerHTML = `
    <div class="panel-head"><div><h2>${{escapeHtml(card.instance_id || card.task_name || card.id)}}</h2>
      <p class="hint">${{escapeHtml(card.kind)}} · ${{escapeHtml(card.job || card.dataset || '-')}} · ${{escapeHtml(card.status || '-')}}</p></div>
      <div class="actions"><button class="copy-btn" data-copy="${{escapeHtml(card.r2_key || '')}}">Copy R2 key</button><button class="copy-btn" data-copy="${{escapeHtml(card.path || card.trajectory_path || '')}}">Copy local path</button>${{loadAction}}</div></div>
    <dl class="kv">
      <dt>Language</dt><dd>${{escapeHtml(card.language || 'unknown')}}</dd>
      <dt>Domain</dt><dd>${{escapeHtml(card.domain || 'unknown')}} · ${{escapeHtml(card.category || 'unknown')}} · ${{escapeHtml(card.difficulty || 'unknown')}}</dd>
      <dt>Model</dt><dd>${{escapeHtml(card.model || '-')}} · ${{escapeHtml(card.scaffold || '-')}}</dd>
      <dt>Score</dt><dd>${{formatMaybe(card.score)}} · reward ${{formatMaybe(card.reward)}} · tool ${{formatPercent(card.tool_success_rate)}}</dd>
      <dt>Usage</dt><dd>${{formatMaybe(card.turns)}} turns · ${{formatMaybe(card.tool_calls)}} tool calls · ${{formatMaybe(card.tokens)}} tokens · ${{formatMaybe(card.cost_usd)}} USD</dd>
      <dt>Embedded</dt><dd>${{card.embedded_available ? `yes · ${{escapeHtml(card.embedded_path || '-')}}` : 'no'}}</dd>
      <dt>R2 key</dt><dd><code>${{escapeHtml(card.r2_key || '-')}}</code></dd>
      <dt>Local</dt><dd><code>${{escapeHtml(card.trajectory_path || card.path || '-')}}</code></dd>
      ${{error}}
    </dl>
    ${{scoreBreakdown(card)}}
    ${{preview}}
    <div id="fullTrajResult" class="json-block hidden"></div>
  `;
  $('#loadFullTraj')?.addEventListener('click', () => loadFullTrajectory(card));
}}

async function loadFullTrajectory(card) {{
  const box = $('#fullTrajResult');
  if (!box) return;
  box.classList.remove('hidden');
  box.textContent = card.embedded_available ? 'Loading embedded trajectory...' : 'Loading /api/traj...';
  try {{
    const data = card.embedded_available ? await loadEmbeddedTrajectory(card) : await fetchRemoteTrajectory(card);
    renderFullTrajectory(box, card, data);
  }} catch (err) {{
    box.textContent = `Full trajectory is not available through /api/traj in this environment.\\nR2 key: ${{card.r2_key || '-'}}\\nLocal path: ${{card.trajectory_path || card.path || '-'}}\\n${{err}}`;
  }}
}}

async function fetchRemoteTrajectory(card) {{
  const resp = await fetch(`/api/traj?r2_key=${{encodeURIComponent(card.r2_key || '')}}`);
  if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
  return resp.json();
}}

async function loadEmbeddedTrajectory(card) {{
  const path = card.embedded_path;
  if (!path) throw new Error('embedded_path is missing');
  if (!embeddedShardCache[path]) {{
    const resp = await fetch(path);
    if (!resp.ok) throw new Error(`embedded ${{path}} HTTP ${{resp.status}}`);
    const text = await resp.text();
    const map = new Map();
    for (const line of text.split(/\\n/)) {{
      if (!line.trim()) continue;
      const row = JSON.parse(line);
      map.set(String(row.id), row.record);
    }}
    embeddedShardCache[path] = map;
  }}
  const record = embeddedShardCache[path].get(String(card.id));
  if (!record) throw new Error(`embedded trajectory not found for ${{card.id}}`);
  return {{record}};
}}

function normalizeTrajectoryPayload(data) {{
  if (!data) return null;
  if (data.record) return data.record;
  if (data.text) {{
    try {{ return JSON.parse(data.text); }} catch (err) {{ return {{text: data.text}}; }}
  }}
  return data;
}}

function contentToText(content) {{
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.map(part => typeof part === 'string' ? part : (part?.text || part?.content || JSON.stringify(part))).join('\\n');
  if (content === null || content === undefined) return '';
  if (typeof content === 'object') return JSON.stringify(content, null, 2);
  return String(content);
}}

function renderFullTrajectory(box, card, data) {{
  const record = normalizeTrajectoryPayload(data);
  box.classList.remove('json-block');
  box.innerHTML = '';
  if (!record || typeof record !== 'object') {{
    box.classList.add('json-block');
    box.textContent = JSON.stringify(data, null, 2);
    return;
  }}
  const wrap = document.createElement('div');
  wrap.className = 'trace-detail';
  wrap.innerHTML = `
    <div class="trace-header">
      <div class="trace-header-row">
        <h2 class="trace-title">${{escapeHtml(card.instance_id || card.task_name || card.id)}}</h2>
        <span class="step-badge accent">${{escapeHtml(card.scaffold || '-')}}</span>
        <span class="step-badge ${{card.status === 'error' ? 'bad' : card.status === 'fail' ? 'warn' : 'good'}}">${{escapeHtml(card.status || card.kind || '-')}}</span>
        <span class="step-badge">score ${{formatMaybe(card.score)}}</span>
      </div>
      <p class="hint">${{escapeHtml(trajSourceName(card))}} · ${{escapeHtml(card.model || '-')}} · ${{escapeHtml(card.language || 'unknown')}}</p>
    </div>
  `;
  if (Array.isArray(record.steps)) renderStepsTrace(wrap, record);
  else if (Array.isArray(record.messages)) renderMessagesTrace(wrap, record);
  else {{
    const pre = document.createElement('pre');
    pre.className = 'block-pre';
    pre.textContent = JSON.stringify(record, null, 2);
    wrap.appendChild(pre);
  }}
  box.appendChild(wrap);
}}

function renderStepsTrace(root, record) {{
  const steps = record.steps || [];
  const system = steps.find(step => step.source === 'system');
  const user = steps.find(step => step.source === 'user');
  if (system) root.appendChild(prefaceCard('system prompt', contentToText(system.message), false));
  if (user) root.appendChild(prefaceCard('user task', contentToText(user.message), true));
  const list = document.createElement('div');
  list.className = 'turns-list';
  steps.filter(step => step.source === 'agent' || step.tool_calls || step.observation).forEach((step, idx) => {{
    list.appendChild(stepTurnCard(step, idx, idx < 2));
  }});
  root.appendChild(list);
}}

function renderMessagesTrace(root, record) {{
  const messages = record.messages || [];
  const system = messages.find(msg => msg.role === 'system');
  const firstUser = messages.find(msg => msg.role === 'user');
  if (system) root.appendChild(prefaceCard('system prompt', contentToText(system.content), false));
  if (firstUser) root.appendChild(prefaceCard('user task', contentToText(firstUser.content), true));
  const list = document.createElement('div');
  list.className = 'turns-list';
  buildMessageTurns(messages).forEach((turn, idx) => list.appendChild(messageTurnCard(turn, idx, idx < 2)));
  root.appendChild(list);
}}

function prefaceCard(label, text, open) {{
  const details = document.createElement('details');
  details.className = 'preface-card';
  if (open) details.setAttribute('open', '');
  details.innerHTML = `<summary><span class="block-label">${{escapeHtml(label)}}</span><span class="muted">${{formatMaybe((text || '').length)}} chars</span></summary><pre class="preface-pre">${{escapeHtml(text || '')}}</pre>`;
  return details;
}}

function stepTurnCard(step, idx, open) {{
  const toolCalls = Array.isArray(step.tool_calls) ? step.tool_calls : [];
  const observations = Array.isArray(step.observation?.results) ? step.observation.results : [];
  return turnCardShell(idx, open, toolCalls.map(toolNameFromStepCall), observations.length, body => {{
    const text = contentToText(step.message);
    if (text.trim()) body.appendChild(traceBlock('thought', 'assistant', text));
    for (const call of toolCalls) body.appendChild(actionBlock(call));
    for (const obs of observations) body.appendChild(observationBlock(obs.content, obs.source_call_id));
    if (!body.children.length) body.appendChild(emptySmall('(empty turn)'));
  }});
}}

function buildMessageTurns(messages) {{
  const turns = [];
  let current = null;
  for (const msg of messages) {{
    if (msg.role === 'assistant') {{
      current = {{assistant: msg, tools: []}};
      turns.push(current);
    }} else if (msg.role === 'tool') {{
      if (!current) {{ current = {{assistant: null, tools: []}}; turns.push(current); }}
      current.tools.push(msg);
    }} else if (msg.role === 'user' && current) {{
      current.tools.push({{role: 'user', content: msg.content, _user: true}});
    }}
  }}
  return turns;
}}

function messageTurnCard(turn, idx, open) {{
  const assistant = turn.assistant || {{}};
  const toolCalls = Array.isArray(assistant.tool_calls) ? assistant.tool_calls : [];
  return turnCardShell(idx, open, toolCalls.map(toolNameFromMessageCall), turn.tools.length, body => {{
    if (assistant.reasoning_content) body.appendChild(traceBlock('thought', 'thought / reasoning', assistant.reasoning_content));
    const text = contentToText(assistant.content);
    if (text.trim()) body.appendChild(traceBlock('thought', 'assistant', text));
    for (const call of toolCalls) body.appendChild(actionBlock(call));
    for (const obs of turn.tools) body.appendChild(observationBlock(contentToText(obs.content), obs._user ? 'user feedback' : 'observation'));
    if (!body.children.length) body.appendChild(emptySmall('(empty turn)'));
  }});
}}

function turnCardShell(idx, open, toolNames, obsCount, fillBody) {{
  const card = document.createElement('div');
  card.className = 'turn-card' + (open ? ' open' : '');
  const body = document.createElement('div');
  body.className = 'turn-body';
  body.style.display = open ? 'flex' : 'none';
  const button = document.createElement('button');
  button.className = 'turn-head';
  button.type = 'button';
  button.innerHTML = `
    <span class="turn-chev">${{open ? 'v' : '>'}}</span>
    <span class="turn-num">Turn ${{idx + 1}}</span>
    <span class="turn-tools">${{toolNames.length ? toolNames.map(name => `<span class="step-badge warn">${{escapeHtml(name)}}</span>`).join('') : '<span class="muted">no tool call</span>'}}</span>
    <span class="turn-spacer"></span>
    <span class="muted">${{obsCount}} obs</span>
  `;
  button.addEventListener('click', () => {{
    const isOpen = card.classList.toggle('open');
    body.style.display = isOpen ? 'flex' : 'none';
    $('.turn-chev', button).textContent = isOpen ? 'v' : '>';
  }});
  fillBody(body);
  card.appendChild(button);
  card.appendChild(body);
  return card;
}}

function toolNameFromStepCall(call) {{
  return call?.function_name || call?.function?.name || 'tool';
}}

function toolNameFromMessageCall(call) {{
  return call?.function?.name || call?.function_name || 'tool';
}}

function actionBlock(call) {{
  const name = toolNameFromStepCall(call);
  let args = call?.arguments ?? call?.function?.arguments;
  if (typeof args === 'string') {{
    try {{ args = JSON.parse(args); }} catch (err) {{}}
  }}
  const wrap = traceBlock('action', 'action', '', name);
  const pre = $('.block-pre', wrap);
  if (args && typeof args === 'object' && !Array.isArray(args)) {{
    pre.remove();
    const body = document.createElement('div');
    for (const [key, value] of Object.entries(args)) {{
      const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
      if (text.length > 100 || text.includes('\\n')) {{
        const block = document.createElement('div');
        block.className = 'arg-block';
        block.innerHTML = `<span class="arg-key">${{escapeHtml(key)}}</span><pre class="block-pre">${{escapeHtml(text)}}</pre>`;
        body.appendChild(block);
      }} else {{
        const pill = document.createElement('span');
        pill.className = 'arg-pill';
        pill.innerHTML = `<span class="arg-key">${{escapeHtml(key)}}</span><span>${{escapeHtml(text)}}</span>`;
        body.appendChild(pill);
      }}
    }}
    wrap.appendChild(body);
  }} else {{
    pre.textContent = contentToText(args);
  }}
  return wrap;
}}

function observationBlock(text, label) {{
  const isErr = obsLooksError(text);
  return traceBlock(isErr ? 'observation error' : 'observation', label || 'observation', text || '', isErr ? 'error?' : '');
}}

function traceBlock(kind, label, text, toolName = '') {{
  const wrap = document.createElement('div');
  wrap.className = `trace-block ${{kind}}`;
  wrap.innerHTML = `<div class="block-head"><span class="block-label">${{escapeHtml(label)}}</span>${{toolName ? `<span class="block-tool-name">${{escapeHtml(toolName)}}</span>` : ''}}</div><pre class="block-pre">${{escapeHtml(text || '')}}</pre>`;
  return wrap;
}}

function obsLooksError(text) {{
  const head = String(text || '').slice(0, 800);
  const m = head.match(/exit code[:\\s]+(-?\\d+)/i);
  if (m) return m[1] !== '0';
  return /<tool_use_error>|arguments provided to the tool are invalid|traceback \\(most recent call last\\)|command not found|permission denied|no such file or directory|non-zero exit status|error:/i.test(head);
}}

function emptySmall(text) {{
  const node = document.createElement('div');
  node.className = 'muted';
  node.textContent = text;
  return node;
}}

function metricValue(row, metric) {{
  const value = row?.[metric];
  if (value === null || value === undefined || value === '') return -Infinity;
  return Number(value);
}}

function dimLabel(dim) {{
  return (analysisData.dim_labels || {{}})[dim] || dim;
}}

const segmentSorts = {{}};

  function segmentSortValue(row, field) {{
    if (field === 'value') return String(row.value || '').toLowerCase();
    const value = row?.[field];
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
  return Number.isFinite(n) ? n : null;
}}

function defaultSegmentSort(dim) {{
  return {{field: dim === 'job' ? 'job_finished_ts' : 'quality_records', dir: 'desc'}};
}}

function currentSegmentSort(dim) {{
  if (!segmentSorts[dim]) segmentSorts[dim] = defaultSegmentSort(dim);
  return segmentSorts[dim];
}}

function compareSegmentRows(a, b, sort) {{
  const av = segmentSortValue(a, sort.field);
  const bv = segmentSortValue(b, sort.field);
  if (typeof av === 'string' || typeof bv === 'string') {{
    const result = String(av || '').localeCompare(String(bv || ''));
    return result * (sort.dir === 'asc' ? 1 : -1);
  }}
  const an = av === null ? -Infinity : av;
  const bn = bv === null ? -Infinity : bv;
  if (an === bn) return String(a.value).localeCompare(String(b.value));
  return (an - bn) * (sort.dir === 'asc' ? 1 : -1);
}}

function renderHeat(value, maxValue, formatter = formatMaybe) {{
  const numeric = Number(value);
  const max = Number(maxValue);
  const width = Number.isFinite(numeric) && Number.isFinite(max) && max > 0 ? Math.max(0, Math.min(100, numeric / max * 100)) : 0;
  return `
    <div class="heat">
      <div>${{formatter(value)}}</div>
      <div class="heat-track"><div class="heat-fill" style="width:${{width.toFixed(1)}}%"></div></div>
    </div>
  `;
}}

function renderSegmentMetric(row, sort, field, value, formatter = formatMaybe, maxValue = 0) {{
  if (sort.field === field && field !== 'value') {{
    return renderHeat(value, maxValue, formatter);
  }}
  return formatter(value);
}}

function renderTaskDifficulty(row) {{
  const avg = Number(row.task_difficulty_avg);
  const valid = Number(row.task_difficulty_count) || 0;
  const unknown = Number(row.task_difficulty_unknown) || 0;
  const total = Number(row.task_difficulty_total) || 0;
  let value = '-';
  let note = '';
  if (Number.isFinite(avg)) {{
    value = avg.toFixed(2);
    note = `${{valid.toLocaleString()}} scored`;
  }} else if (unknown && total) {{
    note = 'all unknown';
  }}
  if (unknown) {{
    const suffix = `${{unknown.toLocaleString()}} unknown`;
    note = note ? `${{note}} · ${{suffix}}` : suffix;
  }}
  const cls = unknown ? 'difficulty-warning' : 'muted';
  return `${{escapeHtml(value)}}${{note ? `<div class="${{cls}}">${{escapeHtml(note)}}</div>` : ''}}`;
}}

function updateSegmentSortHeaders(table, sort) {{
  $$('th[data-segment-sort]', table).forEach(th => {{
    const active = th.dataset.segmentSort === sort.field;
    th.classList.toggle('active', active);
    th.classList.toggle('asc', active && sort.dir === 'asc');
    th.classList.toggle('desc', active && sort.dir === 'desc');
    th.setAttribute('aria-sort', active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
  }});
}}

function renderSegmentTable(table) {{
  const dim = table.dataset.segmentDim || 'job';
  const showTaskDifficulty = dim !== 'difficulty';
  const sort = currentSegmentSort(dim);
  const rowsEl = $(`[data-segment-rows="${{dim}}"]`);
  if (!rowsEl) return;
  const rows = (analysisData.segments || [])
    .filter(row => row.dim === dim)
    .sort((a, b) => compareSegmentRows(a, b, sort))
    .slice(0, 120);
  const maxSortValue = Math.max(0, ...rows.map(row => {{
    const value = segmentSortValue(row, sort.field);
    return typeof value === 'number' && Number.isFinite(value) ? value : 0;
  }}));
  updateSegmentSortHeaders(table, sort);
  if (!rows.length) {{
    rowsEl.innerHTML = `<tr><td colspan="${{showTaskDifficulty ? 8 : 7}}" class="empty">No segments match the current filters.</td></tr>`;
    return;
  }}
  rowsEl.innerHTML = rows.map((row, idx) => `
    <tr data-dim="${{escapeHtml(row.dim)}}" data-value="${{escapeHtml(row.value)}}">
      <td><strong>${{escapeHtml(row.value)}}</strong><div class="muted">${{escapeHtml(dimLabel(row.dim))}}${{row.dim === 'job' ? ` · finished ${{escapeHtml(formatDateTime(row.job_finished_at))}}` : ''}}</div></td>
      <td class="num">${{renderSegmentMetric(row, sort, 'trials', row.trials, formatMaybe, maxSortValue)}}</td>
      <td class="num">${{renderSegmentMetric(row, sort, 'passed', row.passed, formatMaybe, maxSortValue)}}</td>
      <td class="num">${{renderSegmentMetric(row, sort, 'errors', row.errors, formatMaybe, maxSortValue)}}</td>
      <td class="num">${{renderSegmentMetric(row, sort, 'pass_rate', row.pass_rate, formatPercent, maxSortValue)}}</td>
      ${{showTaskDifficulty ? `<td class="num">${{renderTaskDifficulty(row)}}</td>` : ''}}
      <td class="num">${{renderSegmentMetric(row, sort, 'quality_records', row.quality_records, formatMaybe, maxSortValue)}}</td>
      <td class="num">valid ${{renderSegmentMetric(row, sort, 'avg_quality_tokens', row.avg_quality_tokens, formatTokenUnits, maxSortValue)}}<div class="muted">total ${{formatTokenUnits(row.avg_trial_tokens)}}</div></td>
    </tr>
  `).join('');
}}

function renderSegments() {{
  $$('table[data-segment-dim]').forEach(renderSegmentTable);
}}

function escapeHtml(text) {{
  return String(text ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
}}

function selectSample(sample, button) {{
  currentSample = sample;
  $$('.sample-item').forEach(item => item.classList.remove('active'));
  if (button) button.classList.add('active');
  const detail = $('#sampleDetail');
  const messages = (sample.messages || []).map(msg => `
    <div class="message">
      <div class="message-role">${{escapeHtml(msg.role)}}</div>
      <div class="message-content">${{escapeHtml(msg.content || '')}}</div>
    </div>
  `).join('');
  detail.classList.remove('empty');
  detail.innerHTML = `
    <dl class="kv">
      <dt>Instance</dt><dd>${{escapeHtml(sample.instance_id)}}</dd>
      <dt>Dataset</dt><dd>${{escapeHtml(sample.dataset)}}</dd>
      <dt>Source</dt><dd>${{escapeHtml(sample.source)}}</dd>
      <dt>Score</dt><dd>${{formatMaybe(sample.score)}}</dd>
      <dt>Turns</dt><dd>${{formatMaybe(sample.turns)}}</dd>
      <dt>Tokens</dt><dd>${{formatMaybe(sample.tokens)}}</dd>
      <dt>Tool calls</dt><dd>${{formatMaybe(sample.tool_calls)}} · success ${{formatMaybe(sample.tool_success_rate)}}</dd>
    </dl>
    ${{messages || '<div class="empty">No messages in this preview.</div>'}}
  `;
}}

document.addEventListener('click', event => {{
  const nav = event.target.closest('.nav-item');
  if (nav) setPage(nav.dataset.page);
  const copy = event.target.closest('[data-copy]');
  if (copy) copyText(copy.dataset.copy);
  const sourceButton = event.target.closest('[data-source-filter]');
  if (sourceButton) {{
    const sourceSelect = $('#trajSource');
    if (sourceSelect) {{
      sourceSelect.value = sourceButton.dataset.sourceFilter || '';
      renderTrajectoryList();
    }}
  }}
}});
['sftSearch','sftScaffold'].forEach(id => {{
  const el = $('#' + id);
  if (el) el.addEventListener('input', applyFilters);
  if (el) el.addEventListener('change', applyFilters);
}});
['sampleSearch','sampleDataset'].forEach(id => {{
  const el = $('#' + id);
  if (el) el.addEventListener('input', renderSampleList);
  if (el) el.addEventListener('change', renderSampleList);
}});
$$('th[data-segment-sort]').forEach(th => th.addEventListener('click', () => {{
  const table = th.closest('table[data-segment-dim]');
  if (!table) return;
  const dim = table.dataset.segmentDim || 'job';
  const field = th.dataset.segmentSort || 'quality_records';
  const sort = currentSegmentSort(dim);
  segmentSorts[dim] = {{
    field,
    dir: sort.field === field && sort.dir === 'desc' ? 'asc' : 'desc',
  }};
  renderSegmentTable(table);
}}));
$$('th[data-source-sort]').forEach(th => th.addEventListener('click', () => {{
  const field = th.dataset.sourceSort || 'comp';
  trajSourceSort = {{
    field,
    dir: trajSourceSort.field === field && trajSourceSort.dir === 'desc' ? 'asc' : 'desc',
  }};
  renderTrajectorySourceTable();
}}));
['trajSearch','trajMode','trajLanguage','trajSource','trajSampleSize'].forEach(id => {{
  const el = $('#' + id);
  if (el) el.addEventListener('input', renderTrajectoryList);
  if (el) el.addEventListener('change', renderTrajectoryList);
}});
$('#trajResample')?.addEventListener('click', () => {{
  trajSampleSeed += 1;
  renderTrajectoryList();
}});
$$('th.sortable').forEach(th => th.addEventListener('click', () => sortTable(th)));
$('#copySampleId')?.addEventListener('click', () => copyText(currentSample?.instance_id || ''));
$('#themeToggle')?.addEventListener('click', () => setTheme(currentTheme() === 'dark' ? 'light' : 'dark'));
$('#refreshNow')?.addEventListener('click', event => {{
  const btn = event.currentTarget;
  btn.classList.add('refreshing');
  btn.setAttribute('aria-busy', 'true');
  window.location.reload();
}});
populateSelect('trajLanguage', new Set(trajCardData.map(row => row.language || 'unknown')));
populateSelect('trajSource', new Set([
  ...trajCardData.map(row => trajSourceName(row)),
  ...trajSourceSummaryData.map(row => row.source || 'unknown'),
]));
renderThemeToggle();
renderOverviewCharts();
renderTrajectorySourceTable();
renderSubscoreMatrix();
renderTrajectoryList();
renderSampleList();
renderSegments();
</script>
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
    p.add_argument("--index-file", type=Path, default=DEFAULT_INDEX,
                   help="Archived-run index used by the dashboard status panel.")
    p.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOBS)
    p.add_argument("--sft-dir", type=Path, default=DEFAULT_SFT)
    p.add_argument("--tasks-dir", type=Path, default=DEFAULT_TASKS)
    p.add_argument("--harbor-jobs-dir", type=Path, default=DEFAULT_HARBOR_JOBS)
    p.add_argument("--index-job", action="append", default=[],
                   help="Additional Harbor job name to index for trial-level analysis. Repeatable.")
    p.add_argument("--max-trials-per-job", type=int, default=0,
                   help="Limit trial result.json records read per Harbor job. 0 means no limit.")
    p.add_argument("--max-quality-records-per-dataset", type=int, default=0,
                   help="Limit im.jsonl rows read per SFT dataset for quality facts. 0 means no limit.")
    p.add_argument("--local-mode", choices=["full", "public"], default="full",
                   help="full keeps bounded text previews in analysis drilldown; public keeps metrics only.")
    p.add_argument("--public-no-samples", action="store_true",
                   help="Disable embedded sample previews when publishing a public dashboard.")
    p.add_argument("--refresh", type=int, default=60, help="HTML auto-refresh interval (seconds).")
    p.add_argument("--loop", nargs="?", const=60, type=int, default=None,
                   help="Regenerate on a loop; defaults to 60s when --loop is given without value.")
    p.add_argument("--serve", action="store_true", help="Start a local HTTP server on --host:--port.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="Open the page in a browser after the first write.")
    p.add_argument("--force-full-scan", action="store_true", help="Ignore cache and re-parse every file.")
    p.add_argument("--include-samples", action=argparse.BooleanOptionalAction, default=True,
                   help="Embed bounded SFT sample previews in the static HTML.")
    p.add_argument("--sample-limit", type=int, default=200,
                   help="Maximum sample previews to embed per dataset.")
    p.add_argument("--sample-preview-chars", type=int, default=1200,
                   help="Maximum characters to keep per message preview.")
    p.add_argument("--max-preview-chars", dest="sample_preview_chars", type=int, default=argparse.SUPPRESS,
                   help="Alias for --sample-preview-chars.")
    p.add_argument("--sample-message-limit", type=int, default=12,
                   help="Maximum messages to keep per sample preview.")
    p.add_argument("--embedded-traj-limit", type=int, default=DEFAULT_EMBEDDED_TRAJ_LIMIT,
                   help="Maximum full trajectory JSON records to embed into data/traj_embedded shards. 0 disables embedding.")
    p.add_argument("--embedded-traj-max-bytes", type=int, default=DEFAULT_EMBEDDED_TRAJ_MAX_BYTES,
                   help="Maximum total bytes for embedded full trajectory JSON shards. 0 disables embedding.")
    return p.parse_args(argv)


def effective_embedded_trajectory_limits(args: argparse.Namespace, *, include_samples: bool) -> tuple[int, int]:
    """Disable full trajectory exports whenever previews are disabled or public."""
    if args.local_mode == "public" or args.public_no_samples or not include_samples:
        return 0, 0
    return (
        max(0, int(args.embedded_traj_limit or 0)),
        max(0, int(args.embedded_traj_max_bytes or 0)),
    )


def run_once(args: argparse.Namespace, refresh_seconds: int) -> dict[str, Any]:
    cache = {"version": CACHE_VERSION, "jobs": {}, "sft": {}} if args.force_full_scan else load_cache(args.cache_file)
    include_samples = bool(args.include_samples)
    if args.local_mode == "public" or args.public_no_samples:
        include_samples = False
    jobs = collect_jobs(args.jobs_dir.resolve(), cache)
    sft = collect_sft(args.sft_dir.resolve(), cache)
    status = read_status(args.index_file.resolve())
    samples = collect_samples(
        args.sft_dir.resolve(),
        include_samples=include_samples,
        sample_limit=args.sample_limit,
        preview_chars=args.sample_preview_chars,
        message_limit=args.sample_message_limit,
    )
    task_dim = collect_task_dim(args.tasks_dir.resolve())
    index_jobs = [str(j.get("job") or "") for j in jobs] + [str(s.get("job") or "") for s in sft] + list(args.index_job or [])
    trial_facts = collect_trial_facts(
        args.harbor_jobs_dir.resolve(),
        index_jobs,
        task_dim,
        max_trials_per_job=max(0, int(args.max_trials_per_job or 0)),
    )
    quality_facts = collect_quality_facts(
        args.sft_dir.resolve(),
        task_dim,
        max_records_per_dataset=max(0, int(args.max_quality_records_per_dataset or 0)),
        preview_chars=args.sample_preview_chars,
        include_previews=args.local_mode == "full",
    )
    analysis = build_analysis(task_dim, trial_facts, quality_facts, jobs)
    instances = build_instance_index(task_dim, trial_facts, quality_facts)
    all_traj_cards, embedded_traj_cards = build_traj_cards(trial_facts, quality_facts)
    embedded_traj_limit, embedded_traj_max_bytes = effective_embedded_trajectory_limits(
        args,
        include_samples=include_samples,
    )
    error_summary = build_error_summary(trial_facts, quality_facts)
    totals = compute_totals(jobs, sft)
    totals["coverage"] = build_coverage_summary(sft, trial_facts, quality_facts, instances)
    totals["samples"] = len(samples)
    totals["task_dim"] = len(task_dim)
    totals["trial_facts"] = len(trial_facts)
    totals["quality_facts"] = len(quality_facts)
    totals["instances"] = len(instances)
    totals["traj_cards"] = len(all_traj_cards)
    save_cache(args.cache_file, cache)
    write_data_exports(
        args.output_html,
        task_dim=task_dim,
        trial_facts=trial_facts,
        quality_facts=quality_facts,
        analysis=analysis,
        instances=instances,
        traj_cards=embedded_traj_cards,
        error_summary=error_summary,
        totals=totals,
        harbor_jobs_dir=args.harbor_jobs_dir.resolve(),
        embedded_traj_limit=embedded_traj_limit,
        embedded_traj_max_bytes=embedded_traj_max_bytes,
    )
    write_worker_script(args.output_html)
    html_doc = render_html(
        jobs,
        sft,
        status,
        samples,
        totals,
        analysis,
        instances,
        embedded_traj_cards,
        build_traj_source_summary(quality_facts, sft, jobs),
        error_summary,
        refresh_seconds,
        args.jobs_dir.resolve(),
        args.sft_dir.resolve(),
        args.harbor_jobs_dir.resolve(),
        args.index_file.resolve(),
    )
    atomic_write_text(args.output_html, html_doc)
    return totals


def start_http_server(directory: Path, host: str, port: int) -> ThreadingHTTPServer:
    directory.mkdir(parents=True, exist_ok=True)
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    Thread(target=server.serve_forever, name="tracer-dashboard-http", daemon=True).start()
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
                f"sft={totals['sft_count']} samples={totals.get('samples', 0)} "
                f"indexed_trials={totals.get('trial_facts', 0)} quality={totals.get('quality_facts', 0)} "
                f"instances={totals.get('instances', 0)} traj_cards={totals.get('traj_cards', 0)}"
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
