#!/usr/bin/env bash
# CI test 01: config.yaml schema.
# Asserts config.yaml parses and the keys required by the runtime contract
# are present and non-empty. Calibrated to this CI runner's expected layout.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

[[ -f "$CONFIG" ]] || { echo "FAIL: $CONFIG missing"; exit 1; }

python3 - "$CONFIG" <<'PY' || exit 1
import sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed in the runner's python3", file=sys.stderr)
    sys.exit(1)

cfg_path = sys.argv[1]
with open(cfg_path, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}

REQUIRED = [
    "meta_info.name",
    "meta_info.environment.sft_uv",
    "meta_info.repositories.llama_factory.path",
    "meta_info.repositories.llama_factory.commit",
    "meta_info.repositories.swe_data_process.path",
    "meta_info.repositories.swe_data_process.commit",
    "runtime_info.input.source.scaffold",
    # `source.job_dir` is validated in cases/05 with source.type-aware logic
    # (only required when source.type == "harbor_job"). Don't gate the schema
    # check on it — sft also supports hf_lf / local_lf sources where job_dir
    # is unused.
    "runtime_info.input.conversion.data_name",
    "runtime_info.input.conversion.exclude_repos_file",
    "runtime_info.input.model.model_name_or_path",
    "runtime_info.input.training.stage",
    "runtime_info.input.training.finetuning_type",
    "runtime_info.input.training.deepspeed",
    "runtime_info.input.training.template",
    "runtime_info.input.training.cutoff_len",
    "runtime_info.input.training.output_dir",
    "runtime_info.input.infrastructure.n_gpus_per_node",
    "runtime_info.input.experiment.wandb_mode",
]

def get(d, dotted):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur

missing = [k for k in REQUIRED if get(cfg, k) in (None, "")]
if missing:
    for k in missing:
        print(f"FAIL: missing or empty: {k}", file=sys.stderr)
    sys.exit(1)

name = get(cfg, "meta_info.name")
if name != "sft":
    print(f"FAIL: meta_info.name == {name!r}, expected 'sft'", file=sys.stderr)
    sys.exit(1)

# Enumerated fields must hold a value train.sh knows how to map.
VALID_SCAFFOLDS = {"openhands-sdk", "claude-code", "open-code", "terminus2"}
scaffold = get(cfg, "runtime_info.input.source.scaffold")
if scaffold not in VALID_SCAFFOLDS:
    print(f"FAIL: source.scaffold == {scaffold!r}, expected one of {sorted(VALID_SCAFFOLDS)}", file=sys.stderr)
    sys.exit(1)

VALID_WANDB = {"online", "offline", "disabled"}
wandb_mode = get(cfg, "runtime_info.input.experiment.wandb_mode")
if wandb_mode not in VALID_WANDB:
    print(f"FAIL: experiment.wandb_mode == {wandb_mode!r}, expected one of {sorted(VALID_WANDB)}", file=sys.stderr)
    sys.exit(1)

# online WandB requires the key in config.yaml (train.sh reads it from there,
# not from the env) — catch the misconfiguration here, not at launch.
if wandb_mode == "online" and not get(cfg, "runtime_info.input.credentials.wandb_api_key"):
    print("FAIL: wandb_mode=online but credentials.wandb_api_key is empty", file=sys.stderr)
    sys.exit(1)

n_gpus = get(cfg, "runtime_info.input.infrastructure.n_gpus_per_node")
if not isinstance(n_gpus, int) or n_gpus < 1:
    print(f"FAIL: infrastructure.n_gpus_per_node == {n_gpus!r}, expected a positive integer", file=sys.stderr)
    sys.exit(1)

print("PASS: config.yaml schema")
PY
