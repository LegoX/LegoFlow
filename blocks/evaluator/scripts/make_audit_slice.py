#!/usr/bin/env python3
"""Draw the stratified audit slice for the RSI loop (RSI_LOOP_PLAN §5.3).

The audit slice is the loop's held-out honesty control: its task ids are
excluded from every directive-feeding aggregate (build_weakness_report.py
drops them via --audit-slice) and its scores are read only for promotion
decisions. This script samples it deterministically:

  * stratified by --strata (default: language, i.e. gold task.toml
    [metadata] tags[0]; also area/topic/bug_class/difficulty),
  * proportional allocation by largest remainder (deterministic tie-break on
    stratum name),
  * within a stratum, a per-stratum seeded RNG (random.Random(f"{seed}|{stratum}"))
    over the sorted id list, so adding tasks to ANOTHER stratum can never
    change this stratum's draw,
  * refuses n > available (a short slice silently weakens every promotion
    readout; refuse instead of quietly shrinking).

Same seed + same population => byte-identical slice. The slice and its meta
are meant to be git-tracked (plan §5.3 durability note).

Usage:
  python3 make_audit_slice.py --dataset-dir artifacts/datasets/swebench-verified \
      --n 64 --seed 1 --strata language \
      --out artifacts/reserve/audit_slice.txt \
      [--meta artifacts/reserve/audit_slice.meta.json] \
      [--emit-registry artifacts/reserve/audit_registry.json] \
      [--slice-version 1]

The meta file records the slice's version. Overwriting an existing meta whose
slice_version differs is refused unless --rotate is passed — a silent version
change would make same-version comparisons across rounds meaningless.

--emit-registry additionally writes the frozen Harbor-style registry the loop
serves the audit slice through (RSI_LOOP_PLAN §5.3): a JSON list with one
{name, version, tasks: [{name, path}]} entry over the slice's task dirs,
mirroring terminal-curator's artifacts/tasks/registry.json schema. Refused
when any sampled task dir is missing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

STRATA_TO_TAG_INDEX = {"language": 0, "area": 1, "topic": 2, "bug_class": 3}


def stratum_of(task_dir: Path, strata: str) -> str:
    """Stratum label for one gold task dir ('unknown' when untagged/untaggable)."""
    toml_path = task_dir / "task.toml"
    if not toml_path.is_file():
        return "unknown"
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return "unknown"
    metadata = data.get("metadata") if isinstance(data, dict) else None
    if not isinstance(metadata, dict):
        return "unknown"
    if strata == "difficulty":
        value = metadata.get("difficulty")
        return str(value).strip().lower() if value else "unknown"
    idx = STRATA_TO_TAG_INDEX.get(strata)
    if idx is None:
        return "unknown"
    tags = [str(t).strip().lower() for t in (metadata.get("tags") or [])]
    return tags[idx] if len(tags) > idx and tags[idx] else "unknown"


def stratified_sample(population: dict, n: int, seed: int) -> tuple[list[str], dict]:
    """Proportional largest-remainder allocation + per-stratum seeded draw."""
    strata = sorted({s for s in population.values()})
    sizes = {s: 0 for s in strata}
    for stratum in strata:
        sizes[stratum] = sum(1 for v in population.values() if v == stratum)
    total = sum(sizes.values())
    if n > total:
        raise ValueError(f"n={n} exceeds the available population of {total} tasks")
    if strata == ["unknown"]:
        print("WARNING: no task.toml tags found; the slice is unstratified "
              "(every task landed in the 'unknown' stratum).", file=sys.stderr)

    quota = {s: (n * sizes[s]) // total for s in strata}
    remainder = n - sum(quota.values())
    # Largest fractional remainder first; ties broken by stratum name (stable).
    by_remainder = sorted(strata, key=lambda s: (-(n * sizes[s] % total), s))
    for stratum in by_remainder[:remainder]:
        quota[stratum] += 1
    # Allocation cannot exceed a stratum's size when n <= total, but clamp anyway.
    for stratum in strata:
        quota[stratum] = min(quota[stratum], sizes[stratum])

    picked: list[str] = []
    for stratum in strata:
        members = sorted(t for t, s in population.items() if s == stratum)
        take = quota[stratum]
        if take <= 0:
            continue
        rng = random.Random(f"{seed}|{stratum}")
        picked.extend(rng.sample(members, take))
    return sorted(picked), quota


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="make_audit_slice",
        description="Deterministic stratified audit-slice sample over a gold dataset.")
    parser.add_argument("--dataset-dir", required=True, metavar="DIR",
                        help="gold dataset dir with per-task task dirs")
    parser.add_argument("--n", type=int, default=64,
                        help="slice size (default: 64; plan §11 recommends 64 to start)")
    parser.add_argument("--seed", type=int, default=1,
                        help="sampling seed (default: 1)")
    parser.add_argument("--strata", default="language",
                        choices=["language", "area", "topic", "bug_class", "difficulty"],
                        help="stratification dimension (default: language)")
    parser.add_argument("--out", required=True, metavar="PATH",
                        help="audit_slice.txt output path")
    parser.add_argument("--meta", default=None, metavar="PATH",
                        help="audit_slice.meta.json output path (written only if given)")
    parser.add_argument("--emit-registry", default=None, metavar="PATH",
                        help="write the frozen Harbor-style registry JSON over "
                             "the slice's task dirs (written only if given)")
    parser.add_argument("--registry-name", default="legoflow-audit", metavar="NAME",
                        help="registry entry name for --emit-registry "
                             "(default: legoflow-audit)")
    parser.add_argument("--slice-version", type=int, default=1,
                        help="slice format version recorded in meta (default: 1)")
    parser.add_argument("--rotate", action="store_true",
                        help="allow replacing an existing meta whose "
                             "slice_version differs (refused by default)")
    args = parser.parse_args(argv)

    dataset_dir = Path(args.dataset_dir)
    if not dataset_dir.is_dir():
        print(f"ERROR: dataset dir not found: {dataset_dir}", file=sys.stderr)
        return 1
    task_dirs = sorted(p for p in dataset_dir.iterdir() if p.is_dir())
    population = {d.name: stratum_of(d, args.strata) for d in task_dirs}
    if not population:
        print(f"ERROR: no task dirs under {dataset_dir}", file=sys.stderr)
        return 1
    if args.n <= 0:
        print(f"ERROR: --n must be >= 1 (got {args.n})", file=sys.stderr)
        return 1

    try:
        slice_ids, sample_sizes = stratified_sample(population, args.n, args.seed)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Refuse a silent slice_version change: an existing meta recorded under a
    # different version may only be replaced deliberately (--rotate), before
    # any file is written.
    meta_path = Path(args.meta) if args.meta else None
    if meta_path is not None and meta_path.is_file() and not args.rotate:
        prev_version = None
        try:
            prev_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prev_meta = None
        if isinstance(prev_meta, dict):
            prev_version = prev_meta.get("slice_version", 1)
        if prev_version != args.slice_version:
            print(f"ERROR: {meta_path} records slice_version {prev_version}; "
                  f"refusing to overwrite it with version {args.slice_version} "
                  f"without --rotate.", file=sys.stderr)
            return 1

    # Registry payload (frozen legoflow-audit Harbor registry) is built — and
    # its task dirs verified — BEFORE any file is written: a slice whose task
    # dirs have gone missing must not be half-published.
    registry_path = Path(args.emit_registry) if args.emit_registry else None
    registry_entry = None
    if registry_path is not None:
        missing = [t for t in slice_ids if not (dataset_dir / t).is_dir()]
        if missing:
            shown = ", ".join(missing[:5]) + (" …" if len(missing) > 5 else "")
            print(f"ERROR: cannot emit registry; sampled task dir(s) missing under "
                  f"{dataset_dir}: {shown}", file=sys.stderr)
            return 1
        registry_entry = [{
            "name": args.registry_name,
            "version": "1.0",
            "description": "Frozen audit-slice reserve tasks, excluded from every "
                           "directive-feeding aggregate.",
            "tasks": [{"name": task_id, "path": str((dataset_dir / task_id).resolve())}
                      for task_id in slice_ids],
        }]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(slice_ids) + "\n", encoding="utf-8")

    print(f"audit slice: {len(slice_ids)}/{len(population)} tasks "
          f"(strata={args.strata}, seed={args.seed}) -> {out_path}")
    if args.meta:
        meta = {
            "slice_version": args.slice_version,
            "seed": args.seed,
            "strata": args.strata,
            "sample_sizes": sample_sizes,
            "created_from": str(dataset_dir),
            "n": len(slice_ids),
            "population_size": len(population),
            "population_sha256": hashlib.sha256(
                "\n".join(sorted(population)).encode("utf-8")).hexdigest(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"meta:       {meta_path}")

    if registry_entry is not None:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            json.dumps(registry_entry, indent=2) + "\n", encoding="utf-8")
        print(f"registry:   {registry_path} "
              f"({args.registry_name}@1.0, {len(slice_ids)} tasks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
