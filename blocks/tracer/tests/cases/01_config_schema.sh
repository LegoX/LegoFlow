#!/usr/bin/env bash
# CI test 01: tracer config.yaml schema.

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
    "runtime_info.input.sft_conversion.tokenizer_name",
]
missing = [k for k in REQUIRED if get(cfg, k) in (None, "")]
if missing:
    for k in missing: print(f"FAIL: missing or empty: {k}", file=sys.stderr)
    sys.exit(1)

# meta_info.dependencies must be {from: {...}, to: {...}} (both keys, both mappings).
deps = get(cfg, "meta_info.dependencies")
if (
    not isinstance(deps, dict)
    or set(deps.keys()) != {"from", "to"}
    or not isinstance(deps.get("from"), dict)
    or not isinstance(deps.get("to"), dict)
):
    print("FAIL: meta_info.dependencies must have exactly `from` and `to` keys, each a mapping (use {} for no edges)", file=sys.stderr)
    sys.exit(1)
if get(cfg, "meta_info.name") != "tracer":
    print(f"FAIL: meta_info.name != 'tracer'", file=sys.stderr); sys.exit(1)
prov = get(cfg, "runtime_info.input.task_source.provider")
if prov not in ("local", "huggingface"):
    print(f"FAIL: task_source.provider must be 'local' or 'huggingface' (got {prov!r})", file=sys.stderr)
    sys.exit(1)

# Harbor jobs_dir / dataset_path prefix check (mirrors dryrun.sh section 8).
# Catches the class of bug where a smoke variant sets jobs_dir to a sibling
# path like "artifacts/jobs-smoke" — that fails start.sh's internal dryrun.
def under(p, prefix):
    if p is None:
        return True
    return p == prefix or p.startswith(prefix + "/") or p.startswith("/") and ("/" + prefix in p)
jobs_dir = get(cfg, "runtime_info.input.harbor_job.jobs_dir")
if jobs_dir and not under(jobs_dir, "artifacts/jobs"):
    print(f"FAIL: harbor_job.jobs_dir must be artifacts/jobs or under it (got {jobs_dir!r})", file=sys.stderr)
    sys.exit(1)
dataset_path = get(cfg, "runtime_info.input.harbor_job.dataset_path")
if dataset_path and not under(dataset_path, "artifacts/tasks"):
    print(f"FAIL: harbor_job.dataset_path must be under artifacts/tasks (got {dataset_path!r})", file=sys.stderr)
    sys.exit(1)

print("PASS: config.yaml schema")
PY

# Shared block-contract validator (schema-only: `human` fill markers are
# expected on a fresh clone and downgraded to warnings here).
REPO_ROOT="$(cd "$BLOCK_DIR/../.." && pwd)"
if [[ -f "$REPO_ROOT/scripts/validate_config.py" ]]; then
  python3 "$REPO_ROOT/scripts/validate_config.py" --block "$BLOCK_DIR" --config "$CONFIG" --schema-only \
    || { echo "FAIL: validate_config.py reported schema failures"; exit 1; }
else
  echo "WARN: shared validator not found — skipping contract validation"
fi
