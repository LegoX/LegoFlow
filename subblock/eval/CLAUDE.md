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
2. `dashboard/overview.mdx` — current state narrative

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

## Repos

- `repos/harbor/`: Harbor runtime (managed local-only dependency, not committed to Live repo). The block reads `registry.json` from this checkout.

## How To Run

- `scripts/update_repos.sh`: clone or update repos/harbor at the pinned commit.
- `scripts/dryrun.sh`: validate config, Harbor repo state, environments, model API, and confirm the configured `(dataset_name, version)` exists in `registry.json`.
- `scripts/start.sh`: run dryrun preflight → generate LiteLLM config → start proxy on `runtime_info.input.litellm_proxy.port` → run Harbor job with `--dataset <name> --registry-path repos/harbor/registry.json` and any `--exclude-task-name` flags from `HARBOR_EXCLUDE_TASKS`.
- `scripts/clean.sh`: remove gitignored runtime outputs (jobs/, litellm/, logs/). Pass `--repos` to also drop `repos/`.

Eval scripts need PyYAML in the runtime Python (used by inline `python3 -` config readers). If you see `ERROR: PyYAML is required`, `pip install pyyaml` into the active interpreter.

## Repository Policy

Harbor is a managed local dependency. Do not edit Harbor source inside this Live block. Use `scripts/update_repos.sh` to clone/fetch/checkout the configured ref. The script refuses to update if the Harbor worktree has local modifications.

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing metadata.yaml, config snapshot, scripts copy, session.log, and monitor.md. Append entry to `artifacts/index.yaml`.

## Memory

Long-form notes, repo policy, and operational decisions are kept in `dashboard/memory/`.

## Remote Execution

This block runs on the node declared in `config.yaml` → `meta_info.resources.ip` (currently `192.168.35.240`).

- If your shell is on a **different** host: SSH into `192.168.35.240` and operate inside a tmux session there — never invoke this block's scripts from a different node.
- If your shell is **already on** `192.168.35.240`: skip the SSH step and run scripts directly in a local tmux session (`tmux new-session -d -s eval …`).

Either way, all execution must happen on the configured IP, in a named tmux session (e.g. `eval`), so the run survives shell disconnects.
