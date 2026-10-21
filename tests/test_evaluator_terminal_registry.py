"""The Evaluator launch pins a terminal registry batch before proxy warmup."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml
from test_terminal_curator_integration import attest_fixture, make_task


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mutate_after_preflight", [False, True])
def test_final_integrity_check_stops_harbor_after_proxy_warmup(tmp_path, mutate_after_preflight):
    """Execute the real final launch section with an already warmed proxy."""
    pool = tmp_path / "batch"
    pool.mkdir()
    make_task(pool, "tc-final")
    (pool / "verifiable_tasks.txt").write_text("tc-final\n")
    (pool / "registry.json").write_text(json.dumps([
        {"name": "terminal-curator", "version": "1.0",
         "tasks": [{"name": "tc-final", "path": str(pool / "tc-final")}]}
    ]))
    attest_fixture(pool)
    if mutate_after_preflight:
        (pool / "tc-final/instruction.md").write_text("Changed during proxy warmup.\n")
    source = (ROOT / "blocks/evaluator/scripts/start.sh").read_text()
    # Select the actual launch boundary rather than reproducing its shell logic.
    before_harbor = source.index('\n(cd "$HARBOR_DIR" && bash -c "$RUN_COMMAND")')
    boundary = source.index('\necho "=== evaluator start ==="')
    end = source.index('\nHARBOR_RC=', before_harbor)
    marker = tmp_path / "harbor-started"
    env = {**os.environ, "BLOCK_DIR": str(ROOT / "blocks/evaluator"),
           "DATASET_NAME": "terminal-curator", "REGISTRY_ABS": str(pool / "registry.json"),
           "HARBOR_DIR": str(tmp_path), "RUN_COMMAND": "touch " + shlex.quote(str(marker)),
           "LOG_FILE": str(tmp_path / "launch.log")}
    result = subprocess.run(["bash", "-c", "set -eo pipefail\n" + source[boundary:end]],
                            env=env, text=True, capture_output=True, timeout=30)
    if mutate_after_preflight:
        assert result.returncode == 2
        assert "integrity check failed" in result.stderr
        assert not marker.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert marker.exists()


@pytest.mark.parametrize("dataset_name", ["terminal-curator", "swebench-verified"])
@pytest.mark.parametrize('mutate', [False, True])
def test_only_terminal_registry_is_frozen_when_publication_changes_before_execution(tmp_path, dataset_name, mutate):
    block = tmp_path / "blocks/evaluator"
    (block / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "blocks/evaluator/scripts/start.sh", block / "scripts")
    verifier = block.parent / 'terminal-curator/scripts/publication.py'
    verifier.parent.mkdir(parents=True)
    shutil.copy2(ROOT / 'blocks/terminal-curator/scripts/publication.py', verifier)
    (block / "scripts/dryrun.sh").write_text("exit 0\n")
    harbor = block / "repos/harbor"
    (harbor / ".git").mkdir(parents=True)
    (harbor / "scripts/serve_llm").mkdir(parents=True)
    (harbor / "scripts/serve_llm/trajectory_logger.py").write_text("# Fixture logger.\n")
    (harbor / "proxy.example.yaml").write_text("{}\n")
    python = block / "artifacts/env/harbor/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    first = tmp_path / "producer/batches/first"
    second = tmp_path / "producer/batches/second"
    for batch in (first, second):
        batch.mkdir(parents=True)
        (batch / "registry.json").write_text(json.dumps([
            {"name": dataset_name, "version": "1.0",
             "tasks": [{"name": f"tc-{batch.name}", "path": str(batch / f"tc-{batch.name}")}]}]))
        make_task(batch, f'tc-{batch.name}')
        if dataset_name == 'terminal-curator':
            (batch / 'verifiable_tasks.txt').write_text(f'tc-{batch.name}\n')
            attest_fixture(batch)
    if mutate:
        (first / 'tc-first/instruction.md').write_text('Changed after validation.\n')
    public = tmp_path / "producer/tasks"
    public.symlink_to(first, target_is_directory=True)
    config = yaml.safe_load((ROOT / "blocks/evaluator/config.yaml").read_text())
    config["meta_info"]["repositories"]["harbor"]["path"] = "repos/harbor"
    config["meta_info"]["environment"]["harbor_uv"] = "artifacts/env/harbor"
    inputs = config["runtime_info"]["input"]
    inputs["task_source"].update(dataset_name=dataset_name, version="1.0",
                                 registry_path=str(public / "registry.json"), no_hack=False)
    inputs["llm_api"] = {"model": "openai/fixture-model", "api_key": "fixture-api-key"}
    inputs["litellm_proxy"]["config_template"] = "proxy.example.yaml"
    inputs["env_extra"] = {}
    (block / "config.local.yaml").write_text(yaml.safe_dump(config))
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # Snapshotting occurs after registry selection and before proxy rendering.
    # Simulate a concurrent producer publication at precisely that point.
    (scripts / "redact_archive_config.py").write_text(
        'from pathlib import Path\nimport os\nimport sys\n'
        'Path(sys.argv[2]).write_text("{}\\n")\n'
        'public = Path(os.environ["TEST_PUBLICATION"])\n'
        'public.unlink()\npublic.symlink_to(os.environ["TEST_NEXT_BATCH"])\n'
    )
    env = {**os.environ, "EVAL_CONFIG": "config.local.yaml",
           "TEST_PUBLICATION": str(public), "TEST_NEXT_BATCH": str(second)}
    for name in ("OPENAI_MODEL", "ANTHROPIC_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_BASE_URL", "OPENAI_BASE_URL"):
        env.pop(name, None)

    result = subprocess.run(["bash", str(block / "scripts/start.sh"), "--dry-run-command"],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)

    if mutate and dataset_name == 'terminal-curator':
        assert result.returncode != 0
        assert 'integrity check failed' in result.stderr
        assert public.resolve() == first
        assert not list((block / 'artifacts/litellm').glob('*/*.yaml'))
        return
    assert result.returncode == 0, result.stdout + result.stderr
    command = next(line for line in result.stdout.splitlines() if line.startswith("uv run harbor run "))
    arguments = shlex.split(command.split(" --mounts-json ", 1)[0])
    expected = first / "registry.json" if dataset_name == "terminal-curator" else public / "registry.json"
    assert arguments[arguments.index("--registry-path") + 1] == str(expected)
    assert public.resolve() == second
    selected = json.loads(Path(arguments[arguments.index("--registry-path") + 1]).read_text())
    expected_task = "tc-first" if dataset_name == "terminal-curator" else "tc-second"
    assert selected[0]["tasks"][0]["name"] == expected_task
    proxy_configs = list((block / "artifacts/litellm").glob("*/*.yaml"))
    assert len(proxy_configs) == 1
    assert proxy_configs[0].stat().st_mode & 0o777 == 0o600
