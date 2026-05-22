---
name: create
description: >
  Scaffolds a new block directory in a block-structured project. A `block` is the basic collaboration unit in a block-structured project, which contains all necessary information about its role, repos, scripts and artifacts. Use this skill whenever the user wants to create a new block, add a subblock, set up a new project unit, or initialize a block directory. Triggers on phrases like "create a block", "add a subblock", "scaffold a new block", "initialize a block for X", "make a block called Y", or any request to set up a new unit in a block-structured repo. Don't wait for the user to say "block" explicitly — if they describe wanting to create a new project unit, module, or agent workspace with inputs/outputs/scripts, use this skill.
---

# /block:create

Scaffolds a complete block directory — the standard collaboration unit in a block-structured project. For the full block definition, refer to `references/BLOCK_DEFINITION.md` bundled in this plugin (sibling of this `skills/` folder).

## What you produce

```
<block_name>/
├── CLAUDE.md
├── config.yaml
├── dashboard/
│   └── overview.mdx
├── repos/              ← only if repos are declared; git submodules live here
├── memory/
│   └── notes.md
├── scripts/
│   ├── start.sh
│   ├── dryrun.sh
│   └── clean.sh
├── artifacts/
│   └── index.yaml          ← archives/ created at first run
└── subblock/
```

A complete example is in `references/example_block/` bundled in this plugin — an `sft_training` leaf block with inputs, outputs, a remote resource, and a git submodule repo. Read it when you need a concrete reference.

## How to get started (tell the user this first)

When a user asks to create a block, explain the two ways they can provide the information:

**Option A — Fill in the intake form** (recommended for new users or complex blocks)

> Copy `references/BLOCK_INTAKE.md` from this plugin into your project, fill it in, and paste it back. The agent will scaffold everything from it.

**Option B — Chat interactively**

> Just describe your block — what it does, what it needs, what it produces, where to put it. The agent will ask follow-up questions and scaffold immediately.

If the user already provided enough information (name, role, location), skip this explanation and go straight to scaffolding.

## Interview

If going the chat route, collect the following in a single conversational message. Skip anything the user already answered.

**Required:**
1. **Name** — short snake_case identifier (e.g. `data_curation`, `sft_training`)
2. **Role** — one sentence: what does this block do?
3. **Parent** — name of the parent block, or `null` if this is the root
4. **Children** — names of direct child blocks, or `[]` if leaf
5. **Where to create it** — directory path where the block folder should be created

**Optional (ask, but accept null):**
6. **Inputs** — runtime values needed before running (API keys, dataset paths, hyperparameters). For each: name and description.
7. **Outputs** — values produced after a run. For each: name and description.
8. **Resources** — remote node IP, credentials, storage path (null = local execution)
9. **Repos** — code repos attached to this block (name, URL or local path, commit ID, role)
10. **Evolving** — what parameters can be tuned between runs, and what results to observe

If the user says "just scaffold it" or seems in a hurry, fill in sensible placeholders and proceed.

## Scaffolding

Once you have the answers (from the intake form or the interview), create all files in one pass. Do not ask for confirmation — just write.

### `config.yaml`

```yaml
meta_info:
  name: <name>
  label: <human-readable label, title-case of name>
  description: <role sentence>
  parent: <parent or null>
  subblocks:
    <child1>:
      role:
      dependencies: []
    # repeat for each child; omit section if no children

  repos:
    # omit section if no repos
    <repo_name>:
      name:
      commit_id:
      role:

  environment:
    venv_name: # the virtual env to be used
    venv_path: # the path to this virtual env
    requirements: # from where the virtual env is built
    description:

  resources:
    ip: <ip or null>
    pwd: <pwd or null>
    directory: <null> # by default it's the current directory if this is not on another server
    description: the server where you should by default work at ${directory}
    public_storage:

runtime_info:
  input:
    <input_name>: null   # <description>
    # one entry per declared input; omit section if none

  output:
    <output_name>: null  # <description>
    # one entry per declared output; omit section if none

status:
  phase: idle
  progress: null
  next_steps: Fill runtime_info.input in config.yaml, then run scripts/dryrun.sh.
  blockers: null
  last_updated: <today's date ISO format>

evolving:
  description: <evolving description or null>
  tunable_params:
    # <param>: null
```

### `CLAUDE.md`

Concise agent contract, under 60 lines. Include:
- Block name and one-sentence role
- Parent block and children (if any)
- What to read first (`config.yaml`, then `dashboard/overview.mdx`)
- Input/output contract: what values to read from `runtime_info.input`, what to write to `runtime_info.output`
- Repos: list any repos under `repos/` and their purpose; note each is a git submodule pinned to a specific commit
- How to run: `scripts/start.sh` to execute, `scripts/dryrun.sh` to validate. Mention that `/block:run` (from the `block` plugin) preflights and executes this contract.
- Artifact archiving rule: after each run, create `artifacts/archives/run_NNN/` with: `metadata.yaml` (id, timestamps, stage, results, repo commits, copy of inputs), `config.yaml` snapshot, `scripts/` copy, `repo/` snapshot, `session.log` (Claude Code session record), `monitor.md` (agent monitor output); append one entry to `artifacts/index.yaml` with `archive: artifacts/archives/run_NNN/`
- Inter-block wiring: values from other blocks are declared in `meta_info.subblocks[].dependencies` — do not duplicate them in `runtime_info.input`. Only external values (API keys, human decisions) go in `runtime_info.input`.
- Status update rule: keep `status` in `config.yaml` current throughout execution
- Remote execution rule: if `meta_info.resources.ip` is set, open a local tmux window, SSH into the remote node, attach to a tmux session there, and run scripts inside it — never run a remote-resource block locally

### `dashboard/overview.mdx`

Short MDX with: Status, What this block does, Inputs table, Outputs table, Last run ("No runs yet.").

### `memory/notes.md`

```markdown
# Notes

```

### `scripts/start.sh`, `dryrun.sh`, `clean.sh`

Stub scripts with shebang, purpose comment, `set -euo pipefail`, and a TODO. Make all three executable (`chmod +x`).

### `artifacts/index.yaml`

```yaml
runs: []
```

Each entry appended after a run:
```yaml
- id: run_001
  started_at: "2026-05-03T08:00:00Z"
  completed_at: "2026-05-03T10:11:35Z"
  status: completed        # running | completed | failed
  archive: artifacts/archives/run_001/
  notes: "one-line summary of what this run tested"
```

### `repos/`

Only create if repos were declared. For each repo:

1. If inside a git repo: `git submodule add <url> repos/<name>`, then pin with `git checkout <commit_id>` if a commit was given.
2. If not inside a git repo: create `repos/<name>/README.md` with clone URL, commit, and role.

Do not create an empty `repos/` folder.

### `subblock/`

Create the directory. For each declared child, do not create it directly, but instead ask the users to create them separately.

## After scaffolding

Print a short summary (under 10 lines):
- Path where the block was created
- File tree
- "Fill in `runtime_info.input` in `config.yaml`, then run `/block:run` to preflight and execute (or `scripts/dryrun.sh` to validate without side effects)."
