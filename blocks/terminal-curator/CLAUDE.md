# Terminal Curator Agent Workbench

This directory is the `terminal-curator` data block, parallel to the SWE
`curator` block. Read the repository's
`.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md`, this file,
`config.yaml`, and `dashboard/overview.mdx` before operating it. `AGENTS.md`
points here so Claude Code and Codex follow one contract.

## Purpose and boundary

Turn a **bounded** source batch into verified Harbor terminal tasks for Tracer
and a native local registry for Evaluator.
The source is a Stack Overflow query, a Terminal-Lego questions JSON file, or
local candidate task directories. The block adapter reuses the pinned
`repos/terminal-lego` generator and normalizes its output. It does not run the
upstream `run_pipeline.sh`: that script is an open-ended, human-operated loop.

The hand-off is `artifacts/tasks/`, gated by
`artifacts/tasks/verifiable_tasks.txt`. Publish a task only after **Oracle
reward = 1 and NOP reward = 0**, in separate fresh Harbor environments, with no
Harbor trial exception. Preserve rejected candidates and validation evidence
under `artifacts/`; a generated directory alone is never verified output.
Passing this gate establishes a runnable task and a nontrivial verifier. It
does not establish difficulty or broad test quality.

Before handing a task to Tracer or Evaluator, the operating agent must also
audit the written instruction, tests, and reference solution for agreement.
Every assertion must follow from the stated contract, and required behavior
must be exercised. Verify command outputs, flags, exit status, and filesystem
effects by execution; lexical source checks alone cannot establish behavior.
Correct unstated output requirements or an inaccurate reference solution in
a candidate copy, preserve the changes in provenance, and rerun fresh Oracle
and NOP validation before forwarding the corrected publication.

The block does not launch Tracer, training, evaluation, or an unlimited crawler.
Those are separate skills and runs. A successful rollout still needs an actual
agent trajectory and a verifier reward in Tracer.

## Configuration and credentials

`config.yaml` has only `meta_info` and `runtime_info`. It is read once per run.
The loaded configuration is frozen as a redacted snapshot at run start; later
profile edits do not rewrite that run's recorded configuration.
The user supplies the source, finite limits, LLM endpoint/model, and resource
host in `runtime_info.input` / `meta_info.resources`; progress belongs in
`artifacts/state.json`, `artifacts/reports/latest.json`, and `artifacts/index.yaml`.

Read [the configuration reference](docs/content/docs/configuration.mdx) for
field meanings. The adapter accepts `--config <path>`; `setup.sh`, `dryrun.sh`, `start.sh`, and `archive_run.sh`
accept the same option so a private local overlay can be used without changing
the tracked defaults. Confirm the resolved source, limits, host, output pool,
model, and concurrency before running.

Use environment variables for secrets:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | LLM access; needed for `stackoverflow` / `json` generation. |
| `OPENAI_API_BASE_URL` (`OPENAI_API_BASE` also supported) | LLM endpoint override. |
| `MODEL_NAME` | Generation model override. |
| `SO_API_KEY` | Optional Stack Exchange API key. |
| `SO_API_BASE_URL` | Stack Exchange API base override. |

Leave tracked `llm_api.api_key` empty. Do not put keys in shell arguments,
question JSON, source task metadata, reports, dashboard payloads, or commits.
Run archives redact configuration secrets; inspect generated artifacts before
sharing them. An absent LLM key is normal for `source.type: tasks`.

## Operator interface

The canonical skills live in `.claude/plugins/terminal-curator-plugin/skills/`.
Codex entry skills under `<repo_root>/plugins/terminal-curator/skills/` read
those files.

| Workflow | Claude Code | Codex | Script |
| --- | --- | --- | --- |
| Bootstrap | `/terminal-curator:setup` | `$terminal-curator-setup` | `bash scripts/setup.sh` |
| Preflight | `/terminal-curator:check` | `$terminal-curator-check` | `bash scripts/dryrun.sh` |
| Bounded run | `/terminal-curator:run` | `$terminal-curator-run` | `bash scripts/start.sh` |
| Progress | `/terminal-curator:dashboard` | `$terminal-curator-dashboard` | `artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py` |

From this block directory:

```bash
bash scripts/setup.sh
bash scripts/dryrun.sh
# After checking the configuration and the user's authorization:
bash scripts/start.sh
artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py --html
```

`setup.sh` bootstraps `artifacts/env/terminal-curator`, initializes the pinned
submodule, and prepares Harbor through Tracer's pinned checkout/environment.
The default Harbor executable is
`../tracer/artifacts/env/harbor-uv/bin/harbor`. Do not replace Harbor with an
unrelated system install: validation and downstream rollout must use the
supported schema/runtime. No environment or generated data belongs in `repos/`.

`dryrun.sh` is read-only. It checks the schema, source, repository pin, tools,
Docker reachability, and source-dependent inputs. `terminal_curator.py probe`
performs live service probes; report the actual result and do not infer a
working completion endpoint from a model catalog response.

Before `start.sh`, run the check and present its results. Reuse explicit
authorization already given for the same bounded operation; if the requested
source, budget, host, or scope is unclear, clarify it before running. New
large runs need the user's confirmation. Execute on the configured host in a
named tmux session for a long run. A remote resource means SSH + tmux there;
resolve code synchronization before launch unless already authorized.

## Run evidence and recovery

Published batches are immutable copies under `artifacts/tasks-versions/<id>/`.
`artifacts/tasks` is an atomically updated symlink to the newest passing
batch, containing its manifest and `registry.json`. Tracer resolves that
version before staging,
so a later publication does not retarget an existing rollout's task links.
Keep old versions while downstream runs refer to them.

`start.sh` runs a finite batch and archives on EXIT. It must fail if the batch
produces no verified tasks. Each trial is linked by Harbor's task checksum;
the same normalized bytes are checked before and after both jobs and in the
actual copied publication. `harbor_task_checksums` records this identity.
`task_checksums` is a separate block file digest and cannot substitute for it.
Inspect the exact per-run report, the manifest, and the
Oracle/NOP job results before reporting success. Preserve the failed candidate
and its evidence; fix the block adapter or source, then rerun only the affected
batch. Do not relax the verifier, publish a failed task, or count a missing
result as a zero reward.

An LLM test review must return a JSON object with a real boolean `pass: true`
and `issues: []` before its generated tests can be accepted. Missing issues,
a non-list, non-string entries, contradictory reported issues, malformed or
negative review, and string-valued approval all reject the fallback candidate.
Complete the agent's own per-requirement semantic audit after the runtime
checks. If a repair is needed, copy the affected task out of the immutable
batch, keep its source provenance, and record the instruction/test mapping,
findings, edits, behavioral checks, and reviewer in the candidate's
`provenance.json` and run notes. Import that copy through `source.type: tasks`
and require both new fresh-container trials to pass. Record their run IDs
before using the final manifest or registry downstream.

`scripts/stop.sh` stops this block's recorded run. `scripts/clean.sh` cleans
temporary `artifacts/runs/` trees while retaining published versions, the
output symlink, environments, reports/state, and archives. Preview with
`--dry-run`; deleting run trees also removes detailed source/candidate/job
evidence, so retain any material needed for diagnosis before cleanup.
`--all --yes` requires an explicitly authorized full cleanup. Normal cleanup
also removes the per-run frozen-config copies; matching run archives retain
their copied evidence.
The worker writes a redacted loaded-config snapshot to
`artifacts/runs/<runtime-id>/config.yaml`. The launcher passes that exact
runtime ID to archival; `artifacts/index.yaml` distinguishes its sequential
`id: run_NNN` from `run_id`, and copies only the matching per-run report.
It does not adopt whichever report is newest after waiting for a lock.
Archival failure is surfaced as a run failure. For a manual archive with a
specific report, pass its explicit `--run-id`; without one, an archive does
not claim an existing run report. A fresh checkout has no run index.

Stop verifies both PID and process start identity using private
`run.process.json`, captures the owning run before termination, and cleans
only its Compose resources. This process metadata is not dashboard data.
Cleanup checks launcher, run and archive locks so it cannot remove evidence
between worker exit and the EXIT archive. The launcher holds `launch.lock`
through preflight, execution and archival. Script code and the effective
redacted configuration are frozen at run start; private profiles, hidden
files and symlink targets are excluded from the code snapshot. After a
worker is forcibly killed, stop can still recover its exact recorded
Compose sidecars from `run.process.json` without adopting a later run.

Treat `repos/terminal-lego` and Tracer's `repos/harbor` as read-only pinned
dependencies. Put compatibility code in `scripts/`. Any proposed vendored-code
change follows the explicit warning and confirmation procedure in the block
definition; never hide a dirty submodule behind a successful run.

## Progress and dashboard

Generation records `collected_questions`, `generation_completed` (including
failed attempts), `generation_failed`, successful `candidate_ids`, and bounded
per-task stage/status updates after each completed future. A single failed
candidate is recorded and does not discard successful siblings. Imported
candidates have `collected_questions: null`, not zero collected questions.
The generation retry budget means **extra retries**: `max_retries: 2` allows
at most three API attempts per call.

The Curator-style board has Overview, Task List, Validation and Runs views.
It reads only allowlisted metadata from live state, matching reports,
immutable batches, archived run summaries and contained trial/JUnit files.
Static exports are snapshots and never poll a live API; the local server
refreshes data without resetting the selected view or filters. Unavailable
metadata remains unavailable. Runtime pass, semantic review and successful
solving are distinct claims. See `dashboard/README.md` for the exact scopes.
The docs demonstration is declared synthetic and is never populated from a
real local run.

## Downstream contract

Use the canonical plugin workflow for generation and semantic audit before
starting a separate Tracer run. The legacy root shell refuses implicit
terminal-to-Tracer execution so it cannot skip this audit; explicit
terminal-only and tracer-only commands remain separate operator actions.

Tracer selects this producer with:

```yaml
task_source:
  provider: local
  local_block: terminal-curator
  terminal_tasks_dir: ../terminal-curator/artifacts/tasks
```

Keep the existing SWE `dataset_name` setting: the source resolver selects the
terminal pool when `local_block` is `terminal-curator`. Both blocks declare
the conditional edge in `meta_info.dependencies`. Tracer stages only IDs in
the nonempty `verifiable_tasks.txt`; do not scan raw candidates into a rollout
or fall back to an unverified terminal pool when the manifest is missing.

Evaluator consumes the same accepted tasks through
`runtime_info.output.eval_registry.path` (`artifacts/tasks/registry.json`).
The registry is generated in the immutable batch before publication. Its
`terminal-curator@1.0` entry contains only manifest-listed task IDs and their
absolute versioned paths; it contains no credentials or remote task URLs.
Both block configs mirror the conditional edge to Evaluator's
`task_source.registry_path` when `dataset_name: terminal-curator` is selected.

Keep Evaluator's `provider: harbor_registry`; select `dataset_name:
terminal-curator`, `version: "1.0"`, and
`registry_path: ../terminal-curator/artifacts/tasks/registry.json`. Set
`no_hack: false`, because that setting remaps only SWE-bench Verified, and
`job_analysis.enabled: false`, because SWE gold-dataset analysis does not
describe these terminal verifiers. Run Evaluator separately on a host that
can read the versioned task paths, with its own configured endpoint/model.
Inspect per-trial rewards and trajectories before reporting evaluation
success. See [Evaluator hand-off](docs/content/docs/evaluator-handoff.mdx).

See [workflow and hand-off](docs/content/docs/workflow.mdx) and
[outputs](docs/content/docs/outputs.mdx). Keep the human-facing overview
accurate and record durable implementation observations in `memory/notes.md`.
