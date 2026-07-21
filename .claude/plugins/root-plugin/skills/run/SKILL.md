---
name: run
description: >
  Resolve and execute a block through the block protocol. An explicitly
  selected subblock delegates to that block's `/<name>:run` skill and waits
  for it. With no selected subblock, preflight the block in the current
  working directory: a parent dispatches to its children, while a leaf may
  execute its own `scripts/start.sh` locally or through SSH+tmux according to
  `meta_info.resources.ip`. Triggers on phrases like "run this block",
  "execute the block", "kick off start.sh", "fire the block", "run /root:run".
---

# /root:run

Resolve the target and preserve each block's run boundary. Explicit subblock
targets delegate to their own run skill. Direct `start.sh` execution is reserved
for a leaf block invoked from inside that block.

## Arguments

The args string is free-form natural language (may be empty). The agent reads the whole string holistically — no token parsing — to decide **which block to run** and **what (if anything) to change** beforehand.

### Target resolution

List `./subblock/` to get the set of valid block names, then read the args string and decide:

- **Single block clearly identified** (literal name or unambiguous paraphrase — consult each block's `CLAUDE.md` when the user uses a description) → set `block_name=<name>` and `TARGET_DIR=./subblock/<name>/`.
- **Multiple blocks mentioned, or genuinely ambiguous** → ask the user which one. Do not guess.
- **No block mentioned** (empty, generic, or refers to the whole pipeline) → `TARGET_DIR=CWD` (the root). The root may lack its own `config.yaml`; treat that as a warning, not an abort.
- **Block inferred but `./subblock/<name>/` doesn't exist** → abort with the actual `./subblock/` listing and ask the user to pick. Never fall back to root silently.

### Confirmation

- Args empty → run the block in CWD. A named block with no extra instruction
  delegates immediately to `/<name>:run`.
- Instruction implies a config edit or flag injection → propose the concrete change (file path, old → new value, or flag to inject) and confirm before applying.
- Ambiguous target → ask before proceeding.

Never silently mutate `runtime_info` or `meta_info` fields. A user instruction is permission to *propose*, not to act unilaterally.

### Examples

| Args | Target | Behavior |
| ---- | ------ | -------- |
| *(empty)* | root | dispatch root children through their run skills |
| `curator` | curator | invoke `/curator:run` and wait |
| `curator only 32 verified tasks` | curator | propose config/flag change, confirm, then invoke `/curator:run` |
| `run curator with 32 tasks` | curator | same — name embedded in text |
| `run the trajectory generator` | tracer | resolved via paraphrase + `CLAUDE.md` |
| `run curator and tracer` | ambiguous | ask which block |
| `start the data pipeline` | root | dispatch root children through their run skills |
| `run frobnicator` | abort | print valid list, ask user to pick |

An explicitly named target follows Step 0a and then returns. Every later step
operates directly on CWD because no `block_name` was selected.

**IMPORTANT: Before executing, the agent MUST:**
1. For an explicitly selected subblock, delegate to its run skill; that skill
   owns preflight, the run summary, and confirmation.
2. For direct CWD execution, run `/root:check` (or this skill's built-in
   preflight), present the summary, and **wait for explicit user confirmation**.
3. Never auto-launch — training runs consume GPUs for hours and are hard to
   reverse once started.

## Step 0 — Orient

Read `resources/BLOCK_DEFINITION.md` bundled in this plugin (sibling of the `skills/` folder containing this file). It is the contract. Pay particular attention to:

- The `meta_info` / `runtime_info` / `evolving` schema (config.yaml is one-shot per run — no live status field).
- The **wiring rule**: inter-block values live only in `meta_info.subblocks[<child>].dependencies` (formatted `<source_block>.output.<key>` or the literal `human`), never in `runtime_info.input`. `runtime_info.input` is exclusively for values that originate **outside** the block tree (API keys, external dataset paths, human decisions).
- The **remote-execution rule**: `meta_info.resources.ip: local` (or null/absent) means run on the current host — no SSH. Only a real remote IP triggers SSH+tmux. Never attempt to SSH to the literal value `local`.
- The **archiving rule**: after each run, create `artifacts/archives/run_NNN/` with `metadata.yaml` (id, block, timestamps, status, exit_code, repo commit SHAs), snapshot `config.yaml`, snapshot `scripts/`, `session.log`, `monitor.md`; then append one entry to `artifacts/index.yaml` with `archive: artifacts/archives/run_NNN/`. Note: repo trees are **not** copied — only commit SHAs are recorded in `metadata.yaml`.

## Step 0a — Delegate an explicitly selected subblock

If `block_name` is set by target resolution:

1. Invoke `/<name>:run`, forwarding any remaining user intent that the selected
   block needs to choose its supported mode, and wait for it to complete.
2. The selected block's run skill owns its preflight, confirmation gate,
   execution, remote-resource decision, and archiving.
3. If it fails or the user declines confirmation, return that outcome verbatim.

The root skill MUST NOT execute the selected subblock's `scripts/start.sh`,
shell into its directory, or duplicate its run implementation. Do not continue
to Step 1 after delegation completes.

## Step 1 — Load this block

This direct path is reached only when `block_name` is unset. The current block
is the current working directory. Read, in order:

1. `./config.yaml`. If it is absent, treat CWD as the SWE-Lego-Live
   coordinator pattern: skip config-driven checks and emit a warning.
2. `./CLAUDE.md` — read it; honor any block-specific rules it states.
3. `./dashboard/overview.mdx` — useful context, not load-bearing.

If CWD is intended to be a leaf block but lacks `config.yaml`, stop and ask the
user to confirm the working directory.

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
| 1 | If this direct CWD target is a leaf, `./scripts/start.sh` exists. Parent coordinators do not require one. | "Missing `scripts/start.sh`." |
| 2 | For a direct leaf, `./scripts/start.sh` is executable. If not, `chmod +x` it and emit a warning (not a failure). | warning only |
| 3 | Every key under `runtime_info.input` has a non-null, non-empty value. | "Input `<key>` is unfilled. Edit `config.yaml` and set it." |
| 4 | For each `child` in `meta_info.subblocks`, for each `dep_key: dep_value` in `subblocks[child].dependencies`: if `dep_value` is the literal `human`, then `runtime_info.input.<dep_key>` (on the parent — this block) must be non-null. Otherwise `dep_value` parses as `<src>.output.<key>` and `subblock/<src>/config.yaml`'s `runtime_info.output.<key>` must be non-null. | "Subblock `<child>` dependency `<dep_key>` is unresolved: `<dep_value>` is null/missing." |
| 5 | For each entry under `meta_info.repos`: `./repos/<name>/` exists. If a `commit_id` is pinned, the checked-out HEAD of that submodule matches it. | "Repo `<name>` missing under `repos/`." or "Repo `<name>` HEAD `<actual>` does not match pinned `<commit_id>`." |
| 6 | If `meta_info.environment.venv_path` is set: that path exists on the host that will run the script (local for local execution; the remote node for remote). | "Virtual env `<path>` not found." |
| 7 | If `meta_info.resources.ip` is set: `ssh -o BatchMode=yes -o ConnectTimeout=5 <ip> true` succeeds. Also, before executing, ask the user once: "Code at `<directory>` on remote — already in sync, or should I rsync the current tree first?" (per BLOCK_DEFINITION's remote-execution rule). | "Cannot SSH to `<ip>`." |

If any check fails, print all failures in one message and stop. Do **not** invent values or skip checks just because the user said "just run it" — they need to know what is missing.

## Step 4 — Execute the direct CWD target

Step 0a already handled every explicitly selected subblock. This execution
model applies only to the block in the current working directory.

### Step 4a — Parent block: dispatch to children, never to their `start.sh`

**Rule (non-negotiable):** when CWD has subblocks, this skill's job is
*orchestration only*. It MUST invoke each child block's own `/<child>:run` skill
(via the slash-command surface), in the dependency-resolved order declared under
`meta_info.subblocks`. It MUST NOT reach into `subblock/<child>/scripts/start.sh`
directly, MUST NOT shell out into the child's directory, and MUST NOT duplicate
the child's preflight logic.

Rationale: each block owns its own contract (preflight, confirmation, archiving, remote-execution decision). Bypassing the child skill bypasses those guarantees and breaks the recursion model — the parent would silently inherit responsibility for things the child is supposed to enforce.

Procedure at a parent:

1. Topologically sort `meta_info.subblocks` by the `dependencies` graph (a child that consumes `<src>.output.<key>` must run after `<src>`). Break ties by declaration order.
2. For each child in that order, invoke `/<child>:run` and wait for it to complete. The child skill is responsible for its own preflight, confirmation, execution, and archiving.
3. If a child fails or the user aborts at its confirmation gate, stop the parent's dispatch immediately — do not run downstream children. Report which child stopped the pipeline and surface its failure verbatim.
4. The parent block does not have its own `scripts/start.sh` to execute; it has nothing to run beyond dispatching children. If the user explicitly asks for "just the root" (no children), there is nothing to do — say so and exit.

### Step 4b — Direct leaf invocation: run `scripts/start.sh`

Only when `block_name` is unset and the current working directory is a leaf
block (no subblocks declared) may this skill run that leaf's own `start.sh`.
Parent blocks and explicitly selected subblocks never do — see Step 0a and 4a.

- **Local execution** (no `meta_info.resources.ip`, or it is `local`/null): run `bash ./scripts/start.sh` in CWD, streaming stdout/stderr. Optionally capture the session to a file you'd move into the archive as `session.log`.
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
