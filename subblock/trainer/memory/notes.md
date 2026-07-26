# Training Notes

## Architecture

```text
harbor_job ─▶ convert trajectory → IM → score/filter → LF JSON ─┐
hf_lf + hf_file_name ─▶ download one exact Hub file ────────────┤
hf_lf (no file name) ─▶ register Hub dataset config/split ──────┤
local_lf ─▶ register an existing local LF JSON ─────────────────┘
                                                               │
                                                               ▼
                                    dataset registration → LLaMA-Factory train
                                                               │
                                                               ▼
                                                  artifacts/model/<run>/
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

`scripts/excluded_repos.txt` contains 64 `owner/repo` entries from SWE-bench_Verified,
SWE-bench_Pro, and SWE-bench_Multilingual. All converters default to filtering these out.
Regenerate with `scripts/generate_excluded_repos.py`.

## Key Config Gotchas

- The SFT Python environment is a uv venv at `meta_info.environment.sft_uv`
  (default `artifacts/env/lf`). Use `bash scripts/install_env.sh` to recreate it.
  The installer uses the Tsinghua PyPI mirror for general packages, installs
  PyTorch 2.10.0 / torchvision 0.25.0 / torchaudio 2.10.0 from the CUDA 12.8
  PyTorch index, installs editable `swe_data_process[llm]` and the patched
  LLaMA-Factory plus metrics/DeepSpeed/Liger requirements, builds `flash-attn`,
  installs Qwen3.5's `flash-linear-attention` and `tilelang`, and installs `wandb`.
- `FORCE_TORCHRUN=1` must be set before `python -m llamafactory.cli train` to enable distributed training.
- `NPROC_PER_NODE` is set from `infrastructure.n_gpus_per_node`; keep it aligned with the
  actual visible GPU count on the current node.
- Qwen3 uses `qwen3` or `qwen3_nothink`; Qwen3.5 uses `qwen3_5` or
  `qwen3_5_nothink`. Match the template to the base model and whether the
  training records preserve thinking content. The active config uses `qwen3_5`.
- Enable a model-supported RoPE scaling method such as `yarn` only when
  `cutoff_len` exceeds that model's native `max_position_embeddings` or
  documented context length; 32768 is not a universal threshold.
- `save_only_model: true` skips saving optimizer state — saves disk but prevents resuming.
- `resume_from_checkpoint: null` — set to checkpoint dir path to resume.
- Non-empty output directories are rejected unless a checkpoint resume is configured
  or both explicit overwrite controls are set.
- LF output and `dataset_info.json` both live in `artifacts/data/lf_data/`;
  the generated YAML sets `dataset_dir` to point there.
- `experiment.wandb_mode=disabled` generates `report_to: none`; `online` requires
  `WANDB_API_KEY` in the private runtime environment; `offline` does not.
- `source.type` selects the dataset source: `harbor_job` (default, convert trajectories),
  `hf_lf`, or `local_lf` (register an existing LF json by absolute `file_name`). With `hf_lf`,
  setting `source.hf_file_name` downloads that exact file in STEP 0 and registers it locally;
  leaving it empty registers `hf_hub_url` plus the optional subset/split for train-time loading.
  An empty/absent `source.type` defaults to `harbor_job`. For ready-made sources,
  `conversion.data_name` remains the dataset key and `conversion.max_instances` (>0) becomes
  the entry's `num_samples`. Private HF datasets use `HF_TOKEN` from the runtime. Scoring and
  repo exclusion only run during conversion, so clean `hf_lf`/`local_lf` data upstream.
- `scripts/dataprep.sh` runs data conversion only (STEP 0), no dataset registration or training.
  Useful for preparing Harbor data independently. It exits early for `hf_lf`/`local_lf`;
  `scripts/train.sh` performs any configured exact-Hub-file download.
- Live training progress is served by the dashboard webui (`dashboard/start_dashboard.sh`),
  which reads `trainer_log.jsonl` / `trainer_state.json` from `artifacts/model/<run>/` directly.
- `config.yaml.runtime_info.output` is updated after training with checkpoint, metrics,
  and artifacts. The update preserves the rest of `config.yaml`
  instead of re-dumping the whole file.
- Relative `training.output_dir` values write to `artifacts/model/<basename>`. Absolute
  `training.output_dir` values are honored consistently by training and the dashboard.
- `scripts/clean.sh` with no flags removes only a run's temporary output under
  `artifacts/` (logs, the generated training YAML, offline WandB state) and keeps
  the uv env, `data/` and `model/`. `--all` wipes `artifacts/` except git-tracked
  files and confirms twice. It never touches `repos/`.

## Experiment Log

| Date | Model | Dataset | Config | Notes |
|---|---|---|---|---|
| (fill in) | — | — | — | — |

## Known Failure Modes

| # | Issue | Fix |
|---|---|---|
| 1 | `FORCE_TORCHRUN=1` missing → single-GPU run on 8-GPU node | Always set in train.sh |
| 2 | Dataset key points to an old LF filename in `dataset_info.json` | Let train.sh update the mapping or pick a new dataset.name |
| 3 | `cutoff_len` exceeds the model's native context window without compatible scaling | Enable a RoPE scaling method supported by that model |
| 4 | Existing output directory blocks launch | Rename output_dir, resume explicitly, or acknowledge destructive overwrite |
| 5 | WandB online mode without an API key | Export `WANDB_API_KEY` or switch to offline/disabled |
| 6 | Partial conversion output exists (only IM or LF file) | Delete the partial file or restore the missing pair before rerunning |
