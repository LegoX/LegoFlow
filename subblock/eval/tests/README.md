# eval CI tests

Drift detection (cheap, ~10 s) plus an optional end-to-end smoke run
(~40 min, 10 tasks) for the eval block. Calibrated to this repo's fixed CI runner.
For portable user-environment diagnostics, use the `/eval:check` skill instead.

---

## Quickstart

```bash
# cheap path — every test except the smoke. Safe to run anywhere:
bash subblock/eval/tests/run.sh

# full path — adds the 40-min, real-LLM, real-Docker smoke. Self-hosted only:
bash subblock/eval/tests/run.sh --with-smoke
# equivalent:  TESTS_WITH_SMOKE=1 bash subblock/eval/tests/run.sh
```

Per-test exit codes: `0` pass · `77` skip · anything else fail.
`run.sh` returns non-zero iff at least one test failed; skips never fail the suite.

---

## What gets checked

| # | Test | What it asserts | Time |
|---|---|---|---|
| 01 | config schema | every required key in `config.yaml` is set and `task_source.provider == harbor_registry`; `jobs_dir` under `artifacts/jobs`; `n_tasks` is null or a positive int | <1 s |
| 02 | repo pin | `repos/harbor` at its pinned commit, origin matches, worktree clean | <2 s |
| 03 | uv envs | both envs exist; `harbor` editable from `repos/harbor`; `litellm` CLI present **and** installed version == the pin (`1.83.14`) | ~1 s |
| 04 | LLM endpoint | best-effort `GET ${api_base_url}/models`; 200 + model present → PASS, 200 + model absent → FAIL, CF-class code / unreachable → **SKIP** | ~1 s |
| 05 | LiteLLM port | port from `litellm_proxy.port` is free, or held by a process the current uid owns | <1 s |
| 06 | runtime image | `docker image inspect ${agent.runtime_image}` exits 0 (pre-pulled) | <1 s |
| 07 | registry dataset | `(task_source.dataset_name, version)` resolves in `repos/harbor/registry.json` and expands to ≥1 task | <1 s |
| 08 | agent runtime | `agent.runtime_host_path` exists, is non-empty, and holds the per-agent marker (`bin/claude` / `runtime-env.sh` / `bin/opencode`) | <1 s |
| 10 | **registry-task demo** *(smoke)* | swap config + `start.sh` against 10 registry tasks (first 10 of the configured dataset); ≥1 trial reaches a terminal **clean scored** state within 40 min | up to 40 min |

The registry-task demo runs only with `--with-smoke` and is gated to `push`
events on `dev`/`main` and manual `workflow_dispatch` runs (`run_smoke=true`)
in CI — never to PRs. The smoke runs the **first `n_tasks` of the configured
`task_source.dataset_name`** (currently `n_tasks=10`, `n_concurrent=5`), so it
validates whatever benchmark you're about to launch.

**Case 04 is `/models`-only by design.** The cheap `cases/` path must not spend
LLM tokens, so case 04 only lists the model catalog — which can return 200 from a
local proxy even when completions 502 against a dead origin. The real
**completion-based launch gate** (`scripts/probe_llm_completion.sh` — a 1-token
chat completion that classifies origin health: 200→PASS, 5xx/timeout→FAIL,
401/403 or 400/404→WARN) lives in `/eval:check` and is exercised for real by the
smoke. See memory `project-eval-llm-endpoint`.

### How eval differs from trajgen's suite

eval is the **registry-driven, terminal** block, so the suite drops three
trajgen cases and adds two:

- **No `swe_data_process` env** (case 03 checks 2 envs, not 3) — eval does not
  convert trajectories.
- **No HF-dataset / consumption-ledger cases** — eval stages no tasks locally;
  case 07 (registry resolution) replaces trajgen's HF reachability check.
- **Case 08 (agent runtime)** is eval-specific: the bind-mounted agent runtime
  is the single most common cause of a green preflight that still produces zero
  usable trajectories, so it gets its own case.
- **Smoke pass condition is a pipeline signal, not a model-quality gate.**
  trajgen's smoke requires ≥1 *resolved* trajectory (its product). eval's
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
| 02: `.git missing` | harbor wasn't checked out yet | `bash scripts/update_repos.sh` (or `/eval:setup`) |
| 02: `HEAD does not match commit` | someone fetched a different commit | `bash scripts/update_repos.sh` |
| 02: origin URL mismatch | runner's `~/.gitconfig` has `url.X.insteadOf` rules that rewrite `git remote get-url` output (the SSH↔https rewrite used by from-scratch CI) | clean `~/.gitconfig`: `git config --global --remove-section 'url.…'`, or run on a HOME-isolated job |
| 02: worktree has local modifications | manual edits under `repos/` (forbidden — see memo `feedback-no-edits-under-repos`) | revert and put fixes in `scripts/` or `config.yaml` |
| 03: `harbor: import not from repos/harbor` | uv env hosts a pip-installed `harbor` shadowing the editable install | `rm -rf artifacts/env/harbor-uv` and rebuild via `/eval:setup` |
| 03: `litellm installed X != pinned 1.83.14` | venv built against the wrong litellm | rebuild the litellm venv via `/eval:setup` |
| 04: `FAIL: configured model … not in /models catalog` | endpoint serves a different model than `llm_api.model` | fix the model in `config.yaml` |
| 04: SKIP (502/401/unreachable) | Cloudflare-gating / off-node shell — expected, real reachability is proxy-mediated | nothing; the smoke exercises the real path. See memory `project-eval-llm-endpoint` |
| 05: port held by another uid | someone else is on `litellm_proxy.port` | change the port, or kill the foreign process |
| 06: image not pulled | runner's daemon lost the image | `docker pull <agent.runtime_image>` |
| 07: `not in registry.json` | typo in `dataset_name`/`version`, or stale registry | fix `task_source` against the curated table in `CLAUDE.md`, or `bash scripts/update_repos.sh` |
| 08: `missing marker` / `empty` | agent runtime not extracted into `runtime_host_path` | re-extract via `/eval:setup` (`docker create … && docker cp …`) |
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
smoke/                         expensive end-to-end runs (--with-smoke gates them)
  10_registry_task_demo.sh
run.sh                         aggregator
```
