#!/usr/bin/env bash
# Root case 05: the four smoke configs form a consistent, wired pipeline.
# This is the check that distinguishes the ROOT smoke (a real chain) from the
# isolated per-block smokes. It asserts that each stage is configured to
# consume the previous stage's real output:
#
#   curator  -> collects ~200 PRs, writes verified tasks under a smoke subdir
#   tracer -> consumes that curator subdir (NOT a HF slice), converts the
#              reward==1 trajectories to SFT data
#   trainer     -> trains on the 512 fixture COMBINED with tracer's reward==1 LF,
#              and persists a checkpoint
#   evaluator    -> evaluates trainer's checkpoint on swebench-verified, 100-task subset
#
# A drift between any two adjacent stages (e.g. tracer still pointing at the HF
# slice, or evaluator not depending on trainer) would silently turn the "end-to-end"
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
def load_prod(b):
    """The block's real config — used where the smoke must not drift from it."""
    return yaml.safe_load(open(os.path.join(root, "blocks", b, "config.yaml"), encoding="utf-8")) or {}
def get(d, dotted, default=None):
    cur = d
    for p in dotted.split("."):
        if not isinstance(cur, dict): return default
        cur = cur.get(p)
    return default if cur is None else cur

try:
    swe, trj, trainer, ev = load("curator"), load("tracer"), load("trainer"), load("evaluator")
except Exception as e:
    print(f"FAIL: cannot load a smoke config: {e}", file=sys.stderr); sys.exit(1)

errs = []

# --- curator: collect ~200 PRs from scratch, write to a smoke subdir ----------
if get(swe, "runtime_info.input.smoke.collect.enabled") is not True:
    errs.append("curator: smoke.collect.enabled must be true (collect 200 PRs from scratch)")
target = get(swe, "runtime_info.input.smoke.collect.target_prs")
if not isinstance(target, int) or target < 50:
    errs.append(f"curator: smoke.collect.target_prs={target!r}, expected an int (design calls for 200)")
swe_subdir = get(swe, "runtime_info.input.smoke.output_subdir")
if not swe_subdir:
    errs.append("curator: smoke.output_subdir must be set (where verified tasks land)")

# --- tracer: consume the curator subdir; convert reward==1 -------------------
trj_dep_val = get(trj, "meta_info.dependencies.from", {}).get("task_source.dataset_name")
trj_dep_from = trj_dep_val.get("from") if isinstance(trj_dep_val, dict) else trj_dep_val
if trj_dep_from != "curator.output.swe_tasks_dir":
    errs.append("tracer: meta_info.dependencies.from['task_source.dataset_name'] must reference curator.output.swe_tasks_dir")
prov = get(trj, "runtime_info.input.task_source.provider")
if prov != "local":
    errs.append(f"tracer: task_source.provider={prov!r} — root smoke must be 'local' (consume the curator handoff, not a HF slice)")
# For the `local` provider, prepare_tasks.sh reads the source dir from
# task_source.dataset_name (and manifest-filters it by verifiable_tasks.txt).
src_dir = str(get(trj, "runtime_info.input.task_source.dataset_name", ""))
if swe_subdir and swe_subdir not in src_dir:
    errs.append(f"tracer: task_source.dataset_name={src_dir!r} does not reference curator subdir {swe_subdir!r}")
if get(trj, "runtime_info.input.sft_conversion.enabled") is not True:
    errs.append("tracer: sft_conversion.enabled must be true (trainer consumes the converted LF)")
if get(trj, "runtime_info.input.sft_conversion.reward_min") != 1:
    errs.append("tracer: sft_conversion.reward_min must be 1 (reward==1 focus)")
tracer_tokenizer = get(trj, "runtime_info.input.sft_conversion.tokenizer_name")
trainer_model = get(trainer, "runtime_info.input.model.model_name_or_path")
if tracer_tokenizer != trainer_model:
    errs.append(
        f"tracer: sft_conversion.tokenizer_name={tracer_tokenizer!r} must match "
        f"trainer model.model_name_or_path={trainer_model!r}"
    )
prod_tracer_tokenizer = get(load_prod("tracer"), "runtime_info.input.sft_conversion.tokenizer_name")
prod_trainer_model = get(load_prod("trainer"), "runtime_info.input.model.model_name_or_path")
if prod_tracer_tokenizer != prod_trainer_model:
    errs.append(
        f"production tracer tokenizer={prod_tracer_tokenizer!r} must match "
        f"production trainer model={prod_trainer_model!r}"
    )

# --- trainer: combine 512 fixture + tracer reward==1 LF, persist checkpoint -----
tr_dep_val = get(trainer, "meta_info.dependencies.from", {}).get("source.upstream_lf_dir")
tr_dep_from = tr_dep_val.get("from") if isinstance(tr_dep_val, dict) else tr_dep_val
if tr_dep_from != "tracer.output.sft_data_dir":
    errs.append("trainer: meta_info.dependencies.from['source.upstream_lf_dir'] must reference tracer.output.sft_data_dir")
if get(trainer, "runtime_info.input.source.type") != "combined_lf":
    errs.append("trainer: source.type must be 'combined_lf' (512 fixture + tracer reward==1 LF)")
# The smoke must train on the SAME dataset as the production block, so that what
# it exercises is the real training setup rather than a stand-in. The two configs
# carry that dataset differently — production is source.type=hf_lf and names it in
# hf_file_name (fetched from the Hub); the smoke is combined_lf and points
# fixture_lf at the already-downloaded local copy — so compare the file name, not
# the path. Asserting a hard-coded name here instead is what went stale when the
# fixture was switched to the production dataset in e7f7d94.
fixture = str(get(trainer, "runtime_info.input.source.fixture_lf", ""))
prod_file = str(get(load_prod("trainer"), "runtime_info.input.source.hf_file_name", ""))
if not prod_file:
    errs.append("trainer: production config has no source.hf_file_name to pin the smoke fixture against "
                "(did source.type change away from hf_lf? update this check together with it)")
elif os.path.basename(fixture) != prod_file:
    errs.append(f"trainer: smoke source.fixture_lf={fixture!r} does not match the production "
                f"dataset {prod_file!r} — the smoke would train on a different dataset than the block")
up = str(get(trainer, "runtime_info.input.source.upstream_lf_dir", ""))
if "sft_data" not in up:
    errs.append(f"trainer: source.upstream_lf_dir={up!r} should reference tracer's sft_data dir")
if str(get(trainer, "runtime_info.input.training.output_dir", "")).startswith("_smoke"):
    errs.append("trainer: training.output_dir is a throwaway _smoke dir — root smoke must PERSIST the checkpoint for evaluator")

# --- evaluator: evaluate trainer's checkpoint on swebench-verified, 100 tasks ---------
ev_dep_val = get(ev, "meta_info.dependencies.from", {}).get("llm_api.api_base_url")
ev_dep_from = ev_dep_val.get("from") if isinstance(ev_dep_val, dict) else ev_dep_val
if ev_dep_from != "trainer.output.checkpoint_path":
    errs.append("evaluator: meta_info.dependencies.from['llm_api.api_base_url'] must reference trainer.output.checkpoint_path")
ds = get(ev, "runtime_info.input.task_source.dataset_name")
if ds != "swebench-verified":
    errs.append(f"evaluator: task_source.dataset_name={ds!r}, expected 'swebench-verified'")
n_tasks = get(ev, "runtime_info.input.harbor_job.n_tasks")
if n_tasks != 100:
    errs.append(f"evaluator: harbor_job.n_tasks={n_tasks!r}, expected 100 (the verified 100-subset)")
if get(ev, "runtime_info.input.llm_api.served_via") != "remote_vllm_of_sft_checkpoint":
    errs.append("evaluator: llm_api.served_via must be 'remote_vllm_of_sft_checkpoint' (vLLM+LiteLLM of the trained model)")

if errs:
    for e in errs: print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
print("PASS: pipeline chain is consistently wired (curator -> tracer -> trainer -> evaluator)")
PY
