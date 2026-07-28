# tracer CI tests

Drift detection (cheap, ~30 s) plus an optional end-to-end smoke run
(~30 min) for the tracer block. Calibrated to this repo's fixed CI runner.
For portable user-environment diagnostics, use the `/tracer:check` skill instead.

---

## Quickstart

```bash
# cheap path — every test except the smoke. Safe to run anywhere:
bash subblock/tracer/tests/run.sh

# full path — adds the 30-min, real-LLM, real-Docker smoke. Self-hosted only:
bash subblock/tracer/tests/run.sh --with-smoke
# equivalent:  TESTS_WITH_SMOKE=1 bash subblock/tracer/tests/run.sh
```

Per-test exit codes: `0` pass · `77` skip · anything else fail.
`run.sh` returns non-zero iff at least one test failed; skips never fail the suite.

---

## What gets checked

| # | Test | What it asserts | Time |
|---|---|---|---|
| 01 | config schema | every required key in `config.yaml` is set and `task_source.provider ∈ {local, huggingface}` | <1 s |
| 02 | repo pins | `repos/harbor` and `repos/swe_data_process` at their pinned commits, origins match, worktrees clean | <2 s |
| 03 | uv envs | three envs exist; `harbor` editable from `repos/harbor`, `litellm` importable, `swe_data_process` importable (SKIP when `sft_conversion.enabled=false`) | ~1 min |
| 04 | LLM endpoint | `GET ${api_base_url}/models` returns 200 and the configured model is in `data[].id` | ~1 s |
| 05 | HF dataset | `huggingface.co/api/datasets/<name>` reachable with the configured token (SKIP for `local` provider) | <1 s |
| 06 | LiteLLM port | port from `litellm_proxy.port` is free, or held by a process the current uid owns | <1 s |
| 07 | runtime image | `docker image inspect ${agent.runtime_image}` exits 0 (pre-pulled) | <1 s |
| 08 | processed-tasks ledger | ledger parses, every `done`/`failed`/`skipped` entry is also in `HARBOR_EXCLUDE_TASKS` | <1 s |
| 09 | dashboard regressions | public/no-sample exports omit full trajectories, latest archived run is shown, R2 reads every JSONL shard, smoke config matches production | <1 s |
| 10 | **10-HF-task demo** *(smoke)* | swap config + `start.sh` against 10 HF tasks; ≥1 trial resolves within 30 min | up to 30 min |

The 10-HF-task demo runs only with `--with-smoke` and is gated to `push`
events on `dev`/`main` and manual `workflow_dispatch` runs
(`run_smoke=true`) in CI — never to PRs.

---

## When something fails

| You see | Most likely cause | What to do |
|---|---|---|
| 02: `.git missing` | a repo wasn't checked out yet | `bash scripts/update_repos.sh --repo <name>` |
| 02: `HEAD does not match commit` | someone fetched a different commit | `bash scripts/update_repos.sh --repo <name>` |
| 02: origin URL mismatch | runner's `~/.gitconfig` has `url.X.insteadOf` rules that rewrite `git remote get-url` output | clean `~/.gitconfig`: `git config --global --remove-section 'url.…'` |
| 02: worktree has local modifications | manual edits under `repos/` (forbidden — see memo `feedback-no-edits-under-repos`) | revert and put fixes in `scripts/` or `config.yaml` |
| 03: `harbor: import not from repos/harbor` | uv env hosts a pip-installed `harbor` shadowing the editable install | `rm -rf artifacts/env/harbor-uv && bash scripts/setup_harbor_env.sh` |
| 03: `missing_env:HARBOR_EDITABLE_ROOT` | test invoked the editable check without exporting `HARBOR_EDITABLE_ROOT` | bug in the test wrapper, not the env — file a fix |
| 04: HTTP 401 | bad/expired `llm_api.api_key` | rotate the key in `config.yaml` |
| 04: model not in catalog | `llm_api.model` doesn't match what upstream serves | update the model in `config.yaml` or with a per-job override |
| 05: HF 401/403 | stale or missing HF token | refresh `~/.cache/huggingface/token` (or `HF_TOKEN` env) |
| 05: HF 404 | typo in `task_source.dataset_name` | fix in `config.yaml` |
| 06: port held by another uid | someone else is on `litellm_proxy.port` | change the port, or kill the foreign process |
| 07: image not pulled | runner's daemon lost the image | `docker pull docker.io/jierun/c-cc-2.1.118:v0.1` |
| 08: ledger task not in `HARBOR_EXCLUDE_TASKS` | done/failed/skipped tasks would re-execute next run | add them to `runtime_info.input.env_extra.HARBOR_EXCLUDE_TASKS` in `config.yaml` |
| 10: budget hit, no resolved trial | model regression, network slowness, or 10 unusually hard tasks | inspect `artifacts/jobs/smoke/<job>/*/result.json` for verifier output |

---

## Layout

```
cases/                         cheap deterministic checks
  01_config_schema.sh
  02_repo_pins.sh
  03_uv_envs_editable.sh
  04_llm_endpoint.sh
  05_hf_dataset.sh
  06_litellm_port.sh
  07_runtime_image.sh
  09_dashboard_regressions.sh
smoke/                         expensive end-to-end runs (--with-smoke gates them)
  10_hf_task_demo.sh
run.sh                         aggregator
```

---

## Per-test reference

Skip this section unless you're debugging a specific case or about to change one.

<details>
<summary><code>cases/01_config_schema.sh</code> — config.yaml shape</summary>

Parses `config.yaml` with PyYAML and asserts every key the tracer runtime
contract depends on is non-empty: `meta_info.name == "tracer"`,
`meta_info.repositories.{harbor, swe_data_process}.{url, commit, path, readonly}`,
`meta_info.environment.{harbor_uv, litellm_uv, swe_data_process_uv}`,
`runtime_info.input.llm_api.{api_key, api_base_url, model}`,
`runtime_info.input.litellm_proxy.{port, master_key}`,
`runtime_info.input.task_source.{provider, dataset_name}`,
`runtime_info.input.harbor_job.{jobs_dir, n_concurrent, max_retries, timeout_multiplier}`,
`runtime_info.input.agent.{name, version, runtime_image, max_turns}`,
`runtime_info.input.sft_conversion.{enabled, tokenizer_name}`. Also enforces
`task_source.provider ∈ {local, huggingface}`.
</details>

<details>
<summary><code>cases/02_repo_pins.sh</code> — pinned commits + clean worktrees</summary>

For each of `repos/harbor` and `repos/swe_data_process`: `.git` exists,
`git remote get-url origin` matches `meta_info.repositories.<name>.url`,
`git rev-parse HEAD` matches the pin, and `git status --porcelain` is empty.
Enforces the "vendored + read-only" contract from `BLOCK_DEFINITION.md §1.5`.
</details>

<details>
<summary><code>cases/03_uv_envs_editable.sh</code> — three envs + expected installs</summary>

For each env, dir exists and python is executable; then:
**harbor uv** runs `scripts/check_harbor_editable.py` with
`HARBOR_EDITABLE_ROOT` set, pass iff `harbor.__file__` resolves under
`repos/harbor/src/`. **litellm venv**: `python -c "import litellm"`.
**swe_data_process uv**: `python -c "import swe_data_process"`, downgraded
to SKIP when `sft_conversion.enabled=false`. ~1 min, dominated by harbor's
import time.
</details>

<details>
<summary><code>cases/04_llm_endpoint.sh</code> — endpoint reachable + model present</summary>

`GET ${api_base_url}/models` with `Authorization: Bearer ${api_key}` and
`User-Agent: curl/8.5.0` (dodges the CF UA filter that 403s
`Python-urllib/*`). Asserts 200 and that the configured model — stripped of
any `openai/` litellm provider prefix — appears in `data[].id`. Cloudflare
`502/503/52x/530` responses are retried up to three times to absorb brief edge
transitions. A persistent response identified by the `Server: cloudflare`
header SKIPs this external-health check; the same status from another server,
or an authentication error, still FAILs.
</details>

<details>
<summary><code>cases/05_hf_dataset.sh</code> — HF dataset reachable</summary>

When `task_source.provider == "huggingface"`, `GET
huggingface.co/api/datasets/<dataset_name>` with the HF token (loaded from
`HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, or
`${HF_HOME:-~/.cache/huggingface}/token`). Otherwise SKIPs. Catches
gated-repo failures before `prepare_tasks.sh` hits them.
</details>

<details>
<summary><code>cases/06_litellm_port.sh</code> — proxy port available</summary>

Tries `ss -ltnp` first. Free → PASS. Held by current uid (visible in
`users:` field) → PASS (treated as "our previous proxy still up"). Held by
another uid → FAIL. Falls back to a Python `socket.bind` check when `ss`
isn't on PATH.
</details>

<details>
<summary><code>cases/07_runtime_image.sh</code> — agent runtime pre-pulled</summary>

`docker image inspect ${agent.runtime_image}` exits 0 against the
configured `DOCKER_HOST`. CI must not pay a multi-GB pull mid-job.
</details>

<details>
<summary><code>smoke/10_hf_task_demo.sh</code> — end-to-end 10-HF-task demo</summary>

Swaps `config.yaml` for a smoke variant
(`harbor_job.jobs_dir=artifacts/jobs/smoke`, `n_tasks=10`, `n_concurrent=2`,
`max_retries=0`, `agent.max_turns=80`, `sft_conversion.enabled=false`), runs
`scripts/start.sh` against the configured HF dataset, restores `config.yaml`
on any exit path via an `EXIT` trap. Wrapped in `timeout --foreground 1800`.
Passes when at least one trial under
`artifacts/jobs/smoke/<job>/<trial>/result.json` has a positive value in
`verifier_result.rewards`.
</details>
