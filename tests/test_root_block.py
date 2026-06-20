"""Root-block sanity checks for LegoFactory.

Validates that the block tree under `subblock/` is structurally sound:
the five expected blocks exist, each has a parseable config.yaml whose
identity matches its directory, and each ships the uniform script contract
(start/dryrun/clean/archive_run).

No subblock scripts are executed; no network, Docker, or GPU is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBBLOCK_DIR = REPO_ROOT / "subblock"
EXPECTED_SUBBLOCKS = ["curator", "tracer", "trainer", "rl", "evaluator"]
UNIFORM_SCRIPTS = ["start.sh", "dryrun.sh", "clean.sh", "archive_run.sh"]
EXPECTED_PARENT = "lego_factory"


def test_subblock_dir_exists():
    assert SUBBLOCK_DIR.is_dir(), f"missing {SUBBLOCK_DIR}"


@pytest.mark.parametrize("name", EXPECTED_SUBBLOCKS)
def test_subblock_present(name: str):
    path = SUBBLOCK_DIR / name
    assert path.is_dir(), f"expected subblock dir {path} not found"


@pytest.mark.parametrize("name", EXPECTED_SUBBLOCKS)
def test_config_yaml_parses(name: str):
    config_path = SUBBLOCK_DIR / name / "config.yaml"
    assert config_path.is_file(), f"missing {config_path}"
    with config_path.open() as f:
        cfg = yaml.safe_load(f)
    assert isinstance(cfg, dict), f"{config_path} did not parse to a mapping"
    assert "meta_info" in cfg, f"{config_path} missing top-level `meta_info`"
    assert "runtime_info" in cfg, f"{config_path} missing top-level `runtime_info`"


@pytest.mark.parametrize("name", EXPECTED_SUBBLOCKS)
def test_meta_info_identity(name: str):
    config_path = SUBBLOCK_DIR / name / "config.yaml"
    with config_path.open() as f:
        cfg = yaml.safe_load(f)
    meta = cfg.get("meta_info", {})
    assert meta.get("name") == name, (
        f"{config_path}: meta_info.name={meta.get('name')!r} does not match dir name {name!r}"
    )
    assert meta.get("parent") == EXPECTED_PARENT, (
        f"{config_path}: meta_info.parent={meta.get('parent')!r}, expected {EXPECTED_PARENT!r}"
    )


@pytest.mark.parametrize("name", EXPECTED_SUBBLOCKS)
@pytest.mark.parametrize("script", UNIFORM_SCRIPTS)
def test_uniform_scripts_present(name: str, script: str):
    script_path = SUBBLOCK_DIR / name / "scripts" / script
    assert script_path.is_file(), f"missing uniform script: {script_path}"
