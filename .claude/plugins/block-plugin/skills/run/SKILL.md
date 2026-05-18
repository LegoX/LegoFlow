---
name: run
description: >
  Preflight and execute the block in the current working directory. Reads the bundled BLOCK_DEFINITION.md to recall the contract, then validates that the block in CWD is ready to run — config.yaml is well-formed, every runtime_info.input is filled, every inter-block dependency declared in meta_info.subblocks[].dependencies resolves to a non-null output of the named sibling block, repos under repos/ are present and at their pinned commits, the environment (venv_path) exists, and scripts/start.sh is present and executable. Only if all checks pass does it run scripts/start.sh — locally, or inside a tmux+SSH session if meta_info.resources.ip is set. After execution, it archives the run per the BLOCK_DEFINITION rule and updates status. Triggers on phrases like "run this block", "execute the block", "kick off start.sh", "fire the block", "run /block:run".
---

# /block:run

Preflight the block in the **current working directory**, then execute `scripts/start.sh`. Refuse to execute if any prerequisite is missing — a missing input is the user's signal to fill it, never a signal to invent a value.

## Step 0 — Orient

Read `references/BLOCK_DEFINITION.md` bundled in this plugin (sibling of the `skills/` folder containing this file). It is the contract. Pay particular attention to:

- The `meta_info` / `runtime_info` / `status` / `evolving` schema.
- The **wiring rule**: inter-block values live only in `meta_info.subblocks[<child>].dependencies` (formatted `<source_block>.output.<key>` or the literal `human`), never in `runtime_info.input`. `runtime_info.input` is exclusively for values that originate **outside** the block tree (API keys, external dataset paths, human decisions).
- The **remote-execution rule**: if `meta_info.resources.ip` is set, the block must be executed inside a tmux session on that remote node, reached over SSH. Never run a remote-resource block locally.
- The **archiving rule**: after each run, create `artifacts/archives/run_NNN/` with `metadata.yaml`, snapshot `config.yaml`, snapshot `scripts/`, snapshot `repo/`, `session.log`, `monitor.md`; then append one entry to `artifacts/index.yaml` with `archive: artifacts/archives/run_NNN/`.

## Step 1 — Load this block

The "current block" is the current working directory. Read, in order:

1. `./config.yaml` — if missing, abort: "This directory is not a block (no `config.yaml`). Run `/block:create` to scaffold one first."
2. `./CLAUDE.md` — read it; honor any block-specific rules it states.
3. `./dashboard/overview.mdx` — useful context, not load-bearing.

## Step 2 — Load subblocks

For each `name` listed under `meta_info.subblocks` in `./config.yaml`, read `./subblock/<name>/config.yaml`. Keep a map:

```
<name> -> {
  status: <child status.phase>,
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
| 3 | Every key under `runtime_info.input` has a non-null, non-empty value. | "Input `<key>` is unfilled. Edit `config.yaml` and set it." |
| 4 | For each `child` in `meta_info.subblocks`, for each `dep_key: dep_value` in `subblocks[child].dependencies`: if `dep_value` is the literal `human`, then `runtime_info.input.<dep_key>` (on the parent — this block) must be non-null. Otherwise `dep_value` parses as `<src>.output.<key>` and `subblock/<src>/config.yaml`'s `runtime_info.output.<key>` must be non-null. | "Subblock `<child>` dependency `<dep_key>` is unresolved: `<dep_value>` is null/missing." |
| 5 | For each entry under `meta_info.repos`: `./repos/<name>/` exists. If a `commit_id` is pinned, the checked-out HEAD of that submodule matches it. | "Repo `<name>` missing under `repos/`." or "Repo `<name>` HEAD `<actual>` does not match pinned `<commit_id>`." |
| 6 | If `meta_info.environment.venv_path` is set: that path exists on the host that will run the script (local for local execution; the remote node for remote). | "Virtual env `<path>` not found." |
| 7 | If `meta_info.resources.ip` is set: `ssh -o BatchMode=yes -o ConnectTimeout=5 <ip> true` succeeds. Also, before executing, ask the user once: "Code at `<directory>` on remote — already in sync, or should I rsync the current tree first?" (per BLOCK_DEFINITION's remote-execution rule). | "Cannot SSH to `<ip>`." |

If any check fails, print all failures in one message, do **not** flip `status.phase`, and stop. Do **not** invent values or skip checks just because the user said "just run it" — they need to know what is missing.

## Step 4 — Stamp running state

If all checks pass:

1. Determine the next run id: scan `artifacts/index.yaml` for the highest `run_NNN`, use the next zero-padded id (e.g. `run_003`).
2. In `./config.yaml`: set `status.phase: running`, `status.last_updated: <today ISO date>`, `status.progress: "<run_NNN> in flight"`, `status.blockers: null`.
3. Append a starting entry to `artifacts/index.yaml`:
   ```yaml
   - id: run_NNN
     started_at: "<UTC now ISO>"
     status: running
     notes: "<one-line summary of what this run is testing — derive from status.progress or ask the user briefly if non-obvious>"
   ```

## Step 5 — Execute `scripts/start.sh`

- **Local execution** (no `meta_info.resources.ip`): run `bash ./scripts/start.sh` from CWD, streaming stdout/stderr. Capture the full session into a temporary log file you will later move into the archive as `session.log`.
- **Remote execution** (`meta_info.resources.ip` is set):
  1. Open a local tmux window named after this block (`tmux new-window -n <meta_info.name>`).
  2. Inside it, `ssh <resources.ip>` (using credentials from `resources.pwd` per the contract).
  3. On the remote, attach to (or create) a tmux session named after this block.
  4. Inside that remote tmux session, `cd <resources.directory>` then `bash ./scripts/start.sh`.
  5. Do **not** execute `start.sh` on the local host.

Keep `status.progress` in `./config.yaml` updated with meaningful milestones if the script exposes them (e.g. stage names in its output).

## Step 6 — Archive on completion

When `start.sh` exits, regardless of exit code:

1. Create `./artifacts/archives/run_NNN/`.
2. Write `metadata.yaml` with: `id`, `started_at`, `completed_at`, `status` (`completed` if exit 0 else `failed`), `exit_code`, copy of `runtime_info.input` at run time, and for each entry in `meta_info.repos` the resolved commit id.
3. Snapshot `./config.yaml` to `archives/run_NNN/config.yaml`.
4. Copy `./scripts/` to `archives/run_NNN/scripts/`.
5. Snapshot the repo state to `archives/run_NNN/repo/` — for submodules, a `commit_id.txt` per repo is sufficient if a full copy is wasteful; the BLOCK_DEFINITION allows reference-by-commit.
6. Move the captured execution log to `archives/run_NNN/session.log`.
7. Create `archives/run_NNN/monitor.md` as a brief human-readable narrative of what happened (one or two paragraphs).
8. Update the matching entry in `./artifacts/index.yaml`: set `completed_at`, `status` (`completed` / `failed`), add `archive: artifacts/archives/run_NNN/`, and refine `notes`.
9. Update `./config.yaml`: `status.phase` to `done` (on success) or `failed` (on non-zero exit), `status.progress: null`, `status.next_steps` set appropriately (e.g. "Inspect `artifacts/archives/run_NNN/`."), `status.blockers` set to the failure cause if any, `status.last_updated` to now.

## Step 7 — Report

Print a short summary (under 12 lines): run id, duration, exit status, archive path, the block's new `status.phase`, and either the produced outputs (from `runtime_info.output` if `start.sh` updated them) or the failure cause.
