---
name: check
description: >
  Preflight the terminalgen block. Validates config.yaml schema; verifies
  that `repos/terminal-lego/` is checked out at the pinned commit; verifies
  the StackExchange key reaches the API (`GET /2.3/questions` with key,
  reports quota); exercises the LLM endpoint with an actual
  `chat.completions.create` ping against `OPENAI_API_BASE_URL` (so a
  misconfigured endpoint is caught here, not on first task); verifies
  `DOCKER_HOST` is set and the daemon is reachable (`docker info`);
  optionally replays a known-good verified task (`https-nginx-cert-setup`,
  expected reward=1). Runs `scripts/dryrun.sh` if present. Read-only.
  Reports all failures in one consolidated message with a run-configuration
  summary. **Mandatory before `:run`.** Triggers on phrases like
  "check terminalgen", "preflight terminalgen", "is terminalgen ready",
  "diagnose terminalgen", "validate terminalgen config".
---

# /terminalgen:check

Read-only preflight for the terminalgen block. It answers "is this block ready
to scrape questions, generate tasks, and run Docker validation?" Report every
failure in one pass; do not stop at the first failed check.

## Step 0 - Orient

Run only from `subblock/terminalgen/`. Validate:

1. `./config.yaml` exists and parses.
2. `meta_info.name == "terminalgen"`.
3. `./repos/terminal-lego/requirements.txt` exists.
4. `./scripts/dryrun.sh` exists.

If any of these fail, continue with checks that can still run and include all
failures in the final report.

## Step 1 - Deterministic file and config checks

Check these without changing the workspace:

- `config.yaml` has `meta_info`, `runtime_info.input`, `runtime_info.output`,
  and `status`.
- `runtime_info.input.domains` contains the 13 domain keys:
  `core-terminal-os`, `versioning-containers`, `networking-services`,
  `file-text-processing`, `python-ecosystem`, `ml-data`, `databases-storage`,
  `web-automation-apis`, `security-cryptography`, `debugging-reliability`,
  `algorithms-concurrency`, `media-scientific`, `build-editor-tooling`.
- Each enabled domain has `tag_filter` and `params.{gen_workers,val_workers,val_timeout}`;
  these are consumed by `scripts/read_params.py` and `scripts/create_domain.sh`.
- `runtime_info.output.terminal_tasks_dir.path` points to `artifacts/terminal_tasks`.
- `runtime_info.output.merged_tasks_dir.path` points to `artifacts/merged_terminal_tasks`.
- `scripts/scrape_so_questions.sh`, `scripts/create_domain.sh`, `scripts/start.sh`,
  `scripts/create_all_bg.sh`, `scripts/load_runtime_env.sh`,
  `scripts/extract_verified_tasks.py`, and `scripts/archive_run.sh` exist.

For the submodule, run:

```bash
git -C repos/terminal-lego rev-parse HEAD
```

The HEAD must match `meta_info.repos.terminal-lego.commit_id`. Report any drift
as a failure (the pipeline is pinned for reproducibility).

## Step 2 - StackExchange credentials

Resolve the key from `SO_API_KEY`. Call:

```text
GET https://api.stackexchange.com/2.3/questions?site=stackoverflow&pagesize=1&key=$SO_API_KEY
```

Report HTTP status and `quota_remaining`. A working key returns `quota_max:
10000`; absence falls back to 300/day shared by IP — report as a warning (the
pipeline still runs, just rate-limited).

## Step 3 - LLM endpoint

This check is mandatory before `/terminalgen:run`; do not skip it just because
`scripts/dryrun.sh` passes. Hit the endpoint the generator actually uses:

```python
from openai import OpenAI
import os
base = os.environ["OPENAI_API_BASE_URL"]
key  = os.environ["OPENAI_API_KEY"]
model = os.environ.get("MODEL_NAME", "deepseek-v4-flash")
OpenAI(api_key=key, base_url=base, timeout=60).chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "ping"}],
    max_tokens=16,
)
```

A `/models` probe is not enough; real completion catches wrong keys and
wrong-region routing. **Note**: terminal-lego's generator reads the endpoint
from `--api-base` (which `create_domain.sh` sets to `$OPENAI_API_BASE_URL`), so
test that variable specifically.

If the provider returns `401 Invalid token`, stop and ask for a replacement API
key. When testing a replacement key, export it only for the current shell
process; never write it to `.env`, `config.yaml`, or logs.

## Step 4 - Docker readiness

Run:

```bash
docker info --format '{{.ServerVersion}}'
```

Also require `DOCKER_HOST` to be set, preferably `unix:///var/run/docker.sock`.
The validator builds and runs one container per task; Docker must be reachable.

If the user asks for a smoke check, replay the known-good fixture task:

```bash
bash tests/smoke/verify.sh
```

Expected result: `https-nginx-cert-setup` validates with reward=1. If the
fixture is missing, report that `/terminalgen:setup` must stage it.

## Step 5 - Run the block dryrun

Run `bash scripts/dryrun.sh` and include its OK/WARN/FAIL lines in the report.
This script verifies the submodule pin, YAML parsing, key env vars, and Docker
availability.

## Step 6 - Final report

Always end with a compact summary:

```text
terminalgen check - <CWD>
  config              : <ok|fail>
  repos/terminal-lego : <sha or missing>
  so api              : <quota_remaining or no-key warning>
  llm                 : <base> / <model> / <ok|fail>
  docker              : <server version> at <DOCKER_HOST or unset>
  domains             : <enabled list with gen_workers/val_workers/val_timeout>
  terminal tasks      : <per-domain generated + verified counts>
  dryrun              : <pass|warn|fail>
  smoke               : <skipped|pass|fail>

SAFE TO RUN: <YES|NO>
```

`SAFE TO RUN` is `NO` if config, submodule pin, LLM, Docker, or dryrun failed.
A missing SO key is a warning, not a blocker. A skipped smoke does not block
unless the user explicitly requested smoke.

## Guardrails

- Read-only except for the optional smoke jobs directory.
- Do not edit `config.yaml`, `.env`, or `artifacts/index.yaml`.
- **Never modify `repos/terminal-lego/`.**
- Do not launch `scripts/start.sh` or the generator; that is `/terminalgen:run`.
- Do not hide credential or provider errors. Quote the provider error message,
  but never print secret values.
