# Training Notes

## Architecture

```
Raw trajectories (jierun / chaofan format, per-scaffold)
    └─▶  python -m swe_data_process.<scaffold>.convert_*
              └─▶  Intermediate "IM" format (OpenAI-style JSONL, with _score fields)
                        └─▶  rule_score.py (auto-invoked) + optional llm_score.py
                                  └─▶  LLaMA-Factory "LF" format (sharegpt JSON)
                                            └─▶  artifacts/data/lf_data/<dataset>.json
                                                      └─▶  llamafactory-cli train <config.yaml>
                                                                └─▶  artifacts/model/<run>/
```

**Node layout:**
- Single node: 8× GPU (A100/H100)
- Training framework: LLaMA-Factory + DeepSpeed ZeRO-3
- Effective global batch size: `per_device_train_batch_size × gradient_accumulation_steps × n_gpus`

## Data Flow Details

### Scaffold / Source Matrix

| Source | Scaffold | Converter script |
|---|---|---|
| jierun | claude-code | `claudecode_opencode/convert_cc_jierun_to_im.py` |
| jierun | open-code | `claudecode_opencode/convert_oc_jierun_to_im.py` |
| jierun | openhands-sdk | `openhands/convert_openhands_sdk_jierun_to_im.py` |
| jierun | terminus2 | `terminus2/convert_terminus2_jierun_to_im.py` |
| chaofan | claude-code | `claudecode_opencode/convert_cc_chaofan_to_im.py` |
| chaofan | open-code | `claudecode_opencode/convert_oc_chaofan_to_im.py` |
| chaofan | openhands | `openhands/convert_openhands_chaofan_to_im.py` |
| chaofan | openhands-sdk | `openhands/convert_openhands_sdk_chaofan_to_im.py` |
| chaofan | terminus2 | `terminus2/convert_terminus2_chaofan_to_im.py` |

### IM Format

```json
{
  "messages": [{"role": "user/assistant/tool", "content": "...", "reasoning_content": "...", "tool_calls": [...]}],
  "tools": [...],
  "think_mode": "slow|fast",
  "_instance_id": "owner__repo-123",
  "_agent_type": "main|subagent",
  "_score": {"composite_score": 0.72, "efficiency_score": 0.68, ...}
}
```

`_agent_type`: CC/OC instances may produce multiple records (one main + zero or more subagents).
Only main records are scored; subagents get `_score: null`.

### Repo Exclusion Filter

`artifacts/data/excluded_repos.txt` contains 64 `owner/repo` entries from SWE-bench_Verified,
SWE-bench_Pro, and SWE-bench_Multilingual. All converters default to filtering these out.
Regenerate with `scripts/generate_excluded_repos.py`.

## Key Config Gotchas

- `FORCE_TORCHRUN=1` must be set before `llamafactory-cli train` to enable distributed training.
- `NPROC_PER_NODE` is set from `infrastructure.n_gpus_per_node`; keep it aligned with the
  actual visible GPU count on the current node.
- `template: qwen3_nothink` disables thinking mode in the Qwen3 chat template.
  Use `qwen3` if training with chain-of-thought (`reasoning_content` present).
- `rope_scaling: yarn` is required for `cutoff_len > 32768`.
- `save_only_model: true` skips saving optimizer state — saves disk but prevents resuming.
- `resume_from_checkpoint: null` — set to checkpoint dir path to resume.
- `overwrite_output_dir: true` will silently overwrite an existing checkpoint directory.
- LF output and `dataset_info.json` both live in `artifacts/data/lf_data/`;
  the generated YAML sets `dataset_dir` to point there.
- `experiment.wandb_mode=disabled` generates `report_to: none`; `online` requires
  `credentials.wandb_api_key`; `offline` does not.
- The experiment tracking table (`artifacts/实验追踪表.xlsx`) is the single source of truth.
  `scripts/update_tracking.py` appends rows automatically after each training run.
- `scripts/dataprep.sh` runs data conversion only (STEP 0), no dataset registration or training.
  Useful for preparing data independently.
- `scripts/update_status.py` generates `dashboard/status.mdx` with live training state.
  Auto-launched by `train.sh` in background (30s interval); also works standalone.
- `outputs.yaml` uses a unified format: each entry has `description`, `value`, `note`.
  Includes training metrics extracted from `train_results.json`, `trainer_state.json`,
  and `training_loss.png` in the output directory.
- `memory/inputs_参数说明.md` is the Chinese reference for all `inputs.yaml` parameters.

## Experiment Log

| Date | Model | Dataset | Config | Notes |
|---|---|---|---|---|
| (fill in) | Qwen3-8B | — | — | — |

## Known Failure Modes

| # | Issue | Fix |
|---|---|---|
| 1 | `FORCE_TORCHRUN=1` missing → single-GPU run on 8-GPU node | Always set in train.sh |
| 2 | Dataset key points to an old LF filename in `dataset_info.json` | Let train.sh update the mapping or pick a new dataset.name |
| 3 | `cutoff_len > 32768` without `rope_scaling: yarn` → position embedding OOM | Add rope_scaling |
| 4 | `overwrite_output_dir: true` overwrites in-progress checkpoint | Rename output_dir or set to false |
| 5 | WandB online mode without an API key | Set `credentials.wandb_api_key` or switch to offline/disabled |
