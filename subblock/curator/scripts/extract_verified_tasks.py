#!/usr/bin/env python3
"""Extract verified SWE tasks from all languages into a unified artifacts directory."""

import shutil
import sys
from pathlib import Path

LANGUAGES = ["py", "js", "ts", "go", "c", "cpp", "java", "rust"]
BLOCK_DIR = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = BLOCK_DIR / "artifacts" / "swe_tasks"
OUTPUT_DIR = BLOCK_DIR / "artifacts" / "merged_swe_tasks"


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    total = 0
    stats = {}
    extracted_ids = []

    for lang in LANGUAGES:
        lang_dir = ARTIFACTS_ROOT / f"{lang}-cc"
        vt_file = lang_dir / "verifiable_tasks.txt"
        if not vt_file.exists():
            stats[lang] = 0
            continue

        task_ids = [line.strip() for line in vt_file.read_text().splitlines() if line.strip()]
        copied = 0
        for task_id in task_ids:
            src = lang_dir / task_id
            dst = OUTPUT_DIR / task_id
            if not src.is_dir():
                print(f"  WARN: {lang}/{task_id} not found, skipping")
                continue
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied += 1
            extracted_ids.append(task_id)

        stats[lang] = copied
        total += copied

    manifest_file = OUTPUT_DIR / "verifiable_tasks.txt"
    manifest_file.write_text("\n".join(sorted(extracted_ids)) + "\n")

    print(f"\n{'Language':<10} {'Extracted':<10}")
    print("-" * 20)
    for lang in LANGUAGES:
        print(f"{lang:<10} {stats.get(lang, 0):<10}")
    print("-" * 20)
    print(f"{'TOTAL':<10} {total:<10}")
    print(f"\nOutput directory: {OUTPUT_DIR}")
    print(f"Manifest written: {manifest_file}")


if __name__ == "__main__":
    main()
