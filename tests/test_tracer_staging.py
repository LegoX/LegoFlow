"""Source switching and data ownership on the real Tracer preparation path."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from test_terminal_curator_integration import TRACER_SCRIPTS, make_task, staging, task_source


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setitem(sys.modules, "task_source", task_source)
    spec = importlib.util.spec_from_file_location("tracer_staging", TRACER_SCRIPTS / "stage_tasks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def edit_source(config_path, **source):
    config = yaml.safe_load(config_path.read_text())
    config["runtime_info"]["input"]["task_source"] = source
    config_path.write_text(yaml.safe_dump(config))
    return config


def test_same_basename_different_source_replaces_selection_without_rewriting_previous_version(staging):
    block, source, config_path, prepare = staging
    assert prepare().returncode == 0
    target = block / "artifacts/tasks/terminal-curator"
    previous = target.resolve()
    external = source.parent / "external/terminal-curator"
    make_task(external, "external-task")
    edit_source(config_path, provider="local", local_block="external", dataset_name=str(external))

    result = prepare()

    assert result.returncode == 0, result.stdout + result.stderr
    assert {path.name for path in target.iterdir()} == {"external-task"}
    assert target.resolve() != previous
    assert {path.name for path in previous.iterdir()} == {"tc-verified"}
    assert (previous / "tc-verified").resolve() == source / "tc-verified"


def test_shared_custom_target_and_dataset_label_are_honored(staging):
    block, source, config_path, prepare = staging
    config = yaml.safe_load(config_path.read_text())
    config["runtime_info"]["input"]["harbor_job"] = {
        "dataset": "shared-label", "dataset_path": "artifacts/tasks/shared-target",
    }
    config_path.write_text(yaml.safe_dump(config))
    assert prepare().returncode == 0
    target = block / "artifacts/tasks/shared-target"
    assert target.resolve().name == "shared-label"
    external = source.parent / "external"
    make_task(external, "different-task")
    edit_source(config_path, provider="local", local_block="external", dataset_name=str(external))

    result = prepare()

    assert result.returncode == 0, result.stdout + result.stderr
    assert {path.name for path in target.iterdir()} == {"different-task"}
    assert target.resolve().name == "shared-label"


def test_source_metadata_changes_create_a_new_version_and_local_content_remains_linked(staging):
    block, source, _, prepare = staging
    assert prepare().returncode == 0
    target = block / "artifacts/tasks/terminal-curator"
    previous = target.resolve()
    assert prepare().returncode == 0
    assert target.resolve() == previous
    instruction = source / "tc-verified/instruction.md"
    instruction.write_text("Updated mutable local task content.\n")
    assert prepare().returncode == 0
    assert target.resolve() != previous
    # Ordinary local pools retain link semantics; this is not a byte snapshot.
    assert (previous / "tc-verified/instruction.md").read_text() == instruction.read_text()


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("symlink", [False, True])
def test_preparation_preserves_unowned_real_datasets_and_external_links(staging, overwrite, symlink):
    block, source, _, prepare = staging
    target = block / "artifacts/tasks/terminal-curator"
    target.parent.mkdir(parents=True)
    original = source.parent / "user-dataset" if symlink else target
    make_task(original, "must-remain")
    marker = original / "private-notes.txt"
    marker.write_text("User data must be preserved.\n")
    if symlink:
        target.symlink_to(original, target_is_directory=True)

    result = prepare(*(["--overwrite"] if overwrite else []))

    assert result.returncode != 0
    assert "owned" in result.stderr
    assert marker.read_text() == "User data must be preserved.\n"
    assert (original / "must-remain/task.toml").is_file()
    assert target.is_symlink() == symlink


@pytest.mark.parametrize("alias", ["same", "source-parent", "source-child", "cache", "cache-child", "cache-parent"])
def test_source_target_and_cache_aliases_are_rejected_without_data_loss(staging, adapter, alias):
    block, source, config_path, _ = staging
    cache = adapter.cache_directory(block)
    target = {
        "same": source,
        "source-parent": source.parent,
        "source-child": source / "child",
        "cache": cache,
        "cache-child": cache / "child",
        "cache-parent": cache.parent,
    }[alias]
    options = adapter.settings(yaml.safe_load(config_path.read_text()))
    with pytest.raises(ValueError, match="overlap|alias"):
        adapter.stage(block, source, target, options, copy=False)
    assert (source / "tc-verified/task.toml").is_file()


def test_reusing_staging_itself_as_a_source_is_rejected(staging, adapter):
    block, _, config_path, prepare = staging
    assert prepare().returncode == 0
    target = block / "artifacts/tasks/terminal-curator"
    previous = target.resolve()
    options = adapter.settings(yaml.safe_load(config_path.read_text()))
    for source in (target, previous, previous.parent):
        with pytest.raises(ValueError, match="overlap|alias"):
            adapter.stage(block, source, target, options, copy=False)
    assert target.resolve() == previous


def test_hf_copy_survives_snapshot_cleanup_and_does_not_reuse_a_local_selection(staging, adapter):
    block, source, config_path, prepare = staging
    assert prepare().returncode == 0
    target = block / "artifacts/tasks/terminal-curator"
    config = edit_source(config_path, provider="huggingface", dataset_name="example/terminal-curator")
    options = adapter.settings(config)
    assert adapter.reuse_copy(block, target, options) is None
    snapshot = source.parent / "download"
    task = make_task(snapshot, "hf-task")
    payload = source.parent / "hub-asset"
    payload.write_text("An asset stored in the hub cache.\n")
    (task / "environment/asset").symlink_to(payload)

    fixed = adapter.stage(block, snapshot, target, options, copy=True)
    shutil.rmtree(snapshot)
    payload.unlink()

    assert (fixed / "hf-task/environment/asset").read_text() == "An asset stored in the hub cache.\n"
    assert not (fixed / "hf-task/environment/asset").is_symlink()
    assert adapter.reuse_copy(block, target, options) == fixed
    other_options = dict(options, dataset_name="other/terminal-curator")
    assert adapter.reuse_copy(block, target, other_options) is None
    (fixed / "hf-task/instruction.md").write_text("Changed cached copy.\n")
    assert adapter.reuse_copy(block, target, options) is None


def test_hf_prepare_uses_the_matching_owned_cache_and_redownloads_changed_source(staging, monkeypatch):
    block, source, config_path, prepare = staging
    fake_module = source.parent / "fake-module"
    fake_module.mkdir()
    (fake_module / "huggingface_hub.py").write_text(
        'from pathlib import Path\n'
        'import os\n'
        'def snapshot_download(repo_id, local_dir, **kwargs):\n'
        '    task = Path(local_dir) / repo_id.split("/")[0]\n'
        '    (task / "environment").mkdir(parents=True)\n'
        '    (task / "tests").mkdir()\n'
        '    (task / "task.toml").write_text("version = \\"1.0\\"\\n")\n'
        '    (task / "instruction.md").write_text(repo_id)\n'
        '    with open(os.environ["TEST_HF_DOWNLOADS"], "a") as stream: stream.write(repo_id + "\\n")\n'
    )
    downloads = source.parent / "download-events"
    monkeypatch.setenv("PYTHONPATH", str(fake_module))
    monkeypatch.setenv("TEST_HF_DOWNLOADS", str(downloads))
    edit_source(config_path, provider="huggingface", dataset_name="first/terminal-curator")
    for _ in range(2):
        result = prepare()
        assert result.returncode == 0, result.stdout + result.stderr
    assert downloads.read_text().splitlines() == ["first/terminal-curator"]
    edit_source(config_path, provider="huggingface", dataset_name="second/terminal-curator")
    result = prepare()
    assert result.returncode == 0, result.stdout + result.stderr
    assert downloads.read_text().splitlines() == ["first/terminal-curator", "second/terminal-curator"]
    assert {p.name for p in (block / "artifacts/tasks/terminal-curator").iterdir()} == {"second"}
    assert not list((block / "artifacts/tasks/.downloads").iterdir())


def test_result_file_pins_a_version_when_later_preparation_advances_the_pointer(staging):
    block, source, _, prepare = staging
    result_file = source.parent / "prepared.json"
    result = prepare("--result-file", str(result_file))
    assert result.returncode == 0, result.stdout + result.stderr
    fixed = Path(json.loads(result_file.read_text())["dataset_path"])
    assert fixed.is_absolute()
    (source / "verifiable_tasks.txt").write_text("tc-unverified\n")
    assert prepare().returncode == 0
    assert (block / "artifacts/tasks/terminal-curator").resolve() != fixed
    assert {p.name for p in fixed.iterdir()} == {"tc-verified"}


def test_start_passes_the_invocation_version_to_harbor_after_public_pointer_moves(staging):
    block, source, config_path, _ = staging
    for name in ("start.sh", "resolve_exclude_tasks.py"):
        shutil.copy2(TRACER_SCRIPTS / name, block / "scripts" / name)
    root_scripts = block.parents[1] / "scripts"
    root_scripts.mkdir()
    shutil.copy2(TRACER_SCRIPTS.parents[2] / "scripts/redact_archive_config.py", root_scripts)
    defaults = yaml.safe_load((TRACER_SCRIPTS.parent / "config.yaml").read_text())
    current = yaml.safe_load(config_path.read_text())
    defaults["meta_info"] = current["meta_info"]
    defaults["runtime_info"]["input"]["task_source"] = current["runtime_info"]["input"]["task_source"]
    defaults["runtime_info"]["input"]["litellm_proxy"]["config_template"] = "proxy.example.yaml"
    defaults["runtime_info"]["input"]["llm_api"] = {"model": "openai/fake", "api_key": "fake-fixture"}
    defaults["runtime_info"]["input"]["env_extra"] = {}
    config_path.write_text(yaml.safe_dump(defaults))
    harbor = block / "repos/harbor"
    (harbor / "proxy.example.yaml").write_text("{}\n")
    (harbor / "scripts/serve_llm").mkdir(parents=True)
    (harbor / "scripts/serve_llm/trajectory_logger.py").write_text("# Fixture logger.\n")
    next_batch = source.parent / "next-batch"
    make_task(next_batch, "future-task")
    # Preflight runs after staging. Simulate another invocation moving only
    # the public pointer before this invocation constructs the Harbor command.
    (block / "scripts/dryrun.sh").write_text(
        '#!/bin/bash\n'
        'python3 - "$TEST_STAGING_POINTER" "$TEST_NEXT_BATCH" <<\'PY\'\n'
        'from pathlib import Path\nimport sys\n'
        'pointer = Path(sys.argv[1])\npointer.unlink()\npointer.symlink_to(sys.argv[2])\n'
        'PY\n'
    )
    env = {**os.environ, "TRAJGEN_CONFIG": str(config_path), "TRAJGEN_PREPARE_TASKS": "1",
           "TEST_STAGING_POINTER": str(block / "artifacts/tasks/terminal-curator"),
           "TEST_NEXT_BATCH": str(next_batch)}
    for key in ("OPENAI_MODEL", "ANTHROPIC_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_BASE_URL", "OPENAI_BASE_URL"):
        env.pop(key, None)

    result = subprocess.run(["bash", str(block / "scripts/start.sh"), "--dry-run-command"],
                            env=env, cwd=block, text=True, capture_output=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    command = next(line for line in result.stdout.splitlines() if line.startswith("uv run harbor run --path "))
    import shlex
    fixed = Path(shlex.split(command.split(" --mounts-json ", 1)[0])[5])
    assert fixed != next_batch
    assert ".legoflow-staging-v1" in fixed.parts
    assert {p.name for p in fixed.iterdir()} == {"tc-verified"}
    assert (block / "artifacts/tasks/terminal-curator").resolve() == next_batch
    proxy_configs = list((block / "artifacts/litellm").glob("*/*.yaml"))
    assert len(proxy_configs) == 1
    assert proxy_configs[0].stat().st_mode & 0o777 == 0o600
    snapshot = next((block / "artifacts/jobs").glob("*/config.yaml"))
    assert yaml.safe_load(snapshot.read_text())["runtime_info"]["input"]["llm_api"]["api_key"] == ""
