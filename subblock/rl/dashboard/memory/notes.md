# Training Notes

## Architecture

```
Coding Agent (K8s pod, configurable — e.g. Claude Code)
    └─▶  LiteLLM proxy (:8001)    ← agent API → OpenAI format + trajectory logging
               └─▶  vLLM (:8000)  ← configurable LLM (set in inputs/index.yaml; default Qwen3-30B-A3B-Instruct-2507), TP=vllm_tp_size
                                     managed by verl hybrid engine (Actor+Rollout co-located)
```

**Node layout (2-node):**
- Node 0: `192.168.0.44` — rank-0, hosts Ray head, LiteLLM proxy, FSDP actor shards 0–7
- Node 1: `192.168.0.47` — rank-1, FSDP actor shards 8–15; AsyncvLLMServer may land here

**Port assignments (this repo):**
| Port | Service |
|---|---|
| 8000 | vLLM (AsyncvLLMServer, OpenAI format) |
| 8001 | LiteLLM proxy (Anthropic → OpenAI) |

> Note: rllm-harbor uses port 8002 for LiteLLM. This repo uses 8001. Keep configs consistent.

## Key Config Gotchas

- `val_before_train=True` must stay enabled — it also initializes vLLM SPMD workers.
  Disabling it causes CPU tensor crash on the first training step.
- `raise_on_error=False` in `AgentWorkflowEngine` — without this, any permanently
  failing task brings down the entire training run.
- `WANDB_RESUME=allow` + `WANDB_RUN_ID=3qi5pkgu` — set these to continue plotting
  on the existing WandB curves rather than starting a new run.
- `SERVED_MODEL_NAME` must NOT contain `/` — LiteLLM routing constraint.
- Trajectory log path is controlled at runtime via `/tmp/trajectory_output_dir.txt`.
  Copy logs to persistent storage after each run before the next run overwrites them.

## Experiment Log

| Date | Variant | Nodes | TP | Batch | Parallel | Notes |
|---|---|---|---|---|---|---|
| 2026-03 | qwen3_30b_coder v002 | 1 | 8 | 64 | 32 | Baseline coder run |
| 2026-03 | qwen3_30b_coder two-node v42 | 2 | 16 | 64 | 48 | 2-node coder, WandB run 3qi5pkgu |
| 2026-04-16 | qwen3_30b_instruct_2node | 2 | 16 | 64 | 48 | Instruct model variant (most recent) |

## Known Failure Modes

See `examples/harbor/scripts/TRAINING_PIPELINE.md §8` for full details.

| # | Issue | Fix location |
|---|---|---|
| 8.1 | vLLM streaming tool parser IndexError | pending — patch qwen3coder_tool_parser.py |
| 8.2 | raise_on_error crash | agent_workflow_trainer.py |
| 8.3 | Empty trajectory tokenize crash | harbor_workflow.py `_make_empty_episode()` |
| 8.4 | All tasks fail → pad_sequence crash | agent_workflow_engine.py |
| 8.5 | val_before_train=False CPU tensor | always keep val_before_train=True |
| 8.6 | output_config WARNING spam | vllm/entrypoints/openai/protocol.py |
| 8.7 | First chat_history message role check | harbor_workflow.py |

## Open Questions

- Instruct vs Coder model: which converges faster on binary SWE-bench reward?
- Optimal `N_PARALLEL_TASKS` for 2-node TP=16 (currently 48)?
- How to introduce partial-credit reward beyond binary 0/1?
