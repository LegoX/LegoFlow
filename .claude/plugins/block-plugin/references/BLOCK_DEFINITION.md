# What Is A Block

A `block` is the basic collaboration unit in a block-structured project. It defines what a unit is responsible for, what it depends on, what it produces, and how it runs. Every block follows the same layout so humans and agents can navigate any block without prior knowledge.

The repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is also a block.

## Agent Team Model

Each block is operated by a dedicated agent. The agent reads `CLAUDE.md` for its contract and `config.yaml` for inputs, outputs, resources, and tree position. Agents coordinate through two mechanisms:

- **Inter-block wiring** — declared in `meta_info.subblocks[].dependencies`: one block's `runtime_info.output` key is referenced by name in a child's dependency map. This is the primary coordination channel.
- **External inputs** — declared in `runtime_info.input`: values that come from outside the block tree (API keys, human decisions, external dataset paths). These are filled manually before a run.

Each agent maintains its block's `status` and `memory/` independently.

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
│   └── clean.sh
├── artifacts/
│   ├── index.yaml
│   └── archives/
│       └── run_NNN/
│           ├── metadata.yaml
│           ├── config.yaml
│           ├── scripts/
│           ├── repo/
│           ├── session.log
│           └── monitor.md
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

status:
  phase:                # idle | running | done | blocked
  progress:
  next_steps:
  blockers:
  last_updated:

evolving:
  description:
  tunable_params: {}
```

## File Roles

| File | Updated by | Purpose |
|---|---|---|
| `CLAUDE.md` | Agent or human | Agent contract: role, I/O, run rules, archiving rules |
| `config.yaml` | Agent or human | Identity, resources, runtime I/O, live status, tunable params |
| `dashboard/overview.mdx` | Agent | Human-readable current state: done, in progress, next |
| `artifacts/index.yaml` | Agent | Append-only run index |
| `memory/notes.md` | Agent | Long-form observations, decisions, postmortems |

## Artifacts — Archiving Each Run

Every run must be fully archived. After each run, the agent:

1. Creates `artifacts/archives/run_NNN/` with these six items:

   | File | Content |
   |---|---|
   | `metadata.yaml` | Run id, timestamps, phase/stage, results summary, repo commit ids |
   | `config.yaml` | Snapshot of `config.yaml` as it was at run time |
   | `scripts/` | Copy of all scripts executed during this run |
   | `repo/` | Snapshot or reference of the repo code at the pinned commit |
   | `session.log` | Claude Code session record (tool calls, agent reasoning, decisions) |
   | `monitor.md` | Human-readable monitor output produced by the agent during the run |

2. Appends one entry to `artifacts/index.yaml`:

```yaml
- id: run_001
  started_at: "2026-05-03T08:00:00Z"
  completed_at: "2026-05-03T10:11:35Z"
  status: completed        # running | completed | failed
  archive: artifacts/archives/run_001/
  notes: "one-line summary of what this run tested"
```

### `metadata.yaml` schema

```yaml
id: run_001
started_at: "2026-05-03T08:00:00Z"
completed_at: "2026-05-03T10:11:35Z"
status: completed
stage: <pipeline stage name>
results:
  <metric_key>: <value>   # key results, e.g. verified_tasks: 16
repos:
  <repo_name>: <commit_id>
inputs:                   # copy of runtime_info.input used for this run
  <key>: <value>
notes: ""
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
3. The agent archives the full run to `artifacts/archives/run_NNN/` and appends to `artifacts/index.yaml`.
