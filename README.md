# SWE-Lego-Live

A self-evolving LLM development pipeline. It generates coding-agent training data from real GitHub PRs, runs agent trajectories, and feeds the results into SFT and RL training — all coordinated by an AI agent that monitors progress and tunes parameters automatically.



## Block Overview

This entire project is built on a **block** abstraction. The pipeline consists of four blocks, run in order. Each has its own `CLAUDE.md` (agent contract), `config.yaml` (inputs, outputs, status), and `scripts/`.

| Block | Role | Primary output |
|-------|------|----------------|
| `subblock/swegen/` | Converts GitHub PRs → verified SWE tasks | `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` |
| `subblock/trajgen/` | Runs an agent on SWE tasks → raw trajectories | `artifacts/jobs/<job>/` (Harbor job dirs) |
| `subblock/sft/` | Converts trajectories → sharegpt data, trains with LLaMA-Factory | `artifacts/model/<run>/` (checkpoints) |
| `subblock/rl/` | Online RL (GRPO/GSPO) on SWE-bench via Harbor + vLLM + verl | `repos/harbor-verl-train/outputs/` (actor checkpoints) |


### What is a Block?

Each unit of work — `swegen`, `trajgen`, `sft`, `rl` — is a self-contained directory with the same fixed structure:

- `config.yaml` declares the block's inputs, outputs, children, dependencies between children, and (optionally) a remote node it must run on
- `scripts/start.sh`, `scripts/dryrun.sh`, `scripts/clean.sh` are how it actually executes
- `artifacts/` holds the results of each run, archived by run id
- `status` (inside `config.yaml`) is the live phase: `idle | running | done | blocked`
- Blocks can nest — a parent declares its children under `meta_info.subblocks`, and outputs of one child are wired into another child's inputs via `meta_info.subblocks[].dependencies`. The full specification is in [`BLOCK_DEFINITION.md`](BLOCK_DEFINITION.md).



### Pipeline Architecture

```
GitHub PRs
    │
    ▼
┌────────┐  SWE tasks  ┌─────────┐  trajectories  ┌─────┐  ┌────┐
│ swegen │ ──────────► │ trajgen │ ─────────────► │ sft │─►│ rl │
└────────┘             └─────────┘                └─────┘  └────┘
```


## Project Layout

```
SWE-Lego-Live/
├── CLAUDE.md                  # root block agent contract
├── BLOCK_DEFINITION.md        # block system specification
├── scripts/
│   ├── dryrun.sh              # validate root block
│   ├── start.sh               # launch data blocks (swegen + trajgen) on remote node
│   └── clean.sh               # remove temp files
├── dashboard/
│   └── overview.mdx           # human-readable current state
├── artifacts/
│   ├── index.yaml             # append-only run index
│   └── archives/              # per-run snapshots
└── subblock/
    ├── swegen/                # SWE task generation block
    ├── trajgen/               # trajectory generation block
    ├── sft/                   # SFT training block
    └── rl/                    # RL training block
```


## Quick Start

Run the pipeline block by block in order: **swegen → trajgen → sft → rl**. Each step produces artifacts the next block depends on. You can also run blocks individually once their inputs and upstream dependencies are satisfied.

### Prerequisites

- **All blocks**: Claude Code with this repo's block plugin loaded (`/reload-plugins` shows `1 plugin · 3 skills`); `git submodule update --init --recursive` after clone
- **swegen**: GitHub token(s) with `repo` read scope; OpenAI-compatible LLM API; Docker on the run host
- **trajgen**: OpenAI-compatible LLM API; Docker; verified tasks from swegen (wired via `meta_info.dependencies`)
- **sft**: Multi-GPU node (typically 8× GPU); conda env and model paths per `subblock/sft/CLAUDE.md`; trajectory source (from trajgen or an existing job dir)
- **rl**: Multi-GPU node; Kubernetes access for Harbor task execution; vLLM + Ray; paths to SWE-bench parquet/task data per `subblock/rl/CLAUDE.md`; optional WandB key

Root `scripts/start.sh` only automates the **data** stage (swegen + trajgen on the configured remote node). **sft** and **rl** are started from their own directories via `/block:run` or `scripts/start.sh`.

### The `Block` Plugin

You don't operate blocks by hand. A Claude Code plugin at `.claude/plugins/block-plugin/` helps you set up and run the whole tree:

- **`/block:create`** — scaffold a new block with the correct structure (config.yaml, scripts, dashboard, artifacts index, optional submodules).
- **`/block:check`** — recursively sanity-check every block under the current directory: schema, filled inputs, dependency wiring, remote-resource reachability, and live availability of any LLM endpoint declared in `runtime_info.input`. Read-only.
- **`/block:run`** — preflight every input and dependency for the block in your current directory, then execute its `scripts/start.sh` (locally, or in a tmux session over SSH if it's a remote-resource block) and archive the result under `artifacts/archives/run_NNN/`.


### 1. Clone

```bash
git clone --recurse-submodules <repo-url> SWE-Lego-Live
cd SWE-Lego-Live
```

If you already cloned without `--recurse-submodules`, run `git submodule update --init --recursive`.

### 2. Discover what needs to be filled

Open Claude Code in the repo root, then ask:

```text
/block:check
```

On a fresh clone, the report tells you exactly which `runtime_info.input` keys are unfilled, which submodules are missing, whether the remote node is reachable, and whether your LLM endpoint answers a `GET /models` probe (no chat-completion calls — `/block:check` never costs anything to run). You don't need to read each `config.yaml` cold; let the skill point at the gaps.

### 3. Fill the gaps

Edit each `config.yaml` flagged in step 2, setting only keys under `runtime_info.input`. These are external values the block cannot derive — upstream block outputs are wired via `meta_info.dependencies` (or `meta_info.subblocks[].dependencies` on parent blocks) and you do **not** copy paths by hand.

| Block | What to fill (see that block's `CLAUDE.md` for the full list) |
|-------|------------------------------------------------------------------|
| **swegen** | `github_tokens`; `llm_api` (api_key, api_base_url, pr_model, task_model) |
| **trajgen** | `llm_api` (api_key, api_base_url, model); task source comes from swegen dependency |
| **sft** | `source` (provider, scaffold, job_dir / trajs_dir); `conversion`; `model`; `training`; `infrastructure`; `credentials` (WandB if online) |
| **rl** | `model`; `infrastructure` (nodes, GPUs, K8s); `training`; `data` (parquet + Harbor task dirs); `experiment`; `credentials` |

Re-run `/block:check` until it prints `All blocks healthy — safe to /block:run.`

### 4. Run the pipeline

From inside each subblock directory, invoke `/block:run` (or `bash scripts/start.sh`). Preflight matches `/block:check`; execution runs locally or over SSH + tmux when `meta_info.resources.ip` is set. Each run archives under `artifacts/archives/run_NNN/` (metadata, config snapshot, `session.log`, `monitor.md`).

**1. swegen** — generate and validate SWE tasks:

```text
cd subblock/swegen
/block:run
```

**2. trajgen** — run the agent on verified tasks (after swegen has entries in `verifiable_tasks.txt`):

```text
cd subblock/trajgen
/block:run
```

**3. sft** — convert trajectories and fine-tune (after trajgen job dirs exist; `trajectories_dir` dependency points at trajgen output):

```text
cd subblock/sft
/block:run
```

**4. rl** — online RL from the SFT checkpoint (after sft writes `runtime_info.output.checkpoint_path`):

```text
cd subblock/rl
/block:run
```

Alternatively, from the repo root, `bash scripts/start.sh` syncs to the remote node and starts swegen and trajgen together (`--swegen-only` / `--trajgen-only` to run one data block).

### 5. Monitor

The block's own state is the source of truth — no need to attach to remote tmux unless you want to.

- `config.yaml` → `status.phase`: `idle | running | done | blocked` (kept current by `/block:run`)
- `artifacts/index.yaml`: append-only run log with start/end timestamps and archive paths
- `artifacts/archives/run_NNN/session.log`: full captured execution log for that run
- `artifacts/archives/run_NNN/metadata.yaml`: run id, timestamps, exit code, resolved repo commits, copy of inputs at run time
- `artifacts/archives/run_NNN/monitor.md`: short human narrative of what happened

## Adding a New Block

Use `/block:create` to scaffold a new block — it produces the full directory tree (`config.yaml`, `CLAUDE.md`, `dashboard/`, `scripts/{start,dryrun,clean}.sh`, `artifacts/index.yaml`, `memory/notes.md`, `subblock/`) wired up to the [`BLOCK_DEFINITION.md`](BLOCK_DEFINITION.md) contract.

Two ways to drive it:

- **Intake form** — copy `.claude/plugins/block-plugin/references/BLOCK_INTAKE.md` into your project, fill it in, and paste it back. The agent scaffolds everything from your answers.
- **Interactive** — describe the block (name, role, parent, inputs/outputs, optional remote node and repos); the agent asks any follow-ups and scaffolds in one pass.

A complete reference scaffold lives at `.claude/plugins/block-plugin/references/example_block/`. After creation, fill in the new block's `runtime_info.input`, then validate the whole tree with `/block:check` before running.
