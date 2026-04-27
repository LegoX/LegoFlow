# rl-train

This file declares that the current directory is a `block`.

For the canonical definition of a block, the field semantics, and the default directory contract, see [`what_is_a_block.md`](https://github.com/SWE-Lego/SWE-Lego-Live/blob/master/what_is_a_block.md).

## Block Summary

```md
Name: rl-train
Type: training
Main doc: `dashboard/overview.mdx`
Meta info: `metainfo.yaml`
Status: `status.yaml`
Definition reference: `BLOCK_DEFINITION.md`
```

## Functional Positioning

This block is responsible for RL training of coding agents on SWE-bench.

It exists to coordinate the end-to-end training pipeline: running a configurable coding agent against a configurable local LLM via vLLM, evaluating patches with the SWE-bench grader, and updating model weights via GRPO/GSPO.

Its boundary is:
- in scope: RL training runs, model checkpointing, training configs, logs, evaluation results
- out of scope: SWE-bench task preparation, downstream deployment of trained models

## Inputs

This block depends on the following input categories:
- `training_dataset`: curated SWE-bench parquet (task instances for training)
- `val_dataset`: SWE-bench parquet for validation
- `base_model`: model checkpoint to initialize training from (configurable)
- `harbor_task_dirs`: pre-processed Harbor task directories (parquet → per-instance dirs)
- `harbor_val_task_dirs`: pre-processed Harbor task directories for validation
- `task_index`: train/val index parquets built by create_task_index.py
- `train_config`: Hydra training config
- `litellm_train_config`: LiteLLM proxy config for training
- `litellm_val_config`: LiteLLM proxy config for validation

Input readiness rule:
- this block can start when task parquets exist and the model is accessible via vLLM
- this block is blocked when Docker images are unavailable or GPU resources are insufficient

## Outputs

This block produces:
- `checkpoint_path`: path to the latest trained actor model checkpoint
- `val_resolve_rate`: SWE-bench-Verified resolve rate (%) for the best checkpoint
- `training_curves`: WandB run with reward, loss, token counts, and timing metrics
- `trajectory_log_dir`: JSONL logs of all agent API traffic from rollouts

## Artifacts And Memory

Artifacts stored by this block include:
- `model_checkpoints`: actor checkpoints saved during training (in `outputs/` of the training repo)
- `training_logs`: per-run training logs, one file per experiment (in `logs/` of the training repo)
- `training_out_logs`: raw stdout/stderr `.out` files from each training run
- `trajectory_logs`: JSONL files of all agent API traffic from rollouts (ephemeral at `/tmp/trajectory_output_dir/` — copy after each run)
- `training_scripts`: launch scripts for each variant (1-node, 2-node, coder, instruct)
- `pipeline_doc`: detailed pipeline architecture, data flow, and bug fix record (`TRAINING_PIPELINE.md`)

Long-form memory maintained by this block includes:
- `memory/notes.md`: architecture overview, config gotchas, experiment log, known failure modes
- `memory/decisions.md`: design decisions with rationale (ports, reward signal, data difficulty tiers, etc.)
- `memory/reports/`: postmortems, reward curve analyses, and eval reports per run

## Parent And Child Relationships

Parent relationship:
- parent block: none (root block)
- this block receives task data and base model from upstream infra
- this block reports back trained checkpoints and eval results

Child relationship:
- child blocks under `subblock/`: none yet
- future subblocks may include `subblock/eval`, `subblock/data-prep`

## Environment

Conda env: `harbor-rllm-env` (`/anaconda3/envs/harbor-rllm-env`, Python 3.12)

**Setup (first time):**
```bash
conda create -n harbor-rllm-env python=3.12
conda activate harbor-rllm-env

# Install training packages (rllm workflow layer + verl RL engine + harbor task env)
pip install -e repos/harbor-verl-train/
pip install --no-deps -e repos/harbor-verl-train/verl/
bash repos/harbor-verl-train/scripts/install_verl.sh
pip install -e repos/harbor/
```

If `scripts/start.sh` fails with `ModuleNotFoundError`, rerun the corresponding `pip install` step above.

**Key package versions (2026-04-20):**

| Package | Version | Note |
|---|---|---|
| torch | 2.7.1 | |
| vllm | 0.10.0 | |
| flash_attn | 2.7.4.post1 | cu12, cxx11abi=False |
| tensordict | 0.9.1 | |
| verl | 0.5.0 | editable from `repos/harbor-verl-train/verl/` |
| rllm | 0.2.0 | editable from `repos/harbor-verl-train/` |
| ray | 2.54.0 | |
| transformers | 4.57.1 | |
| wandb | 0.22.3 | |
| harbor | 0.1.44 | editable from `repos/harbor/` |
| litellm | 1.82.0 | proxy runs on port 8001 (not 8002) |
| hydra-core | 1.3.2 | |

## Configuration

All training variables (model path, infra IPs, data paths, experiment name, credentials) are in `inputs.yaml`. Edit this file before running any script.

### Machine-specific fields

The following fields depend on the host. Sensible defaults are shipped, and `node0_ip` is auto-detected at launch — you should only need to touch these in edge cases.

| Field | Default behavior | When to override |
|---|---|---|
| `infrastructure.node0_ip` | Empty in yaml → `scripts/train_1node.sh` auto-detects via `hostname -I \| awk '{print $1}'` and prints the chosen IP at launch. | Only if the host has multiple network interfaces and auto-detect picks the wrong one. Set explicitly in yaml. |
| `infrastructure.n_gpus_per_node` | `8` | Your node has a different GPU count — check with `nvidia-smi -L \| wc -l`. |
| `infrastructure.vllm_tp_size` | `2` (TP=2, DP=4 on 8 GPUs — matches Qwen3-30B-A3B's 4 KV heads) | See **TP sizing for MoE/GQA models** below before changing. Wrong TP causes a silent CUDA illegal-memory crash. |
| `CONDA_ENV_BIN` env var | `/anaconda3/envs/harbor-rllm-env/bin` | Your conda env is installed elsewhere. Override per launch: `CONDA_ENV_BIN=/path/to/env/bin bash scripts/start.sh`. |

All other paths (model, data, kubeconfig, trials dir) in `inputs/index.yaml` point to `/mnt/public/...` shared locations and work as-is across machines.

#### TP sizing for MoE/GQA models

`vllm_tp_size` must evenly divide the model's `num_key_value_heads` (found in the model's `config.json`). Violating this is not caught cleanly — vLLM dispatches attention with misshaped tensors and the first forward pass crashes with `CUDA error: an illegal memory access was encountered` (the stack trace may point inside flash-attn sources and look like a kernel-arch bug, but the real cause is the TP/KV-head mismatch).

Quick check before changing TP:
```bash
python3 -c "import json; c=json.load(open('/mnt/public/models/<model_dir>/config.json')); print('num_key_value_heads:', c['num_key_value_heads'])"
```

For the shipped default model `Qwen3-30B-A3B-Instruct-2507`: `num_key_value_heads=4`, so valid TP ∈ {1, 2, 4}. With 8 GPUs, TP=2 gives DP=4 replicas (best throughput for this config).

## Execution Interface

Available scripts:
- `scripts/start.sh`: launch a training run
- `scripts/dryrun.sh`: validate config, paths, and Docker images without running training
- `scripts/clean.sh`: remove temporary outputs and logs

Dryrun expectation:
- `scripts/dryrun.sh` should verify configs, required Docker images, and GPU availability.

### Health checks after launch

The inference data path is **Agent (in k8s pod) → LiteLLM proxy (:8001) → vLLM (:8000) → GPUs**. A failure anywhere in the chain stalls rollouts silently. Three complementary probes cover it:

**1. LiteLLM-reported endpoint health (primary signal)**

LiteLLM already polls its upstream vLLM endpoints. Hitting its `/health` gives a truthful up/down count without touching the GPU:

```bash
curl -sS http://127.0.0.1:8001/health \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(f'healthy={d[\"healthy_count\"]} unhealthy={d[\"unhealthy_count\"]}')"
```

Healthy training should print `healthy=N unhealthy=0` (where N == `vllm_dp_size`). Any `unhealthy>0` means vLLM is not serving on `node0_ip:8000` — either still loading, crashed, bound to a wrong IP, or the model name in the LiteLLM config doesn't match what vLLM actually serves.

**2. Real inference ping (end-to-end, 3 tokens)**

The `/health` above is a fixed LiteLLM internal request. To verify the exact path your agent uses, send one through:

```bash
curl -sS --max-time 30 http://127.0.0.1:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-30B-A3B-Instruct-2507","messages":[{"role":"user","content":"ping"}],"max_tokens":3}' \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print('reply:', d['choices'][0]['message']['content'])"
```

Returning any text → the full `user → LiteLLM → vLLM → GPU → reply` chain works. Replace the `model` value with whatever `model.served_model_name` is set to in `inputs/index.yaml`.

**3. GPU utilization during rollout (sanity check)**

Once the model is loaded and rollouts are running, GPUs should stay busy:

```bash
watch -n 2 nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader
```

| Observed | Meaning |
|---|---|
| `memory.used ≈ 0`, `util = 0%` for > 5 min after launch | vLLM never started. Check training log and `/tmp/litellm_cc_train_8001.log`. |
| `memory.used` high (≥ 20 GiB/card for 30B @ TP=8), `util = 0%` sustained during rollout | vLLM loaded but no traffic arriving. The Agent/k8s side is stuck — check Harbor pods and `actor_rollout_ref` logs. |
| `memory.used` high, `util` fluctuates 20–100% | Rollouts are running normally. |

**Quick combined one-liner (paste when things look off):**

```bash
echo "=== LiteLLM ==="; curl -sS http://127.0.0.1:8001/health | python3 -c "import json,sys;d=json.load(sys.stdin);print(f'healthy={d[\"healthy_count\"]} unhealthy={d[\"unhealthy_count\"]}')"; \
echo "=== GPU avg util ==="; nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | awk '{s+=$1}END{printf \"%.1f%%\\n\", s/NR}'; \
echo "=== Rollout traffic (last 5s LiteLLM log) ==="; tail -n 200 /tmp/litellm_cc_train_8001.log | grep -c "POST /v1/chat/completions"
```

Reading it: LiteLLM healthy=N, GPU >20% avg, and non-zero POST count → training is actually doing work.

## Collaboration Rules

When updating this block:
- read `dashboard/overview.mdx` first for current state
- read `metainfo.yaml` for block identity, resources, and dependency wiring
- read `status.yaml` for live job progress, results, and next steps
- use `inputs.yaml` for all configurable training parameters
- use `outputs.yaml` to record the latest checkpoint and run info
- after every run, archive params, metrics, inputs, and log into `artifacts/files/run_NNN/` and append to `artifacts/index.yaml`
- use `artifacts/` for raw evidence (logs, trajectories, exports)
- use `memory/` for long-form context and experiment notes
- the training repo: `https://github.com/Elvin-Yiming-Du/harbor-verl-train`
