# trajgen CI tests

CI gate for the `trajgen` block. Calibrated to **this repo's fixed CI runner**
(specific uv env paths, configured `DOCKER_HOST`, pinned Harbor and
swe_data_process commits, configured LiteLLM proxy port, pre-pulled agent
runtime image). These tests are NOT a drop-in replacement for the portable
`/trajgen:check` skill, which tolerates arbitrary user environments.

## Layout

```
cases/                          cheap deterministic checks (mirror /trajgen:check)
  01_config_schema.sh
  02_repo_pins.sh
  03_uv_envs_editable.sh
  04_llm_endpoint.sh
  05_hf_dataset.sh              (SKIP when provider != huggingface)
  06_litellm_port.sh
  07_runtime_image.sh
  08_consumption_ledger.sh
smoke/                          end-to-end runs that cost real LLM tokens + Docker
  10_hf_task_demo.sh
run.sh                          aggregator
```

## Invocation

```bash
# Cheap path (cases/ only) — safe for cloud CI, runs in ~30s:
bash subblock/trajgen/tests/run.sh

# Full path (cases/ + smoke/) — ~30 min, real LLM + Docker; self-hosted only:
bash subblock/trajgen/tests/run.sh --with-smoke
# or:  TESTS_WITH_SMOKE=1 bash subblock/trajgen/tests/run.sh
```

Each test exits `0` for pass, `77` for skip, anything else for fail. `run.sh`
returns non-zero iff any test failed. SKIP does not fail the suite.

## Test cases

### `cases/01_config_schema.sh` — config.yaml shape

Parses `config.yaml` with PyYAML and asserts every key the trajgen runtime
contract depends on is non-empty: `meta_info.name == "trajgen"`,
`meta_info.repositories.{harbor,swe_data_process}.{url,commit,path,readonly}`,
`meta_info.environment.{harbor_uv,litellm_uv,swe_data_process_uv}`,
`runtime_info.input.llm_api.{api_key,api_base_url,model}`,
`runtime_info.input.litellm_proxy.{port,master_key}`,
`runtime_info.input.task_source.{provider,dataset_name}`,
`runtime_info.input.harbor_job.{jobs_dir,n_concurrent,max_retries,timeout_multiplier}`,
`runtime_info.input.agent.{name,version,runtime_image,max_turns}`,
`runtime_info.input.sft_conversion.enabled`. Also enforces
`task_source.provider ∈ {local, huggingface}`.

**Fails when** any required key is missing, blank, or `task_source.provider`
is an unsupported value. Pure-Python check — no I/O, no shell. <1 s.

### `cases/02_repo_pins.sh` — pinned commits + clean worktrees

For each of `repos/harbor` and `repos/swe_data_process`:

1. `repos/<name>/.git` exists (file gitlink OR directory).
2. `git remote get-url origin` matches `meta_info.repositories.<name>.url`.
3. `git rev-parse HEAD` matches `meta_info.repositories.<name>.commit`.
4. `git status --porcelain` is empty (no local modifications).

**Fails when** either repo is missing, on the wrong commit, or has dirty
local edits — i.e. the runtime environment has drifted from the pin. <2 s.
Enforces the "vendored + read-only" contract from `BLOCK_DEFINITION.md §1.5`
(per the `feedback-no-edits-under-repos` memo).

### `cases/03_uv_envs_editable.sh` — three uv envs + expected editable installs

Checks all three managed environments. For each, the env directory exists
and its python is executable; then:

- **harbor uv** (`artifacts/env/harbor-uv`): runs `scripts/check_harbor_editable.py`
  with `HARBOR_EDITABLE_ROOT=repos/harbor` and `UV_PROJECT_ENVIRONMENT` set
  to the env. Pass if `harbor.__file__` resolves under `repos/harbor/src/`
  (i.e. the editable install actually points at the pinned source tree).
- **litellm venv** (`artifacts/env/litellm-venv`): `python -c "import litellm"` works.
- **swe_data_process uv** (`artifacts/env/swe-data-process-uv`):
  `python -c "import swe_data_process"` works. Downgraded to **SKIP** if
  `runtime_info.input.sft_conversion.enabled` is false (the env isn't on the
  critical path for trajectory generation in that case).

**Fails when** an env is missing entirely, the editable install for harbor
escaped to a system-installed package (would happen after a `pip install
harbor` mishap), or required packages aren't importable. ~1 min — dominated
by harbor's import time.

### `cases/04_llm_endpoint.sh` — LLM endpoint reachable + model present

Issues `GET ${api_base_url}/models` with `Authorization: Bearer ${api_key}`
and `User-Agent: curl/8.5.0` (the latter dodges the CF UA filter that
sometimes 403s the default `Python-urllib/*` UA). Asserts 200 status and
that the configured model (stripped of any `openai/` litellm provider
prefix) appears in `data[].id`.

**Fails when** the endpoint is down, credentials are rejected, or the
configured model isn't served by the upstream. Light probe (no chat
completion — that path is exercised by the smoke test). ~1 s.

### `cases/05_hf_dataset.sh` — HuggingFace dataset reachable

When `task_source.provider == "huggingface"`, issues
`GET https://huggingface.co/api/datasets/<dataset_name>` with the HF token
(loaded from `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, or
`${HF_HOME:-~/.cache/huggingface}/token`). Otherwise **SKIPs**.

**Fails when** the dataset 401/403s (bad/missing token), 404s (typo or
deleted/renamed), or the HF API is unreachable. <1 s. Catches gated-repo
auth issues *before* `prepare_tasks.sh` tries `snapshot_download` mid-run.

### `cases/06_litellm_port.sh` — proxy port available

Looks up `runtime_info.input.litellm_proxy.port` and tries `ss -ltnp` first.
If the port is free → PASS. If it's held by a process the current uid owns
(visible in `ss`'s `users:` field) → PASS (treated as "our previous run's
proxy that's still up — start.sh will reuse the slot"). If it's held by
another uid → FAIL. Falls back to a Python `socket.bind` check when `ss`
isn't on PATH.

**Fails when** another user (or a foreign system service) is sitting on the
port and `start.sh` would fail to bind. <1 s.

### `cases/07_runtime_image.sh` — agent runtime image pre-pulled

Confirms `docker image inspect ${agent.runtime_image}` exits 0 against the
configured `DOCKER_HOST`. CI must not pay a multi-GB pull mid-job; the
runner is expected to have warmed the image once via
`docker pull <runtime_image>`.

**Fails when** the daemon is unreachable, the CLI is missing, or the image
hasn't been pulled. <1 s.

### `cases/08_consumption_ledger.sh` — ledger ↔ HARBOR_EXCLUDE_TASKS

Parses `artifacts/consumption_ledger.yaml` and enforces two invariants:

1. Every entry under `runs[]` has `status ∈ {pending, running, done, failed, skipped}`.
2. Every entry whose status is `done`, `failed`, or `skipped` has its
   `task_id` listed in `environment.extra.HARBOR_EXCLUDE_TASKS`. If not,
   Harbor would re-execute already-consumed tasks on the next run.

**Fails when** an entry has an invalid status, or any terminal-state task is
missing from the exclude list. Lists up to 10 offenders so you can patch
the config before re-running. <1 s.

### `smoke/10_hf_task_demo.sh` — end-to-end 10-HF-task demo

Swaps `config.yaml` for a smoke variant (`harbor_job.jobs_dir=artifacts/jobs-smoke`,
`n_tasks=10`, `n_concurrent=2`, `max_retries=0`, `agent.max_turns=40`,
`sft_conversion.enabled=false`), then runs `scripts/start.sh` against the
HF dataset already declared in `runtime_info.input.task_source`
(`SWE-Lego/swerebenchv2-200-260429`). Harbor selects the first 10 tasks
deterministically from the dataset snapshot. Wrapped in `timeout
--foreground 1800` for a 30-minute hard wall-clock budget. The original
`config.yaml` is restored from backup via an `EXIT` trap on any exit path
(including SIGINT/SIGTERM).

**Passes when** at least one trial under `artifacts/jobs-smoke/<job>/<trial>/result.json`
has a `verifier_result.rewards` mapping containing a positive value — i.e.
at least one trajectory resolved correctly. **Fails when** the budget hits
without any resolved trajectory, or `start.sh` returns non-zero with zero
resolutions. Per-trial counts (`resolved/total`) are emitted as INFO regardless.

Burns real LLM tokens + Docker time. Only fires when the test runner is
invoked with `--with-smoke` (or `TESTS_WITH_SMOKE=1`). The GitHub Actions
workflow gates this to `push` events on `dev`/`main` and to
`workflow_dispatch` runs with `run_smoke=true`, never to PRs.
