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


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("dataset_name", ["terminal-curator", "swebench-verified"])
def test_only_terminal_registry_is_frozen_when_publication_changes_before_execution(tmp_path, dataset_name):
    block = tmp_path / "blocks/evaluator"
    (block / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "blocks/evaluator/scripts/start.sh", block / "scripts")
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
