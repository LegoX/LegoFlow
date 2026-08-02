#!/usr/bin/env python3
"""Aggregate verified SWE tasks from every language into one flat directory.

The destination is `runtime_info.output.merged_tasks_dir.path` in config.yaml —
the same value tracer's `dependencies.from` resolves to — so the two ends cannot
drift. Pass --output-dir to override.

Safe to run repeatedly while generation is still in flight: a task already
present and no older than its source is left alone, so the periodic aggregation
started by scripts/start.sh costs a stat per task rather than a full re-copy of
everything produced so far.

Only task ids listed in a language's verifiable_tasks.txt are copied — those are
the ones that passed NOP/Oracle validation — and the combined manifest written
at the destination is what tracer's prepare_tasks.sh filters against.
"""

import argparse
import shutil
import sys
from pathlib import Path

LANGUAGES = ["py", "js", "ts", "go", "c", "cpp", "java", "rust"]
BLOCK_DIR = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = BLOCK_DIR / "artifacts" / "swe_tasks"
DEFAULT_OUTPUT = BLOCK_DIR / "artifacts" / "merged_swe_tasks"


def configured_output_dir() -> Path:
    """Destination from config.yaml, falling back to the historical default."""
    cfg = BLOCK_DIR / "config.yaml"
    try:
        import yaml
        with cfg.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        path = (((data.get("runtime_info") or {}).get("output") or {})
                .get("merged_tasks_dir") or {}).get("path")
    except Exception:
        path = None
    if not path:
        return DEFAULT_OUTPUT
    p = Path(path)
    return p if p.is_absolute() else BLOCK_DIR / p


def is_current(src: Path, dst: Path) -> bool:
    """Is the copy at dst up to date with src?

    Compares the directory mtimes only. Walking both trees is the accurate
    answer but costs a stat per file, which at a few hundred tasks took over a
    minute per pass — far too slow for something that runs on a timer while
    generation is in flight. copytree preserves the source's directory mtime,
    and swegen rewrites the task directory when it regenerates a task, so the
    top-level comparison catches the case that matters. --force covers the rest.
    """
    try:
        return dst.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-dir", type=Path, default=None,
                    help="destination (default: config.yaml output.merged_tasks_dir.path)")
    ap.add_argument("--force", action="store_true",
                    help="re-copy every task even if the destination is current")
    ap.add_argument("--quiet", action="store_true",
                    help="print one summary line only (for periodic runs)")
    args = ap.parse_args()

    output_dir = args.output_dir or configured_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    stats, extracted_ids = {}, []
    copied_total = skipped_total = 0

    for lang in LANGUAGES:
        lang_dir = ARTIFACTS_ROOT / f"{lang}-cc"
        vt_file = lang_dir / "verifiable_tasks.txt"
        if not vt_file.exists():
            stats[lang] = 0
            continue

        task_ids = [ln.strip() for ln in vt_file.read_text().splitlines() if ln.strip()]
        copied = 0
        for task_id in task_ids:
            src, dst = lang_dir / task_id, output_dir / task_id
            if not src.is_dir():
                if not args.quiet:
                    print(f"  WARN: {lang}/{task_id} not found, skipping")
                continue
            # A verified task is a finished artifact, so an existing copy is
            # current unless the source has been regenerated since.
            if dst.exists():
                if not args.force and is_current(src, dst):
                    extracted_ids.append(task_id)
                    skipped_total += 1
                    continue
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied += 1
            extracted_ids.append(task_id)

        stats[lang] = copied
        copied_total += copied

    # The manifest is the consumer contract (tracer filters against it), so it
    # is rewritten in full every pass — cheap, and it self-heals if it goes
    # missing. It lists what is actually PRESENT here, not what this pass
    # managed to copy: a task only ever lands in this directory by passing
    # validation, so once it is here it stays listed even if its per-language
    # source has since been cleaned away. Deriving the manifest from this pass
    # instead silently dropped 28 already-verified tasks whose sources were gone.
    manifest_file = output_dir / "verifiable_tasks.txt"
    present = sorted(p.name for p in output_dir.iterdir() if p.is_dir())
    manifest_file.write_text("\n".join(present) + "\n")

    total = len(present)
    if args.quiet:
        print(f"aggregated {total} verified task(s) -> {output_dir} "
              f"(+{copied_total} new, {skipped_total} already current)")
        return 0

    print(f"\n{'Language':<10} {'Copied':<10}")
    print("-" * 20)
    for lang in LANGUAGES:
        print(f"{lang:<10} {stats.get(lang, 0):<10}")
    print("-" * 20)
    print(f"{'NEW':<10} {copied_total:<10}")
    print(f"{'CURRENT':<10} {skipped_total:<10}")
    print(f"{'TOTAL':<10} {total:<10}")
    print(f"\nOutput directory: {output_dir}")
    print(f"Manifest written: {manifest_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
