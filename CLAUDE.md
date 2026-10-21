# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# LegoFlow

Root orchestration block for the self-evolving LLM development pipeline. Coordinates a selected task producer (SWE `curator` or `terminal-curator`), trajectory generation (tracer), and supervised fine-tuning (trainer) in sequence, with a standalone evaluator.

## Block System

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `blocks/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md`. Every agent with this repo SHOULD READ that file before any actions.

### config.yaml schema

Every block's `config.yaml` — root included — follows this structure (two top-level sections only):

```yaml
meta_info:
  name, label, description, parent
  blocks:         # parent blocks only: children with a role one-liner — NO wiring here
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

**Enforcement**: `python3 scripts/validate_config.py --root .` validates the whole tree (schema, dependency resolution in both directions, fill markers, path consistency, `from`/`to` drift); `--block blocks/<name>` validates one block. Every block's `dryrun.sh` and the `:check` skills run it.

### Execution location rule

**Default: run locally.** Unless explicitly told otherwise, agents should treat `meta_info.resources.ip: local` (or null) as the intended setting and execute on the current host inside a local tmux session — no SSH, no rsync. Do not "restore" an old remote IP found in git history or older CLAUDE.md revisions; the local default is intentional.

If — and only if — `meta_info.resources.ip` is set to a real remote IP, the agent **must** SSH into that node and run inside a tmux session there, and confirm with the user whether code needs to be synced or is already present at the remote path.

## Block Identity (Root)

- **Name**: legoflow
- **Parent**: none
- **Children**: curator or terminal-curator → tracer → trainer (+ evaluator, standalone)

The local task producer is selected by `blocks/tracer/config.yaml` →
`runtime_info.input.task_source.local_block` (`curator` by default).
`terminal-curator` is an alternative producer, not an extra stage before SWE
curation. Root `:setup` / `:run` include the producer selected by Tracer and
also include `terminal-curator` when an in-scope Evaluator selects its
`terminal-curator@1.0` registry. With an `external` or non-local Tracer source
and a default evaluation benchmark, neither producer is launched. An explicitly targeted
block can still be set up or run on its own. Default checks cover the active
pipeline; an explicit full-tree check may inspect both producers, reporting
unused credentials separately. Root dashboards show all registered blocks.

## What To Read First

1. `config.yaml` (root) — the block roster; then each active block's `config.yaml` for identity, resources, dependency wiring, and runtime values. Live state is in each block's `artifacts/index.yaml`, not `config.yaml`.
2. `.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` — full block system specification

The root `config.yaml` holds orchestration identity only (block roster, roles); all external inputs and outputs are owned by the block configs listed below. Each block has its own `CLAUDE.md` agent contract.

## Input/Output Contract

The root block consumes no external **pipeline** inputs — those are filled into each active block's `runtime_info.input`. The only exception is two optional, tree-wide infrastructure sections in the root config:

**root** (`config.yaml` → `runtime_info.input`) — both default to all-empty, meaning "feature off":
- `cloudflare.{account_id, api_token}`: used by every block's dashboard publishing (`dashboard/run_cloudflare_pages_sync.sh`, `docs/deploy_cloudflare_pages.sh`). Dashboards publish through `scripts/publish_dashboard.sh`: with credentials they go to the Pages project `legoflow-<block>` and the reachable URL is read back from the API (never built from the project name — a taken subdomain gets suffixed); without credentials they fall back to a temporary `*.trycloudflare.com` quick tunnel.
- `docker.{registry, username, password, mirror}`: registry login used by `scripts/docker_login.sh` to lift the anonymous 100-pulls-per-6h-per-IP cap that otherwise breaks image pulls mid-job in curator/tracer/evaluator

Every block reads these through `scripts/shared_credentials.sh`, which resolves each field as **env > root `config.yaml` > that block's legacy env file** (`~/.config/{legoflow-curator,trajgen,harbor_webui}_*_cloudflare.env`, still supported). `config.yaml` is git-tracked, so keep `api_token` / `password` empty there and supply them via `$CLOUDFLARE_API_TOKEN` / `$DOCKER_PASSWORD`. Missing credentials are always a WARN, never a FAIL — nothing in the core pipeline depends on them.

Required external values per block:

**curator** (`blocks/curator/config.yaml` → `runtime_info.input`):
- PR collection tokens are provided through `GITHUB_TOKENS`, `GITHUB_TOKEN`, or an ignored local token file (`gh_token.txt`) — never through `config.yaml`
- `llm_api.api_key`, `llm_api.api_base_url`: OpenAI-compatible LLM endpoint
- `llm_api.pr_model`, `llm_api.task_model`: model names for PR evaluation and task completion

**tracer** (`blocks/tracer/config.yaml` → `runtime_info.input`):
- `llm_api.api_key`, `llm_api.api_base_url`, `llm_api.model`: OpenAI-compatible LLM endpoint and model used by the per-job LiteLLM proxy

**terminal-curator** (`blocks/terminal-curator/config.yaml` → `runtime_info.input`):
- `source.type`: `stackoverflow`, `json`, or `tasks`, with finite `max_questions` / `max_pages` limits and source-specific paths/tags/IDs
- `llm_api.api_base_url`, `llm_api.model`: generation endpoint/model; secret comes from `OPENAI_API_KEY` (not required for `source.type: tasks`)
- optional `SO_API_KEY`: Stack Exchange API access; never stored in a tracked config
- Docker and Tracer's pinned Harbor environment for independent Oracle/NOP validation

**Outputs** (downstream-consumable artifacts):
- `curator.output.merged_tasks_dir`: verified SWE tasks flattened into `blocks/curator/artifacts/merged_swe_tasks/` by `scripts/extract_verified_tasks.py`, which copies only task IDs listed in each language's `verifiable_tasks.txt` and writes a combined `merged_swe_tasks/verifiable_tasks.txt`. This is what tracer consumes.
- `curator.output.swe_tasks_dir`: the per-language source pool at `blocks/curator/artifacts/swe_tasks/{lang}-cc/`, whose `{lang}-cc/verifiable_tasks.txt` lists the task IDs that passed NOP/Oracle validation. Input to the merge step above; no longer handed to a downstream block directly.
- `terminal-curator.output.harbor_tasks_dir`: normalized terminal tasks under `blocks/terminal-curator/artifacts/tasks/`, with `verifiable_tasks.txt` containing only IDs that passed independent fresh-environment Oracle=1 and NOP=0 Harbor trials.
- `terminal-curator.output.eval_registry`: `blocks/terminal-curator/artifacts/tasks/registry.json`, a native Harbor `terminal-curator@1.0` registry listing exactly those accepted IDs with immutable local batch paths. Evaluator consumes it conditionally through `task_source.registry_path` when this dataset name is selected.
- `tracer.output.raw_trajectories_dir`: raw agent trajectories under `blocks/tracer/artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`
- `tracer.output.sft_data_dir`: LLaMA-Factory LF-format SFT JSON converted from those trajectories at `blocks/tracer/artifacts/sft_data/<job>/lf.json` (produced by `blocks/tracer/scripts/convert_trajectories.sh`, which runs the `legoflow_trace_crafter` converters under their own uv env at `blocks/tracer/artifacts/env/legoflow-trace-crafter-uv`)

**Producer→consumer contract**: tracer consumes tasks from the selected
local producer's `verifiable_tasks.txt`. SWE selects
`curator.output.merged_tasks_dir`; terminal curation selects
`terminal-curator.output.harbor_tasks_dir` through
`task_source.local_block: terminal-curator` and `terminal_tasks_dir`. Terminal
sources require a nonempty manifest and fail closed if it is absent. The
legacy generic local-source fallback remains available for sources outside
that terminal contract.

`blocks/tracer/scripts/prepare_tasks.sh` atomically publishes an owned version
of the selected task links under a private cache, with source/manifest/settings/
file-metadata based reuse. `start.sh` pins that version for its own invocation.
Terminal links resolve into immutable producer batches; ordinary mutable
SWE/external pools retain linked-content semantics. Existing unowned real
staging directories are refused, even with --overwrite; select a fresh path or
move old staging after checking active jobs. Task IDs already
processed are recorded by the `/tracer:run` agent in `blocks/tracer/artifacts/processed_tasks.yaml`,
which together with `blocks/tracer/excluded_tasks.txt` is what
`HARBOR_EXCLUDE_TASKS` resolves. Each consumer declares its upstream in its
own `meta_info.dependencies.from`, mirrored by the producer's
`dependencies.to`. Switching task producers does not change Tracer's
trajectory outputs, conversion, or Trainer's input contract.

Evaluator can select the same verified terminal tasks with
`provider: harbor_registry`, `dataset_name: terminal-curator`, `version: "1.0"`,
and `registry_path: ../terminal-curator/artifacts/tasks/registry.json`. Set
`no_hack: false` and `job_analysis.enabled: false`; the SWE-specific remap
and gold-data analysis do not apply to this dataset. Its inference run and
per-task rewards remain a separate operation from curation.

## How To Run

Root skills honor complete private profiles selected by `TRAJGEN_CONFIG`,
`EVAL_CONFIG`, and `TERMINAL_CURATOR_CONFIG` (relative filenames resolve from
that block), using the same profile set for selection, dependencies, resources,
and child dispatch. See the canonical root plugin's
[profile contract](.claude/plugins/root-plugin/resources/EFFECTIVE_PROFILES.md).
For terminal orchestration, use `/root:run` / `$root:run` so the producer skill
performs instruction/tests/reference-solution semantic audit, records corrections,
and repeats fresh Oracle/NOP checks before downstream rollout. The root shell
helper cannot perform that agent step and rejects an implicit terminal chain
before generation. Explicit terminal-only then audited tracer-only operations
remain available; this adds no new user approval gate.

**Mandatory workflow: check → confirm → run.** Agents must never skip the confirmation step.

1. **Check**: Run `/root:check` (or `bash scripts/dryrun.sh` for a single block). This validates config, inputs, paths, GPUs, Docker/K8s connectivity, credentials, and model compatibility — all in one pass, with no side effects.
2. **Confirm**: Present the check results and run configuration summary to the user. **Wait for explicit user confirmation** ("yes", "go ahead", etc.) before proceeding. Never auto-launch — heavy operations (multi-hour GPU training, multi-container rollouts) are expensive and hard to reverse.
3. **Run**: Only after user confirmation, execute `/root:run` (or `bash scripts/start.sh`).

```bash
scripts/dryrun.sh   # validate config, inputs, and required paths (no side effects)
scripts/start.sh    # legacy SWE Python launcher -> tracer; terminal implicit chain is refused
scripts/clean.sh    # remove a run's temporary output in every block (keeps envs,
                    # run records, and anything expensive to regenerate)
scripts/clean.sh --all   # wipe every block's artifacts/ except git-tracked files
                         # (destroys envs, datasets, checkpoints; confirms twice)
```

## Blocks

All blocks run **locally** by default (`meta_info.resources.ip: local`). Override to a remote IP only on explicit user request.

| Block | Execution | Key tool | Status |
|---|---|---|---|
| `blocks/curator/` | Local (CPU + Docker) | `legoflow-curator` CLI + GitHub API | Adaptive per-language task generation |
| `blocks/terminal-curator/` | Local (CPU + Docker) | Pinned Terminal-Lego adapter + Harbor | Bounded terminal task generation/import with Oracle/NOP validation |
| `blocks/tracer/` | Local (CPU + Docker) | Harbor + LiteLLM proxy | Trajectory generation from verified Harbor tasks |
| `blocks/trainer/` | Local (needs 8× GPU) | LLaMA-Factory + DeepSpeed ZeRO-3 | SFT on Qwen3-8B |

Each block has its own `CLAUDE.md` with its full agent contract.

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

Live state (what's running, what just finished) lives in the newest entry of each block's `artifacts/index.yaml`, written automatically by `scripts/archive_run.sh` (invoked from `start.sh`'s EXIT trap). `config.yaml` is one-shot per run and is not edited during execution.
