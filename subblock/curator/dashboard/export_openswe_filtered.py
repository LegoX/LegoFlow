#!/usr/bin/env python3
"""Export the OpenSWE-filtered dataset (SWE-Lego/openswe_filtered_for_rl) to
unified JSONL for the dashboard tagging pipeline.

Downloads the single JSONL from HuggingFace and normalizes each record to the
schema used by tag_task_metadata.py: instance_id, problem_statement, patch,
test_patch, repo, language, dataset_source.
"""
import json
import os
from pathlib import Path

from huggingface_hub import hf_hub_download

DASHBOARD_ROOT = Path(__file__).parent
OUTPUT_DIR = DASHBOARD_ROOT / "datasets" / "openswe_filtered"

HF_REPO = "SWE-Lego/openswe_filtered_for_rl"
HF_FILE = "openswe_filtered_for_rl_22806.jsonl"

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
    from collections import Counter
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
    print(f"{'='*80}\n导出 OpenSWE-filtered 数据集\n源: {HF_REPO}/{HF_FILE}\n{'='*80}")
    src = hf_hub_download(HF_REPO, HF_FILE, repo_type="dataset", token=token)
    print(f"下载: {src}")

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
                "instance_id": rec["instance_id"],
                "problem_statement": rec.get("problem_statement") or "",
                "patch": patch,
                "test_patch": rec.get("test_patch") or "",
                "repo": rec.get("repo") or "",
                "language": infer_language_from_patch(patch),
                "dataset_source": "openswe_filtered",
            }
            fout.write(json.dumps(unified, ensure_ascii=False) + "\n")
            count += 1

    print(f"{'='*80}\n✓ 导出完成: {count} 个任务 (跳过 {skipped} 个无 patch)\n输出: {out_path}\n{'='*80}")


if __name__ == "__main__":
    export()
