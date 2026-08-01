#!/usr/bin/env bash
# CI test 01: evaluator config.yaml schema.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIGS=("$BLOCK_DIR/config.yaml" "$BLOCK_DIR/tests/smoke/config.yaml")

for CONFIG in "${CONFIGS[@]}"; do
[[ -f "$CONFIG" ]] || { echo "FAIL: $CONFIG missing"; exit 1; }
python3 - "$CONFIG" <<'PY' || exit 1
import sys
from pathlib import Path
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
    "runtime_info.input.task_source.no_hack",
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
    "runtime_info.output.eval_results_dir.path",
    "runtime_info.output.eval_results_dir.job_layout",
    "runtime_info.output.eval_results_dir.trajectory_format",
    "runtime_info.output.eval_results_dir.results_summary_format",
]
# The smoke template must keep its credential fields blank: scripts/
# inject_smoke_secrets.py fills a field only when it is "", so a placeholder
# here is never replaced — which is how the smoke ended up pointing at a vLLM
# nobody starts. Structure is still required in both configs; only these three
# values are supplied at run time.
RUNTIME_SUPPLIED = {
    "runtime_info.input.llm_api.api_key",
    "runtime_info.input.llm_api.api_base_url",
    "runtime_info.input.llm_api.model",
}
is_smoke = Path(sys.argv[1]).parent.name == "smoke"
missing = [
    k for k in REQUIRED
    if get(cfg, k) is None or (get(cfg, k) == "" and not (is_smoke and k in RUNTIME_SUPPLIED))
]
if missing:
    for k in missing: print(f"FAIL: missing or empty: {k}", file=sys.stderr)
    sys.exit(1)
if is_smoke:
    filled = [k for k in RUNTIME_SUPPLIED if get(cfg, k) not in (None, "")]
    if filled:
        for k in filled:
            print(f"FAIL: smoke config must leave {k} blank for "
                  f"inject_smoke_secrets.py to fill", file=sys.stderr)
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
if get(cfg, "meta_info.name") != "evaluator":
    print("FAIL: meta_info.name != 'evaluator'", file=sys.stderr); sys.exit(1)

# evaluator is registry-driven; the only supported provider is harbor_registry.
prov = get(cfg, "runtime_info.input.task_source.provider")
if prov != "harbor_registry":
    print(f"FAIL: task_source.provider must be 'harbor_registry' (got {prov!r})", file=sys.stderr)
    sys.exit(1)

no_hack = get(cfg, "runtime_info.input.task_source.no_hack")
if not isinstance(no_hack, bool):
    print(f"FAIL: task_source.no_hack must be a bool (got {no_hack!r})", file=sys.stderr)
    sys.exit(1)

# Harbor jobs_dir prefix check (mirrors dryrun.sh section 8). Catches a smoke
# variant that points jobs_dir at a sibling path start.sh's dryrun rejects.
def under(p, prefix):
    if p is None:
        return True
    p = str(p).rstrip("/")
    if p.startswith("/"):
        marker = "/" + prefix
        return p.endswith(marker) or marker + "/" in p
    return p == prefix or p.startswith(prefix + "/")
jobs_dir = get(cfg, "runtime_info.input.harbor_job.jobs_dir")
if jobs_dir and not under(jobs_dir, "artifacts/jobs"):
    print(f"FAIL: harbor_job.jobs_dir must be artifacts/jobs or under it (got {jobs_dir!r})", file=sys.stderr)
    sys.exit(1)

# n_tasks is optional (null = full benchmark); when set it must be a positive int.
n_tasks = get(cfg, "runtime_info.input.harbor_job.n_tasks")
if n_tasks is not None and (not isinstance(n_tasks, int) or n_tasks <= 0):
    print(f"FAIL: harbor_job.n_tasks must be null or a positive int (got {n_tasks!r})", file=sys.stderr)
    sys.exit(1)

n_concurrent = get(cfg, "runtime_info.input.harbor_job.n_concurrent")
if not isinstance(n_concurrent, int) or n_concurrent <= 0:
    print(f"FAIL: harbor_job.n_concurrent must be a positive int (got {n_concurrent!r})", file=sys.stderr)
    sys.exit(1)

max_retries = get(cfg, "runtime_info.input.harbor_job.max_retries")
if not isinstance(max_retries, int) or max_retries < 0:
    print(f"FAIL: harbor_job.max_retries must be a non-negative int (got {max_retries!r})", file=sys.stderr)
    sys.exit(1)

port = get(cfg, "runtime_info.input.litellm_proxy.port")
if not isinstance(port, int) or not 1 <= port <= 65535:
    print(f"FAIL: litellm_proxy.port must be an integer in 1..65535 (got {port!r})", file=sys.stderr)
    sys.exit(1)

max_turns = get(cfg, "runtime_info.input.agent.max_turns")
if not isinstance(max_turns, int) or max_turns <= 0:
    print(f"FAIL: agent.max_turns must be a positive int (got {max_turns!r})", file=sys.stderr)
    sys.exit(1)

temperature = get(cfg, "runtime_info.input.agent.temperature")
if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or not 0 <= temperature <= 1:
    print(f"FAIL: agent.temperature must be numeric in 0..1 (got {temperature!r})", file=sys.stderr)
    sys.exit(1)

job_layout = get(cfg, "runtime_info.output.eval_results_dir.job_layout")
if not isinstance(job_layout, str) or "{agent,verifier}" not in job_layout or "evaluation" in job_layout:
    print(f"FAIL: output job_layout must use {{agent,verifier}} (got {job_layout!r})", file=sys.stderr)
    sys.exit(1)

summary_format = get(cfg, "runtime_info.output.eval_results_dir.results_summary_format")
if not isinstance(summary_format, str) or not summary_format.endswith("/result.json"):
    print(f"FAIL: results_summary_format must end in /result.json (got {summary_format!r})", file=sys.stderr)
    sys.exit(1)

print(f"PASS: {Path(sys.argv[1]).name} schema ({sys.argv[1]})")
PY
done

# Shared block-contract validator (schema-only: `human` fill markers are
# expected on a fresh clone and downgraded to warnings here).
REPO_ROOT="$(cd "$BLOCK_DIR/../.." && pwd)"
if [[ -f "$REPO_ROOT/scripts/validate_config.py" ]]; then
  for CONFIG in "${CONFIGS[@]}"; do
    python3 "$REPO_ROOT/scripts/validate_config.py" --block "$BLOCK_DIR" --config "$CONFIG" --schema-only \
      || { echo "FAIL: validate_config.py reported schema failures for $CONFIG"; exit 1; }
  done
else
  echo "WARN: shared validator not found — skipping contract validation"
fi
