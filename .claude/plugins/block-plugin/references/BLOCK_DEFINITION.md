# What Is A Block

A `block` is the basic collaboration unit in a block-structured project. It defines what a unit is responsible for, what it depends on, what it produces, and how it runs. Every block follows the same layout so humans and agents can navigate any block without prior knowledge.

The repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is also a block.

## Agent Team Model

Each block is operated by a dedicated agent. The agent reads `CLAUDE.md` for its contract and `config.yaml` for inputs, outputs, resources, and tree position. Agents coordinate through two mechanisms:

- **Inter-block wiring** — declared in `meta_info.subblocks[].dependencies`: one block's `runtime_info.output` key is referenced by name in a child's dependency map. This is the primary coordination channel.
- **External inputs** — declared in `runtime_info.input`: values that come from outside the block tree (API keys, human decisions, external dataset paths). These are filled manually before a run.

Each agent maintains its block's `memory/` and `artifacts/` independently. Live run state lives in `artifacts/index.yaml` (most recent entry's `status` field — written automatically by `archive_run.sh`), not in `config.yaml`.

## Directory Layout

```
<block_dir>/
├── CLAUDE.md
├── config.yaml
├── dashboard/
│   └── overview.mdx
├── repos/              ← only if repos are declared (git submodules)
├── memory/
│   └── notes.md
├── scripts/
│   ├── start.sh
│   ├── dryrun.sh
│   ├── clean.sh
│   └── archive_run.sh
├── artifacts/
│   ├── index.yaml
│   └── archives/
│       └── run_NNN/
│           ├── metadata.yaml      ← required (written by archive_run.sh)
│           ├── config.yaml        ← required (snapshot)
│           ├── scripts/           ← required (snapshot)
│           ├── session.log        ← optional (agent adds manually)
│           └── monitor.md         ← optional (agent adds manually)
└── subblock/
```

## `config.yaml` — the single source of truth

```yaml
meta_info:
  name:
  label:
  description:
  parent:
  subblocks:
    <child_name>:
      role:
      dependencies:
        # <input_key>: <source_block>.output.<key>  or  human
  repos: {}             # name → {commit_id, role}
  environment:
    requirements:
  resources:
    ip:                 # remote node; null = run locally
    pwd:
    apikey:
    model:
    public_storage:

runtime_info:
  # Only external inputs (API keys, human decisions, external paths).
  # Inter-block values are wired through subblocks[].dependencies — not here.
  input: {}
  output: {}            # values produced for other blocks or downstream consumers

evolving:
  description:
  tunable_params: {}
```

`config.yaml` is **one-shot per run**: every key is configuration the block reads at launch time. Live state (whether a run is in flight, how it ended, what it produced) lives in `artifacts/index.yaml` and the per-run `artifacts/archives/run_NNN/` snapshots — not in `config.yaml`.

## File Roles

| File | Updated by | Purpose |
|---|---|---|
| `CLAUDE.md` | Agent or human | Agent contract: role, I/O, run rules, archiving rules |
| `config.yaml` | Agent or human | Identity, resources, runtime I/O, tunable params. One-shot per run — no live state. |
| `dashboard/overview.mdx` | Agent | Human-readable current state: done, in progress, next |
| `artifacts/index.yaml` | Agent | Append-only run index |
| `memory/notes.md` | Agent | Long-form observations, decisions, postmortems |

## Artifacts — Archiving Each Run

Archiving is **automated** by `scripts/archive_run.sh`, which every block keeps next to `start.sh`. Each block's `start.sh` installs an EXIT trap that invokes its sibling `archive_run.sh` — so an archive entry is created whether the run exits cleanly, fails (`set -e`), or is interrupted (SIGINT / SIGTERM). The script picks the next `run_NNN` id by scanning both `artifacts/archives/run_*/` and existing `id: run_NNN` entries in `artifacts/index.yaml`, so manual narrative entries and automated entries share one id space.

Each run produces `artifacts/archives/run_NNN/` containing:

| File | Content | Produced by |
|---|---|---|
| `metadata.yaml` | Run id, block name, timestamps, status, exit code, repo commit SHAs | `archive_run.sh` |
| `config.yaml` | Snapshot of `config.yaml` as it was at run time | `archive_run.sh` |
| `scripts/` | Copy of all scripts (top-level files + non-hidden subdirs; hidden state dirs like `.swegen-py` are skipped) | `archive_run.sh` |
| `session.log` *(optional)* | Claude Code session record (tool calls, agent reasoning, decisions) | Agent, manually after the run |
| `monitor.md` *(optional)* | Human-readable monitor output produced by the agent during the run | Agent, manually after the run |

Note: the spec previously required a full `repo/` snapshot — that's been replaced by the `repos:` field in `metadata.yaml`, which records each `repos/<name>/`'s `git rev-parse HEAD`. The SHA carries the same information as a tree copy provided the commit is published.

`archive_run.sh` also appends one entry to `artifacts/index.yaml` (using PyYAML for a clean round-trip; falls back to a plain text append if PyYAML is unavailable):

```yaml
- id: run_001
  started_at: "2026-05-03T08:00:00Z"
  completed_at: "2026-05-03T10:11:35Z"
  status: completed        # completed | failed | interrupted
  archive: artifacts/archives/run_001/
  notes: ""                # one-line summary; agent may edit after the run
```

The `status` field is derived from the script's exit code: `0` → `completed`, `130`/`143` (SIGINT / SIGTERM) → `interrupted`, anything else → `failed`.

Manual invocation is supported for ad-hoc archives or to backfill:

```bash
bash scripts/archive_run.sh [exit_code] [started_at_iso8601] [notes]
```

### `metadata.yaml` schema

```yaml
id: run_001
block: <block_name>              # name of the block this archive belongs to
started_at: "2026-05-03T08:00:00Z"
completed_at: "2026-05-03T10:11:35Z"
status: completed                # completed | failed | interrupted
exit_code: 0                     # raw exit code of start.sh (0, 130, 143, etc.)
repos:
  <repo_name>: <commit_sha>      # one entry per directory under repos/
notes: ""

# Optional fields the agent may add manually after the run:
# stage: <pipeline stage name>
# results:
#   <metric_key>: <value>          # e.g. verified_tasks: 16
# inputs:                          # copy of runtime_info.input used for this run
#   <key>: <value>
```

## Inter-Block Dependencies

Subblock dependencies are declared in `meta_info.subblocks[].dependencies`. This is the authoritative wiring between blocks — not `runtime_info`. Example:

```yaml
subblocks:
  trajgen:
    role: Generate trajectories from verified SWE instances
    dependencies:
      verified_tasks_dir: swegen.output.verified_tasks_dir  # from sibling block
      api_key: human                                         # filled manually
```

`runtime_info.input` is only for values that originate outside the block tree entirely.

## Remote Execution Rule

If `meta_info.resources.ip` is set, the agent **must** execute remotely:
1. Create a tmux window immediately named after the block.
2. SSH into the remote node and attach to (or create) a tmux session there.
3. Run scripts inside that remote session — never run a remote-resource block locally.
4. Double check with the users if the code needs to be synced to that CPU node, or the code repo is exactly on the same directory (after path mapping).

## Creating a Block

**Option A — Intake form:** copy `BLOCK_INTAKE.md` from the `create-block` skill's `assets/` folder, fill it in, and hand it to the agent.

**Option B — Chat:** describe the block in plain language; the agent asks follow-up questions and scaffolds immediately.

## Running a Block

1. Fill `runtime_info.input` in `config.yaml` with external values.
2. Run `scripts/start.sh` to execute, or `scripts/dryrun.sh` to validate without side effects.
3. `start.sh`'s EXIT trap invokes `scripts/archive_run.sh`, which archives the run to `artifacts/archives/run_NNN/` and appends one entry to `artifacts/index.yaml` — automatically, on success, failure, or interrupt. The agent may add `session.log` / `monitor.md` to the archive afterwards.
