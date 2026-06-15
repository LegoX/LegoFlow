#!/usr/bin/env bash
# CI test 01: eval config.yaml schema.

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
    "meta_info.environment.harbor_uv",
    "meta_info.environment.litellm_uv",
    "runtime_info.input.llm_api.api_key",
    "runtime_info.input.llm_api.api_base_url",
    "runtime_info.input.llm_api.model",
    "runtime_info.input.litellm_proxy.config_template",
    "runtime_info.input.litellm_proxy.port",
    "runtime_info.input.litellm_proxy.master_key",
    "runtime_info.input.task_source.provider",
    "runtime_info.input.task_source.dataset_name",
    "runtime_info.input.task_source.version",
    "runtime_info.input.task_source.registry_path",
    "runtime_info.input.harbor_job.jobs_dir",
    "runtime_info.input.harbor_job.n_concurrent",
    "runtime_info.input.harbor_job.max_retries",
    "runtime_info.input.harbor_job.timeout_multiplier",
    "runtime_info.input.agent.name",
    "runtime_info.input.agent.version",
    "runtime_info.input.agent.runtime_image",
    "runtime_info.input.agent.runtime_host_path",
    "runtime_info.input.agent.max_turns",
    "runtime_info.input.agent.temperature",
]
missing = [k for k in REQUIRED if get(cfg, k) in (None, "")]
if missing:
    for k in missing: print(f"FAIL: missing or empty: {k}", file=sys.stderr)
    sys.exit(1)
if get(cfg, "meta_info.name") != "eval":
    print("FAIL: meta_info.name != 'eval'", file=sys.stderr); sys.exit(1)

# eval is registry-driven; the only supported provider is harbor_registry.
prov = get(cfg, "runtime_info.input.task_source.provider")
if prov != "harbor_registry":
    print(f"FAIL: task_source.provider must be 'harbor_registry' (got {prov!r})", file=sys.stderr)
    sys.exit(1)

# Harbor jobs_dir prefix check (mirrors dryrun.sh section 8). Catches a smoke
# variant that points jobs_dir at a sibling path start.sh's dryrun rejects.
def under(p, prefix):
    if p is None:
        return True
    return p == prefix or p.startswith(prefix + "/") or (p.startswith("/") and ("/" + prefix in p))
jobs_dir = get(cfg, "runtime_info.input.harbor_job.jobs_dir")
if jobs_dir and not under(jobs_dir, "artifacts/jobs"):
    print(f"FAIL: harbor_job.jobs_dir must be artifacts/jobs or under it (got {jobs_dir!r})", file=sys.stderr)
    sys.exit(1)

# n_tasks is optional (null = full benchmark); when set it must be a positive int.
n_tasks = get(cfg, "runtime_info.input.harbor_job.n_tasks")
if n_tasks is not None and (not isinstance(n_tasks, int) or n_tasks <= 0):
    print(f"FAIL: harbor_job.n_tasks must be null or a positive int (got {n_tasks!r})", file=sys.stderr)
    sys.exit(1)

print("PASS: config.yaml schema")
PY
