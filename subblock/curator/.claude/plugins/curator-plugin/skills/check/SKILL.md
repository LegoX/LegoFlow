---
name: check
description: >
  Preflight the curator block. Validates config.yaml schema; verifies that
  `repos/swegen/` is checked out at the pinned commit; verifies
  GITHUB_TOKENS reach the GitHub API (one `GET /rate_limit` per token);
  exercises the LLM endpoint with an actual `chat.completions.create`
  ping through `swegen.llm_env.hydrate_cross_provider_env` (so a
  misconfigured cross-provider env is caught here, not on first task);
  verifies `DOCKER_HOST` is set and the daemon is reachable
  (`docker info`); optionally runs a Harbor smoke against a known
  verified task (NOP/Oracle expected to print `reward=0` / `reward=1`).
  Runs `scripts/dryrun.sh` if present. Read-only. Reports all failures
  in one consolidated message with a run-configuration summary.
  **Mandatory before `:run`.** Triggers on phrases like "check curator",
  "preflight curator", "is curator ready", "diagnose curator",
  "validate curator config".
---

# /curator:check

Read-only preflight for the curator block. It answers "is this block ready
to collect PRs, generate tasks, and run Harbor validation?" Report every
failure in one pass; do not stop at the first failed check.

## Step 0 - Orient

Run only from `subblock/curator/`. Validate:

1. `./config.yaml` exists and parses.
2. `meta_info.name == "curator"`.
3. `./repos/swegen/pyproject.toml` exists.
4. `./scripts/dryrun.sh` exists.

If any of these fail, continue with checks that can still run and include
all failures in the final report.

## Step 1 - Deterministic file and config checks

Check these without changing the workspace:

- `config.yaml` has `meta_info`, `runtime_info.input`, `runtime_info.output`,
  and `status`.
- `runtime_info.input.languages` contains the supported language keys:
  `py`, `js`, `ts`, `go`, `c`, `cpp`, `java`, `rust`.
- Each enabled language has `params.timeout`, `params.cc_timeout`, and
  `params.n_concurrent`; these are consumed by `scripts/read_params.py`
  and `scripts/create_<lang>.sh`.
- `runtime_info.output.swe_tasks_dir.path` points to `artifacts/swe_tasks`.
- `scripts/create_<lang>.sh` exists for every enabled language.
- `scripts/start.sh`, `scripts/create_all_bg.sh`, `scripts/load_runtime_env.sh`,
  and `scripts/archive_run.sh` exist.

For the submodule, run:

```bash
git -C repos/swegen rev-parse HEAD
```

If `meta_info.repos.swegen.commit_id` is non-null, the HEAD must match it.
If the config says `null`, report the HEAD as informational, not a failure.

## Step 2 - GitHub credentials

Resolve tokens from `GITHUB_TOKENS`, `GITHUB_TOKEN`, or an explicit token
file. Note that the collector `repos/swegen/tools/collect_prs_wo_image.py`
defaults to `repos/swegen/gh_token.txt` unless
`COLLECT_GITHUB_TOKEN_FILE` overrides it.

For each token, call:

```text
GET https://api.github.com/rate_limit
```

Report HTTP status and `resources.core.remaining`. Missing tokens are a
failure for real runs and a warning for pure dashboard inspection.

## Step 3 - LLM endpoint

This check is mandatory before `/curator:run`; do not skip it just because
`scripts/dryrun.sh` passes. Use the installed SWEgen package, not an ad hoc
request:

```python
from openai import OpenAI
from swegen.llm_env import hydrate_cross_provider_env, get_openai_compatible_config

hydrate_cross_provider_env()
model, key, base = get_openai_compatible_config()
OpenAI(api_key=key, base_url=base, timeout=60).chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "ping"}],
    max_tokens=16,
)
```

A `/models` probe is not enough; real completion catches wrong keys,
wrong-region routing, and stale Anthropic/OpenAI shim variables.

If the provider returns `401 Invalid token`, stop and ask for a replacement
API key. Keep the base URLs from the environment unless the error points at
routing. When testing a replacement key, export it only for the current
shell process and mirror it to both `OPENAI_API_KEY` and
`ANTHROPIC_API_KEY`; never write it to `.env`, `config.yaml`, or logs.

## Step 3b - Claude Code path (verification proxy)

The OpenAI ping above only covers PR evaluation. The Claude Code path
(`ANTHROPIC_BASE_URL`) is what writes `verifiable_tasks.txt`, and it fails
*silently* when misconfigured. Read `llm_api.cc_provider_mode`:

- `openai_proxy` (Qwen / GLM / sglang / vLLM and most self-hosted endpoints):
  `ANTHROPIC_BASE_URL` must be a running local LiteLLM proxy. Verify:

  ```bash
  curl -sf "${ANTHROPIC_BASE_URL%/}/health" >/dev/null && echo "cc proxy ok" || echo "cc proxy DOWN"
  ```

  A down proxy is a **blocking failure** — generation would report success
  while verifying nothing. Tell the user to start it (see `CLAUDE.md`
  "LLM provider modes").
- `native` (real Claude / Anthropic-compatible gateway): no proxy required;
  `ANTHROPIC_BASE_URL` points straight at the provider. Note it as
  informational.

## Step 4 - Docker and Harbor readiness

Run:

```bash
docker info --format '{{.ServerVersion}}'
```

Also require `DOCKER_HOST` to be set, preferably
`unix:///var/run/docker.sock`. If Docker works but `DOCKER_HOST` is empty,
warn that Harbor may incorrectly probe `/tmp/podman-fresh.sock`.

If the user asks for a smoke check, validate the submodule sample task:

```bash
swegen validate \
  repos/swegen/artifacts/swe_tasks/py-cc \
  --task tox-dev__tox-3813 \
  --jobs-dir artifacts/experiments/quick-verify/harbor-jobs-quick \
  --env docker \
  --docker-prune-batch 0
```

Expected result: NOP reward is `0` and Oracle reward is `1`. If the sample
task is missing, report that `/curator:setup` must initialize the submodule.

## Step 5 - Run the block dryrun

Run `bash scripts/dryrun.sh` and include its OK/WARN/FAIL lines in the
report. This script verifies the installed package, YAML parsing, key env
vars, the Claude Code proxy endpoint, and Docker availability.

## Step 6 - Final report

Always end with a compact summary:

```text
curator check - <CWD>
  config          : <ok|fail>
  repos/swegen    : <sha or missing>
  github          : <N tokens ok, total remaining K>
  llm (openai)    : <base> / <model> / <ok|fail>
  cc path         : <mode> / <anthropic_base_url> / <ok|down|native>
  docker          : <server version> at <DOCKER_HOST or unset>
  languages       : <enabled list with timeout/cc_timeout/n_concurrent>
  swe tasks       : <per-language generated + verified counts>
  dryrun          : <pass|warn|fail>
  smoke           : <skipped|pass|fail>

SAFE TO RUN: <YES|NO>
```

`SAFE TO RUN` is `NO` if config, package install, GitHub, LLM, Docker, or
dryrun failed, or if `cc_provider_mode=openai_proxy` and the CC proxy is down.
A skipped smoke does not block unless the user explicitly requested smoke.

## Guardrails

- Read-only except for the optional Harbor smoke jobs directory.
- Do not edit `config.yaml`, `.env`, token files, or `artifacts/index.yaml`.
- Do not launch `scripts/start.sh` or `swegen create`; that is `/curator:run`.
- Do not hide credential or provider errors. Quote the provider error
  message, but never print secret values.
