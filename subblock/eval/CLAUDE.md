# eval

Runs registered evaluation benchmarks (SWE-bench Verified, SWE-bench Multilingual,
SWE-bench Pro, Terminal-Bench 2.0, plus 100-task subsets and a handful of
general coding benchmarks) through Harbor against a configurable upstream LLM.

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `<repo_root>/.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` (resolve from the repo root, not from this block's directory). Every agent with this repo SHOULD READ that file before any actions.

## Block Identity

- **Name**: eval
- **Role**: Evaluation - runs Harbor benchmarks end-to-end and stores per-task results / trajectories
- **Parent**: swe_lego_live
- **Children**: none (leaf block)

## What To Read First

1. `config.yaml` — block identity, Harbor config, runtime values (one-shot per run; live state in `artifacts/index.yaml`)
2. `memory/overview.mdx` — current state narrative

## Input/Output Contract

**Inputs** (read from `config.yaml`):
- `meta_info.repositories.harbor`: Harbor Git URL, branch, commit, path, and read-only policy
- `meta_info.environment`: Harbor uv environment path and LiteLLM venv path
- `runtime_info.input.llm_api`: raw upstream API config (model, api_base_url, api_key, optional token costs) — used to build the per-job LiteLLM proxy config
- `runtime_info.input.litellm_proxy`: LiteLLM config template, port, and master key
- `runtime_info.input.task_source`: benchmark selection — `provider: harbor_registry`, `dataset_name` (one of `swebench-verified`, `swebench-verified-100`, `swebench_multilingual`, `swebench_multilingual-100`, `swebenchpro`, `swebenchpro-100`, `terminal-bench`, `aider-polyglot`, `livecodebench`, `humanevalfix`, `bigcodebench-hard-complete`), `version`, and `registry_path` pointing at `repos/harbor/registry.json`
- `runtime_info.input.harbor_job`: jobs directory, concurrency, retries, timeout multiplier, optional `n_tasks` smoke cap
- `runtime_info.input.agent`: agent name, version, runtime image, max turns, temperature
- `environment.extra.HARBOR_EXCLUDE_TASKS`: space-separated list of task IDs Harbor must skip (prior timeouts/OOMs)

**Outputs** (written to `config.yaml` → `runtime_info.output`):
- `eval_results_dir`: Harbor job directories with per-task evaluation outputs at `artifacts/jobs/<job>/<task>/{agent,evaluation}/`, plus LiteLLM trajectory logs at `artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`

## Benchmark Selection

The eval block is registry-driven: it does not stage tasks locally. Switch benchmark
by editing two fields in `config.yaml → runtime_info.input.task_source`:

| `dataset_name` | `version` | Tasks | Source |
|---|---|---|---|
| `swebench-verified` | `1.0` | 500 | human-validated SWE-bench |
| `swebench-verified-100` | `1.0` | 100 | 100-task subset of swebench-verified |
| `swebench_multilingual` | `1.0` | 300 | multilingual SWE-bench |
| `swebench_multilingual-100` | `1.0` | 100 | random subset of swebench_multilingual (seed=42) |
| `swebenchpro` | `1.0` | 731 | SWE-bench Pro multi-language |
| `swebenchpro-100` | `1.0` | 100 | 100-task subset of swebenchpro |
| `terminal-bench` | `2.0` | 89 | Terminal-Bench 2.0 |
| `aider-polyglot` | `1.0` | 225 | polyglot code editing |
| `livecodebench` | `6.0` | 100 | competitive programming |
| `humanevalfix` | `1.0` | 164 | bug fixing on HumanEval programs |
| `bigcodebench-hard-complete` | `1.0.0` | 145 | function-level code completion (hard split) |

Harbor resolves `(dataset_name, version)` against `repos/harbor/registry.json` and
fetches the underlying task data automatically. No `artifacts/tasks/` staging is required.

For smoke runs you can either pick a `-100` subset above, or set
`runtime_info.input.harbor_job.n_tasks` to a small integer (Harbor will cap the run).

### Other registry entries

The table above is the **curated** set — these are the benchmarks validated to
run end-to-end under `agent.name: custom-claude-code`. The full
`registry.json` contains ~80 more entries (e.g. `aider-polyglot`,
`bigcodebench-hard-complete`, `livecodebench`, `terminal-bench-pro`,
`swesmith`, `swtbench-verified`, plus many non-code benchmarks like
`gpqa-diamond`, `aime`, `gaia`, `lawbench`, …). The scripts will accept any
of them as `dataset_name` without code changes, but two caveats apply:

- **Agent compatibility is your problem.** Math/MCQ/QA benchmarks (`aime`,
  `gpqa-diamond`, `simpleqa`, `mmau`, …) and benchmarks that ship their own
  agent runtime (`gaia`, `mlgym-bench`, `replicationbench`, …) generally do
  not work as-is with the custom Claude Code agent configured here. Verify
  against the adapter README under Harbor's `adapters/<name>/` before adding
  one to a production run profile.
- **List the full set with:**
  `python3 -c 'import json; [print(e["name"]+"@"+e["version"]) for e in json.load(open("repos/harbor/registry.json"))]'`

## Evaluating a custom local model (vLLM + LiteLLM)

By default this block evaluates a remote upstream API (`runtime_info.input.llm_api.api_base_url`).
To benchmark a **local checkpoint** (e.g. an SFT/RL output) instead, serve it with
vLLM and point `llm_api` at it — no code change to `start.sh` is needed, because
the block only ever talks to an OpenAI/Anthropic-compatible HTTP endpoint.

Topology (the eval node is CPU-only, so vLLM must run elsewhere):

```
eval node (CPU)                                  GPU node
  agent container → eval LiteLLM :4101  ─────────▶ vLLM :8000/v1  (local ckpt)
                    (start.sh, trajectory_logger)   api_base_url
```

The model-serving layer is **vLLM only**. Do not start a second LiteLLM next to
vLLM — `start.sh` already runs the per-job LiteLLM proxy (with `trajectory_logger`,
sticky routing, Anthropic-format support); a second proxy would collide on the
port and bypass that logging.

Steps:

0. **One-time, on the GPU node**, create the vLLM conda env (the eval node's
   `/eval:setup` does *not* build this — it only builds the CPU-side Harbor uv
   env + LiteLLM venv). For a standard bf16/fp16 checkpoint a plain pip install
   is enough:
   ```bash
   conda create -y -n vllm_0.18.1 python=3.12
   conda activate vllm_0.18.1
   pip install vllm==0.18.1
   ```
   Only FP8 models with custom kernels (e.g. GLM-5.1-FP8) need the heavy
   source build in `repos/harbor/scripts/serve_llm/install_vllm_32b717_cu128.sh`.
   `serve_local_model.sh` activates `$VLLM_CONDA_ENV` (default `vllm_0.18.1`)
   and errors with this exact recipe if `vllm` isn't found.

1. **On a GPU node**, serve the checkpoint:
   ```bash
   bash scripts/serve_local_model.sh   # runs in foreground; use tmux
   ```
   The checkpoint path and served name are read from `config.yaml →
   runtime_info.input.local_model_serving` (`model_path` / `model_name`) — that
   is the source of truth, not a hardcoded script default. Everything else is
   env-overridable (`MODEL_PATH`, `MODEL_NAME`, `VLLM_PORT`,
   `TENSOR_PARALLEL_SIZE`, `API_KEY`, `VLLM_CONDA_ENV`, …); it serves on `:8000`
   by default. When ready it prints the exact `llm_api` block to paste.
2. **On the eval node**, edit `config.yaml → runtime_info.input.llm_api` to the
   local-vLLM recipe (commented at the top of `runtime_info.input`): set
   `api_base_url: http://<GPU_NODE_IP>:8000/v1`, `model: openai/<MODEL_NAME>`,
   `api_key` matching vLLM's `--api-key`, and costs to `0.0`.
3. Run `scripts/dryrun.sh` (its `probe_llm_completion.sh` sends a real completion
   to verify the endpoint is live), then `scripts/start.sh` as usual.

## Repos

- `repos/harbor/`: Harbor runtime (managed local-only dependency, not committed to Live repo). The block reads `registry.json` from this checkout.

## How To Run

- `scripts/update_repos.sh`: clone or update repos/harbor at the pinned commit.
- `scripts/dryrun.sh`: validate config, Harbor repo state, environments, model API, and confirm the configured `(dataset_name, version)` exists in `registry.json`.
- `scripts/start.sh`: run dryrun preflight → generate LiteLLM config → start proxy on `runtime_info.input.litellm_proxy.port` → run Harbor job with `--dataset <name> --registry-path repos/harbor/registry.json` and any `--exclude-task-name` flags from `HARBOR_EXCLUDE_TASKS` → **post-eval job analysis** (unless disabled).
- `scripts/analyze_job.sh [<job_dir>]`: run the Harbor `job_analysis` pipeline on a completed job and write results into `<job_dir>/analysis/` (the layout the dashboard reads). No arg → newest job under `jobs_dir`. Safe to re-run and to run on old jobs. `start.sh` calls this automatically after each eval (non-fatal). If the gold dataset is missing it auto-invokes `prepare_dataset.sh` first (disable with `JOB_ANALYSIS_PREPARE_DATASET=0`).
- `scripts/prepare_dataset.sh [<dataset_name>]`: generate a Harbor gold dataset under `artifacts/datasets/<gold_base>/` for analysis — step 1 runs the matching `repos/harbor/adapters/<name>` adapter (from HuggingFace) to produce `tests/config.json` gold; step 2 runs `repos/harbor/scripts/task_analysis/tag_task_metadata.py` to complete each `task.toml`'s `[language, area, topic, bug_class]` + difficulty tags via an LLM. Idempotent (skips populated datasets unless `PREP_FORCE=1`). Tagging is best-effort: gold is still produced if it fails.
- `scripts/clean.sh`: remove gitignored runtime outputs (jobs/, litellm/, logs/). Pass `--repos` to also drop `repos/`.

Eval scripts need PyYAML in the runtime Python (used by inline `python3 -` config readers). If you see `ERROR: PyYAML is required`, `pip install pyyaml` into the active interpreter.

## Job Analysis

After each eval, `start.sh` runs `scripts/analyze_job.sh "$JOB_DIR"` to produce
attribution/scoring artifacts under `<job_dir>/analysis/` — the exact files the
dashboard (`dashboard/server.py`) reads: `report_failed/resolved.json`,
`report_task_analysis.json`, `traj_analysis/score_comparison.json`,
`instance_analysis/{summary,correlations}.json`, `instances.jsonl`, and a
self-contained `analysis_config.yaml`.

- **Opt-out**: set `runtime_info.input.job_analysis.enabled: false` in `config.yaml`.
- **Non-fatal**: a failed analysis never fails a completed eval run.
- **Cost**: LLM judge is off by default → pure-CPU, zero token cost. Enable with
  `JOB_ANALYSIS_JUDGE=1` (needs `ANTHROPIC_API_KEY`).
- **Engine**: uses `artifacts/env/harbor-uv` (has `scipy`+`pyyaml`); the read-only
  Harbor repo is only `cd`-ed into for `from src...` imports — all output lands in
  the writable job dir.
- **Gold dependency / auto-generation**: the pipeline needs the gold dataset at
  `artifacts/datasets/<gold_base>/` (per-instance `<id>/tests/config.json`). When
  absent, `analyze_job.sh` auto-runs `scripts/prepare_dataset.sh <dataset_name>`
  (adapter → tagger) to build it, then proceeds; if it still can't be produced it
  **skips cleanly** instead of crashing. Supported datasets:
  `swebench-verified` (adapter `swebench`, HF `princeton-nlp/SWE-bench_Verified`),
  `swebench_multilingual` (HF `SWE-bench/SWE-bench_Multilingual`),
  `swebenchpro` (HF `ScaleAI/SWE-bench_Pro`); `-100` subsets map to the same base.
  Generation needs network (HuggingFace) and the `harbor-uv` env (has `datasets` +
  `swebench`). No Docker — only the gold metadata files are written.
- **Tagging model**: `tag_task_metadata.py` POSTs to `<base_url>/chat/completions`
  and parses a JSON object — it needs a model that returns **clean JSON**. The
  default endpoint is the block's `llm_api`. A reasoning model that emits
  `<think>…` (e.g. `Qwen3-8B` served with thinking on) produces unparseable output
  and tags fail; point tagging at a JSON-clean / instruct endpoint via
  `PREP_TAG_BASE_URL` / `PREP_TAG_MODEL` / `PREP_TAG_API_KEY`. Tagging failure does
  not lose the gold — analysis still runs, only Language/Area breakdown is limited.
- **Caveat (Harbor-managed)**: `task_analysis` domain classification relies on
  `repos/harbor/scripts/job_analysis/src/task_analysis/classifier.py`'s repo→domain
  map, which is Python-SWE-bench-Verified-centric. For multilingual repos
  (apache/druid, lucene, …) domain falls back to `other`. Any local customization
  of that classifier is overwritten by `update_repos.sh` — keep such tweaks out of
  the read-only repo.

## Repository Policy

Harbor is a managed local dependency. Do not edit Harbor source inside this Live block. Use `scripts/update_repos.sh` to clone/fetch/checkout the configured ref. The script refuses to update if the Harbor worktree has local modifications.

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing metadata.yaml, config snapshot, scripts copy, session.log, and monitor.md. Append entry to `artifacts/index.yaml`.

## Memory

Long-form notes, repo policy, and operational decisions are kept in `memory/`.

## Remote Execution

This block runs on the node declared in `config.yaml` → `meta_info.resources.ip` (currently `192.168.35.240`).

- If your shell is on a **different** host: SSH into `192.168.35.240` and operate inside a tmux session there — never invoke this block's scripts from a different node.
- If your shell is **already on** `192.168.35.240`: skip the SSH step and run scripts directly in a local tmux session (`tmux new-session -d -s eval …`).

Either way, all execution must happen on the configured IP, in a named tmux session (e.g. `eval`), so the run survives shell disconnects.
