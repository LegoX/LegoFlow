#!/usr/bin/env python3
"""Resolve the configured Harbor task source consistently for every tracer script."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import yaml


def resolve(config: dict) -> dict:
    source = ((config.get("runtime_info") or {}).get("input") or {}).get("task_source") or {}
    if not isinstance(source, dict):
        raise ValueError("runtime_info.input.task_source must be a mapping")
    provider = source.get("provider", "local")
    if not isinstance(provider, str) or provider not in {"local", "huggingface"}:
        raise ValueError(f"unsupported task_source.provider: {provider!r}")
    local_block = source.get("local_block", "curator")
    if provider == "local" and (
        not isinstance(local_block, str) or local_block not in {"curator", "terminal-curator", "external"}
    ):
        raise ValueError(f"unsupported task_source.local_block: {local_block!r}")
    terminal = provider == "local" and local_block == "terminal-curator"
    key = "terminal_tasks_dir" if terminal else "dataset_name"
    dataset_name = source.get(key)
    if not isinstance(dataset_name, str) or not dataset_name.strip():
        raise ValueError(f"runtime_info.input.task_source.{key} is empty")
    # Give the terminal producer its own staging directory even if a custom SWE
    # source also happens to be called 'tasks'. Existing SWE/HF names stay stable.
    dataset_id = "terminal-curator" if terminal else Path(dataset_name.rstrip("/")).name
    if not dataset_id or dataset_id in {".", ".."}:
        raise ValueError("task source must have a usable dataset directory name")
    return {
        "provider": provider,
        "local_block": local_block,
        "dataset_name": dataset_name,
        "dataset_id": dataset_id,
        "manifest_required": terminal,
        "producer": local_block if provider == "local" and local_block != "external" else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--field", choices=("provider", "local_block", "dataset_name", "dataset_id", "manifest_required", "producer"))
    args = parser.parse_args()
    try:
        data = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
        result = resolve(data)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        print(f"ERROR: invalid YAML in {args.config}{location}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.field:
        value = result[args.field]
        print(str(value).lower() if isinstance(value, bool) else value)
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
