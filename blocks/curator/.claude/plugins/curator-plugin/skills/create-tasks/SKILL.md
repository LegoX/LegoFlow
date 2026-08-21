---
name: create-tasks
description: >
  Launch Curator task generation from existing per-language PR ID files after
  preflight passes. This skill does not collect PRs. It runs `legoflow-curator create`,
  performs NOP/Oracle verification, and appends verified task IDs to
  `verifiable_tasks.txt`. Per-language launches use
  `bash scripts/create_<lang>.sh` (tuned `--timeout`, `--cc-timeout`,
  `--n-concurrent`, `--state-dir`). Long-running (hours per language).
  For a first-time end-to-end smoke before committing to a full run,
  this skill can drive a 10-PR `--max-pr 1` flow that attempts to produce one
  verified task ID. Full mode archives launcher exit into
  `artifacts/index.yaml` via `scripts/archive_run.sh`; smoke and single-language
  modes do not archive. Triggers on phrases like "create Curator tasks",
  "generate Curator SWE tasks", "start Curator task generation",
  "smoke-test Curator task creation".
---

# /curator:create-tasks

Preflight and launch SWE task generation. Curator is a leaf block: this
skill runs commands inside `blocks/curator/` and does not dispatch to
child blocks.

Production modes consume the fixed
`artifacts/collected_prs/{language}_pr_ids.txt` paths used by
`scripts/create_<lang>.sh`. If a required pool is missing, run
`/curator:collect-prs` first and wait for its background collector to finish.
Changing `pr_collection.output_dir` does not change the create scripts' input
paths. Smoke mode is independent: it uses the submodule's bundled sample PR
file instead of the block's collected pool.

## Step 0 - Orient

Validate:

1. Current directory is `blocks/curator/`.
2. `config.yaml` has `meta_info.name == "curator"`.
3. `scripts/start.sh` and the required `scripts/create_<lang>.sh` files
   exist.
4. `repos/legoflow-curator/` is initialized and installable.

Read `config.yaml`, `CLAUDE.md`, and `memory/quick-verify.md` before choosing a
mode.

## Step 1 - Refuse duplicate live runs

Before launching, check for existing Curator work owned by this block. Scope
the match to the current block path or this block's artifact paths so older
jobs in a separate checkout such as `$HOME/LegoFlow Curator` do not block this run:

```bash
BLOCK_DIR="$(pwd -P)"
pgrep -af 'legoflow-curator create|scripts/create_.*\\.sh|scripts/create_all_bg\\.sh' \
  | grep -F "$BLOCK_DIR" || true
```

If a run is alive, refuse to start another. Report the PID, elapsed time,
and likely log path under `artifacts/logs/legoflow-curator-create/`. Tell the user
to let it finish or stop it before retrying.

## Step 2 - Choose the run mode

Resolve the user's natural-language request into one mode:

| Mode | Use when | Command shape |
| --- | --- | --- |
| `smoke` | First run, "quick verify", "smoke", "one task", or "10 PRs". | `legoflow-curator create` against the submodule sample PR file, with `--max-pr 1`, `--n-concurrent 1`, `--min-source-files 1`, and output under `artifacts/experiments/quick-verify/`. |
| `single-language` | The user names one language: `py`, `js`, `ts`, `go`, `c`, `cpp`, `java`, or `rust`. | `bash scripts/create_<lang>.sh` after confirming tuned params from `scripts/read_params.py`. |
| `full` | The user says all languages, pipeline, or gives no narrower scope. | Pick by `llm_api.cc_provider_mode` in `config.yaml`: `bash scripts/start_with_anthropic_api.sh` for `native` (real Claude / Anthropic-format gateway, no proxy), `bash scripts/start_with_openai_api.sh` for `openai_proxy` (starts the local LiteLLM proxy first, then runs `start.sh`). Both delegate to `scripts/start.sh -> scripts/create_all_bg.sh`, which starts one worker per language whose `enabled` is `true`. |

If the request implies config changes, such as "32 tasks" or "more
concurrency", show the exact proposed config/env override and wait for
confirmation before changing or launching.

## Step 3 - Preflight

Run `/curator:check` logic first. For `smoke`, include the Harbor sample
validation unless the user explicitly skips it. Abort on any blocking
failure:

- config/package install failed
- GitHub tokens unavailable
- LLM completion ping failed
- CC proxy down while `cc_provider_mode=openai_proxy` (verification would fail silently)
- Docker unavailable
- `scripts/dryrun.sh` failed
- requested smoke validation failed

The LLM completion ping is mandatory; never proceed to `legoflow-curator create`
after only `scripts/dryrun.sh`. If the LLM ping returns `401 Invalid token`,
ask for a replacement API key, mirror it to `OPENAI_API_KEY` and
`ANTHROPIC_API_KEY` only in the current shell, and rerun preflight before
launch. Do not edit inputs to make preflight pass.

For `openai_proxy`, run `bash scripts/setup_cc_proxy_env.sh` once and use
`scripts/cc_proxy_lib.sh` (the smoke drivers do this automatically). If the
configured port belongs to another process, choose a free
`LEGOFLOW_CURATOR_CC_PROXY_PORT`; never reuse a merely live but unrelated proxy.
Claude Code runs with an isolated ignored HOME so user-level plugins and stale
credentials cannot override the explicit gateway.

## Step 4 - Show run configuration and confirm

Print a compact summary and ask for explicit confirmation:

```text
curator create-tasks configuration
  mode             : <smoke|single-language|full>
  languages        : <list>
  input PRs         : <path>
  output tasks      : <path>
  state dir         : <path>
  timeout           : <per-language timeout>
  cc_timeout        : <per-language cc timeout>
  concurrency       : <per-language n_concurrent>
  validation        : NOP + Oracle via Harbor
  logs              : artifacts/logs/legoflow-curator-create/
  archive           : full only; none for smoke/single-language
```

Never launch a full or single-language run without an explicit "yes".
Smoke can run in the foreground after confirmation; long runs default to
background.

## Step 5 - Launch

### Smoke

Use the fixed sample PR list carried by the submodule, but write outputs
to this block's experiment directory:

```bash
legoflow-curator create \
  --input-ids-file repos/legoflow-curator/artifacts/collected_prs/python_pr_ids.txt \
  --max-pr 1 \
  --n-concurrent 1 \
  --output artifacts/experiments/quick-verify/swe_tasks/py-cc \
  --state-dir artifacts/experiments/quick-verify/state \
  --timeout 2400 \
  --cc-timeout 1800 \
  --no-require-issue \
  --min-source-files 1 \
  --max-source-files 10 \
  --docker-prune-batch 0 \
  --verbose
```

Success means
`artifacts/experiments/quick-verify/swe_tasks/py-cc/verifiable_tasks.txt`
exists and contains at least one task id.

Smoke is small but not instant: Claude Code task generation plus Harbor
validation can take several minutes. Use a long foreground timeout or launch
it in the background with a log if the user does not want the session held.
If interrupted, inspect `artifacts/experiments/quick-verify/state/` and
`artifacts/experiments/quick-verify/swe_tasks/py-cc/.legoflow-curator-create-batch/`
before deciding whether to resume or clean up.

### Single language

Use the language script so it honors `config.yaml` via `scripts/read_params.py`:

```bash
bash scripts/create_<lang>.sh
```

The script writes to `artifacts/swe_tasks/<lang>-cc/`, appends verified
task ids to `verifiable_tasks.txt`, and logs under
`artifacts/logs/legoflow-curator-create/`.

### Full run

Pick the launcher that matches `llm_api.cc_provider_mode` in `config.yaml`:

```bash
# native: real Claude or Anthropic-format gateway (no local proxy)
bash scripts/start_with_anthropic_api.sh

# openai_proxy: OpenAI-only provider (Qwen/GLM/sglang/vLLM); starts a local
# LiteLLM proxy (Anthropic -> OpenAI) on cc_proxy_port first, then launches generation
bash scripts/start_with_openai_api.sh
```

Both refuse to run if the config's `cc_provider_mode` does not match the
launcher, then delegate to `scripts/start.sh` (which calls
`scripts/create_all_bg.sh`). That script starts one `nohup` worker per language
whose `enabled` is `true` and returns without waiting for them. `start.sh` then archives the
launcher exit, so an archive status of `completed` does not mean the workers
have finished; use their logs, batch state, and `/curator:dashboard` for live
progress.

`start_with_openai_api.sh` owns its LiteLLM proxy and stops it when the launcher
returns. Because the workers are detached, the current wrapper does not keep
that proxy alive for their full run. Do not report an `openai_proxy` full launch
as healthy unless a separately supervised compatible proxy remains available.
Do not pre-write entries to `artifacts/index.yaml`; the scripts own launcher
archiving.

## Step 6 - Report and monitor

After launch, print:

- command or background PID
- log path
- output path
- how to stop or attach
- next `/curator:dashboard` command

For foreground smoke, report the resulting task id or the failing phase.
For background runs, poll once after a short delay to ensure the process
started, then hand off to `/curator:dashboard`.

## Guardrails

- Do not run multiple Curator generation jobs concurrently in the same block.
- Do not write secrets to repository files.
- Do not modify `repos/legoflow-curator/` source while running data generation.
- Do not delete generated tasks, state dirs, or logs unless the user asks
  for cleanup.
## Shared LegoFlow CLI

The canonical execution command for this skill is `./bin/legoflow create-tasks`. Claude Code and Codex use this same command; this skill supplies the agent-specific confirmation, reporting, and artifact-analysis workflow around it.
