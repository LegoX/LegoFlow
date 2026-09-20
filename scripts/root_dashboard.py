#!/usr/bin/env python3
"""Print the latest archived state for every LegoFlow block."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def latest_run(index: dict) -> dict:
    runs = index.get("runs")
    if not isinstance(runs, list) or not runs:
        return {}
    return runs[-1] if isinstance(runs[-1], dict) else {}


def duration(run: dict) -> str:
    try:
        start = datetime.fromisoformat(str(run["started_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(run["completed_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return "-"
    seconds = max(0, int((end - start).total_seconds()))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def rows(root: Path) -> list[tuple[str, str, str, str, str, str]]:
    config = load_yaml(root / "config.yaml")
    blocks = ((config.get("meta_info") or {}).get("blocks") or {})
    result = []
    for name in blocks:
        block = root / "blocks" / name
        run = latest_run(load_yaml(block / "artifacts" / "index.yaml"))
        result.append(
            (
                name,
                str(run.get("id", "-")),
                str(run.get("status", "never-run")),
                str(run.get("started_at", "-")),
                duration(run),
                str(run.get("notes", "") or "-").replace("\n", " "),
            )
        )
    return result


def output_rows(root: Path) -> list[tuple[str, str, str]]:
    config = load_yaml(root / "config.yaml")
    blocks = ((config.get("meta_info") or {}).get("blocks") or {})
    result = []
    for name in blocks:
        block = root / "blocks" / name
        outputs = ((load_yaml(block / "config.yaml").get("runtime_info") or {}).get("output") or {})
        for output_name, specification in outputs.items():
            if not isinstance(specification, dict) or not specification.get("path"):
                continue
            configured_path = str(specification["path"])
            path = Path(configured_path)
            if not path.is_absolute():
                path = block / path
            result.append((f"{name}.{output_name}", configured_path, "yes" if path.exists() else "no"))
    return result


def print_table(headers: tuple[str, ...], table_rows: list[tuple[str, ...]]) -> None:
    widths = [len(header) for header in headers]
    for row in table_rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    print(" | ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("-+-".join("-" * width for width in widths))
    for row in table_rows:
        print(" | ".join(value.ljust(width) for value, width in zip(row, widths)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    dashboard_rows = rows(args.root.resolve())
    print("LegoFlow dashboard")
    print_table(("block", "last_run", "status", "started_at", "duration", "notes"), dashboard_rows)
    print(f"blocks: {len(dashboard_rows)}")
    print()
    print("Configured output paths")
    configured_outputs = output_rows(args.root.resolve())
    print_table(("output", "path", "exists"), configured_outputs)
    print(f"outputs: {len(configured_outputs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
