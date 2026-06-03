---
name: check
description: >
  RL-specific preflight for the rl block in the current working directory.
  Detects deployment topology (single vs multi-node, k8s vs docker agent
  backend) from config.yaml and only runs the checks that apply. Wraps
  scripts/dryrun.sh and adds: agent backend reachability, host port
  conflicts on ray/dashboard/litellm, venv editable-install path
  verification, submodule HEAD vs config.yaml-pinned commits, running-job
  sanity (if status.phase is 'running'), and live LiteLLM/vLLM health if
  a run is in flight. Read-only — never edits config.yaml, never runs
  start.sh. Triggers on phrases like "check the rl block", "preflight
  rl", "is rl ready to launch", "diagnose rl", "validate the rl config",
  "sanity check rl before launch".
---

# /rl:check

Validate the rl block end-to-end. Topology-aware: skips checks that
don't apply to the configured deployment. Reports every issue in one
consolidated message at the end. Read-only.

## Step 0 — Orient

**Prerequisites:**
1. `./config.yaml` exists and `meta_info.name == 'rl'`. Otherwise abort:
   `"/rl:check must be run from inside the rl block (subblock/rl/)."`
2. `./scripts/dryrun.sh` exists and is executable.

Read `./config.yaml` and `./CLAUDE.md` for context — do NOT echo them.

**Topology detection** — parse from `config.yaml` and use to decide
which steps apply:

- `NNODES` ← `runtime_info.input.infrastructure.nnodes`
- `BACKEND` ← parse `runtime_info.input.harbor_agent.environment_import_path`:
  contains `kubernetes` → `k8s`; contains `docker` → `docker`; else
  `unknown`.
- `LIVE_RUN` ← `status.phase == 'running'`.

Print this line at the top of the Step 7 report:
```
topology: nnodes=<N>, backend=<k8s|docker|unknown>, status=<phase>
```

## Checklist

Checks run in order; collect every failure, never abort early.

Severity: `required` ✗ blocks `/rl:run`; `advisory` ✗ shows in the
report but doesn't block. Warnings (`⚠`) and skips (`·`) never block.
If a step's **Applies when** predicate is false, it emits
`· step-skipped:<reason>` and contributes nothing to totals.

| # | Step                         | Severity                                                  | Applies when                                                                |
| - | ---------------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------- |
| 1 | `scripts/dryrun.sh` baseline | required                                                  | always                                                                      |
| 2 | Agent backend reachability   | required                                                  | `BACKEND ∈ {k8s, docker}` (else warn `backend:unknown`)                     |
| 3 | Host port conflicts (head)   | required                                                  | always (multi-node: head only)                                              |
| 4 | Venv editable-install paths  | required (default venv) / advisory (custom `venv_path`)   | `$VENV/bin/python` exists (default: warn `not-yet-bootstrapped`; custom: missing is hard ✗) |
| 5 | Submodule pin verification   | required per repo with `pinned_commit`                    | repo has a `pinned_commit` (else `branch-tracking` info only)               |
| 6 | Running-job sanity           | advisory                                                  | `LIVE_RUN` is true                                                          |
| 7 | Report                       | n/a                                                       | always                                                                      |

Each step below follows the same shape: **Goal · Severity · Applies when
· Why · Command · Outcomes · Notes**.

---

### Step 1 — `scripts/dryrun.sh` baseline

**Goal:** Capture baseline checks (config schema, repo presence, paths,
GPU, KV-head divisibility) without aborting on the first failure.

**Severity:** required. **Applies when:** always.

**Why:** `dryrun.sh` catches failures that crash training within seconds
and waste a 10–20 min vLLM warmup cycle — missing `model_path` (vLLM
fails on weight load), missing parquet indexes (verl exits at first
batch), `vllm.gen_tp` not dividing `num_key_value_heads` (CUDA illegal
memory access), `WANDB_API_KEY` unset (wandb init aborts mid-training,
run orphaned).

**Command:**
```bash
bash ./scripts/dryrun.sh 2>&1; echo "EXIT=$?"
```

**Parse:** lines starting with `  MISSING ` or `  EMPTY ` are failures.
Closing line `[rl/dryrun] Done. ok=N missing=M` gives totals.

**Outcomes:**
- ✓ `dryrun:ok` — exit 0 and `missing=0`.
- ✗ `dryrun:<label>` — one entry per `MISSING`/`EMPTY` line; copy
  label and path verbatim.

**Notes:** Exit code is informational; never abort here. `dryrun.sh`
is the authoritative source for basic file/path checks — this skill
does not re-implement them. `dryrun.sh` also prints a "Run Configuration
Summary" block at the end showing key parameters (model, data, backend,
parallelism, batch size, algorithm, etc.) — always include this in the
Step 7 report so the user sees what they're about to run.

---

### Step 2 — Agent backend reachability

**Goal:** Confirm the backend that executes Harbor trials (k8s cluster
or Docker daemon) is actually reachable, not just configured.

**Severity:** required. **Applies when:** `BACKEND ∈ {k8s, docker}`;
otherwise emit `⚠ backend:unknown` (include the literal
`environment_import_path`) and skip.

**Why:** Trials run in a backend-managed sandbox. An unreachable
backend means the agent loop fails on its first call — but the failure
surfaces ~5–10 min into the run, after vLLM has warmed up. File
presence checks (kubeconfig exists, `docker` binary exists) hide it:
VPN dropped, control-plane down, cert expired, daemon socket down.

**Command (`BACKEND == k8s`):**
```bash
KUBECONFIG="<runtime_info.input.k8s.kubeconfig>" \
  kubectl get nodes -o wide --request-timeout=10s
```
Outcomes:
- ✓ `k8s:reachable` — exit 0 AND ≥1 node `Ready`.
- ✗ `k8s:no-ready-nodes` — exit 0 but zero `Ready` (include table).
- ✗ `k8s:unreachable` — non-zero exit / timeout (include stderr).
- ⚠ `k8s:kubectl-missing` — pod-side uses cluster-internal kubectl, so
  warning only.

**Command (`BACKEND == docker`):**

Resolve `DOCKER_HOST_CFG` from `runtime_info.input.harbor_agent.docker_host`:
- Empty or unset → local mode (Docker defaults to `unix:///var/run/docker.sock`)
- `unix:///path/to/sock` → explicit local socket
- `tcp://<ip>:<port>` → remote Docker daemon

For **local** (empty or `unix://`):
```bash
docker info --format '{{.ServerVersion}} {{.OperatingSystem}}' 2>&1
```

For **remote** (`tcp://`):
```bash
DOCKER_HOST="<docker_host_cfg>" docker info --format '{{.ServerVersion}} {{.OperatingSystem}}' 2>&1
```

Outcomes:
- ✓ `docker:reachable` — exit 0, server version printed.
- ✗ `docker:unreachable` — non-zero exit (include stderr; usually
  "Cannot connect to the Docker daemon").
- ⚠ `docker:cli-missing` — `docker` not installed on this host.
- ⚠ `docker:insecure-port` — remote `tcp://<ip>:2375` (unencrypted,
  root-equivalent). Suggest TLS on `:2376`.

**Docker SDK check (required when `BACKEND == docker`):**

Harbor's `RemoteDockerEnvironment` / `DockerEnvironment` uses the Python
`docker` SDK (not the CLI) to manage containers. The SDK must be importable
from the venv and able to connect to the configured daemon.

```bash
"$VENV/bin/python" -c "
from docker import DockerClient
c = DockerClient(base_url='<docker_host_cfg or unix:///var/run/docker.sock>')
print('OK', c.info()['ServerVersion'])
"
```

Outcomes:
- ✓ `docker:sdk-ok` — import + connect succeeded, server version printed.
- ✗ `docker:sdk-missing` — `ImportError` on `from docker import DockerClient`.
  Common cause: `docker` PyPI package not installed, or a directory named
  `docker/` in a repo on `sys.path` shadows it (e.g. `verl/docker/`).
  Suggest: `uv pip install --python $VENV/bin/python docker`.
- ✗ `docker:sdk-connect-failed` — import OK but `DockerClient()` raised
  (include error). Usually daemon unreachable or TLS mismatch.

**Notes:** Backend is derived by matching the substrings `kubernetes`
or `docker` in `environment_import_path`. When `docker_host` is empty
the wrapper does not export `DOCKER_HOST`, so Docker SDK uses the local
socket automatically. Extend the matcher if a new backend lands under a
different module path.

---

### Step 3 — Host port conflicts (head)

**Goal:** Verify ray / dashboard / litellm ports aren't already bound
on the head node.

**Severity:** required. **Applies when:** always. Multi-node caveat:
this checks only the host running `/rl:check`, which must be the head.

**Why:** All three services bind on startup. 6379 occupied → Ray head
fails, verl never boots. 8002 occupied (the common case — orphan
LiteLLM from a prior run) → new proxy silently binds a fallback port,
`claude-code` keeps hitting the dead one, every trial 100% timeouts
with no obvious cause. The dead-LiteLLM symptom is the worst because
the failure looks like an agent problem, not a port problem.

**Ports:** `RAY_PORT` (default 6379), `DASH_PORT` (8265), `LITELLM_PORT`
(8002), each from `runtime_info.input.infrastructure.*`.

**Command (per port `P`):**
```bash
ss -lntp 2>/dev/null | awk -v p=":${P}\$" '$4 ~ p {print}'
# fallback: lsof -nP -iTCP:${P} -sTCP:LISTEN 2>/dev/null
```

**Outcomes (per port):**
- ✓ `port:free:<P>` — no listener.
- ⚠ `port:in-use-by-current-run:<P>` — listener's `comm` matches
  `sync_1node_cc|main_ppo|ray|litellm|vllm`, `LIVE_RUN` is true, and
  walking `ppid` via `ps -o pid,ppid,comm` reaches a
  `sync_1node_cc.sh` PID recorded in `status.current_job.detail`.
- ✗ `port:conflict:<P>` — anything else holding the port. Include
  `comm` and PID; suggest `bash scripts/clean.sh` or `kill <pid>`.

**Notes:** The awk anchor `:${P}$` prevents matching substring ports
(e.g. `63791`). Worker-node Ray ports are dynamic and not checked here.

---

### Step 4 — Venv editable-install paths

**Goal:** Verify the venv's editable installs of `harbor`, `verl`,
`verl_patch`, `harbor_verl_train` resolve to the source trees the user
intends to run.

**Severity:**
- **Default mode** (`runtime_info.input.environment.venv_path` is empty)
  → required. The default venv at `./repos/harbor-verl-train/.venv`
  is built by `setup_env.sh` to install editable from `./repos/`;
  deviation means the venv is corrupted or tampered with.
- **Custom mode** (`venv_path` set) → advisory. We print where each
  module resolves but don't enforce a target; the user owns intent.
  Missing venv or import errors are still hard ✗.

**Applies when:** default mode — `$VENV/bin/python` exists (else warn
`not-yet-bootstrapped`); custom mode — always.

**Why:** This is the worst failure mode in the block: silent
wrong-code execution. A venv whose editable installs point unexpectedly
runs different code than the user thinks. Training runs, vLLM serves,
wandb plots — but the numbers reflect a different commit, with no
obvious failure to point at. In default mode we enforce paths land
under `./repos/`; in custom mode we surface them and let the user
confirm. Recommend the default to anyone who doesn't have a strong
reason to deviate.

**Resolve `$VENV`:** `venv_path` if non-empty (relative to block root)
→ custom mode; else `./repos/harbor-verl-train/.venv` → default mode.

**Command:**
```bash
"$VENV/bin/python" -c "
import harbor, verl, verl_patch, harbor_verl_train, os
block_repos = os.path.realpath('./repos')
for m in (harbor, verl, verl_patch, harbor_verl_train):
    p = os.path.realpath(m.__file__)
    print(m.__name__, p, p.startswith(block_repos))
"
```

**Outcomes — default mode:**
- ✓ `venv:editable` — every module's realpath starts with realpath of
  `./repos/`.
- ✗ `venv:editable-mismatch:<module>` — at least one module outside
  `./repos/`; suggest rerunning `setup_env.sh`.
- ✗ `venv:import-error:<module>` — import raised (include traceback's
  last line).
- ⚠ `venv:not-yet-bootstrapped` — `$VENV/bin/python` missing;
  `setup_env.sh` will create it on first `/rl:run`.

**Outcomes — custom mode:**
- ⚠ `venv:custom-path` — print each module's resolved path; advisory
  only. Suggest in the report: "Leave `venv_path` empty unless you
  have a specific reason."
- ✗ `venv:custom-missing` — `$VENV/bin/python` not found at the
  configured path; run can't start.
- ✗ `venv:import-error:<module>` — import raised.

**Notes:** `realpath` on both sides so a symlinked default venv still
passes default mode as long as the editable installs point back into
`./repos/`.

---

### Step 5 — Submodule pin verification

**Goal:** Each repo under `meta_info.repos` with a `pinned_commit`
must have its submodule HEAD on that commit.

**Severity:** required per repo with `pinned_commit`; info-only for
branch-tracking repos.

**Applies when:** at least one entry under `meta_info.repos` exists.

**Why:** Drift means `config.yaml` documents one commit but the
working tree runs another. For `verl`, `pinned_commit: bcb638649` is
what `patches/verl_bcb638649.patch` was generated against — a drifted
HEAD silently mis-applies the patch or applies it to code that no
longer means the same thing. For `harbor`, drift means the verifier
reward scoring is different from documented. Reproducibility diverges
from the config.

**Command (per repo `R` with `pinned_commit` `C`):**
```bash
git -C ./repos/${R} rev-parse --git-dir >/dev/null 2>&1 || echo "NO_GIT_DIR"
git -C ./repos/${R} rev-parse HEAD
git -C ./repos/${R} symbolic-ref --short -q HEAD 2>/dev/null || echo "DETACHED"
```

**Outcomes (per repo):**
- ✓ `repo:pin-ok:<R>` — HEAD matches `pinned_commit` (full or prefix).
- ✗ `repo:pin-drift:<R>` — HEAD differs (include declared vs actual).
- ✗ `repo:not-initialized:<R>` — `NO_GIT_DIR`; suggest
  `git submodule update --init repos/<R>`.
- ✓ `repo:branch-tracking:<R>` — no `pinned_commit`; report current
  HEAD and branch (or `DETACHED`) without failing.

**Notes:** Prefix match required because `verl` is pinned as
`bcb638649` (9 chars) while `rev-parse HEAD` returns 40 chars.

---

### Step 6 — Running-job sanity

**Goal:** If the block thinks a run is in flight, confirm the recorded
PIDs are alive and the live endpoints are healthy.

**Severity:** advisory. **Applies when:** `LIVE_RUN` is true (else
skip with `· step-skipped:no-live-run`).

**Why advisory:** the information shapes the user's next move, not
the launch gate. `job:status-stale` (PIDs dead but `phase=running`)
is the common aftermath of a crash where no one updated `config.yaml`
— `/rl:run` will overwrite `status` anyway, so this doesn't block;
but the user should read the crash log before relaunching to avoid
re-triggering a deterministic failure. `job:still-running` matters to
`/rl:run`, which has its own gate against concurrent launches.

**Parse PIDs:** scan `status.current_job.detail` (free-text) for
`pid <digits>`.

**Commands:**
```bash
ps -p ${P} -o pid=,comm=,etime=                                    # per PID
curl -sS --max-time 5 http://127.0.0.1:${LITELLM_PORT}/health/liveliness
nvidia-smi --query-gpu=index,utilization.gpu --format=csv,noheader
```

**Outcomes:**
- ✓ `job:still-running:<P>` — PID alive AND `comm` matches
  `sync_1node_cc|train_1node_cc|main_ppo|bash` (include `etime`).
- ⚠ `job:pid-reused:<P>` — alive but unrelated `comm`; OS recycled
  the PID.
- ✗ `job:status-stale` — no PIDs alive but `phase=running`. Suggest:
  inspect `repos/harbor-verl-train/logs/<exp>.log`, then edit
  `status.phase` manually.
- ✓ `live:litellm-up` — curl 2xx.
- ⚠ `live:litellm-not-yet-up` — curl fails, oldest `etime < 30 min`
  (vLLM CUDA-graph capture commonly takes 10–20 min on Qwen3-30B TP=4).
- ✗ `live:litellm-down` — curl fails, oldest `etime ≥ 30 min`.
- `info:gpu-util` — print average + per-GPU; no pass/fail.

**Notes:** Once `/rl:run` writes structured `status.current_job.pids:
[...]`, switch the parser to use it.

---

### Step 7 — Report

Layout:

1. Header: `rl block check (CWD = <relative path>)`
2. Topology line (from Step 0).
3. `scripts/dryrun.sh: ok=N missing=M`, then one line per
   `MISSING`/`EMPTY`.
4. `Required checks:` — one line per outcome from Steps 2–5. Group
   passes into a single ✓ line per category when nothing's wrong.
   Include `· step-skipped:<reason>` rows so the user sees what didn't
   apply.
5. `Advisory checks:` — Step 6 outcomes. Omit this block entirely
   when Step 6 was skipped.
6. `Summary: <Freq> required failures · <Fadv> advisory failures · <W> warnings · <S> skipped · safe-to-run = <YES|NO>`.
   **`YES` iff `Freq == 0`.** Advisory failures, warnings, and skips
   never flip the gate.
7. `Next steps:` — numbered, actionable, one per distinct failure
   (required first). Always end with `Re-run /rl:check.`

**Rules:**
- Use the failure labels verbatim from Steps 1–6.
- For `live:*` / `job:*`, include the underlying number (etime, PID,
  GPU util).
- Quote upstream error messages (kubectl stderr, curl, git) verbatim
  so they're searchable.
- Multi-node deployments: add an italics note after the totals:
  `(multi-node: head-only checks; run /rl:check on each worker for full coverage)`.
- All-green ending: `All checks passed — safe to /rl:run.`
- Required-clean-but-advisory-or-warnings ending:
  `Required checks passed — safe to /rl:run (advisory findings above).`

**Example:**
```
rl block check (CWD = subblock/rl)
topology: nnodes=1, backend=k8s, status=running

scripts/dryrun.sh: ok=12 missing=1
  MISSING  k8s.kubeconfig  (/mnt/ydu/k8s-cpu-221.yaml)

Required checks:
  ✗ k8s:unreachable             ssh: connect timeout to API server (kubeconfig /mnt/ydu/k8s-cpu-221.yaml)
  ✓ port                        6379, 8265 free
  ⚠ port:in-use-by-current-run  8002 held by litellm pid 2710740 (tree → sync_1node_cc.sh pid 2995677)
  ✓ venv:editable               harbor, verl, verl_patch, harbor_verl_train all under repos/
  ✓ repo:pin-ok                 harbor 9f98f9d → 9f98f9d69e37b1c
  ✗ repo:pin-drift:verl         pinned bcb638649 → HEAD a1b2c3def

Advisory checks:
  ⚠ job:status-stale            status.phase=running but no PIDs alive — see logs/harbor-cc-sync-1n-20260515-022150.log

Summary: 2 required failures · 0 advisory failures · 2 warnings · 0 skipped · safe-to-run = NO.

Next steps:
  1. Restore the kubeconfig at /mnt/ydu/k8s-cpu-221.yaml.
  2. Realign the verl submodule: `git -C subblock/rl/repos/verl checkout bcb638649`.
  3. Investigate the stale running state, then edit status.phase manually.
  4. Re-run /rl:check.
```

## What this skill must NOT do

- Never edit `config.yaml`, never flip `status.phase`, never write into `artifacts/`.
- Never call `scripts/start.sh`, `scripts/clean.sh`, or any training script.
- Never run a chat-completion call against LiteLLM — only `/health/liveliness`.
- Never `kill` a process — surface the conflict; the user decides.
- Never substitute env-var values to "satisfy" a missing input (e.g. don't use `$KUBECONFIG` if `runtime_info.input.k8s.kubeconfig` is empty — that's a `dryrun:EMPTY` to surface, not patch).
- Never run worker-side commands over SSH on multi-node deployments — `/rl:check` is local-host only. The report tells the user to run it on each worker.
