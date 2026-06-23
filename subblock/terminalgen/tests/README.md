# terminalgen CI tests

Drift detection (cheap, ~30 s) plus an optional end-to-end smoke run for the
terminalgen block. Calibrated to this repo's fixed CI runner. For portable
user-environment diagnostics, use the `/terminalgen:check` skill instead.

---

## Quickstart

```bash
# cheap path — every test except the generation smoke. Safe to run anywhere:
bash subblock/terminalgen/tests/run.sh

# full path — adds the real-LLM, real-Docker generation smoke. Self-hosted only:
bash subblock/terminalgen/tests/run.sh --with-smoke
# equivalent:  TESTS_WITH_SMOKE=1 bash subblock/terminalgen/tests/run.sh
```

Per-test exit codes: `0` pass · `77` skip · anything else fail.
`run.sh` returns non-zero iff at least one test failed; skips never fail the suite.

---

## What gets checked

| # | Test | What it asserts | Time |
|---|---|---|---|
| 01 | config schema | every required key in `config.yaml` is set, `meta_info.name == "terminalgen"`, all 13 domains have `tag_filter` + params | <1 s |
| 02 | repo pin + venv | `repos/terminal-lego` is at the pinned commit with all pipeline entrypoints, and `requests` imports inside the venv | <1 s |
| 03 | SO API key | `SO_API_KEY` returns 200 from `/2.3/questions` with quota (SKIPs if no key — 300/day fallback) | ~1 s |
| 04 | LLM endpoint | a real `chat.completions` POST to `OPENAI_API_BASE_URL` returns content | ~20 s |
| 05 | Docker | daemon reachable via configured `DOCKER_HOST` | <1 s |
| 06 | fixture smoke | known verified task `https-nginx-cert-setup` still validates reward=1.0 (SKIPs if Docker/fixture absent) | ~30 s |
| 10 | **SO demo** *(smoke)* | generator + validator over the 2-question fixture produces ≥1 verified task | a few min |

The SO demo runs only with `--with-smoke` and is gated to `push` events on
`dev`/`main` and manual `workflow_dispatch` runs in CI — never to PRs, so PR
builds don't burn LLM tokens.

---

## When something fails

| You see | Most likely cause | What to do |
|---|---|---|
| 02: `.git missing` | submodule never initialised | `git submodule update --init repos/terminal-lego` |
| 02: `HEAD does not match commit_id` | someone fetched a different commit | `git -C repos/terminal-lego checkout <pin>` |
| 02: `requests not importable` | venv exists but deps not installed | `pip install -r repos/terminal-lego/requirements.txt` |
| 03: HTTP non-200 | SO key expired/invalid | regenerate at stackapps.com, update `SO_API_KEY` |
| 03: SKIP | no `SO_API_KEY` set | not a failure; scraper uses 300/day shared quota |
| 04: HTTP 401/403 | `OPENAI_API_KEY` wrong or endpoint mismatch | verify `OPENAI_API_BASE_URL` + key |
| 05: `docker info failed` | daemon down or `DOCKER_HOST` stale | `export DOCKER_HOST=unix:///var/run/docker.sock` |
| 06: SKIPped | Docker or fixture absent | not a failure; stage fixture via `/terminalgen:setup` |
| 10: no verified task | LLM picked an internet/credential-dependent topic | narrow the domain `tag_filter`; tail `artifacts/experiments/quick-verify/smoke.log` |

---

## Layout

```
cases/                cheap deterministic checks
  01_config_schema.sh
  02_repo_pin.sh
  03_so_api_key.sh
  04_llm_endpoint.sh
  05_docker_daemon.sh
  06_harbor_smoke.sh
smoke/                expensive end-to-end runs (--with-smoke gates them)
  verify.sh           deterministic fixture replay (no LLM) — the default first check
  10_so_demo.sh       real generation + validation over a 2-question fixture
  config.yaml         smoke-mode config overlay
  fixtures/
    https-nginx-cert-setup/   known-good harbor-1.1 verified task
    so_data_sample.json       2 sample SO questions
run.sh                aggregator
```

---

## Per-test reference

Skip this section unless you're debugging a specific case or about to change one.

<details>
<summary><code>cases/01_config_schema.sh</code> — config.yaml shape</summary>

Parses `config.yaml` with PyYAML and asserts every key the runtime contract
depends on is present and non-empty: `meta_info.name == "terminalgen"`,
`meta_info.environment.{venv_path, requirements}`, `meta_info.repos.terminal-lego.commit_id`,
`runtime_info.input.{so_api_key, llm_api.api_key, llm_api.api_base_url, llm_api.gen_model}`,
`runtime_info.output.{terminal_tasks_dir, merged_tasks_dir}.path`, and that all 13
domains carry a `tag_filter` and `params.{gen_workers,val_workers,val_timeout}`.
</details>

<details>
<summary><code>cases/02_repo_pin.sh</code> — submodule pin + venv dependency</summary>

Asserts `repos/terminal-lego/.git` exists, `git rev-parse HEAD` matches the
pinned `commit_id`, the three pipeline entrypoints (scraper/generator/validator)
are present, and `requests` imports inside `artifacts/envs/terminalgen-env`
(SKIPs the import check if the venv hasn't been built yet).
</details>

<details>
<summary><code>cases/03_so_api_key.sh</code> — StackExchange key reachable</summary>

Sources `scripts/load_runtime_env.sh`, then `GET /2.3/questions` with the key
and reports `quota_remaining` / `quota_max` (10000 with a valid key). SKIPs when
no key is set — the pipeline still runs at the 300/day shared-IP quota.
</details>

<details>
<summary><code>cases/04_llm_endpoint.sh</code> — chat completion succeeds</summary>

Dependency-free POST to `OPENAI_API_BASE_URL/chat/completions` with the
configured model and `max_tokens=16`. Retries transient 5xx/timeouts; fails fast
on 401/403/400/404. This is the endpoint the generator hits via `--api-base`.
</details>

<details>
<summary><code>cases/05_docker_daemon.sh</code> — Docker socket reachable</summary>

Asserts `docker` is on `PATH` and `docker info` exits 0 with
`DOCKER_HOST=unix:///var/run/docker.sock` exported by the test itself.
</details>

<details>
<summary><code>cases/06_harbor_smoke.sh</code> — known verified task still works</summary>

Stages `tests/smoke/fixtures/https-nginx-cert-setup` as `task_00000` and runs
terminal-lego's validator, asserting `validation_report.json` reports
`passed == 1`. SKIPs if Docker or the validator/fixture are absent.
</details>

<details>
<summary><code>smoke/10_so_demo.sh</code> — end-to-end generation demo</summary>

Runs the terminal-lego generator (`--limit 1`) over the 2-question
`fixtures/so_data_sample.json`, then Docker-validates the candidate(s). Passes
when `validation_report.json` reports ≥1 verified task. On failure, tails the
last 40 lines of `artifacts/experiments/quick-verify/smoke.log`.
</details>
