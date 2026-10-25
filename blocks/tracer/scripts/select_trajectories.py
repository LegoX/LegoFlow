#!/usr/bin/env python3
"""Best-of-n selection across sample jobs' converted SFT data.

A campaign (`runtime_info.input.harbor_job.samples_per_task >= 2`) converts
each sample job into its own `artifacts/sft_data/<job>/` directory, so the
same task can appear several times across directories. This script merges
those directories into one dataset:

- records are paired per job by `meta_info.unique_info._instance_id` (LF
  record + its IM record when `im.jsonl` exists);
- behaviorally identical records are dropped as duplicates, detected via a
  fingerprint of the ordered tool-call names plus whitespace-normalized
  assistant text — not raw JSON equality, so a re-sampled identical run with
  different metadata still dedups;
- distinct trajectories are ranked by `composite_score` (missing -> 0) and
  the top `--per-task-cap` per task are kept.

Outputs under `--out-dir`:
- `lf.json`                 selected LF records, ordered by `_instance_id` then rank
- `lf.stats.json`           aggregate stats using the converter's field names
                            (count, token_lens, n_turns, scores, total_tokens,
                            tool_call_errors) so the progress dashboard keeps
                            rendering the directory as a normal SFT dataset;
- `selection_manifest.json` provenance: jobs, per-task picks with rank and
                            source job, dropped-duplicate count, sha256 of
                            the `lf.json` bytes.

Deterministic: the same inputs produce the same selection and byte-identical
`lf.json` / `lf.stats.json` (`generated_at` in the manifest aside).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLOCK_DIR = Path(__file__).resolve().parent.parent

# Composite-score keys the dashboard's summarize_sample() accepts, in the
# order it prefers them; anything non-numeric counts as missing (rank 0).
COMPOSITE_KEYS = ("composite_score", "composite_score_v4", "composite_score_v3")

TOOL_CALLS_DOMAIN = "im"
LF_DOMAIN = "lf"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def unique_info(record: Any) -> dict[str, Any]:
    meta = record.get("meta_info") if isinstance(record, dict) else None
    info = meta.get("unique_info") if isinstance(meta, dict) else None
    return info if isinstance(info, dict) else {}


def instance_id_of(record: Any, where: str) -> str:
    # Mirrors the dashboard's summarize_sample(): _instance_id under
    # meta_info.unique_info first, a top-level _instance_id as fallback.
    info = unique_info(record)
    value = info.get("_instance_id")
    if not value and isinstance(record, dict):
        value = record.get("_instance_id")
    if not isinstance(value, str) or not value:
        fail(f"malformed record in {where}: meta_info.unique_info._instance_id is missing or empty")
    return value


def numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def composite_of(record: dict[str, Any]) -> float | None:
    score = unique_info(record).get("_score")
    if not isinstance(score, dict):
        return None
    for key in COMPOSITE_KEYS:
        value = numeric(score.get(key))
        if value is not None:
            return value
    return None


def tool_call_fingerprint(messages: Any) -> tuple[str, str]:
    """Ordered (tool-call name, normalized-arguments) pairs.

    Returns two newline-joined strings so the caller can hash them together.
    Including normalized arguments (not just names) keeps two trajectories that
    call the same tools in the same order but with different commands, file
    paths, or patches from colliding as behavioral duplicates.
    """
    names: list[str] = []
    args: list[str] = []
    if not isinstance(messages, list):
        return "", ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
          continue
        for call in calls:
          name: Any = None
          raw_args: Any = None
          if isinstance(call, dict):
            function = call.get("function")
            if isinstance(function, dict):
              name = function.get("name")
              raw_args = function.get("arguments")
            if name is None:
              name = call.get("name")
            if raw_args is None:
              raw_args = call.get("arguments")
          elif isinstance(call, str):
            name = call
          names.append(str(name) if name is not None else "<unnamed>")
          args.append(normalize_tool_arguments(raw_args))
    return "\n".join(names), "\n".join(args)


def normalize_tool_arguments(raw: Any) -> str:
    """Stable, whitespace-normalized JSON of a tool-call's arguments.

    Accepts the OpenAI shape (a JSON *string* under function.arguments) and a
    bare dict/list; unknown shapes become a sorted repr so the fingerprint is
    still deterministic. Whitespace inside strings is collapsed the same way
    the assistant-text normalizer collapses it, so reformatted-but-equal
    arguments collide while genuinely different commands do not.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
          return ""
        try:
          import json
          parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
          return " ".join(raw.split())
        return normalize_tool_arguments(parsed)
    if isinstance(raw, (dict, list)):
      try:
        import json
        text = json.dumps(raw, sort_keys=True, ensure_ascii=False)
      except (TypeError, ValueError):
        text = repr(raw)
      return " ".join(text.split())
    return " ".join(str(raw).split())


def normalized_assistant_text(messages: Any) -> str:
    """Whitespace-normalized assistant text: `a  b\\nc` and `a b c` collide."""

    def blocks(content: Any) -> list[str]:
        if isinstance(content, str):
            return [" ".join(content.split())]
        if isinstance(content, list):
            out = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    out.append(" ".join(block["text"].split()))
            return out
        return []

    parts: list[str] = []
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "assistant":
            parts.extend(blocks(message.get("content")))
    return "\n".join(parts)


def behavioral_fingerprint(domain: str, messages: Any) -> str:
    """sha256 of (tool-call names + normalized tool-call arguments + normalized
    assistant text), domain-tagged to keep IM-derived fingerprints from
    colliding with LF-derived ones (text only, used when im.jsonl is absent).
    """

    tool_names, tool_args = tool_call_fingerprint(messages)
    text = normalized_assistant_text(messages)
    tool_name_hash = hashlib.sha256(tool_names.encode("utf-8")).hexdigest()
    tool_arg_hash = hashlib.sha256(tool_args.encode("utf-8")).hexdigest()
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    blob = f"{domain}\x00{tool_name_hash}\x00{tool_arg_hash}\x00{text_hash}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_lf_records(job: str, job_dir: Path) -> list[dict[str, Any]]:
    lf_path = job_dir / "lf.json"
    if not lf_path.is_file():
        fail(f"job '{job}' has no lf.json under {job_dir} (run scripts/convert_trajectories.sh --job {job} first)")
    try:
        data = json.loads(lf_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"malformed lf.json for job '{job}': {exc}")
    if not isinstance(data, list):
        fail(f"malformed lf.json for job '{job}': expected a JSON array of records")
    records: list[dict[str, Any]] = []
    for index, record in enumerate(data):
        if not isinstance(record, dict):
            fail(f"malformed record in {job}/lf.json at index {index}: not a JSON object")
        records.append(record)
    return records


def load_im_by_instance(job: str, job_dir: Path) -> dict[str, dict[str, Any]]:
    """First IM record per _instance_id; a missing im.jsonl yields {} (callers
    then fingerprint from the LF record's own assistant messages)."""
    im_path = job_dir / "im.jsonl"
    if not im_path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        lines = im_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"malformed im.jsonl for job '{job}': {exc}")
    for line_no, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"malformed im.jsonl for job '{job}' at line {line_no}: {exc}")
        if not isinstance(record, dict):
            fail(f"malformed im.jsonl for job '{job}' at line {line_no}: not a JSON object")
        info = unique_info(record)
        instance_id = info.get("_instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            print(
                f"WARN: {job}/im.jsonl line {line_no} has no _instance_id; its LF record falls back to LF-only fingerprinting",
                file=sys.stderr,
            )
            continue
        out.setdefault(instance_id, record)
    return out


def stats_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {}
    return {
        "min": round(min(values), 4),
        "mean": round(sum(values) / len(values), 4),
        "max": round(max(values), 4),
    }


def build_stats(selected: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregates over the selected records' meta_info, using the stats field
    names the dashboard reads (progress_monitor.collect_sft)."""
    tokens: list[float] = []
    turns: list[float] = []
    scores: list[float] = []
    error_rates: list[float] = []
    for record in selected:
        info = unique_info(record)
        usage = info.get("_usage") if isinstance(info.get("_usage"), dict) else {}
        score = info.get("_score") if isinstance(info.get("_score"), dict) else {}
        meta = record.get("meta_info") if isinstance(record.get("meta_info"), dict) else {}
        token_value = numeric(usage.get("total_tokens"))
        if token_value is not None:
            tokens.append(token_value)
        turn_value = numeric(score.get("assistant_turns"))
        if turn_value is None:
            turn_value = numeric(meta.get("rounds"))
        if turn_value is None:
            messages = record.get("messages")
            turn_value = float(len(messages)) if isinstance(messages, list) else 0.0
        turns.append(turn_value)
        composite = composite_of(record)
        if composite is not None:
            scores.append(composite)
        success_rate = numeric(score.get("c1_tool_success_rate"))
        if success_rate is not None:
            error_rates.append(1.0 - success_rate)
    return {
        "count": len(selected),
        "token_lens": stats_summary(tokens),
        "n_turns": stats_summary(turns),
        "scores": stats_summary(scores),
        "total_tokens": int(sum(tokens)) if tokens else 0,
        "tool_call_errors": (
            {"error_rate": round(sum(error_rates) / len(error_rates), 4)} if error_rates else {"error_rate": None}
        ),
    }


def select(entries: list[dict[str, Any]], per_task_cap: int) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], int]:
    """Group by _instance_id, dedup by fingerprint, rank, and cap.

    Returns (selected entries ordered by (instance_id, rank), per-task manifest
    slices, dropped-duplicate count). Identical fingerprints keep the copy with
    the highest composite (ties: first job name in sort order), so the kept
    record is itself deterministic.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault(entry["instance_id"], []).append(entry)

    selected: list[dict[str, Any]] = []
    per_task: dict[str, list[dict[str, Any]]] = {}
    dropped_duplicates = 0
    for instance_id in sorted(groups):
        # Fingerprints are domain-tagged (im.jsonl vs LF-only fallback), so a
        # mixed-domain group can hold behaviorally identical copies that never
        # collide. That silently defeats dedup — warn so the operator knows the
        # selected lf.json may contain behavioral duplicates for this task.
        domains = {entry.get("fp_domain", "?") for entry in groups[instance_id]}
        if len(domains) > 1:
            jobs = ",".join(sorted({entry["source_job"] for entry in groups[instance_id]}))
            print(
                f"WARN: {instance_id}: fingerprint domains mixed ({'/'.join(sorted(domains))}) "
                f"across [{jobs}]; behavioral dedup may not apply to this task",
                file=sys.stderr,
            )
        by_fingerprint: dict[str, list[dict[str, Any]]] = {}
        for entry in groups[instance_id]:
            by_fingerprint.setdefault(entry["fingerprint"], []).append(entry)
        distinct: list[dict[str, Any]] = []
        for fingerprint in sorted(by_fingerprint):
            copies = sorted(
                by_fingerprint[fingerprint],
                key=lambda e: (-e["composite"], e["source_job"]),
            )
            distinct.append(copies[0])
            dropped_duplicates += len(copies) - 1
        distinct.sort(key=lambda e: (-e["composite"], e["fingerprint"], e["instance_id"]))
        if per_task_cap > 0:
            distinct = distinct[:per_task_cap]
        for rank, entry in enumerate(distinct, start=1):
            entry["rank"] = rank
            selected.append(entry)
            per_task.setdefault(instance_id, []).append(
                {
                    "fingerprint": entry["fingerprint"],
                    "composite_score": entry["composite"],
                    "source_job": entry["source_job"],
                    "rank": rank,
                }
            )
    return selected, per_task, dropped_duplicates


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--jobs",
        required=True,
        help="comma-separated sample job names resolved under --sft-root (e.g. <job>-s1,<job>-s2)",
    )
    parser.add_argument(
        "--sft-root",
        default="artifacts/sft_data",
        help="root the sample job directories resolve under (default: %(default)s; relative paths resolve against the block directory)",
    )
    parser.add_argument(
        "--out-dir",
        default="artifacts/sft_data/selected",
        help="output directory for lf.json, lf.stats.json, selection_manifest.json (default: %(default)s)",
    )
    parser.add_argument(
        "--per-task-cap",
        type=int,
        default=2,
        help="keep at most this many distinct trajectories per task; 0 keeps all (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.per_task_cap < 0:
        parser.error("--per-task-cap must be >= 0 (0 = unlimited)")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    sft_root = Path(args.sft_root)
    if not sft_root.is_absolute():
        sft_root = BLOCK_DIR / sft_root
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = BLOCK_DIR / out_dir

    jobs = [name.strip() for name in args.jobs.split(",") if name.strip()]
    if not jobs:
        fail("--jobs is empty; pass comma-separated sample job names")
    missing = [job for job in jobs if not (sft_root / job).is_dir()]
    if missing:
        fail(f"no jobs found under {sft_root}: {', '.join(missing)}")

    entries: list[dict[str, Any]] = []
    for job in jobs:
        job_dir = sft_root / job
        lf_records = load_lf_records(job, job_dir)
        im_by_instance = load_im_by_instance(job, job_dir)
        for index, record in enumerate(lf_records):
            where = f"{job}/lf.json:{index}"
            instance_id = instance_id_of(record, where)
            im_record = im_by_instance.get(instance_id)
            if im_record is not None:
                fp_domain = TOOL_CALLS_DOMAIN
                fingerprint = behavioral_fingerprint(TOOL_CALLS_DOMAIN, im_record.get("messages"))
            else:
                fp_domain = LF_DOMAIN
                fingerprint = behavioral_fingerprint(LF_DOMAIN, record.get("messages"))
            composite = composite_of(record)
            entries.append(
                {
                    "instance_id": instance_id,
                    "fingerprint": fingerprint,
                    "fp_domain": fp_domain,
                    "composite": composite if composite is not None else 0.0,
                    "source_job": job,
                    "record": record,
                }
            )

    selected, per_task, dropped_duplicates = select(entries, args.per_task_cap)
    if not selected:
        fail(f"zero records selected from {', '.join(jobs)} (no lf.json records survived)")

    out_dir.mkdir(parents=True, exist_ok=True)
    lf_path = out_dir / "lf.json"
    lf_payload = [entry["record"] for entry in selected]
    lf_path.write_text(json.dumps(lf_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sha256_lf = hashlib.sha256(lf_path.read_bytes()).hexdigest()

    stats_path = out_dir / "lf.stats.json"
    stats_path.write_text(json.dumps(build_stats(lf_payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "jobs": jobs,
        "per_task_cap": args.per_task_cap,
        "selected_count": len(selected),
        "dropped_duplicates": dropped_duplicates,
        "per_task": per_task,
        "sha256_lf": sha256_lf,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": " ".join(sys.argv),
    }
    manifest_path = out_dir / "selection_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Selected {len(selected)} record(s) across {len(per_task)} task(s) from {len(jobs)} job(s)")
    print(f"Dropped duplicates: {dropped_duplicates}")
    print(f"LF file:     {lf_path}")
    print(f"Stats file:  {stats_path}")
    print(f"Manifest:    {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
