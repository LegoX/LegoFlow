#!/usr/bin/env python3
"""Emit R2 upload manifest rows from generated trial-fact JSONL shards.

Output format:
  <r2_key>\t<local_trajectory_path>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial_facts", type=Path, nargs="+")
    parser.add_argument("--offset", type=int, default=0, help="Eligible rows to skip before emitting.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows to emit. 0 means no limit.")
    return parser.parse_args()


def safe_slug(value: Any, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[^A-Za-z0-9_.=-]+", "-", text).strip("-._")
    return text[:180] or fallback


def make_r2_key(row: dict[str, Any]) -> str:
    existing = str(row.get("r2_key") or "")
    if existing.startswith("trajs/"):
        return existing
    job = safe_slug(row.get("job") or row.get("dataset") or "unknown-job")
    instance = safe_slug(row.get("instance_id") or row.get("task_name") or "unknown-instance")
    traj = safe_slug(row.get("trial") or row.get("index") or row.get("id") or "record")
    return f"trajs/{job}/{instance}/{traj}.json"


def main() -> int:
    args = parse_args()
    emitted = 0
    eligible = 0
    for trial_fact in args.trial_facts:
        with trial_fact.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError:
                    continue
                r2_key = make_r2_key(row)
                path = Path(str(row.get("trajectory_path") or ""))
                if not r2_key.startswith("trajs/") or not path.is_file():
                    continue
                if eligible < max(0, args.offset):
                    eligible += 1
                    continue
                eligible += 1
                print(f"{r2_key}\t{path}")
                emitted += 1
                if args.limit > 0 and emitted >= args.limit:
                    return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
