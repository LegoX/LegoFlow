#!/usr/bin/env python3
"""Resolve HARBOR_EXCLUDE_TASKS into a concrete list of Harbor task ids.

Each whitespace-separated token is a YAML ledger path (-> its terminal
done/failed/skipped entries), a plain-text list path (-> one id per line), or a
literal task id. Relative paths resolve against --block-dir.

--ledger-exclude 0 suppresses ledger-derived ids so a smoke can re-run its
fixtures; literal ids and text lists still apply.

Missing files and a missing PyYAML are non-fatal — this runs on the launch path
and must not abort it.
"""
import argparse
import sys
from pathlib import Path

TERMINAL_STATUSES = {"done", "failed", "skipped"}


def _load_yaml(path: Path):
    try:
        import yaml
    except ImportError:
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except Exception:
        return None


def ledger_ids(path: Path) -> set:
    """Terminal task ids recorded in a processed-tasks ledger."""
    doc = _load_yaml(path)
    if not isinstance(doc, dict):
        return set()
    runs = doc.get("runs")
    if not isinstance(runs, list):
        return set()
    return {
        entry.get("task_id")
        for entry in runs
        if isinstance(entry, dict)
        and entry.get("status") in TERMINAL_STATUSES
        and entry.get("task_id")
    }


def text_ids(path: Path) -> set:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return set()
    ids = set()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            ids.update(line.split())
    return ids


def is_yaml_ledger(path: Path) -> bool:
    if path.suffix.lower() in {".yaml", ".yml"}:
        return True
    doc = _load_yaml(path)
    return isinstance(doc, dict) and isinstance(doc.get("runs"), list)


def resolve(spec: str, block_dir: Path, ledger_exclude: bool) -> set:
    ids = set()
    for token in (spec or "").split():
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = block_dir / candidate
        if not candidate.is_file():
            # A slash means it was meant as a path; surface the typo instead of
            # excluding a "task" named artifacts/processed_tasks.yaml.
            if "/" in token:
                print(
                    f"WARN: exclusion source {token!r} not found under {block_dir}",
                    file=sys.stderr,
                )
                continue
            ids.add(token)
            continue
        if is_yaml_ledger(candidate):
            if ledger_exclude:
                ids |= ledger_ids(candidate)
        else:
            ids |= text_ids(candidate)
    return {i for i in ids if i}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--block-dir", required=True, help="Block root for relative paths")
    ap.add_argument("--spec", default="", help="Raw HARBOR_EXCLUDE_TASKS value")
    ap.add_argument(
        "--ledger",
        default="",
        help="Ledger unioned in even when the spec does not name it (back-compat "
        "for configs that still hold a literal id list)",
    )
    ap.add_argument(
        "--ledger-exclude",
        default="1",
        help="0 suppresses every ledger-derived id (smoke re-runs its fixtures)",
    )
    ap.add_argument(
        "--count-only", action="store_true", help="Print the id count instead of the ids"
    )
    args = ap.parse_args()

    block_dir = Path(args.block_dir).resolve()
    ledger_exclude = str(args.ledger_exclude).strip() not in {"0", "false", "no", ""}

    ids = resolve(args.spec, block_dir, ledger_exclude)
    if args.ledger and ledger_exclude:
        ledger_path = Path(args.ledger)
        if not ledger_path.is_absolute():
            ledger_path = block_dir / ledger_path
        if ledger_path.is_file():
            ids |= ledger_ids(ledger_path)

    if args.count_only:
        print(len(ids))
    else:
        for task_id in sorted(ids):
            print(task_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
