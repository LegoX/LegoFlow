"""Runtime profile selection, coherent sibling wiring, and safe diagnostics."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE_ENV = {"tracer": "TRAJGEN_CONFIG", "evaluator": "EVAL_CONFIG", "terminal-curator": "TERMINAL_CURATOR_CONFIG"}


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/validate_config.py", root / "scripts")
    shutil.copy2(ROOT / "config.yaml", root)
    for name in ("curator", "terminal-curator", "tracer", "trainer", "evaluator"):
        block = root / "blocks" / name
        block.mkdir(parents=True)
        shutil.copy2(ROOT / "blocks" / name / "config.yaml", block)
    env = dict(os.environ)
    for name in PROFILE_ENV.values():
        env.pop(name, None)
    return root, env


def validate(root, env, *args):
    return subprocess.run([sys.executable, str(root / "scripts/validate_config.py"),
                           *map(str, args), "--schema-only"],
                          env=env, cwd=root.parent, text=True, capture_output=True, timeout=15)


def profile(root, name, modify):
    block = root / "blocks" / name
    config = yaml.safe_load((block / "config.yaml").read_text())
    modify(config)
    # Spaces, quotes, and shell substitution are only a filename here.
    path = block / "profiles" / "bounded $(touch NEVER_CREATED) 'run'.local.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(config))
    return path


def terminal_profiles(root, env):
    def terminal(config):
        config["runtime_info"]["output"]["harbor_tasks_dir"]["path"] = "artifacts/published"
        config["runtime_info"]["output"]["eval_registry"]["path"] = "artifacts/published/registry.json"

    def tracer(config):
        config["runtime_info"]["input"]["task_source"]["local_block"] = "terminal-curator"
        config["runtime_info"]["input"]["task_source"]["terminal_tasks_dir"] = "../terminal-curator/artifacts/published"

    def evaluator(config):
        source = config["runtime_info"]["input"]["task_source"]
        source.update(dataset_name="terminal-curator", version="1.0",
                      registry_path="../terminal-curator/artifacts/published/registry.json", no_hack=False)

    for name, modify in (("terminal-curator", terminal), ("tracer", tracer), ("evaluator", evaluator)):
        path = profile(root, name, modify)
        env[PROFILE_ENV[name]] = str(path.relative_to(root / "blocks" / name))


def test_effective_profiles_select_source_and_sibling_output_from_the_same_config_set(tree):
    root, env = tree
    terminal_profiles(root, env)
    result = validate(root, env, "--root", root, "--effective-profiles")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tracer: dependencies.from.task_source.terminal_tasks_dir -> terminal-curator.output.harbor_tasks_dir = artifacts/published" in result.stdout
    assert "evaluator: dependencies.from.task_source.registry_path -> terminal-curator.output.eval_registry = artifacts/published/registry.json" in result.stdout
    assert "dep:path-mismatch" not in result.stdout
    assert not (root.parent / "NEVER_CREATED").exists()


def test_default_validation_ignores_business_profile_environment(tree):
    root, env = tree
    terminal_profiles(root, env)
    # CI's static validation remains independent of a malformed runtime file.
    path = root / "blocks/terminal-curator" / env["TERMINAL_CURATOR_CONFIG"]
    path.write_text('runtime_info: [not-valid\n')
    result = validate(root, env, "--root", root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tracer: dependencies.from.task_source.dataset_name -> curator.output.merged_tasks_dir" in result.stdout
    assert " = artifacts/published" not in result.stdout


def test_explicit_current_profile_wins_but_sibling_profiles_still_apply(tree):
    root, env = tree
    terminal_profiles(root, env)
    explicit = root / "explicit-tracer.yaml"
    config = yaml.safe_load((root / "blocks/tracer/config.yaml").read_text())
    config["runtime_info"]["input"]["task_source"]["local_block"] = "external"
    explicit.write_text(yaml.safe_dump(config))
    result = validate(root, env, "--block", root / "blocks/tracer", "--config", explicit, "--effective-profiles")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tracer: dependencies.from.task_source.terminal_tasks_dir inactive" in result.stdout


def test_effective_profiles_and_smoke_overlay_are_distinct_config_sets(tree):
    root, env = tree
    result = validate(root, env, "--root", root, "--overlay-dir", root / "smoke", "--effective-profiles")
    assert result.returncode == 2
    assert "choose one" in result.stderr


def test_omitted_legacy_local_selector_keeps_both_swe_dependency_edges_active(tree):
    root, env = tree
    tracer = root / "blocks/tracer/config.yaml"
    config = yaml.safe_load(tracer.read_text())
    del config["runtime_info"]["input"]["task_source"]["local_block"]
    tracer.write_text(yaml.safe_dump(config))
    result = validate(root, env, "--root", root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tracer: dependencies.from.task_source.dataset_name -> curator.output.merged_tasks_dir" in result.stdout
    assert "curator: dependencies.to.merged_tasks_dir -> tracer.input.task_source.dataset_name (active:" in result.stdout


def test_invalid_yaml_reports_location_without_echoing_secret_source(tree):
    root, env = tree
    malformed = root / "broken.local.yaml"
    marker = "SENTINEL_PRIVATE_FIELD_MUST_NOT_APPEAR"
    malformed.write_text(f'meta_info: {{name: tracer}}\nruntime_info:\n  input: {{api_key: "{marker}\n')
    result = validate(root, env, "--block", root / "blocks/tracer", "--config", malformed)
    assert result.returncode == 1
    assert marker not in result.stdout + result.stderr
    assert "invalid YAML" in result.stdout
    assert "line " in result.stdout and "column " in result.stdout
    assert str(malformed) in result.stdout


@pytest.mark.parametrize("name", ["tracer", "evaluator"])
def test_dryrun_stops_after_safe_parse_diagnostic_instead_of_reparsing_secret_lines(tree, name):
    root, env = tree
    scripts = root / "blocks" / name / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "blocks" / name / "scripts/dryrun.sh", scripts)
    profile_path = scripts.parent / "broken.local.yaml"
    marker = "SENTINEL_PROXY_KEY_MUST_NOT_APPEAR"
    profile_path.write_text(f'runtime_info: {{input: {{api_key: "{marker}\n')
    env[PROFILE_ENV[name]] = profile_path.name
    result = subprocess.run(["bash", str(scripts / "dryrun.sh")], env=env, cwd=root.parent,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert "invalid YAML" in result.stdout
    assert marker not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize("name", ["tracer", "evaluator"])
def test_required_field_check_does_not_print_private_proxy_key(name, tmp_path):
    script = (ROOT / "blocks" / name / "scripts/dryrun.sh").read_text()
    # Execute the actual required-field loop with bounded fake inputs, without
    # running the unrelated Docker/env/API probes of a full block preflight.
    start = script.index("for key in ")
    end = script.index("\ndone", start) + len("\ndone")
    loop = script[start:end]
    marker = "SENTINEL_PRIVATE_PROXY_MASTER"
    harness = tmp_path / "check-required-fields.sh"
    harness.write_text('set -euo pipefail\n'
                       'cfg() { if [[ "$1" == runtime_info.input.litellm_proxy.master_key ]]; '
                       'then echo "$TEST_PRIVATE_KEY"; else echo fixture-value; fi; }\n'
                       'ok() { echo "OK: $1"; }\nfail() { echo "FAIL: $1"; }\n' + loop)
    result = subprocess.run(["bash", str(harness)], env={**os.environ, "TEST_PRIVATE_KEY": marker},
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "runtime_info.input.litellm_proxy.master_key is set" in result.stdout
    assert marker not in result.stdout + result.stderr


def test_root_full_dryrun_uses_each_child_host_and_quotes_mapped_profiles(tree):
    root, env = tree
    shutil.copy2(ROOT / "scripts/dryrun.sh", root / "scripts")
    (root / "CLAUDE.md").write_text("Fixture contract.\n")
    resource = root / ".claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md"
    resource.parent.mkdir(parents=True)
    resource.write_text("Fixture block schema.\n")
    (root / "artifacts").mkdir()
    (root / "scripts/shared_credentials.sh").write_text('load_shared_credentials() { :; }\n')
    root_config = yaml.safe_load((root / "config.yaml").read_text())
    root_config["meta_info"]["blocks"] = {"terminal-curator": {"role": "producer"}, "tracer": {"role": "consumer"}}
    root_config["meta_info"]["resources"] = {"ip": "local"}
    (root / "config.yaml").write_text(yaml.safe_dump(root_config))
    for name in ("terminal-curator", "tracer"):
        block = root / "blocks" / name
        config = {"meta_info": {"name": name, "parent": "legoflow", "blocks": {},
                                 "dependencies": {"from": {}, "to": {}}, "resources": {"ip": "local"}},
                  "runtime_info": {"input": {}, "output": {}}}
        (block / "config.yaml").write_text(yaml.safe_dump(config))
        (block / "scripts").mkdir()
        (block / "scripts/dryrun.sh").write_text(f'echo "{name}:$TRAJGEN_CONFIG" >> "$TEST_LOCAL_EVENTS"\n')

    remote_directory = "/remote/checkout with spaces/blocks/terminal-curator"
    selected = profile(root, "terminal-curator", lambda config: config["meta_info"]["resources"].update(
        ip="192.0.2.30", user="fixture-user", directory=remote_directory))
    env["TERMINAL_CURATOR_CONFIG"] = str(selected.relative_to(root / "blocks/terminal-curator"))
    binary = root / "bin/ssh"
    binary.parent.mkdir()
    binary.write_text('#!/bin/bash\nprintf "%s\\0" "$@" >> "$TEST_REMOTE_COMMAND"\n')
    binary.chmod(0o755)
    events = root / "local-events"
    captured = root / "ssh-command"
    # The stub only records commands. No remote access or credential forwarding.
    for name in list(env):
        if name.startswith(("CLOUDFLARE_", "DOCKER_")):
            env.pop(name)
    env.update(PATH=str(binary.parent) + os.pathsep + env["PATH"], TEST_LOCAL_EVENTS=str(events),
               TEST_REMOTE_COMMAND=str(captured), DOCKER_CONFIG=str(root / "empty-docker"),
               OPENAI_API_KEY="SENTINEL_NOT_A_REMOTE_ARGUMENT")

    result = subprocess.run(["bash", str(root / "scripts/dryrun.sh"), "--full"],
                            env=env, cwd=root.parent, text=True, capture_output=True, timeout=20)

    assert result.returncode == 0, result.stdout + result.stderr
    assert events.read_text().splitlines() == [f"tracer:{root}/blocks/tracer/config.yaml"]
    args = captured.read_text().split("\0")[:-1]
    assert args[:5] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "fixture-user@192.0.2.30"]
    lexer = shlex.shlex(args[5], posix=True, punctuation_chars=";&")
    lexer.whitespace_split = True
    command = list(lexer)
    assert command[:4] == ["cd", remote_directory, "&&", "export"]
    mapped = str(Path(remote_directory) / selected.relative_to(root / "blocks/terminal-curator"))
    assert "TERMINAL_CURATOR_CONFIG=" + mapped in command
    assert "TRAJGEN_CONFIG=/remote/checkout with spaces/blocks/tracer/config.yaml" in command
    assert command[-3:] == ["&&", "bash", "scripts/dryrun.sh"]
    assert "SENTINEL_NOT_A_REMOTE_ARGUMENT" not in captured.read_text()
    assert not (root.parent / "NEVER_CREATED").exists()
