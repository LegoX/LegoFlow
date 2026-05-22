#!/usr/bin/env python3
"""Extract verified SWE tasks from all languages into a unified outputs/ directory."""

import shutil
import sys
from pathlib import Path

LANGUAGES = ["py", "js", "ts", "go", "c", "cpp", "java", "rust"]
ARTIFACTS_ROOT = Path(__file__).parent / "artifacts" / "swe_tasks"
OUTPUT_DIR = Path(__file__).parent / "outputs"


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    total = 0
    stats = {}

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

        stats[lang] = copied
        total += copied

    print(f"\n{'Language':<10} {'Extracted':<10}")
    print("-" * 20)
    for lang in LANGUAGES:
        print(f"{lang:<10} {stats.get(lang, 0):<10}")
    print("-" * 20)
    print(f"{'TOTAL':<10} {total:<10}")
    print(f"\nOutput directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
