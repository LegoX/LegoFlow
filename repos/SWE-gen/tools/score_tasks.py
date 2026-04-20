#!/usr/bin/env python3
"""Batch difficulty scoring for SWE-gen tasks.

Usage:
    # Score all verified tasks (Feb + March)
    python score_tasks.py --all

    # Score a specific March language directory (all task dirs, not only verified)
    python score_tasks.py --lang py

    # Score all March task directories (including unverified / in-progress task dirs)
    python score_tasks.py --march-all --update-toml

    # Score a single task
    python score_tasks.py --task /path/to/task_dir

    # Score only selected task ids under a directory
    python score_tasks.py --dir tasks/March/py-cc --task-ids-file /tmp/task_ids.txt

    # Score and update task.toml files
    python score_tasks.py --all --update-toml
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from swegen.scoring import score_task, score_tasks_batch, update_task_toml_difficulty

_PROJECT_ROOT = Path(__file__).parent.parent
MARCH_ROOT = _PROJECT_ROOT / "artifacts" / "swe_tasks"
FEB_ROOT = _PROJECT_ROOT / "artifacts" / "swe_tasks"

LANG_DIRS = {
    "py": "py-cc", "js": "js-cc", "ts": "ts-cc", "go": "go-cc",
    "c": "c-cc", "cpp": "cpp-cc", "java": "java-cc", "rust": "rust-cc",
}


def get_verified_tasks(lang_dir: Path) -> list[str]:
    """Read verifiable_tasks.txt and return task IDs."""
    vf = lang_dir / "verifiable_tasks.txt"
    if not vf.exists():
        return []
    return [l.strip() for l in vf.read_text().splitlines() if l.strip()]


def get_all_task_dirs(lang_dir: Path) -> list[Path]:
    """Return every task directory under a March language output dir."""
    return sorted(
        d for d in lang_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def get_selected_task_dirs(root: Path, task_ids: list[str]) -> list[Path]:
    """Return existing task directories for an explicit task-id list."""
    task_dirs: list[Path] = []
    seen: set[str] = set()
    for task_id in task_ids:
        if task_id in seen:
            continue
        seen.add(task_id)
        td = root / task_id
        if td.is_dir():
            task_dirs.append(td)
    return task_dirs


def read_task_ids_file(path: Path) -> list[str]:
    """Read task ids from a plain text file, one per line."""
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def _summarize_scores(scores: list, prefix: str) -> None:
    easy = sum(1 for s in scores if s.label == "easy")
    med = sum(1 for s in scores if s.label == "medium")
    hard = sum(1 for s in scores if s.label == "hard")
    avg = sum(s.score for s in scores) / len(scores) if scores else 0
    print(f"{prefix} avg={avg:.1f} easy={easy} med={med} hard={hard}")


def score_language(
    lang: str,
    update_toml: bool = False,
    verified_only: bool = False,
    task_ids: list[str] | None = None,
    output_name: str | None = None,
) -> list[dict]:
    """Score March tasks for a language.

    By default this scores all task directories, including generated-but-unverified
    tasks. Pass verified_only=True to restrict to verifiable_tasks.txt.
    """
    lang_dir = MARCH_ROOT / LANG_DIRS[lang]
    if verified_only:
        tasks = task_ids if task_ids is not None else get_verified_tasks(lang_dir)
        if not tasks:
            print(f"  {lang}: no verified tasks found")
            return []
        task_dirs = get_selected_task_dirs(lang_dir, tasks)
        mode = "verified"
    elif task_ids is not None:
        task_dirs = get_selected_task_dirs(lang_dir, task_ids)
        if not task_dirs:
            print(f"  {lang}: no selected task dirs found")
            return []
        mode = "selected"
    else:
        task_dirs = get_all_task_dirs(lang_dir)
        if not task_dirs:
            print(f"  {lang}: no task dirs found")
            return []
        mode = "all"

    print(f"  {lang}: scoring {len(task_dirs)} {mode} tasks...", end=" ", flush=True)

    t0 = time.time()
    scores = score_tasks_batch(task_dirs)
    elapsed = time.time() - t0

    if update_toml:
        for s in scores:
            td = lang_dir / s.task_id
            if td.exists():
                update_task_toml_difficulty(td, s)

    # Save scores
    default_output_name = "difficulty_scores.jsonl" if verified_only else "difficulty_scores_all.jsonl"
    output_file = lang_dir / (output_name or default_output_name)
    with open(output_file, "w") as f:
        for s in scores:
            f.write(s.model_dump_json() + "\n")

    print(f"done in {elapsed:.1f}s.", end=" ")
    _summarize_scores(scores, "")
    return [s.model_dump() for s in scores]


def score_march_all(update_toml: bool = False) -> list[dict]:
    """Score all March task directories across all 8 languages."""
    all_scores = []
    print("=== March Task Difficulty Scoring (all task dirs) ===\n")
    for lang in LANG_DIRS:
        all_scores.extend(score_language(lang, update_toml=update_toml, verified_only=False))
    return all_scores


def score_feb_verified(update_toml: bool = False) -> list[dict]:
    """Score Feb-verified-combine tasks."""
    if not FEB_ROOT.exists():
        print("  Feb-verified: directory not found")
        return []

    task_dirs = sorted([d for d in FEB_ROOT.iterdir() if d.is_dir()])
    print(f"  Feb-verified: scoring {len(task_dirs)} tasks...", end=" ", flush=True)

    t0 = time.time()
    scores = score_tasks_batch(task_dirs)
    elapsed = time.time() - t0

    if update_toml:
        for s in scores:
            td = FEB_ROOT / s.task_id
            if td.exists():
                update_task_toml_difficulty(td, s)

    # Save scores
    output_file = FEB_ROOT / "difficulty_scores.jsonl"
    with open(output_file, "w") as f:
        for s in scores:
            f.write(s.model_dump_json() + "\n")

    easy = sum(1 for s in scores if s.label == "easy")
    med = sum(1 for s in scores if s.label == "medium")
    hard = sum(1 for s in scores if s.label == "hard")
    avg = sum(s.score for s in scores) / len(scores) if scores else 0

    print(f"done in {elapsed:.1f}s. avg={avg:.1f} easy={easy} med={med} hard={hard}")
    return [s.model_dump() for s in scores]


def main():
    parser = argparse.ArgumentParser(description="Score SWE-gen tasks for difficulty")
    parser.add_argument("--all", action="store_true", help="Score Feb-verified + all verified March tasks")
    parser.add_argument("--march-all", action="store_true", help="Score all March task dirs, including unverified ones")
    parser.add_argument("--lang", type=str, help="Score a specific March language (all task dirs)")
    parser.add_argument("--task", type=str, help="Score a single task directory")
    parser.add_argument("--dir", type=str, help="Score all task dirs under an arbitrary directory")
    parser.add_argument("--task-ids-file", type=str, help="Restrict --dir/--lang to task ids listed in a file")
    parser.add_argument("--output-file", type=str, help="Override output JSONL path")
    parser.add_argument("--update-toml", action="store_true", help="Update task.toml with scores")
    parser.add_argument("--feb", action="store_true", help="Score Feb-verified tasks")
    parser.add_argument("--verified-only", action="store_true", help="When used with --lang, only score verifiable_tasks.txt")
    args = parser.parse_args()

    task_ids: list[str] | None = None
    if args.task_ids_file:
        task_ids_path = Path(args.task_ids_file)
        if not task_ids_path.exists():
            print(f"Error: {task_ids_path} does not exist")
            return
        task_ids = read_task_ids_file(task_ids_path)

    if args.task:
        td = Path(args.task)
        if not td.exists():
            print(f"Error: {td} does not exist")
            return
        s = score_task(td)
        if args.update_toml:
            update_task_toml_difficulty(td, s)
        print(json.dumps(s.model_dump(), indent=2))
        return

    if args.dir:
        root = Path(args.dir)
        if not root.exists():
            print(f"Error: {root} does not exist")
            return
        if task_ids is not None:
            task_dirs = get_selected_task_dirs(root, task_ids)
        else:
            task_dirs = sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
        if not task_dirs:
            print(f"  No task dirs found in {root}")
            return
        print(f"Scoring {len(task_dirs)} tasks in {root}...", end=" ", flush=True)
        t0 = time.time()
        scores = score_tasks_batch(task_dirs)
        elapsed = time.time() - t0
        if args.update_toml:
            for s in scores:
                td = root / s.task_id
                if td.exists():
                    update_task_toml_difficulty(td, s)
        output_file = Path(args.output_file) if args.output_file else root / "difficulty_scores.jsonl"
        with open(output_file, "w") as f:
            for s in scores:
                f.write(s.model_dump_json() + "\n")
        _summarize_scores(scores, f"done in {elapsed:.1f}s.")
        return

    if args.lang:
        if args.lang not in LANG_DIRS:
            print(f"Error: unknown language {args.lang}. Choose from: {list(LANG_DIRS.keys())}")
            return
        output_name = Path(args.output_file).name if args.output_file else None
        score_language(
            args.lang,
            args.update_toml,
            verified_only=args.verified_only,
            task_ids=task_ids,
            output_name=output_name,
        )
        return

    if args.march_all:
        all_scores = score_march_all(args.update_toml)
        if all_scores:
            total = len(all_scores)
            easy = sum(1 for s in all_scores if s.get("label") == "easy")
            med = sum(1 for s in all_scores if s.get("label") == "medium")
            hard = sum(1 for s in all_scores if s.get("label") == "hard")
            avg = sum(s.get("score", 0) for s in all_scores) / total
            print(f"\n=== March total: {total} tasks, avg={avg:.1f}, easy={easy} ({easy*100//total}%), "
                  f"medium={med} ({med*100//total}%), hard={hard} ({hard*100//total}%) ===")
        return

    if args.all or args.feb:
        print("=== Difficulty Scoring ===\n")
        all_scores = []

        if args.all or args.feb:
            all_scores.extend(score_feb_verified(args.update_toml))

        if args.all:
            for lang in LANG_DIRS:
                all_scores.extend(score_language(lang, args.update_toml, verified_only=True))

        # Summary
        if all_scores:
            total = len(all_scores)
            easy = sum(1 for s in all_scores if s.get("label") == "easy")
            med = sum(1 for s in all_scores if s.get("label") == "medium")
            hard = sum(1 for s in all_scores if s.get("label") == "hard")
            avg = sum(s.get("score", 0) for s in all_scores) / total
            print(f"\n=== Total: {total} tasks, avg={avg:.1f}, easy={easy} ({easy*100//total}%), "
                  f"medium={med} ({med*100//total}%), hard={hard} ({hard*100//total}%) ===")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
