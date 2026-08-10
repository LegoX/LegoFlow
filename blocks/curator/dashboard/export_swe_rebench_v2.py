#!/usr/bin/env python3
"""Export the SWE-rebench-V2 dataset (nebius/SWE-rebench-V2) to unified JSONL
for the dashboard tagging pipeline.

Downloads the parquet from HuggingFace and normalizes each record to the schema
used by tag_task_metadata.py: instance_id, problem_statement, patch, test_patch,
repo, language, dataset_source.
"""
import json
import os
from pathlib import Path

import pandas as pd
from huggingface_hub import hf_hub_download

DASHBOARD_ROOT = Path(__file__).parent
OUTPUT_DIR = DASHBOARD_ROOT / "datasets" / "swe_rebench_v2"

HF_REPO = "nebius/SWE-rebench-V2"
HF_FILE = "data/train-00000-of-00001.parquet"


def export():
    token = os.environ.get("HF_TOKEN")
    print(f"{'='*80}\nExport the SWE-rebench-V2 dataset\nsource: {HF_REPO}/{HF_FILE}\n{'='*80}")
    src = hf_hub_download(HF_REPO, HF_FILE, repo_type="dataset", token=token)
    print(f"download: {src}")

    df = pd.read_parquet(src, columns=["instance_id", "problem_statement", "patch", "test_patch", "repo", "language"])
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
                "language": str(rec.get("language") or "").lower(),
                "dataset_source": "swe_rebench_v2",
            }
            fout.write(json.dumps(unified, ensure_ascii=False) + "\n")
            count += 1

    print(f"{'='*80}\n✓ export complete: {count} tasks (skipped {skipped} with no patch)\noutput: {out_path}\n{'='*80}")


if __name__ == "__main__":
    export()
