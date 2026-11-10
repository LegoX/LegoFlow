#!/usr/bin/env python3
"""Attach the Lego-X/LegoFlow-SWE Hugging Face dataset into the curator pool.

EXPLICIT, DEFAULT-OFF ESCAPE HATCH. The default pipeline is unchanged: the
loop starts from the Curator (collect PRs -> `legoflow-curator create` ->
Oracle/NOP verification). This script only runs when the user asks for it
twice on the same command line — `--attach legoflow-swe` AND the environment
variable `LEGOFLOW_RSI_ATTACH=legoflow-swe` (belt and suspenders) — and
refuses with exit 2 otherwise.

Why it exists: a fresh checkout has
zero run data, and the supervisor's shortcut is to reuse the published
~6,500-task corpus instead of rolling a new pool from scratch. The dataset
ships COMPLETE Harbor task directories (`tasks/` and `tasks-anti-hack/`:
task.toml, instruction.md, environment/{Dockerfile,bug.patch},
solution/{fix.patch,solve.sh}, tests/) that already passed the publisher's
Oracle/NOP gate, so this import ATTACHES rather than re-verifies: no Docker,
no LLM, no network beyond the one Hugging Face download.

What it does per task:
  * minimal static validation (task.toml parses; the declared file set exists
    and is non-empty where a file must carry content)
  * maps the language from task.toml `metadata.tags` (the 4-tag schema's
    first tag) into the existing `artifacts/swe_tasks/{lang}-cc/` pool layout
  * copies the task directory in (never overwriting a locally curated task)
  * appends the id to that pool's `verifiable_tasks.txt` under the SAME
    `.verifiable_tasks.lock` + dedup discipline `legoflow-curator create`
    uses (see src/legoflow_curator/create/create.py::append_verifiable_task),
    so concurrent create shards and this import never collide
  * records provenance `imported:legoflow-swe:<subset>` in a sidecar manifest
    under artifacts/attach/

Idempotent: ids already listed in a pool's verifiable_tasks.txt are skipped,
so re-running the same import is a no-op.

After a real import, merge so tracer's pool includes the attached tasks:
    python3 scripts/extract_verified_tasks.py

Usage:
    LEGOFLOW_RSI_ATTACH=legoflow-swe python3 import_legoflow_swe.py \
        --attach legoflow-swe --dataset Lego-X/LegoFlow-SWE --revision main
    LEGOFLOW_RSI_ATTACH=legoflow-swe python3 import_legoflow_swe.py \
        --attach legoflow-swe --from-dir /path/to/dataset/root --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ATTACH_VALUE = "legoflow-swe"
ATTACH_ENV = "LEGOFLOW_RSI_ATTACH"
DEFAULT_DATASET = "Lego-X/LegoFlow-SWE"
DEFAULT_REVISION = "main"
SUBSETS = ("tasks", "tasks-anti-hack")

BLOCK_DIR = Path(__file__).resolve().parents[1]
DEFAULT_POOL_ROOT = BLOCK_DIR / "artifacts" / "swe_tasks"
DEFAULT_ATTACH_DIR = BLOCK_DIR / "artifacts" / "attach"
MERGE_COMMAND = "python3 scripts/extract_verified_tasks.py"

# Same roster as scripts/extract_verified_tasks.py — the pool layout is shared.
LANGUAGES = ["py", "js", "ts", "go", "c", "cpp", "java", "rust"]

# task.toml metadata.tags carries [language, area, topic, bug_class]; the
# publisher writes full language names, the pool directories use codes.
LANG_ALIASES = {
    "python": "py",
    "py": "py",
    "javascript": "js",
    "js": "js",
    "typescript": "ts",
    "ts": "ts",
    "go": "go",
    "golang": "go",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "cxx": "cpp",
    "java": "java",
    "rust": "rust",
    "rs": "rust",
}

# The dataset's declared layout. bug.patch may legitimately be empty for a
# from-scratch task, so only its presence is required; tests/test.sh is
# required for parity with the curator's own verifiable_tasks.txt append gate
# (a task without a verifier can never be re-validated downstream); the files
# that must carry content (instruction, Dockerfile, fix, solve, test) are
# checked for it.
REQUIRED_FILES = (
    "instruction.md",
    "environment/Dockerfile",
    "environment/bug.patch",
    "solution/fix.patch",
    "solution/solve.sh",
    "tests/test.sh",
)
REQUIRED_NON_EMPTY = ("instruction.md", "environment/Dockerfile", "solution/fix.patch",
                      "solution/solve.sh", "tests/test.sh")
REQUIRED_DIRS = ("environment", "solution", "tests")

try:  # POSIX only; the curator runs on darwin/linux. Without fcntl the append
    import fcntl  # still dedups, it just cannot exclude a concurrent writer.
except ImportError:  # pragma: no cover - windows dev boxes only
    fcntl = None


@contextmanager
def file_lock(lock_path: Path, timeout: float = 120.0, poll_interval: float = 0.1) -> Iterator[None]:
    """Cross-process exclusive lock, mirroring legoflow_curator.create.file_lock.

    The lock file lives next to the record it guards
    (`.verifiable_tasks.lock`, exactly what `legoflow-curator create` uses) so
    this import and any concurrent create shard serialize on the same inode.
    """
    if fcntl is None:  # pragma: no cover - non-POSIX fallback
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as handle:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if timeout > 0 and (time.monotonic() - start) >= timeout:
                    raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
                time.sleep(poll_interval)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_manifest_ids(pool_dir: Path) -> set[str]:
    """Ids currently listed in one language pool's verifiable_tasks.txt."""
    record = pool_dir / "verifiable_tasks.txt"
    if not record.exists():
        return set()
    ids = set()
    for raw in record.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line:
            ids.add(line)
    return ids


def append_verifiable_tasks(pool_dir: Path, task_ids: list[str]) -> list[str]:
    """Append ids to a pool's verifiable_tasks.txt once each, lock held.

    Mirrors append_verifiable_task() in legoflow_curator.create.create: same
    lock file, same read-set-then-append dedup, one write per batch instead of
    one per task (a single import can carry thousands of ids). Ids a
    concurrent writer landed while we were copying are dropped, not doubled.
    Returns the ids actually appended.
    """
    if not task_ids:
        return []
    pool_dir.mkdir(parents=True, exist_ok=True)
    record = pool_dir / "verifiable_tasks.txt"
    lock_file = pool_dir / ".verifiable_tasks.lock"
    with file_lock(lock_file):
        existing = read_manifest_ids(pool_dir)
        pending = [t for t in task_ids if t not in existing]
        if pending:
            with open(record, "a", encoding="utf-8") as fh:
                fh.write("".join(t + "\n" for t in pending))
        return pending


def language_of(task_toml: dict) -> str | None:
    """Map task.toml metadata tags onto a pool language code.

    Tags follow the 4-tag schema [language, area, topic, bug_class], language
    first; scanning in order takes the language in the normal case and stays
    correct if a publisher ever reorders the list.
    """
    tags = ((task_toml.get("metadata") or {}).get("tags")) or []
    if isinstance(tags, str):
        tags = [tags]
    for tag in tags:
        code = LANG_ALIASES.get(str(tag).strip().lower())
        if code:
            return code
    return None


def validate_task_dir(task_dir: Path) -> str | None:
    """Minimal static validation. Returns None when valid, else the reason.

    The publisher verified these tasks against Oracle/NOP before shipping;
    re-running Docker here would burn ~15 min per task for signal we already
    have, so this checks only that the declared layout is intact.
    """
    toml_path = task_dir / "task.toml"
    if not toml_path.is_file():
        return "missing task.toml"
    try:
        with toml_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return f"unparseable task.toml ({exc})"
    if not isinstance(data, dict):
        return "task.toml is not a table"
    if not data.get("metadata"):
        return "task.toml has no [metadata] table"

    for rel in REQUIRED_DIRS:
        if not (task_dir / rel).is_dir():
            return f"missing {rel}/"
    tests_dir = task_dir / "tests"
    if not any(tests_dir.iterdir()):
        return "empty tests/"

    for rel in REQUIRED_FILES:
        if not (task_dir / rel).is_file():
            return f"missing {rel}"
    for rel in REQUIRED_NON_EMPTY:
        if (task_dir / rel).stat().st_size == 0:
            return f"empty {rel}"
    return None


def iter_subset_dirs(source_root: Path, subset: str) -> list[Path]:
    """Candidate task directories of one subset, in stable (name) order."""
    subset_dir = source_root / subset
    if not subset_dir.is_dir():
        return []
    return sorted(p for p in subset_dir.iterdir() if p.is_dir())


def resolve_source(args: argparse.Namespace, subsets: list[str]) -> tuple[Path | None, str]:
    """Local dir directly, or a Hugging Face snapshot download."""
    if args.from_dir is not None:
        if not args.from_dir.is_dir():
            return None, f"--from-dir does not exist or is not a directory: {args.from_dir}"
        return args.from_dir, "local-dir"
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return None, (
            "huggingface_hub is not importable; install it with "
            "`pip install --upgrade huggingface_hub` (or pass --from-dir for an "
            "already-downloaded snapshot)"
        )
    try:
        root = Path(snapshot_download(
            repo_id=args.dataset,
            repo_type="dataset",
            revision=args.revision,
            # only the selected subsets (~76k files each in LegoFlow-SWE); a
            # full snapshot would fetch the anti-hack half unprompted
            allow_patterns=[f"{s}/*" for s in subsets],
        ))
    except Exception as exc:  # network / auth / revision errors are operational
        return None, f"Hugging Face download failed for {args.dataset}@{args.revision}: {exc}"
    return root, "huggingface"


def gate_refusal_reasons(args: argparse.Namespace) -> list[str]:
    """Why the attach gate stays closed for this invocation."""
    reasons = []
    if args.attach is None:
        reasons.append(f"--attach {ATTACH_VALUE} was not given")
    elif args.attach != ATTACH_VALUE:
        reasons.append(f"--attach must be exactly '{ATTACH_VALUE}' (got {args.attach!r})")
    env_value = os.environ.get(ATTACH_ENV, "")
    if env_value != ATTACH_VALUE:
        reasons.append(f"env {ATTACH_ENV}={env_value!r} (expected '{ATTACH_VALUE}')")
    return reasons


def write_attach_manifest(attach_dir: Path, manifest: dict) -> Path:
    """Write the provenance sidecar, cumulatively: imported[] from prior runs
    is merged (by task_id+subset) under an exclusive lock before the atomic
    replace, so a later run never erases the import history. counts describe
    THIS run only; imported[] is the cumulative record."""
    attach_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = attach_dir / "manifest.json"
    lock_path = attach_dir / ".manifest.lock"
    with open(lock_path, "a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            if manifest_path.exists():
                try:
                    prior = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    prior = None
                if isinstance(prior, dict):
                    prior_imported = [e for e in prior.get("imported") or []
                                      if isinstance(e, dict)]
                    seen = {(e.get("task_id"), e.get("subset")) for e in prior_imported}
                    manifest["imported"] = prior_imported + [
                        e for e in manifest["imported"]
                        if (e.get("task_id"), e.get("subset")) not in seen]
            tmp = manifest_path.with_name(manifest_path.name + ".tmp")
            tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            tmp.replace(manifest_path)
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return manifest_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attach", default=None, metavar="legoflow-swe",
                    help="explicit opt-in; the only accepted value is 'legoflow-swe'")
    ap.add_argument("--from-dir", type=Path, default=None,
                    help="offline mode: local root of the dataset (contains tasks/ [and "
                         "tasks-anti-hack/]); no network is touched")
    ap.add_argument("--dataset", default=DEFAULT_DATASET,
                    help=f"Hugging Face dataset id (default: {DEFAULT_DATASET})")
    ap.add_argument("--revision", default=DEFAULT_REVISION,
                    help=f"dataset revision to pin (default: {DEFAULT_REVISION})")
    ap.add_argument("--subset", choices=[*SUBSETS, "both"], default="tasks",
                    help="which subset of the dataset to attach (default: tasks)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="stop after N newly attached tasks (0 = all)")
    ap.add_argument("--lang-filter", default=None, metavar="LANG[,LANG...]",
                    help="comma-separated pool language codes to keep (e.g. py,go); "
                         f"one of {','.join(LANGUAGES)}")
    ap.add_argument("--pool-root", type=Path, default=DEFAULT_POOL_ROOT,
                    help="curator pool root to attach into "
                         f"(default: {DEFAULT_POOL_ROOT.relative_to(BLOCK_DIR)})")
    ap.add_argument("--attach-dir", type=Path, default=DEFAULT_ATTACH_DIR,
                    help="where the provenance manifest is written "
                         f"(default: {DEFAULT_ATTACH_DIR.relative_to(BLOCK_DIR)})")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and report only; write nothing (note: without "
                         "--from-dir the Hugging Face snapshot is still fully "
                         "downloaded first — point --dry-run at an existing local "
                         "snapshot to avoid the download)")
    args = ap.parse_args()

    # ---- the gate: default-off, both switches at once -----------------------
    reasons = gate_refusal_reasons(args)
    if reasons:
        print("REFUSED: the LegoFlow-SWE attach is an explicit, default-off escape hatch.",
              file=sys.stderr)
        for reason in reasons:
            print(f"  - {reason}", file=sys.stderr)
        print("  The default flow is unchanged: the loop starts from the Curator "
              "(collect PRs -> create tasks -> Oracle/NOP verify).",
              file=sys.stderr)
        return 2

    lang_filter = None
    if args.lang_filter:
        wanted = [x.strip().lower() for x in args.lang_filter.split(",") if x.strip()]
        unknown = [x for x in wanted if x not in LANGUAGES]
        if unknown:
            print(f"ERROR: --lang-filter has unknown code(s) {unknown}; "
                  f"valid: {LANGUAGES}", file=sys.stderr)
            return 1
        lang_filter = set(wanted)

    subsets = list(SUBSETS) if args.subset == "both" else [args.subset]
    source_root, source_note = resolve_source(args, subsets)
    if source_root is None:
        print(f"ERROR: {source_note}", file=sys.stderr)
        return 1

    missing = [s for s in subsets if not (source_root / s).is_dir()]
    if missing:
        print(f"ERROR: {source_root} has no {missing} subset dir(s); not a "
              f"LegoFlow-SWE layout", file=sys.stderr)
        return 1

    print(f"source: {source_root} ({source_note})")
    print(f"subset: {', '.join(subsets)}   limit: {args.limit or 'all'}   "
          f"lang filter: {','.join(sorted(lang_filter)) if lang_filter else 'none'}")
    if args.dry_run:
        print("DRY RUN: nothing will be written")

    imported: list[dict] = []
    skipped: list[dict] = []
    # ids to append per language pool; appended once, under the pool's lock
    pending_appends: dict[str, list[str]] = {lang: [] for lang in LANGUAGES}
    # verifiable_tasks.txt read once per pool per run (reading it per candidate
    # is quadratic at dataset scale); ids this run schedules are added in
    # memory, and the final append re-reads under the lock so a concurrent
    # create shard can never be doubled.
    listed_cache: dict[str, set[str]] = {}

    def listed_ids(lang: str, pool_dir: Path) -> set[str]:
        if lang not in listed_cache:
            listed_cache[lang] = read_manifest_ids(pool_dir)
        return listed_cache[lang]

    for subset in subsets:
        provenance = f"imported:{ATTACH_VALUE}:{subset}"
        for task_dir in iter_subset_dirs(source_root, subset):
            task_id = task_dir.name
            reason = validate_task_dir(task_dir)
            if reason is not None:
                skipped.append({"task_id": task_id, "subset": subset, "reason": reason})
                continue

            with (task_dir / "task.toml").open("rb") as fh:
                lang = language_of(tomllib.load(fh))
            if lang is None:
                skipped.append({"task_id": task_id, "subset": subset,
                                "reason": "language-unmappable (no known tag)"})
                continue
            if lang_filter is not None and lang not in lang_filter:
                skipped.append({"task_id": task_id, "subset": subset,
                                "reason": f"lang-filter-excluded ({lang})"})
                continue

            pool_dir = args.pool_root / f"{lang}-cc"
            if task_id in listed_ids(lang, pool_dir):
                skipped.append({"task_id": task_id, "subset": subset,
                                "reason": "already-imported (in verifiable_tasks.txt)"})
                continue
            dst = pool_dir / task_id
            if dst.exists():
                # Never clobber a locally curated task (a generated skeleton or
                # an unlisted candidate). The manifest is authoritative, so an
                # id we did not list stays unlisted and a human decides.
                skipped.append({"task_id": task_id, "subset": subset,
                                "reason": "destination exists but id not in "
                                          "verifiable_tasks.txt (left untouched)"})
                continue

            imported.append({"task_id": task_id, "subset": subset, "language": lang,
                             "pool": f"{lang}-cc", "provenance": provenance})
            pending_appends[lang].append(task_id)
            listed_ids(lang, pool_dir).add(task_id)  # dedup within this run
            if not args.dry_run:
                pool_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copytree(task_dir, dst, symlinks=True)
                except FileExistsError:
                    # a concurrent create shard (or subset twin) landed the dir
                    # between the check and the copy — leave it alone, record it
                    imported.pop()
                    pending_appends[lang].pop()
                    listed_ids(lang, pool_dir).discard(task_id)
                    skipped.append({"task_id": task_id, "subset": subset,
                                    "reason": "destination appeared mid-run "
                                              "(left untouched)"})
                    continue
                except (shutil.Error, OSError) as exc:
                    # partial copy: remove what we started so no half-written,
                    # unlisted skeleton lingers in the pool
                    imported.pop()
                    pending_appends[lang].pop()
                    # the id is NOT listed (the copy failed) — drop it from the
                    # in-run cache too, or a later twin would be skipped as
                    # "already-imported" while nothing was imported at all
                    listed_ids(lang, pool_dir).discard(task_id)
                    shutil.rmtree(dst, ignore_errors=True)
                    skipped.append({"task_id": task_id, "subset": subset,
                                    "reason": f"copy failed ({exc})"})
                    continue

            if args.limit and len(imported) >= args.limit:
                break
        if args.limit and len(imported) >= args.limit:
            print(f"  limit reached: stopping after {len(imported)} task(s)")
            break

    appended_total = 0
    if not args.dry_run:
        for lang in LANGUAGES:
            appended = append_verifiable_tasks(args.pool_root / f"{lang}-cc",
                                               pending_appends[lang])
            appended_total += len(appended)

    # ---- provenance sidecar manifest ---------------------------------------
    manifest = {
        "schema": "legoflow.attach.v1",
        "dataset": None if args.from_dir is not None else args.dataset,
        "dataset_dir": str(source_root),
        "source": source_note,
        "revision": None if args.from_dir is not None else args.revision,
        "subset": args.subset,
        "provenance": [f"imported:{ATTACH_VALUE}:{s}" for s in subsets],
        "attach_flag": ATTACH_VALUE,
        "dry_run": bool(args.dry_run),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pool_root": str(args.pool_root),
        "imported": imported,
        "skipped": skipped,
        "counts": {
            "imported": len(imported),
            "appended": appended_total,
            "skipped": len(skipped),
            "by_language": dict(sorted(Counter(e["language"] for e in imported).items())),
            "by_skip_reason": dict(sorted(Counter(e["reason"] for e in skipped).items())),
        },
    }
    if not args.dry_run:
        manifest_path = write_attach_manifest(args.attach_dir, manifest)
    else:
        manifest_path = None

    # ---- report -------------------------------------------------------------
    print()
    print(f"{'Language':<10} {'Attached':<10}")
    print("-" * 20)
    by_lang = Counter(e["language"] for e in imported)
    for lang in LANGUAGES:
        if by_lang.get(lang):
            print(f"{lang:<10} {by_lang[lang]:<10}")
    print("-" * 20)
    print(f"{'NEW':<10} {len(imported):<10}")
    print(f"{'SKIPPED':<10} {len(skipped):<10}")
    for reason, n in sorted(Counter(e["reason"] for e in skipped).items()):
        print(f"    {n:>5}  {reason}")
    if args.dry_run:
        print("\nDRY RUN: no tasks were copied, no verifiable_tasks.txt touched, "
              "no manifest written")
        return 0

    print(f"\nManifest written: {manifest_path} "
          f"({len(imported)} imported, {appended_total} appended to "
          f"verifiable_tasks.txt)")
    if appended_total or imported:
        print("\nNEXT STEP: merge the pool so tracer's dataset includes the "
              "attached tasks (from blocks/curator/):")
        print(f"    {MERGE_COMMAND}")
    else:
        print("\nNothing new to attach; the pool and its manifests are unchanged "
              f"(re-run {MERGE_COMMAND} only if the pool changed since the last merge).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
