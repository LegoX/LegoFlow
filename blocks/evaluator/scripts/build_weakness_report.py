#!/usr/bin/env python3
"""Build the RSI-loop weakness report (schema legoflow.weakness.v1).

Joins the k repeat eval job dirs of
one (checkpoint, dataset) round into the single artifact that may feed curator
targeting — after the signal firewall has vetted it (normative: the output dict
is run through signal_guard BEFORE anything is written; a violation aborts the
script with no side effects).

Formulas (fixed, shared with curator's apply_targeting.py):
  pooled pass_rate      resolved / n over tasks*repeats (n = valid trials)
  ci95_wilson           Wilson score interval, z = 1.96
  per-repeat rate r_i   mean reward within the tag at repeat i (present tasks only)
  between_repeat_sd     sd(r_1..r_k, ddof=1); 0.0 when k == 1
                         - per tag AND globally (over all non-excluded tasks)
                         - repeats with no usable trial rewards are excluded
                         (never fabricated as 0.0) and listed in
                         noise.empty_repeats
  delta_vs_prev         this round's pooled tag pass_rate minus the same tag's
                        pass_rate in the newest history copy (null in round 1)
  learning_progress     delta_vs_prev / noise_floor, where
                        noise_floor = max(tag_sd, global_sd, 1/n)
                        (progress measured in units of measurable noise; 1/n is
                        the one-instance resolution floor so a zero-SD cell
                        cannot produce an infinite score). Null when delta is.
  failure_mix           distribution of instances.jsonl `primary_failure` over
                        the tag's instances — ONLINE-ELIGIBLE fields only; the
                        gold-aware C1-C4 / hunk / judge fields present in the
                        same records are never read.

Tag source per task (join priority): gold task.toml [metadata]
tags = [language, area, topic, bug_class] + difficulty, with difficulty_tier
overridden by the job's analysis/task_instances.jsonl join when present; a task
with no gold entry degrades to the language-only fallback (language:unknown).

Usage:
  python3 build_weakness_report.py \
      --jobs <job_dir_r1> <job_dir_r2> <job_dir_r3> --dataset swebench-verified \
      --audit-slice artifacts/reserve/audit_slice.txt \
      --out artifacts/weakness/weakness_report.json \
      --history-dir artifacts/weakness/history \
      --response-matrix artifacts/response_matrix.jsonl \
      --ledger artifacts/loop/ledger.yaml \
      [--min-tag-n 20] [--placebo SEED] [--dataset-dir <gold dir>] \
      [--receipt <trainer round_receipt.json>] [--prev-directives <yaml>]

Round bookkeeping: the round number is history-dir-owned (max existing
weakness_report_NNN.json + 1; 1 when empty). The history copy is immutable —
written once, never overwritten — and re-invoking the builder over jobs that
ANY history round already measured (same checkpoint_sha + eval_jobs + dataset)
is refused (no phantom rounds); to
re-measure a round, move its history copy aside deliberately AND prune that
round's response-matrix and ledger rows. --placebo SEED deterministically
permutes delta_vs_prev across tags and re-derives learning_progress and
directives_blocked from the permuted deltas (so the placebo report satisfies
every invariant of a real one), leaves the report itself unmarked (blind),
and registers the permutation only in <history-dir>/placebo_rounds.yaml plus
the ledger row's placebo flag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
BLOCK_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import signal_guard  # noqa: E402  (same-dir module; scripts/ is not a package)

SCHEMA = "legoflow.weakness.v1"
TAGGER_VERSION = "gold_toml_tags+analysis_join_v1"
WILSON_Z = 1.96
TAG_DIMENSIONS = ("language", "area", "topic", "bug_class", "difficulty_tier")
POLICY_PATH = BLOCK_DIR / "signal_policy.yaml"
HISTORY_RE = re.compile(r"^weakness_report_(\d+)\.json$")


# ---------------------------------------------------------------------------
# Small stats helpers
# ---------------------------------------------------------------------------
def wilson_ci(resolved: int, n: int, z: float = WILSON_Z) -> list[float]:
    """Wilson score interval for a binomial proportion, rounded to 6 decimals."""
    if n <= 0:
        return [0.0, 0.0]
    p = resolved / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2 * n)
    margin = z * ((p * (1 - p) / n) + z2 / (4 * n * n)) ** 0.5
    return [round(max(0.0, (centre - margin) / denom), 6),
            round(min(1.0, (centre + margin) / denom), 6)]


def sd(values: list[float], ddof: int = 1) -> float:
    """Sample SD; 0.0 when there are fewer than ddof+1 values (k==1 repeats)."""
    if len(values) <= ddof:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - ddof)
    return round(var ** 0.5, 6)


def sha256_ids(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def load_audit_slice(path: Path) -> list[str]:
    """Read audit_slice.txt; a missing file is a warn + no exclusion (contract)."""
    if not path.is_file():
        print(f"WARNING: audit slice not found at {path}; no audit exclusion applied.",
              file=sys.stderr)
        return []
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        # Test the stripped line: an indented comment must not become an id.
        if stripped and not stripped.startswith("#"):
            ids.append(stripped)
    return sorted(set(ids))


def iter_trials(job_dir: Path):
    """Yield per-trial records from <job>/<trial>/result.json.

    Joined by result.json `task_name` — never by the 32-char trial-dir prefix,
    which is task_name[:32] plus a random suffix and is not unique per task.
    The job-level aggregate <job>/result.json is skipped.
    """
    for path in sorted(job_dir.glob("*/result.json")):
        if path.parent == job_dir:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: unreadable trial result {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict) or "task_name" not in data:
            continue
        verifier = data.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        raw_reward = rewards.get("reward")
        try:
            reward = None if raw_reward is None else float(raw_reward)
        except (TypeError, ValueError):
            reward = None
        agent = data.get("agent_result") or {}
        n_in, n_out = agent.get("n_input_tokens"), agent.get("n_output_tokens")
        tokens = None if (n_in is None and n_out is None) else int(n_in or 0) + int(n_out or 0)
        exceptions = []
        exc = data.get("exception_info") or {}
        if exc.get("exception_type"):
            exceptions.append(str(exc["exception_type"]))
        if reward is None and not exceptions:
            exceptions.append("no_verifier_result")
        yield {
            "task_name": str(data["task_name"]),
            "reward": reward,
            "tokens": tokens,
            "exceptions": exceptions,
        }


def load_analysis(job_dir: Path) -> dict:
    """Load the job's analysis/ join tables (instances.jsonl, task_instances.jsonl)."""
    out: dict = {"instances": {}, "task_features": {}, "scaffold": None}
    instances_path = job_dir / "analysis" / "instances.jsonl"
    if instances_path.is_file():
        for line in instances_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = str(rec.get("instance_id") or "")
            if not iid:
                continue
            out["instances"][iid] = rec
            out["instances"].setdefault(iid.lower(), rec)
            if out["scaffold"] is None and rec.get("scaffold"):
                out["scaffold"] = str(rec["scaffold"])
    features_path = job_dir / "analysis" / "task_instances.jsonl"
    if features_path.is_file():
        for line in features_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            iid = str(rec.get("instance_id") or "")
            if iid:
                out["task_features"][iid] = rec
                out["task_features"].setdefault(iid.lower(), rec)
    return out


def job_scaffold(job_dir: Path, analysis: dict) -> str:
    """Scaffold label: analysis instances.jsonl, else the job config's agent name."""
    if analysis.get("scaffold"):
        return analysis["scaffold"]
    config_path = job_dir / "config.yaml"
    if config_path.is_file():
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            agent = (((cfg.get("runtime_info") or {}).get("input") or {})
                     .get("agent") or {}).get("name")
            if agent:
                return str(agent)
        except yaml.YAMLError:
            pass
    return "unknown"


def load_checkpoint_ref(job_dirs: list[Path],
                        receipt_path: Path | None = None) -> tuple[str, str]:
    """Return (checkpoint_ref, checkpoint_sha).

    checkpoint_sha comes from the trainer round receipt (contract 8) when
    present, else 'unknown'. The receipt is probed at --receipt FIRST (the
    trainer writes round_receipt.json next to the served checkpoint and
    nothing copies it into the job dirs automatically), then from each job
    dir's own <job>/round_receipt.json copy; disagreeing receipts warn and
    keep the first — a mismatch mid-round is a runbook error, not a reason
    to silently drop the provenance. checkpoint_ref is the human-readable
    identity (the trainer checkpoint path recorded in the job config, else
    round + short sha, else 'unknown').
    """
    ref, sha = "unknown", "unknown"
    seen: dict[str, str] = {}
    probes: list[Path] = []
    if receipt_path is not None:
        probes.append(Path(receipt_path))
    probes.extend(job_dir / "round_receipt.json" for job_dir in job_dirs)
    for probe in probes:
        if not probe.is_file():
            if receipt_path is not None and probe == Path(receipt_path):
                print(f"WARNING: --receipt {probe} not found; falling back to "
                      "per-job round_receipt.json copies.", file=sys.stderr)
            continue
        try:
            receipt = json.loads(probe.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: unreadable round receipt {probe}: {exc}",
                  file=sys.stderr)
            continue
        if isinstance(receipt, dict):
            r_sha = str(receipt.get("checkpoint_sha256") or "unknown")
            r_round = receipt.get("round")
            key = f"round{r_round}:{r_sha[:12]}"
            seen.setdefault(key, r_sha)
            if len(seen) > 1:
                print(f"WARNING: round receipts disagree across jobs: "
                      f"{sorted(seen)}; keeping the first.", file=sys.stderr)
    for job_dir in job_dirs:
        if ref == "unknown":
            config_path = job_dir / "config.yaml"
            if config_path.is_file():
                try:
                    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                    ckpt = ((((cfg.get("runtime_info") or {}).get("input") or {})
                             .get("local_model_serving") or {})
                            .get("from_trainer_checkpoint") or "")
                    if ckpt:
                        ref = str(ckpt)
                except yaml.YAMLError:
                    pass
    if seen:
        first_key = next(iter(seen))
        if ref == "unknown":
            ref = first_key
        sha = seen[first_key]
    if sha == "unknown":
        print("WARNING: this round's checkpoint identity is 'unknown' — no round "
              "receipt found (the trainer writes round_receipt.json next to the "
              "served checkpoint; nothing copies it into the job dirs). Pass "
              "--receipt <path> or copy the receipt into a job dir, or the "
              "report, response-matrix, and ledger rows cannot be joined to the "
              "training round by sha.", file=sys.stderr)
    return ref, sha


def load_gold_tags(dataset_dir: Path) -> dict:
    """Map task_id -> {language, area, topic, bug_class, difficulty} from gold task.toml."""
    tags: dict = {}
    if not dataset_dir.is_dir():
        print(f"WARNING: gold dataset dir not found at {dataset_dir}; "
              "tags degrade to the language-only fallback.", file=sys.stderr)
        return tags
    for task_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
        toml_path = task_dir / "task.toml"
        if not toml_path.is_file():
            continue
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            print(f"WARNING: unparsable {toml_path}: {exc}", file=sys.stderr)
            continue
        metadata = data.get("metadata") if isinstance(data, dict) else None
        if not isinstance(metadata, dict):
            continue
        raw = [str(t).strip().lower() for t in (metadata.get("tags") or []) if str(t).strip()]
        entry = {}
        if len(raw) >= 4:
            entry = {"language": raw[0], "area": raw[1], "topic": raw[2],
                     "bug_class": raw[3]}
        elif len(raw) == 3:
            entry = {"language": raw[0], "area": raw[1], "topic": raw[2]}
        elif raw:
            entry = {"language": raw[0]}
        if metadata.get("difficulty"):
            entry["difficulty"] = str(metadata["difficulty"]).strip().lower()
        if entry:
            tags[task_dir.name] = entry
            tags.setdefault(task_dir.name.lower(), entry)
    return tags


def tag_dims_for_task(task_name: str, gold_tags: dict,
                      analysis_features: dict | None) -> dict:
    """Resolve the tag dimensions for one task (join priority, see module docstring)."""
    gold = gold_tags.get(task_name) or gold_tags.get(task_name.lower()) or {}
    feats = analysis_features or {}
    dims: dict = {}
    for dim in ("language", "area", "topic", "bug_class"):
        value = gold.get(dim)
        if value:
            dims[dim] = value
    if not dims:
        dims["language"] = "unknown"  # language-only fallback
    tier = feats.get("difficulty_tier") or gold.get("difficulty")
    if tier:
        dims["difficulty_tier"] = str(tier).strip().lower()
    return dims


# ---------------------------------------------------------------------------
# History (immutable) bookkeeping
# ---------------------------------------------------------------------------
def history_rounds(history_dir: Path) -> list[int]:
    if not history_dir.is_dir():
        return []
    return sorted(int(m.group(1)) for m in
                  (HISTORY_RE.match(p.name) for p in history_dir.iterdir()) if m)


def load_prev_report(history_dir: Path) -> tuple[int, dict | None]:
    """Newest history copy -> (its round, its parsed tags' pass rates)."""
    rounds = history_rounds(history_dir)
    if not rounds:
        return 0, None
    prev_round = rounds[-1]
    path = history_dir / f"weakness_report_{prev_round:03d}.json"
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: unreadable history copy {path}: {exc}; "
              "delta_vs_prev will be null.", file=sys.stderr)
        return prev_round, None
    return prev_round, prev


def find_measured_round(history_dir: Path, checkpoint_sha: str,
                        eval_jobs: list[str], dataset: str) -> int | None:
    """Round whose history copy already measured this (checkpoint, jobs, dataset).

    Scans EVERY history copy, oldest first — comparing only against the newest
    would let a re-measure of an older round mint a phantom round: a fresh
    history file, duplicate response-matrix rows under a new round number, and
    a third ledger row, all with exit 0. Unreadable copies warn and are skipped
    (same policy as load_prev_report).
    """
    for round_no in history_rounds(history_dir):
        path = history_dir / f"weakness_report_{round_no:03d}.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: unreadable history copy {path}: {exc}; cannot check "
                  "it for a prior measurement of these jobs.", file=sys.stderr)
            continue
        if (isinstance(report, dict)
                and report.get("checkpoint_sha") == checkpoint_sha
                and report.get("eval_jobs") == eval_jobs
                and report.get("dataset") == dataset):
            return round_no
    return None


def append_yaml_list(path: Path, row: dict) -> None:
    """Append one row to a YAML list file, preserving all prior rows.

    Rows are re-serialized by yaml.safe_dump (content-preserving, not
    byte-verbatim) and written through a tmp+replace so a crash mid-write can
    never truncate the ledger (same pattern as apply_targeting.save_ledger).
    """
    rows: list = []
    if path.exists():
        try:
            existing = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SystemExit(f"ERROR: unparseable {path}: {exc}")
        if existing is not None:
            if not isinstance(existing, list):
                raise SystemExit(
                    f"ERROR: {path} is not a YAML list; refusing to clobber it.")
            rows = list(existing)
    rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(rows, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


MATRIX_KEY_FIELDS = ("round", "task_id", "repeat_idx", "scaffold")
MATRIX_CONTENT_FIELDS = ("checkpoint_sha", "reward", "tokens")


def filter_matrix_rows(matrix_path: Path, matrix_rows: list[dict]) -> list[dict]:
    """Idempotent response-matrix append guard.

    Rows whose (round, task_id, repeat_idx, scaffold) key is already present
    with identical checkpoint/reward/tokens content are skipped (a re-measure
    after the history copy was moved aside must not double-append); a
    conflicting row for the same key refuses the run — re-measuring a round
    also means pruning that round's matrix rows.
    """
    existing: dict[tuple, dict] = {}
    if matrix_path.exists():
        for line_no, line in enumerate(
                matrix_path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"ERROR: unparseable response matrix "
                                 f"{matrix_path} (line {line_no}): {exc}")
            if isinstance(row, dict):
                existing[tuple(row.get(f) for f in MATRIX_KEY_FIELDS)] = row
    append_rows = []
    for row in matrix_rows:
        prior = existing.get(tuple(row.get(f) for f in MATRIX_KEY_FIELDS))
        if prior is None:
            append_rows.append(row)
        elif any(prior.get(f) != row.get(f) for f in MATRIX_CONTENT_FIELDS):
            raise SystemExit(
                f"ERROR: response matrix {matrix_path} already holds a different "
                f"measurement for round {row['round']} task {row['task_id']} "
                f"(repeat_idx {row['repeat_idx']}, scaffold {row['scaffold']}). "
                f"Re-measuring a round requires pruning that round's "
                f"response-matrix and ledger rows.")
    return append_rows


def ledger_row_appendable(ledger_path: Path, row: dict) -> bool:
    """False when the ledger already holds an identical row for this round
    (idempotent skip); refuse when it holds a different row for the round."""
    if not ledger_path.exists():
        return True
    try:
        rows = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SystemExit(f"ERROR: unparseable {ledger_path}: {exc}")
    if rows is None:
        return True
    if not isinstance(rows, list):
        raise SystemExit(
            f"ERROR: {ledger_path} is not a YAML list; refusing to clobber it.")
    for prior in rows:
        if isinstance(prior, dict) and prior.get("round") == row["round"]:
            if prior == row:
                return False
            raise SystemExit(
                f"ERROR: ledger {ledger_path} already holds a different row for "
                f"round {row['round']}. Re-measuring a round requires pruning "
                f"that round's response-matrix and ledger rows.")
    return True


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def derive_blocked(tags: list[dict], global_sd: float, min_tag_n: int) -> list[dict]:
    """directives_blocked derivation shared by the real and placebo paths.

    Too-small cells are always blocked; sub-noise deltas additionally. The
    report-level gate is 1x max(tag_sd, global_sd) with a strict '<' —
    deliberately looser than the curator actuation gate (apply_targeting.py
    applies noise_gate_multiplier, default 2.0, times max(sd, global_sd, 0)):
    the report blocks what cannot be honestly *measured*, the curator blocks
    what is not worth *acting on*.
    """
    blocked = []
    for tag in tags:
        if tag["n"] < min_tag_n:
            blocked.append({"key": tag["key"], "reason": "n_too_small"})
        elif (tag["delta_vs_prev"] is not None
              and abs(tag["delta_vs_prev"]) < max(tag["tag_sd"], global_sd)):
            blocked.append({"key": tag["key"], "reason": "sub_noise_delta"})
    return blocked


def build_report(args) -> tuple[dict, list[dict], dict]:
    jobs = [Path(p) for p in args.jobs]
    for job_dir in jobs:
        if not job_dir.is_dir():
            raise SystemExit(f"ERROR: job dir not found: {job_dir}")
    k = len(jobs)
    if k == 0:
        raise SystemExit("ERROR: --jobs requires at least one job dir")

    audit_ids = load_audit_slice(Path(args.audit_slice))
    audit_excluded = set(audit_ids)

    if getattr(args, "dataset_dir", None):
        dataset_dir = Path(args.dataset_dir)
    else:
        base = args.dataset[:-4] if args.dataset.endswith("-100") else args.dataset
        if base.endswith("-nohack"):
            base = base[: -len("-nohack")]
        dataset_dir = BLOCK_DIR / "artifacts" / "datasets" / base
    gold_tags = load_gold_tags(dataset_dir)

    # Per-repeat rewards + analysis join tables.
    rewards: list[dict] = []           # rewards[i][task] = reward | None
    tokens_by: list[dict] = []         # tokens_by[i][task] = int | None
    exceptions_by: dict = {}           # task -> set(exception kinds)
    analyses = [load_analysis(job) for job in jobs]
    scaffolds = [job_scaffold(job, an) for job, an in zip(jobs, analyses)]
    for idx, job_dir in enumerate(jobs):
        per_task: dict = {}
        per_tokens: dict = {}
        for trial in iter_trials(job_dir):
            task = trial["task_name"]
            if task in audit_excluded:
                continue
            per_task.setdefault(task, trial["reward"])
            per_tokens.setdefault(task, trial["tokens"])
            if trial["exceptions"]:
                exceptions_by.setdefault(task, set()).update(trial["exceptions"])
        rewards.append(per_task)
        tokens_by.append(per_tokens)

    all_tasks = sorted({t for per in rewards for t in per})
    if not all_tasks:
        raise SystemExit("ERROR: no per-trial result.json task_name records found "
                         "(nothing survived the audit-slice exclusion)")
    missing = [i for i, per in enumerate(rewards)
               if set(per) != set(rewards[0])]
    if missing:
        print(f"WARNING: repeat job dirs disagree on task sets (indices {missing}); "
              "per-repeat rates use only the tasks present in that repeat.",
              file=sys.stderr)

    checkpoint_ref, checkpoint_sha = load_checkpoint_ref(
        jobs, getattr(args, "receipt", None))

    # Round numbering + previous-round tag rates.
    history_dir = Path(args.history_dir)
    rounds_seen = history_rounds(history_dir)
    round_no = rounds_seen[-1] + 1 if rounds_seen else 1
    # Refuse a duplicate measurement of ANY history round, not just the newest:
    # re-measuring an older round would otherwise mint a phantom next round
    # (a fresh history copy plus duplicate response-matrix / ledger rows, exit
    # 0). Mirrors the history-immutability refusal in main().
    measured_round = find_measured_round(history_dir, checkpoint_sha,
                                         [job.name for job in jobs], args.dataset)
    if measured_round is not None:
        raise SystemExit(
            f"ERROR: these jobs are already measured as history round "
            f"{measured_round:03d} (same checkpoint_sha + eval_jobs + dataset); "
            f"refusing to mint a phantom round. History is written once per "
            f"round — to re-measure, move weakness_report_{measured_round:03d}.json "
            f"aside deliberately AND prune that round's response-matrix and "
            f"ledger rows.")
    prev_round, prev_report = load_prev_report(history_dir)
    prev_rates: dict = {}
    if prev_report:
        if prev_round:
            print(f"delta baseline: history round {prev_round:03d}", file=sys.stderr)
        for tag in prev_report.get("tags") or []:
            if tag.get("key") and tag.get("pass_rate") is not None:
                prev_rates[str(tag["key"])] = float(tag["pass_rate"])

    # Tag cells: dimension:value -> set of tasks.
    cells: dict = {}
    for task in all_tasks:
        feats = None
        for analysis in analyses:
            feats = feats or analysis["task_features"].get(task) \
                or analysis["task_features"].get(task.lower())
        dims = tag_dims_for_task(task, gold_tags, feats)
        for dim, value in dims.items():
            if value:
                cells.setdefault(f"{dim}:{value}", set()).add(task)

    # Global per-repeat rates (pooled across all non-excluded tasks). Repeats
    # with no usable trial rewards (empty job dir, unreadable JSON, or every
    # trial audit-excluded) are excluded, never fabricated as a 0.0 rate — a
    # phantom 0.0 would inflate between_repeat_sd and every tag's noise floor
    # (mirrors the per-tag path's `if vals:` guard below).
    empty_repeats = [i for i, per in enumerate(rewards)
                     if not any(r is not None for r in per.values())]
    global_rates = []
    for i, per in enumerate(rewards):
        vals = [r for r in per.values() if r is not None]
        if vals:
            global_rates.append(round(sum(vals) / len(vals), 6))
        else:
            print(f"WARNING: repeat job index {i} contributed no usable trial rewards; "
                  "excluded from between_repeat_sd (a fabricated 0.0 rate would "
                  "inflate every noise floor).", file=sys.stderr)
    global_sd = sd(global_rates)
    global_resolved = sum(1 for per in rewards for r in per.values() if r == 1.0)
    global_n = sum(1 for per in rewards for r in per.values() if r is not None)
    internal_resolve = round(global_resolved / global_n, 6) if global_n else 0.0

    # failure_mix input: online-eligible fields of instances.jsonl only.
    # primary_failure is the sole field read here by design (see docstring);
    # the records' gold-aware C1-C4/hunk/judge fields are never touched.
    lower_tasks = {t.lower(): t for t in all_tasks}
    failure_records: dict = {}  # canonical task -> analysis instance record
    for analysis in analyses:
        for iid, rec in analysis["instances"].items():
            task = lower_tasks.get(iid.lower())
            if task is not None and task not in failure_records:
                failure_records[task] = rec

    tags_out = []
    per_tag_sd: dict = {}
    for key in sorted(cells):
        tasks = cells[key]
        per_repeat_rates = []
        resolved = 0
        n = 0
        for per in rewards:
            vals = [per[t] for t in tasks if t in per and per[t] is not None]
            if vals:
                per_repeat_rates.append(round(sum(vals) / len(vals), 6))
                resolved += sum(1 for v in vals if v == 1.0)
                n += len(vals)
        if n == 0:
            continue
        pass_rate = round(resolved / n, 6)
        tag_sd = sd(per_repeat_rates)
        per_tag_sd[key] = tag_sd
        delta = None
        lp = None
        if key in prev_rates:
            delta = round(pass_rate - prev_rates[key], 6)
            noise_floor = max(tag_sd, global_sd, 1.0 / n)
            lp = round(delta / noise_floor, 6)
        mix: dict = {}
        mix_counts: dict = {}
        mix_total = 0
        for t in tasks:
            rec = failure_records.get(t)
            if rec is None:
                continue
            label = str(rec.get("primary_failure") or "unknown")
            mix_counts[label] = mix_counts.get(label, 0) + 1
            mix_total += 1
        if mix_total:
            mix = {label: round(cnt / mix_total, 6)
                   for label, cnt in sorted(mix_counts.items())}
        tags_out.append({
            "key": key,
            "n": n,
            "resolved": resolved,
            "pass_rate": pass_rate,
            "ci95_wilson": wilson_ci(resolved, n),
            "per_repeat": per_repeat_rates,
            "tag_sd": tag_sd,
            "noise_floor": round(max(tag_sd, global_sd, 1.0 / n), 6),
            "delta_vs_prev": delta,
            "learning_progress": lp,
            "failure_mix": mix,
        })

    # directives_blocked: too-small cells always; sub-noise deltas additionally.
    blocked = derive_blocked(tags_out, global_sd, args.min_tag_n)

    tasks_out = {}
    for task in all_tasks:
        seq = [per.get(task) for per in rewards]
        vals = [r for r in seq if r is not None]
        tasks_out[task] = {
            "reward_mean": round(sum(vals) / len(vals), 6) if vals else None,
            "per_repeat": seq,
            "exceptions": sorted(exceptions_by.get(task, [])),
        }

    report = {
        "schema": SCHEMA,
        "round": round_no,
        "eval_jobs": [job.name for job in jobs],
        "checkpoint_ref": checkpoint_ref,
        "checkpoint_sha": checkpoint_sha,
        "generated_at": now_iso(),
        "dataset": args.dataset,
        "k": k,
        "noise": {
            "k": k,
            "between_repeat_sd": global_sd,
            "per_tag_sd": per_tag_sd,
            # Which joined job dirs contributed no usable rewards (k still
            # documents how many job dirs were joined).
            "empty_repeats": empty_repeats,
        },
        "audit_excluded_ids_sha256": sha256_ids(audit_ids),
        "tagger_version": TAGGER_VERSION,
        "tags": tags_out,
        "tasks": tasks_out,
        "directives_blocked": blocked,
    }

    # Response matrix rows (audit-slice tasks excluded from every loop product).
    matrix_rows = []
    for idx, per in enumerate(rewards):
        for task in sorted(per):
            if task in audit_excluded:
                continue
            matrix_rows.append({
                "round": round_no,
                "checkpoint_sha": checkpoint_sha,
                "task_id": task,
                "scaffold": scaffolds[idx],
                "repeat_idx": idx,
                "reward": per[task],
                "tokens": tokens_by[idx].get(task),
            })

    # Provenance for which directives this round acted under: sha256 of the
    # prior round's directives file (supplied by the runbook from
    # hypothesis_ledger.directives_file). Absent -> null (the field stays a
    # documented part of the row either way).
    directives_sha = None
    if getattr(args, "prev_directives", None):
        prev_directives_path = Path(args.prev_directives)
        try:
            directives_sha = hashlib.sha256(
                prev_directives_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise SystemExit(
                f"ERROR: cannot read --prev-directives {prev_directives_path}: {exc}")

    ledger_row = {
        "round": round_no,
        "eval_jobs": [job.name for job in jobs],
        "checkpoint_ref": checkpoint_ref,
        "internal_resolve": internal_resolve,
        "between_repeat_sd": global_sd,
        "placebo": bool(args.placebo is not None),
        "directives_sha256": directives_sha,
        "verdict": None,
    }
    return report, matrix_rows, ledger_row


def apply_placebo(report: dict, seed: int, min_tag_n: int) -> dict:
    """Deterministically permute delta_vs_prev across tags and re-derive.

    Only delta_vs_prev moves; learning_progress and directives_blocked are
    re-derived from the permuted deltas against each destination tag's own
    noise floor, so the placebo report satisfies every invariant of a real
    one (learning_progress == delta / max(tag_sd, global_sd, 1/n), and
    directives_blocked follows the permuted deltas). A reviewer holding only
    the report cannot detect — let alone reconstruct — the permutation.
    The report stays schema-identical and unmarked (blind); the permutation
    is registered only in <history-dir>/placebo_rounds.yaml.
    """
    tags = report["tags"]
    global_sd = report["noise"]["between_repeat_sd"]
    deltas = [t["delta_vs_prev"] for t in tags]
    order = list(range(len(tags)))
    random.Random(seed).shuffle(order)
    swap = {}
    for idx, tag in enumerate(tags):
        source = tags[order[idx]]
        delta = deltas[order[idx]]
        tag["delta_vs_prev"] = delta
        # Same formula as build_report (tag_sd/global_sd are the stored 6-dp
        # values, so this is bit-identical to a real report's derivation).
        tag["learning_progress"] = None if delta is None else round(
            delta / max(tag["tag_sd"], global_sd, 1.0 / tag["n"]), 6)
        swap[tag["key"]] = source["key"]
    report["directives_blocked"] = derive_blocked(tags, global_sd, min_tag_n)
    return swap


def guard_payloads(report: dict, matrix_rows: list[dict], ledger_row: dict) -> None:
    """Normative fix #8: nothing is written until the firewall passes."""
    try:
        policy = signal_guard.load_policy(POLICY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"ERROR: {exc}")
    for label, payload in (("report", report), ("response_matrix", matrix_rows),
                           ("ledger", ledger_row)):
        violations, _warnings = signal_guard.check_payload(
            payload, policy=policy, context="online")
        if violations:
            print(f"ERROR: signal firewall refused the {label} "
                  f"({len(violations)} offline/eval-only field(s)):", file=sys.stderr)
            for line in signal_guard.format_findings(violations):
                print(line, file=sys.stderr)
            raise SystemExit(3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_weakness_report",
        description="Build the legoflow.weakness.v1 RSI-loop weakness report from "
                    "k repeat eval job dirs.",
        epilog="Receipt probe note: when training writes no checkpoint-* subdir, "
               "the receipt is looked up one directory above the run dir and "
               "checkpoint identity may degrade to 'unknown'.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", nargs="+", required=True, metavar="DIR",
                        help="k repeat job dirs of one round, in repeat order")
    parser.add_argument("--dataset", required=True,
                        help="dataset base name (gold dir under artifacts/datasets/)")
    parser.add_argument("--audit-slice", required=True, metavar="PATH",
                        help="audit_slice.txt; a missing file warns and skips exclusion")
    parser.add_argument("--out", required=True, metavar="PATH",
                        help="weakness_report.json path (live copy; overwritten)")
    parser.add_argument("--history-dir", required=True, metavar="DIR",
                        help="immutable weakness_report_NNN.json history dir")
    parser.add_argument("--response-matrix", required=True, metavar="PATH",
                        help="response_matrix.jsonl to append to")
    parser.add_argument("--ledger", required=True, metavar="PATH",
                        help="loop ledger.yaml to append one measurement row to")
    parser.add_argument("--min-tag-n", type=int, default=20,
                        help="minimum pooled n per tag cell (default: 20)")
    parser.add_argument("--placebo", type=int, default=None, metavar="SEED",
                        help="permute tag deltas deterministically, re-deriving "
                             "learning_progress/directives_blocked (blind)")
    parser.add_argument("--dataset-dir", default=None, metavar="PATH",
                        help="gold dataset dir override (default: "
                             "artifacts/datasets/<base>)")
    parser.add_argument("--receipt", default=None, metavar="PATH",
                        help="trainer round_receipt.json for this round (probed "
                             "before per-job <job>/round_receipt.json copies)")
    parser.add_argument("--prev-directives", default=None, metavar="PATH",
                        help="prior round's directives YAML; its sha256 is "
                             "stamped into the ledger row")
    args = parser.parse_args(argv)

    if args.min_tag_n < 1:
        raise SystemExit("ERROR: --min-tag-n must be >= 1")

    # Gold dataset dir derivation mirrors prepare_dataset.sh/analyze_job.sh
    # (-100 subset and -nohack suffixes map to the base adapter output); see
    # build_report() for the actual resolution.
    report, matrix_rows, ledger_row = build_report(args)

    swap = None
    if args.placebo is not None:
        swap = apply_placebo(report, args.placebo, args.min_tag_n)

    # Firewall first: no file is touched unless every payload is clean.
    guard_payloads(report, matrix_rows, ledger_row)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    # Five-artifact commit, ordered fail-safe: every refusal below happens
    # BEFORE any write; the immutable history copy is validated and written
    # BEFORE the live --out copy (an aborted round must never become the live
    # report apply_targeting.py consumes); and the placebo registration
    # precedes both — a registered-but-unwritten placebo is detectable, a
    # written-but-unregistered one is not.
    history_dir = Path(args.history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"weakness_report_{report['round']:03d}.json"
    if history_path.exists():
        if history_path.read_text(encoding="utf-8") == payload:
            print(f"history copy {history_path.name} already recorded identically; "
                  "leaving it untouched.")
        else:
            raise SystemExit(
                f"ERROR: history copies are immutable and {history_path} already "
                f"exists with different content. History is written once per "
                f"round; to re-measure a round, move the stale copy aside "
                f"deliberately AND prune that round's rows from the response "
                f"matrix and the ledger.")

    # Re-measure guard: decide the appends (idempotent skip vs refusal) before
    # anything is written, so a refused re-measure leaves every artifact intact.
    matrix_path = Path(args.response_matrix)
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    append_matrix_rows = filter_matrix_rows(matrix_path, matrix_rows)
    ledger_path = Path(args.ledger)
    append_ledger = ledger_row_appendable(ledger_path, ledger_row)

    if swap is not None:
        append_yaml_list(history_dir / "placebo_rounds.yaml", {
            "round": report["round"],
            "seed": args.placebo,
            "registered_at": now_iso(),
            "value_swap": swap,
        })

    if not history_path.exists():
        history_path.write_text(payload, encoding="utf-8")
    out_path.write_text(payload, encoding="utf-8")

    with matrix_path.open("a", encoding="utf-8") as fh:
        for row in append_matrix_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if append_ledger:
        append_yaml_list(ledger_path, ledger_row)

    blocked = len(report["directives_blocked"])
    print(f"weakness report round {report['round']}: {len(report['tags'])} tags, "
          f"{len(report['tasks'])} tasks, k={report['k']}, "
          f"between_repeat_sd={report['noise']['between_repeat_sd']}, "
          f"directives_blocked={blocked}")
    print(f"report:  {out_path}")
    print(f"history: {history_path}")
    print(f"matrix:  +{len(append_matrix_rows)} rows -> {matrix_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
