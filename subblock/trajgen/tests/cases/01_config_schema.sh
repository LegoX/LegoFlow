#!/usr/bin/env bash
# CI test 01: trajgen config.yaml schema.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

[[ -f "$CONFIG" ]] || { echo "FAIL: $CONFIG missing"; exit 1; }

python3 - "$CONFIG" <<'PY' || exit 1
import sys
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed in runner's python3", file=sys.stderr)
    sys.exit(1)
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}

def get(d, dotted):
    cur = d
    for p in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur

REQUIRED = [
    "meta_info.name",
    "meta_info.repositories.harbor.url",
    "meta_info.repositories.harbor.commit",
    "meta_info.repositories.harbor.path",
    "meta_info.repositories.harbor.readonly",
    "meta_info.repositories.swe_data_process.url",
    "meta_info.repositories.swe_data_process.commit",
    "meta_info.repositories.swe_data_process.path",
    "meta_info.repositories.swe_data_process.readonly",
    "meta_info.environment.harbor_uv",
    "meta_info.environment.litellm_uv",
    "meta_info.environment.swe_data_process_uv",
    "runtime_info.input.llm_api.api_key",
    "runtime_info.input.llm_api.api_base_url",
    "runtime_info.input.llm_api.model",
    "runtime_info.input.litellm_proxy.port",
    "runtime_info.input.litellm_proxy.master_key",
    "runtime_info.input.task_source.provider",
    "runtime_info.input.task_source.dataset_name",
    "runtime_info.input.harbor_job.jobs_dir",
    "runtime_info.input.harbor_job.n_concurrent",
    "runtime_info.input.harbor_job.max_retries",
    "runtime_info.input.harbor_job.timeout_multiplier",
    "runtime_info.input.agent.name",
    "runtime_info.input.agent.version",
    "runtime_info.input.agent.runtime_image",
    "runtime_info.input.agent.max_turns",
    "runtime_info.input.sft_conversion.enabled",
]
missing = [k for k in REQUIRED if get(cfg, k) in (None, "")]
if missing:
    for k in missing: print(f"FAIL: missing or empty: {k}", file=sys.stderr)
    sys.exit(1)
if get(cfg, "meta_info.name") != "trajgen":
    print(f"FAIL: meta_info.name != 'trajgen'", file=sys.stderr); sys.exit(1)
prov = get(cfg, "runtime_info.input.task_source.provider")
if prov not in ("local", "huggingface"):
    print(f"FAIL: task_source.provider must be 'local' or 'huggingface' (got {prov!r})", file=sys.stderr)
    sys.exit(1)
print("PASS: config.yaml schema")
PY
