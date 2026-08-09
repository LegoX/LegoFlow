#!/usr/bin/env python3
"""Shared metadata schema and provenance helpers for dashboard records."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

METADATA_SCHEMA_VERSION = "1.0"
CANONICAL_TAGGER = "../repos/legoflow-curator/tools/tag_task_metadata.py"

SELF_MADE_SCORER_PROVENANCE = {
    "tool": "legoflow_curator.scoring",
    "mode": "precomputed",
    "source": "task.toml:scoring",
}
SELF_MADE_TAGGER_PROVENANCE = {
    "tool": "LegoFlow-SWE-Curator",
    "mode": "precomputed",
    "source": "task.toml:metadata.tags",
}


def external_scorer_provenance() -> dict[str, str]:
    return {
        "tool": CANONICAL_TAGGER,
        "mode": "computed_during_dataset_preparation",
    }


def external_tagger_provenance() -> dict[str, str]:
    return {
        "tool": CANONICAL_TAGGER,
        "mode": "computed_during_dataset_preparation",
    }


def write_jsonl_atomic(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Replace a JSONL file only after every record has been prepared."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            for record in records:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def annotate_external_tags(path: Path, dataset_id: str) -> int:
    """Add explicit canonical-tagger provenance to an external dataset."""
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise TypeError(f"{path}:{line_number}: expected a JSON object")
            record.setdefault("metadata_source", "canonical_dashboard_tagger")
            record.setdefault("metadata_schema_version", METADATA_SCHEMA_VERSION)
            record.setdefault("scorer_provenance", external_scorer_provenance())
            record.setdefault("tagger_provenance", external_tagger_provenance())
            record.setdefault("dataset_source", dataset_id)
            records.append(record)
    write_jsonl_atomic(path, records)
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Annotate dashboard metadata provenance"
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--tags-file", required=True, type=Path)
    args = parser.parse_args()
    count = annotate_external_tags(args.tags_file, args.dataset)
    print(f"annotated {count} records in {args.tags_file}")


if __name__ == "__main__":
    main()
