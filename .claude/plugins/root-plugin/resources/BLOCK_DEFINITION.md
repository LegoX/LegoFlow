# What Is A Block

A `block` is the basic collaboration unit in a block-structured project. It defines what a unit is responsible for, what it depends on, what it produces, and how it runs. Every block follows the same layout so humans and agents can navigate any block without prior knowledge.

The repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is also a block.

This document is organized as four sections, in the order an agent typically needs them:

1. **Block contract** — what's in a block, what `config.yaml` looks like, what each file is for.
2. **Lifecycle** — how a block is bootstrapped, run, stopped, and archived (including the remote-execution rule).
3. **Coordination between blocks** — how parents talk to children.
4. **Operator surface** — the plugin skills that wrap every block's shell scripts in a uniform interface.

---

## 1. Block Contract

### 1.1 Agent team model

Each block is operated by a dedicated agent. The agent reads `CLAUDE.md` for its contract and `config.yaml` for inputs, outputs, resources, and tree position. Agents coordinate through two mechanisms, both detailed in §3:

- **Inter-block wiring** — each block's own `meta_info.dependencies`, showing both the upstream it consumes (`from`) and the downstream its own outputs feed (`to`) (the primary channel).
- **External inputs** — `runtime_info.input` (values that originate outside the block tree).

Each agent maintains its block's `memory/` and `artifacts/` independently. **Live run state lives in `artifacts/index.yaml`** (the newest entry's `status` field, written automatically by `archive_run.sh`) — never in `config.yaml`. This invariant is referenced throughout the rest of this document.

### 1.2 Directory layout

A canonical example block is shipped at [`example_block/`](./example_block/) (sibling of this file). When the layout of a block is in question — what files belong where, what is optional vs. required — the agent should `Read`/`ls` that directory rather than relying on a tree diagram duplicated here.

### 1.3 `config.yaml`

The authoritative schema lives in [`config.template.yaml`](./config.template.yaml) (sibling of this file). Read the template for the exact field set, inline comments, and default conventions. Do not duplicate the schema here. The schema is **enforced** by `<repo_root>/scripts/validate_config.py` (`--root <repo_root>` for the whole tree, `--block <block_dir>` for one block) — every block's `dryrun.sh`, the CI schema tests, and the `:check` skills run it.

Key invariants the template encodes:

- Top-level sections: `meta_info` and `runtime_info` — nothing else. Legacy `status:` and `evolving:` sections and a top-level `environment:` are validation failures (live state lives in `artifacts/index.yaml`; per-run env vars live in `runtime_info.input.env_extra`).
- Inter-block wiring goes in each block's own `meta_info.dependencies` — `from` declared by the consumer, `to` declared by the producer, the same edge on both ends (see §3.1). **Never** put inter-block values in `runtime_info.input` — the dependency entry names which `runtime_info.input` path receives the wired value.
- `runtime_info.input` holds only values originating outside the block tree (API keys, human decisions, external paths). Fill markers are standardized: `human` = the user must replace this before a run (the validator fails on it); `""` = auto-derived at runtime or supplied via an env/file channel (never edit to run); `null` = semantic unset/default; anything else is a real working default.
- `runtime_info.output` holds values this block produces for siblings/consumers. Each output key is a mapping with `path:` (a static location fixed at authoring time) and/or `value:` (run-produced — `null` until the block's run script writes it back, e.g. trainer's `train.sh` STEP 3). Consumers resolve `value` first, then `path`.
- `meta_info.resources.ip`: `local` / null / absent → run on the current host. A real remote IP → SSH + tmux per §2.3.

`config.yaml` is **one-shot per run**: every key is configuration the block reads at launch time (run-produced `output.value` write-backs are the single exception). Live state lives in `artifacts/index.yaml` (§1.1, §2.2), not in `config.yaml`.

### 1.4 File roles

| File | Updated by | Purpose |
|---|---|---|
| `CLAUDE.md` | Agent or human | Agent contract: role, I/O, run rules, archiving rules |
| `config.yaml` | Agent or human | Identity, resources, runtime I/O, tunable params. One-shot per run — no live state. |
| `dashboard/overview.mdx` | Agent | Human-readable current state: done, in progress, next |
| `artifacts/index.yaml` | `archive_run.sh` (and agent for `notes`) | Append-only run index. Example: [`example_block/artifacts/index.yaml`](./example_block/artifacts/index.yaml). |
| `memory/notes.md` | Agent | Long-form observations, decisions, postmortems |

### 1.5 `repos/` is read-only by default

Anything under `<block_dir>/repos/` is **vendored code** — a git submodule or clone pinned to the commit declared in `meta_info.repos.<name>.commit_id`. The operating agent **MUST NOT** modify files under `repos/` as part of normal operation (setup, check, run, dashboard). The pin is what makes a run reproducible and what `archive_run.sh` records in `metadata.yaml`'s `repos:` field; silent edits drift the working tree away from the pin and break that contract without leaving a trace.

If the agent encounters a situation where it believes it must modify code under `repos/` (e.g. a bug in a vendored tool blocks the block from running, a hotfix the upstream hasn't released yet), it must:

1. **Stop** and surface the proposed change to the user, with a verbatim WARNING along the lines of: *"This edit modifies vendored code under `repos/<name>/`. This is not expected by design — the canonical fix is upstream. Proceeding will diverge the working tree from the pinned commit `<sha>` and break the reproducibility contract recorded in `metadata.yaml`."*
2. **Wait for explicit user confirmation** before applying the edit. A user saying "just fix it" without acknowledging the warning is not sufficient — the agent must show the warning text and require a fresh ack.
3. After applying the edit, **flag the pin as stale**: either bump `meta_info.repos.<name>.commit_id` to the new local SHA (if the user will commit upstream), or add a `notes:` entry to the next archive recording the local divergence and the reason. Do not silently leave a pinned commit pointing at a different working tree.

This rule applies symmetrically across all skills: `:setup` configures and pins, it does not patch; `:check` validates against the pin, it does not "fix"; `:run` executes the pinned code, it does not edit it. Edits to `scripts/`, `config.yaml`, `dashboard/`, `memory/`, or `artifacts/` are normal block operation and not subject to this rule.

---

## 2. Lifecycle

### 2.1 Setup → check → run → stop

Each block is operated through the skills described in §4.1: `:setup` bootstraps a fresh clone, `:check` validates without side effects, `:run` executes, `:dashboard` surfaces state. The full playbook for each skill lives in its own `SKILL.md`. The root plugin additionally exposes `:create` for scaffolding new blocks.

**Termination is a script, not a skill.** Each block ships `scripts/stop.sh` next to `start.sh`. The script enumerates the block's live processes (PIDs recorded in `artifacts/index.yaml`, the block's tmux session, the `start.sh` process tree, and any per-block sidecars the block knows about) and signals SIGTERM → grace period → SIGKILL. Block-specific sidecar logic (Docker containers, K8s pods, port-bound proxies) lives in that block's `scripts/stop.sh`; there is no central stop orchestrator. To stop a parent block's tree, invoke each child's `scripts/stop.sh` in reverse dependency order — `:stop` is not a skill because the confirmation gate is well-handled by a TTY `read -p` and there is no agent value-add worth the indirection.

### 2.2 Archiving

Archiving is **automated** by `scripts/archive_run.sh`, which every block keeps next to `start.sh`. Each block's `start.sh` installs an EXIT trap that invokes its sibling `archive_run.sh` — so an archive entry is created whether the run exits cleanly, fails (`set -e`), or is interrupted (SIGINT / SIGTERM). The script picks the next `run_NNN` id by scanning both `artifacts/archives/run_*/` and existing `id: run_NNN` entries in `artifacts/index.yaml`, so manual narrative entries and automated entries share one id space.

Each run produces `artifacts/archives/run_NNN/` containing:

| File | Content | Produced by |
|---|---|---|
| `metadata.yaml` | Full per-run record. Example: [`example_block/artifacts/archives/run_001/metadata.yaml`](./example_block/artifacts/archives/run_001/metadata.yaml). | `archive_run.sh` |
| `config.yaml` | Snapshot of `config.yaml` as it was at run time | `archive_run.sh` |
| `scripts/` | Copy of all scripts (top-level files + non-hidden subdirs; hidden state dirs like `.swegen-py` are skipped) | `archive_run.sh` |
| `session.log` *(optional)* | Claude Code session record (tool calls, agent reasoning, decisions) | Agent, manually after the run |
| `monitor.md` *(optional)* | Human-readable monitor output produced by the agent during the run | Agent, manually after the run |

A full `repos/` tree snapshot is **not** produced; the `repos:` field of `metadata.yaml` records each `repos/<name>/`'s `git rev-parse HEAD` instead. The SHA carries the same information as a tree copy provided the commit is published.

`archive_run.sh` also appends one entry to `artifacts/index.yaml` (using PyYAML for a clean round-trip; falls back to a plain text append if PyYAML is unavailable). For a worked entry, see [`example_block/artifacts/index.yaml`](./example_block/artifacts/index.yaml). The `status` field is derived from the script's exit code: `0` → `completed`, `130`/`143` (SIGINT / SIGTERM) → `interrupted`, anything else → `failed`.

Manual invocation is supported for ad-hoc archives or backfill:

```bash
bash scripts/archive_run.sh [exit_code] [started_at_iso8601] [notes]
```

### 2.3 Remote execution rule

If `meta_info.resources.ip` is a real remote IP (not `local`, null, or absent), the agent **must** execute remotely:

1. Create a tmux window immediately named after the block.
2. SSH into the remote node and attach to (or create) a tmux session there.
3. Run scripts inside that remote session — never run a remote-resource block locally.
4. Double-check with the user whether the code needs to be synced to that node, or the repo is already at the same directory (after path mapping).

---

## 3. Coordination Between Blocks

### 3.1 Inter-block wiring

Every block declares its dependencies in `meta_info.dependencies`, a mapping with exactly two keys, `from` and `to` — each block shows both directions it participates in, from its own file. The mapping is mandatory and always has both keys present (use `{}` for a direction with no edges): `dependencies: {from: {}, to: {}}` for a block with neither.

**`from`** — this block's own **upstream** hand-offs (unchanged from the original grammar):

- **Key** — a dot-path into this block's own `runtime_info.input` naming the input that receives the hand-off (e.g. `task_source.dataset_name`). The path must exist; freeform labels are a validation failure.
- **Value** — either the string `<src>.output.<key>` (a required reference to a sibling block's `runtime_info.output` key), or a mapping for conditional/optional hand-offs:

```yaml
# subblock/tracer/config.yaml — consumer declares its own upstream
meta_info:
  dependencies:
    from:
      task_source.dataset_name:
        from: curator.output.swe_tasks_dir
        when: {task_source.provider: local}   # enforced only while these inputs hold these values
    to: {}

# optional upstream (null producer output → warning, not failure)
meta_info:
  dependencies:
    from:
      model.model_path:
        from: trainer.output.checkpoint_path
        required: false
    to: {}
```

**`to`** — this block's own **downstream** hand-offs, the mirror image, declared by the *producer*:

- **Key** — one of this block's own `runtime_info.output` keys.
- **Value** — either the string `<consumer>.input.<path>` (naming the exact input field on the consumer that receives this output), or a mapping with an optional `when` gate whose keys are **fully-qualified** `<consumer>.input.<path>` (the condition lives on the consumer's own state, since the producer doesn't own an input to gate on):

```yaml
# subblock/curator/config.yaml — producer declares its own downstream
meta_info:
  dependencies:
    from: {}
    to:
      swe_tasks_dir:
        to: tracer.input.task_source.dataset_name
        when: {tracer.input.task_source.provider: local}
```

The same logical edge is declared twice — once by the consumer's `from`, once by the producer's `to` — each block only ever editing its own file. `scripts/validate_config.py` cross-checks the two declarations and emits a `dep:link-mismatch` warning if they've drifted apart (e.g. one side renamed a field and the other wasn't updated).

Resolution reads the producer's `runtime_info.output.<key>`: a non-null `value` first, else `path`. A dangling reference (producer/consumer block or field missing) is always a validation failure, even when the dependency is inactive or optional. `scripts/validate_config.py` enforces all of this.

The **root block's** `config.yaml` lists its children under `meta_info.subblocks` with a `role:` one-liner each — no wiring there; a `dependencies` key under a `subblocks` entry is a validation failure. Root's own `dependencies` is `{from: {}, to: {}}` (it has no `runtime_info.input`/`output` of its own to hang edges off).

`runtime_info.input` is reserved for values that originate outside the block tree entirely; human-supplied values live there directly (marked `human` until filled) — the literal `human` is **not** part of the dependency grammar.

### 3.2 Parent dispatches to children — never to `start.sh`

When a parent block has subblocks declared, the parent's skills (`:run`, `:check`, `:setup`) MUST delegate to each child's corresponding `/<child>:<skill>` rather than reaching into the child's `scripts/start.sh` or shelling into the child's directory. Bypassing the child's skill bypasses its preflight, confirmation, archiving, and remote-execution decision — and silently shifts those responsibilities up to the parent.

A parent block has no `scripts/start.sh` to run; its `:run` is purely an orchestrator.

The order in which a parent walks its children (dependency-resolved forward for `:run` / `:setup`, unconstrained for `:check` / `:dashboard`) is the responsibility of each parent skill's `SKILL.md`; see those files for the precise rules.

Termination follows the same parent-doesn't-reach-into-children spirit but is handled by `scripts/stop.sh` (§2.1), not a skill. To stop a parent block's tree, a stop script (or a human) invokes each child's `scripts/stop.sh` in reverse dependency order.

---

## 4. Block Management with Plugins

Every block ships a Claude Code plugin at `./.claude/plugins/<block_name>-plugin/` that exposes the block's operational interface as slash commands. Plugins are the "meta" launch scripts for the block — they wrap the underlying shell scripts with agent-friendly preflight, confirmation, and reporting layers.

### 4.1 Unified skill set

Every block plugin exposes the same skill surface (uniform interface across the tree):

| Skill | Purpose | Idempotent? |
|---|---|---|
| `/<block>:setup`     | One-shot bootstrap: install env, sync repos to pinned commits, fill in `runtime_info.input` (prompting only for unfilled values). Brings the block from a fresh clone to "`:check` passes". | yes |
| `/<block>:check`     | Read-only preflight: schema, env vars, repo pins, environment integrity, dependency reachability (LLM endpoints, k8s, docker), and `scripts/dryrun.sh`. Reports all failures in one consolidated message with a run-configuration summary. **Mandatory before `:run`.** | yes |
| `/<block>:dashboard` | Surface block state — textual table by default (last run, status, key metrics) plus an optional webui launcher. Read-only. | yes |
| `/<block>:run`       | Preflight → confirm → execute the block. At a leaf block, runs `scripts/start.sh`. At a parent block, dispatches to each child's `/<child>:run` in dependency order (see §3.2) — **never** reaches into a child's `scripts/start.sh` directly. May document block-specific substeps inside the block's `:run`; `:run` remains the orchestrator. | no (mutates state) |
| `/root:create` *(root only)* | Scaffold a new block under `subblock/<name>/` from the canonical [`example_block/`](./example_block/) layout, fill in its `config.yaml` from an intake form or chat-driven Q&A, and wire it into the parent's `meta_info.subblocks`. **Only the root plugin exposes this skill** — subblocks do not create further blocks (the tree is flat under each subblock). | no (mutates state) |

Termination is intentionally not a skill — see §2.1. Each block ships a `scripts/stop.sh` that does the SIGTERM → grace → SIGKILL work directly.

### 4.2 Plugin layout

```
<block_dir>/.claude/
├── settings.json                                   # registers the marketplace + enables the plugin
└── plugins/
    ├── .claude-plugin/marketplace.json             # local marketplace declaration
    └── <block_name>-plugin/
        ├── .claude-plugin/plugin.json              # plugin manifest (name = block name)
        ├── README.md                               # human-facing per-plugin docs (not auto-loaded)
        ├── resources/                              # bundled reference docs / templates / archive_run.sh
        │   └── BLOCK_DEFINITION.md                 # only the root plugin ships the canonical copy
        └── skills/
            ├── setup/SKILL.md
            ├── check/SKILL.md
            ├── dashboard/SKILL.md
            ├── run/SKILL.md
            └── create/SKILL.md                     # root plugin only
```

Naming rules:

- Plugin manifest `name` MUST equal the block name (`root`, `curator`, `tracer`, …). The slash-command namespace is `/<name>:`.
- Marketplace `name` in `marketplace.json` MUST equal the key used in `settings.json` `extraKnownMarketplaces` (e.g. `curator-local`).
- Directory name follows `<block_name>-plugin/` for grep-friendliness; the source path in `marketplace.json` (`./<block_name>-plugin`) must match.

### 4.3 Inter-plugin references

Each block's plugin lives **only** in that block's own `.claude/plugins/` — no symlinks, no copies. When a block's skill delegates to a subblock (per §3.2), it does so by **calling the subblock's skill**, not by importing its code. The recursion happens through the plugin command surface, not the filesystem.

When a skill needs reference material (the canonical `BLOCK_DEFINITION.md`, intake templates, `archive_run.sh`), it reads from `<repo_root>/.claude/plugins/root-plugin/resources/`. The root plugin is the only place that ships the canonical copy.

> **Auto-load note:** Claude Code only auto-loads `skills/`, `commands/`, `agents/`, `hooks/`, `.mcp.json`, `.lsp.json`, `monitors/`, and `settings.json`. The `resources/` folder is **not** auto-loaded — a skill must explicitly `Read` files from it.
