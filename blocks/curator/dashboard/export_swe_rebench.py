#!/usr/bin/env python3
"""Export the SWE-rebench dataset (nebius/SWE-rebench) to unified JSONL for the
dashboard tagging pipeline.

Uses the curated `filtered` split. Normalizes each record to the schema used by
tag_task_metadata.py: instance_id, problem_statement, patch, test_patch, repo,
language, dataset_source. Language is inferred from the patch (no language
column in this dataset).
"""
import json
import os
from collections import Counter
from pathlib import Path

import pandas as pd
from huggingface_hub import hf_hub_download

from metadata_records import METADATA_SCHEMA_VERSION

DASHBOARD_ROOT = Path(__file__).parent
OUTPUT_DIR = DASHBOARD_ROOT / "datasets" / "swe_rebench"

HF_REPO = "nebius/SWE-rebench"
HF_FILE = "data/filtered-00000-of-00001.parquet"

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
    return counts.most_common(1)[0][0] if counts else ""


def export():
    token = os.environ.get("HF_TOKEN")
    print(f"{'='*80}\nExport the SWE-rebench dataset\nsource: {HF_REPO}/{HF_FILE}\n{'='*80}")
    src = hf_hub_download(HF_REPO, HF_FILE, repo_type="dataset", token=token)
    print(f"download: {src}")

    df = pd.read_parquet(src, columns=["instance_id", "problem_statement", "patch", "test_patch", "repo"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "tasks.jsonl"

    count = 0
    skipped = 0
    with out_path.open("w", encoding="utf-8") as fout:
        for _, rec in df.iterrows():
            patch = rec.get("patch") or ""
            if not patch:
                skipped += 1
                continue
            unified = {
                "instance_id": str(rec["instance_id"]),
                "problem_statement": str(rec.get("problem_statement") or ""),
                "patch": str(patch),
                "test_patch": str(rec.get("test_patch") or ""),
                "repo": str(rec.get("repo") or ""),
                "language": infer_language_from_patch(str(patch)),
                "dataset_source": "swe_rebench",
                "metadata_source": "canonical_dashboard_tagger",
                "metadata_schema_version": METADATA_SCHEMA_VERSION,
            }
            fout.write(json.dumps(unified, ensure_ascii=False) + "\n")
            count += 1

    print(f"{'='*80}\n✓ export complete: {count} tasks (skipped {skipped} with no patch)\noutput: {out_path}\n{'='*80}")


if __name__ == "__main__":
    export()
