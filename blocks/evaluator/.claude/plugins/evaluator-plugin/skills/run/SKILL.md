---
name: run
description: >
  Launch the evaluator pipeline via `scripts/start.sh` after preflight
  passes: generate the per-job LiteLLM config from
  `runtime_info.input.litellm_proxy`, start the LiteLLM proxy on the
  configured port, then run the Harbor job with
  the configured dataset/version and registry path, together with any
  `--exclude-task-name` flags from `HARBOR_EXCLUDE_TASKS`. With
  `harbor_job.repeats > 1` the Harbor+analyze section runs k stochastic
  repeats (proxy restarted per repeat); the post-run RSI loop builder wired
  into `start.sh` emits the weakness report only — the identity index and
  audit slice are agent-invoked after the run (step 5). Long-running.
  Stamps live state into `artifacts/index.yaml` via
  `scripts/archive_run.sh`. Triggers on phrases like "run evaluator",
  "launch evaluator", "run swebench", "evaluate the model", "kick off the
  evaluator benchmark".
---

# /evaluator:run


Use the complete effective profile selected by `EVAL_CONFIG` (relative filenames
resolve from `blocks/evaluator/`), otherwise this block's `config.yaml`. Retain a
parent-selected profile throughout checks, setup, launch, and archive. Block-local
references to `config.yaml` below mean that profile; root shared-credential config
references keep their existing meaning. Do not reset the selector or copy a
private profile over the committed config.

Preflight-then-execute entry point. Always invoke `/evaluator:check` first;
require explicit user confirmation before launching. evaluator is a **leaf
block** — no child blocks — so this skill runs `scripts/start.sh` directly
(per BLOCK_DEFINITION.md §3.2, no child dispatch applies).

## Procedure

1. **Preflight** — invoke `/evaluator:check`; abort on any FAIL. Never launch
   on a stale/unvalidated config.
2. **Confirm** — present the run-configuration summary from `:check`
   plus the benchmark task count (post-`HARBOR_EXCLUDE_TASKS`), the
   `agent.name@version` + `runtime_image`, and a rough wall-time
   estimate. **Wait for an explicit `yes`** (root `CLAUDE.md`
   "check → confirm → run"). Never auto-launch — a full benchmark is
   many container-hours.
3. **Launch** — `bash scripts/start.sh`, background by default (evaluator runs
   are long; ask the user once if they want foreground). Per
   `meta_info.resources.ip`:
   - `local` / null / absent → run on the current host inside a tmux
     session named `evaluator`.
   - configured remote hostname/IP → connect with the user's SSH-key
     configuration, attach to (or create) a remote tmux session named `evaluator`, and run `start.sh` inside it from
     `meta_info.resources.directory` (per BLOCK_DEFINITION.md §2.3).
     Confirm with the user whether the code is already in sync at that
     path or needs rsync first. A tmux session keeps the run alive across
     shell disconnects.
4. **Archive** — `start.sh`'s EXIT trap runs `cleanup_litellm` then
   `scripts/archive_run.sh "$rc" "$RUN_STARTED_AT"`, which appends to
   `artifacts/index.yaml` and snapshots config + scripts under
   `artifacts/archives/run_NNN/`. Do not write `index.yaml` from this
   skill.
5. **RSI loop artifacts (when `harbor_job.repeats > 1` or a loop round is
   active)** — after the run, verify and report the loop outputs:
   `artifacts/weakness/weakness_report.json` (+ its immutable
   `weakness/history/` copy), the `artifacts/loop/ledger.yaml` measurement
   row, `artifacts/response_matrix.jsonl` rows, and — if this round built or
   rotated it — `artifacts/reserve/audit_slice.txt` +
   `audit_slice.meta.json`. Surface `directives_blocked` entries (tags
   blocked for `n < min_tag_n` or sub-noise deltas) as first-class outcomes:
   "do not actuate" is the loop's expected steady state, not a failure. If
   `artifacts/weakness/weakness_refused.json` exists (signal firewall refused
   the payloads, builder exit 3), report the refusal as the round's outcome:
   the stale live report was quarantined to `weakness_report.refused.json`,
   NO live report is served, and the fix is payload/policy + a builder
   re-run — not an eval re-run. Remind
   the user to `git add` the git-tracked loop-memory files
   (`artifacts/loop/`, `artifacts/weakness/history/`, the two audit-slice
   files, `artifacts/response_matrix.jsonl`) so `clean.sh --all` cannot wipe
   loop history. When this eval measures a checkpoint produced under curator
   targeting directives, export `PREV_DIRECTIVES=<curator
   artifacts/targeting/directives_round_NNN.yaml>` (the newest
   `directives_file` in the hypothesis ledger) before launching — the ledger
   row then stamps `directives_sha256` and the round is attributable to its
   actuation; without it the row records null and the loop loses that
   provenance. For a placebo-arm
   round, the report was built with `scripts/build_weakness_report.py
   --placebo <seed>` — keep the arm assignment blinded in the summary.
   Build the identity index yourself (agent-side; `start.sh` never runs it):
   once the gold dataset exists under `artifacts/datasets/<base>/` (`-100`
   subsets map to the same base; it is auto-built by `analyze_job.sh`'s
   `prepare_dataset` step when `job_analysis` is enabled), run
   `python3 scripts/build_identity_index.py --dataset <base> --dataset-dir artifacts/datasets/<base> --out artifacts/bench_identity/<base>.identity.json`
   and report the entry count. This index is curator's decontam input (the
   `benchmark_identity_index` output edge).

## What `scripts/start.sh` does internally

1. Optional `update_repos.sh` (only with `--update-repos` /
   `EVAL_UPDATE_REPOS=1`).
2. Re-runs `scripts/dryrun.sh`, then
   `scripts/probe_llm_completion.sh`, as its own launch gate. Probe FAIL
   aborts. Probe WARN also aborts unless the user explicitly accepted
   the warning and the launch sets `EVAL_ALLOW_PROBE_WARN=1`.
3. Generates a per-job LiteLLM config from
   `runtime_info.input.{llm_api, litellm_proxy}` + the Harbor template
   into `artifacts/litellm/<job>/`, copying Harbor's
   `trajectory_logger.py` alongside it.
4. Starts the LiteLLM proxy (`serve_litellm.sh`) on
   `litellm_proxy.port`, waits up to 120 s for it to accept connections
   (cold/network-filesystem imports can take 30–60 s), and aborts if it
   exits early.
5. Builds the default Harbor command (`uv run harbor run --dataset <name>@<version> …
   --registry-path … --agent-import-path … --mounts-json … --model …`)
   with per-agent flags, `--retry-exclude AgentTimeoutError`, and one
   `--exclude-task-name` per entry in `HARBOR_EXCLUDE_TASKS`, then runs
   it inside `repos/harbor`, teeing to `artifacts/logs/evaluator_<ts>.log`.
   With `runtime_info.input.harbor_job.repeats: k > 1` (RSI loop), this
   Harbor section — proxy start, run, proxy stop — repeats k times
   **sequentially**, one fresh LiteLLM proxy per repeat (~30–60 s restart
   each; the proxy's trajectory root is fixed at fork time, so a single
   proxy must never be reused across repeats), yielding one job dir per
   repeat of the same `(checkpoint, dataset)` pair.
6. **RSI loop builder** (when `harbor_job.repeats > 1`; wired into the
   EXIT-trap path so a partial round still reports): after the repeat jobs,
   `scripts/build_weakness_report.py` joins the k repeat jobs by
   `result.json`'s `task_name` (never the 32-char trial-dir prefix),
   excludes audit-slice IDs from every aggregate, and writes
   `artifacts/weakness/weakness_report.json` (+ immutable `history/` copy,
   `artifacts/loop/ledger.yaml` row, `artifacts/response_matrix.jsonl` rows).
   CPU-only, zero tokens. The identity index is not built here — it is an
   agent-side step (procedure step 5).
7. **Post-eval job analysis** (unless
   `runtime_info.input.job_analysis.enabled: false`): calls
   `scripts/analyze_job.sh "$JOB_DIR"` to write `<job_dir>/analysis/` —
   the attribution/scoring artifacts the dashboard reads. **Non-fatal**: a
   failed analysis never fails a completed eval. Pure-CPU by default (LLM
   judge off → zero token cost). If the gold dataset is missing it
   auto-runs `scripts/prepare_dataset.sh <dataset_name>` first (adapter →
   tagger); the tagger uses `job_analysis.tag_llm` and needs a JSON-clean
   endpoint. See `/evaluator:dashboard` for browsing the results.

Use `bash scripts/start.sh --dry-run-command` to print the generated
LiteLLM config path and Harbor command **without** launching — useful in
the confirm step.

## Modes

| Mode | Trigger | What runs |
|---|---|---|
| `smoke` | Args mention "smoke"/"quick"; or `harbor_job.n_tasks` is a small int; or a `-100` benchmark subset is selected. | Standard `start.sh` flow; Harbor honours the `n_tasks` cap. A `-100` subset is the cleanest smoke. |
| `full`  | Args empty or say "everything"/"all tasks". | `start.sh` against the full benchmark. |

## Conventions to honour

- **Benchmark selection lives in `config.yaml`, not flags.** To change
  benchmark, edit `runtime_info.input.task_source.{dataset_name,version,registry_path,no_hack}`
  and re-run `/evaluator:check`. Refuse to override these via free-form args —
  launching a different benchmark than the one `:check` validated is
  never correct. `no_hack: true` only applies to `swebench-verified` — the
  `-100` subset is rejected (cap with `harbor_job.n_tasks` instead); start.sh
  remaps to the local hardened registry + agent allowlist.
  Terminal Curator's `terminal-curator@1.0` uses its exported local registry
  with `no_hack: false` and `job_analysis.enabled: false`; retain that registry
  path through preflight and launch. Keep the referenced immutable batch
  available, and do not start a curation run as an evaluation side effect.
- **Agent selection lives in `config.yaml`, not flags.** Switch agents by
  editing `agent.{name, version, runtime_image, runtime_host_path}`
  together and re-running `/evaluator:check` (so the runtime-extraction check
  re-runs). Don't inject agent overrides via args.
- **Excluded tasks come from `HARBOR_EXCLUDE_TASKS`.** Record them in
  `config.yaml`'s `runtime_info.input.env_extra.HARBOR_EXCLUDE_TASKS` so they
  survive across runs and appear in archives — don't pass exclusions via
  skill args.
- **One LiteLLM proxy per run.** If `:check` flagged the port as held by
  a foreign process, do not start a second proxy on a different port
  without the user explicitly OK-ing the config change.
- **Timed-out tasks are not retried.** `start.sh` always passes
  `--retry-exclude AgentTimeoutError`; a task that times out will time
  out again and only burns budget. If a task repeatedly times out, add it
  to `HARBOR_EXCLUDE_TASKS`.
- **Repeats are config, not flags.** `harbor_job.repeats` sets k; repeats
  multiply wall-clock and tokens ~linearly — surface that in the confirm
  step. Cross-repeat joins use `result.json`'s `task_name`; deltas that do
  not clear the measured noise floor are reported via `directives_blocked`,
  never actuated.
- **The audit slice is quarantined.** IDs in
  `artifacts/reserve/audit_slice.txt` never feed directive-deriving
  aggregates; promotion readouts read the slice only. Rotate it per the
  `slice_version` policy, do not grow it ad hoc.
- **Placebo arms stay blind.** When a round runs
  `build_weakness_report.py --placebo <seed>`, do not reveal which report
  was permuted in run summaries; unblinding is pre-registered.
- **Custom local model: vLLM must be up first.** When `llm_api.api_base_url`
  points at a local vLLM endpoint (a custom checkpoint, not a remote API),
  that vLLM must already be serving on the GPU node — start it with
  `scripts/serve_local_model.sh` in its own tmux session **before** `:run`,
  and keep it alive for the whole job. `start.sh` does not launch vLLM; it
  only starts the LiteLLM proxy in front of it. `/evaluator:check`'s endpoint
  probe runs `scripts/probe_llm_completion.sh` against the raw upstream;
  connection errors and 5xx responses FAIL if vLLM isn't healthy, so
  this is caught in preflight.

## Resume on interrupt

Harbor's per-job state under `artifacts/jobs/<job>/` lets an interrupted
run be re-driven. If a prior `evaluator` run was interrupted, surface it and
ask whether to resume that job dir vs. start a fresh one, rather than
silently launching a duplicate.

## Out of scope

- Editing `config.yaml`, building envs, or extracting agent runtimes —
  those belong in `/evaluator:setup`.
- Writing `artifacts/index.yaml` — owned by `archive_run.sh`.
