#!/usr/bin/env bash
# Root case 08: evaluator smoke targets the swebench-verified 100-task subset and is
# wired to serve the trainer checkpoint via remote vLLM + LiteLLM.
# The design's final stage evaluates the trained model on a 100-task subset of
# SWE-bench Verified. Validate the benchmark, the subset size, and that the
# serving handoff (remote GPU pod -> vLLM -> LiteLLM -> evaluator) is declared.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY' || exit 1
import sys, os
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)

cfg = yaml.safe_load(open(os.path.join(sys.argv[1], "tests/smoke/evaluator/config.yaml"), encoding="utf-8")) or {}
def get(dotted, default=None):
    cur = cfg
    for p in dotted.split("."):
        if not isinstance(cur, dict): return default
        cur = cur.get(p)
    return default if cur is None else cur

errs = []
if get("runtime_info.input.task_source.dataset_name") != "swebench-verified":
    errs.append(f"task_source.dataset_name={get('runtime_info.input.task_source.dataset_name')!r}, expected 'swebench-verified'")
n = get("runtime_info.input.harbor_job.n_tasks")
if n != 100:
    errs.append(f"harbor_job.n_tasks={n!r}, expected 100")

# Serving handoff: the trained checkpoint is served on the trainer GPU pod via
# vLLM and wrapped by LiteLLM; evaluator points its llm_api at that wrapper.
serving = get("runtime_info.input.serving") or {}
if get("runtime_info.input.llm_api.served_via") != "remote_vllm_of_sft_checkpoint":
    errs.append("llm_api.served_via must be 'remote_vllm_of_sft_checkpoint'")
if not serving.get("vllm"):
    errs.append("serving.vllm block missing (how to launch vLLM on the trainer pod)")
if not serving.get("litellm"):
    errs.append("serving.litellm block missing (how to wrap vLLM for evaluator)")
host_ref = str(serving.get("host", ""))
# host is resolved from the trainer block's resources at run time; the config should
# say so rather than hardcode a stale IP.
if host_ref and host_ref not in ("sft.resources", "from_sft_resources"):
    errs.append(f"serving.host={host_ref!r}; expected to reference the trainer pod (from_sft_resources)")

if errs:
    for e in errs: print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
print(f"PASS: evaluator smoke -> swebench-verified n_tasks={n}, served from trainer checkpoint")
PY
