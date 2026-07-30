# evaluator CI tests

Drift detection (typically under 40 s, no rollout tokens) plus an optional end-to-end smoke run
(~40 min, 10 tasks) for the evaluator block. Calibrated to this repo's fixed CI runner.
For portable user-environment diagnostics, use the `/evaluator:check` skill instead.

---

## Quickstart

```bash
# cheap path — no rollout/token spend, but host readiness checks can fail:
bash subblock/evaluator/tests/run.sh

# full path — adds the 40-min, real-LLM, real-Docker smoke. Self-hosted only:
bash subblock/evaluator/tests/run.sh --with-smoke
# equivalent:  TESTS_WITH_SMOKE=1 bash subblock/evaluator/tests/run.sh
```

Per-test exit codes: `0` pass · `77` skip · anything else fail.
`run.sh` returns non-zero iff at least one test failed; skips never fail the suite.

---

## What gets checked

| # | Test | What it asserts | Time |
|---|---|---|---|
| 01 | config schema | every required key in `config.yaml` is set and `task_source.provider == harbor_registry`; `jobs_dir` under `artifacts/jobs`; `n_tasks` is null or a positive int | <1 s |
| 02 | repo pin | `repos/harbor` at its pinned commit, origin matches, worktree clean, and `update_repos.sh --ref` overrides the pin | <2 s |
| 03 | uv envs | Harbor Python/imports/editable CLI health plus LiteLLM CLI and pinned version (`1.83.14`) | <30 s cold |
| 04 | LLM endpoint | `GET ${api_base_url}/models`; model present → PASS; remote CF-gating → SKIP; local auth/network/5xx or model mismatch → FAIL | ~1 s |
| 05 | LiteLLM port | port from `litellm_proxy.port` is free (any existing listener blocks a new launch) | <1 s |
| 06 | runtime image | `docker image inspect ${agent.runtime_image}` exits 0 (pre-pulled) | <1 s |
| 07 | registry dataset | `(task_source.dataset_name, version)` resolves in `repos/harbor/registry.json` and expands to ≥1 task | <1 s |
| 08 | agent runtime | `agent.runtime_host_path` contains the per-agent marker and executable (`bin/claude`, `bin/python`, or `bin/opencode`) | <1 s |
| 09 | completion launch gate | mock valid/malformed/5xx/local-auth responses plus configured-gateway auth warning | <1 s |
| 10 | start contract | source-level guard for isolated config, `dataset@version`, relative model paths, and dryrun → completion probe → proxy ordering | <1 s |
| 11 | script utilities | unsafe clean rejection plus isolated/concurrent `archive_run.sh` metadata/index behavior | <5 s |
| 12 | smoke verifier | fixtures for missing/stale output, clean scored trial, and scored trial with exception | <1 s |
| 13 | smoke contract | fixture pin/image/path/port alignment, temporary config, and safe container cleanup guard | <1 s |
| S10 | **registry-task demo** *(smoke)* | isolated `EVAL_CONFIG` + `start.sh` against 10 registry tasks (first 10 of the configured dataset); ≥1 trial reaches a terminal **clean scored** state within 40 min | up to 40 min |

The registry-task demo runs only with `--with-smoke` and is gated to `push`
events on `dev`/`main` and manual `workflow_dispatch` runs (`run_smoke=true`)
in CI — never to PRs. The smoke runs the **first `n_tasks` of the configured
`task_source.dataset_name`** (currently `n_tasks=10`, `n_concurrent=5`), so it
validates whatever benchmark you're about to launch.

**Case 04 is `/models`-only by design.** The cheap `cases/` path must not spend
LLM tokens, so case 04 only lists the model catalog — which can return 200 from a
local proxy even when completions 502 against a dead origin. The real
**completion-based launch gate** (`scripts/probe_llm_completion.sh` — a minimal
chat completion that classifies origin health: valid 200→PASS, 5xx/timeout or
local 401/403→FAIL, CF-gated remote 401/403 or app-level 400/404→WARN) lives in
`/evaluator:check`, is enforced by `start.sh`, and is exercised for real by the smoke.

### How evaluator differs from tracer's suite

evaluator is the **registry-driven, terminal** block, so the suite drops the local
task-staging checks and adds eval-specific coverage:

- **No `swe_data_process` env** (case 03 checks 2 envs, not 3) — evaluator does not
  convert trajectories.
- **No HF-dataset / processed-tasks cases** — evaluator stages no tasks locally;
  case 07 (registry resolution) replaces tracer's HF reachability check.
- **Case 08 (agent runtime)** is evaluator-specific: the bind-mounted agent runtime
  is the single most common cause of a green preflight that still produces zero
  usable trajectories, so it gets its own case.
- **Case 09 (completion gate)** uses a local mock server to verify valid,
  malformed, auth-failure, and 5xx responses without spending model tokens.
- **Smoke pass condition is a pipeline signal, not a model-quality gate.**
  tracer's smoke requires ≥1 *resolved* trajectory (its product). evaluator's
  product is a *scored* result, so the smoke passes when ≥1 trial is
  **clean scored** — the verifier recorded a reward (0 or 1) **and** that trial
  did *not* raise an agent exception. The "clean" qualifier matters: a broken
  agent (e.g. missing runtime bind-mount → in-container install 403s → nonzero
  exit) still leaves the repo unchanged and the verifier still records reward 0,
  so a "reward exists" gate would mask exactly the failure case 08 guards
  against. Resolved (reward>0) and errored counts are reported but do not gate —
  at n=10 a capable model can legitimately solve 0 SWE-bench tasks without the
  pipeline being broken. The scan reads the live `stats.evals` (reward_stats −
  exception_stats), so a timeout-killed run is still scored from finished trials.

---

## When something fails

| You see | Most likely cause | What to do |
|---|---|---|
| 02: `.git missing` | harbor wasn't checked out yet | `bash scripts/update_repos.sh` (or `/evaluator:setup`) |
| 02: `HEAD does not match commit` | someone fetched a different commit | `bash scripts/update_repos.sh` |
| 02: origin URL mismatch | the checkout points at a genuinely different repository (SSH and HTTPS transports for the same GitHub repo are treated as equivalent) | correct the remote or reinitialize via `/evaluator:setup` |
| 02: worktree has local modifications | manual edits under `repos/` (forbidden — see memo `feedback-no-edits-under-repos`) | revert and put fixes in `scripts/` or `config.yaml` |
| 03: `harbor: import not from repos/harbor` | uv env hosts a pip-installed `harbor` shadowing the editable install | `rm -rf artifacts/env/harbor-uv` and rebuild via `/evaluator:setup` |
| 03: `litellm installed X != pinned 1.83.14` | venv built against the wrong litellm | rebuild the litellm venv via `/evaluator:setup` |
| 04: `FAIL: configured model … not in /models catalog` | endpoint serves a different model than `llm_api.model` | fix the model in `config.yaml` |
| 04: remote endpoint SKIP (502/401/unreachable) | Cloudflare-gating from this shell | re-probe on the evaluator node; `start.sh` still enforces the real completion gate |
| 04: local endpoint auth/network failure | vLLM is down/unreachable or its API key differs | restart/fix vLLM on the GPU node |
| 05: port already has a listener | another eval/proxy is on `litellm_proxy.port` | stop the owning run or choose another configured port |
| 06: image not pulled | runner's daemon lost the image | `docker pull <agent.runtime_image>` |
| 07: `not in registry.json` | typo in `dataset_name`/`version`, or stale registry | fix `task_source` against the curated table in `CLAUDE.md`, or `bash scripts/update_repos.sh` |
| 08: `missing marker` / `empty` | agent runtime not extracted into `runtime_host_path` | re-extract via `/evaluator:setup` (`docker create … && docker cp …`) |
| 10: 0 clean scored trials | dead upstream origin (5xx — run `scripts/probe_llm_completion.sh` first), agent install failed in-container, or every trial errored | inspect `artifacts/jobs/smoke/<job>/result.json` (`stats.evals[*].exception_stats`) and the smoke log |

---

## Layout

```
cases/                         cheap deterministic checks
  01_config_schema.sh
  02_repo_pin.sh
  03_uv_envs_editable.sh
  04_llm_endpoint.sh
  05_litellm_port.sh
  06_runtime_image.sh
  07_registry_dataset.sh
  08_agent_runtime.sh
  09_probe_completion.sh
  10_start_contract.sh
  11_script_utilities.sh
  12_smoke_verifier.sh
  13_smoke_contract.sh
smoke/                         expensive end-to-end runs (--with-smoke gates them)
  config.yaml
  10_registry_task_demo.sh
  verify.sh
run.sh                         aggregator
```
