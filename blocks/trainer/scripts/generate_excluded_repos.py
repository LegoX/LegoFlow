#!/usr/bin/env python3
"""
Copy the maintained exclusion list from LegoFlow-Trace-Crafter.

The output contains one ``owner/repo`` per line and can be passed directly to
the conversion scripts through ``--exclude-repos-file``.

Usage:
    cd /path/to/LegoFlow/blocks/trainer
    artifacts/env/lf/bin/python scripts/generate_excluded_repos.py
    artifacts/env/lf/bin/python scripts/generate_excluded_repos.py -o /tmp/excluded.txt
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

BLOCK_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = (
    BLOCK_DIR / "repos" / "LegoFlow-Trace-Crafter" / "artifacts" / "excluded_repos.txt"
)
DEFAULT_OUTPUT = BLOCK_DIR / "scripts" / "excluded_repos.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy repositories to exclude from LegoFlow-Trace-Crafter"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"source file (default: {DEFAULT_SOURCE})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.source.is_file():
        raise SystemExit(f"ERROR: exclusion list not found: {args.source}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.source, args.output)
    n_repos = sum(
        1
        for line in args.output.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    print(f"Copied {n_repos} repositories from {args.source} to {args.output}")


if __name__ == "__main__":
    main()
