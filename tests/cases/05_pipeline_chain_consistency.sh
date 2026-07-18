#!/usr/bin/env bash
# Root case 05: the four smoke configs form a consistent, wired pipeline.
# This is the check that distinguishes the ROOT smoke (a real chain) from the
# isolated per-subblock smokes. It asserts that each stage is configured to
# consume the previous stage's real output:
#
#   swegen  -> collects ~200 PRs, writes verified tasks under a smoke subdir
#   trajgen -> consumes that swegen subdir (NOT a HF slice), converts the
#              reward==1 trajectories to SFT data
#   sft     -> trains on the 512 fixture COMBINED with trajgen's reward==1 LF,
#              and persists a checkpoint
#   eval    -> evaluates sft's checkpoint on swebench-verified, 100-task subset
#
# A drift between any two adjacent stages (e.g. trajgen still pointing at the HF
# slice, or eval not depending on sft) would silently turn the "end-to-end"
# smoke back into four disconnected runs. Catch it cheaply, here.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY' || exit 1
import sys, os
try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr); sys.exit(1)

root = sys.argv[1]
def load(b):
    return yaml.safe_load(open(os.path.join(root, "tests", "smoke", b, "config.yaml"), encoding="utf-8")) or {}
def get(d, dotted, default=None):
    cur = d
    for p in dotted.split("."):
        if not isinstance(cur, dict): return default
        cur = cur.get(p)
    return default if cur is None else cur

try:
    swe, trj, sft, ev = load("swegen"), load("trajgen"), load("sft"), load("eval")
except Exception as e:
    print(f"FAIL: cannot load a smoke config: {e}", file=sys.stderr); sys.exit(1)

errs = []

# --- swegen: collect ~200 PRs from scratch, write to a smoke subdir ----------
if get(swe, "runtime_info.input.smoke.collect.enabled") is not True:
    errs.append("swegen: smoke.collect.enabled must be true (collect 200 PRs from scratch)")
target = get(swe, "runtime_info.input.smoke.collect.target_prs")
if not isinstance(target, int) or target < 50:
    errs.append(f"swegen: smoke.collect.target_prs={target!r}, expected an int (design calls for 200)")
swe_subdir = get(swe, "runtime_info.input.smoke.output_subdir")
if not swe_subdir:
    errs.append("swegen: smoke.output_subdir must be set (where verified tasks land)")

# --- trajgen: consume the swegen subdir; convert reward==1 -------------------
if get(trj, "meta_info.dependencies.task_source_dir") != "swegen.output.swe_tasks_dir":
    errs.append("trajgen: meta_info.dependencies.task_source_dir must be swegen.output.swe_tasks_dir")
prov = get(trj, "runtime_info.input.task_source.provider")
if prov != "local":
    errs.append(f"trajgen: task_source.provider={prov!r} — root smoke must be 'local' (consume the swegen handoff, not a HF slice)")
# For the `local` provider, prepare_tasks.sh reads the source dir from
# task_source.dataset_name (and manifest-filters it by verifiable_tasks.txt).
src_dir = str(get(trj, "runtime_info.input.task_source.dataset_name", ""))
if swe_subdir and swe_subdir not in src_dir:
    errs.append(f"trajgen: task_source.dataset_name={src_dir!r} does not reference swegen subdir {swe_subdir!r}")
if get(trj, "runtime_info.input.sft_conversion.enabled") is not True:
    errs.append("trajgen: sft_conversion.enabled must be true (sft consumes the converted LF)")
if get(trj, "runtime_info.input.sft_conversion.reward_min") != 1:
    errs.append("trajgen: sft_conversion.reward_min must be 1 (reward==1 focus)")

# --- sft: combine 512 fixture + trajgen reward==1 LF, persist checkpoint -----
if get(sft, "meta_info.dependencies.training_data") != "trajgen.output.sft_data_dir":
    errs.append("sft: meta_info.dependencies.training_data must be trajgen.output.sft_data_dir")
if get(sft, "runtime_info.input.source.type") != "combined_lf":
    errs.append("sft: source.type must be 'combined_lf' (512 fixture + trajgen reward==1 LF)")
fixture = str(get(sft, "runtime_info.input.source.fixture_lf", ""))
if "lf_512" not in fixture:
    errs.append(f"sft: source.fixture_lf={fixture!r} should point at the 512-sample fixture (lf_512.json)")
up = str(get(sft, "runtime_info.input.source.upstream_lf_dir", ""))
if "sft_data" not in up:
    errs.append(f"sft: source.upstream_lf_dir={up!r} should reference trajgen's sft_data dir")
if str(get(sft, "runtime_info.input.training.output_dir", "")).startswith("_smoke"):
    errs.append("sft: training.output_dir is a throwaway _smoke dir — root smoke must PERSIST the checkpoint for eval")

# --- eval: evaluate sft's checkpoint on swebench-verified, 100 tasks ---------
if get(ev, "meta_info.dependencies.model_checkpoint") != "sft.output.checkpoint_path":
    errs.append("eval: meta_info.dependencies.model_checkpoint must be sft.output.checkpoint_path")
ds = get(ev, "runtime_info.input.task_source.dataset_name")
if ds != "swebench-verified":
    errs.append(f"eval: task_source.dataset_name={ds!r}, expected 'swebench-verified'")
n_tasks = get(ev, "runtime_info.input.harbor_job.n_tasks")
if n_tasks != 100:
    errs.append(f"eval: harbor_job.n_tasks={n_tasks!r}, expected 100 (the verified 100-subset)")
if get(ev, "runtime_info.input.llm_api.served_via") != "remote_vllm_of_sft_checkpoint":
    errs.append("eval: llm_api.served_via must be 'remote_vllm_of_sft_checkpoint' (vLLM+LiteLLM of the trained model)")

if errs:
    for e in errs: print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
print("PASS: pipeline chain is consistently wired (swegen -> trajgen -> sft -> eval)")
PY
