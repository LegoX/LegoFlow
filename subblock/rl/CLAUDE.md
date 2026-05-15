# rl

Online RL training pipeline for SWE-bench coding agents (claude-code agent +
Harbor k8s sandbox + verl trainer/rollout).

## Block Identity

- **Name**: rl
- **Role**: Training — single-node sync RL (PPO/GRPO/GSPO) over agent trajectories
- **Parent**: swe_lego_live
- **Children**: none

## What To Read First

1. `config.yaml` — block identity, all runtime knobs, current status
2. `artifacts/index.yaml` — what each run produces and where
3. `repos/harbor-verl-train/README.md` — full upstream pipeline docs

## Architecture

```
claude-code (in k8s pod, mounted runtime image)
  → LiteLLM proxy on training host :8002 (Anthropic API surface,
    trajectory_logger callback writes per-trial JSONL)
    → vLLM (verl-managed, DP×TP, OpenAI API surface)
```

verl drives the training loop and Harbor executes each trial in a fresh k8s
pod. The verifier inside the pod produces the reward signal that verl uses
for PPO/GRPO/GSPO updates.

## Repos (submodules under `repos/`)

| Repo | URL | Pinned to |
|---|---|---|
| `harbor-verl-train` | `Elvin-Yiming-Du/harbor-verl-train` | branch `ydu_dev` |
| `harbor` | `SWE-Lego/harbor` | branch `ydu_dev` (commit `9f98f9d`) |
| `verl` | `verl-project/verl` | commit `bcb638649` (patched) |

`harbor` and `verl` commits are what `repos/harbor-verl-train/scripts/setup_env.sh`
expects. The patch in `harbor-verl-train/patches/verl_bcb638649.patch` is applied
to verl during setup.

## Environment

Default path: `setup_env.sh` builds a fresh venv at
`repos/harbor-verl-train/.venv` via `uv` + `pip install -e` for harbor / verl /
harbor-verl-train and pulls in pinned `vllm`, `flash_attn`, `cupy`,
`transformers`. The first run of `scripts/start.sh` triggers this if the venv
is missing.

To reuse an existing venv (e.g. a sibling block already bootstrapped one), set
`runtime_info.input.environment.venv_path` in `config.yaml` to that venv's
absolute path. The wrapper exports `VENV_PATH` and the upstream
`sync_1nodes_cc.sh` sources `$VENV_PATH/bin/activate` instead of bootstrapping.

> WARNING: the venv must have **harbor / verl / harbor-verl-train installed
> editable from the same source trees you intend to run**. A venv whose editable
> paths point elsewhere will silently run different code than what's under
> `repos/`. Verify with:
> `$VENV_PATH/bin/python -c "import harbor, verl, verl_patch; print(harbor.__file__)"`

## Configuration

All knobs live in `config.yaml`. Two tiers:

| Tier | Sections | Plumbed via |
|---|---|---|
| **Env-driven** (live) | `model`, `data`, `infrastructure`, `environment`, `k8s`, `harbor_agent`, `harbor_runtime`, `experiment`, `credentials` | `scripts/train_1node_cc.sh` exports them as env vars consumed by `sync_1nodes_cc.sh` and forwarded into the Ray runtime env |
| **Upstream-fixed** (documentation only) | `vllm`, `training`, `algorithm` | hardcoded in `repos/harbor-verl-train/scripts/sync_1nodes_cc.sh`. To change, edit the upstream script (or fork it) — they are mirrored here so this file documents the live state. |

### Common edits

- **Switch model**: `runtime_info.input.model.model_path` (re-check `vllm.gen_tp` divides `num_key_value_heads` — `dryrun.sh` validates this).
- **Different k8s cluster**: `runtime_info.input.k8s.kubeconfig`.
- **Reuse a prebuilt venv**: `runtime_info.input.environment.venv_path` (skips `setup_env.sh`; see Environment section above for the editable-install gotcha).
- **Bump parallelism**: `harbor_runtime.num_workers` (16 cold-start; 32–96 steady).
- **Enable tail-killer**: `harbor_runtime.tail_kill_target=0.95` (kills slowest 5% per step after `tail_kill_grace_sec=180`).
- **wandb**: NEVER hardcode the key in `config.yaml` — keep `credentials.wandb_api_key: ""` and `export WANDB_API_KEY=...` in your shell before launch (or set `wandb_mode: disabled` to opt out). `dryrun.sh` checks both sources.

## Execution

```bash
# 0. (one-time) bootstrap venv + clone+patch verl/harbor
bash repos/harbor-verl-train/scripts/setup_env.sh

# 1. validate config + paths + GPU + KV-head divisibility
bash scripts/dryrun.sh

# 2. launch a training run
bash scripts/start.sh

# 3. clean transient state between runs
bash scripts/clean.sh                # ray+litellm temp only
bash scripts/clean.sh --logs --pods  # also wipe logs and orphan k8s pods
```

`scripts/start.sh` auto-runs `setup_env.sh` if `.venv` is missing, then execs
`scripts/train_1node_cc.sh`, which is a thin wrapper that reads `config.yaml`
and execs `repos/harbor-verl-train/scripts/sync_1nodes_cc.sh`.

## Health Checks (during a run)

The chain is `claude-code → LiteLLM :8002 → vLLM (verl-managed DP replicas)`.

LiteLLM is started **after** verl boots (it discovers vLLM addresses from Ray
named actors `vllm_server_{i}_0`). On a 30B MoE TP=4 model, vLLM CUDA-graph
capture is ~10–20 min before the first replica registers — the launch script
waits up to 30 min.

```bash
# 1. LiteLLM-reported endpoint health
curl -sS http://127.0.0.1:8002/health/liveliness

# 2. End-to-end inference ping (replace model name with served_model_name from config.yaml)
curl -sS --max-time 30 http://127.0.0.1:8002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"vllm_model","messages":[{"role":"user","content":"ping"}],"max_tokens":3}'

# 3. GPU utilization (during rollout)
watch -n 2 nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader

# 4. Ray-registered vLLM actors
python3 -c "import ray; ray.init(address='auto', ignore_reinit_error=True); \
  print([a for a in ray.util.list_named_actors(all_namespaces=True) if 'vllm_server' in a['name']])"
```

| Symptom | Likely cause |
|---|---|
| `connection refused` on :8002 for >30 min | vLLM not registering — check `logs/<exp_name>.log` for vLLM init errors |
| GPU memory high, util 0% sustained during rollout | k8s side stuck (no traffic from claude-code pods) — `kubectl get pods -l harbor-run=swe-lego-live-rl` |
| `CUDA error: an illegal memory access` at first forward pass | `vllm.gen_tp` does not divide `num_key_value_heads` — re-run `dryrun.sh` |
| LiteLLM started but `claude-code` returns 404 | model name mismatch — proxy serves `claude-*`, `hosted_vllm/<served>`, and `<served>` aliases |

## Artifact Archiving

Per-run outputs land at:
- `repos/harbor-verl-train/logs/<exp_name>.log` — main training log
- `repos/harbor-verl-train/logs/<exp_name>_vllm.log` — throughput-only filtered log
- `repos/harbor-verl-train/harbor_trials/<project>/<exp>/<trial_id>/litellm-trajectory.jsonl` — per-trial agent traffic
- `repos/harbor-verl-train/checkpoints/` — actor model shards (per `save_freq`)

After a meaningful run, copy the relevant subset under
`artifacts/runs/<exp_name>/` (config snapshot, log tail, trajectory sample,
metadata.yaml). Do not commit checkpoints.

## Memory

Long-form notes — architecture rationale, experiment log, design decisions —
live in `dashboard/memory/` (preserved from the legacy layout).

## Long-running Backgrounding

Training takes hours. To detach from your shell:

- `tmux` / `screen` are the cleanest options — install separately
  (`apt-get install tmux`) if missing.
- Otherwise `nohup setsid bash scripts/start.sh > logs/launch.log 2>&1 < /dev/null &`
  works on this image (both `nohup` and `setsid` are present).

If you need to run on a different machine, `meta_info.resources.ip` is the
intended SSH target; this block has no built-in remote runner so wire it up
yourself for now.

## Legacy Backup

The pre-`ydu_dev` versions of `artifacts/`, `scripts/`, `CLAUDE.md`, and
`config.yaml` (last update 2026-05-06, written for the rLLM/Hydra-override
launch path) are preserved at:
`/mnt/ydu/SWE-Lego-Live-RL-rl-legacy-backup/`.
