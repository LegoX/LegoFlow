---
name: check
description: >
  Preflight the rl block: answer "is it safe to launch training right now?"
  Runs every deterministic check through scripts/dryrun.sh, adds the few
  live checks a script can't judge (is a training job already running? is
  this port conflict ours? are the GPUs held by a foreign process?), and
  always ends with one structured report: a SAFE-TO-RUN verdict, a status
  table, the run configuration, and numbered next steps. Read-only — never
  edits config.yaml, never launches. Triggers on "check rl", "preflight rl",
  "is rl ready to launch", "diagnose rl", "validate the rl config",
  "sanity check rl before launch".
---

# /rl:check — preflight the rl block

Answers one question: **is it safe to launch training right now?**

It runs the deterministic checks via `scripts/dryrun.sh`, adds the handful
of live checks a script can't judge, and prints **one structured report**
ending in a clear YES / NO. Read-only — it never edits config or launches
anything.

## How it works

```
/rl:check
   │
   ├─ 1. dryrun.sh ...... deterministic checks  → OK / FAIL / WARN lines
   │
   ├─ 2. live probes .... only what needs judgment (training already
   │                       running? port mine or stale? GPUs ours or foreign?)
   │
   └─ 3. report ......... verdict + status table + run config + next steps
```

Every check lives in **exactly one** layer:

| Layer | Run by | Covers |
|---|---|---|
| **Deterministic** | `scripts/dryrun.sh` | config schema · repo pins · paths · GPU · KV-head divisibility · backend reachability · docker SDK · venv editable installs · port occupancy |
| **Live (judgment)** | this skill | is a training process already alive? · classify an occupied port (mine vs stale) · GPUs idle / ours / foreign · LiteLLM health when a run is live |

---

## Step 0 — Orient

Run only from inside the rl block. If `./config.yaml` is missing or
`meta_info.name != 'rl'`, or `./scripts/dryrun.sh` is missing → **abort**,
but still print the Step 3 report (heading + a `NO` verdict stating why).

Read `./config.yaml` and `./CLAUDE.md` for context (don't echo them), and
resolve these variables:

| Variable | Source | Default |
|---|---|---|
| `LITELLM_PORT` | `runtime_info.input.infrastructure.litellm_port` | 8002 |
| `NNODES` | `runtime_info.input.infrastructure.nnodes` | 1 |

Live run state is **not** read from `config.yaml` — per
`BLOCK_DEFINITION.md`, `config.yaml` is one-shot configuration and live
state lives in `artifacts/index.yaml` (written by `archive_run.sh` on run
exit) plus the actual process table. Step 2a probes the process table.

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

- `WARN  port <P> in use by <comm> pid <pid>` → hand to **Step 2b**.
- the `Run Configuration Summary` block → copy **verbatim** into the report.

## Step 2 — Live checks

Things `dryrun.sh` can't decide from a static snapshot — they need to know
which processes are *ours*. Judge them here. Three outcomes block launch —
a training run already in flight (`job:running`), a real port conflict
(`port:conflict`), and a foreign job holding the GPUs (`gpu:foreign`);
everything else in this step is advisory.

### 2a — Is a training run already in flight?

A second concurrent run on the same GPUs will OOM or corrupt both. Probe:

```bash
pgrep -af 'sync_1node_cc|train_1node_cc'   # the launch wrappers
pgrep -af 'main_ppo'                       # the verl trainer
```

| Observation | Name | Status |
|---|---|:---:|
| a launcher / trainer process is alive | `job:running` | ✗ blocks |
| nothing matches | `job:none` | ✓ |

On `job:running`, record the PID set (call it the **run tree** — it anchors
the ownership tests in 2b/2c), report PID + `etime`
(`ps -p <pid> -o pid=,etime=`), and tell the user to let it finish or stop
it themselves — never kill it for them. Then run **2d** to report the live
run's health (that's the monitoring value of `/rl:check` during a run).

### 2b — Who owns a busy port?

Input: each `WARN port <P> in use by <comm> pid <pid>` line from Step 1.

| Owner | Name | Status |
|---|---|:---:|
| the current run* | `port:mine` | ⚠ |
| anything else | `port:conflict` | ✗ blocks |

\* *the current run* = 2a found `job:running` **and** `comm` ∈
{`sync_1node_cc`, `main_ppo`, `ray`, `litellm`, `vllm`} **and** the parent
chain (`ps -o ppid= -p <pid>`, repeated) reaches a PID in the run tree.
For `port:conflict`, suggest `bash scripts/clean.sh` or `kill <pid>`.

Port **8002** is the one that bites: a leftover LiteLLM there makes every
trial time out while looking like an agent bug. Always classify it.

### 2c — Are the GPUs free, or busy with another job?

Input: each `WARN gpu in use by pid <pid> (<comm>) <mem>` line from Step 1.

| Owner | Name | Status |
|---|---|:---:|
| the current run* | `gpu:mine` | ⚠ |
| another job (foreign PID) | `gpu:foreign` | ✗ blocks |

Same ownership test as 2b (`*`). A foreign process holding GPU memory means
the run won't get the GPUs it needs — block, and report the PID + memory so
the user can decide whether to wait or stop it. If Step 1 reported
`GPUs idle`, emit `gpu:idle` (✓) and skip the classification.

### 2d — Live-run health  (only when 2a found `job:running`)

```bash
curl -sS --max-time 5 http://127.0.0.1:${LITELLM_PORT}/health/liveliness
```

| Observation | Name | Status |
|---|---|:---:|
| LiteLLM answers | `litellm:up` | ✓ |
| LiteLLM silent, run < 30 min old (vLLM still warming) | `litellm:warming` | ⚠ |
| LiteLLM silent, run ≥ 30 min old | `litellm:down` | ⚠ |

Judge the run's age from the launcher PID's `etime`. On `litellm:down`,
point the user at `repos/harbor-verl-train/logs/<exp>.log`. These rows are
informational — the verdict is already `NO` because of `job:running`.

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
| live | job           | <✓/✗>   | <job:none / job:running pid=<P> etime=<T>> |
| live | ports         | <✓/⚠/✗> | <port:free / port:mine / port:conflict:<P>> |
| live | gpu           | <✓/⚠/✗> | <gpu:idle / gpu:mine / gpu:foreign> |
| live | litellm       | <✓/⚠/·> | <litellm:up / litellm:warming / litellm:down / skip:no-live-run> |

**Run configuration**
```
<paste dryrun.sh's "Run Configuration Summary" block verbatim>
```

**Next steps**
1. <one per failure, required first; quote kubectl/curl/git errors verbatim>
2. ...
Re-run `/rl:check`.
````

**The three invariants:**

1. **Verdict** — `✅ YES` iff `R == 0`, where `R` = dryrun
   `MISSING`/`EMPTY`/`FAIL` count **+** any `job:running` **+** any
   `port:conflict` **+** any `gpu:foreign`. Advisory failures and warnings
   *never* change it.
2. **Glyphs** — `✓` pass · `✗` blocks · `⚠` advisory/warning · `·` skipped.
3. **Collapse** — fold all passing `det` checks into the first row; add a
   row only for each `det` check that is `✗` or `⚠`.

If `NNODES > 1`, add one italic line under the table:
*(multi-node: head-only checks; run `/rl:check` on each worker)*.

---

## Guardrails — never do these

- edit `config.yaml` or write to `artifacts/`
- run `start.sh`, `clean.sh`, or any training script
- re-implement or override a `dryrun.sh` check (add only the live layer)
- modify anything under `repos/` — it is pinned, read-only code
- call LiteLLM for anything beyond `/health/liveliness`
- `kill` a process — surface the conflict, let the user decide
- SSH to worker nodes — `/rl:check` is head-node only

---

## Config reference (moved from config.yaml — do not re-add as comments)

- **docker_host security**: `tcp://<ip>:2375` is the unencrypted Docker daemon port — anyone who can reach it has root-equivalent access to that host. Acceptable only on isolated test networks; production/shared setups need TLS (`tcp://<ip>:2376`, dockerd --tlsverify). `""` = not exported → local unix socket.
- **vllm.gen_tp** must divide the model's `num_key_value_heads` (dryrun validates; e.g. TP=4 matches Qwen3-30B-A3B's 4 KV heads). A mismatch surfaces later as `CUDA error: an illegal memory access`.
- **infrastructure.anthropic_api_key: sk-dummy** is a functional shim for LiteLLM's Anthropic surface (proxied to local vLLM) — it is not a real key and must not be flagged as a placeholder.
