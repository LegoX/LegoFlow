"""Root-block sanity checks for SWE-Lego-Live.

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
SMOKE_DIR = REPO_ROOT / "tests" / "smoke"
EXPECTED_SUBBLOCKS = ["curator", "tracer", "trainer", "rl", "evaluator"]
# The four blocks the root end-to-end smoke chains (rl is not in this pipeline).
PIPELINE_BLOCKS = ["curator", "tracer", "trainer", "evaluator"]
UNIFORM_SCRIPTS = ["start.sh", "dryrun.sh", "clean.sh", "archive_run.sh"]
ROOT_SMOKE_SCRIPTS = [
    "tests/run.sh",
    "tests/smoke/run_pipeline.sh",
    "tests/smoke/verify.sh",
    "tests/smoke/serve_checkpoint.sh",
]
EXPECTED_PARENT = "swe_lego_live"


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


# --- Root end-to-end smoke harness -------------------------------------------
# The deeper, semantic checks (dependency wiring, smoke-config chain
# consistency) live in tests/cases/*.sh, which need bash; these pytest checks
# are the cloud-CI-portable structural layer.


@pytest.mark.parametrize("name", PIPELINE_BLOCKS)
def test_smoke_config_present_and_parses(name: str):
    cfg_path = SMOKE_DIR / name / "config.yaml"
    assert cfg_path.is_file(), f"missing root smoke config: {cfg_path}"
    with cfg_path.open() as f:
        cfg = yaml.safe_load(f)
    assert isinstance(cfg, dict), f"{cfg_path} did not parse to a mapping"
    assert "meta_info" in cfg and "runtime_info" in cfg, (
        f"{cfg_path} missing meta_info/runtime_info"
    )


@pytest.mark.parametrize("name", PIPELINE_BLOCKS)
def test_smoke_config_identity(name: str):
    cfg_path = SMOKE_DIR / name / "config.yaml"
    with cfg_path.open() as f:
        cfg = yaml.safe_load(f)
    meta = cfg.get("meta_info", {})
    assert meta.get("name") == name, (
        f"{cfg_path}: meta_info.name={meta.get('name')!r} != {name!r}"
    )
    assert meta.get("parent") == EXPECTED_PARENT, (
        f"{cfg_path}: meta_info.parent={meta.get('parent')!r} != {EXPECTED_PARENT!r}"
    )


@pytest.mark.parametrize("name", PIPELINE_BLOCKS)
def test_smoke_config_schema_subset_of_production(name: str):
    """A smoke overlay may prune production keys but must not invent new
    top-level sections the block code won't read."""
    smoke = yaml.safe_load((SMOKE_DIR / name / "config.yaml").read_text())
    prod = yaml.safe_load((SUBBLOCK_DIR / name / "config.yaml").read_text())
    extra = set(smoke) - set(prod)
    assert not extra, f"tests/smoke/{name}/config.yaml has non-production top-level keys: {sorted(extra)}"


@pytest.mark.parametrize("script", ROOT_SMOKE_SCRIPTS)
def test_root_smoke_scripts_present(script: str):
    path = REPO_ROOT / script
    assert path.is_file(), f"missing root smoke harness script: {script}"
