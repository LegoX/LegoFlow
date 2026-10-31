"""RSI-loop config contract for LegoFlow.

Covers the three new feedback edges landed for the self-improvement loop:

  EDGE 1  evaluator.output.weakness_report          -> curator.input.targeting.directives_path
  EDGE 2  evaluator.output.benchmark_identity_index -> curator.input.decontam.benchmark_index
  EDGE 3  trainer.output.checkpoint_path            -> evaluator.input.local_model_serving.from_trainer_checkpoint

Both evaluator->curator edges ship disabled (`targeting.enabled` /
`decontam.enabled` false), so the default tree validates with them INACTIVE —
enabling either flag must activate the mirrored pair end-to-end (the
consumer's `from` resolves the producer output; the producer's `to` reports
active) without a `dep:path-mismatch`. No network, no Docker; each case reads
local configs and runs the validator once as a subprocess.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "scripts" / "validate_config.py"
# Runtime profile selectors the validator honors under --effective-profiles;
# popped so these tests always exercise the tracked configs.
PROFILE_ENV = ("TRAJGEN_CONFIG", "EVAL_CONFIG", "TERMINAL_CURATOR_CONFIG")
TREE_BLOCKS = ("curator", "terminal-curator", "tracer", "trainer", "evaluator")

DIRECTIVES_PATH = "../evaluator/artifacts/weakness/weakness_report.json"
BENCHMARK_INDEX = "../evaluator/artifacts/bench_identity/swebench-verified.identity.json"


def load_block(name):
    return yaml.safe_load((REPO_ROOT / "blocks" / name / "config.yaml").read_text())


def run_validator(*args, root=REPO_ROOT):
    env = dict(os.environ)
    for var in PROFILE_ENV:
        env.pop(var, None)
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--schema-only", *map(str, args)],
        env=env, cwd=str(REPO_ROOT), text=True, capture_output=True, timeout=60,
    )


@pytest.fixture
def tree(tmp_path):
    """A throwaway copy of the real config tree (validator + root + 5 blocks)."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(VALIDATOR, root / "scripts")
    shutil.copy2(REPO_ROOT / "config.yaml", root)
    for name in TREE_BLOCKS:
        block = root / "blocks" / name
        block.mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "blocks" / name / "config.yaml", block)
    return root


def flip(root, dotted, value):
    """Set one nested leaf of a copied block config: `<block>.<a>.<b>...`."""
    block, *path = dotted.split(".")
    config_path = root / "blocks" / block / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    node = config
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    config_path.write_text(yaml.safe_dump(config))


def test_rsi_contract_leaves_and_mirrors():
    curator = load_block("curator")
    evaluator = load_block("evaluator")
    trainer = load_block("trainer")

    # Curator feedback inputs: default-off with the exact contract knobs.
    assert curator["runtime_info"]["input"]["targeting"] == {
        "enabled": False,
        "directives_path": DIRECTIVES_PATH,
        "min_tag_n": 20,
        "noise_gate_multiplier": 2.0,
        "supply_satisfaction_threshold": 40,
    }
    assert curator["runtime_info"]["input"]["decontam"] == {
        "enabled": False,
        "benchmark_index": BENCHMARK_INDEX,
    }

    # Consumer-side mirrors: consumer-local `when`, optional (required: false)
    # so a not-yet-produced evaluator output can never fail a curator run.
    curator_from = curator["meta_info"]["dependencies"]["from"]
    assert curator_from["targeting.directives_path"] == {
        "from": "evaluator.output.weakness_report",
        "when": {"targeting.enabled": True},
        "required": False,
    }
    assert curator_from["decontam.benchmark_index"] == {
        "from": "evaluator.output.benchmark_identity_index",
        "when": {"decontam.enabled": True},
        "required": False,
    }

    # Evaluator: variance-gated repeats default to a single pass; the trainer
    # checkpoint hand-off leaf exists while the live model_path knob is untouched.
    ev_input = evaluator["runtime_info"]["input"]
    assert ev_input["harbor_job"]["repeats"] == 1
    assert ev_input["local_model_serving"]["from_trainer_checkpoint"] == ""
    assert ev_input["local_model_serving"]["model_path"] == "artifacts/models/Qwen3.5-35B-A3B"

    ev_output = evaluator["runtime_info"]["output"]
    assert ev_output["weakness_report"]["path"] == "artifacts/weakness"
    assert ev_output["benchmark_identity_index"]["path"] == "artifacts/bench_identity"

    # Producer-side mirrors: `when` keys fully-qualified on curator's own input
    # (the same asymmetry as the tracer precedent).
    ev_to = evaluator["meta_info"]["dependencies"]["to"]
    assert ev_to["weakness_report"] == {
        "to": "curator.input.targeting.directives_path",
        "when": {"curator.input.targeting.enabled": True},
    }
    assert ev_to["benchmark_identity_index"] == {
        "to": "curator.input.decontam.benchmark_index",
        "when": {"curator.input.decontam.enabled": True},
    }

    # EDGE 3: unconditional but optional until train.sh writes the value back.
    assert evaluator["meta_info"]["dependencies"]["from"][
        "local_model_serving.from_trainer_checkpoint"
    ] == {
        "from": "trainer.output.checkpoint_path",
        "required": False,
    }
    # ...and its producer-side mirror clears trainer's checkpoint_path orphan WARN.
    assert (trainer["meta_info"]["dependencies"]["to"]["checkpoint_path"]
            == "evaluator.input.local_model_serving.from_trainer_checkpoint")


def test_default_tree_validates_with_feedback_edges_inactive():
    result = run_validator("--root", REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

    # Both evaluator->curator edges ship INACTIVE: dispatch stays cycle-free.
    assert "curator: dependencies.from.targeting.directives_path inactive" in result.stdout
    assert "curator: dependencies.from.decontam.benchmark_index inactive" in result.stdout
    assert "evaluator: dependencies.to.weakness_report inactive" in result.stdout
    assert "evaluator: dependencies.to.benchmark_identity_index inactive" in result.stdout

    # EDGE 3 is declared both ways: resolved as optional-unwritten (WARN), not FAIL.
    assert ("[WARN] dep:unresolved evaluator: dependencies.from."
            "local_model_serving.from_trainer_checkpoint") in result.stdout
    assert ("trainer: dependencies.to.checkpoint_path -> "
            "evaluator.input.local_model_serving.from_trainer_checkpoint") in result.stdout

    # The wiring itself is sound: no drift, no dangling keys, no orphaned checkpoint_path.
    assert "dep:link-mismatch" not in result.stdout
    assert "dep:bad-key" not in result.stdout
    assert "dep:bad-ref" not in result.stdout
    assert "output:orphan trainer: runtime_info.output.checkpoint_path" not in result.stdout


def test_effective_profiles_validation_stays_green():
    # Normative fix #5: the profile-aware pass must accept the new leaves too.
    # (No profile env vars are set, so every block resolves to its tracked
    # config.yaml — exactly the CI invocation.)
    result = run_validator("--root", REPO_ROOT, "--effective-profiles")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "curator: dependencies.from.targeting.directives_path inactive" in result.stdout


@pytest.mark.parametrize(
    ("flip_leaf", "when_key", "out_key", "in_leaf", "producer_path",
     "other_when_key", "other_out_key", "other_in_leaf"),
    [
        pytest.param(
            "curator.runtime_info.input.targeting.enabled", "targeting.enabled",
            "weakness_report", "targeting.directives_path", "artifacts/weakness",
            "decontam.enabled", "benchmark_identity_index", "decontam.benchmark_index",
            id="targeting",
        ),
        pytest.param(
            "curator.runtime_info.input.decontam.enabled", "decontam.enabled",
            "benchmark_identity_index", "decontam.benchmark_index", "artifacts/bench_identity",
            "targeting.enabled", "weakness_report", "targeting.directives_path",
            id="decontam",
        ),
    ],
)
def test_enabling_a_flag_activates_its_mirrored_edge_pair(
    tree, flip_leaf, when_key, out_key, in_leaf, producer_path,
    other_when_key, other_out_key, other_in_leaf,
):
    flip(tree, flip_leaf, True)
    result = run_validator("--root", tree)
    assert result.returncode == 0, result.stdout + result.stderr

    # Consumer side resolves the producer's declared output path...
    assert (f"[OK] dep:resolved curator: dependencies.from.{in_leaf} "
            f"-> evaluator.output.{out_key} = {producer_path}") in result.stdout
    # ...and the producer side flips to active on the fully-qualified condition.
    assert (f"[OK] dep:to-declared evaluator: dependencies.to.{out_key} "
            f"-> curator.input.{in_leaf} "
            f"(active: when {{'curator.input.{when_key}': True}} matches)") in result.stdout
    # The consumer's configured path resolves inside the producer's output path.
    assert "dep:path-mismatch" not in result.stdout

    # The sibling feedback edge stays inactive: each flag gates only its own edge.
    assert (f"curator: dependencies.from.{other_in_leaf} inactive "
            f"(when {{'{other_when_key}': True}} does not match current input)") in result.stdout
    assert f"evaluator: dependencies.to.{other_out_key} inactive" in result.stdout


def test_serve_script_prefers_trainer_checkpoint():
    """Edge 3 runtime consumer.

    `config.yaml` and `blocks/evaluator/CLAUDE.md` both document that
    `serve_local_model.sh` prefers `local_model_serving.from_trainer_checkpoint`
    when non-empty; lock the doc claim to the script by asserting the
    checkpoint branch is consulted BEFORE the live external `model_path` knob
    (env MODEL_PATH still overrides both for one-off runs).
    """
    text = (REPO_ROOT / "blocks/evaluator/scripts/serve_local_model.sh").read_text()
    ckpt_branch = "cfg runtime_info.input.local_model_serving.from_trainer_checkpoint"
    knob_branch = "cfg runtime_info.input.local_model_serving.model_path"
    assert ckpt_branch in text, (
        "serve_local_model.sh no longer reads local_model_serving."
        "from_trainer_checkpoint — the documented Edge 3 preference "
        "(trainer writeback over the live model_path knob) is not implemented"
    )
    assert text.index(ckpt_branch) < text.index(knob_branch), (
        "serve_local_model.sh must resolve from_trainer_checkpoint BEFORE "
        "model_path: the trainer checkpoint writeback outranks the live "
        "external knob whenever it is non-empty"
    )
