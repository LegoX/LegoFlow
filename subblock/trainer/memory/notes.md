# Training Notes

## Architecture

```
Harbor job trajectories (job_dir, per-scaffold)
    └─▶  python -m swe_data_process.<subpackage>.convert_*_to_im
              └─▶  Intermediate "IM" format (PangUML v2 JSONL, score in meta_info.unique_info)
                        └─▶  rule_score.py (auto-invoked) + optional llm_score.py
                                  └─▶  LLaMA-Factory "LF" format (ShareGPT JSON)
                                            └─▶  artifacts/data/lf_data/<dataset>.json
                                                      └─▶  python -m llamafactory.cli train <generated_config.yaml>
                                                                └─▶  artifacts/model/<run>/
```

**Node layout:**
- Single node: 8× GPU (A100/H100)
- Training framework: LLaMA-Factory + DeepSpeed ZeRO-3
- Effective global batch size: `per_device_train_batch_size × gradient_accumulation_steps × n_gpus`

## Data Flow Details

### Scaffold / Source Matrix

| Scaffold | Converter module |
|---|---|
| claude-code | `swe_data_process.claudecode_opencode.convert_cc_to_im` |
| open-code | `swe_data_process.claudecode_opencode.convert_oc_to_im` |
| openhands-sdk | `swe_data_process.openhands.convert_openhands_sdk_to_im` |
| terminus2 | `swe_data_process.terminus2.convert_terminus2_to_im` |

### IM Format

```json
{
  "version": "2.0.0",
  "meta_info": {
    "unique_info": {
      "_instance_id": "owner__repo-123",
      "_agent_type": "main",
      "_score": {"composite_score": 0.72, ...}
    }
  },
  "tools": [...],
  "messages": [{"role": "user/assistant/tool", "content": "...", "reasoning_content": "...", "tool_calls": [...]}],
}
```

`_instance_id`, `_agent_type`, and `_score` are stored under `meta_info.unique_info` on disk. The package `load_jsonl()` expands them back to the legacy top-level shape for internal scoring/filtering helpers.

### Repo Exclusion Filter

`artifacts/data/excluded_repos.txt` contains 64 `owner/repo` entries from SWE-bench_Verified,
SWE-bench_Pro, and SWE-bench_Multilingual. All converters default to filtering these out.
Regenerate with `scripts/generate_excluded_repos.py`.

## Key Config Gotchas

- The SFT Python environment is a uv venv at `meta_info.environment.sft_uv`
  (default `artifacts/env/lf`). Use `bash scripts/install_env.sh` to recreate it.
  The installer uses the Tsinghua PyPI mirror for general packages, installs
  `swe_data_process[llm]`, installs PyTorch 2.8.0 / torchvision 0.23.0 /
  torchaudio 2.8.0 from the CUDA 12.8 PyTorch index, installs
  `LLaMA-Factory[torch,metrics,deepspeed,liger-kernel]` with `--no-build-isolation`,
  installs the pinned flash-attn 2.8.3 wheel from `artifacts/wheels/`, and installs `wandb`.
- `FORCE_TORCHRUN=1` must be set before `python -m llamafactory.cli train` to enable distributed training.
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
- `source.type` selects the dataset source: `harbor_job` (default, convert trajectories),
  `hf_lf` (register `hf_hub_url`, load from the HuggingFace Hub at train time), or `local_lf`
  (register an existing LF json by absolute `file_name`). `hf_lf`/`local_lf` skip STEP 0; an
  empty/absent `source.type` defaults to `harbor_job`. For ready-made sources, `conversion.data_name`
  is still the dataset key and `conversion.max_instances` (>0) becomes the entry's `num_samples`.
  Private HF datasets need `credentials.hf_token`. Scoring + repo-exclusion only run during
  conversion, so clean `hf_lf`/`local_lf` data upstream.
- `scripts/dataprep.sh` runs data conversion only (STEP 0), no dataset registration or training.
  Useful for preparing data independently. It exits early for `source.type` = `hf_lf`/`local_lf`
  (nothing to convert).
- Live training progress is served by the dashboard webui (`dashboard/start_dashboard.sh`),
  which reads `trainer_log.jsonl` / `trainer_state.json` from `artifacts/model/<run>/` directly.
- `config.yaml.runtime_info.output` is updated after training with checkpoint, metrics,
  and artifacts. The update preserves the rest of `config.yaml`
  instead of re-dumping the whole file.
- Relative `training.output_dir` values write to `artifacts/model/<basename>`. Absolute
  `training.output_dir` values are honored consistently by training and the dashboard.
- `scripts/clean.sh` cleans script-local `__pycache__` by default. Use
  `--repo-cache --yes` only when you intentionally want to clean cache files under `repos/`.

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
| 6 | Partial conversion output exists (only IM or LF file) | Delete the partial file or restore the missing pair before rerunning |
