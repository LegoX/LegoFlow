# swegen CI tests

CI gate for the `swegen` block. Calibrated to **this repo's fixed CI runner**
(specific venv paths, configured `DOCKER_HOST`, the `artifacts/envs/swegen-env`
layout, etc.) — these tests are NOT a drop-in replacement for the portable
`/swegen:check` skill, which tolerates arbitrary user environments.

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
# Cheap path (cases/ only) — safe for cloud CI:
bash subblock/swegen/tests/run.sh

# Full path (cases/ + smoke/) — ~30 min, real LLM + Docker; self-hosted only:
bash subblock/swegen/tests/run.sh --with-smoke
# or:  TESTS_WITH_SMOKE=1 bash subblock/swegen/tests/run.sh
```

Each test exits `0` for pass, `77` for skip, anything else for fail. `run.sh`
returns non-zero iff any test failed.

## Smoke pass condition

`smoke/10_pr_demo.sh` runs `swegen create --max-pr 1` against the 10 Python PRs
listed in `fixtures/python_pr_ids.txt` (the quick-verify set, with
`tox-dev/tox:pr-3813` first). It passes iff
`artifacts/swe_tasks/py-cc-smoke/verifiable_tasks.txt` contains at least one
task ID within a 30-minute wall-clock budget.
