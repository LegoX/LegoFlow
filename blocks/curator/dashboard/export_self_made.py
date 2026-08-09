#!/usr/bin/env python3
"""Export Curator-created tasks without rescoring or retagging them.

Every source must be either a complete task directory (or a directory containing
complete task directories) or a tarball containing ``task.toml``,
``instruction.md``, and ``solution/fix.patch`` for every task. Difficulty and
semantic tags are copied from task.toml into the dashboard's standard
``tags.jsonl`` format. No network or LLM access is used.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tarfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import tomllib
from metadata_records import (
    METADATA_SCHEMA_VERSION,
    SELF_MADE_SCORER_PROVENANCE,
    SELF_MADE_TAGGER_PROVENANCE,
    write_jsonl_atomic,
)

DASHBOARD_ROOT = Path(__file__).parent
OUTPUT_DIR = DASHBOARD_ROOT / "datasets" / "self_made"

HOME_DIR = Path(os.environ.get("LEGOFLOW_CURATOR_HOME", str(Path.home())))
DATA_ROOT = Path(
    os.environ.get("LEGOFLOW_CURATOR_DATA_ROOT", str(HOME_DIR / "LegoFlow-SWE-Curator"))
)
EXPORTS_ROOT = Path(
    os.environ.get("LEGOFLOW_CURATOR_EXPORTS_ROOT", str(DATA_ROOT / "exports_hf"))
)

DEFAULT_SOURCES = (
    EXPORTS_ROOT / "legoflow-curator-selfmade-260301-260721-non-top5k" / "tasks.tar.gz",
    EXPORTS_ROOT / "legoflow-curator-selfmade-260301-260622-top5k" / "tasks.tar.gz",
)


class ExportValidationError(ValueError):
    """Raised after all invalid source tasks have been collected."""


@dataclass(frozen=True)
class TaskPayload:
    task_id: str
    source: str
    instruction: str | None
    patch: str | None
    task_toml: str | None


def _task_root(member_name: str) -> tuple[PurePosixPath, str] | None:
    path = PurePosixPath(member_name.lstrip("./"))
    if path.name == "instruction.md":
        return path.parent, "instruction"
    if path.name == "task.toml":
        return path.parent, "task_toml"
    if len(path.parts) >= 2 and path.parts[-2:] == ("solution", "fix.patch"):
        return path.parent.parent, "patch"
    return None


def read_tarball(path: Path) -> list[TaskPayload]:
    pending: dict[PurePosixPath, dict[str, str]] = {}
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                matched = _task_root(member.name)
                if matched is None:
                    continue
                task_root, field = matched
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                pending.setdefault(task_root, {})[field] = extracted.read().decode(
                    "utf-8", errors="replace"
                )
    except (OSError, tarfile.TarError) as exc:
        raise ExportValidationError(
            f"{path}: could not read task tarball: {exc}"
        ) from exc

    return [
        TaskPayload(
            task_id=task_root.name,
            source=f"{path}:{task_root}",
            instruction=data.get("instruction"),
            patch=data.get("patch"),
            task_toml=data.get("task_toml"),
        )
        for task_root, data in sorted(pending.items(), key=lambda item: str(item[0]))
    ]


def read_directory(path: Path) -> list[TaskPayload]:
    task_roots: set[Path] = set()
    if (path / "task.toml").is_file():
        task_roots.add(path)
    for candidate in path.rglob("task.toml"):
        task_roots.add(candidate.parent)
    for candidate in path.rglob("instruction.md"):
        task_roots.add(candidate.parent)
    for candidate in path.rglob("fix.patch"):
        if candidate.parent.name == "solution":
            task_roots.add(candidate.parent.parent)

    payloads = []
    for task_root in sorted(task_roots):
        instruction_path = task_root / "instruction.md"
        patch_path = task_root / "solution" / "fix.patch"
        task_toml_path = task_root / "task.toml"
        payloads.append(
            TaskPayload(
                task_id=task_root.name,
                source=str(task_root),
                instruction=(
                    instruction_path.read_text(encoding="utf-8")
                    if instruction_path.is_file()
                    else None
                ),
                patch=patch_path.read_text(encoding="utf-8")
                if patch_path.is_file()
                else None,
                task_toml=(
                    task_toml_path.read_text(encoding="utf-8")
                    if task_toml_path.is_file()
                    else None
                ),
            )
        )
    return payloads


def read_source(path: Path) -> list[TaskPayload]:
    if not path.exists():
        raise ExportValidationError(f"{path}: source does not exist")
    payloads = read_directory(path) if path.is_dir() else read_tarball(path)
    if not payloads:
        raise ExportValidationError(f"{path}: no task directories found")
    return payloads


def patch_stats(patch: str) -> dict[str, int]:
    lines = patch.splitlines()
    return {
        "lines": sum(
            1
            for line in lines
            if (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        ),
        "hunks": sum(1 for line in lines if line.startswith("@@")),
        "files": sum(1 for line in lines if line.startswith("diff --git ")),
    }


def parse_task_metadata(payload: TaskPayload) -> dict[str, object]:
    errors: list[str] = []
    if payload.instruction is None:
        errors.append("missing instruction.md")
    if payload.patch is None:
        errors.append("missing solution/fix.patch")
    if payload.task_toml is None:
        errors.append("missing task.toml")
        raise ExportValidationError(", ".join(errors))

    try:
        task = tomllib.loads(payload.task_toml)
    except tomllib.TOMLDecodeError as exc:
        errors.append(f"invalid task.toml: {exc}")
        raise ExportValidationError(", ".join(errors)) from exc

    metadata = task.get("metadata")
    scoring = task.get("scoring")
    if not isinstance(metadata, dict):
        errors.append("missing [metadata] table")
        metadata = {}
    if not isinstance(scoring, dict):
        errors.append("missing [scoring] table")
        scoring = {}

    difficulty = metadata.get("difficulty")
    if not isinstance(difficulty, str) or difficulty.strip().lower() not in {
        "easy",
        "medium",
        "hard",
    }:
        errors.append("metadata.difficulty must be easy, medium, or hard")

    tags = metadata.get("tags")
    if (
        not isinstance(tags, list)
        or len(tags) != 4
        or not all(isinstance(tag, str) and tag.strip() for tag in tags)
    ):
        errors.append("metadata.tags must contain four non-empty strings")

    score = scoring.get("difficulty_score")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
    ):
        errors.append("scoring.difficulty_score must be a finite number")

    label = scoring.get("difficulty_label")
    if not isinstance(label, str) or label.strip().lower() not in {
        "easy",
        "medium",
        "hard",
    }:
        errors.append("scoring.difficulty_label must be easy, medium, or hard")
    elif (
        isinstance(difficulty, str)
        and difficulty.strip().lower() != label.strip().lower()
    ):
        errors.append("metadata.difficulty must match scoring.difficulty_label")

    if errors:
        raise ExportValidationError(", ".join(errors))

    normalized_tags = [tag.strip().lower() for tag in tags]
    return {
        "difficulty": difficulty.strip().lower(),
        "tags": normalized_tags,
        "difficulty_score": float(score),
        "difficulty_label": label.strip().lower(),
    }


def build_records(payloads: Iterable[TaskPayload]) -> tuple[list[dict], list[dict]]:
    tasks: list[dict] = []
    tags_records: list[dict] = []
    failures: list[str] = []
    seen_ids: set[str] = set()

    for payload in payloads:
        if payload.task_id in seen_ids:
            failures.append(
                f"{payload.task_id} ({payload.source}): duplicate instance_id"
            )
            continue
        seen_ids.add(payload.task_id)
        try:
            metadata = parse_task_metadata(payload)
        except ExportValidationError as exc:
            failures.append(f"{payload.task_id} ({payload.source}): {exc}")
            continue

        tags = metadata["tags"]
        common = {
            "instance_id": payload.task_id,
            "dataset_source": "self_made",
            "metadata_source": "task.toml",
            "metadata_schema_version": METADATA_SCHEMA_VERSION,
            "scorer_provenance": SELF_MADE_SCORER_PROVENANCE,
            "tagger_provenance": SELF_MADE_TAGGER_PROVENANCE,
        }
        tasks.append(
            {
                **common,
                "problem_statement": payload.instruction,
                "patch": payload.patch,
                "test_patch": "",
                "repo": payload.task_id.split("__", 1)[0]
                if "__" in payload.task_id
                else "",
                "language": tags[0],
            }
        )
        tags_records.append(
            {
                **common,
                "difficulty": metadata["difficulty"],
                "difficulty_score": metadata["difficulty_score"],
                "difficulty_label": metadata["difficulty_label"],
                "tags": tags,
                "bug_class": tags[3],
                "patch_stats": patch_stats(payload.patch or ""),
            }
        )

    if failures:
        details = "\n".join(f"  - {failure}" for failure in failures)
        raise ExportValidationError(
            f"self-made export rejected {len(failures)} task(s):\n{details}"
        )
    return tasks, tags_records


def export(
    sources: Iterable[Path] | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path]:
    selected_sources = tuple(Path(source) for source in (sources or DEFAULT_SOURCES))
    payloads: list[TaskPayload] = []
    source_failures: list[str] = []
    for source in selected_sources:
        try:
            payloads.extend(read_source(source))
        except ExportValidationError as exc:
            source_failures.append(str(exc))
    if source_failures:
        details = "\n".join(f"  - {failure}" for failure in source_failures)
        raise ExportValidationError(
            f"self-made export could not read {len(source_failures)} source(s):\n{details}"
        )

    tasks, tags_records = build_records(payloads)
    tasks_path = output_dir / "tasks.jsonl"
    tags_path = output_dir / "tags.jsonl"
    write_jsonl_atomic(tasks_path, tasks)
    write_jsonl_atomic(tags_path, tags_records)

    languages = Counter(record["language"] for record in tasks)
    print(f"exported {len(tasks)} self-made tasks to {tasks_path} and {tags_path}")
    for language, count in languages.most_common():
        print(f"  {language}: {count}")
    return tasks_path, tags_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        dest="sources",
        help="Task directory or task tarball; repeat to combine sources",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    try:
        export(args.sources, args.output_dir)
    except ExportValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
