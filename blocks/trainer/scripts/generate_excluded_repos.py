#!/usr/bin/env python3
"""
Extract repository names from the Hugging Face reference datasets.

The output contains one ``owner/repo`` per line and can be passed directly to
the conversion scripts through ``--exclude-repos-file``.

Usage:
    cd /path/to/LegoFlow/blocks/trainer
    artifacts/env/lf/bin/python scripts/generate_excluded_repos.py
    artifacts/env/lf/bin/python scripts/generate_excluded_repos.py -o /tmp/excluded.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BLOCK_DIR = Path(__file__).resolve().parent.parent
LOCAL_SRC = BLOCK_DIR / "repos" / "swe_data_process" / "src"
if LOCAL_SRC.exists():
    sys.path.insert(0, str(LOCAL_SRC))

from swe_data_process.utils import (
    DEFAULT_REFERENCE_DATASETS,
    load_reference_repos_from_hf,
)

DEFAULT_OUTPUT = BLOCK_DIR / "scripts" / "excluded_repos.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract repositories to exclude from Hugging Face reference datasets"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output file (default: {DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("Loading reference datasets from Hugging Face...")
    repos = load_reference_repos_from_hf(DEFAULT_REFERENCE_DATASETS)

    if not repos:
        print("WARNING: no repositories found; check the network and dataset names.")
        return

    sorted_repos = sorted(repos)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        f.write(
            "# Reference-dataset repositories (generated; do not edit manually)\n"
            "# Sources: SWE-bench_Verified, SWE-bench_Pro, SWE-bench_Multilingual\n"
            "# One owner/repo per line; pass this file with --exclude-repos-file\n"
        )
        for repo in sorted_repos:
            f.write(repo + "\n")

    print(f"\nSaved {len(sorted_repos)} repositories to {args.output}")


if __name__ == "__main__":
    main()
