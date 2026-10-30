#!/usr/bin/env python3
"""Free frontier signal: per-task outcome disagreement across the scaffolds of
one multi-scaffold tracer campaign (RSI_LOOP_PLAN.md §5.4 A5 / §6.1 M1).

A campaign (`runtime_info.input.agent.scaffolds`) leaves one Harbor job dir per
scaffold (times `samples_per_task` sample jobs per scaffold) under
`runtime_info.input.harbor_job.jobs_dir`. This script is PURE POST-PROCESSING
over a finished campaign — zero extra rollouts: it reads each job dir's
per-trial `<trial-dir>/result.json` (joined by `task_name`, never the trial-dir
prefix) and classifies every task into one of four solve bands:

  saturated  every scaffold solved it -> retire to the eval reserve
  flaky      mixed AND high outcome variance AND low cross-scaffold agreement
             AND an exception in some trial -> reject candidate (infra /
             instruction noise, not training signal)
  reaudit    no scaffold solved it anywhere (though Oracle passed at curation)
             -> instruction-ambiguity audit
  frontier   mixed otherwise -> keep mining/learning on it

ADVICE ONLY (exit 0 even when nothing is flagged): the proposal files are
inputs for agent review, and enforcement goes through the existing exclusion
namespace — `blocks/tracer/excluded_tasks.txt` / `artifacts/processed_tasks.yaml`
via `HARBOR_EXCLUDE_TASKS`. `scripts/resolve_exclude_tasks.py` reads plain-text
lists and ignores `#` comments, so a proposal file can be named there verbatim.

Scaffold identity per job dir — first hit wins, fail closed when all miss:
  1. the job dir's own `config.json` (Harbor writes it beside the trials):
     `agents[].name`, else `agents[].import_path`, normalized with the
     dashboard's scaffold patterns (dashboard/progress_monitor.py — same
     labels the board renders);
  2. the job dir name (campaign job names encode the scaffold);
  3. the explicit `--scaffold` given for that job.
A trial's own `agent_info.name` is only a consistency check (warn on mismatch).

Outputs under --out-dir:
  disagreement.json         schema legoflow.scaffold_disagreement.v1 — one row
                            per task: outcomes per scaffold (mean reward),
                            solve_fraction, flag, and the audit counters
  trial_index.jsonl         schema legoflow.trial_index.v1 — one sorted JSON
                            line per trial (job, scaffold, task_id,
                            trial_index, reward, exception); overwritten per
                            run, accumulable across campaigns because every
                            row carries campaign/job (seeds the M2 response
                            matrix)
  saturation_proposals.txt  newline-delimited task ids, `#` header comment
  flaky_proposals.txt       same shape
  reaudit_proposals.txt     same shape

Deterministic: identical inputs and flags produce byte-identical outputs — no
timestamps, and nothing about the output location is written into the report.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

BLOCK_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = "artifacts/scaffold_signal"
SCHEMA = "legoflow.scaffold_disagreement.v1"
TRIAL_SCHEMA = "legoflow.trial_index.v1"
FLAGS = ("saturated", "flaky", "reaudit", "frontier")

# Import after BLOCK_DIR is fixed: same-block import, exactly the scaffold
# labels the dashboard derives (merge_sft_data.py does the same).
sys.path.insert(0, str(BLOCK_DIR / "dashboard"))
from progress_monitor import derive_scaffold, extract_reward, normalize_scaffold  # noqa: E402


def warn(message: str) -> None:
    print(f"WARN: {message}", file=sys.stderr)


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def resolve_job_dir(raw: str) -> Path:
    """A job entry is a directory; block-dir relative paths also resolve."""
    candidate = Path(raw)
    if candidate.is_dir():
        return candidate.resolve()
    block_candidate = BLOCK_DIR / raw
    if block_candidate.is_dir():
        return block_candidate.resolve()
    die(f"job directory not found: {raw} (also tried {block_candidate})")


def label_from_text(raw: Any) -> str | None:
    """Normalize a scaffold hint with the dashboard patterns; None if unmatched."""
    text = str(raw or "").strip()
    if not text:
        return None
    label = normalize_scaffold(text, "")
    return None if label == "unknown" else label


def scaffold_from_job_config(job_dir: Path) -> str | None:
    """agents[].name (else import_path) from the job dir's Harbor config.json."""
    config_path = job_dir / "config.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warn(f"{config_path} is unreadable ({exc}); falling back to the job name")
        return None
    if not isinstance(data, dict):
        warn(f"{config_path} is not a JSON object; falling back to the job name")
        return None
    agents = data.get("agents")
    for agent in agents if isinstance(agents, list) else []:
        if not isinstance(agent, dict):
            continue
        for key in ("name", "import_path"):
            label = label_from_text(agent.get(key))
            if label is not None:
                return label
    return None


def scaffold_for_job(job_dir: Path, override: str | None) -> tuple[str, str]:
    """(label, provenance) with config.json > job name > --scaffold."""
    label = scaffold_from_job_config(job_dir)
    if label is not None:
        return label, "config.json"
    label = label_from_text(job_dir.name)
    if label is not None:
        return label, "job-name"
    if override:
        text = override.strip()
        return label_from_text(text) or text, "--scaffold"
    die(
        f"cannot resolve the scaffold of job {job_dir}: its config.json carries no "
        "recognizable agent name/import_path and the directory name has no scaffold "
        "hint — pass it explicitly, e.g. "
        f"--scaffold claude-code --jobs {job_dir.name}"
    )


def exception_present(data: dict[str, Any]) -> bool:
    info = data.get("exception_info")
    if info is None:
        return False
    if isinstance(info, dict):
        return bool(info)
    return bool(str(info).strip())


def collect_trials(job: str, job_dir: Path, job_label: str) -> list[dict[str, Any]]:
    """One row per `<trial-dir>/result.json`; unreadable trials are skipped."""
    trials: list[dict[str, Any]] = []
    mismatched: set[str] = set()
    for trial_dir in sorted(p for p in job_dir.iterdir() if p.is_dir()):
        result_file = trial_dir / "result.json"
        if not result_file.is_file():
            continue
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warn(f"skipping {result_file}: unreadable ({exc})")
            continue
        if not isinstance(data, dict):
            warn(f"skipping {result_file}: not a JSON object")
            continue
        agent_info = data.get("agent_info") if isinstance(data.get("agent_info"), dict) else {}
        trial_label = label_from_text(agent_info.get("name"))
        if trial_label is not None and trial_label != job_label:
            mismatched.add(trial_label)
        trials.append(
            {
                # task_name is the join key (never the 32-char trial-dir prefix)
                "task_id": str(data.get("task_name") or data.get("task_id") or trial_dir.name),
                "reward": extract_reward(data),
                "exception": exception_present(data),
                "trial": str(data.get("trial_name") or trial_dir.name),
                # provenance: one scaffold can span several sample job dirs
                "job": job,
            }
        )
    for label in sorted(mismatched):
        warn(
            f"job {job} is labeled '{job_label}' but some trials report agent "
            f"'{label}'; keeping the job-level label"
        )
    return trials


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6)


def population_sd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = sum(values) / len(values)
    return round(math.sqrt(sum((v - mu) ** 2 for v in values) / len(values)), 6)


def cross_scaffold_agreement(solved: list[bool]) -> float | None:
    """Fraction of scaffold pairs whose solve status agrees; None below 2."""
    n = len(solved)
    if n < 2:
        return None
    pairs = n * (n - 1) // 2
    agreeing = sum(1 for i in range(n) for j in range(i + 1, n) if solved[i] == solved[j])
    return round(agreeing / pairs, 6)


def classify(entry: dict[str, Any], thresholds: dict[str, Any]) -> str:
    solve_fraction = entry["solve_fraction"]
    # Only a task the WHOLE campaign ran carries band evidence: a task some
    # scaffold never attempted is missing data, not evidence (main() already
    # refuses campaigns with <2 distinct scaffolds, so full coverage implies
    # n_scaffolds >= 2).
    full_coverage = entry["n_scaffolds"] == entry["n_campaign_scaffolds"]
    # saturated needs the whole campaign's view: every scaffold solved it.
    if full_coverage and solve_fraction == 1.0:
        return "saturated"
    # reaudit = "none solve though Oracle passed": f==0.0 with at least one
    # real verifier verdict — a task whose every trial crashed before
    # verification is not evidence of an ambiguous instruction.
    if full_coverage and solve_fraction == 0.0 and entry["verified_trials"] >= 1:
        return "reaudit"
    if (
        0.0 < solve_fraction < 1.0
        and full_coverage
        and entry["outcome_sd"] >= thresholds["flaky_min_sd"]
        and entry["cross_scaffold_agreement"] is not None
        and entry["cross_scaffold_agreement"] <= thresholds["flaky_max_agreement"]
        and entry["exception_scaffolds"]
    ):
        return "flaky"
    return "frontier"


def build_report(
    campaign: str,
    jobs: list[dict[str, Any]],
    tasks: dict[str, dict[str, Any]],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    scaffolds = sorted({job["scaffold"] for job in jobs})
    rows: list[dict[str, Any]] = []
    for task_id in sorted(tasks):
        per_scaffold = tasks[task_id]
        outcomes: dict[str, float] = {}
        solved_by: list[str] = []
        exception_scaffolds: list[str] = []
        n_trials = 0
        verified = 0
        missing = 0
        for scaffold in sorted(per_scaffold):
            trials = per_scaffold[scaffold]
            # An unverified trial (verifier never ran) counts as unsolved and
            # is reported separately, so a crashed campaign reads as missing
            # data rather than as "task too hard".
            values = [t["reward"] if t["reward"] is not None else 0.0 for t in trials]
            outcome = mean(values)
            outcomes[scaffold] = outcome
            if outcome >= thresholds["solve_threshold"]:
                solved_by.append(scaffold)
            if any(t["exception"] for t in trials):
                exception_scaffolds.append(scaffold)
            n_trials += len(trials)
            verified += sum(1 for t in trials if t["reward"] is not None)
            missing += sum(1 for t in trials if t["reward"] is None)
        outcome_values = [outcomes[scaffold] for scaffold in sorted(outcomes)]
        solved_flags = [scaffold in solved_by for scaffold in sorted(outcomes)]
        rows.append(
            {
                "task_id": task_id,
                "outcomes": outcomes,
                "solve_fraction": round(len(solved_by) / len(outcomes), 6),
                "flag": "",  # filled below (classify reads the finished row)
                "n_scaffolds": len(outcomes),
                "n_campaign_scaffolds": len(scaffolds),
                "n_trials": n_trials,
                "verified_trials": verified,
                "missing_reward_trials": missing,
                "solved_by": solved_by,
                "exception_scaffolds": exception_scaffolds,
                "outcome_sd": population_sd(outcome_values),
                "cross_scaffold_agreement": cross_scaffold_agreement(solved_flags),
                "trials_per_scaffold": {s: len(per_scaffold[s]) for s in sorted(per_scaffold)},
            }
        )
    for row in rows:
        row["flag"] = classify(row, thresholds)
    flag_counts = {name: sum(1 for row in rows if row["flag"] == name) for name in FLAGS}
    return {
        "schema": SCHEMA,
        "campaign": campaign,
        "advice_only": True,
        "jobs": jobs,
        "scaffolds": scaffolds,
        "thresholds": thresholds,
        "summary": {
            "n_tasks": len(rows),
            "flags": flag_counts,
            "partial_coverage_tasks": sum(1 for row in rows if row["n_scaffolds"] < len(scaffolds)),
            "unverified_tasks": sum(1 for row in rows if row["verified_trials"] == 0),
        },
        "tasks": rows,
    }


PROPOSAL_ACTIONS = {
    "saturation": "retire these tasks from the training pool to the eval reserve",
    "flaky": "reject candidates: mixed outcomes with high variance, low cross-scaffold agreement, and an exception",
    "reaudit": "instruction-ambiguity audit: no scaffold solves them though Oracle passed at curation",
}


def write_proposals(out_dir: Path, kind: str, flag: str, report: dict[str, Any]) -> Path:
    ids = [row["task_id"] for row in report["tasks"] if row["flag"] == flag]
    path = out_dir / f"{kind}_proposals.txt"
    lines = [
        f"# {SCHEMA} {kind} proposals (advice-only, exit 0 — agent review required)",
        f"# campaign: {report['campaign']}",
        f"# scaffolds: {', '.join(report['scaffolds'])}",
        f"# action: {PROPOSAL_ACTIONS[kind]}",
        "# enforcement: after review, via blocks/tracer/excluded_tasks.txt /",
        "# HARBOR_EXCLUDE_TASKS (scripts/resolve_exclude_tasks.py ignores # comments)",
    ]
    lines.extend(ids)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_trial_index(out_dir: Path, campaign: str, tasks: dict[str, dict[str, Any]]) -> Path:
    """One JSON line per trial (schema TRIAL_SCHEMA), rows in sorted order.

    Overwritten (never appended) per run so identical inputs stay
    byte-identical; accumulation across campaigns happens by keeping one
    out-dir per campaign — every row carries campaign/job for the join.
    """
    rows = [
        {
            "schema": TRIAL_SCHEMA,
            "campaign": campaign,
            "job": trial["job"],
            "scaffold": scaffold,
            "task_id": task_id,
            "trial_index": trial["trial"],
            "reward": trial["reward"],
            "exception": trial["exception"],
        }
        for task_id, per_scaffold in sorted(tasks.items())
        for scaffold, trials in sorted(per_scaffold.items())
        for trial in trials
    ]
    path = out_dir / "trial_index.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
scaffold identity: each --jobs group takes the --scaffold given next to it
(config.json / job name win over an override when they identify the scaffold):

  scaffold_disagreement.py --jobs artifacts/jobs/A artifacts/jobs/B
  scaffold_disagreement.py --scaffold claude-code --jobs jobs/A \\
      --scaffold openhands --jobs jobs/B --out-dir artifacts/scaffold_signal

one --scaffold may cover several job dirs of the SAME scaffold (sample jobs):
  --scaffold claude-code --jobs jobs/A-s1 jobs/A-s2 --scaffold openhands --jobs jobs/B
""",
    )
    parser.add_argument(
        "--jobs",
        required=True,
        action="append",
        nargs="+",
        metavar="DIR",
        help="Harbor job directories of ONE campaign, one group per scaffold "
        "(block-dir relative paths also resolve)",
    )
    parser.add_argument(
        "--scaffold",
        action="append",
        default=None,
        metavar="NAME",
        help="scaffold label for the --jobs group that follows it; only needed "
        "when a job dir carries no config.json and its name has no scaffold hint",
    )
    parser.add_argument(
        "--campaign",
        default="",
        help="campaign name recorded in disagreement.json (default: joined scaffold labels)",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"output directory (default: {DEFAULT_OUT_DIR}; block-dir relative)",
    )
    parser.add_argument(
        "--solve-threshold",
        type=float,
        default=0.5,
        help="a scaffold counts as solving a task when its mean reward is >= this "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--flaky-min-sd",
        type=float,
        default=0.35,
        help="population sd over the per-scaffold outcomes must be >= this for flaky "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--flaky-max-agreement",
        type=float,
        default=0.5,
        help="cross-scaffold solve-status agreement must be <= this for flaky "
        "(default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if not (0.0 < args.solve_threshold <= 1.0):
        parser.error("--solve-threshold must be in (0, 1]")
    if args.flaky_min_sd < 0.0:
        parser.error("--flaky-min-sd must be >= 0")
    if not (0.0 <= args.flaky_max_agreement <= 1.0):
        parser.error("--flaky-max-agreement must be in [0, 1]")
    if args.scaffold and len(args.scaffold) != len(args.jobs):
        parser.error(
            f"{len(args.scaffold)} --scaffold value(s) for {len(args.jobs)} --jobs "
            "group(s): pair them 1:1 (--scaffold NAME --jobs DIR [DIR ...], repeated)"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    thresholds = {
        "solve_threshold": args.solve_threshold,
        "flaky_min_sd": args.flaky_min_sd,
        "flaky_max_agreement": args.flaky_max_agreement,
    }
    # One --scaffold per --jobs group, already validated 1:1 by parse_args.
    overrides: list[str | None] = list(args.scaffold) if args.scaffold else [None] * len(args.jobs)

    job_specs: list[tuple[str, Path, str | None]] = []
    seen: dict[Path, str] = {}
    for group, override in zip(args.jobs, overrides):
        for raw in group:
            job_dir = resolve_job_dir(raw)
            if job_dir in seen:
                die(f"job directory listed twice: {raw} (would double-count its trials)")
            seen[job_dir] = raw
            job_specs.append((raw, job_dir, override))

    jobs: list[dict[str, Any]] = []
    tasks: dict[str, dict[str, Any]] = {}
    total_trials = 0
    for raw, job_dir, override in job_specs:
        label, source = scaffold_for_job(job_dir, override)
        trials = collect_trials(raw, job_dir, label)
        if not trials:
            warn(f"job {raw} ({label}) has no readable trial result.json")
        total_trials += len(trials)
        jobs.append(
            {
                "job": raw,
                "resolved_path": str(job_dir),
                "scaffold": label,
                "scaffold_source": source,
                "n_trials": len(trials),
            }
        )
        for trial in trials:
            tasks.setdefault(trial["task_id"], {}).setdefault(label, []).append(trial)

    if total_trials == 0:
        die("no trial result.json found under the given job dir(s)")

    scaffolds = sorted({job["scaffold"] for job in jobs})
    if len(scaffolds) < 2:
        die(
            f"a disagreement signal needs >= 2 distinct scaffolds, got {len(scaffolds)} "
            f"({', '.join(scaffolds) or 'none'}) — pass the other scaffold's job dir(s) "
            "of the same campaign"
        )

    campaign = args.campaign.strip() or "-".join(scaffolds)
    report = build_report(campaign, jobs, tasks, thresholds)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = BLOCK_DIR / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    disagreement_path = out_dir / "disagreement.json"
    with disagreement_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    trial_index_path = write_trial_index(out_dir, campaign, tasks)

    proposal_paths = {
        "saturation": write_proposals(out_dir, "saturation", "saturated", report),
        "flaky": write_proposals(out_dir, "flaky", "flaky", report),
        "reaudit": write_proposals(out_dir, "reaudit", "reaudit", report),
    }

    counts = report["summary"]["flags"]
    print(f"Campaign '{campaign}': {report['summary']['n_tasks']} task(s) across {len(scaffolds)} scaffold(s)")
    print(
        "Flags: "
        + ", ".join(f"{name}={counts[name]}" for name in FLAGS)
    )
    if report["summary"]["partial_coverage_tasks"]:
        warn(
            f"{report['summary']['partial_coverage_tasks']} task(s) appear under fewer "
            "than the campaign's scaffolds (missing data, not evidence — flagged frontier)"
        )
    print(f"Disagreement: {disagreement_path}")
    print(f"Trial index: {trial_index_path}")
    for kind in ("saturation", "flaky", "reaudit"):
        n = sum(1 for line in proposal_paths[kind].read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
        print(f"{kind}_proposals.txt: {n} task id(s) -> {proposal_paths[kind]}")
    print("Advice-only: review before enforcing through the exclusion namespace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
