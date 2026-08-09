from __future__ import annotations

import json
import socket
import sys
import tarfile
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).resolve().parents[1]
CURATOR_DIR = DASHBOARD_DIR.parent
sys.path.insert(0, str(DASHBOARD_DIR))

import dataset_registry
import export_self_made
import progress_monitor_multi
from metadata_records import annotate_external_tags

VALID_TASK_TOML = """\
[metadata]
difficulty = "medium"
tags = ["python", "backend", "api", "incomplete-validation"]

[scoring]
difficulty_score = 6.25
difficulty_label = "medium"
"""


def write_task(
    root: Path, task_id: str, task_toml: str | None = VALID_TASK_TOML
) -> Path:
    task = root / task_id
    (task / "solution").mkdir(parents=True)
    (task / "instruction.md").write_text("Fix request validation.\n", encoding="utf-8")
    (task / "solution" / "fix.patch").write_text(
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-return False\n"
        "+return True\n",
        encoding="utf-8",
    )
    if task_toml is not None:
        (task / "task.toml").write_text(task_toml, encoding="utf-8")
    return task


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_self_made_export_reads_task_toml_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    write_task(source, "owner__repo-123")

    def reject_network(*_args, **_kwargs):
        raise AssertionError("self-made export attempted network access")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    output = tmp_path / "output"
    tasks_path, tags_path = export_self_made.export([source], output)

    task = read_jsonl(tasks_path)[0]
    metadata = read_jsonl(tags_path)[0]
    assert task["language"] == "python"
    assert metadata["difficulty"] == "medium"
    assert metadata["difficulty_score"] == 6.25
    assert metadata["difficulty_label"] == "medium"
    assert metadata["tags"] == [
        "python",
        "backend",
        "api",
        "incomplete-validation",
    ]
    assert metadata["bug_class"] == "incomplete-validation"
    assert metadata["metadata_source"] == "task.toml"
    assert metadata["scorer_provenance"]["mode"] == "precomputed"
    assert metadata["tagger_provenance"]["mode"] == "precomputed"


def test_self_made_export_reads_task_toml_from_tarball(tmp_path: Path) -> None:
    source = tmp_path / "source"
    task = write_task(source, "owner__repo-456")
    tarball = tmp_path / "tasks.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(task, arcname=f"tasks/{task.name}")

    _, tags_path = export_self_made.export([tarball], tmp_path / "output")

    assert read_jsonl(tags_path)[0]["instance_id"] == "owner__repo-456"


def test_self_made_export_lists_all_tasks_with_missing_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_task(source, "missing-toml", task_toml=None)
    write_task(
        source,
        "missing-score",
        task_toml="""\
[metadata]
difficulty = "easy"
tags = ["python", "cli", "parser", "wrong-default"]

[scoring]
difficulty_label = "easy"
""",
    )
    output = tmp_path / "output"

    with pytest.raises(export_self_made.ExportValidationError) as exc_info:
        export_self_made.export([source], output)

    message = str(exc_info.value)
    assert "missing-toml" in message
    assert "missing task.toml" in message
    assert "missing-score" in message
    assert "scoring.difficulty_score" in message
    assert not (output / "tasks.jsonl").exists()
    assert not (output / "tags.jsonl").exists()


def test_self_made_export_rejects_inconsistent_difficulty(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_task(
        source,
        "difficulty-mismatch",
        task_toml=VALID_TASK_TOML.replace(
            'difficulty_label = "medium"',
            'difficulty_label = "hard"',
        ),
    )

    with pytest.raises(
        export_self_made.ExportValidationError,
        match="metadata.difficulty must match scoring.difficulty_label",
    ):
        export_self_made.export([source], tmp_path / "output")


def test_renderer_reads_and_validates_tags_jsonl_only(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "self_made"
    dataset_dir.mkdir()
    (dataset_dir / "tasks.jsonl").write_text("not json and intentionally unused\n")
    record = {
        "instance_id": "task-1",
        "difficulty_score": 3.5,
        "difficulty_label": "easy",
        "tags": ["python", "backend", "api", "wrong-default"],
        "patch_stats": {"lines": 2, "hunks": 1, "files": 1},
    }
    (dataset_dir / "tags.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    aggregate = progress_monitor_multi.aggregate_dataset("self_made", tmp_path)

    assert aggregate["total"] == 1
    assert aggregate["languages"] == {"python": 1}
    assert aggregate["bug_classes"] == {"wrong-default": 1}

    output_html = tmp_path / "index.html"
    rendered = progress_monitor_multi.render_html([aggregate], output_html)
    assert "<title>legoflow-databoard</title>" in rendered
    assert "LegoFlow Curator Instances" in rendered
    assert 'class="ds-item active"' in rendered

    (dataset_dir / "tags.jsonl").write_text("{broken json\n", encoding="utf-8")
    with pytest.raises(progress_monitor_multi.DashboardDataError, match="invalid JSON"):
        progress_monitor_multi.aggregate_dataset("self_made", tmp_path)


def test_external_tag_provenance_is_added_atomically(tmp_path: Path) -> None:
    tags_path = tmp_path / "tags.jsonl"
    tags_path.write_text(
        json.dumps(
            {
                "instance_id": "external-1",
                "difficulty_score": 7.0,
                "difficulty_label": "medium",
                "tags": ["go", "backend", "http", "missing-fallback"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert annotate_external_tags(tags_path, "swe_rebench") == 1
    record = read_jsonl(tags_path)[0]
    assert record["metadata_source"] == "canonical_dashboard_tagger"
    assert record["metadata_schema_version"] == "1.0"
    assert record["scorer_provenance"]["mode"] == "computed_during_dataset_preparation"
    assert record["tagger_provenance"]["mode"] == "computed_during_dataset_preparation"


def test_registry_is_canonical_and_documented() -> None:
    expected = (
        "self_made",
        "swe_rebench",
        "swe_rebench_v2",
        "openswe_filtered",
        "scale_swe",
    )
    assert dataset_registry.DATASET_IDS == expected
    assert progress_monitor_multi.DATASETS is dataset_registry.DATASETS
    assert (
        dataset_registry.DATASETS_BY_ID["self_made"].display_name
        == "LegoFlow Curator Instances"
    )

    documentation = (
        DASHBOARD_DIR / "README.md",
        CURATOR_DIR / "docs" / "content" / "docs" / "dashboard.mdx",
        CURATOR_DIR
        / ".claude"
        / "plugins"
        / "curator-plugin"
        / "skills"
        / "dashboard"
        / "SKILL.md",
    )
    for path in documentation:
        text = path.read_text(encoding="utf-8")
        for dataset_id in expected:
            assert f"`{dataset_id}`" in text, f"{path} omits {dataset_id}"

    pipeline = (DASHBOARD_DIR / "run_pipeline.sh").read_text(encoding="utf-8")
    assert "dataset_registry.py" in pipeline
