# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# SWE Lego Live

Root orchestration block for the self-evolving LLM development pipeline. Coordinates data curation (curator), trajectory generation (tracer), and supervised fine-tuning (trainer) in sequence, with a standalone evaluator.

## Block System

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md`. Every agent with this repo SHOULD READ that file before any actions.

### config.yaml schema

Every block's `config.yaml` — root included — follows this structure (two top-level sections only):

```yaml
meta_info:
  name, label, description, parent
  subblocks:         # parent blocks only: children with a role one-liner — NO wiring here
    <child>: {role: "<one phrase>"}
  dependencies:      # this block's own upstream AND downstream edges (mandatory; both keys always present)
    from:            # upstream hand-offs this block consumes
      <input.dot.path>: <source_block>.output.<key>          # required dep
      <input.dot.path>:                                       # conditional / optional dep
        from: <source_block>.output.<key>
        when: {<input.dot.path>: <value>}                     # enforced only while matching
        required: false                                       # null producer output -> warn
    to:              # downstream hand-offs this block's own outputs feed (mirror, owned by the producer)
      <output_key>: <consumer_block>.input.<their.dot.path>
      <output_key>:
        to: <consumer_block>.input.<their.dot.path>
        when: {<consumer_block>.input.<path>: <value>}        # fully-qualified — condition lives on the consumer
  repos: {}          # name → {commit_id, role}
  resources:
    ip:              # 'local' (default) or null = run on current host; remote IP = run via SSH+tmux
    directory:       # working directory on remote node (only used when ip is a remote IP)

runtime_info:
  input: {}          # ONLY external values. Fill markers: `human` = must fill before a run;
                     # "" = auto-derived or env-supplied; anything else = working default
  output: {}         # values produced for downstream blocks; each key is a mapping with
                     # `path` (static) and/or `value` (run-produced, null until written back)
```

`config.yaml` is **one-shot per run**: every key is configuration. Live state (running / completed / failed) lives in `artifacts/index.yaml` (written automatically by `scripts/archive_run.sh`'s EXIT trap), not in `config.yaml`. Legacy `status:` / `evolving:` sections are retired.

**Wiring rule**: `meta_info.dependencies` shows both directions from each block's own file. `from` is declared by the **consumer** — the key is the dot-path in that block's `runtime_info.input` that receives the value, never a freeform label. `to` is declared by the **producer** — the key is one of its own `runtime_info.output` keys, the value names the exact consumer input field. The same edge is declared on both ends; the validator cross-checks them. Only values originating outside the block tree go in `runtime_info.input`.

**Enforcement**: `python3 scripts/validate_config.py --root .` validates the whole tree (schema, dependency resolution in both directions, fill markers, path consistency, `from`/`to` drift); `--block subblock/<name>` validates one block. Every block's `dryrun.sh` and the `:check` skills run it.

### Execution location rule

**Default: run locally.** Unless explicitly told otherwise, agents should treat `meta_info.resources.ip: local` (or null) as the intended setting and execute on the current host inside a local tmux session — no SSH, no rsync. Do not "restore" an old remote IP found in git history or older CLAUDE.md revisions; the local default is intentional.

If — and only if — `meta_info.resources.ip` is set to a real remote IP, the agent **must** SSH into that node and run inside a tmux session there, and confirm with the user whether code needs to be synced or is already present at the remote path.

## Block Identity (Root)

- **Name**: swe_lego_live
- **Parent**: none
- **Children**: curator → tracer → trainer (+ evaluator, standalone)

## What To Read First

1. `config.yaml` (root) — the subblock roster; then each active subblock's `config.yaml` for identity, resources, dependency wiring, and runtime values. Live state is in each subblock's `artifacts/index.yaml`, not `config.yaml`.
2. `.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` — full block system specification

The root `config.yaml` holds orchestration identity only (subblock roster, roles); all external inputs and outputs are owned by the subblock configs listed below. Each subblock has its own `CLAUDE.md` agent contract.

## Input/Output Contract

The root block does not consume external inputs directly (`runtime_info.input: {}` in the root config). Required external values are filled into each active subblock's `runtime_info.input`:

**curator** (`subblock/curator/config.yaml` → `runtime_info.input`):
- PR collection tokens are provided through `GITHUB_TOKENS`, `GITHUB_TOKEN`, or an ignored local token file (`gh_token.txt`) — never through `config.yaml`
- `llm_api.api_key`, `llm_api.api_base_url`: OpenAI-compatible LLM endpoint
- `llm_api.pr_model`, `llm_api.task_model`: model names for PR evaluation and task completion

**tracer** (`subblock/tracer/config.yaml` → `runtime_info.input`):
- `llm_api.api_key`, `llm_api.api_base_url`, `llm_api.model`: OpenAI-compatible LLM endpoint and model used by the per-job LiteLLM proxy

**Outputs** (downstream-consumable artifacts):
- `curator.output.swe_tasks_dir`: verified SWE tasks under `subblock/curator/artifacts/swe_tasks/{lang}-cc/`. The authoritative manifest is `{lang}-cc/verifiable_tasks.txt` — only task IDs in that file have passed NOP/Oracle validation.
- `tracer.output.raw_trajectories_dir`: raw agent trajectories under `subblock/tracer/artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`
- `tracer.output.sft_data_dir`: LLaMA-Factory LF-format SFT JSON converted from those trajectories at `subblock/tracer/artifacts/sft_data/<job>/lf.json` (produced by `subblock/tracer/scripts/convert_trajectories.sh`, which runs the `swe_data_process` converters under their own uv env at `subblock/tracer/artifacts/env/swe-data-process-uv`)

**Producer→consumer contract**: tracer consumes **only** tasks listed in curator's `verifiable_tasks.txt`. `subblock/tracer/scripts/prepare_tasks.sh` enforces this by filtering through the manifest when copying from a local task source; task IDs already processed are tracked in `subblock/tracer/artifacts/processed_tasks.yaml` and re-excluded via `HARBOR_EXCLUDE_TASKS` in tracer's `config.yaml`. Each consumer declares its upstream in its own `meta_info.dependencies.from` (e.g. trainer wires `source.job_dir: {from: tracer.output.raw_trajectories_dir, when: {source.type: harbor_job}}`), mirrored by the producer's own `dependencies.to` (tracer wires `raw_trajectories_dir: {to: trainer.input.source.job_dir, when: {...}}`).

## How To Run

**Mandatory workflow: check → confirm → run.** Agents must never skip the confirmation step.

1. **Check**: Run `/root:check` (or `bash scripts/dryrun.sh` for a single block). This validates config, inputs, paths, GPUs, Docker/K8s connectivity, credentials, and model compatibility — all in one pass, with no side effects.
2. **Confirm**: Present the check results and run configuration summary to the user. **Wait for explicit user confirmation** ("yes", "go ahead", etc.) before proceeding. Never auto-launch — heavy operations (multi-hour GPU training, multi-container rollouts) are expensive and hard to reverse.
3. **Run**: Only after user confirmation, execute `/root:run` (or `bash scripts/start.sh`).

```bash
scripts/dryrun.sh   # validate config, inputs, and required paths (no side effects)
scripts/start.sh    # launch the wired curator/tracer jobs; PR collection is separate (ONLY after user confirms)
scripts/clean.sh    # remove a run's temporary output in every block (keeps envs,
                    # run records, and anything expensive to regenerate)
scripts/clean.sh --all   # wipe every block's artifacts/ except git-tracked files
                         # (destroys envs, datasets, checkpoints; confirms twice)
```

## Subblocks

All subblocks run **locally** by default (`meta_info.resources.ip: local`). Override to a remote IP only on explicit user request.

| Block | Execution | Key tool | Status |
|---|---|---|---|
| `subblock/curator/` | Local (CPU + Docker) | `swegen` CLI + GitHub API | Adaptive per-language task generation |
| `subblock/tracer/` | Local (CPU + Docker) | Harbor + LiteLLM proxy | Trajectory generation from SWE instances |
| `subblock/trainer/` | Local (needs 8× GPU) | LLaMA-Factory + DeepSpeed ZeRO-3 | SFT on Qwen3-8B |

Each subblock has its own `CLAUDE.md` with its full agent contract.

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing:

| File | Content |
|---|---|
| `metadata.yaml` | run id, timestamps, phase/stage, results, repo commit ids, copy of inputs |
| `config.yaml` | snapshot of config at run time |
| `scripts/` | copy of scripts executed |
| `repos/` | snapshot of repo state |
| `session.log` | Claude Code session record |
| `monitor.md` | agent monitor output |

Append one entry to `artifacts/index.yaml`:
```yaml
- id: run_001
  started_at: "..."
  completed_at: "..."
  status: completed   # running | completed | failed
  archive: artifacts/archives/run_001/
  notes: "one-line summary"
```

## Live State

Live state (what's running, what just finished) lives in the newest entry of each subblock's `artifacts/index.yaml`, written automatically by `scripts/archive_run.sh` (invoked from `start.sh`'s EXIT trap). `config.yaml` is one-shot per run and is not edited during execution.
