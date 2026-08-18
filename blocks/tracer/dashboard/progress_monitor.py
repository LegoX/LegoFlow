#!/usr/bin/env -S uv run --no-project --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Local HTML dashboard for the tracer block.

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
import base64 as _b64
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
DEFAULT_HARBOR_JOBS = Path(os.environ.get("HARBOR_JOBS_DIR", str(DEFAULT_JOBS)))
DEFAULT_HTML = SCRIPT_DIR / "site" / "index.html"
DEFAULT_CACHE = SCRIPT_DIR / ".cache" / ".progress_monitor_cache.json"
DEFAULT_INDEX = BLOCK_DIR / "artifacts" / "index.yaml"
CACHE_VERSION = 1
DEFAULT_EMBEDDED_TRAJ_LIMIT = 120
DEFAULT_EMBEDDED_TRAJ_MAX_BYTES = 40_000_000
EMBEDDED_TRAJ_SHARD_BYTES = 8_000_000
# One trajectory is one JSONL line and cannot be split, so an outsized trace sets
# its shard's size alone — big enough to break the host's per-file limit, and to
# eat the whole budget. Skip those; they keep their local path and R2 key.
EMBEDDED_TRAJ_MAX_RECORD_BYTES = 4_000_000
TRAJECTORY_ARTIFACT_NAMES = ("litellm-trajectory.jsonl", "trajectory.json")

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
# Acronym spelled out, then what it measures. Wording follows
# swe_data_process/rule_score.py, which computes these. Zero-weight entries are
# diagnostics: shown, but they do not move composite_score.
SUBSCORE_LABELS = {
    "composite_score": "Trajectory score — weighted sum of the subscores below (Σw = 1.00)",
    "sub_score": "SUB — submission completeness: whether the run delivered a complete patch",
    "stp_score": "STP — step efficiency: reaching the same result in fewer steps scores higher",
    "tvr_score": "TVR — test verification: whether the run wrote and ran tests to check its own fix",
    "fec_score": "FEC — file-edit concentration: edits focused on few files rather than scattered "
                 "(raised to the 5th power before weighting)",
    "dpi_score": "DPI — dirty-pattern penalty: truncation, edits never committed, loops, repeated "
                 "errors (cubed before weighting)",
    "oec_score": "OEC — observation entropy collapse: tool output stops carrying new information "
                 "(diagnostic, weight 0)",
    "iac_score": "IAC — intent/action consistency: actions match the stated intent "
                 "(diagnostic, weight 0)",
    "ped_score": "PED — post-error strategy diversity: the approach varies after an error "
                 "(diagnostic, weight 0)",
    "psn_score": "PSN — progressive scope narrowing: the search narrows toward the fix "
                 "(diagnostic, weight 0)",
    "tte_score": "TTE — tool transition entropy: how varied the tool-to-tool transitions are "
                 "(diagnostic, weight 0)",
    "scp_score": "SCP — time to first effective edit: how long before the first edit that sticks "
                 "(diagnostic, weight 0)",
}
def render_rubric_html() -> str:
    """The trajectory-quality rubric, rendered from the same weights the score
    itself uses so the card can never drift from the arithmetic."""
    rows = []
    for key in WEIGHTED_SUBSCORE_KEYS:
        weight = TQS_WEIGHTS[key]
        label = SUBSCORE_LABELS.get(key, key)
        # labels are stored as "<code> <name>"; split so each lands in its own column
        code, _, name = label.partition(" ")
        rows.append(
            '<div class="rubric-row">'
            f'<span class="rk">{html.escape(code)}</span>'
            f'<span class="rn">{html.escape(name or label)}</span>'
            f'<span class="rw">{weight:.2f}</span>'
            f'<span class="rb"><i style="width:{weight / max(TQS_WEIGHTS.values()) * 100:.1f}%"></i></span>'
            "</div>"
        )
    diagnostic = [k for k in SUBSCORE_KEYS if TQS_WEIGHTS.get(k, 0) == 0]
    return (
        '<section class="method-card"><h3>Trajectory Quality Score '
        "<span>rule-based, no LLM judge</span></h3>"
        '<div class="method-body">'
        '<span class="mh">Composite</span>'
        "<code>TQS = &Sigma; w&middot;s</code> over the weighted dimensions below "
        f"(&Sigma;w = {sum(TQS_WEIGHTS.values()):.2f}); each <code>s</code> is a rule score in "
        "<code>[0, 1]</code>."
        '<span class="mh">Weighted dimensions</span>'
        f'<div class="rubric">{"".join(rows)}</div>'
        '<span class="mh">Diagnostic dimensions</span>'
        f'<div class="method-note">Carried for inspection at weight 0, so they never move the score: '
        f'<code>{html.escape(", ".join(k.removesuffix("_score") for k in diagnostic))}</code>.</div>'
        '<div class="method-note">Scores come from the exported SFT quality facts '
        "(<code>swe_data_process</code> rule_score); a trajectory with no facts is counted as "
        "unscored, never as zero.</div>"
        "</div></section>"
    )


IDENTITY_COLORS = [
    "#2563eb",
    "#3f8f2f",
    "#d03b3b",
    "#4a3aa7",
    "#199e70",
    "#c8860d",
    "#be185d",
    "#b3431f",
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
        return {"_error": f"{display_path(index_path)} not found"}
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"_error": f"failed to read {display_path(index_path)}: {exc}"}

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


def find_trial_trajectory(trial_dir: Path) -> Path | None:
    agent_dir = trial_dir / "agent"
    for name in TRAJECTORY_ARTIFACT_NAMES:
        path = agent_dir / name
        if path.is_file():
            return path
    return None


def trial_model_name(model_info: dict[str, Any], config_agent: dict[str, Any]) -> str:
    """The model a trial ran against — the teacher, on a distillation run.

    What the agent reported, then what it was configured with, then the agent
    environment for scaffolds that record it only there.
    """
    reported = str(model_info.get("name") or "").strip()
    if reported:
        return reported
    configured = str(config_agent.get("model_name") or "").strip()
    if configured:
        return configured
    env = config_agent.get("env") if isinstance(config_agent.get("env"), dict) else {}
    for key in ("ANTHROPIC_MODEL", "OPENAI_MODEL", "MODEL"):
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return "unknown"


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
            trial_config = data.get("config") if isinstance(data.get("config"), dict) else {}
            config_agent = trial_config.get("agent") if isinstance(trial_config.get("agent"), dict) else {}
            agent_result = data.get("agent_result") if isinstance(data.get("agent_result"), dict) else {}
            exception_info = data.get("exception_info")
            reward = extract_reward(data)
            trajectory_path = find_trial_trajectory(trial_dir)
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
                # Harbor fills model_info only for agents that report it. When it
                # does not, the model is still in this result.json's launch config.
                "model": trial_model_name(model_info, config_agent),
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
                "trajectory_path": str(trajectory_path) if trajectory_path else "",
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
                    # An SFT record has no trajectory file — it *is* the
                    # trajectory, at this line. Remember where, so it can be embedded.
                    "im_path": str(im_file),
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
            "im_path": fact.get("im_path"),
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
    def has_source(card: dict[str, Any]) -> bool:
        path = str(card.get("trajectory_path") or "")
        if path and Path(path).is_file():
            return True
        # A converted SFT record is its own trace: one line of an im.jsonl.
        return bool(card.get("im_path")) and card.get("index") is not None
    candidates = [card for card in cards if has_source(card)]
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


RECORD_PATH_KEYS = ("trajectory_output_path",)


def strip_record_paths(node: Any, block_dir: Path = BLOCK_DIR) -> Any:
    """Rewrite host paths Harbor recorded inside a trajectory payload.

    Named metadata keys only. Message content stays as it was — rewriting it
    would make the published trace disagree with the one on disk.
    """
    if isinstance(node, dict):
        return {
            k: (display_path(v, block_dir) if k in RECORD_PATH_KEYS and isinstance(v, str)
                else strip_record_paths(v, block_dir))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [strip_record_paths(v, block_dir) for v in node]
    return node


def read_im_records(cards: list[dict[str, Any]]) -> dict[tuple[str, int], Any]:
    """Fetch the specific im.jsonl lines a set of cards points at.

    One pass per file, stopping at the last line wanted — these run to hundreds
    of megabytes, and per-record seeking would re-read the file each time.
    """
    wanted: dict[str, set[int]] = {}
    for card in cards:
        path, index = str(card.get("im_path") or ""), card.get("index")
        if path and index is not None:
            wanted.setdefault(path, set()).add(int(index))

    out: dict[tuple[str, int], Any] = {}
    for path, indices in wanted.items():
        last = max(indices)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh):
                    if i in indices:
                        try:
                            out[(path, i)] = json.loads(line)
                        except json.JSONDecodeError:
                            print(f"WARN: invalid JSON at {path}:{i + 1}", file=sys.stderr)
                    if i >= last:
                        break
        except OSError as exc:
            print(f"WARN: failed to read {path}: {exc}", file=sys.stderr)
    return out


def read_trajectory_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        return json.loads(text)
    except json.JSONDecodeError as document_error:
        records: list[Any] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as line_error:
                raise ValueError(f"invalid trajectory JSONL at line {line_number}") from line_error
        if records:
            return records
        raise document_error


def write_embedded_trajectory_shards(
    data_dir: Path,
    cards: list[dict[str, Any]],
    *,
    limit: int,
    max_total_bytes: int,
    shard_max_bytes: int = EMBEDDED_TRAJ_SHARD_BYTES,
    max_record_bytes: int = EMBEDDED_TRAJ_MAX_RECORD_BYTES,
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
    oversized = 0

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

    selected = select_embedded_traj_cards(cards, limit=limit)
    im_records = read_im_records(selected)
    for card in selected:
        trajectory_path = Path(str(card.get("trajectory_path") or ""))
        if str(card.get("trajectory_path") or ""):
            try:
                record = read_trajectory_payload(trajectory_path)
            except (OSError, ValueError) as exc:
                print(f"WARN: failed to embed trajectory {trajectory_path}: {exc}", file=sys.stderr)
                continue
        else:
            record = im_records.get((str(card.get("im_path")), int(card.get("index"))))
            if record is None:
                continue
        record = strip_record_paths(record)
        row = {"id": card.get("id"), "record": record}
        line = json.dumps(row, ensure_ascii=False, default=json_default) + "\n"
        line_bytes = len(line.encode("utf-8"))
        if max_record_bytes and line_bytes > max_record_bytes:
            oversized += 1
            continue
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
    if oversized:
        print(f"NOTE: {oversized} trajectory record(s) exceeded "
              f"{max_record_bytes / 1_000_000:.0f} MB and were not embedded; "
              "they remain reachable by local path / R2 key", file=sys.stderr)
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
        return json({ r2_key: r2Key, record: parseTrajectory(text) }, 200);
      } catch (err) {
        return json({ r2_key: r2Key, text }, 200);
      }
    }
    return env.ASSETS.fetch(request);
  },
};

function parseTrajectory(text) {
  try {
    return JSON.parse(text);
  } catch (documentError) {
    const lines = text.split(/\\r?\\n/).filter(line => line.trim());
    if (!lines.length) throw documentError;
    return lines.map(line => JSON.parse(line));
  }
}

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


# Path fields that reach the published page, in cards, task rows and the summary.
PUBLISHED_PATH_KEYS = ("path", "trajectory_path", "im_path", "lf_path")
# Same treatment, but the value is a list of paths.
PUBLISHED_PATH_LIST_KEYS = ("trajectory_paths",)


def display_path(value: Any, block_dir: Path = BLOCK_DIR) -> str:
    """A path fit to publish: block-relative, and never naming the host.

    Absolute paths are shipped inside the page and its data files, where they
    disclose the operator's home directory and layout while telling a reader
    nothing actionable. A pool linked in from elsewhere has no useful relative
    form, so keep the tail from `artifacts/` and drop what is above it.
    """
    text = str(value or "")
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        return text
    try:
        return str(path.relative_to(block_dir))
    except ValueError:
        pass
    parts = path.parts
    if "artifacts" in parts:
        return "external:" + str(Path(*parts[parts.index("artifacts"):]))
    return "external:" + "/".join(parts[-3:])


def strip_published_paths(
    *row_lists: list[dict[str, Any]],
    block_dir: Path = BLOCK_DIR,
) -> None:
    """Rewrite path fields in place, once embedding has read what it needed.

    The same list objects reach both the exports and the HTML, so one pass here
    covers every published copy.
    """
    for rows in row_lists:
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in PUBLISHED_PATH_KEYS:
                if isinstance(row.get(key), str) and row[key]:
                    row[key] = display_path(row[key], block_dir)
            for key in PUBLISHED_PATH_LIST_KEYS:
                if isinstance(row.get(key), list):
                    row[key] = [display_path(v, block_dir) if isinstance(v, str) else v
                               for v in row[key]]


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
    embedded_traj_max_record_bytes: int = EMBEDDED_TRAJ_MAX_RECORD_BYTES,
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
    # Facts and instances are written first, so they are sanitised first. Cards
    # wait until embedding has read the real locations, further down.
    strip_published_paths(trial_facts, quality_facts, instances)
    trial_fact_exports = write_jsonl_shards(data_dir / "trial_fact.jsonl", trial_facts)
    instance_exports = write_jsonl_shards(data_dir / "instances.jsonl", instances)
    embedded_traj_exports = write_embedded_trajectory_shards(
        data_dir,
        traj_cards,
        limit=embedded_traj_limit,
        max_total_bytes=embedded_traj_max_bytes,
        max_record_bytes=embedded_traj_max_record_bytes,
    )
    # Everything above needed real paths; nothing below may publish them.
    strip_published_paths(traj_cards, task_rows)
    summary = {
        "generated_at": now_bjt().isoformat(),
        "harbor_jobs_dir": display_path(harbor_jobs_dir),
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
        composite_v4 = score.get("composite_score_v4")
        composite = composite_v4 if composite_v4 is not None else score.get("composite_score_v3")
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
    # Language, model and scaffold belong to the rollout, not to the conversion —
    # every trial carries them. Prefer the converted set when there is one, since
    # the other SFT surfaces describe it; fall back to the trials.
    dimension_facts = quality_facts or trial_facts
    languages, language_counts = ranked_values(dimension_facts, "language")
    dataset_jobs = meaningful_values(sft, "job")
    models = meaningful_values(dimension_facts, "model")
    scaffolds = meaningful_values(dimension_facts, "scaffold")
    processed_instances = {fact_instance_key(fact) for fact in trial_facts}
    return {
        "dimensions_from": "sft" if quality_facts else "trials",
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

FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="14" fill="#b3431f"/>'
    '<text x="32" y="42" text-anchor="middle" font-family="Georgia,serif" '
    'font-weight="700" font-size="30" fill="#fafaf7">LF</text></svg>'
)
FAVICON_DATA_URI = "data:image/svg+xml;base64," + _b64.b64encode(
    FAVICON_SVG.encode("utf-8")
).decode("ascii")

ICON_INFO = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
             '<circle cx="12" cy="12" r="9"></circle><path d="M12 16v-5"></path>'
             '<path d="M12 8h.01"></path></svg>')

CARET_SVG = ('<svg class="caret icon" viewBox="0 0 24 24" aria-hidden="true" '
             'style="width:14px;height:14px"><path d="M9 6l6 6-6 6"></path></svg>')

ICON_METRICS = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
                '<path d="M4 19h16"></path><path d="M7 19v-7"></path>'
                '<path d="M12 19V5"></path><path d="M17 19v-10"></path></svg>')

CSS = """
:root {
  color-scheme: light;
  --bg: #fafaf7;
  --panel: #fff;
  --panel-soft: #fafaf7;
  --panel-softer: #f1efe9;
  --text: #111111;
  --fg-dim: #4a453e;
  --muted: #6b6b66;
  --fg-faint: #8c8c85;
  --method-bg: #b3431f0a;
  --method-line: #b3431f33;
  --method-head: #b3431f;
  --line: #e6e3da;
  --soft: #f1efe9;
  --ink: #b3431f;
  --ink-contrast: #fff;
  --accent-soft: #b3431f1f;
  --accent-border: #b3431f80;
  --active-text: #b3431f;
  --button-bg: #fff;
  --button-hover: #f1efe9;
  --bar-bg: #e6e3da;
  /* bar fills repeat dozens of times per page; a softened tint of the accent
     keeps the page calm without changing the brand colour */
  --accent-fill: #c96a45;
  --matrix-heat-rgb: 179, 67, 31;
  --bar-text: #111111;
  --active-row: #f1efe9;
  --warn-bg: #fab21922;
  --warn-line: #fab21955;
  --warn-text: #8a5a10;
  --blue: #b3431f;
  --green: #3f8f2f;
  --amber: #c8860d;
  --red: #d03b3b;
  --purple: #4a3aa7;
  --cyan: #199e70;
  --badge-running-bg: #fab21922;
  --badge-running-text: #8a5a10;
  --badge-done-bg: #4a944022;
  --badge-done-text: #3f8f2f;
  --badge-missing-bg: #d03b3b22;
  --badge-missing-text: #d03b3b;
  --badge-scaffold-bg: #efa07c22;
  --badge-scaffold-text: #b3431f;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #14110e;
  --panel: #1a1714;
  --panel-soft: #1a1714;
  --panel-softer: #211d19;
  --text: #f0ede7;
  --fg-dim: #c9c2b6;
  --muted: #a9a297;
  --fg-faint: #8a847a;
  --method-bg: #efa07c12;
  --method-line: #efa07c33;
  --method-head: #f5c9b4;
  --line: #302a2499;
  --soft: #1a1714;
  --ink: #efa07c;
  --ink-contrast: #fff;
  --accent-soft: #efa07c33;
  --accent-border: #efa07c80;
  --active-text: #f5c9b4;
  --button-bg: #1a171480;
  --button-hover: #302a2440;
  --bar-bg: #302a2466;
  --accent-fill: #c9805f;
  --matrix-heat-rgb: 239, 160, 124;
  --bar-text: #f0ede7;
  --active-row: #302a2440;
  --warn-bg: #fab21922;
  --warn-line: #fab21955;
  --warn-text: #fab219;
  --blue: #efa07c;
  --green: #4a9440;
  --amber: #fab219;
  --red: #d03b3b;
  --purple: #9085e9;
  --cyan: #199e70;
  --badge-running-bg: #fab21922;
  --badge-running-text: #fab219;
  --badge-done-bg: #4a944022;
  --badge-done-text: #4a9440;
  --badge-missing-bg: #d03b3b22;
  --badge-missing-text: #d03b3b;
  --badge-scaffold-bg: #efa07c33;
  --badge-scaffold-text: #f5c9b4;
}
* { box-sizing: border-box; }
/* Shell metrics (type scale, sidebar width, topbar and content padding) are
   shared with every other block's board — change them there too. */
:root { font-size: 15px; }
body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
       background: var(--bg); color: var(--text); font-size: 15px; line-height: 1.45;
       -webkit-font-smoothing: antialiased; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-thumb { background: var(--fg-faint); border-radius: 3px; }
::-webkit-scrollbar-track { background: transparent; }
header { padding: 24px 32px 18px; background: var(--panel); border-bottom: 1px solid var(--line); }
.header-top { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
header h1 { margin: 0 0 6px; font-size: 26px; font-weight: 720; letter-spacing: 0; }
header p { margin: 4px 0; color: var(--muted); }
header code, code { background: var(--soft); padding: 2px 6px; border-radius: 5px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
main { padding: 22px 32px 48px; max-width: 1760px; margin: 0 auto; }
.app-shell { min-height: 100vh; display: grid; grid-template-columns: 240px minmax(0, 1fr); }
.sidebar { position: sticky; top: 0; height: 100vh; padding: 0; border-right: 1px solid var(--line); background: linear-gradient(180deg, #12100d 0%, var(--bg) 100%); overflow-y: auto; }
:root:not([data-theme="dark"]) .sidebar { background: var(--panel); }
.brand { padding: 14px 16px; border-bottom: 1px solid var(--line); }
.brand-lockup { display: flex; align-items: center; gap: 10px; min-width: 0; }
.brand-mark { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 28px; flex: 0 0 32px; border-radius: 7px; background: var(--ink); color: var(--ink-contrast); font-size: 13px; font-weight: 800; letter-spacing: -0.02em; }
.brand-title { min-width: 0; }
.brand h1 { margin: 0; font-size: 15px; font-weight: 650; line-height: 1.25; letter-spacing: 0; }
.brand p { margin: 4px 0 0; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.side-nav { display: grid; gap: 2px; padding: 8px; }
.nav-item { width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; border: 1px solid transparent; background: transparent; color: var(--fg-dim); border-radius: 8px; font-size: 14px; font-weight: 600; padding: 8px 10px; }
.nav-item:hover { background: var(--button-hover); color: var(--text); border-color: transparent; }
.nav-item.active { background: var(--accent-soft); color: var(--active-text); border-color: var(--accent-border); }
.nav-icon { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; flex: 0 0 20px; }
.nav-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.main-shell { min-width: 0; }
.topbar { display: flex; justify-content: space-between; gap: 16px; align-items: center; min-height: 48px; padding: 10px 24px; border-bottom: 1px solid var(--line); background: var(--bg); }
.topbar h2 { margin: 0; font-size: 17px; font-weight: 650; }
.topbar p { margin: 2px 0 0; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.topbar-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.update-status { color: var(--muted); font-size: 11px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.content { padding: 18px 24px 32px; }
button, input, select { font: inherit; }
button { border: 1px solid var(--line); background: var(--button-bg); color: var(--text); border-radius: 6px; padding: 7px 10px; cursor: pointer; }
button:hover { border-color: var(--accent-border); background: var(--button-hover); color: var(--text); }
.icon { width: 18px; height: 18px; display: block; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
/* Modal — same dialog spec on every block's board: centred over a dimmed page,
   generous gutters, one quiet header rule. */
.modal { position: fixed; inset: 0; z-index: 200; display: flex; align-items: center;
  justify-content: center; padding: 32px 24px; background: rgba(17, 17, 17, .32); }
:root[data-theme="dark"] .modal { background: rgba(0, 0, 0, .5); }
.modal[hidden] { display: none; }
.modal-box { width: min(920px, 94vw); max-height: min(80vh, 780px); display: flex;
  flex-direction: column; background: var(--panel); border: 1px solid var(--line);
  border-radius: 16px; box-shadow: 0 24px 64px rgba(0, 0, 0, .18); overflow: hidden; text-align: left; }
:root[data-theme="dark"] .modal-box { box-shadow: 0 24px 64px rgba(0, 0, 0, .6); }
.modal-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px;
  padding: 22px 28px 18px; border-bottom: 1px solid var(--line); }
.modal-head h3 { margin: 0; font-size: 17px; font-weight: 650; letter-spacing: -.01em;
  line-height: 1.3; }
.modal-head .sub { color: var(--muted); font-size: 12.5px; margin-top: 5px; line-height: 1.5; }
.modal-close { background: transparent; border: 1px solid transparent; color: var(--muted);
  border-radius: 9px; width: 30px; height: 30px; cursor: pointer; font-size: 19px;
  line-height: 1; flex: 0 0 30px; }
.modal-close:hover { color: var(--text); border-color: var(--line); background: var(--button-bg); }
.modal-body { padding: 22px 28px 26px; overflow: auto; }
.modal-body table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.modal-body td { padding: 5px 7px; border-bottom: 1px solid var(--line); text-align: left; }
.modal-body td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px; word-break: break-all; }
.modal-body .sub { color: var(--muted); font-size: 11.5px; margin: 14px 0 5px;
  text-transform: uppercase; letter-spacing: .05em; font-weight: 700; }
.modal-body .sub:first-child { margin-top: 0; }
/* Scoring rubric card — mirrors curator's methodology card. Every dimension is
   one unwrapped row: a fixed code column, an elastic name, then the weight and
   its bar, so the weights line up as a column you can read down. */
.method-card { background: var(--method-bg); border: 1px solid var(--method-line);
  border-radius: 10px; padding: 14px 18px; }
.method-card h3 { font-size: 15px; margin: 0 0 4px; font-weight: 700; color: var(--method-head); }
.method-card h3 span { color: var(--ink); font-weight: 500; font-size: 13px; margin-left: 6px; }
.method-body { font-size: 13px; line-height: 1.7; color: var(--text); }
.method-body .mh { display: block; color: var(--method-head); font-weight: 800;
  font-size: 13.5px; margin: 10px 0 4px; }
.method-body .mh:first-child { margin-top: 0; }
.method-body code { color: var(--ink); background: var(--accent-soft); padding: 1px 5px;
  border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.method-note { color: var(--muted); font-size: 12px; line-height: 1.6; margin-top: 8px; }
.rubric { display: grid; gap: 4px; margin-top: 2px; }
.rubric-row { display: grid; grid-template-columns: 52px minmax(0, 1fr) 52px 132px;
  gap: 12px; align-items: center; white-space: nowrap; font-variant-numeric: tabular-nums; }
.rubric-row .rk { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
  font-weight: 700; color: var(--ink); }
.rubric-row .rn { overflow: hidden; text-overflow: ellipsis; }
.rubric-row .rw { text-align: right; font-weight: 700; }
.rubric-row .rb { height: 8px; border-radius: 999px; background: var(--bar-bg); overflow: hidden; }
.rubric-row .rb i { display: block; height: 100%; background: var(--accent-fill); border-radius: inherit; }
@media (max-width: 700px) {
  .rubric-row { grid-template-columns: 52px minmax(0, 1fr) 52px; }
  .rubric-row .rb { display: none; }
}
/* Shared chrome with the other blocks' boards: 36px square, 8px radius, muted
   ink that firms up on hover. Keep in step with curator/trainer/evaluator. */
.icon-btn { width: 36px; height: 36px; padding: 0; display: inline-flex; align-items: center;
  justify-content: center; flex: 0 0 36px; background: var(--button-bg);
  border: 1px solid var(--line); color: var(--fg-dim); border-radius: 8px; cursor: pointer; }
.icon-btn:hover { color: var(--text); border-color: var(--accent-border); }
.topbar-actions .copy-btn { height: 36px; padding: 0 12px; border-radius: 8px; font-size: 12px; }
.theme-toggle { min-width: 0; white-space: nowrap; }
.theme-toggle .theme-sun { display: none; }
.theme-toggle .theme-moon { display: block; }
:root[data-theme="dark"] .theme-toggle .theme-sun { display: block; }
:root[data-theme="dark"] .theme-toggle .theme-moon { display: none; }
.refresh-btn.refreshing .icon { animation: spin .75s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.grid { display: grid; gap: 12px; }
.kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0 0 12px; }
.card, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
/* Stat tiles sit at the same weight as every other board's: the number leads,
   the label and caption stay quiet, and nothing is padded out to a fixed height. */
.card { padding: 12px 14px; }
.card .label { color: var(--muted); font-size: 12px; line-height: 1.35; text-transform: uppercase; letter-spacing: .04em; }
.card .value { font-size: 22px; font-weight: 700; margin-top: 4px; line-height: 1.15; font-variant-numeric: tabular-nums; }
.card .sub { color: var(--muted); margin-top: 3px; font-size: 11px; line-height: 1.35; }
.panel { padding: 18px; margin-top: 18px; overflow: hidden; }
.panel-head { display: flex; align-items: start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.panel h2 { margin: 0; font-size: 18px; }
.panel .hint { color: var(--muted); margin: 4px 0 0; font-size: 12px; }
.tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 18px; }
.tab { font-weight: 650; }
.tab.active { background: var(--ink); color: var(--ink-contrast); border-color: var(--ink); }
.section { display: none; }
.section.active { display: block; }
.segment-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; align-items: start; margin-top: 18px; }
.segment-grid .panel { margin-top: 0; height: 100%; }
/* Folds — same affordance as curator's board: a quiet uppercase bar that turns
   its caret when open. Breakdowns start closed so the Jobs page opens on the
   job table alone rather than on eight stacked tables. */
.fold { margin: 0; }
.fold > summary { list-style: none; cursor: pointer; display: flex; align-items: center;
  gap: 8px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--panel); font-size: 12px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .07em; color: var(--muted); }
.fold > summary::-webkit-details-marker { display: none; }
.fold > summary:hover { color: var(--text); border-color: var(--accent-border); }
.fold > summary .caret { transition: transform .15s ease; flex: 0 0 auto; }
.fold[open] > summary .caret { transform: rotate(90deg); }
.fold[open] > summary { border-bottom-left-radius: 0; border-bottom-right-radius: 0; color: var(--text); }
.fold > summary .hint { margin-left: auto; text-transform: none; letter-spacing: 0;
  font-weight: 500; font-size: 11.5px; color: var(--fg-faint); margin-bottom: 0; }
.fold-body { border: 1px solid var(--line); border-top: 0; border-radius: 0 0 10px 10px; padding: 14px; }
.segment-panel h2 { font-size: 16px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; background: var(--panel); }
th { text-align: left; color: var(--muted); font-weight: 650; background: color-mix(in srgb, var(--panel-soft) 78%, transparent); white-space: nowrap; }
th.sortable { cursor: pointer; }
th.sortable::after { content: " ↕"; color: #a9a297; font-size: 11px; }
th.segment-sort { cursor: pointer; user-select: none; }
th.segment-sort::after { content: " ↕"; color: #a9a297; font-size: 11px; }
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
.bar-fill { position: absolute; inset: 0 auto 0 0; background: var(--accent-fill); border-radius: inherit; }
.bar span { position: relative; z-index: 1; display: block; line-height: 18px; text-align: center; font-size: 12px; color: var(--bar-text); font-weight: 650; }
.muted { color: var(--muted); }
.difficulty-warning { color: var(--amber); font-size: 12px; margin-top: 2px; }
details > summary { cursor: pointer; color: var(--blue); font-size: 13px; padding: 4px 0; user-select: none; }
.eval-table { margin-top: 8px; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.eval-table th { background: var(--panel-softer); font-size: 13px; }
.eval-table td { font-size: 13px; }
.footer { color: var(--muted); font-size: 12px; padding: 24px 0 0; text-align: center; }
.empty { color: var(--muted); padding: 24px; text-align: center; font-style: italic; }
.kv { display: grid; grid-template-columns: 120px 1fr; gap: 8px 12px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
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
.mini-bar-fill { height: 100%; background: var(--accent-fill); border-radius: inherit; }
.mini-bar-value { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.split-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, .72fr); gap: 14px; align-items: start; }
.source-link { border: 0; background: transparent; color: inherit; padding: 0; text-align: left; font: inherit; max-width: 100%; }
.source-link:hover { color: var(--active-text); background: transparent; border-color: transparent; }
.source-name { display: inline-flex; align-items: center; gap: 7px; max-width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }
.source-sub { color: var(--muted); font-size: 12px; margin-top: 3px; overflow-wrap: anywhere; }
.metric-pill { display: inline-flex; align-items: center; min-height: 22px; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); background: var(--panel-soft); font-variant-numeric: tabular-nums; }
.metric-pill.good { background: #4a944022; color: var(--green); border-color: #4a944055; }
.metric-pill.warn { background: #fab21922; color: var(--amber); border-color: #fab21955; }
.metric-pill.bad { background: #d03b3b22; color: var(--red); border-color: #d03b3b55; }
.subscore-matrix-table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.subscore-matrix-table th, .subscore-matrix-table td { padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }
.subscore-matrix-table th { position: sticky; top: 0; background: var(--panel); z-index: 1; font-size: 12px; }
.subscore-matrix-table th .sub-weight { display: block; margin-top: 2px; color: var(--muted); font-size: 10px; font-weight: 500; }
.subscore-matrix-table td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; max-width: 360px; overflow-wrap: anywhere; }
.subscore-matrix-table td.num { text-align: right; }
.subscore-matrix-table th.score-sep, .subscore-matrix-table td.score-sep { border-right: 2px solid var(--line); }
.subscore-matrix-table th.segment-sort { cursor: pointer; }
/* Trajectory inspector — the same fixed two-pane dialog curator uses for sample
   tasks: the sampled trajectories on the left, the selected one on the right.
   Fixed rather than content-sized so the dialog does not jump between a card with
   no preview and one carrying a full turn-by-turn trace. */
.traj-box { width: min(1160px, 95vw); height: min(760px, 88vh); max-height: none; }
.traj-body { padding: 0; display: grid; grid-template-columns: 280px minmax(0, 1fr);
  min-height: 0; flex: 1; overflow: hidden; }
.traj-side { border-right: 1px solid var(--line); overflow-y: auto; padding: 12px;
  background: var(--panel-soft); min-height: 0; }
.traj-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }

.traj-filters { display: flex; flex-direction: column; gap: 6px; padding-bottom: 10px;
  margin-bottom: 8px; border-bottom: 1px solid var(--line); }
.traj-filters input, .traj-filters select { width: 100%; font-size: 12px; }
.traj-filter-row { display: flex; gap: 6px; }
.traj-filter-row select { flex: 1 1 auto; min-width: 0; }
.traj-filter-row button { flex: 0 0 auto; font-size: 12px; }
.traj-filters .hint { font-size: 11px; color: var(--muted); }
/* Highlighted JSON always sits on the dark editor surface, in both themes —
   curator's file pane does the same. One surface means one token palette that is
   guaranteed to have contrast, instead of two that have to be kept in step. */
.json-hl { background: #1a1714; color: #f0ede7; border: 1px solid #302a24; }
.json-hl .json-key { color: #9ec7c2; }
.json-hl .json-str { color: #c9a26a; }
.json-hl .json-num { color: #e0a06f; }
.json-hl .json-lit { color: #b48ead; }
.traj-picker { display: flex; flex-direction: column; gap: 5px; }
.traj-group { display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  margin: 10px 0 2px; padding-bottom: 4px; border-bottom: 1px solid var(--line); }
.traj-group:first-child { margin-top: 0; }
.traj-group-name { font-size: 11px; font-weight: 650; color: var(--fg-dim); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.traj-group-n { font-size: 10.5px; color: var(--fg-faint); white-space: nowrap; }
.traj-pick { display: flex; flex-direction: column; gap: 3px; align-items: flex-start;
  background: transparent; border: 1px solid transparent; border-radius: 8px;
  padding: 8px 10px; cursor: pointer; font: inherit; font-size: 12px; color: var(--text);
  text-align: left; width: 100%; min-width: 0; }
.traj-pick:hover { border-color: var(--line); background: var(--panel); }
.traj-pick.active { background: var(--accent-soft); border-color: var(--accent-border); }
.traj-pick .sid { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px; width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.traj-pick .smeta { color: var(--muted); font-size: 10.5px; }

.traj-head { padding: 14px 20px 12px; border-bottom: 1px solid var(--line); }
.traj-head .t { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
  font-weight: 650; word-break: break-all; line-height: 1.4; }
.traj-head .m { color: var(--muted); font-size: 11.5px; margin-top: 4px; }
.traj-head .actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.traj-tabs { display: flex; flex-wrap: wrap; gap: 4px; padding: 10px 20px 0; }
.traj-tab { background: transparent; border: 1px solid transparent; color: var(--fg-dim);
  border-radius: 8px 8px 0 0; padding: 6px 12px; cursor: pointer; font: inherit;
  font-size: 12px; white-space: nowrap; }
.traj-tab:hover { color: var(--text); }
.traj-tab.active { background: var(--panel-soft); border-color: var(--line);
  border-bottom-color: var(--panel-soft); color: var(--text); font-weight: 650; }
.traj-tab .sz { color: var(--fg-faint); font-size: 10.5px; margin-left: 6px; }
.traj-pane { flex: 1; min-height: 0; margin: 0 20px 20px; border: 1px solid var(--line);
  border-radius: 0 10px 10px 10px; background: var(--panel-soft); overflow: auto;
  padding: 16px 18px; }
.traj-pane > .empty, .traj-pane .empty { color: var(--muted); font-size: 12.5px; }
.traj-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
.traj-pane .detail-preview { max-height: none; margin-top: 0; background: var(--panel); }
.traj-pane .json-block { max-height: none; background: var(--panel); }
.traj-pane .kv { margin: 0; }
.traj-pane h2 { font-size: 13px; margin: 18px 0 8px; }
.traj-pane h2:first-child { margin-top: 0; }
.traj-view { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); min-height: 520px; padding: 14px; }
.step-row { width: 100%; display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 8px; padding: 9px 11px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; text-align: left; background: var(--panel); }
.step-row:hover, .step-row.active { background: var(--active-row); border-color: var(--line); }
.step-row .id { color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; padding-top: 2px; }
.step-row-body { min-width: 0; display: grid; gap: 5px; }
.step-row .desc { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-badges { display: flex; gap: 5px; flex-wrap: wrap; }
.step-badge { display: inline-flex; align-items: center; min-height: 20px; padding: 1px 6px; border: 1px solid var(--line); border-radius: 999px; font-size: 10.5px; color: var(--muted); background: var(--panel-soft); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.step-badge.good { background: #4a944022; color: var(--green); border-color: #4a944055; }
.step-badge.bad { background: #d03b3b22; color: var(--red); border-color: #d03b3b55; }
.step-badge.warn { background: #fab21922; color: var(--amber); border-color: #fab21955; }
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
.trace-block.thought { border-color: #3987e555; background: #3987e50a; }
.trace-block.action { border-color: #fab21955; background: #fab2190a; }
.trace-block.observation { border-color: #4a944055; background: #4a94400a; }
.trace-block.error { border-color: #d03b3b55; background: #d03b3b0d; }
.block-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.block-label { color: var(--muted); font-size: 10.5px; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }
.block-tool-name { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: var(--amber); background: #fab21922; padding: 1px 7px; border-radius: 4px; }
.block-pre, .preface-pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; background: #1a1714; color: #f0ede7; border: 1px solid #302a24; border-radius: 6px; padding: 10px; max-height: 420px; overflow-y: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.45; }
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
  .kpis, .samples-layout, .split-layout { grid-template-columns: 1fr; }
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


def render_job_segment_row(
    job: dict[str, Any] | None,
    segment: dict[str, Any] | None = None,
    fallback_name: str = "",
) -> str:
    """One row per job-shaped thing: a Harbor job, an imported dataset, or both.

    A dataset that arrived already converted has no Harbor job behind it, so run
    status does not apply and is reported as such rather than as "running".
    """
    segment = segment or {}
    has_job = bool(job)
    job = job or {}
    job_name = str(job.get("job") or fallback_name or segment.get("value") or "unknown")
    job_id = str(job.get("id") or "-")
    job_path = str(job.get("path") or "")
    job_color = stable_identity_color(job_name)
    scaffold = str(job.get("scaffold") or "unknown")
    status = (
        status_badge(job.get("finished_at")) if has_job
        else '<span class="muted" title="not produced by a Harbor job in this block">n/a</span>'
    )
    progress = (
        progress_bar(job.get("n_trials"), job.get("n_total_trials")) if has_job
        else '<span class="muted">-</span>'
    )
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


def svg_icon(name: str) -> str:
    icons = {
        "overview": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="14" width="7" height="7" rx="1.5"></rect><rect x="3" y="14" width="7" height="7" rx="1.5"></rect></svg>',
        "instances": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="5" rx="8" ry="3"></ellipse><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"></path><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"></path></svg>',
        "trajectories": '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="3"></circle><circle cx="18" cy="6" r="3"></circle><circle cx="18" cy="18" r="3"></circle><path d="M9 6h6"></path><path d="M6 9v2a7 7 0 0 0 7 7h2"></path></svg>',
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
    r2_api: bool = False,
) -> str:
    jobs_sorted = sorted(jobs, key=lambda j: j.get("started_at") or "", reverse=True)
    sft_sorted = sorted(sft, key=lambda s: s["job"])
    now_str = now_bjt().strftime("%Y-%m-%d %H:%M:%S BJT")

    # What this board read, so a surprising number can be traced to a path
    # without leaving the page.
    _sources = [
        ("Harbor jobs", jobs_dir, len(jobs)),
        ("SFT data", sft_dir, len(sft)),
        ("Harbor job dir", harbor_jobs_dir, None),
        ("Run index", index_path, None),
    ]
    # Scores come only from converted SFT data, which is optional. Empty axes read
    # as "scored nothing" rather than "nothing was scored", so drop the surfaces.
    has_quality = bool((analysis.get("summary") or {}).get("quality_count"))
    rubric_html = render_rubric_html() if has_quality else ""
    # Two absences: `quality` gates the score surfaces, `sft` gates the counters of
    # converted data. A block that converted nothing has neither.
    r2_api_js = "true" if r2_api else "false"
    has_sft = bool(sft)
    gate_rules = [
        rule for present, rule in (
            (has_quality, "[data-requires-quality]"),
            (has_sft, "[data-requires-sft]"),
        ) if not present
    ]
    quality_gate_css = (
        f"<style>{','.join(gate_rules)}{{display:none !important}}</style>"
        if gate_rules else ""
    )
    info_html = (
        "<div class='sub'>Sources</div><table>"
        + "".join(
            f"<tr><td>{html.escape(name)}</td>"
            f"<td class='mono'>{html.escape(display_path(path))}</td>"
            f"<td>{'' if n is None else f'{n:,} found'}"
            f"{'' if Path(path).exists() else '<em>not found</em>'}</td></tr>"
            for name, path, n in _sources
        )
        + "</table>"
        f"<div class='sub'>Refresh</div><table><tr><td>interval</td>"
        f"<td class='mono'>{refresh_seconds}s</td><td></td></tr></table>"
        "<div class='sub'>Exports</div><table><tr><td>summary JSON</td>"
        "<td class='mono'>data/summary.json</td>"
        "<td><button class='copy-btn' data-copy='data/summary.json'>Copy</button></td>"
        "</tr></table>"
    )

    missing_jobs = sum(1 for j in jobs_sorted if not j.get("source_exists", True))
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
    # These three count over the converted set when there is one, and over all
    # rolled-out trials otherwise — different denominators under the same label,
    # so say which is in force rather than leaving it to be guessed.
    _dim_scope = (
        "across converted SFT data" if coverage.get("dimensions_from") == "sft"
        else "across all trials"
    )
    def _hint(values: Any) -> str:
        text = compact_values(values or [])
        return html.escape(f"{text} · {_dim_scope}" if text else _dim_scope)

    language_hint = _hint(coverage.get("languages"))
    model_hint = _hint(coverage.get("models") or coverage.get("teacher_models"))
    scaffold_hint = _hint(coverage.get("scaffolds"))
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
        render_job_segment_row(job_by_name.get(name), job_segments.get(name), name)
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
        "<th class='num sortable'>Valid Trajectories</th><th class='num sortable'>Tokens</th>"
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
        '<th class="num segment-sort" data-segment-sort="quality_records">Valid Trajectories</th>'
        '<th class="num segment-sort" data-segment-sort="avg_quality_tokens">Tokens</th>'
        '</tr></thead>'
    )
    difficulty_segment_header = segment_header.replace(
        f'<th class="num segment-sort" data-segment-sort="task_difficulty_avg" title="{difficulty_title}">Task Difficulty</th>',
        "",
    )
    segment_panels = job_segment_panel + "".join(
        '<details class="fold segment-fold">'
        f'<summary>{CARET_SVG}{html.escape(SEGMENT_LABELS.get(dim, dim))}'
        f'<span class="hint">pass rate, difficulty and token totals per '
        f'{html.escape(SEGMENT_LABELS.get(dim, dim).lower())}</span></summary>'
        '<div class="fold-body">'
        f'<div class="table-wrap"><table class="segment-table" data-segment-dim="{html.escape(dim)}">'
        f'{difficulty_segment_header if dim == "difficulty" else segment_header}<tbody data-segment-rows="{html.escape(dim)}"></tbody></table></div>'
        "</div></details>"
        for dim in segment_dims if dim != "job"
    )
    icon_overview = svg_icon("overview")
    icon_instances = svg_icon("instances")
    icon_trajectories = svg_icon("trajectories")
    icon_moon = svg_icon("moon")
    icon_sun = svg_icon("sun")
    icon_refresh = svg_icon("refresh")
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
<title>LegoFlow-Tracer</title>
<link rel="icon" type="image/svg+xml" href="{FAVICON_DATA_URI}">
<script>
(() => {{
  const saved = localStorage.getItem('tracer-theme');
  document.documentElement.dataset.theme = saved === 'dark' ? 'dark' : 'light';
}})();
</script>
<style>{CSS}</style>
{quality_gate_css}
</head>
<body>
<div class="app-shell">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-lockup">
        <div class="brand-mark" aria-hidden="true">LF</div>
        <div class="brand-title">
          <h1>Tracer Dashboard</h1>
        </div>
      </div>
    </div>
    <nav class="side-nav" aria-label="Dashboard sections">
      <button class="nav-item active" data-page="overview"><span class="nav-icon">{icon_overview}</span><span class="nav-label">Overview</span></button>
      <button class="nav-item" data-page="instances"><span class="nav-icon">{icon_instances}</span><span class="nav-label">Jobs</span></button>
      <button class="nav-item" data-page="trajectories"><span class="nav-icon">{icon_trajectories}</span><span class="nav-label">Trajectories</span></button>
    </nav>
  </aside>
  <div class="main-shell">
    <div class="topbar">
      <div>
        <h2 id="pageTitle">Overview</h2>
        <p id="pageSubtitle">Monitor generation health, pass rate, quality score, and data coverage.</p>
      </div>
      <div class="topbar-actions">
        <span class="update-status">Updated {html.escape(now_str)} &middot; refresh {refresh_seconds}s</span>
        <button id="metricsToggle" class="icon-btn" type="button" data-requires-quality aria-label="Trajectory scoring rubric" title="How the trajectory quality score is computed">{ICON_METRICS}</button>
        <button id="infoToggle" class="icon-btn" type="button" aria-label="Dashboard info" title="What this board is reading">{ICON_INFO}</button>
        <button id="refreshNow" class="icon-btn refresh-btn" type="button" aria-label="Refresh dashboard" title="Refresh dashboard">{icon_refresh}</button>
        <button id="themeToggle" class="icon-btn theme-toggle" type="button" aria-label="Toggle black and white theme" title="Toggle theme"><span class="theme-moon">{icon_moon}</span><span class="theme-sun">{icon_sun}</span></button>
      </div>
    </div>
<div class="modal" id="trajPanel" role="dialog" aria-modal="true" hidden>
  <div class="modal-box traj-box">
    <div class="modal-head">
      <div><h3 id="trajPanelTitle">Trajectory</h3>
        <div class="sub">metrics, preview and the full turn-by-turn trace when available</div></div>
      <button class="modal-close" data-close type="button" aria-label="Close">&times;</button>
    </div>
    <div class="traj-body">
      <div class="traj-side">
        <div class="traj-filters">
          <input id="trajSearch" placeholder="Search instance, source, language, status">
          <select id="trajSource"><option value="">All sources</option></select>
          <div class="traj-filter-row">
            <span id="trajSampleInfo" class="hint"></span>
            <button id="trajResample" type="button">Resample</button>
          </div>
        </div>
        <div id="trajList" class="traj-picker"></div>
      </div>
      <div class="traj-main" id="trajView"></div>
    </div>
  </div>
</div>
<div class="modal" id="metricsPanel" role="dialog" aria-modal="true" data-requires-quality hidden>
  <div class="modal-box">
    <div class="modal-head">
      <div><h3>Trajectory scoring rubric</h3>
        <div class="sub">how the quality score on this board is computed</div></div>
      <button class="modal-close" data-close type="button" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">{rubric_html}</div>
  </div>
</div>
<div class="modal" id="infoPanel" role="dialog" aria-modal="true" hidden>
  <div class="modal-box">
    <div class="modal-head">
      <div><h3>What this board is reading</h3>
        <div class="sub">resolved at render time</div></div>
      <button class="modal-close" data-close type="button" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">{info_html}</div>
  </div>
</div>
<main class="content">
  <section id="overview" class="section active" data-title="Overview" data-subtitle="Monitor generation health, pass rate, quality score, and data coverage.">
    <div class="grid kpis">
    <div class="card" data-requires-sft><div class="label">Valid Trajectories</div>
      <div class="value">{fmt_num(coverage.get('valid_trajs'))}</div>
      <div class="sub">for SFT in LF format</div></div>
    <div class="card" data-requires-sft><div class="label">Valid Tokens</div>
      <div class="value">{fmt_tokens_b(coverage.get('valid_tokens'))}</div>
      <div class="sub">for SFT in LF format</div></div>
    <div class="card" data-requires-sft><div class="label">Data Sources</div>
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
      <section class="panel" data-requires-quality>
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
    <section class="panel" data-requires-sft>
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
    <section class="panel" data-requires-quality>
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
        <p class="hint">Search, filter and sample concrete trajectories, then read one turn by turn. Opens as a dialog so the trace gets the whole screen.</p></div>
        <div class="actions"><button id="openSampler" type="button">Open sampler</button></div></div>
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
// What the sampler is showing; the dialog's left pane lists exactly this.
let trajVisibleCards = [];
let trajActiveTab = 'details';
const TRAJ_SAMPLE_SIZE = 10;
// Whether /api/traj can actually serve a trace that is not embedded. Without the
// R2 binding it answers 503, so offering the control would fail on every click.
const R2_API_AVAILABLE = {r2_api_js};
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

// One rule: a random draw, preferring trajectories that can be opened. Resample
// advances the seed.
function trajSampleCards(cards) {{
  const available = cards.filter(card => trajAvailabilityRank(card) < 2);
  const pool = available.length ? available : cards;
  return seededShuffle(pool, trajSampleSeed);
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
  const alpha = (n * 0.34).toFixed(3);
  return `<td class="num ${{extraClass}}" style="background:rgba(var(--matrix-heat-rgb),${{alpha}})">${{Number(value).toFixed(3)}}</td>`;
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
    <th class="segment-sort ${{subscoreSort.field === 'source' ? 'active ' + subscoreSort.dir : ''}}" data-subscore-sort="source" title="The Harbor job or imported dataset these trajectories came from. One row per batch.">Data Source${{subscoreSort.field === 'source' ? arrow : ''}}</th>
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
  const source = $('#trajSource')?.value || '';
  const matched = trajCardData
    .filter(card => !source || trajSourceName(card) === source)
    .filter(card => {{
      if (!q) return true;
      const hay = [card.id, card.instance_id, card.task_name, card.job, card.dataset, card.source, card.language, card.status, card.model, card.scaffold, card.exception_type].join(' ').toLowerCase();
      return hay.includes(q);
    }});

  // Per batch, not from one pool: a global draw lets the largest batch crowd out
  // the rest, and the availability preference can drop a whole batch that has no
  // reachable trace. Ten from each means every batch shows up.
  const groups = new Map();
  for (const card of matched) {{
    const key = trajSourceName(card) || 'unknown';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(card);
  }}
  const sections = [...groups.entries()]
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
    .map(([name, list]) => ({{name, total: list.length,
                             cards: trajSampleCards(list).slice(0, TRAJ_SAMPLE_SIZE)}}));
  const cards = sections.flatMap(section => section.cards);
  trajVisibleCards = cards;
  const info = $('#trajSampleInfo');
  if (info) {{
    info.textContent = sections.length
      ? `${{cards.length}} shown · up to ${{TRAJ_SAMPLE_SIZE}} per batch · ${{sections.length}} batch${{sections.length === 1 ? '' : 'es'}}`
      : 'nothing matches';
  }}
  if (!cards.length) {{
    list.innerHTML = '<div class="empty">No trajectory cards match the current filters.</div>';
    const view = $('#trajView');
    if (view) view.innerHTML = '<div class="traj-pane"><div class="empty">No trajectory selected.</div></div>';
    currentTraj = null;
    return;
  }}
  // Narrow rows — an id and one line of metadata, curator's sample-row shape —
  // under a heading per batch, so it is always clear which batch a row came from.
  let n = 0;
  list.innerHTML = sections.map(section => {{
    const rows = section.cards.map(card => {{
      const idx = n++;
      return `
    <button class="traj-pick ${{idx === 0 ? 'active' : ''}}" data-i="${{idx}}" data-id="${{escapeHtml(card.id)}}">
      <span class="sid">#${{idx + 1}} ${{escapeHtml(card.instance_id || card.task_name || card.id)}}</span>
      <span class="smeta">${{escapeHtml(card.status || card.kind || '-')}} · score ${{formatMaybe(card.score)}} · ${{formatMaybe(card.turns)}} turns · ${{escapeHtml(card.language || 'unknown')}}${{card.embedded_available ? ' · embedded' : ''}}</span>
    </button>`;
    }}).join('');
    return `<div class="traj-group"><span class="traj-group-name" title="${{escapeHtml(section.name)}}">${{escapeHtml(section.name)}}</span>`
         + `<span class="traj-group-n">${{section.cards.length}} of ${{section.total}}</span></div>${{rows}}`;
  }}).join('');
  $$('.traj-pick', list).forEach(btn => btn.addEventListener('click', () => selectTrajectory(cards[Number(btn.dataset.i)], btn)));
  // Prime the detail so the dialog never opens on an empty right pane.
  selectTrajectory(cards[0], $('.traj-pick', list));
}}

// The sampler is the dialog. Opening it renders the current sample if that has
// not happened yet, so the page carries no standing trajectory list.
function openSampler() {{
  const panel = document.getElementById('trajPanel');
  if (!panel) return;
  if (!trajVisibleCards.length) renderTrajectoryList();
  panel.hidden = false;
}}

function scoreBreakdown(card) {{
  const keys = ['score_v3', 'score_v4', 'efficiency_score', 'style_score', 'tool_mastery_score', 'completion_score', 'precision_score'];
  const rows = keys.filter(key => card[key] !== undefined && card[key] !== null).map(key => `<dt>${{escapeHtml(key)}}</dt><dd>${{formatMaybe(card[key])}}</dd>`).join('');
  return rows ? `<h2>Score Breakdown</h2><dl class="kv">${{rows}}</dl>` : '';
}}

function selectTrajectory(card, button) {{
  currentTraj = card;
  $$('.traj-pick').forEach(item => item.classList.remove('active'));
  if (button) button.classList.add('active');
  const dialogTitle = document.getElementById('trajPanelTitle');
  if (dialogTitle) dialogTitle.textContent = card.instance_id || card.task_name || card.id || 'Trajectory';
  const view = $('#trajView');
  view.classList.remove('empty');
  // No heading: the tab it lives under already names it.
  const preview = card.preview ? `<div class="detail-preview">${{escapeHtml(card.preview)}}</div>` : '';
  // Say up front whether this trace can be opened here: only embedded ones load
  // from the page, everything else needs the R2 binding.
  // Three distinct reasons a trace does or does not open, and they call for three
  // different sentences. The generic "not reachable" line read as a fault when the
  // usual case is simply an SFT record, which never had a trajectory file at all.
  const traceNote = card.embedded_available
    ? 'Embedded in this page — opens offline, no backend needed.'
    : (card.full_available
       ? (R2_API_AVAILABLE
          ? 'Not embedded: this trace is larger than the per-record embed cap, so it is fetched from R2 on demand.'
          : 'Not embedded: this trace is larger than the per-record embed cap, and this board has no R2 backend to fetch it from. It is readable at the local path above, on the machine that produced it.')
       : (card.kind === 'quality'
          ? 'This row is a converted SFT record, not a Harbor rollout, so there is no separate trajectory file to open. Its conversation lives inside the dataset\\'s im.jsonl, which is not published with this board — the Preview tab shows a bounded excerpt.'
          : 'No trajectory file was recorded for this run, so there is nothing to open.'));
  const error = card.exception_type ? `<dt>Exception</dt><dd>${{escapeHtml(card.exception_type)}}</dd>` : '';
  // A local path proves nothing to a remote reader. Offer the control only when
  // the trace is embedded, or a backend was declared that can fetch it.
  const canLoad = card.embedded_available || (card.full_available && R2_API_AVAILABLE);
  const loadAction = canLoad
    ? `<button id="loadFullTraj" type="button">${{card.embedded_available ? 'Open embedded trace' : 'Load full'}}</button>`
    : '';
  // Head / tabs / one scrolling pane, as curator's sample viewer. Tabs keep the
  // dialog a fixed size whatever the card carries.
  const details = `
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
    ${{scoreBreakdown(card)}}`;
  const tabs = [
    {{key: 'details', label: 'Details', body: details}},
    {{key: 'preview', label: 'Preview',
     body: preview || '<div class="empty">no preview was embedded for this trajectory</div>'}},
    {{key: 'trace', label: 'Full trace',
     body: `<div class="traj-actions">${{loadAction}}</div>`
           + `<div class="empty">${{traceNote}}</div>`
           + '<div id="fullTrajResult" class="json-block hidden"></div>'}},
  ];
  view.innerHTML = `
    <div class="traj-head">
      <div class="t">${{escapeHtml(card.instance_id || card.task_name || card.id)}}</div>
      <div class="m">${{escapeHtml(card.kind)}} · ${{escapeHtml(card.job || card.dataset || '-')}} · ${{escapeHtml(card.status || '-')}}</div>
      <div class="actions">
        <button class="copy-btn" data-copy="${{escapeHtml(card.r2_key || '')}}">Copy R2 key</button>
        <button class="copy-btn" data-copy="${{escapeHtml(card.path || card.trajectory_path || '')}}">Copy local path</button>
      </div>
    </div>
    <div class="traj-tabs">${{tabs.map(t => `<button class="traj-tab" data-tab="${{t.key}}">${{t.label}}</button>`).join('')}}</div>
    <div class="traj-pane" id="trajPane"></div>`;

  function showTab(key) {{
    const tab = tabs.find(t => t.key === key) || tabs[0];
    trajActiveTab = tab.key;
    $('#trajPane').innerHTML = tab.body;
    $$('.traj-tab', view).forEach(b => b.classList.toggle('active', b.dataset.tab === tab.key));
    $('#loadFullTraj')?.addEventListener('click', () => loadFullTrajectory(card));
  }}
  $$('.traj-tab', view).forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
  showTab(trajActiveTab);
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
    box.classList.remove('json-hl');
    box.textContent =
      `This trace is not embedded in the page, and /api/traj could not serve it (${{err}}).\\n`
      + `Opening it needs the TRACER_TRAJ_BUCKET R2 binding on the Pages project, with the `
      + `object uploaded.\\n\\nR2 key:     ${{card.r2_key || '-'}}\\nLocal path: ${{card.trajectory_path || card.path || '-'}}`;
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

// Escape first, tokenize second — the other order eats the markup just inserted.
// Only & < > are escaped: the tokenizer needs quotes to find string boundaries,
// and a bare quote is harmless in element content. Strings match whole, so a
// number inside one is never mis-coloured.
function highlightJson(value) {{
  let text;
  try {{
    text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  }} catch (err) {{
    return escapeHtml(String(value));
  }}
  if (text === undefined || text === null) return '';
  const escaped = String(text).replace(/[&<>]/g, ch => ({{'&': '&amp;', '<': '&lt;', '>': '&gt;'}}[ch]));
  return escaped.replace(
    /("(?:\\\\.|[^"\\\\])*")(\s*:)?|\\b(?:true|false|null)\\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
    (match, str, colon) => {{
      if (str !== undefined) {{
        return colon !== undefined && colon !== null
          ? `<span class="json-key">${{str}}</span>${{colon}}`
          : `<span class="json-str">${{str}}</span>`;
      }}
      if (/^(?:true|false|null)$/.test(match)) return `<span class="json-lit">${{match}}</span>`;
      return `<span class="json-num">${{match}}</span>`;
    }});
}}

function renderFullTrajectory(box, card, data) {{
  const record = normalizeTrajectoryPayload(data);
  box.classList.remove('json-block');
  box.innerHTML = '';
  if (!record || typeof record !== 'object') {{
    box.classList.add('json-block', 'json-hl');
    box.innerHTML = highlightJson(data);
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
  // One rendering for every payload. Splitting into turns was a second, busier
  // view of the same bytes, and which one you got depended on the payload shape.
  const pre = document.createElement('pre');
  pre.className = 'block-pre json-hl';
  pre.innerHTML = highlightJson(record);
  wrap.appendChild(pre);
  box.appendChild(wrap);
}}



function prefaceCard(label, text, open) {{
  const details = document.createElement('details');
  details.className = 'preface-card';
  if (open) details.setAttribute('open', '');
  details.innerHTML = `<summary><span class="block-label">${{escapeHtml(label)}}</span><span class="muted">${{formatMaybe((text || '').length)}} chars</span></summary><pre class="preface-pre">${{escapeHtml(text || '')}}</pre>`;
  return details;
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

document.addEventListener('keydown', event => {{
  if (event.key !== 'Escape') return;
  for (const id of ['infoPanel', 'metricsPanel', 'trajPanel']) {{
    const panel = document.getElementById(id);
    if (panel) panel.hidden = true;
  }}
}});

document.addEventListener('click', event => {{
  // Every dialog behaves the same: its button toggles it, and the backdrop or the
  // close control dismisses it. Dismissal resolves against the dialog the click
  // actually happened in — matching on data-close alone would close whichever
  // dialog this loop happened to reach first.
  const PANELS = [['infoPanel', 'infoToggle'], ['metricsPanel', 'metricsToggle'], ['trajPanel', null]];
  for (const [panelId, buttonId] of PANELS) {{
    const panel = document.getElementById(panelId);
    const button = buttonId && document.getElementById(buttonId);
    if (!panel || !button) continue;
    if (button.contains(event.target)) {{
      const opening = panel.hidden;
      for (const [otherId] of PANELS) {{
        const other = document.getElementById(otherId);
        if (other) other.hidden = true;
      }}
      panel.hidden = !opening;
      return;
    }}
  }}
  const owner = event.target.closest ? event.target.closest('.modal') : null;
  if (owner && (event.target === owner
                || (event.target.hasAttribute && event.target.hasAttribute('data-close')))) {{
    owner.hidden = true;
    return;
  }}
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
['trajSearch','trajSource'].forEach(id => {{
  const el = $('#' + id);
  if (el) el.addEventListener('input', renderTrajectoryList);
  if (el) el.addEventListener('change', renderTrajectoryList);
}});
$('#trajResample')?.addEventListener('click', () => {{
  trajSampleSeed += 1;
  renderTrajectoryList();
}});
$('#openSampler')?.addEventListener('click', openSampler);
$$('th.sortable').forEach(th => th.addEventListener('click', () => sortTable(th)));
$('#copySampleId')?.addEventListener('click', () => copyText(currentSample?.instance_id || ''));
$('#themeToggle')?.addEventListener('click', () => setTheme(currentTheme() === 'dark' ? 'light' : 'dark'));
$('#refreshNow')?.addEventListener('click', event => {{
  const btn = event.currentTarget;
  btn.classList.add('refreshing');
  btn.setAttribute('aria-busy', 'true');
  window.location.reload();
}});
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

def _batch_note(child: Path) -> str:
    """Say where a batch really lives when its name does not.

    Staged pools are symlinks, so the contents can sit anywhere on disk.
    """
    if not child.is_symlink():
        return ""
    return f"-> {child.resolve()}"


def discover_sources(block_dir: Path = BLOCK_DIR) -> dict[str, Any]:
    """Resolve what the board reads. Fixed locations under artifacts/, never
    configured.

    * Tasks — every immediate child of `artifacts/tasks/` is one batch, holding
      one harbor task per child of its own (a real dir of symlinks, as staged).
    * Jobs  — every immediate child of `artifacts/jobs/` is one Harbor job.
    * SFT   — every immediate child of `artifacts/sft_data/` is one converted
      dataset. Optional: with none, the board simply carries no score surfaces.
    """
    artifacts = block_dir / "artifacts"

    def children(root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        return sorted(
            (c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")),
            key=lambda c: c.name,
        )

    tasks_root = artifacts / "tasks"
    task_batches = []
    for child in children(tasks_root):
        n_tasks = sum(1 for t in child.iterdir() if (t / "task.toml").is_file())
        n_other = sum(1 for t in child.iterdir() if t.is_dir() and not (t / "task.toml").is_file())
        task_batches.append({
            "name": child.name, "path": child, "tasks": n_tasks,
            "ignored": n_other, "note": _batch_note(child),
        })

    jobs_root = artifacts / "jobs"
    jobs = []
    for child in children(jobs_root):
        trials = sum(1 for t in child.iterdir() if t.is_dir() and not t.name.startswith("."))
        jobs.append({
            "name": child.name, "path": child, "trials": trials,
            "summarized": (child / "result.json").is_file(), "note": _batch_note(child),
        })

    sft_root = artifacts / "sft_data"
    sft = []
    for child in children(sft_root):
        present = [n for n in ("lf.json", "lf.stats.json", "im.jsonl") if (child / n).is_file()]
        sft.append({"name": child.name, "path": child, "files": present,
                    "note": _batch_note(child)})

    return {
        "tasks_root": tasks_root, "task_batches": task_batches,
        "jobs_root": jobs_root, "jobs": jobs,
        "sft_root": sft_root, "sft": sft,
        "index_file": artifacts / "index.yaml",
    }


def print_source_report(sources: dict[str, Any]) -> int:
    """Print the resolved sources for an operator to confirm before rendering.

    Read-only and cheap: no trajectory is opened or parsed.
    """
    line = "=" * 70
    out = [line, "tracer dashboard sources", line]

    def section(label: str, root: Path, rows: list[str], empty: str) -> None:
        out.append(f"  {label:<22} {root}")
        out.extend(rows or [f"    {empty}"])

    section(
        "task batches", sources["tasks_root"],
        [
            f"    {b['name']:<38} {b['tasks']} task(s)"
            + (f" · {b['ignored']} non-task dir(s) ignored" if b["ignored"] else "")
            + (f"\n      {b['note']}" if b["note"] else "")
            for b in sources["task_batches"]
        ],
        "none staged — prepare_tasks.sh links them in at launch",
    )
    section(
        "harbor jobs", sources["jobs_root"],
        [
            f"    {j['name']:<38} {j['trials']} trial dir(s)"
            + ("" if j["summarized"] else " · no result.json (still running or aborted)")
            for j in sources["jobs"]
        ],
        "no jobs yet",
    )
    section(
        "sft data", sources["sft_root"],
        [f"    {s['name']:<38} {', '.join(s['files']) or 'no converted files'}"
         for s in sources["sft"]],
        "none — optional; the board omits trajectory-score surfaces without it",
    )

    idx = sources["index_file"]
    out.append(f"  {'run index':<22} {idx}" + ("" if idx.is_file() else "  (absent)"))
    out.append(line)
    print("\n".join(out))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report-only", action="store_true",
                   help="Print the resolved sources and the batches found under each, then exit. "
                        "Writes nothing; run this and confirm before rendering.")
    p.add_argument("--output-html", type=Path, default=DEFAULT_HTML)
    p.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--index-file", type=Path, default=DEFAULT_INDEX,
                   help="Archived-run index retained for source reporting and compatibility.")
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
                   help="No longer gates sample/trajectory inclusion (both modes embed bounded "
                        "previews by default) — use --public-no-samples for a metrics-only public "
                        "payload. Kept for compatibility with existing invocations.")
    p.add_argument("--public-no-samples", action="store_true",
                   help="Disable embedded sample previews and full trajectory embedding — the "
                        "metrics-only payload for a public dashboard.")
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
    p.add_argument("--r2-api", action="store_true",
                   help="Offer 'Load full' for traces that are not embedded. Only useful when the "
                        "Pages project has the TRACER_TRAJ_BUCKET R2 binding and the objects have "
                        "been uploaded; without it /api/traj answers 503, so the control is hidden "
                        "by default rather than failing on every click.")
    p.add_argument("--embedded-traj-max-record-bytes", type=int, default=EMBEDDED_TRAJ_MAX_RECORD_BYTES,
                   help="Skip embedding any single trajectory larger than this. A record cannot be "
                        "split across shards, so one huge trace would both blow the per-file limit of "
                        "the host and eat the whole budget. 0 disables the cap.")
    return p.parse_args(argv)


def effective_embedded_trajectory_limits(args: argparse.Namespace, *, include_samples: bool) -> tuple[int, int]:
    """Disable full trajectory exports whenever previews are disabled."""
    if args.public_no_samples or not include_samples:
        return 0, 0
    return (
        max(0, int(args.embedded_traj_limit or 0)),
        max(0, int(args.embedded_traj_max_bytes or 0)),
    )


def run_once(args: argparse.Namespace, refresh_seconds: int) -> dict[str, Any]:
    cache = {"version": CACHE_VERSION, "jobs": {}, "sft": {}} if args.force_full_scan else load_cache(args.cache_file)
    include_samples = bool(args.include_samples)
    if args.public_no_samples:
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
        include_previews=include_samples,
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
        embedded_traj_max_record_bytes=max(0, int(args.embedded_traj_max_record_bytes or 0)),
        embedded_traj_max_bytes=embedded_traj_max_bytes,
    )
    write_worker_script(args.output_html)
    # Second and last moment for this: write_data_exports sanitised what it wrote,
    # and these lists reach only the HTML. Both have to happen after embedding,
    # which is the one step that still needs the real locations.
    strip_published_paths(
        jobs, sft,
        analysis.get("quality_examples") or [],
        analysis.get("trial_examples") or [],
    )
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
        r2_api=bool(args.r2_api),
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
    if args.report_only:
        return print_source_report(discover_sources())
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
