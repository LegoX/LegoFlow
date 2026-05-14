#!/usr/bin/env python3
"""Read a scalar value from config.yaml for shell scripts."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("root", choices=["meta_info", "runtime_input"])
    parser.add_argument("path", help="Dot-separated key path, e.g. training.output_dir")
    parser.add_argument("--default", default="")
    return parser.parse_args()


def lookup(root: Any, path: str, default: str) -> Any:
    cur = root
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return default if cur is None else cur


def main() -> None:
    args = parse_args()
    with args.config.open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if not isinstance(config, dict):
        print(args.default)
        return

    if args.root == "meta_info":
        root = config.get("meta_info", {})
    else:
        runtime_info = config.get("runtime_info", {})
        root = runtime_info.get("input", {}) if isinstance(runtime_info, dict) else {}

    value = lookup(root, args.path, args.default)
    if isinstance(value, bool):
        print(str(value).lower())
    elif value is None:
        print("")
    else:
        print(value)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: failed to read config value: {exc}", file=sys.stderr)
        raise
