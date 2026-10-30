---
name: create-tasks
description: >
  Launch Curator task generation from existing per-language PR ID files after
  preflight passes. This skill does not collect PRs. It runs `legoflow-curator create`,
  performs NOP/Oracle verification, and appends verified task IDs to
  `verifiable_tasks.txt`. Per-language launches use
  `bash scripts/create_<lang>.sh` (tuned `--timeout`, `--cc-timeout`,
  `--n-concurrent`, `--state-dir`). Long-running (hours per language).
  When the RSI loop inputs are enabled, also runs the targeting audit gate
  (`apply_targeting.py` directives → agent audit → user confirmation before
  any config edit) and the two `decontam_check.py` stages (PR-ID prefilter
  before `create`, pool scrub after). For a first-time end-to-end smoke
  before committing to a full run,
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

## Step 2b - RSI targeting gate (only when `targeting.enabled: true`)

The RSI loop's actuation gate — mirrors the terminal-curator semantic-audit
pattern: scripts propose, this agent audits, the human confirms, and only then
does anything touch `config.yaml`. Design: `RSI_LOOP_PLAN.md` §2.2/§5.3;
round context: `RSI_RUNBOOK.md`.

1. Run `python3 scripts/apply_targeting.py`. It refuses on any open verdict
   in `artifacts/targeting/hypothesis_ledger.yaml`, supply-checks each weak
   tag against this block's own manifests, applies the noise gate, ranks by
   learning progress, and writes `artifacts/targeting/directives_round_NNN.yaml`
   with a pre-registered hypothesis (target tags, expected direction,
   actuation, noise floor).
2. **Audit the directives like the terminal-curator semantic audit, not a
   rubber stamp**: check each target tag's supply count and delta against the
   weakness report, confirm blocked tags are legitimately blocked
   (`below_noise_gate`, `n < min_tag_n`), confirm holdout tags are reserved,
   and confirm the quota deltas stay within the stability guards (slew ≤
   ±20%/round, per-repo ≤15%, per-tag-cell ≤25%). A directives file whose
   numbers do not match its own `source_report_sha256` is refused.
3. Present the reviewable diff (quota vector + filter deltas + skip-file
   reference) and **wait for explicit user confirmation**. Only then apply
   the numbers to the **next run's** `config.yaml` or private profile —
   one-shot semantics: never edit a config mid-run. Directives that fail the
   audit are recorded back in the ledger, not silently dropped.
4. No directive round at all (`directives_blocked` empty, targeting disabled,
   or the report absent) is a normal outcome — proceed with the ordinary
   create flow and say so plainly.

## Step 2c - RSI decontam prefilter (only when `decontam.enabled: true`)

Before any production launch, run `python3 scripts/decontam_check.py
--index <decontam.benchmark_index> --stage pr_ids` so benchmark-overlapping
PRs are filtered out of `artifacts/collected_prs/{lang}_pr_ids.txt`
**before** `create` — this saves the expensive Docker Oracle/NOP minutes. It
fails closed on a missing identity index: fix the index path or disable
decontam for this run; never launch with a dangling index. Hits are recorded
with match basis in `artifacts/decontam/quarantine.yaml`.

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

## Step 5b - RSI decontam pool scrub (only when `decontam.enabled: true`)

After generation and Oracle/NOP verification append to
`verifiable_tasks.txt`, run `python3 scripts/decontam_check.py --index
<decontam.benchmark_index> --stage pool` to scrub any identity match that
slipped through the PR-ID prefilter out of
the manifests, re-run `scripts/extract_verified_tasks.py` if the merged pool
already exists, and report the quarantined IDs with their match basis (repo+PR,
patch-path Jaccard, test-file overlap, or normalized patch hash) plus the
pruned counts. Never delete task directories silently — quarantine evidence
goes to `artifacts/decontam/quarantine.yaml`; durable suppressions use
`owner__repo-<PR>` lines (bare PR number, e.g. `owner__repo-101`) in the skip
file consumed by `--skip-pr-files` — the form `cli.py` actually matches.
Remind the user to `git add` the git-tracked loop-memory files
(`artifacts/targeting/`, `artifacts/decontam/`) so `clean.sh --all` cannot
wipe the hypothesis history.

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
- RSI directives reach `config.yaml` only through the Step 2b audited gate —
  agent audit plus explicit user confirmation of the reviewable diff. Never
  apply a directives file mechanically or edit a config mid-run.
- Decontam skip-file lines must be `owner__repo-<PR>` (double underscore, bare
  PR number); `legoflow-curator --skip-pr-files` parses a literal
  `owner__repo-pr-N` line as repo `owner/repo-pr` (no suppression) and rejects
  `owner/repo:pr-N` lines.
