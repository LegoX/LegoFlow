---
name: stop
description: >
  Gracefully terminate the running processes of the target block. Identifies every live process associated with the block — PIDs recorded in `artifacts/index.yaml` / `config.yaml.status`, the tmux session named after the block, child processes of `scripts/start.sh`, and any per-block sidecars (LiteLLM proxy, vLLM, Harbor jobs, dashboards) discoverable by CWD or by start.sh path. Always lists the candidate PIDs to the user, requires explicit confirmation, then sends SIGTERM, waits for the configured grace period, and escalates to SIGKILL only for processes that did not exit. At a parent block (one with subblocks declared), dispatches to each child block's `/<child>:stop` instead — never kills child processes directly. Honors the local-vs-remote contract: stops local processes locally; stops processes inside the remote tmux session over SSH when `meta_info.resources.ip` is a real IP. Read-only with respect to `config.yaml`; updates the newest `artifacts/index.yaml` entry's `status` to `interrupted` only if the EXIT trap in `start.sh` did not already do so. Triggers on phrases like "stop the rl block", "kill swegen", "shut down trajgen", "abort the run", "stop /root:run", "tear down the dashboard for X".
---

# /root:stop

Stop the target block's running processes safely. Refuse to kill processes the agent cannot confidently attribute to the block — a missing PID is the user's signal to tell the agent where to look, never a signal to `pkill -f` against a loose pattern.

## Arguments

Same parsing rules as `/root:run`: free-form natural language. The agent reads the whole string and decides which block to stop and any non-default options ("force kill", "skip grace period", "leave the dashboard running"). Honor those only after explicit confirmation.

### Target resolution

List `./subblock/` to get valid block names, then:

- **Single block clearly identified** → `TARGET_DIR=./subblock/<name>/`.
- **Multiple blocks mentioned, or ambiguous** → ask the user which one. Do not guess.
- **No block mentioned** ("stop everything", empty, "stop the pipeline") → `TARGET_DIR=CWD` (the root), which then recurses per Step 3a.
- **Block name doesn't exist** → abort with the actual `./subblock/` listing and ask the user to pick. Never fall back to root silently.

Every step below operates on `TARGET_DIR`.

**IMPORTANT: Before sending any signal, the agent MUST:**
1. Enumerate the candidate processes and present them to the user (PID, command, CWD, start time).
2. **Wait for explicit user confirmation** before sending SIGTERM. Never auto-kill — stopping a training job mid-step may leave checkpoints or state in an inconsistent shape, and stopping the wrong PID is unrecoverable.
3. After confirmation, escalate only as configured below.

## Step 0 — Orient

Read `resources/BLOCK_DEFINITION.md` bundled in this plugin. Pay particular attention to:

- The **remote-execution rule**: if `meta_info.resources.ip` is a real IP, all enumeration and signaling happens over SSH inside the remote tmux session. Never kill local processes in that case (there are none owned by this block).
- The **archiving rule**: `start.sh`'s EXIT trap is supposed to invoke `archive_run.sh` on SIGTERM/SIGINT and write `status: interrupted` to `artifacts/index.yaml`. If the trap fires successfully, the agent's only job afterwards is to verify it did — not to write the entry again.

## Step 1 — Load this block

The "current block" is `TARGET_DIR`. Read:

1. `<TARGET_DIR>/config.yaml`:
   - If `block_name` is **set**, this file is required — abort if absent.
   - If `block_name` is **unset** (root mode) and the file is absent, that's the coordinator pattern: skip the leaf-block logic (Steps 4–6) and proceed straight to Step 3a's dispatch.
2. `<TARGET_DIR>/CLAUDE.md` — honor any block-specific stop rules it states (e.g. "RL: drain in-flight rollouts before SIGTERM").
3. `<TARGET_DIR>/artifacts/index.yaml` — the newest entry's `status` tells you whether there is even anything to stop.

## Step 2 — Decide whether there is anything to stop

Look at the newest entry of `<TARGET_DIR>/artifacts/index.yaml`:

- `status: running` → there should be a live job. Proceed.
- `status: completed | failed | interrupted` → the EXIT trap fired. Confirm with the user before signaling anything ("`artifacts/index.yaml` says the last run already ended at `<completed_at>` with status `<status>`. Is there still a stray process to kill?"). If they confirm, continue; otherwise stop here.
- No entries at all → there is no run to stop. Report and exit.

This is the cheapest correctness check the agent has; do not skip it because the user said "just kill it".

## Step 3 — Branch on leaf vs. parent

### Step 3a — Parent block: dispatch to children

**Rule (non-negotiable, mirrors `/root:run`):** when `TARGET_DIR` has entries under `meta_info.subblocks`, this skill's job is *orchestration only*. It MUST invoke each child block's `/<child>:stop` skill. It MUST NOT enumerate or signal child processes directly, MUST NOT `ssh` into a child's remote host, MUST NOT touch a child's `artifacts/index.yaml`.

Procedure at a parent:

1. Determine which children are actually running. A child is "running" iff its `subblock/<name>/artifacts/index.yaml` newest entry has `status: running`. Children with no running entry are skipped (do not call their `:stop` — there is nothing for them to do, and calling produces noisy confirmations).
2. Stop running children in **reverse** dependency order (a child whose output is consumed downstream must outlive its consumers — stop consumers first). Break ties by reverse declaration order.
3. For each child, invoke `/<child>:stop` and wait for it to return. Surface the child's confirmation prompt to the user verbatim. If the user aborts at any child, stop dispatching further children and report which children were already stopped.
4. The parent itself has no processes to signal — its only role is dispatch.

### Step 3b — Leaf block: enumerate candidate processes

Continue to Step 4.

## Step 4 — Enumerate candidate processes (leaf block)

Identify processes attributable to this block. Use **all** of the heuristics below; deduplicate by PID; do not stop at the first hit.

| # | Source | How |
| - | ------ | --- |
| 1 | `<TARGET_DIR>/config.yaml.status.pid` (or any block-specific field documented in the block's `CLAUDE.md`, e.g. RL's `parent_pid`) | Read it. If the PID exists and its `ps -p <pid> -o cmd=` matches the recorded launch command, it is a confirmed match. |
| 2 | tmux session named after the block | Local: `tmux list-sessions -F '#{session_name}'` and match `meta_info.name`; remote: same command over SSH. Record the session and its panes. |
| 3 | Process tree rooted at `scripts/start.sh` for this block | `ps -eo pid,ppid,cmd` and find any process whose `cmd` contains the absolute path to `<TARGET_DIR>/scripts/start.sh`, plus all descendants via PPID walk. |
| 4 | Per-block sidecars declared in `meta_info` or the block's `CLAUDE.md` (LiteLLM proxy port, vLLM port, Harbor job dirs, dashboard server, Docker containers, K8s pods) | For each documented sidecar, look it up by its declared port / container name / pod label. Anything found whose lifetime is bound to this block goes in the candidate set. Sidecars whose lifetime is **not** bound to this block (e.g. a shared host-level LiteLLM proxy) do **not** go in the candidate set — note them separately for the user. |

If the candidate set is empty after all four heuristics, report that and exit. **Do not** fall back to `pkill -f <block_name>` or any pattern-based mass kill — false positives there are unrecoverable.

## Step 5 — Confirm with the user

Print a table:

```
PID    PPID    Started              CMD                                                 Source
12345  1       2026-06-03T08:00:00  bash <TARGET_DIR>/scripts/start.sh                  start.sh-tree
12346  12345  2026-06-03T08:00:00  python -m harbor ...                                 start.sh-tree
12350  1       2026-06-03T08:00:01  litellm --config .../litellm.yaml --port 8002       sidecar(litellm)
```

Plus the tmux sessions to be killed and any sidecars (Docker containers / K8s pods) that will be stopped.

Ask: "Send SIGTERM to all of the above? (y/N)". Wait for a literal affirmative. Anything else aborts.

If the user asks to exclude specific PIDs, propose the reduced set and re-confirm. Never proceed on partial confirmation.

## Step 6 — Signal

Default policy (overridable by the user only after explicit ack):

1. Send `SIGTERM` (signal 15) to every confirmed PID and to every tmux pane (`tmux send-keys -t <pane> C-c` followed by `kill-session` after the grace period).
2. Wait the configured grace period — default **30 seconds**, or whatever the block's `CLAUDE.md` specifies. During the wait, poll `kill -0 <pid>` every 2 s; processes that exit early are dropped from the watchlist.
3. For any PID still alive after the grace period: ask the user once — "PID `<pid>` (`<cmd>`) did not exit after SIGTERM. Send SIGKILL? (y/N)". On `y`, send `SIGKILL`. On anything else, leave it and report.
4. For Docker containers, prefer `docker stop --time=<grace>` (sends SIGTERM, escalates to SIGKILL after the timeout). For K8s pods, `kubectl delete pod --grace-period=<grace>`.

Remote execution (`meta_info.resources.ip` is a real IP): every signal in this step is issued **inside the remote tmux session over SSH**. Do not kill any local process — there are none attributable to this block.

## Step 7 — Verify the EXIT trap fired

Re-read `<TARGET_DIR>/artifacts/index.yaml`. The newest entry should now have `status: interrupted` and a `completed_at` timestamp, because `start.sh`'s EXIT trap should have invoked `archive_run.sh` on SIGTERM/SIGINT.

- If it is updated correctly → done. Move to Step 8.
- If the newest entry is still `status: running` → the trap did not fire (force-kill of the wrapper, no trap installed, archive_run.sh missing). The agent must close the entry manually: append a one-line note to the existing entry (or, if the schema forbids in-place edits, append a new entry referencing the same archive path) with `status: interrupted`, `completed_at: <now>`, and `notes: "stop via /root:stop — EXIT trap did not fire"`. Do not invent metadata you don't have.

`config.yaml` itself is one-shot per run — do not edit it to record the stop. Live state lives in `artifacts/index.yaml`.

## Step 8 — Report

Print a short summary (under 10 lines): block stopped, PIDs signaled, which exited on SIGTERM vs. SIGKILL, tmux sessions killed, sidecars stopped, whether the EXIT trap fired, and the current newest entry of `artifacts/index.yaml`. If any candidate process refused to die (e.g. uninterruptible sleep, kernel-level wait), say so explicitly — never claim a process is dead without verifying `kill -0` returns non-zero.
