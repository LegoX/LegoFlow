#!/usr/bin/env python3
"""Select a stratified sub-pool of verified SWE tasks for tracer rollouts.

The merged pool (`artifacts/merged_swe_tasks/`) stages EVERY verified task —
manifest membership is the only filter anywhere today, and which tasks a run
actually sees is decided by Harbor's directory order under `n_tasks`. This
script replaces that implicit choice with an explicit, deterministic,
coverage-first selection:

  strata   difficulty bin x language, read from each task's own task.toml
           (easy <= 4.0 < medium <= 7.0 < hard; no score -> the task.toml
           label, else an explicit `unknown` stratum — never silently dropped)
  floors   every stratum gets >= 1 slot, then every owner/repo gets >= 1 slot
           (while slots last)
  quotas   remaining slots go to strata in proportion to sqrt(pool size) —
           a large stratum still dominates, but a small one is not starved

Output is a fresh `artifacts/stratified_swe_tasks/` (one symlink per selected
task + its own `verifiable_tasks.txt` + `selection_report.json`), built for
tracer's `task_source.pool: stratified` mode. Selection is by manifest
membership only — the same authoritative contract as extract_verified_tasks.py.

Deterministic by construction: no randomness, every ordering sorted; re-running
over an unchanged pool reproduces the same manifest byte-for-byte.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLOCK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOCK_DIR / "dashboard"))
import task_toml  # noqa: E402  (same-block import; blocks never import across boundaries)

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr)
    sys.exit(2)

EASY_MAX = 4.0
MEDIUM_MAX = 7.0


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_config(config_arg: str | None) -> dict[str, Any]:
    path = Path(config_arg) if config_arg else BLOCK_DIR / "config.yaml"
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        die(f"cannot read config {path}: {exc}")


def config_value(cfg: dict[str, Any], dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def block_relative(raw: str | None, fallback: str) -> Path:
    p = Path(raw) if raw else Path(fallback)
    return p if p.is_absolute() else BLOCK_DIR / p


def difficulty_bin(dim: dict[str, Any]) -> str:
    """easy/medium/hard from the numeric score; fall back to the label, else unknown."""
    score = dim.get("score")
    if isinstance(score, (int, float)):
        if score <= EASY_MAX:
            return "easy"
        if score <= MEDIUM_MAX:
            return "medium"
        return "hard"
    label = str(dim.get("difficulty") or "").strip().lower()
    return label if label in ("easy", "medium", "hard") else "unknown"


def round_robin(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Interleave repos (sorted) so one large repo cannot fill a stratum's slots alone."""
    queues: dict[str, list[dict[str, Any]]] = {}
    for task in sorted(tasks, key=lambda t: t["task_name"]):
        queues.setdefault(task["repo"], []).append(task)
    ordered: list[dict[str, Any]] = []
    ring = [queues[repo] for repo in sorted(queues)]
    index = 0
    while any(ring):
        queue = ring[index % len(ring)]
        if queue:
            ordered.append(queue.pop(0))
        index += 1
    return ordered


def select(tasks: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Pick `count` tasks: stratum floors -> repo floors -> sqrt-proportional quotas."""
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for task in tasks:
        strata.setdefault((task["bin"], task["language"]), []).append(task)
    for members in strata.values():
        members.sort(key=lambda t: (t["repo"], t["task_name"]))

    # Per-stratum round-robin orderings; pops consume from the front.
    orders = {key: round_robin(members) for key, members in strata.items()}
    pool_sizes = {key: len(members) for key, members in strata.items()}
    picked: dict[str, dict[str, Any]] = {}

    def take(key: tuple[str, str]) -> dict[str, Any] | None:
        """Consume the stratum's next unpicked round-robin candidate."""
        while orders[key]:
            task = orders[key].pop(0)
            if task["task_name"] not in picked:
                picked[task["task_name"]] = task
                return task
        return None

    def take_specific(task: dict[str, Any]) -> None:
        """Pick one exact task, also retiring it from its stratum's queue."""
        picked[task["task_name"]] = task
        key = (task["bin"], task["language"])
        orders[key] = [t for t in orders[key] if t["task_name"] != task["task_name"]]

    # Pass 1 — every stratum gets one slot (sorted stratum order, so ties in
    # budget are deterministic).
    for key in sorted(strata):
        if len(picked) >= count:
            break
        take(key)

    # Pass 2 — every repo gets one slot: take that repo's own next task from
    # the stratum holding the most of its remaining candidates.
    def remaining_of(key: tuple[str, str], repo: str) -> int:
        return sum(1 for t in strata[key] if t["repo"] == repo and t["task_name"] not in picked)

    all_repos = sorted({t["repo"] for t in tasks})
    for repo in all_repos:
        if len(picked) >= count:
            break
        if any(t["repo"] == repo for t in picked.values()):
            continue
        best_key, best_n = None, 0
        for key in sorted(strata):
            n = remaining_of(key, repo)
            if n > best_n:
                best_key, best_n = key, n
        if best_key is None:
            continue
        for task in strata[best_key]:
            if task["repo"] == repo and task["task_name"] not in picked:
                take_specific(task)
                break

    # Pass 3 — sqrt-proportional quotas: repeatedly give the next slot to the
    # stratum whose picked/sqrt(pool) share is smallest, so picks converge to
    # picked_n ~ sqrt(pool_n) (largest-remainder behavior, computed greedily
    # so it stays exact under exhaustion).
    def share(key: tuple[str, str]) -> float:
        picked_n = pool_sizes[key] - len(orders[key])
        return picked_n / (pool_sizes[key] ** 0.5)

    while len(picked) < count:
        live = [key for key in strata if orders[key]]
        if not live:
            break
        if take(min(live, key=lambda key: (share(key), key))) is None:
            continue

    return sorted(picked.values(), key=lambda t: (t["bin"], t["language"], t["repo"], t["task_name"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="", help="config path (default: block config.yaml)")
    ap.add_argument("--source", default="", help="source pool (default: config merged_tasks_dir.path)")
    ap.add_argument("--out", default="", help="output pool (default: config stratified_tasks_dir.path)")
    ap.add_argument("--count", default="", help="how many tasks to select (default: config selection.count; "
                                                "'all' or omitted = whole pool)")
    ap.add_argument("--dry-run", action="store_true", help="report the selection without writing anything")
    args = ap.parse_args()

    cfg = read_config(args.config or None)
    source = block_relative(args.source or config_value(cfg, "runtime_info.output.merged_tasks_dir.path"),
                            "artifacts/merged_swe_tasks")
    out = block_relative(args.out or config_value(cfg, "runtime_info.output.stratified_tasks_dir.path"),
                         "artifacts/stratified_swe_tasks")
    count_raw = args.count if args.count != "" else config_value(cfg, "runtime_info.input.selection.count")
    if count_raw in (None, "", "all"):
        count: int | None = None
    else:
        try:
            count = int(count_raw)
        except (TypeError, ValueError):
            die(f"selection.count must be an integer or null/all (got: {count_raw!r})")
        if count <= 0:
            die(f"selection.count must be > 0 (got: {count}); use null/'all' for the whole pool")

    if not source.is_dir():
        die(f"source pool not found: {source} (run scripts/extract_verified_tasks.py first)")

    dims = task_toml.collect_task_dim(source)
    verified = task_toml.read_verified_ids(source)
    if not verified:
        die(f"no verified task ids found under {source} (looked for verifiable_tasks.txt)")

    tasks: list[dict[str, Any]] = []
    unparseable = 0
    missing = 0
    for task_name in sorted(verified):
        if not (source / task_name).is_dir():
            # Manifest ids without a directory cannot be staged (a symlink to
            # them would dangle) — warn and exclude, like extract_verified_tasks.py.
            missing += 1
            print(f"WARN: manifest id has no task directory, skipping: {task_name}", file=sys.stderr)
            continue
        dim = dims.get(task_name)
        if dim is None:
            # A verified task whose task.toml did not parse still counts — it
            # lands in the unknown stratum rather than being silently dropped.
            unparseable += 1
            dim = {"task_name": task_name, "repo": task_toml.task_repo(task_name),
                   "language": "unknown", "score": None, "difficulty": "unknown"}
        else:
            dim = dict(dim)
        dim["bin"] = difficulty_bin(dim)
        tasks.append(dim)

    if not tasks:
        die("source pool manifest lists no tasks present on disk")

    if count is None or count >= len(tasks):
        selected = sorted(tasks, key=lambda t: (t["bin"], t["language"], t["repo"], t["task_name"]))
        truncated = count is not None and count > len(tasks)
    else:
        selected = select(tasks, count)
        truncated = False
    stats = {}
    for task in selected:
        key = f"{task['bin']}|{task['language']}"
        entry = stats.setdefault(key, {"pool": 0, "selected": 0})
        entry["selected"] += 1
    for task in tasks:
        key = f"{task['bin']}|{task['language']}"
        stats.setdefault(key, {"pool": 0, "selected": 0})["pool"] += 1

    ids = [t["task_name"] for t in selected]
    manifest_text = "\n".join(sorted(ids)) + "\n"
    manifest_sha = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    repos_total = len({t["repo"] for t in tasks})
    repos_covered = len({t["repo"] for t in selected})

    report = {
        "source": str(source),
        "out": str(out),
        "count_requested": count if count is not None else "all",
        "count_selected": len(selected),
        "pool_total": len(tasks),
        "truncated": truncated,
        "unparseable_task_toml": unparseable,
        "missing_task_dirs": missing,
        "strata": dict(sorted(stats.items())),
        "repos_total": repos_total,
        "repos_covered": repos_covered,
        "difficulty_unknown_selected": sum(1 for t in selected if t["bin"] == "unknown"),
        "language_unknown_selected": sum(1 for t in selected if t["language"] == "unknown"),
        "manifest_sha256": manifest_sha,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": " ".join(sys.argv),
    }

    if args.dry_run:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    if out.resolve() == source.resolve():
        die(f"--out must differ from the source pool ({source})")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for task_name in sorted(ids):
        (out / task_name).symlink_to((source / task_name).resolve(), target_is_directory=True)
    (out / "verifiable_tasks.txt").write_text(manifest_text, encoding="utf-8")
    with (out / "selection_report.json").open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"Selected {len(selected)}/{len(tasks)} verified task(s) from {source}")
    print(f"{'Stratum':<24} {'Pool':>6} {'Selected':>9}")
    print("-" * 41)
    for key, entry in sorted(stats.items()):
        print(f"{key:<24} {entry['pool']:>6} {entry['selected']:>9}")
    print("-" * 41)
    print(f"Repos covered: {repos_covered}/{repos_total}")
    print(f"Output pool:   {out}")
    print(f"Manifest:      {out / 'verifiable_tasks.txt'} (sha256 {manifest_sha[:12]}…)")
    if truncated:
        print(f"NOTE: requested {count} > pool {len(tasks)}; selected the whole pool")
    return 0


if __name__ == "__main__":
    sys.exit(main())
