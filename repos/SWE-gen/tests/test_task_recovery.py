from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import swegen.cli as cli
from swegen.create.create import append_verifiable_task
from swegen.create.orchestrator import PRToHarborPipeline
from swegen.create.task_completion import (
    TaskCompletionState,
    classify_task_dir_state,
    task_has_completed_files,
)


runner = CliRunner()


def _write_task(
    root: Path,
    task_id: str,
    *,
    dockerfile: str,
    test_sh: str,
    fix_patch: str = "diff --git a/a b/a\n",
) -> Path:
    task_dir = root / task_id
    (task_dir / "environment").mkdir(parents=True, exist_ok=True)
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "solution").mkdir(parents=True, exist_ok=True)
    (task_dir / "environment" / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (task_dir / "tests" / "test.sh").write_text(test_sh, encoding="utf-8")
    (task_dir / "solution" / "fix.patch").write_text(fix_patch, encoding="utf-8")
    (task_dir / "environment" / "bug.patch").write_text("diff --git a/a b/a\n", encoding="utf-8")
    (task_dir / "instruction.md").write_text("instruction\n", encoding="utf-8")
    (task_dir / "task.toml").write_text("[task]\n", encoding="utf-8")
    return task_dir


def test_completion_state_ignores_embedded_todo_strings(tmp_path: Path) -> None:
    task_dir = _write_task(
        tmp_path,
        "owner__repo-123",
        dockerfile="FROM ubuntu:24.04\nRUN echo ok\n",
        test_sh=(
            "#!/bin/bash\n"
            "cp \"/tests/rebase_todo_test.go\" \"pkg/utils/rebase_todo_test.go\"\n"
            "# 6. tests/d3d12_sparse.c: adds todo_if(is_radv_device)\n"
            "sed -i 's|// TODO|done|' file.c\n"
        ),
    )
    assert classify_task_dir_state(task_dir) == TaskCompletionState.COMPLETE
    assert task_has_completed_files(task_dir)


def test_completion_state_marks_template_todo_comments_incomplete(tmp_path: Path) -> None:
    task_dir = _write_task(
        tmp_path,
        "owner__repo-123",
        dockerfile="FROM ubuntu:24.04\nRUN echo ok\n",
        test_sh="#!/bin/bash\n# TODO: Set environment variables if needed for tests\n",
    )
    assert classify_task_dir_state(task_dir) == TaskCompletionState.INCOMPLETE
    assert not task_has_completed_files(task_dir)


def test_append_verifiable_task_rejects_template_placeholders_and_allows_valid_todo_strings(
    tmp_path: Path,
) -> None:
    _write_task(
        tmp_path,
        "owner__repo-123",
        dockerfile="FROM ubuntu:24.04\nRUN echo ok\n",
        test_sh="#!/bin/bash\n# TODO: Fill in the actual test command\n",
    )
    append_verifiable_task(tmp_path, "owner__repo-123")
    verifiable = tmp_path / "verifiable_tasks.txt"
    assert not verifiable.exists()

    _write_task(
        tmp_path,
        "owner__repo-124",
        dockerfile="FROM ubuntu:24.04\nRUN echo ok\n",
        test_sh=(
            "#!/bin/bash\n"
            "cp \"/tests/rebase_todo_test.go\" \"pkg/utils/rebase_todo_test.go\"\n"
            "sed -i 's|// TODO|done|' file.c\n"
            "go test ./pkg/utils\n"
        ),
    )
    append_verifiable_task(tmp_path, "owner__repo-124")
    assert verifiable.read_text(encoding="utf-8").strip() == "owner__repo-124"


def test_create_task_scaffold_replaces_incomplete_generated_task_dir(tmp_path: Path) -> None:
    pipeline = PRToHarborPipeline("owner/repo", 123)
    task_dir = _write_task(
        tmp_path,
        "owner__repo-123",
        dockerfile="FROM ubuntu:24.04\nRUN echo ok\n",
        test_sh="#!/bin/bash\n# TODO: Fill in the actual test command\n",
    )
    stale_file = task_dir / "stale.txt"
    stale_file.write_text("stale\n", encoding="utf-8")

    recreated = pipeline.create_task_scaffold(tmp_path, overwrite=False)
    assert recreated == tmp_path / "owner__repo-123"
    assert recreated.exists()
    assert not stale_file.exists()


def test_batch_create_auto_revalidates_completed_task_not_in_verifiable(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "out"
    state_dir = tmp_path / "state"
    output.mkdir()
    state_dir.mkdir()
    task_id = "owner__repo-123"
    _write_task(
        output,
        task_id,
        dockerfile=(
            "FROM ubuntu:24.04\n"
            "RUN git clone https://example.com/repo src && \\\n"
            "    (git fetch --depth 1 origin deadbeef || git fetch --depth 1 origin "
            "\"+refs/pull/123/head:refs/remotes/origin/pr/123\")\n"
        ),
        test_sh="#!/bin/bash\ngo test ./...\n",
    )
    input_ids = tmp_path / "input.txt"
    input_ids.write_text("owner/repo:pr-123\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_preflight_github_token", lambda: None)
    monkeypatch.setattr(cli, "_preflight_llm_api", lambda: None)
    monkeypatch.setattr(cli, "run_nop_oracle", lambda **kwargs: (0, 1, {}))
    monkeypatch.setattr(cli, "score_task", lambda *args, **kwargs: {"difficulty": "medium"})
    monkeypatch.setattr(cli, "update_task_toml_difficulty", lambda *args, **kwargs: None)

    def _unexpected_run(*args, **kwargs):
        raise AssertionError("run_reversal_with_timeout should not be called for fast-path revalidation")

    monkeypatch.setattr(cli, "run_reversal_with_timeout", _unexpected_run)

    result = runner.invoke(
        cli.app,
        [
            "create",
            "--input-ids-file",
            str(input_ids),
            "--output",
            str(output),
            "--state-dir",
            str(state_dir),
            "--n-concurrent",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (output / "verifiable_tasks.txt").read_text(encoding="utf-8").strip() == task_id
