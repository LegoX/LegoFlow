#!/usr/bin/env python3
"""Export the Self-Made dataset to a unified JSONL format for tag_task_metadata.py to tag with the same LLM scheme.

Self-Made = the union of the following two HuggingFace datasets:
  1. SWE-Lego/swegen-selfmade-260301-260721-non-top5k
  2. SWE-Lego/swegen-selfmade-260301-260622-top5k

Read each task's instruction.md (problem statement) and solution/fix.patch (patch)
directly from the two datasets' tasks.tar.gz, ensuring the exported content
exactly equals these two datasets.
"""
import json
import os
import tarfile
from collections import Counter
from pathlib import Path

DASHBOARD_ROOT = Path(__file__).parent
DATASETS_DIR = DASHBOARD_ROOT / "datasets"
OUTPUT_DIR = DATASETS_DIR / "self_made"

HOME_DIR = Path(os.environ.get("SWEGEN_HOME", str(Path.home())))
REPO_ROOT = Path(os.environ.get("SWEGEN_DATA_ROOT", str(HOME_DIR / "SWE-gen")))
EXPORTS_ROOT = Path(os.environ.get("SWEGEN_EXPORTS_ROOT", str(REPO_ROOT / "exports_hf")))

# The two dataset tarballs that make up self_made
SOURCE_TARBALLS = [
    EXPORTS_ROOT / "swegen-selfmade-260301-260721-non-top5k" / "tasks.tar.gz",
    EXPORTS_ROOT / "swegen-selfmade-260301-260622-top5k" / "tasks.tar.gz",
]

# Code file extension -> language (used to infer language from patch, for dashboard grouping only)
EXT_TO_LANG = {
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hh": "cpp", ".hpp": "cpp", ".hxx": "cpp",
    ".go": "go",
    ".java": "java",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".pyi": "python",
    ".rs": "rust",
    ".ts": "typescript", ".tsx": "typescript", ".cts": "typescript", ".mts": "typescript",
}


def infer_language_from_patch(patch: str) -> str:
    """Infer the primary language from fix.patch diff file extensions."""
    counts: Counter = Counter()
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                if path.startswith("b/"):
                    path = path[2:]
                ext = os.path.splitext(path)[1].lower()
                lang = EXT_TO_LANG.get(ext)
                if lang:
                    counts[lang] += 1
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def export():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_jsonl = OUTPUT_DIR / "tasks.jsonl"

    print(f"{'='*80}")
    print(f"Export the Self-Made dataset")
    for tb in SOURCE_TARBALLS:
        print(f"  source: {tb}  {'✓' if tb.exists() else '✗ missing'}")
    print(f"output: {output_jsonl}")
    print(f"{'='*80}")

    count = 0
    skipped = 0
    seen_ids: set[str] = set()
    lang_counts: Counter = Counter()

    with output_jsonl.open("w", encoding="utf-8") as out:
        for tarball in SOURCE_TARBALLS:
            if not tarball.exists():
                print(f"WARN: skipping missing tarball: {tarball}")
                continue

            print(f"\nprocessing {tarball.parent.name} ...")
            src_count = 0

            with tarfile.open(tarball, "r:gz") as tar:
                # organize by task_id: instruction.md + solution/fix.patch
                # layout inside tarball: tasks/<task_id>/instruction.md, tasks/<task_id>/solution/fix.patch
                pending: dict[str, dict] = {}

                for member in tar:
                    if not member.isfile():
                        continue
                    parts = member.name.split("/")
                    # tasks/<task_id>/...
                    if len(parts) < 3 or parts[0] != "tasks":
                        continue
                    task_id = parts[1]
                    rel = "/".join(parts[2:])

                    if rel == "instruction.md":
                        f = tar.extractfile(member)
                        if f:
                            pending.setdefault(task_id, {})["problem"] = f.read().decode("utf-8", errors="replace")
                    elif rel == "solution/fix.patch":
                        f = tar.extractfile(member)
                        if f:
                            pending.setdefault(task_id, {})["patch"] = f.read().decode("utf-8", errors="replace")

                for task_id, data in pending.items():
                    if task_id in seen_ids:
                        continue  # dedup across the two datasets (should not overlap in theory)
                    patch = data.get("patch", "")
                    if not patch:
                        skipped += 1
                        continue
                    problem = data.get("problem", "")
                    language = infer_language_from_patch(patch)
                    lang_counts[language or "unknown"] += 1

                    unified = {
                        "instance_id": task_id,
                        "problem_statement": problem,
                        "patch": patch,
                        "repo": task_id.split("__", 1)[0] if "__" in task_id else "",
                        "language": language,
                        "dataset_source": "self_made",
                    }
                    out.write(json.dumps(unified, ensure_ascii=False) + "\n")
                    seen_ids.add(task_id)
                    count += 1
                    src_count += 1

            print(f"  -> {src_count} tasks")

    print(f"{'='*80}")
    print(f"✓ export complete: {count} tasks (skipped {skipped} with no patch)")
    print(f"language distribution:")
    for lang, n in lang_counts.most_common():
        print(f"  {lang:12s}: {n}")
    print(f"{'='*80}")


if __name__ == "__main__":
    export()
