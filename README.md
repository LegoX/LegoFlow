# SWE-Lego-Live

A self-evolving LLM development pipeline. It generates coding-agent training data from real GitHub PRs, runs agent trajectories, and feeds the results into SFT training and benchmark evaluation — all coordinated by an AI agent that monitors progress and tunes parameters automatically.



## Block Overview

This entire project is built on a **block** abstraction. The pipeline consists of four blocks, run in order. Each has its own `CLAUDE.md` (agent contract), `config.yaml` (inputs, outputs — one-shot per run, no live state), and `scripts/`.

| Block | Role | Primary output |
|-------|------|----------------|
| `subblock/curator/` | Converts GitHub PRs → verified SWE tasks | `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` |
| `subblock/tracer/` | Runs an agent on SWE tasks → raw trajectories | `artifacts/jobs/<job>/` (Harbor job dirs) |
| `subblock/trainer/` | Converts trajectories → sharegpt data, trains with LLaMA-Factory | `artifacts/model/<run>/` (checkpoints) |


### What is a Block?

Each unit of work — `curator`, `tracer`, `trainer`, `evaluator` — is a self-contained directory with the same fixed structure:

- `config.yaml` declares the block's inputs, outputs, children, dependencies between children, and (optionally) a remote node it must run on. It is **one-shot per run** — every key is configuration; no live state is stored here.
- `scripts/start.sh`, `dryrun.sh`, `clean.sh`, `archive_run.sh` are how it actually executes. `start.sh` installs an EXIT trap that fires `archive_run.sh` on completion (success, failure, or signal), producing a `artifacts/archives/run_NNN/` snapshot and appending one entry to `artifacts/index.yaml`.
- `artifacts/index.yaml` is the live state: the newest entry's `status` field (`completed | failed | interrupted`) tells you what the block last did.
- Blocks can nest — a parent lists its children under `meta_info.subblocks` (roles only), and each child's own `meta_info.dependencies` shows both directions: `from` (the outputs it consumes, wired into its own inputs) and `to` (which of its own outputs feed which sibling, the mirror declared by the producer). The full specification is in [`BLOCK_DEFINITION.md`](BLOCK_DEFINITION.md).



### Pipeline Architecture

```
GitHub PRs
    │
    ▼
┌────────┐  SWE tasks  ┌─────────┐  trajectories  ┌─────────┐  ckpt  ┌───────────┐
│ curator │ ──────────► │ tracer  │ ─────────────► │ trainer │ ─────► │ evaluator │
└────────┘             └─────────┘                └─────────┘        └───────────┘
```


## Project Layout

```
SWE-Lego-Live/
├── CLAUDE.md                  # root block agent contract
├── BLOCK_DEFINITION.md        # block system specification
├── scripts/
│   ├── dryrun.sh              # validate root block
│   ├── start.sh               # launch data blocks (curator + tracer); installs EXIT-trap → archive_run.sh
│   ├── archive_run.sh         # snapshot config + scripts + repo SHAs → artifacts/archives/run_NNN/
│   └── clean.sh               # remove a run's temporary output; --purge wipes artifacts/
├── dashboard/
│   └── overview.mdx           # human-readable current state
├── artifacts/
│   ├── index.yaml             # append-only run index (newest entry = live state)
│   └── archives/run_NNN/      # per-run snapshots (metadata.yaml + config.yaml + scripts/)
└── subblock/
    ├── curator/                # SWE task generation block (same scripts/ + artifacts/ layout)
    ├── tracer/               # trajectory generation block
    ├── trainer/                   # SFT training block
```


## Quick Start

Run the pipeline block by block in order: **curator → tracer → trainer**, then evaluate with **evaluator**. Each step produces artifacts the next block depends on. You can also run blocks individually once their inputs and upstream dependencies are satisfied.

### Prerequisites

- **All blocks**: Claude Code with this repo's block plugin loaded (`/reload-plugins` shows `1 plugin · 3 skills`); `git submodule update --init --recursive` after clone
- **curator**: GitHub token(s) with `repo` read scope; OpenAI-compatible LLM API; Docker on the run host
- **tracer**: OpenAI-compatible LLM API; Docker; verified tasks from swegen (wired via `meta_info.dependencies`)
- **trainer**: Multi-GPU node (typically 8× GPU); conda env and model paths per `subblock/trainer/CLAUDE.md`; trajectory source (from tracer or an existing job dir)

Root `scripts/start.sh` only automates the **data** stage (curator + tracer on the configured remote node). **trainer** and **evaluator** are started from their own directories via `/root:run` or `scripts/start.sh`.

### The `Block` Plugin

You don't operate blocks by hand. A Claude Code plugin at `.claude/plugins/root-plugin/` helps you set up and run the whole tree:

- **`/root:create`** — scaffold a new block with the correct structure (config.yaml, scripts, dashboard, artifacts index, optional submodules).
- **`/root:check`** — recursively sanity-check every block under the current directory: schema, filled inputs, dependency wiring, remote-resource reachability, and live availability of any LLM endpoint declared in `runtime_info.input`. Read-only.
- **`/root:run`** — preflight every input and dependency for the target block, then execute its `scripts/start.sh` (locally, or in a tmux session over SSH if it's a remote-resource block) and archive the result under `artifacts/archives/run_NNN/`.

Both `/root:check` and `/root:run` take a free-form natural-language argument. The agent reads the whole string and infers the target block (by literal name or unambiguous paraphrase, using each block's `CLAUDE.md` for context); if no block is mentioned it targets the root, and ambiguity (multiple blocks named) triggers a clarification question rather than a guess.

```text
/root:run                                       # root orchestrator
/root:run curator                                # subblock/curator
/root:run curator only 32 verified tasks         # curator + propose config/flag change, confirm, run
/root:run run curator with 32 verified tasks     # same — block name embedded in sentence
/root:run run the trajectory generator          # tracer — resolved by paraphrase
/root:run start the data pipeline               # root — no block mentioned
/root:run run curator and tracer                # ambiguous — agent asks which one
```

For `/root:run`, if the instruction implies a config edit or flag injection, the agent proposes the concrete change (file path, old → new value) and confirms before applying. For `/root:check`, the instruction only shapes the report's focus — every check still runs, and no files are ever modified.


### 1. Clone

```bash
git clone --recurse-submodules <repo-url> SWE-Lego-Live
cd SWE-Lego-Live
```

If you already cloned without `--recurse-submodules`, run `git submodule update --init --recursive`.

### 2. Discover what needs to be filled

Open Claude Code in the repo root, then ask:

```text
/root:check              # check root + every subblock
/root:check curator       # only check the curator subblock
```

On a fresh clone, the report tells you exactly which `runtime_info.input` keys are unfilled, which submodules are missing, whether the remote node is reachable, and whether the configured LLM endpoint exposes the requested model. `/root:check` uses `GET /models`; the separate `/curator:check` adds a small real completion request before generation. You don't need to read each `config.yaml` cold; let the skill point at the gaps.

Pass a subblock name (e.g. `/root:check tracer`) when you're iterating on one block and don't want noise from the others.

### 3. Fill the gaps

Edit each `config.yaml` flagged in step 2, setting only keys under `runtime_info.input` (replace every `human` marker; `""` fields are supplied via env/file channels). Upstream block outputs are wired via each block's `meta_info.dependencies` and you do **not** copy paths by hand.

| Block | What to fill (see that block's `CLAUDE.md` for the full list) |
|-------|------------------------------------------------------------------|
| **curator** | Provide PR-collection tokens via `GITHUB_TOKENS`, `GITHUB_TOKEN`, or an ignored token file (never in config.yaml); fill `llm_api` (api_key, api_base_url, pr_model, task_model) |
| **tracer** | `llm_api` (api_key, api_base_url, model); task source comes from swegen dependency |
| **trainer** | `source` (provider, scaffold, job_dir / trajs_dir); `conversion`; `model`; `training`; `infrastructure`; `credentials` (WandB if online) |

Re-run `/root:check` until it prints `All blocks healthy — safe to /root:run.`

### 4. Run the pipeline

Invoke `/root:run <block_name>` from the repo root, or `cd` into the subblock and invoke `/root:run` with no args. Both forms directly execute the selected block's `scripts/start.sh`. Preflight matches `/root:check`; execution runs locally or over SSH + tmux when `meta_info.resources.ip` is set. Each run archives under `artifacts/archives/run_NNN/` automatically — `start.sh`'s EXIT trap fires `scripts/archive_run.sh` regardless of how the run exits (success, error, SIGINT, SIGTERM).

**1. curator** — generate and validate SWE tasks:

```text
/root:run curator                         # all-language scripts/start.sh
# for Curator smoke, single-language, or full mode selection:
cd subblock/curator && /curator:create-tasks
```

**2. tracer** — run the agent on verified tasks (after curator has entries in `verifiable_tasks.txt`):

```text
/root:run tracer
```

**3. trainer** — convert trajectories and fine-tune (after tracer job dirs exist; `trajectories_dir` dependency points at tracer output):

```text
/root:run trainer
```

**4. evaluator** — benchmark the trained checkpoint (after trainer writes `runtime_info.output.checkpoint_path`):

```text
/root:run evaluator
```

You can also run the **root orchestrator** to launch the data stage (curator + tracer on the configured remote node) as one step:

```text
/root:run              # equivalent to: bash scripts/start.sh
```

`bash scripts/start.sh` directly also works and accepts `--curator-only` / `--tracer-only` for selective launching.

### 5. Monitor

The block's own artifacts are the source of truth — no need to attach to remote tmux unless you want to.

- `artifacts/index.yaml` — timeline. Newest entry's `status` (`completed | failed | interrupted`) tells you what the block last did.
- `artifacts/archives/run_NNN/metadata.yaml` — detail page for one run: id, block, timestamps, exit code, repo commit SHAs.
- `artifacts/archives/run_NNN/config.yaml` — frozen snapshot of the config that produced this run.
- `artifacts/archives/run_NNN/scripts/` — frozen copy of the scripts as they were at run time.
- `artifacts/archives/run_NNN/session.log`, `monitor.md` — optional, agent-added narratives.

#### Archive format reference

Both files follow a fixed schema. `archive_run.sh` writes them on every run; the contract is documented in `.claude/plugins/root-plugin/references/BLOCK_DEFINITION.md` (§ "Artifacts — Archiving Each Run"), with a worked example at `.claude/plugins/root-plugin/references/example_block/artifacts/`.

**`artifacts/index.yaml`** — one entry appended per run:

```yaml
runs:
  - id: run_001
    started_at: "2026-05-04T08:00:00Z"
    completed_at: "2026-05-04T10:11:35Z"
    status: completed          # completed | failed | interrupted
    archive: artifacts/archives/run_001/
    notes: ""                  # agent may refine after the run
```

**`artifacts/archives/run_NNN/metadata.yaml`** — required fields written automatically:

```yaml
id: run_001
block: <block_name>
started_at: "2026-05-04T08:00:00Z"
completed_at: "2026-05-04T10:11:35Z"
status: completed              # completed | failed | interrupted
exit_code: 0
repos:
  <name>: <40-char git sha>    # one per dir under repos/; {} if none
notes: ""
# Optional, agent-added: stage, results, inputs
```

The `status` vocabulary is identical in both files and is derived from `start.sh`'s exit code: `0 → completed`, `130/143 → interrupted` (SIGINT/SIGTERM), anything else → `failed`. Field order is fixed (`sort_keys=False` in the YAML writer), so diffs across runs stay readable.

## Adding a New Block

Use `/root:create` to scaffold a new block — it produces the full directory tree (`config.yaml`, `CLAUDE.md`, `dashboard/`, `scripts/{start,dryrun,clean,archive_run}.sh`, `artifacts/index.yaml`, `memory/notes.md`, `subblock/`) wired up to the [`BLOCK_DEFINITION.md`](BLOCK_DEFINITION.md) contract. `archive_run.sh` is copied unmodified from the plugin's canonical template, and the scaffolded `start.sh` includes the EXIT-trap snippet that invokes it — so a newly created block archives every run automatically without any extra wiring.

Two ways to drive it:

- **Intake form** — copy `.claude/plugins/root-plugin/references/BLOCK_INTAKE.md` into your project, fill it in, and paste it back. The agent scaffolds everything from your answers.
- **Interactive** — describe the block (name, role, parent, inputs/outputs, optional remote node and repos); the agent asks any follow-ups and scaffolds in one pass.

A complete reference scaffold lives at `.claude/plugins/root-plugin/references/example_block/`. After creation, fill in the new block's `runtime_info.input`, then validate the whole tree with `/root:check` before running.
