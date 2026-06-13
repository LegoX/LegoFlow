# swegen CI tests

CI gate for the `swegen` block. Calibrated to **this repo's fixed CI runner**
(specific venv paths, configured `DOCKER_HOST`, `gh_token.txt` on disk, the
`artifacts/envs/swegen-env` layout). These tests are NOT a drop-in replacement
for the portable `/swegen:check` skill, which tolerates arbitrary user
environments.

## Layout

```
cases/                 cheap deterministic checks (mirror /swegen:check)
  01_config_schema.sh
  02_repo_pin.sh
  03_github_tokens.sh
  04_llm_endpoint.sh
  05_docker_daemon.sh
  06_harbor_smoke.sh   (SKIP if py-cc/tox-dev__tox-3813 not present)
smoke/                 end-to-end runs that cost real LLM tokens + Docker time
  10_pr_demo.sh
  fixtures/python_pr_ids.txt
run.sh                 aggregator
```

## Invocation

```bash
# Cheap path (cases/ only) — safe for cloud CI, runs in ~30s:
bash subblock/swegen/tests/run.sh

# Full path (cases/ + smoke/) — ~30 min, real LLM + Docker; self-hosted only:
bash subblock/swegen/tests/run.sh --with-smoke
# or:  TESTS_WITH_SMOKE=1 bash subblock/swegen/tests/run.sh
```

Each test exits `0` for pass, `77` for skip, anything else for fail. `run.sh`
returns non-zero iff any test failed. SKIP does not fail the suite.

## Test cases

### `cases/01_config_schema.sh` — config.yaml shape

Parses `config.yaml` with PyYAML and asserts every key the runtime contract
depends on is present and non-empty:
`meta_info.name == "swegen"`, `meta_info.environment.{venv_path,requirements}`,
`meta_info.repos.swegen`, `runtime_info.input.github_tokens`,
`runtime_info.input.llm_api.{api_key,api_base_url,pr_model,task_model}`,
`runtime_info.output.swe_tasks_dir.path`.

**Fails when** a required key is missing, blank, or `meta_info.name` drifts
from the directory name. Pure-Python check — no I/O, no shell. <1 s.

### `cases/02_repo_pin.sh` — submodule pin + venv editable install

Three things in one:

1. `repos/swegen/.git` exists (file or directory — submodule gitlinks are
   files, plain clones are directories).
2. If `meta_info.repos.swegen.commit_id` is non-null, `git rev-parse HEAD`
   on `repos/swegen/` matches that pin. A `null` pin is interpreted as
   "track latest" and the pin check is skipped (an INFO line is emitted).
3. `artifacts/envs/swegen-env/bin/python -c "import swegen"` succeeds.

**Fails when** the submodule isn't checked out (`/swegen:setup` never ran on
this machine), the working commit drifts from the pin, or the editable
install was never done. <1 s.

### `cases/03_github_tokens.sh` — every GitHub token authenticates

Sources `scripts/load_runtime_env.sh` to hydrate `GITHUB_TOKENS` from env,
`gh_token.txt`, or `~/gh_token.txt` (same precedence the swegen collector
uses). Splits by comma and issues
`GET https://api.github.com/rate_limit` per token with the matching
`Authorization: token …` header.

**Fails when** any token returns ≠ 200 or no token is found at all. Reports
the token's last six characters on failure so you can identify which one
expired. ~1 s per token. Catches expired PATs, commented-out tokens
(per `feedback-swegen-gh-token-comments` memo), and unusable revoked
credentials before the collector hits them at scale.

### `cases/04_llm_endpoint.sh` — LLM endpoint completes a real request

Runs inside `artifacts/envs/swegen-env`, with
`PYTHONPATH=repos/swegen/src` so it can `from swegen.llm_env import …`.
Calls `hydrate_cross_provider_env()`, then `get_openai_compatible_config()`
(which mirrors `OPENAI_*` → `ANTHROPIC_*` so the Claude SDK works), then
issues `client.chat.completions.create(model=…, max_tokens=16)`.

**Fails when** the cross-provider hydration is misconfigured (e.g. mismatched
`OPENAI_MODEL` vs `ANTHROPIC_MODEL`), the endpoint is unreachable,
credentials are rejected, or the configured model isn't served. Light token
spend (~16 output tokens). ~20 s typical. Catches the failure mode where
`/models` would pass but the actual chat call doesn't.

### `cases/05_docker_daemon.sh` — Docker socket reachable

Asserts `docker` is on `PATH` and `docker info` exits 0 with
`DOCKER_HOST=unix:///var/run/docker.sock` (which the test exports so the
result doesn't depend on the runner shell's env). Reports the server
version on success.

**Fails when** the daemon is down, the socket isn't readable, or the runner
shell has `DOCKER_HOST` pointing at a stale/unreachable socket (per
`feedback-swegen-state-dir-convention`-adjacent learnings about Harbor's
default Podman socket). <1 s.

### `cases/06_harbor_smoke.sh` — known verified task still passes Harbor

If `artifacts/swe_tasks/py-cc/tox-dev__tox-3813/` exists locally, runs
`swegen validate … --task tox-dev__tox-3813 --env docker` and asserts the
output contains both `NOP reward=0` and `Oracle reward=1`. Otherwise
**SKIPs** (exit 77) — there's no point asserting Harbor health without a
known-good fixture.

**Fails when** Harbor's NOP/Oracle execution produces different rewards
(usually means the Docker stack regressed, not that the task changed).
~2–5 min when it runs.

### `smoke/10_pr_demo.sh` — end-to-end 10-PR demo

Runs `swegen create --max-pr 1 --no-require-issue --min-source-files 1`
against the 10 Python PRs in `fixtures/python_pr_ids.txt` (the quick-verify
set with `tox-dev/tox:pr-3813` first). Output lands in
`artifacts/swe_tasks/py-cc-smoke/`. Wrapped in `timeout --foreground 1800`
for a hard 30-minute wall-clock budget.

**Passes when** `artifacts/swe_tasks/py-cc-smoke/verifiable_tasks.txt`
contains at least one task ID (i.e. NOP=0 + Oracle=1 verified) before the
budget expires. **Fails when** the timeout hits without a verified task, or
the CLI exits non-zero without producing the manifest. On failure, prints
the last 40 lines of `artifacts/swe_tasks/.swegen-smoke-py.log`.

Burns real LLM tokens + Docker build time. Only fires when the test runner
is invoked with `--with-smoke` (or `TESTS_WITH_SMOKE=1`). The GitHub
Actions workflow gates this to `push` events on `dev`/`main` and to
`workflow_dispatch` runs with `run_smoke=true`, never to PRs.
