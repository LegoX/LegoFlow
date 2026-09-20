# CI/CD for LegoFlow

Friendly tour of what runs when you push a commit, open a PR, or click
**Run workflow** in the GitHub UI. Read this if you want to understand the
shape of CI before diving into `ci.yml` (which is mechanical detail).

## TL;DR

- **One workflow**: `.github/workflows/ci.yml`. It runs two tiers of tests
  for the **root block** and each **block** (`curator`, `tracer`, `evaluator`,
  `trainer`).
- **Cases** — fast unit-shaped tests under each block's `tests/`. Run when
  that block changes in anything other than `.md`/`.mdx`; documentation-only
  changes still get the always-on root sanity job without waiting for a
  self-hosted runner. Goal: catch regressions in minutes, not hours.
- **Smoke** — end-to-end "actually launch the real pipeline against real
  LLMs / Docker / GPUs for a small fixture". Gated: only runs on pushes to
  `dev`/`main` or when you tick **Also run smoke tests** in the manual
  dispatch UI. Goal: catch integration regressions that a unit pass would
  miss (Docker image drift, LLM endpoint changes, env-mount breakage,
  trainer crashes at real `cutoff_len`, etc.).
- **Self-hosted runners** — most jobs run on our own infra (`legoflow-ci`
  pool) because they need Docker, GPUs, or shared caches. The lone
  cloud job is `Root block sanity` (tiny pytest on `ubuntu-latest`). The
  `trainer` smoke is pinned to the 8-GPU runner (`legoflow-gpu`).
- **Claude SDK drives every smoke launch**. A narrow `claude -p` prompt
  walks through 3 gated phases per block (setup → check → run) and stops
  on the first failure. See [Smoke deep-dive](#smoke-deep-dive-claude-sdk--3-phase-gating).

```
your push / PR / manual dispatch
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ci.yml                                                                 │
│                                                                         │
│  ┌──────────────────┐       ┌──────────────────────────────────────┐   │
│  │ Root block       │       │ Detect changed blocks             │   │
│  │ sanity (cloud)   │       │ (paths-filter on blocks/<b>/**)    │   │
│  └──────────────────┘       └─────────────┬────────────────────────┘   │
│                                            │                            │
│         ┌──────────────────────────────────┴──────────────────┐         │
│         ▼                                                     ▼         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ curator      │  │ tracer     │  │ evaluator        │  │ trainer         │    │
│  │   cases ──┐ │  │   cases ──┐ │  │   cases ──┐ │  │   cases ──┐ │    │
│  │           ▼ │  │           ▼ │  │           ▼ │  │           ▼ │    │
│  │   smoke* ▲  │  │   smoke* ▲  │  │   smoke* ▲  │  │   smoke* ▲  │    │
│  │ (gated)  │  │  │ (gated)  │  │  │ (gated)  │  │  │ (gated, GPU)│    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
│       *smoke jobs only run on dev/main pushes or workflow_dispatch     │
│        with run_smoke=true; each `needs:` its own cases job.            │
└─────────────────────────────────────────────────────────────────────────┘
```

## Repo layout for CI

| Path | What it is |
|---|---|
| `.github/workflows/ci.yml` | The workflow. All job graph, runner pinning, and gating logic. |
| `.github/workflows/ci-from-scratch.yml` | Optional "rebuild everything from cold state" workflow used to validate runner provisioning end-to-end. Not part of normal CI. |
| `.github/scripts/smoke_run.sh` | The smoke launcher. Same script for all 4 blocks; takes `<block> <budget_seconds>`. Drives the claude SDK 3-phase chain. |
| `.github/scripts/register-gpu-runner.sh` | One-shot runner enrollment for the GPU box (`legoflow-gpu` label). Operator script — not invoked by CI. See [Self-hosted runner setup](#self-hosted-runner-setup-operator-only) below. |
| `blocks/<block>/tests/run.sh` | Each block's case runner. Iterates `tests/cases/*.sh`. |
| `blocks/<block>/tests/cases/` | The per-block case scripts (unit-shaped, no real LLM calls except `04_llm_endpoint.sh`). |
| `blocks/<block>/tests/smoke/config.yaml` | Smoke-mode config overlay (schema-compatible with the production `blocks/<block>/config.yaml`). Copied over the production file at the top of each smoke job. |
| `blocks/<block>/tests/smoke/verify.sh` | Smoke verifier — reads on-disk artifacts and exits 0=PASS, 77=SKIP→warning, !=0=FAIL. |
| `tests/test_root_block.py` | Root sanity tests (block tree shape, config schemas). Runs on `ubuntu-latest`. |

## The jobs, one by one

### Root block sanity (cloud)

Cheap pytest on `ubuntu-latest`. Validates the block tree's structural
invariants (block list matches `meta_info.blocks`, configs parse, etc.).
Always runs.

### Detect changed blocks

`dorny/paths-filter` against `blocks/<b>/**` (plus the workflow file itself),
with `.md`/`.mdx` under the block subtracted via `!` patterns. Documentation-only
block changes are handled by the always-on root sanity job and do not reserve a
self-hosted runner.

The `!` patterns only subtract because the step sets
`predicate-quantifier: 'some-with-excludes'` — under the default `some`
quantifier a negated pattern matches every file *outside* the block, which
would make each filter true for any change at all. That quantifier needs
`dorny/paths-filter@v4.0.3` or newer, hence the `@v4` pin.

The filter emits per-block boolean outputs (`curator`, `tracer`,
`evaluator`, `trainer`). Each downstream cases/smoke job has
`if: needs.detect-changes.outputs.<b> == 'true'`, so a curator-only PR
doesn't burn cycles on the other three blocks.

### `<block>` tests (cases)

Runs on `[self-hosted, legoflow-ci]`. Lifecycle:

1. **Pre-checkout cleanup** — a tiny docker-alpine container reclaims any
   root-owned residue left by prior smoke runs (harbor's agent
   containers write `agent/sessions` as `root:700`, which a root-squashed
   `root_squash` can't fix from outside docker).
2. **`actions/checkout`** with `submodules: false, clean: false` — the
   pre-checkout step is responsible for cleanliness; the default
   `clean: true` would re-create dirs on every `EACCES`.
3. **Isolate `HOME` + load shared CI env** — points `HOME` at a runner-temp
   dir (so claude SDK history doesn't leak), then sources
   `$LEGOFLOW_CI_SHARED/.env` to pull in `HF_TOKEN`, cache dirs, etc.
4. **Link runtime state** — symlinks the block's `repos/`, `artifacts/env(s)/`,
   `gh_token.txt`, and any large fixtures from
   `$SHARED_RUNTIME/<block>/` into the per-job workspace. This is why
   `actions/checkout` doesn't need submodules and uv envs don't have to
   be (re)built per job.
5. **Run cases** — `bash tests/run.sh` from the block dir. The runner
   iterates `cases/*.sh`, each exiting 0=PASS / 77=SKIP / !=0=FAIL, and
   prints a `PASS=N SKIP=N FAIL=N` summary. The job fails if FAIL > 0.
6. **Upload logs on failure** — gathers the relevant log dirs into an
   artifact named `<block>-cases-logs-<run-id>`.

### `<block>` tests (smoke)

Same lifecycle as cases, plus:

- Gated by `needs.<block>-cases` (smoke only runs if cases passed) **and**
  one of: `workflow_dispatch run_smoke=true`, or push to `dev`/`main`.
- Adds an **Overlay smoke config** step before launch — copies
  `blocks/<b>/tests/smoke/config.yaml` over the production file so the
  block's scripts read smoke-tuned parameters (smaller PR list, shorter
  timeouts, throwaway dataset keys, etc.).
- **Run smoke** invokes `bash .github/scripts/smoke_run.sh <block> <budget>`.
- **Verify smoke** runs the block's `tests/smoke/verify.sh` afterward;
  exit 77 maps to a yellow `::warning::` (legitimate SKIP — e.g. no GPU
  runner online for trainer, curator smoke PR fixture all errored on Docker
  upstream drift), exit 0 is a green PASS, anything else is RED.
- **Upload logs** — `if: always()`, so you can debug PASS-with-warnings
  too.

The `trainer` smoke is the one job pinned to `[self-hosted, legoflow-gpu]`.
If no GPU runner is online, the job queues rather than route to a CPU box.

## Smoke deep-dive: claude SDK + 3-phase gating

Every smoke uses **the same launcher** (`.github/scripts/smoke_run.sh`).
The launcher reads the overlaid smoke config, then drives a single
`claude -p` invocation per block. Claude executes 3 phases in order via
its Bash tool and stops on the first failure:

| Phase | What it does | Generic command (overridable per block) |
|---|---|---|
| 1. **setup** | "Did the runner's *Link runtime state* step actually materialize this block's env + repos?" | `( test -d artifacts/env \|\| test -d artifacts/envs ) && test -d repos` |
| 2. **check** | Block-level preflight (config schema, endpoints reachable, paths exist) | `bash scripts/dryrun.sh` |
| 3. **run** | Background-launch the long-running smoke under `nohup`. Claude `pgrep`s to confirm the child stayed alive, then exits. | `nohup bash scripts/start.sh >> artifacts/logs/smoke-launch.log 2>&1 &` |

Why this shape:

- **Non-interactive.** The block CLAUDE.mds normally require "check →
  confirm → run" with a human typing "yes". Headless CI can't satisfy
  that. The launcher prompt explicitly says *do not ask for confirmation;
  this is a non-interactive CI run* and passes
  `--dangerously-skip-permissions`.
- **`nohup` survives claude.** `claude -p`'s Bash tool caps at 10 min and
  has no harness callback that lets it wait for a 40-min training run.
  So phase 3 is *always* a `nohup ... &` — claude exits within seconds,
  and a separate bash poller (the WAIT phase in `smoke_run.sh`) watches
  the filesystem for the block's terminal artifact (verifiable_tasks.txt,
  result.json, train_results.json) up to the budget.
- **Gated.** A failure in phase 1 stops the chain — saves time + makes
  the root cause obvious in the runner log ("FAILED 1 <tail>"). Without
  gating, a missing env dir would manifest as a confusing dryrun crash.
- **Per-block budgets.** The workflow passes `<budget_seconds>` per call
  (currently curator 3600 / tracer 2400 / evaluator 2400 / trainer 2700) so a slow
  upstream LLM can be tuned independently per block.

For `trainer` in **remote mode** (when `meta_info.resources.ip` points at the
GPU box rather than `local`), all 3 phases run *on the GPU host* via SSH.
The launcher stages 3 small SSH wrapper scripts under
`artifacts/logs/.smoke-remote-{setup,check,run}.sh` and claude invokes them
in order. The WAIT phase polls the remote host for the terminal artifact
and `scp`s it back so `verify.sh` sees identical paths to the local case.

## How to trigger a smoke run

Three ways:

1. **Push to `dev` or `main`** — all 4 smokes run automatically. (Same for
   merges via PR.)
2. **Manual dispatch** — Actions tab → **CI** → **Run workflow** →
   set **Also run smoke tests** = `true`. Pick the branch. Useful for
   testing a branch's smoke before merging.
3. **`gh workflow run`** from the terminal:
   ```bash
   gh workflow run ci.yml --ref <your-branch> -f run_smoke=true
   ```

The cases tier runs on **every** push/PR regardless of the smoke gate.

## Concurrency, timeouts, retries

- `concurrency: ci-${{ github.ref }}` with `cancel-in-progress: true` —
  a new push to a branch cancels its older still-running CI.
- Per-job `timeout-minutes`: cases jobs at 30 min (network-filesystem cold-cache cases
  can take ~7 min), smoke jobs at 70 min (trainer is the long pole due to
  remote training).
- **No automatic retries.** A FAIL is a FAIL. If you suspect a flake (the
  upstream LLM endpoint times out fairly often on `cases/04_llm_endpoint.sh`),
  re-dispatch via the UI rather than wiring in opaque retries.

## Running these locally

Cases jobs are designed to be runnable outside CI — they're just
`bash tests/run.sh` from a block dir. They expect the same shared
env/runtime layout though, so use a self-hosted-runner-like setup:

```bash
cd blocks/curator
bash tests/run.sh
```

For smokes, mimic the workflow steps by hand (overlay the smoke config,
then run `bash .github/scripts/smoke_run.sh curator 3600` from the repo
root). The launcher works the same way locally as on a runner — claude
will drive the same 3 phases.

## Self-hosted runner setup (operator-only)

This section is for whoever provisions the runner hosts. End contributors
don't need to read it — clicking *Run workflow* in the UI Just Works
once the runners are up.

### Runner label tiers

| Label | Runs | Where |
|---|---|---|
| `legoflow-ci` | every cheap/CPU + Docker job (cases, curator/tracer/evaluator smokes) | the generic CI host(s) |
| `legoflow-gpu` | **only** the `trainer-smoke` job — real 8-GPU DeepSpeed ZeRO-3 training | the GPU machine (8× L20X) |

### Why `trainer-smoke` is special

`trainer-smoke` is the **one training job in CI**. It launches full-parameter
Qwen3-8B training at `cutoff_len=131072` across 8 GPUs (see
`blocks/trainer/tests/smoke/10_train_demo.sh`). Every other CI job is CPU/Docker
work that any `legoflow-ci` runner can take. The training smoke must land on
the host that actually has the 8 GPUs, so its `runs-on` in `ci.yml` is pinned
to `[self-hosted, legoflow-gpu]` — a label that **only the GPU machine's
runner carries**. If no GPU runner is online the job queues (gated to
`workflow_dispatch run_smoke=true` or pushes to `dev`/`main`) rather than risk
running on a CPU-only runner.

### Registering the GPU runner

Run `.github/scripts/register-gpu-runner.sh` **on the GPU host itself** (so
the runner binds to that machine and sees its GPUs). Get a registration
token from `repo Settings → Actions → Runners → New self-hosted runner`,
or:

```bash
gh api -X POST "repos/<owner>/<repository>/actions/runners/registration-token" -q .token
```

Then on the GPU host:

```bash
REG_TOKEN=<token> bash .github/scripts/register-gpu-runner.sh
```

It registers a runner with labels `legoflow-ci,legoflow-gpu` (so the box also
serves the generic pool) and a name derived from the host. Start it with
`./run.sh` in the runner dir, or install it as a service
(`sudo ./svc.sh install && sudo ./svc.sh start`).

To confirm the label is live: the runner appears under repo Settings →
Actions → Runners with a `legoflow-gpu` label, and a manual *Run workflow*
(Actions → CI → Run workflow, *Also run smoke tests* = true) dispatches
`trainer-smoke` to it.

### Host-side files for self-hosted runners

Configure the `LEGOFLOW_CI_SHARED`, `LEGOFLOW_SHARED_RUNTIME`, and optional
`LEGOFLOW_CLI_HOME` repository variables for each runner pool. The referenced
files are operator-managed and are never tracked in this repository.

| Path relative to `$LEGOFLOW_CI_SHARED` | Purpose |
|---|---|
| `.env` | Shared CI environment, including cache locations and runtime-only tokens. Keep it mode `0600`. |
| `gh_token.txt` | GitHub PATs for private submodules and the PR collector. |
| `uv/bin/` | Shared `uv` installation added to `PATH`. |
| `runtime/<block>/` | Runtime mirror containing repositories, environments, and large fixtures. |
| `sync_runtime.sh` | Operator-provided command that refreshes the runtime mirror after dependency pin changes. |

## See also

- [`.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md`](../../.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md) — the block-system contract these jobs operate against.
- [`CLAUDE.md`](../../CLAUDE.md) — root block agent contract; lists blocks and the input/output wiring this CI exercises.
