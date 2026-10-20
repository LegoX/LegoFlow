"""Exercise producer selection and actual tracer staging without network or Docker."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
TRACER_SCRIPTS = ROOT / "blocks/tracer/scripts"
spec = importlib.util.spec_from_file_location("tracer_task_source", TRACER_SCRIPTS / "task_source.py")
task_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_source)


def config_source(**values):
    return {"runtime_info": {"input": {"task_source": values}}}


def test_legacy_swe_source_keeps_its_path_and_staging_name():
    resolved = task_source.resolve(config_source(provider="local", dataset_name="../curator/artifacts/merged_swe_tasks"))
    assert resolved["local_block"] == "curator"
    assert resolved["dataset_id"] == "merged_swe_tasks"
    assert not resolved["manifest_required"]


def test_terminal_selection_uses_its_own_path_and_manifest():
    resolved = task_source.resolve(config_source(
        provider="local", local_block="terminal-curator", dataset_name="../curator/old",
        terminal_tasks_dir="../terminal-curator/artifacts/tasks",
    ))
    assert resolved["dataset_name"] == "../terminal-curator/artifacts/tasks"
    assert resolved["dataset_id"] == "terminal-curator"
    assert resolved["manifest_required"]


def test_huggingface_does_not_select_a_local_producer():
    resolved = task_source.resolve(config_source(
        provider="huggingface", local_block="terminal-curator", dataset_name="example/harbor-tasks",
    ))
    assert resolved["dataset_name"] == "example/harbor-tasks"
    assert resolved["producer"] == ""
    assert not resolved["manifest_required"]


@pytest.mark.parametrize("source", [
    {"provider": "unknown", "dataset_name": "tasks"},
    {"provider": "local", "local_block": "unknown", "dataset_name": "tasks"},
    *({"provider": "local", "local_block": value, "dataset_name": "tasks"}
      for value in (False, True, None, "", 0, [], {})),
    {"provider": "local", "local_block": "terminal-curator", "dataset_name": "old-swe"},
])
def test_invalid_selection_fails_instead_of_using_swe(source):
    with pytest.raises(ValueError):
        task_source.resolve(config_source(**source))


def make_task(root: Path, name: str) -> Path:
    task = root / name
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_text("A test fixture task.\n")
    (task / "task.toml").write_text('version = "1.0"\n')
    return task


@pytest.fixture
def staging(tmp_path):
    block = tmp_path / "blocks/tracer"
    scripts = block / "scripts"
    scripts.mkdir(parents=True)
    for name in ("prepare_tasks.sh", "task_source.py", "stage_tasks.py"):
        shutil.copy2(TRACER_SCRIPTS / name, scripts / name)
    (block / "repos/harbor/.git").mkdir(parents=True)
    python = block / "artifacts/env/harbor/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    source = tmp_path / "source"
    source.mkdir()
    make_task(source, "tc-verified")
    make_task(source, "tc-unverified")
    (source / "verifiable_tasks.txt").write_text("tc-verified\n")
    config = config_source(
        provider="local", local_block="terminal-curator", dataset_name="unselected-swe",
        terminal_tasks_dir=str(source),
    )
    config["meta_info"] = {
        "repositories": {"harbor": {"path": "repos/harbor"}},
        "environment": {"harbor_uv": "artifacts/env/harbor"},
    }
    config_path = block / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))

    def prepare(*args):
        return subprocess.run(
            ["bash", str(scripts / "prepare_tasks.sh"), *args],
            cwd=block, text=True, capture_output=True, timeout=30,
        )

    return block, source, config_path, prepare


def test_terminal_staging_filters_and_refreshes_the_verified_manifest(staging):
    block, source, _, prepare = staging
    result = prepare()
    assert result.returncode == 0, result.stdout + result.stderr
    target = block / "artifacts/tasks/terminal-curator"
    assert sorted(path.name for path in target.iterdir()) == ["tc-verified"]
    assert (target / "tc-verified").is_symlink()
    assert (target / "tc-verified").resolve() == source / "tc-verified"
    (source / "verifiable_tasks.txt").write_text("tc-unverified\n")
    result = prepare()
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in target.iterdir()) == ["tc-unverified"]


def test_terminal_staging_pins_tasks_behind_a_versioned_publication_pointer(staging):
    block, source, config_path, prepare = staging
    pointer = source.parent / "published-tasks"
    pointer.symlink_to(source, target_is_directory=True)
    config = yaml.safe_load(config_path.read_text())
    config["runtime_info"]["input"]["task_source"]["terminal_tasks_dir"] = str(pointer)
    config_path.write_text(yaml.safe_dump(config))
    result = prepare()
    assert result.returncode == 0, result.stdout + result.stderr
    staged = block / "artifacts/tasks/terminal-curator/tc-verified"
    assert Path(os.readlink(staged)) == source / "tc-verified"
    next_batch = source.parent / "next-batch"
    make_task(next_batch, "tc-next")
    (next_batch / "verifiable_tasks.txt").write_text("tc-next\n")
    pointer.unlink()
    pointer.symlink_to(next_batch, target_is_directory=True)
    assert staged.is_dir()
    assert staged.resolve() == source / "tc-verified"


def test_existing_staged_tasks_do_not_bypass_terminal_manifest(staging):
    _, source, _, prepare = staging
    assert prepare().returncode == 0
    (source / "verifiable_tasks.txt").unlink()
    result = prepare()
    assert result.returncode != 0
    assert "requires verifiable_tasks.txt" in result.stderr


@pytest.mark.parametrize("entry", ["../outside", "missing-task", "tc-verified/nested", "."])
def test_terminal_manifest_rejects_invalid_or_missing_task_ids(staging, entry):
    _, source, _, prepare = staging
    (source / "verifiable_tasks.txt").write_text(entry + "\n")
    result = prepare()
    assert result.returncode != 0
    assert "ERROR:" in result.stderr


def test_swe_local_staging_still_supports_a_source_without_manifest(staging):
    block, source, config_path, prepare = staging
    config = yaml.safe_load(config_path.read_text())
    config["runtime_info"]["input"]["task_source"] = {
        "provider": "local", "dataset_name": str(source),
    }
    config_path.write_text(yaml.safe_dump(config))
    (source / "verifiable_tasks.txt").unlink()
    result = prepare()
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in (block / "artifacts/tasks/source").iterdir()) == [
        "tc-unverified", "tc-verified",
    ]


@pytest.fixture
def root_runner(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/start.sh", tmp_path / "scripts/start.sh")
    (tmp_path / "scripts/archive_run.sh").write_text("#!/bin/bash\nexit 0\n")
    events = tmp_path / "events.txt"
    for name in ("curator", "terminal-curator", "tracer"):
        block = tmp_path / "blocks" / name
        (block / "scripts").mkdir(parents=True)
        config = config_source(
            provider="local", local_block="terminal-curator", dataset_name="swe-tasks",
            terminal_tasks_dir="terminal-tasks",
        )
        config["meta_info"] = {"resources": {"ip": "local"}}
        (block / "config.yaml").write_text(yaml.safe_dump(config))
        (block / "scripts/start.sh").write_text(f'#!/bin/bash\necho {name} >> "$TEST_EVENTS"\n')
        if name == "curator":
            (block / "scripts/create_py.sh").write_text('#!/bin/bash\necho curator-python >> "$TEST_EVENTS"\n')
    scripts = tmp_path / "blocks/tracer/scripts"
    shutil.copy2(TRACER_SCRIPTS / "task_source.py", scripts / "task_source.py")
    (scripts / "prepare_tasks.sh").write_text('#!/bin/bash\necho prepare >> "$TEST_EVENTS"\n')

    def run(*args, overrides=None, foreground=True):
        env = {**os.environ, "TEST_EVENTS": str(events)}
        env.pop("TRAJGEN_CONFIG", None)
        env.pop("TERMINAL_CURATOR_CONFIG", None)
        env.pop("EVAL_CONFIG", None)
        env.update(overrides or {})
        return subprocess.run(
            ["bash", str(tmp_path / "scripts/start.sh"), *(["--foreground"] if foreground else []), "--no-dryrun", *args],
            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30,
        )

    return tmp_path, events, run


def test_root_runner_requires_semantic_audit_between_terminal_and_tracer(root_runner):
    _, events, run = root_runner
    result = run()
    assert result.returncode == 2
    assert "/root:run" in result.stderr
    assert "semantic audit" in result.stderr
    assert not events.exists()


def test_root_runner_allows_explicit_terminal_then_audited_tracer(root_runner):
    _, events, run = root_runner
    result = run("--terminal-curator-only")
    assert result.returncode == 0, result.stdout + result.stderr
    result = run("--tracer-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert events.read_text().splitlines() == ["terminal-curator", "prepare", "tracer"]


def test_root_runner_stops_before_tracer_if_the_producer_fails(root_runner):
    root, events, run = root_runner
    (root / "blocks/terminal-curator/scripts/start.sh").write_text("#!/bin/bash\nexit 23\n")
    assert run("--terminal-curator-only").returncode == 23
    assert not events.exists()


def test_root_explicit_terminal_mode_does_not_echo_invalid_yaml_secret_lines(root_runner):
    root, events, run = root_runner
    marker = "SENTINEL_PRIVATE_ROOT_RESOURCE_FIELD"
    (root / "blocks/terminal-curator/config.yaml").write_text(f'meta_info: {{resources: {{pwd: "{marker}\n')
    result = run("--terminal-curator-only")
    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr
    assert "invalid YAML" in result.stderr
    assert not events.exists()


def test_root_runner_dry_run_is_read_only(root_runner):
    _, events, run = root_runner
    result = run("--dry-run")
    assert result.returncode == 0
    assert "execution order: terminal-curator tracer" in result.stdout
    assert "real implicit terminal pipeline is refused" in result.stdout
    assert not events.exists()


def test_root_runner_uses_resource_settings_from_terminal_overlay(root_runner):
    root, events, run = root_runner
    block = root / "blocks/terminal-curator"
    config = yaml.safe_load((block / "config.yaml").read_text())
    config["meta_info"]["resources"]["ip"] = "192.0.2.10"
    (block / "config.local.yaml").write_text(yaml.safe_dump(config))
    result = run("--dry-run", overrides={"TERMINAL_CURATOR_CONFIG": "config.local.yaml"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "terminal-curator (192.0.2.10)" in result.stdout
    assert not events.exists()


def test_root_tmux_launcher_preserves_terminal_provider_overrides(root_runner):
    root, events, run = root_runner
    binary = root / "bin/tmux"
    binary.parent.mkdir()
    binary.write_text(
        '#!/bin/bash\n'
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        'printf "%s\\0" "$@" > "$TEST_TMUX_ARGS"\n'
    )
    binary.chmod(0o755)
    captured = root / "tmux-args"
    result = run("--terminal-curator-only", foreground=False, overrides={
        "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"],
        "TEST_TMUX_ARGS": str(captured),
        "MODEL_NAME": "fake-terminal-model",
        "OPENAI_API_BASE": "https://provider.invalid/v1",
    })
    assert result.returncode == 0, result.stdout + result.stderr
    args = captured.read_text().split("\0")
    assert "MODEL_NAME=fake-terminal-model" in args
    assert "OPENAI_API_BASE=https://provider.invalid/v1" in args
    assert any(arg.startswith("TERMINAL_CURATOR_CONFIG=") for arg in args)
    assert not events.exists()


@pytest.mark.parametrize("block", ["terminal-curator", "tracer"])
@pytest.mark.parametrize("external", [False, True])
def test_remote_worker_forwards_the_selected_profile_as_a_quoted_path(root_runner, block, external):
    root, events, run = root_runner
    binary = root / "bin/ssh"
    binary.parent.mkdir()
    binary.write_text('#!/bin/bash\nprintf "%s\\0" "$@" > "$TEST_SSH_ARGS"\n')
    binary.chmod(0o755)
    block_dir = root / "blocks" / block
    profile_dir = root / "external profiles" if external else block_dir / "profiles"
    profile_dir.mkdir()
    profile = profile_dir / "budget $(touch NEVER_CREATED) 'qa'; config.local.yaml"
    config = yaml.safe_load((block_dir / "config.yaml").read_text())
    remote_directory = f"/remote/checkout with spaces/{block}"
    config["meta_info"]["resources"] = {
        "ip": "192.0.2.20", "directory": remote_directory,
    }
    profile.write_text(yaml.safe_dump(config))
    captured = root / "ssh-args"
    variable = "TERMINAL_CURATOR_CONFIG" if block == "terminal-curator" else "TRAJGEN_CONFIG"
    selector = "--terminal-curator-only" if block == "terminal-curator" else "--tracer-only"

    result = run(selector, overrides={
        "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"],
        "TEST_SSH_ARGS": str(captured), variable: str(profile),
    })

    assert result.returncode == 0, result.stdout + result.stderr
    ssh_args = captured.read_text().split("\0")
    assert ssh_args[2] == "192.0.2.20"
    tmux_command = shlex.split(ssh_args[3])
    assert tmux_command[:4] == ["tmux", "new-session", "-d", "-s"]
    lexer = shlex.shlex(tmux_command[5], posix=True, punctuation_chars=";&")
    lexer.whitespace_split = True
    worker = list(lexer)
    assert worker[:3] == ["cd", remote_directory, "&&"]
    expected = str(profile) if external else str(Path(remote_directory) / profile.relative_to(block_dir))
    assert worker[3] == "export"
    export_end = worker.index("&&", 3)
    exports = worker[4:export_end]
    assert variable + "=" + expected in exports
    child = worker[export_end + 1:]
    if block == "terminal-curator":
        assert child[:4] == ["bash", "scripts/start.sh", "--config", expected]
    else:
        assert child[:2] == ["bash", "scripts/prepare_tasks.sh"]
        assert child[3:5] == ["bash", "scripts/start.sh"]
    assert not events.exists()
    assert not (root / "NEVER_CREATED").exists()


def test_root_runner_huggingface_skips_both_producers(root_runner):
    root, events, run = root_runner
    config_path = root / "blocks/tracer/config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["runtime_info"]["input"]["task_source"]["provider"] = "huggingface"
    config_path.write_text(yaml.safe_dump(config))
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert events.read_text().splitlines() == ["prepare", "tracer"]


def test_root_runner_keeps_the_legacy_swe_python_launcher(root_runner):
    root, events, run = root_runner
    config_path = root / "blocks/tracer/config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["runtime_info"]["input"]["task_source"]["local_block"] = "curator"
    config_path.write_text(yaml.safe_dump(config))
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert events.read_text().splitlines() == ["curator-python", "prepare", "tracer"]


def test_hyphenated_block_smoke_env_is_shell_compatible(monkeypatch):
    from scripts.inject_smoke_secrets import resolve

    monkeypatch.setenv("SMOKE_TERMINAL_CURATOR_LLM_API_KEY", "fake-integration-key")
    assert resolve("LLM_API_KEY", "terminal-curator") == (
        "fake-integration-key", "SMOKE_TERMINAL_CURATOR_LLM_API_KEY",
    )


@pytest.mark.parametrize("producer", ["curator", "terminal-curator"])
def test_both_producer_choices_validate_as_a_coherent_tree(tmp_path, producer):
    shutil.copy2(ROOT / "config.yaml", tmp_path / "config.yaml")
    for config_path in (ROOT / "blocks").glob("*/config.yaml"):
        target = tmp_path / "blocks" / config_path.parent.name / "config.yaml"
        target.parent.mkdir(parents=True)
        shutil.copy2(config_path, target)
    tracer_path = tmp_path / "blocks/tracer/config.yaml"
    config = yaml.safe_load(tracer_path.read_text())
    config["runtime_info"]["input"]["task_source"]["local_block"] = producer
    tracer_path.write_text(yaml.safe_dump(config))
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_config.py"), "--root", str(tmp_path), "--schema-only"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dep:link-mismatch" not in result.stdout
    if producer == "terminal-curator":
        assert "dep:resolved tracer: dependencies.from.task_source.terminal_tasks_dir" in result.stdout


def test_evaluator_can_consume_terminal_registry_without_changing_tracer(tmp_path):
    shutil.copy2(ROOT / "config.yaml", tmp_path / "config.yaml")
    for config_path in (ROOT / "blocks").glob("*/config.yaml"):
        target = tmp_path / "blocks" / config_path.parent.name / "config.yaml"
        target.parent.mkdir(parents=True)
        shutil.copy2(config_path, target)
    evaluator_path = tmp_path / "blocks/evaluator/config.yaml"
    config = yaml.safe_load(evaluator_path.read_text())
    source = config["runtime_info"]["input"]["task_source"]
    source.update(
        provider="harbor_registry", dataset_name="terminal-curator", version="1.0",
        registry_path="../terminal-curator/artifacts/tasks/registry.json", no_hack=False,
    )
    evaluator_path.write_text(yaml.safe_dump(config))
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_config.py"), "--root", str(tmp_path), "--schema-only"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dep:link-mismatch" not in result.stdout
    assert "dep:resolved evaluator: dependencies.from.task_source.registry_path" in result.stdout
    assert "dep:path-mismatch evaluator" not in result.stdout
    assert "dep:resolved tracer: dependencies.from.task_source.dataset_name" in result.stdout
