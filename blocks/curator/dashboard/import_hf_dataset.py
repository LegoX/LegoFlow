#!/usr/bin/env python3
"""Turn a public HuggingFace SWE dataset into Harbor task directories.

The dashboard reads `task.toml`, so the way to put an open-source dataset on the
board is to give it the same on-disk shape as a generated one — not a second data
path. After importing, add the output directory to
symlinked into `artifacts/swe_tasks/` and it becomes another batch
alongside the pipeline's own.

    <out>/<instance_id>/task.toml        [metadata] tags / difficulty / category
    <out>/<instance_id>/instruction.md   problem statement

Two stages, matching how the evaluator prepares benchmark datasets
(`blocks/evaluator/scripts/prepare_dataset.sh`):

  1. IMPORT  (this script) — download and write the task dirs. `tags` starts as
     `[language]` only, since the language ships with the dataset.
  2. TAG     — run the canonical tagger over the output to fill in
     `[language, area, topic, bug_class]`, category and scoring:

         python3 ../repos/legoflow-curator/tools/tag_task_metadata.py \\
           --tasks-dir <out> --jobs 64 --retries 3

     Resumable: already-tagged tasks are skipped. Until it runs, the board shows
     these tasks as untagged rather than guessing.

These datasets carry no PR provenance; being a symlink is what marks them external
to keep them out of the PR → task funnel on the Collection view.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterator

# name -> (hf repo, file, dataset_source). Recovered from the exporters this
# replaces; add a row to support another dataset.
DATASETS: dict[str, tuple[str, str, str]] = {
    "scale_swe": ("AweAI-Team/Scale-SWE", "processed_to_upload.jsonl", "scale_swe"),
    "openswe_filtered": (
        "SWE-Lego/openswe_filtered_for_rl",
        "openswe_filtered_for_rl_22806.jsonl",
        "openswe_filtered",
    ),
    "swe_rebench_v2": ("nebius/SWE-rebench-V2", "data/train-00000-of-00001.parquet", "swe_rebench_v2"),
}

EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go",
    ".rs": "rust", ".java": "java", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_dir_name(instance_id: str) -> str:
    """Harbor identifies a task by its directory name, so it must be filesystem
    safe and unique. Keep the id readable; only replace what cannot appear."""
    return _SAFE.sub("__", str(instance_id)).strip("_") or "task"


def infer_language_from_patch(patch: str) -> str:
    """Fallback for datasets that ship no language column: majority extension
    among the files the patch touches."""
    counts: dict[str, int] = {}
    for m in re.finditer(r"^\+\+\+ b/(\S+)", patch or "", re.M):
        ext = os.path.splitext(m.group(1))[1].lower()
        lang = EXT_TO_LANG.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def read_records(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix == ".parquet":
        try:
            import pandas as pd
        except ModuleNotFoundError:
            raise SystemExit("pandas is required to read .parquet datasets")
        for _, row in pd.read_parquet(path).iterrows():
            yield {k: row[k] for k in row.index}
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def toml_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def write_task(out_dir: Path, rec: dict[str, Any], source: str) -> bool:
    instance_id = str(rec.get("instance_id") or "").strip()
    patch = str(rec.get("patch") or "")
    if not instance_id or not patch:
        return False

    language = str(rec.get("language") or "").strip().lower() or infer_language_from_patch(patch)
    task_dir = out_dir / safe_dir_name(instance_id)
    task_dir.mkdir(parents=True, exist_ok=True)

    # tags starts as [language]; the tagger fills positions 2-4. Writing
    # placeholders here would show up on the board as real tags.
    tags = f'[ "{toml_escape(language)}",]' if language else "[]"
    (task_dir / "task.toml").write_text(
        'schema_version = "1.3"\n'
        "artifacts = []\n\n"
        "[metadata]\n"
        f'tags = {tags}\n'
        f'dataset_source = "{toml_escape(source)}"\n'
        f'instance_id = "{toml_escape(instance_id)}"\n'
        f'repo = "{toml_escape(rec.get("repo") or "")}"\n',
        encoding="utf-8",
    )
    (task_dir / "instruction.md").write_text(
        str(rec.get("problem_statement") or ""), encoding="utf-8"
    )
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", choices=sorted(DATASETS), help="which dataset to import")
    ap.add_argument("--out", type=Path, required=True, help="output batch directory")
    ap.add_argument("--limit", type=int, default=0, help="stop after N tasks (0 = all)")
    ap.add_argument("--token", default=os.environ.get("HF_TOKEN"), help="HuggingFace token")
    args = ap.parse_args()

    repo, filename, source = DATASETS[args.dataset]
    try:
        from huggingface_hub import hf_hub_download
    except ModuleNotFoundError:
        raise SystemExit("huggingface_hub is required: pip install huggingface_hub")

    print(f"downloading {repo}/{filename}")
    local = Path(hf_hub_download(repo, filename, repo_type="dataset", token=args.token))

    args.out.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for rec in read_records(local):
        if write_task(args.out, rec, source):
            written += 1
        else:
            skipped += 1
        if args.limit and written >= args.limit:
            break

    print(f"wrote {written:,} tasks to {args.out} (skipped {skipped:,} without id/patch)")
    print("next: run the tagger over this directory to fill tags/category/scoring,")
    print(f"      check it with: python3 dashboard/check_task_dir.py {args.out}")
    print(f"      then link it onto the board: "
          f"ln -s $(pwd)/{args.out} artifacts/swe_tasks/{args.dataset}")


if __name__ == "__main__":
    sys.exit(main())
