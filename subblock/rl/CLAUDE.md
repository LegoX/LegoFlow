# rl

Online RL training pipeline for SWE-bench coding agents.

## Block Identity

- **Name**: rl
- **Role**: Training - reinforcement learning on SWE-bench tasks
- **Parent**: swe_lego_live
- **Children**: none (leaf block)

## What To Read First

1. `config.yaml` — block identity, RL training config, runtime values (one-shot per run; live state in `artifacts/index.yaml`)
2. `dashboard/overview.mdx` — current state narrative

## Input/Output Contract

**Inputs** (read from `config.yaml` → `runtime_info.input`):
- `model`: Base model path and served model name
- `infrastructure`: Node IP, GPU config, vLLM/Ray ports, K8s config
- `training`: Batch size, parallel tasks, max turns, prompt/response lengths
- `data`: Training/val parquet paths, Harbor task directories
- `experiment`: Project name, experiment name, trials directory
- `credentials`: WandB API key

**Outputs** (written to `config.yaml` → `runtime_info.output`):
- `checkpoint_path`: Path to trained actor model checkpoint
- `val_resolve_rate`: SWE-bench-Verified resolve rate (%)
- `training_curves`: WandB run with reward, loss, token metrics
- `training_log`: Per-run log file
- `trajectory_log_dir`: JSONL logs of agent API traffic

## Repos

- `repos/harbor-verl-train/`: RL training framework (rLLM + verl, GRPO/GSPO)
- `repos/harbor/`: Task execution layer (Harbor on Kubernetes)

## Environment

Conda env: `harbor-rllm-env` (Python 3.12). See original CLAUDE.md for full setup instructions.

## How To Run

- `scripts/start.sh`: Launch a training run
- `scripts/dryrun.sh`: Validate config, paths, and Docker images
- `scripts/clean.sh`: Remove temporary outputs and logs

## Health Checks

After launch, verify the inference chain (Agent → LiteLLM :8001 → vLLM :8000 → GPUs):

```bash
# LiteLLM endpoint health
curl -sS http://127.0.0.1:8001/health | python3 -c "import json,sys;d=json.load(sys.stdin);print(f'healthy={d[\"healthy_count\"]} unhealthy={d[\"unhealthy_count\"]}')"

# Real inference ping
curl -sS --max-time 30 http://127.0.0.1:8001/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"Qwen3-30B-A3B-Instruct-2507","messages":[{"role":"user","content":"ping"}],"max_tokens":3}' | python3 -c "import json,sys;d=json.load(sys.stdin);print('reply:', d['choices'][0]['message']['content'])"

# GPU utilization
watch -n 2 nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader
```

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing metadata.yaml, config snapshot, scripts copy, session.log, and monitor.md. Append entry to `artifacts/index.yaml`.

## Memory

Long-form notes, architecture overview, experiment log, and design decisions are kept in `dashboard/memory/`.

## Remote Execution

This block requires 8× A100/H100 GPUs. If `meta_info.resources.ip` is set, execute remotely via SSH + tmux.
