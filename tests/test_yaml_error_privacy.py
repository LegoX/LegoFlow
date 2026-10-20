"""Malformed private YAML must fail without source lines or scalar values."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
MARKER = "SENTINEL_PRIVATE_YAML_VALUE"


def clean_env(tmp_path):
    env = dict(os.environ)
    for name in list(env):
        if name.startswith(("OPENAI_", "ANTHROPIC_")):
            env.pop(name)
    binary = tmp_path / "bin"
    binary.mkdir()
    python = binary / "python3"
    python.write_text('#!/bin/bash\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o755)
    curl = binary / "curl"
    curl.write_text('#!/bin/bash\ntouch "$TEST_NETWORK_ATTEMPT"\nexit 99\n')
    curl.chmod(0o755)
    env.update(PATH=str(binary) + os.pathsep + env.get("PATH", ""),
               TEST_NETWORK_ATTEMPT=str(tmp_path / "network-attempt"))
    return env


@pytest.mark.parametrize("bad_yaml", [
    f'runtime_info: {{input: {{api_key: "{MARKER}\n',
    f'runtime_info: {{input: {{api_key: *{MARKER}}}}}\n',
])
def test_archive_redactor_rejects_private_yaml_without_echoing_the_source(tmp_path, bad_yaml):
    source = tmp_path / "private.local.yaml"
    source.write_text(bad_yaml)
    destination = tmp_path / "snapshot.yaml"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/redact_archive_config.py"),
                             str(source), str(destination)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert MARKER not in result.stdout + result.stderr
    assert "invalid YAML" in result.stderr
    assert "line " in result.stderr and "column " in result.stderr
    assert not destination.exists()


@pytest.mark.parametrize("block,script,variable", [
    ("tracer", "convert_trajectories.sh", "TRAJGEN_CONFIG"),
    ("evaluator", "probe_llm_completion.sh", "EVAL_CONFIG"),
])
def test_independent_consumer_entrypoints_do_not_echo_private_yaml_aliases(tmp_path, block, script, variable):
    source = tmp_path / "private.local.yaml"
    # Even when a file-stream parser omits snippets, its undefined-alias
    # diagnostic can include the entire private scalar unless sanitized.
    source.write_text(f'runtime_info: {{input: {{llm_api: {{api_key: *{MARKER}}}}}}}\n')
    env = clean_env(tmp_path)
    env[variable] = str(source)
    result = subprocess.run(["bash", str(ROOT / "blocks" / block / "scripts" / script)],
                            env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert MARKER not in result.stdout + result.stderr
    assert "invalid YAML" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert not (tmp_path / "network-attempt").exists()


@pytest.mark.parametrize("block", ["tracer", "evaluator"])
@pytest.mark.parametrize("malformed", ["config", "template"])
def test_proxy_rendering_rejects_private_yaml_without_exposing_values(tmp_path, block, malformed):
    source = (ROOT / "blocks" / block / "scripts/start.sh").read_text()
    begin = source.index("write_litellm_config() {")
    end = source.index("\nexport_run_environment() {", begin)
    config = tmp_path / "private.local.yaml"
    template = tmp_path / "proxy.local.yaml"
    config.write_text(yaml.safe_dump({"runtime_info": {"input": {
        "llm_api": {"api_key": "fixture-key", "model": "openai/fixture"},
        "litellm_proxy": {"master_key": "fixture-proxy"},
    }}}))
    template.write_text("{}\n")
    broken = config if malformed == "config" else template
    broken.write_text(f'private_api_key: *{MARKER}\n')
    harness = tmp_path / "render-proxy.sh"
    harness.write_text('set -euo pipefail\n' + source[begin:end] +
                       '\nwrite_litellm_config "$TEST_OUTPUT" "$TEST_TEMPLATE"\n')
    output = tmp_path / "proxy.yaml"
    env = clean_env(tmp_path)
    env.update(CONFIG=str(config), TEST_OUTPUT=str(output), TEST_TEMPLATE=str(template))
    result = subprocess.run(["bash", str(harness)], env=env,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert MARKER not in result.stdout + result.stderr
    assert "invalid YAML" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert not output.exists()
