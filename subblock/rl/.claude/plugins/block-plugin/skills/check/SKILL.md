---
name: check
description: >
  Preflight the rl block: answer "is it safe to launch training right now?"
  Runs every deterministic check through scripts/dryrun.sh, adds the few
  live checks a script can't judge (is this port conflict ours? is the
  running job actually alive?), and always ends with one structured report:
  a SAFE-TO-RUN verdict, a status table, the run configuration, and numbered
  next steps. Read-only — never edits config.yaml, never launches. Triggers
  on "check the rl block", "preflight rl", "is rl ready to launch",
  "diagnose rl", "validate the rl config", "sanity check rl before launch".
---

# /block:check — preflight the rl block

Answers one question: **is it safe to launch training right now?**

It runs the deterministic checks via `scripts/dryrun.sh`, adds the handful
of live checks a script can't judge, and prints **one structured report**
ending in a clear YES / NO. Read-only — it never edits config or launches
anything.

## How it works

```
/block:check
   │
   ├─ 1. dryrun.sh ...... deterministic checks  → OK / FAIL / WARN lines
   │
   ├─ 2. live probes .... only what needs judgment (port mine or stale?
   │                       is the "running" job actually alive?)
   │
   └─ 3. report ......... verdict + status table + run config + next steps
```

Every check lives in **exactly one** layer:

| Layer | Run by | Covers |
|---|---|---|
| **Deterministic** | `scripts/dryrun.sh` | config schema · repo pins · paths · GPU · KV-head divisibility · backend reachability · docker SDK · venv editable installs · port occupancy |
| **Live (judgment)** | this skill | classify an occupied port (mine vs stale) · running-job + LiteLLM health when `phase: running` |

---

## Step 0 — Orient

Run only from inside the rl block. If `./config.yaml` is missing or
`meta_info.name != 'rl'`, or `./scripts/dryrun.sh` is missing → **abort**,
but still print the Step 3 report (heading + a `NO` verdict stating why).

Read these from `config.yaml` (don't echo the file):

| Variable | Source | Default |
|---|---|---|
| `LIVE_RUN` | `status.phase == 'running'` | — |
| `JOB_DETAIL` | `status.current_job.detail` (free text, holds `pid <N>`) | — |
| `LITELLM_PORT` | `runtime_info.input.infrastructure.litellm_port` | 8002 |
| `NNODES` | `runtime_info.input.infrastructure.nnodes` | 1 |

## Step 1 — Deterministic checks

```bash
bash ./scripts/dryrun.sh 2>&1; echo "EXIT=$?"
```

`dryrun.sh` prints one line per check. Read them as-is — never re-run a probe
or overrule an `OK`. The line prefix is the status:

| Prefix | Status | Blocks launch? |
|---|:---:|:---:|
| `OK` | pass | — |
| `MISSING` / `EMPTY` / `FAIL` | fail | **yes** |
| `WARN` | warning | no |

Two kinds of line need follow-up:

- `WARN  port <P> in use by <comm> pid <pid>` → hand to **Step 2a**.
- the `Run Configuration Summary` block → copy **verbatim** into the report.

## Step 2 — Live checks

Things `dryrun.sh` can't decide from a static snapshot — they need to know
which processes are *ours*. Judge them here. Two outcomes block launch — a
real port conflict (`port:conflict`) and a foreign job holding the GPUs
(`gpu:foreign`); everything else in this step is advisory.

### 2a — Who owns a busy port?

Input: each `WARN port <P> in use by <comm> pid <pid>` line from Step 1.

| Owner | Name | Status |
|---|---|:---:|
| the current run* | `port:mine` | ⚠ |
| anything else | `port:conflict` | ✗ blocks |

\* *the current run* = `LIVE_RUN` is true **and** `comm` ∈ {`sync_1node_cc`,
`main_ppo`, `ray`, `litellm`, `vllm`} **and** the parent chain
(`ps -o ppid= -p <pid>`, repeated) reaches a PID named in `JOB_DETAIL`.
For `port:conflict`, suggest `bash scripts/clean.sh` or `kill <pid>`.

Port **8002** is the one that bites: a leftover LiteLLM there makes every
trial time out while looking like an agent bug. Always classify it.

### 2b — Is the job actually running?

Only when `LIVE_RUN`. Read the PIDs from `JOB_DETAIL`, then probe:

```bash
ps -p <pid> -o pid=,comm=,etime=
curl -sS --max-time 5 http://127.0.0.1:${LITELLM_PORT}/health/liveliness
```

| Observation | Name | Status |
|---|---|:---:|
| launcher PID alive (`sync_1node_cc` / `main_ppo` / `bash`) | `job:running` | ✓ |
| PID alive but unrelated process (OS recycled it) | `job:pid-reused` | ⚠ |
| no recorded PID alive, yet `phase: running` | `job:stale` | ⚠ |
| LiteLLM answers | `litellm:up` | ✓ |
| LiteLLM silent, run < 30 min old (vLLM still warming) | `litellm:warming` | ⚠ |
| LiteLLM silent, run ≥ 30 min old | `litellm:down` | ⚠ |

On `job:stale`, tell the user to read
`repos/harbor-verl-train/logs/<exp>.log` before relaunching (`/block:run`
overwrites `status` anyway).

### 2c — Are the GPUs free, or busy with another job?

Input: each `WARN gpu in use by pid <pid> (<comm>) <mem>` line from Step 1.

| Owner | Name | Status |
|---|---|:---:|
| the current run* | `gpu:mine` | ⚠ |
| another job (foreign PID) | `gpu:foreign` | ✗ blocks |

Same ownership test as 2a (`*`). A foreign process holding GPU memory means
the run won't get the GPUs it needs — block, and report the PID + memory so
the user can decide whether to wait or stop it. If Step 1 reported
`GPUs idle`, emit `gpu:idle` (✓) and skip the classification.

---

## Step 3 — The report  (always the last thing you print)

The report **is** the deliverable. Print it every single time — even on an
abort (then: heading + a `NO` verdict whose reason is the abort message,
nothing else). Fill this template exactly; drop only truly inapplicable rows.

````
## rl block check — CWD=<relative path>

**SAFE TO RUN: <✅ YES | ❌ NO>** — <R> required · <A> advisory · <W> warnings

| Layer | Check | Status | Detail |
|-------|-------|:------:|--------|
| det  | config · repos · pins · paths · syntax · perms · GPU-count · disk · backend · docker-SDK · KV-head | ✓ | ok=<N> |
| det  | venv:editable | <✓/✗/⚠> | <verbatim detail> |
| det  | wandb         | <✓/✗>   | <verbatim detail> |
| det  | cache         | <✓/⚠>   | <none / leftover ray·litellm·trials present> |
| live | ports         | <✓/⚠/✗> | <port:free / port:mine / port:conflict:<P>> |
| live | gpu           | <✓/⚠/✗> | <gpu:idle / gpu:mine / gpu:foreign> |
| live | job           | <✓/⚠/·> | <job:running / job:stale / skip:no-live-run> |
| live | litellm       | <✓/⚠/·> | <litellm:up / litellm:warming / litellm:down / skip> |

**Run configuration**
```
<paste dryrun.sh's "Run Configuration Summary" block verbatim>
```

**Next steps**
1. <one per failure, required first; quote kubectl/curl/git errors verbatim>
2. ...
Re-run `/block:check`.
````

**The three invariants:**

1. **Verdict** — `✅ YES` iff `R == 0`, where `R` = dryrun
   `MISSING`/`EMPTY`/`FAIL` count **+** any `port:conflict` **+** any
   `gpu:foreign`. Advisory failures and warnings *never* change it.
2. **Glyphs** — `✓` pass · `✗` blocks · `⚠` advisory/warning · `·` skipped.
3. **Collapse** — fold all passing `det` checks into the first row; add a
   row only for each `det` check that is `✗` or `⚠`.

If `NNODES > 1`, add one italic line under the table:
*(multi-node: head-only checks; run `/block:check` on each worker)*.

---

## Guardrails — never do these

- edit `config.yaml`, flip `status.phase`, or write to `artifacts/`
- run `start.sh`, `clean.sh`, or any training script
- re-implement or override a `dryrun.sh` check (add only the live layer)
- call LiteLLM for anything beyond `/health/liveliness`
- `kill` a process — surface the conflict, let the user decide
- SSH to worker nodes — `/block:check` is head-node only
