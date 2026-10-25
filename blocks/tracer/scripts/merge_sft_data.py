#!/usr/bin/env python3
"""Merge per-scaffold SFT LF datasets into one cross-scaffold campaign dataset.

A multi-scaffold campaign (`runtime_info.input.agent.scaffolds`) produces one
`artifacts/sft_data/<job>/lf.json` per scaffold. This script loads each job's
lf.json and concatenates ALL records — cross-scaffold trajectories are
structurally different by design, so there is deliberately NO behavioral dedup.
With `--per-task-cap N` (N > 0), it keeps only the top N records per
`_instance_id`, ranked by `_score.composite_score` descending (missing score
counts as 0) with a deterministic tie-break on the scaffold label, then source
job, then original position. Scaffold labels are derived exactly the way the
dashboard derives them (dashboard/progress_monitor.py: normalize_scaffold):
per-record `_score.scaffold` first, falling back to the scaffold encoded in
the source job name — `_agent_type` is a converter constant ("main"), not the
scaffold identity, so it is never used for grouping.

Outputs (to --out-dir):
  lf.json             selected records, ordered by (_instance_id, rank)
  lf.stats.json       the exact field names the tracer dashboard reads
                      (dashboard/progress_monitor.py: collect_sft): count,
                      token_lens{min,mean,max}, n_turns, scores, total_tokens,
                      tool_call_errors{...,error_rate} — derived from record
                      meta_info, so the merged dir shows up on the board like
                      any converted job. token_lens is the distribution of
                      per-record _usage.total_tokens (the dashboard reads its
                      mean as "mean tokens per record"); it is NOT the
                      tokenizer-measured sequence length the per-job converter
                      stats may report.
  merge_manifest.json provenance: jobs, cap, per-scaffold counts, per-task
                      selection with ranks, sha256 of lf.json, command line

Exits non-zero on: unknown job, duplicate job, malformed record, zero records,
or a negative --per-task-cap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLOCK_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SFT_ROOT = "artifacts/sft_data"
DEFAULT_OUT_DIR = "artifacts/sft_data/merged"

sys.path.insert(0, str(BLOCK_DIR / "dashboard"))
from progress_monitor import normalize_scaffold  # noqa: E402  (same-block import; blocks never import across boundaries)


def scaffold_of(unique: dict[str, Any], source_job: str) -> str:
    """Scaffold label the way the dashboard reads it: _score.scaffold, else job name."""
    score = unique.get("_score") if isinstance(unique.get("_score"), dict) else {}
    return normalize_scaffold(score.get("scaffold"), source_job)


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--jobs",
        required=True,
        help="Comma-separated job names, resolved under --sft-root (each needs lf.json)",
    )
    ap.add_argument(
        "--sft-root",
        default=DEFAULT_SFT_ROOT,
        help=f"SFT data root that job names resolve under (default: {DEFAULT_SFT_ROOT}; "
        "relative paths resolve against the tracer block dir)",
    )
    ap.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR}; block-dir relative)",
    )
    ap.add_argument(
        "--per-task-cap",
        type=int,
        default=0,
        help="Keep at most N records per _instance_id, ranked by composite score desc "
        "(0 = no cap, the default)",
    )
    return ap.parse_args()


def resolve_block_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else BLOCK_DIR / path


def num(value: Any) -> float | None:
    """Coerce a metadata number; None for anything non-numeric (dashboard safe_float)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def load_job(job: str, job_dir: Path) -> list[dict[str, Any]]:
    lf_path = job_dir / "lf.json"
    if not lf_path.is_file():
        die(f"job '{job}' has no lf.json under {job_dir}")
    try:
        with lf_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        die(f"job '{job}' lf.json is unreadable: {exc}")
    if not isinstance(data, list):
        die(f"job '{job}' lf.json must be a JSON array (got {type(data).__name__})")
    records: list[dict[str, Any]] = []
    for index, row in enumerate(data):
        if not isinstance(row, dict):
            die(f"job '{job}' record #{index} is not a JSON object")
        meta = row.get("meta_info")
        if not isinstance(meta, dict):
            die(f"job '{job}' record #{index} has no meta_info mapping")
        unique = meta.get("unique_info")
        if not isinstance(unique, dict):
            die(f"job '{job}' record #{index} has no meta_info.unique_info mapping")
        instance_id = unique.get("_instance_id") or row.get("_instance_id")
        if not instance_id or not str(instance_id).strip():
            die(f"job '{job}' record #{index} has no _instance_id (cannot group by task)")
        records.append(row)
    if not records:
        print(f"WARN: job '{job}' contributes 0 records", file=sys.stderr)
    return records


def composite_of(unique: dict[str, Any]) -> float:
    """Effective composite score for ranking: composite_score, then v4, then v3; 0 if absent."""
    score = unique.get("_score") if isinstance(unique.get("_score"), dict) else {}
    for key in ("composite_score", "composite_score_v4", "composite_score_v3"):
        value = num(score.get(key))
        if value is not None:
            return value
    return 0.0


def turns_of(row: dict[str, Any], meta: dict[str, Any], unique: dict[str, Any]) -> int | None:
    score = unique.get("_score") if isinstance(unique.get("_score"), dict) else {}
    assistant_turns = num(score.get("assistant_turns"))
    if assistant_turns is not None:
        return int(assistant_turns)
    rounds = num(meta.get("rounds"))
    if rounds is not None:
        return int(rounds)
    messages = row.get("messages")
    return len(messages) if isinstance(messages, list) else None


def build_stats(selected: list[dict[str, Any]]) -> dict[str, Any]:
    """lf.stats.json with exactly the field names the dashboard reads."""
    token_lens: list[float] = []
    n_turns: list[float] = []
    scores: list[float] = []
    total_tokens = 0.0
    total_calls = 0.0
    failed_calls = 0.0
    for row, meta, unique in selected:
        score = unique.get("_score") if isinstance(unique.get("_score"), dict) else {}
        usage = unique.get("_usage") if isinstance(unique.get("_usage"), dict) else {}
        tokens = num(usage.get("total_tokens"))
        if tokens is not None:
            token_lens.append(tokens)
            total_tokens += tokens
        turns = turns_of(row, meta, unique)
        if turns is not None:
            n_turns.append(float(turns))
        scores.append(composite_of(unique))
        calls = num(score.get("total_tool_calls"))
        if calls is not None:
            total_calls += calls
            rate = num(score.get("c1_tool_success_rate"))
            if rate is not None:
                failed_calls += max(0.0, calls * (1.0 - rate))

    def dist(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"min": None, "mean": None, "max": None}
        return {
            "min": min(values),
            "mean": round(sum(values) / len(values), 2),
            "max": max(values),
        }

    return {
        "count": len(selected),
        "token_lens": dist(token_lens),
        "n_turns": dist(n_turns),
        "scores": dist(scores),
        "total_tokens": int(total_tokens),
        "tool_call_errors": {
            "total_tool_calls": int(total_calls),
            "estimated_failed_calls": round(failed_calls, 1),
            "error_rate": round(failed_calls / total_calls, 4) if total_calls > 0 else None,
        },
    }


def main() -> int:
    args = parse_args()
    if args.per_task_cap < 0:
        die("--per-task-cap must be >= 0 (0 = no cap)")

    jobs = [name.strip() for name in args.jobs.split(",")]
    if not jobs or any(not name for name in jobs):
        die("--jobs must be a non-empty comma-separated list of job names")
    duplicates = sorted({name for name in jobs if jobs.count(name) > 1})
    if duplicates:
        die(f"duplicate job name(s) in --jobs: {', '.join(duplicates)}")

    sft_root = resolve_block_path(args.sft_root)
    out_dir = resolve_block_path(args.out_dir)

    # (record, meta, unique, scaffold, composite, source_job, original_index)
    candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, float, str, int]] = []
    for job in jobs:
        job_dir = sft_root / job
        if job_dir.resolve() == out_dir.resolve():
            die(f"--out-dir must differ from job dir '{job}'")
        records = load_job(job, job_dir)
        for index, row in enumerate(records):
            meta = row["meta_info"]
            unique = meta["unique_info"]
            scaffold = scaffold_of(unique, job)
            candidates.append(
                (row, meta, unique, scaffold, composite_of(unique), job, index)
            )
    if not candidates:
        die("no records found in the given job(s)")

    # Rank within each _instance_id: composite desc, then scaffold label,
    # source job, original index — all deterministic tie-breaks.
    by_task: dict[str, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, float, str, int]]] = {}
    for candidate in candidates:
        instance_id = str(candidate[0]["meta_info"]["unique_info"].get("_instance_id")
                          or candidate[0].get("_instance_id"))
        by_task.setdefault(instance_id, []).append(candidate)

    selected: list[tuple[str, int, dict[str, Any]]] = []  # (instance_id, rank, row)
    per_task_manifest: dict[str, list[dict[str, Any]]] = {}
    for instance_id in sorted(by_task):
        ranked = sorted(
            by_task[instance_id],
            key=lambda c: (-c[4], c[3], c[5], c[6]),
        )
        keep = ranked if args.per_task_cap == 0 else ranked[: args.per_task_cap]
        for rank, candidate in enumerate(keep, start=1):
            row, _meta, _unique, scaffold, composite, source_job, _index = candidate
            selected.append((instance_id, rank, row))
            per_task_manifest.setdefault(instance_id, []).append(
                {
                    "scaffold": scaffold,
                    "composite_score": composite,
                    "source_job": source_job,
                    "rank": rank,
                }
            )

    # Deterministic output order: _instance_id, then rank.
    selected.sort(key=lambda item: (item[0], item[1]))
    lf_rows = [row for _instance_id, _rank, row in selected]

    per_scaffold_counts: dict[str, int] = {}
    for instance_id, entries in per_task_manifest.items():
        for entry in entries:
            per_scaffold_counts[entry["scaffold"]] = (
                per_scaffold_counts.get(entry["scaffold"], 0) + 1
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    lf_path = out_dir / "lf.json"
    with lf_path.open("w", encoding="utf-8") as fh:
        json.dump(lf_rows, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    sha256_lf = hashlib.sha256(lf_path.read_bytes()).hexdigest()

    with (out_dir / "lf.stats.json").open("w", encoding="utf-8") as fh:
        json.dump(
            build_stats([(row, row["meta_info"], row["meta_info"]["unique_info"]) for row in lf_rows]),
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        fh.write("\n")

    with (out_dir / "merge_manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "jobs": jobs,
                "per_task_cap": args.per_task_cap,
                "selected_count": len(lf_rows),
                "per_scaffold_counts": dict(sorted(per_scaffold_counts.items())),
                "per_task": per_task_manifest,
                "sha256_lf": sha256_lf,
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "command": shlex.join(sys.argv),
            },
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        fh.write("\n")

    print(f"Merged {len(jobs)} job(s) -> {len(lf_rows)} record(s) at {lf_path}")
    for scaffold in sorted(per_scaffold_counts):
        print(f"  {scaffold}: {per_scaffold_counts[scaffold]}")
    if args.per_task_cap > 0:
        print(f"  per-task cap: {args.per_task_cap} (ranked by composite score desc)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
