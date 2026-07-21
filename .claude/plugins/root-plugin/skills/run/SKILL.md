---
name: run
description: >
  Preflight and execute the block in the current working directory. Reads the bundled BLOCK_DEFINITION.md to recall the contract, then validates that the block in CWD is ready to run — config.yaml is well-formed, every runtime_info.input is filled, every inter-block dependency declared in the block's own meta_info.dependencies resolves against the named sibling block's runtime_info.output, repos under repos/ are present and at their pinned commits, the environment (venv_path) exists, and scripts/start.sh is present and executable. Only if all checks pass does it run scripts/start.sh — locally when meta_info.resources.ip is absent, null, or "local"; inside a tmux+SSH session on the named host when meta_info.resources.ip is a real remote IP. Each run is archived automatically by scripts/archive_run.sh (installed by start.sh's EXIT trap). Triggers on phrases like "run this block", "execute the block", "kick off start.sh", "fire the block", "run /root:run".
---

# /root:run

Preflight the target block, then execute its `scripts/start.sh`. Refuse to execute if any prerequisite is missing — a missing input is the user's signal to fill it, never a signal to invent a value.

## Arguments

The args string is free-form natural language (may be empty). The agent reads the whole string holistically — no token parsing — to decide **which block to run** and **what (if anything) to change** beforehand.

### Target resolution

List `./subblock/` to get the set of valid block names, then read the args string and decide:

- **Single block clearly identified** (literal name or unambiguous paraphrase — consult each block's `CLAUDE.md` when the user uses a description) → `TARGET_DIR=./subblock/<name>/`.
- **Multiple blocks mentioned, or genuinely ambiguous** → ask the user which one. Do not guess.
- **No block mentioned** (empty, generic, or refers to the whole pipeline) → `TARGET_DIR=CWD` (the root). The root may lack its own `config.yaml`; treat that as a warning, not an abort.
- **Block inferred but `./subblock/<name>/` doesn't exist** → abort with the actual `./subblock/` listing and ask the user to pick. Never fall back to root silently.

### Confirmation

- Args empty, or names a block with no extra instruction → run directly.
- Instruction implies a config edit or flag injection → propose the concrete change (file path, old → new value, or flag to inject) and confirm before applying.
- Ambiguous target → ask before proceeding.

Never silently mutate `runtime_info` or `meta_info` fields. A user instruction is permission to *propose*, not to act unilaterally.

### Examples

| Args | Target | Behavior |
| ---- | ------ | -------- |
| *(empty)* | root | run root `start.sh` directly |
| `curator` | curator | run directly |
| `curator only 32 verified tasks` | curator | propose config/flag change, confirm, run |
| `run curator with 32 tasks` | curator | same — name embedded in text |
| `run the trajectory generator` | tracer | resolved via paraphrase + `CLAUDE.md` |
| `run curator and tracer` | ambiguous | ask which block |
| `start the data pipeline` | root | run root `start.sh` directly |
| `run frobnicator` | abort | print valid list, ask user to pick |

Every step below operates on `TARGET_DIR`. Where the rest of this document says "this block" or "CWD", read it as `TARGET_DIR`.

**IMPORTANT: Before executing, the agent MUST:**
1. Run `/root:check` (or this skill's built-in preflight) to validate all prerequisites.
2. Present the check results and run configuration summary to the user.
3. **Wait for explicit user confirmation** before launching `scripts/start.sh`. Never auto-launch — training runs consume GPUs for hours and are hard to reverse once started.

## Step 0 — Orient

Read `resources/BLOCK_DEFINITION.md` bundled in this plugin (sibling of the `skills/` folder containing this file). It is the contract. Pay particular attention to:

- The `meta_info` / `runtime_info` schema (two top-level sections only; config.yaml is one-shot per run — no live status field, no `evolving:` section).
- The **wiring rule**: each consumer block declares its own upstream in a flat `meta_info.dependencies` (keys are dot-paths into that block's `runtime_info.input`; values are `<source_block>.output.<key>` strings or `{from, when, required}` mappings), never in `runtime_info.input`. `runtime_info.input` is exclusively for values that originate **outside** the block tree (API keys, external dataset paths, human decisions), with `human` as the must-fill marker.
- The **remote-execution rule**: `meta_info.resources.ip: local` (or null/absent) means run on the current host — no SSH. Only a real remote IP triggers SSH+tmux. Never attempt to SSH to the literal value `local`.
- The **archiving rule**: after each run, create `artifacts/archives/run_NNN/` with `metadata.yaml` (id, block, timestamps, status, exit_code, repo commit SHAs), snapshot `config.yaml`, snapshot `scripts/`, `session.log`, `monitor.md`; then append one entry to `artifacts/index.yaml` with `archive: artifacts/archives/run_NNN/`. Note: repo trees are **not** copied — only commit SHAs are recorded in `metadata.yaml`.

## Step 1 — Load this block

The "current block" is `TARGET_DIR` (CWD when `block_name` is unset; `./subblock/<block_name>/` when set). Read, in order:

1. `<TARGET_DIR>/config.yaml`:
   - If `block_name` is **set**, this file is required — abort with "Missing `config.yaml` under `subblock/<block_name>/`." if absent.
   - If `block_name` is **unset** (root mode), the root `config.yaml` is expected to exist (orchestration identity + `meta_info.subblocks` roster). If it is absent, fall back to the pseudo-root pattern: skip config-driven preflight (Step 3 checks #3–#7 are scoped to subblocks via their own configs) and proceed, emitting a warning.
2. `<TARGET_DIR>/CLAUDE.md` — read it; honor any block-specific rules it states.
3. `<TARGET_DIR>/dashboard/overview.mdx` — useful context, not load-bearing.

If `config.yaml` is required but absent (subblock target), go back to user and ask them to double check this really is a block.

## Step 2 — Load subblocks

For each `name` listed under `meta_info.subblocks` in `./config.yaml`, read `./subblock/<name>/config.yaml`. Keep a map:

```
<name> -> {
  latest_run: <newest entry in subblock/<name>/artifacts/index.yaml, or null>,
  output: <child runtime_info.output, may have null values>
}
```

If a declared subblock directory is missing, record it as a preflight failure (do not abort yet — collect all failures first).

## Step 3 — Preflight checklist (fail-fast, all failures reported together)

Walk through every check below. Collect failures. Only after the full pass, decide whether to proceed.

| # | Check | Failure message |
| - | ----- | ---------------- |
| 1 | `./scripts/start.sh` exists. | "Missing `scripts/start.sh`." |
| 2 | `./scripts/start.sh` is executable. If not, `chmod +x` it and emit a warning (not a failure). | warning only |
| 3 | Run `python3 <repo_root>/scripts/validate_config.py --block <TARGET_DIR>` (or `--root <repo_root>` in root mode). Any `[FAIL]` line — unfilled `human` markers (`input:unfilled`), legacy placeholders, schema drift, or dependency failures (`dep:bad-key` / `dep:bad-ref` / `dep:unresolved`) — is a preflight failure; quote the validator's message verbatim. | validator `[FAIL]` lines |
| 4 | (covered by check #3 — the validator resolves each `meta_info.dependencies` entry against the producer's `runtime_info.output`, honoring `when:` gates and `required: false`.) | — |
| 5 | For each entry under `meta_info.repos`: `./repos/<name>/` exists. If a `commit_id` is pinned, the checked-out HEAD of that submodule matches it. | "Repo `<name>` missing under `repos/`." or "Repo `<name>` HEAD `<actual>` does not match pinned `<commit_id>`." |
| 6 | If `meta_info.environment.venv_path` is set: that path exists on the host that will run the script (local for local execution; the remote node for remote). | "Virtual env `<path>` not found." |
| 7 | If `meta_info.resources.ip` is set: `ssh -o BatchMode=yes -o ConnectTimeout=5 <ip> true` succeeds. Also, before executing, ask the user once: "Code at `<directory>` on remote — already in sync, or should I rsync the current tree first?" (per BLOCK_DEFINITION's remote-execution rule). | "Cannot SSH to `<ip>`." |

If any check fails, print all failures in one message and stop. Do **not** invent values or skip checks just because the user said "just run it" — they need to know what is missing.

## Step 4 — Execute

The execution model depends on whether `TARGET_DIR` is a **leaf block** (no entries under `meta_info.subblocks`) or a **parent block** (has subblocks declared).

### Step 4a — Parent block: dispatch to children, never to their `start.sh`

**Rule (non-negotiable):** when `TARGET_DIR` has subblocks, this skill's job is *orchestration only*. It MUST invoke each child block's own `/<child>:run` skill (via the slash-command surface), in the dependency-resolved order declared under `meta_info.subblocks`. It MUST NOT reach into `subblock/<child>/scripts/start.sh` directly, MUST NOT shell out into the child's directory, and MUST NOT duplicate the child's preflight logic.

Rationale: each block owns its own contract (preflight, confirmation, archiving, remote-execution decision). Bypassing the child skill bypasses those guarantees and breaks the recursion model — the parent would silently inherit responsibility for things the child is supposed to enforce.

Procedure at a parent:

1. Topologically sort the children by the dependency graph read from **each child's own** `meta_info.dependencies` (`subblock/<name>/config.yaml`): a child that consumes `<src>.output.<key>` must run after `<src>`. Edges that are conditionally inactive (`when:` not matching) or optional (`required: false`) still order the sort but do not block execution. Break ties by the declaration order in the root's `meta_info.subblocks`.
2. For each child in that order, invoke `/<child>:run` and wait for it to complete. The child skill is responsible for its own preflight, confirmation, execution, and archiving.
3. If a child fails or the user aborts at its confirmation gate, stop the parent's dispatch immediately — do not run downstream children. Report which child stopped the pipeline and surface its failure verbatim.
4. The parent block does not have its own `scripts/start.sh` to execute; it has nothing to run beyond dispatching children. If the user explicitly asks for "just the root" (no children), there is nothing to do — say so and exit.

### Step 4b — Leaf block: run `scripts/start.sh`

Only leaf blocks (no subblocks declared) run their own `start.sh`. Parent blocks never do — see 4a.

- **Local execution** (no `meta_info.resources.ip`, or it is `local`/null): `cd <TARGET_DIR>` then run `bash ./scripts/start.sh`, streaming stdout/stderr. Optionally capture the session to a file you'd move into the archive as `session.log`.
- **Remote execution** (`meta_info.resources.ip` is a real IP):
  1. Open a local tmux window named after this block (`tmux new-window -n <meta_info.name>`).
  2. Inside it, `ssh <resources.ip>` (using credentials from `resources.pwd` per the contract).
  3. On the remote, attach to (or create) a tmux session named after this block.
  4. Inside that remote tmux session, `cd <resources.directory>` then `bash ./scripts/start.sh`.
  5. Do **not** execute `start.sh` on the local host.

`config.yaml` is **one-shot per run**: do not edit it during the run to track progress. Live state belongs in `artifacts/index.yaml`.

## Step 5 — Archive (mostly automatic)

Applies to **leaf blocks only**. At a parent, archiving happens inside each child's `/<child>:run` — the parent has no archive of its own; the union of children's `artifacts/index.yaml` entries *is* the parent's live state.

Each leaf block's `start.sh` installs an EXIT trap that invokes `scripts/archive_run.sh`. When the run exits (success, error, SIGINT, SIGTERM) the helper automatically:

- creates `artifacts/archives/run_NNN/` with `metadata.yaml` (id, block, timestamps, status, exit_code, repo SHAs), a snapshot of `config.yaml`, and a snapshot of `scripts/`;
- appends one entry to `artifacts/index.yaml` with the new run id, timestamps, status, and archive path.

The only post-run steps for the agent are optional and additive:

1. Drop a captured `session.log` into `artifacts/archives/run_NNN/` if you streamed one in Step 4.
2. Optionally write `artifacts/archives/run_NNN/monitor.md` as a 1–2 paragraph human-readable narrative of what happened.
3. Optionally refine the `notes` field of the new `artifacts/index.yaml` entry.

Do **not** edit `config.yaml` after the run to record what happened — the archive already does that.

## Step 6 — Report

Print a short summary (under 10 lines): run id, duration, exit status, archive path, and either the produced outputs (from `runtime_info.output` if `start.sh` updated them) or the failure cause. The live state is whatever the newest `artifacts/index.yaml` entry says.
