"""Prove the native Harbor registry contains only immutable verified tasks."""

import json
import os
from pathlib import Path
import subprocess

import pytest


def test_publication_exports_a_native_local_harbor_registry(adapter, fixture_tasks, tmp_path):
    accepted = sorted(path for path in fixture_tasks.iterdir() if path.is_dir())
    pool = tmp_path / "tasks"
    adapter.publish_tasks(accepted, pool)
    batch = pool.resolve(strict=True)
    registry = json.loads((pool / "registry.json").read_text())

    assert isinstance(registry, list) and len(registry) == 1
    dataset = registry[0]
    assert dataset["name"] == "terminal-curator"
    assert dataset["version"] == "1.0"
    assert isinstance(dataset["description"], str) and dataset["description"]
    assert [entry["name"] for entry in dataset["tasks"]] == (
        pool / "verifiable_tasks.txt"
    ).read_text().splitlines()
    assert len(dataset["tasks"]) == len(accepted)
    for entry in dataset["tasks"]:
        path = Path(entry["path"])
        assert path.is_absolute()
        assert path == batch / entry["name"]
        assert (path / "task.toml").is_file()
        assert not entry.get("git_url")
        assert not entry.get("git_commit_id")


def test_registry_excludes_unpublished_candidates(adapter, fixture_tasks, tmp_path):
    chosen = fixture_tasks / "terminal-jsonl-summary"
    pool = tmp_path / "tasks"
    adapter.publish_tasks([chosen], pool)
    registry = json.loads((pool / "registry.json").read_text())
    assert [entry["name"] for entry in registry[0]["tasks"]] == [chosen.name]
    assert "terminal-csv-reconcile" not in (pool / "registry.json").read_text()


def test_prior_registry_keeps_its_task_paths_after_a_new_publication(adapter, fixture_tasks, tmp_path):
    first = fixture_tasks / "terminal-jsonl-summary"
    second = fixture_tasks / "terminal-csv-reconcile"
    pool = tmp_path / "tasks"
    adapter.publish_tasks([first], pool)
    original_registry = pool.resolve() / "registry.json"
    original_bytes = original_registry.read_bytes()
    first_path = Path(json.loads(original_bytes)[0]["tasks"][0]["path"])

    adapter.publish_tasks([second], pool)
    assert original_registry.read_bytes() == original_bytes
    assert first_path.is_dir()
    assert (first_path / "instruction.md").is_file()
    replacement = json.loads((pool / "registry.json").read_text())
    assert [entry["name"] for entry in replacement[0]["tasks"]] == [second.name]


def test_pinned_harbor_resolves_the_export_when_its_runtime_is_installed(adapter, fixture_tasks, tmp_path):
    root = Path(__file__).resolve().parents[3]
    python = Path(os.environ.get(
        "TERMINAL_CURATOR_HARBOR_TEST_PYTHON",
        root / "blocks/tracer/artifacts/env/harbor-uv/bin/python",
    ))
    if not python.is_file():
        pytest.skip("pinned Harbor environment is not installed; real evaluator smoke verifies this on its execution host")
    pool = tmp_path / "tasks"
    accepted = sorted(path for path in fixture_tasks.iterdir() if path.is_dir())
    adapter.publish_tasks(accepted, pool)
    code = '''
import asyncio, json, sys, harbor
from pathlib import Path
from harbor.models.registry import Registry
from harbor.models.job.config import DatasetConfig
path = Path(sys.argv[1])
assert Path(harbor.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve() / "src/harbor")
registry = Registry.from_path(path)
assert len(registry.datasets) == 1
tasks = asyncio.run(DatasetConfig(name="terminal-curator", version="1.0", registry_path=path).get_task_configs())
print(json.dumps([{"path": str(task.path), "source": task.source} for task in tasks]))
'''
    result = subprocess.run(
        [str(python), "-c", code, str(pool / "registry.json"), str(root / "blocks/tracer/repos/harbor")],
        env={
            "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1",
            "HARBOR_EDITABLE_ROOT": str(root / "blocks/tracer/repos/harbor"),
        },
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    tasks = json.loads(result.stdout)
    assert sorted(Path(task["path"]).name for task in tasks) == [task.name for task in accepted]
    assert all(task["source"] == "terminal-curator" for task in tasks)
    assert all(Path(task["path"]).parent == pool.resolve() for task in tasks)
