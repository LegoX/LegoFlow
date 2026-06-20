# curator CI tests

Drift detection (cheap, ~30 s) plus an optional end-to-end smoke run
(~60 min) for the curator block. Calibrated to this repo's fixed CI runner.
For portable user-environment diagnostics, use the `/curator:check` skill instead.

---

## Quickstart

```bash
# cheap path — every test except the smoke. Safe to run anywhere:
bash subblock/curator/tests/run.sh

# full path — adds the 60-min, real-LLM, real-Docker smoke. Self-hosted only:
bash subblock/curator/tests/run.sh --with-smoke
# equivalent:  TESTS_WITH_SMOKE=1 bash subblock/curator/tests/run.sh
```

Per-test exit codes: `0` pass · `77` skip · anything else fail.
`run.sh` returns non-zero iff at least one test failed; skips never fail the suite.

---

## What gets checked

| # | Test | What it asserts | Time |
|---|---|---|---|
| 01 | config schema | every required key in `config.yaml` is set and `meta_info.name == "curator"` | <1 s |
| 02 | repo pin + venv | `repos/swegen` is at the pinned commit and `swegen` imports inside the venv | <1 s |
| 03 | GitHub tokens | every entry in `GITHUB_TOKENS` / `gh_token.txt` returns 200 from `/rate_limit` | ~1 s/token |
| 04 | LLM endpoint | cross-provider hydration succeeds and a real `chat.completions.create` returns content | ~20 s |
| 05 | Docker | daemon reachable via configured `DOCKER_HOST` | <1 s |
| 06 | Harbor smoke | known verified task `tox-dev__tox-3813` still produces NOP=0, Oracle=1 (SKIPs if fixture absent) | ~2–5 min |
| 10 | **10-PR demo** *(smoke)* | `swegen create --max-pr 1` over the 10-PR quick-verify fixture produces ≥1 verified task within 60 min | up to 60 min |

The 10-PR demo runs only with `--with-smoke` and is gated to `push` events on
`dev`/`main` and manual `workflow_dispatch` runs (`run_smoke=true`) in CI —
never to PRs, so PR builds don't burn LLM tokens.

---

## When something fails

| You see | Most likely cause | What to do |
|---|---|---|
| 02: `.git missing` | `/curator:setup` never ran on this machine | run `/curator:setup` |
| 02: `HEAD does not match commit_id` | someone fetched a different commit into `repos/swegen` | `git -C repos/swegen checkout <pin>` |
| 02: `swegen not importable` | venv exists but editable install was never done | `pip install -e repos/swegen/` inside `artifacts/envs/swegen-env` |
| 03: HTTP 401 on a token | PAT expired or was revoked | regenerate the PAT, replace the line in `gh_token.txt` |
| 03: token file has comment headers, all tokens fail | comments aren't stripped by the collector (per memo `feedback-swegen-gh-token-comments`) | remove `#` lines from `gh_token.txt` |
| 04: `chat.completions … raised` | `OPENAI_MODEL` / `ANTHROPIC_MODEL` resolve to different providers | check `config.yaml` `runtime_info.input.llm_api` — both should match the same backend |
| 04: 401 from Claude shell only | sandboxed-shell artifact, not a real cred bug (memo `project-swegen-llm-endpoint`) | re-probe from a non-sandboxed shell |
| 05: `docker info failed` | daemon down or `DOCKER_HOST` stale | `export DOCKER_HOST=unix:///var/run/docker.sock` |
| 06: SKIPped | the `tox-dev__tox-3813` fixture isn't on disk | not a failure; either ignore, or run `/curator:run` once to materialise the fixture |
| 10: budget hit, no verified task | LLM/Docker stall, or the candidate PRs got harder | tail `artifacts/swe_tasks/.swegen-smoke-py.log` — usually identifies the slow step |

---

## Layout

```
cases/                cheap deterministic checks
  01_config_schema.sh
  02_repo_pin.sh
  03_github_tokens.sh
  04_llm_endpoint.sh
  05_docker_daemon.sh
  06_harbor_smoke.sh
smoke/                expensive end-to-end runs (--with-smoke gates them)
  10_pr_demo.sh
  fixtures/python_pr_ids.txt
run.sh                aggregator
```

---

## Per-test reference

Skip this section unless you're debugging a specific case or about to change one.

<details>
<summary><code>cases/01_config_schema.sh</code> — config.yaml shape</summary>

Parses `config.yaml` with PyYAML and asserts every key the runtime contract
depends on is present and non-empty: `meta_info.name == "curator"`,
`meta_info.environment.{venv_path, requirements}`, `meta_info.repos.swegen`,
`runtime_info.input.github_tokens`,
`runtime_info.input.llm_api.{api_key, api_base_url, pr_model, task_model}`,
`runtime_info.output.swe_tasks_dir.path`. Pure-Python check, no I/O.
</details>

<details>
<summary><code>cases/02_repo_pin.sh</code> — submodule pin + venv editable install</summary>

Three checks: (1) `repos/swegen/.git` exists (file or directory); (2) if
`meta_info.repos.swegen.commit_id` is non-null, `git rev-parse HEAD` matches
the pin (a `null` pin is treated as "track latest"); (3)
`artifacts/envs/swegen-env/bin/python -c "import swegen"` succeeds.
</details>

<details>
<summary><code>cases/03_github_tokens.sh</code> — every PAT authenticates</summary>

Sources `scripts/load_runtime_env.sh` to hydrate `GITHUB_TOKENS` (env →
`gh_token.txt` → `~/gh_token.txt`, same precedence as the swegen collector).
Splits the comma-separated list and `GET /rate_limit` per token. Reports the
last six chars of any failing PAT.
</details>

<details>
<summary><code>cases/04_llm_endpoint.sh</code> — chat completion succeeds</summary>

Runs inside `artifacts/envs/swegen-env` with `PYTHONPATH=repos/swegen/src`.
Calls `hydrate_cross_provider_env()` then `get_openai_compatible_config()`,
then `client.chat.completions.create(model=…, max_tokens=16)`. Catches the
failure mode where `/models` would pass but the actual chat call doesn't —
typically a mismatched cross-provider config.
</details>

<details>
<summary><code>cases/05_docker_daemon.sh</code> — Docker socket reachable</summary>

Asserts `docker` is on `PATH` and `docker info` exits 0 with
`DOCKER_HOST=unix:///var/run/docker.sock` exported by the test itself (so
the result doesn't depend on the runner shell's env).
</details>

<details>
<summary><code>cases/06_harbor_smoke.sh</code> — known verified task still works</summary>

If `artifacts/swe_tasks/py-cc/tox-dev__tox-3813/` exists, runs
`swegen validate … --task tox-dev__tox-3813 --env docker` and asserts the
output contains both `NOP reward=0` and `Oracle reward=1`. Otherwise SKIPs.
</details>

<details>
<summary><code>smoke/10_pr_demo.sh</code> — end-to-end 10-PR demo</summary>

Runs `swegen create --max-pr 1 --no-require-issue --min-source-files 1`
against the 10 Python PRs in `fixtures/python_pr_ids.txt` (the quick-verify
set with `tox-dev/tox:pr-3813` first). Wrapped in `timeout --foreground 3600`
for a 60-minute hard budget. Passes when
`artifacts/swe_tasks/py-cc-smoke/verifiable_tasks.txt` has ≥1 task ID. On
failure, tails the last 40 lines of `artifacts/swe_tasks/.swegen-smoke-py.log`.
</details>
