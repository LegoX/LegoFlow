#!/usr/bin/env python3
"""Export the Scale-SWE dataset (AweAI-Team/Scale-SWE) to unified JSONL for the
dashboard tagging pipeline.

Downloads the single JSONL from HuggingFace and normalizes each record to the
schema used by tag_task_metadata.py: instance_id, problem_statement, patch,
test_patch, repo, language, dataset_source.
"""
import json
import os
from pathlib import Path

from huggingface_hub import hf_hub_download

DASHBOARD_ROOT = Path(__file__).parent
OUTPUT_DIR = DASHBOARD_ROOT / "datasets" / "scale_swe"

HF_REPO = "AweAI-Team/Scale-SWE"
HF_FILE = "processed_to_upload.jsonl"


def export():
    token = os.environ.get("HF_TOKEN")
    print(f"{'='*80}\nExport the Scale-SWE dataset\nsource: {HF_REPO}/{HF_FILE}\n{'='*80}")
    src = hf_hub_download(HF_REPO, HF_FILE, repo_type="dataset", token=token)
    print(f"download: {src}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "tasks.jsonl"

    count = 0
    skipped = 0
    with open(src) as fin, out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            rec = json.loads(line)
            patch = rec.get("patch") or ""
            if not patch:
                skipped += 1
                continue
            unified = {
                "instance_id": str(rec["instance_id"]),
                "problem_statement": str(rec.get("problem_statement") or ""),
                "patch": str(patch),
                "test_patch": str(rec.get("f2p_patch") or ""),
                "repo": str(rec.get("repo") or ""),
                "language": str(rec.get("language") or "").lower(),
                "dataset_source": "scale_swe",
            }
            fout.write(json.dumps(unified, ensure_ascii=False) + "\n")
            count += 1

    print(f"{'='*80}\n✓ export complete: {count} tasks (skipped {skipped} with no patch)\noutput: {out_path}\n{'='*80}")


if __name__ == "__main__":
    export()
