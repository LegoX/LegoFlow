---
name: check
description: >
  Recursively sanity-check every block at and beneath the current working directory: config.yaml schema, runtime_info.input completeness, inter-block dependency resolution, repos pin matches, environment (venv_path) existence, remote resource (SSH/directory) reachability, live availability of every OpenAI-compatible LLM endpoint declared in any block's runtime_info.input, AND block-specific dryrun validation (scripts/dryrun.sh — GPU, Docker, WANDB, model compatibility, etc.). Reports all failures in one consolidated message with a run configuration summary. Does not execute scripts/start.sh, does not modify any config, does not write archives. **MANDATORY before /root:run** — the agent must run this skill AND receive explicit user confirmation before launching any block. Triggers on phrases like "check my blocks", "validate the config", "sanity check everything", "are my API keys working", "is the remote reachable", "run /root:check", "diagnose this block".
---

# /root:check

Recursively walk the block tree rooted at the current working directory and validate every block's config, resources, external APIs, and block-specific runtime prerequisites. **Safe to run:** only `scripts/dryrun.sh` (a side-effect-free validation script) is executed — no `scripts/start.sh`, no config edits, no archives written.

**This skill is MANDATORY before `/root:run`.** The agent must:
1. Run `/root:check` to surface all issues in one pass.
2. Present the configuration summary and check results to the user.
3. **Wait for explicit user confirmation** ("yes", "go ahead", etc.) before launching `/root:run`.

Never skip the confirmation step — even if all checks pass. Heavy operations (GPU training, multi-hour jobs) are expensive and hard to reverse once started.

## Arguments

The args string is free-form natural language (may be empty). The agent reads the whole string holistically — no token parsing — to decide **which block to check** and **how to focus the report**.

### Target resolution

Same rule as `/root:run`. List `./blocks/` for valid names, then read args and decide:

- **Single block clearly identified** (literal name or unambiguous paraphrase) → `TARGET_DIR=./blocks/<name>/`. Check that block only — no recursion into its children, no walking of siblings.
- **Multiple blocks mentioned or ambiguous** → ask. Do not guess.
- **No block mentioned** → `TARGET_DIR=CWD` (full block tree walk).
- **Block inferred but `./blocks/<name>/` doesn't exist** → abort with the actual listing and ask the user to pick.

When `TARGET_DIR` is a block, cross-block dependency checks (the validator's `dep:*` checks) still read sibling configs to validate producer outputs — but don't recursively check those siblings' health.

### Report focusing

Any extra context in args (beyond identifying the block) is a hint for **how to present the report** — not what to scan. `/root:check` always runs every check; the instruction only shapes which findings lead and which collapse to a one-line tail. If the hint is ambiguous, default to the full report.

### Examples

| Args | Target | Behavior |
| ---- | ------ | -------- |
| *(empty)* | root | full report across all blocks |
| `curator` | curator | full report for curator |
| `tracer focus on api connectivity` | tracer | lead with `api:*` findings |
| `are my api keys working` | root | full tree; lead with `api:*` findings |
| `check curator for missing inputs` | curator | lead with `input:*` / `schema:*` findings |
| `compare curator and tracer configs` | ambiguous | ask which block |
| `check frobnicator` | abort | print valid list, ask user to pick |

## Step 0 — Orient

Read `resources/BLOCK_DEFINITION.md` bundled in this plugin (sibling of the `skills/` folder containing this file). It is the contract — pay particular attention to:

- The `meta_info` / `runtime_info` schema and the **wiring rule** (`meta_info.dependencies` has two keys, `from` and `to`, both always present. `from`: this block's own upstream — keys are dot-paths into that block's `runtime_info.input`, values are `<source_block>.output.<key>` strings or `{from, when, required}` mappings. `to`: this block's own downstream, the mirror declared by the producer — keys are this block's own `runtime_info.output` keys, values are `<consumer>.input.<path>` strings or `{to, when}` mappings with fully-qualified `<consumer>.input.<path>` when-keys. The same edge is declared on both ends; the validator cross-checks them (`dep:link-mismatch`). `runtime_info.input` is exclusively for values originating outside the block tree). `config.yaml` has exactly two top-level sections — `status:` and `evolving:` are retired; live state lives in `artifacts/index.yaml`.
- The **fill markers** (`human` = must-fill, `""` = auto/env-supplied) and the output `path`/`value` shape.
- The **remote-execution rule** (if `meta_info.resources.ip` is set, the block runs on that host — so its environment and repos must exist there, not locally).

## Step 1 — Discover the block tree

Starting at `TARGET_DIR` (resolved in the Arguments section), decide where to begin:

1. **If `block_name` was passed** (`TARGET_DIR=./blocks/<block_name>/`): treat that directory as the single block under check. Its `config.yaml` must exist — if not, abort: `"blocks/<block_name>/config.yaml not found."`. Do **not** recurse into its `meta_info.blocks` (leaf scope by user choice). Skip cases 2 and 3 below.
2. **No `block_name`, and `./config.yaml` exists**: this directory is the root of the check (the standard LegoFlow case — the root block has its own `config.yaml` listing children under `meta_info.blocks` with roles only). Read it and recurse into every child named under `meta_info.blocks` by descending into `./blocks/<name>/`.
3. **No `block_name`, no `./config.yaml`, but `./blocks/` exists** with child block directories (each with its own `config.yaml`): treat CWD as a pseudo-root (fallback for trees without a root config). Check each child as an independent block; do **not** synthesize a config for the parent.
4. **None of the above**: abort: `"This directory is not a block (no config.yaml) and has no blocks/ children. Run /root:check from inside a block's directory or from a directory whose blocks/ contains blocks."`.

Build a flat list `[(block_path, parsed_config_yaml)]` of every reachable block — exactly one entry when `block_name` is set, more when walking the full tree. Record any declared block whose directory is missing as a `tree:missing-child` failure on its parent (only applicable when walking the tree).

## Step 2 — Per-block static checks

Schema, fill-marker, and dependency validation is owned by the shared validator. Run it once for the whole scope and map its output lines directly into the report:

```bash
# full-tree scope (TARGET_DIR is the root):
python3 <repo_root>/scripts/validate_config.py --root <repo_root>
# single-block scope:
python3 <repo_root>/scripts/validate_config.py --block <block_path>
```

Every `[FAIL] <label> ...` / `[WARN] <label> ...` line becomes one finding row, keyed by its label (`schema:parse-error`, `schema:missing-section`, `schema:legacy-status`, `schema:legacy-evolving`, `schema:unknown-toplevel`, `schema:name-mismatch`, `schema:root-wiring`, `tree:missing-child`, `dep:bad-shape`, `dep:bad-key`, `dep:bad-ref`, `dep:unresolved`, `dep:path-mismatch`, `dep:link-mismatch`, `input:unfilled`, `input:placeholder`, `output:shape`). Include the validator's message verbatim.

Then run the checks the validator cannot judge. **Never abort early** — collect failures across every block, every check; report them all in Step 4.

| # | Check | Failure label |
| - | ----- | ------------- |
| 1 | For each entry under `meta_info.repos` **or** `meta_info.repositories`: `./repos/<name>/` exists. If a pin is provided under `commit_id`, `commit`, or `pinned_commit`, the checked-out HEAD matches that pinned SHA. Treat a submodule gitlink whose recorded SHA matches the pinned SHA as a pass even if the working tree isn't materialized. | `repo:missing` / `repo:pin-drift` |
| 2 | If `meta_info.environment.venv_path` is set: that path exists on the host that will run the block — local if `meta_info.resources.ip` is absent or set to `local`, remote (`ssh <ip> test -d <path>`) only if `meta_info.resources.ip` is set to a non-local host. | `env:venv-missing` |
| 3 | If `meta_info.resources.ip` is set to a non-local host: `ssh -o BatchMode=yes -o ConnectTimeout=5 <ip> true` succeeds. If `meta_info.resources.directory` is also set, also verify `ssh <ip> test -d <directory>`. If `meta_info.resources.ip` is absent or equal to `local`, treat the block as local and do **not** perform SSH reachability checks. | `resource:ssh-unreachable` / `resource:dir-missing` |
| 4 | `scripts/start.sh` exists (warning, not failure — a block may be a coordinator-only parent). | `scripts:no-start` (warning) |
| 5 | **Cloudflare credentials — shared, optional** (root scope; the root `scripts/dryrun.sh` runs this): `scripts/shared_credentials.sh` resolves `account_id` + `api_token` in the order **env > root `config.yaml` → `runtime_info.input.cloudflare` > the block's legacy env file**, and the root dryrun reports which of those three won. When both resolve it also **live-probes the token** against `https://api.cloudflare.com/client/v4/user/tokens/verify` — so distinguish the two failure modes in the report: *not configured* (nothing set anywhere) vs *configured but rejected* (a real, actionable credential problem). Half-configured (only one of the two) is called out separately. Always a warning, never a failure. | `env:cloudflare-config` (warning) |
| 5b | **Cloudflare — per-block dashboard publishing** (optional, surfaced automatically): every block reads the same shared credentials from row 5, so a single root-level `runtime_info.input.cloudflare` now covers all of them; the per-block legacy env files (curator: `swegen_progress_cloudflare.env`, tracer: `trajgen_progress_cloudflare.env`, evaluator: `harbor_webui_cloudflare.env`, under `$SWEGEN_HOME/.config/`) still work and take last priority. What remains genuinely per-block is the **toolchain**: curator/tracer/evaluator each verify `npx`/node in their own `dryrun.sh`. Tracer and evaluator publish via a long-running `dashboard/run_cloudflare_pages_sync.sh` loop; curator via a manual `wrangler pages deploy` from `dashboard/site/`. Trainer needs no account at all — it checks for a `cloudflared` binary (anonymous quick tunnel) and reports the shared credentials as informational only. Step 3 below already folds every block's `dryrun.sh` output into the report. Always a warning, **never required** — local HTML dashboards work regardless. Fix: `/root:setup`'s optional Cloudflare extra. | `dryrun:warn` (warning, via each block's own dryrun.sh) |
| 6 | **Container registry auth** (root scope; the root `scripts/dryrun.sh` runs this): two independent signals, report both. (a) shared credentials — `username`/`password` resolved from env or root `config.yaml` → `runtime_info.input.docker`, usable via `bash scripts/docker_login.sh`; (b) an existing login — `~/.docker/config.json` has a `docker.io` auth entry or a credential store. Either one satisfies the requirement. Anonymous pulls are capped at **100 per 6h per IP**; curator/tracer/evaluator pull task + agent-runtime images and can hit the cap mid-job, surfacing as agent/verifier failures. Neither present → warning, but **escalate it to the top of the report whenever curator, tracer or evaluator is in scope** — advise `bash scripts/docker_login.sh` or a plain `docker login` before launching. | `env:docker-auth` (warning) |

## Step 3 — Block-specific dryrun (scripts/dryrun.sh)

For each discovered block, check if `scripts/dryrun.sh` exists and is executable. If it does, run it:

```bash
bash <block_path>/scripts/dryrun.sh 2>&1
```

`dryrun.sh` is a **side-effect-free** validation script that each block may provide to check runtime prerequisites that go beyond config schema — for example:

- GPU availability and count (`nvidia-smi`)
- Docker daemon reachability (local socket or remote TCP)
- Model/TP compatibility (e.g. `gen_tp` divides `num_key_value_heads`)
- Credential availability (`WANDB_API_KEY`, etc.)
- venv integrity (correct editable installs)
- Upstream script presence

Capture the full stdout/stderr of `dryrun.sh`. Parse its output for:
- Lines containing `OK` → pass
- Lines containing `MISSING` → record as `dryrun:missing` failure
- Lines containing `WARN` → record as `dryrun:warn` warning
- Non-zero exit code → record as `dryrun:failed`

Also capture the **Run Configuration Summary** block that `dryrun.sh` typically prints (delimited by `===` lines). This summary is included verbatim in the Step 5 report for the user to review before confirming a run.

If `scripts/dryrun.sh` does not exist, record `dryrun:no-script` as a warning (not a failure) and skip this step for that block.

## Step 4 — API availability (across all blocks)

Walk every block's `runtime_info.input` and identify entries that look like an LLM endpoint: an object whose leaves include `api_base_url` (or `base_url`/`endpoint`) AND `api_key`. Typical key names: `llm_api`, `openai_api`, `anthropic_api`. For each one:

1. **Placeholder check** — if `api_key` is empty, looks like `YOUR_API_KEY` / `sk-xxx` / `<...>`, or `api_base_url` is not a syntactically valid URL: record `api:placeholder` and skip the live probes for that endpoint.
2. **Live reachability** — `GET <api_base_url>/models` with `Authorization: Bearer <api_key>`, 5s connect + 10s read timeout. Decisions:
   - 2xx → `✓ api:reachable`
   - 401/403 → `api:auth-failed` (include status code and the response body's first line)
   - 404 → `api:no-models-endpoint` (note: endpoint reachable but doesn't expose `/models` — record as a warning, not a hard failure, since some OpenAI-compatible proxies don't implement it)
   - Connection refused / DNS failure / timeout / 5xx → `api:unreachable` (include the underlying error verbatim)
3. **Model presence** — for every sibling key in the same input object whose name contains `model` (e.g. `pr_model`, `task_model`, `model`), check the configured value appears in the `data[].id` list of the `/models` response. If `/models` succeeded:
   - Exact match → pass
   - Missing → `api:model-missing` (suggest the closest available model name as a hint)

Do **not** issue chat-completion or embeddings calls — `/models` is sufficient and free. If you can't tell whether an input object is an LLM endpoint (different shape, non-OpenAI-compatible provider), record it as `api:skipped` with the keys you saw, and let the user confirm.

## Step 5 — The report (always the last thing you print)

The report **is** the deliverable. Print it every single time — even on
an abort (then: heading + a `NO` verdict whose reason is the abort
message, nothing else). Fill this template exactly; drop only truly
inapplicable rows. Each per-block row set mirrors the same
Layer/Check/Status/Detail shape that block's own `:check` skill uses,
so a `/root:check` report and a `/<block>:check` report never disagree
on glyphs or wording — this is a rollup, not a second opinion.

````
## root block check — CWD=<path>

**SAFE TO RUN: <✅ YES | ❌ NO>** — <R> required · <A> advisory · <W> warnings, across <N> blocks

Block tree:
  <path>            (no config.yaml — pseudo-root, or root config.yaml)
  └─ blocks/curator
  └─ blocks/tracer
  └─ blocks/trainer
  └─ blocks/evaluator

| Block | Layer | Check | Status | Detail |
|-------|-------|-------|:------:|--------|
| curator  | det | schema · inputs · repos · dryrun | ✓ | ok=<N> |
| tracer   | det | <each FAIL/WARN check> | <✗/⚠> | <verbatim validator/dryrun line> |
| trainer  | det | dryrun:missing | ✗ | WANDB_API_KEY not set |
| trainer  | det | dryrun:warn | ⚠ | port 2375 is unencrypted |
| *        | det | api(<key>) → <url> | <✓/⚠/✗> | <models: <fields> present \| auth-failed \| unreachable> |

**Run configuration** (one block per fenced block, only for blocks with a dryrun Run Configuration Summary)
```
blocks/trainer:
  Model:        /path/to/models/Qwen3-30B-A3B-Instruct-2507
  Backend:      Docker (unix:///var/run/docker.sock)
  Parallelism:  16 workers
  Batch size:   64 × 8 = 512 trials/step
  Algorithm:    grpo / gspo  lr=1e-06
```

**Next steps**
1. <one per failure, required first; quote the validator/dryrun line verbatim>
2. Re-run `/root:check`.
3. Once **SAFE TO RUN: ✅ YES** → confirm to proceed with `/root:run`.
````

**The three invariants:**

1. **Verdict** — `✅ YES` iff `R == 0` across every checked block, where
   `R` = the sum of each block's own required-failure count (`schema:*`,
   `dep:*` unresolved, `dryrun:missing`/`dryrun:failed`, `api:auth-failed`,
   `api:unreachable`, `api:model-missing`). Warnings (`scripts:no-start`,
   `api:no-models-endpoint`, `dryrun:warn`, `env:cloudflare-config`,
   `env:docker-auth`) *never* change it.
2. **Glyphs** — `✓` pass · `✗` blocks · `⚠` advisory/warning · `·` skipped.
3. **Collapse** — fold all passing checks into one ✓ row per block; add
   a row only for each check that is `✗` or `⚠`, or for a resolved
   `api:*` endpoint (always shown once per endpoint, pass or fail).

Additional rules:

- For `api:*` failures, include the URL and the HTTP status / error string verbatim so the user can paste it into their endpoint dashboard.
- For `dep:*` findings, name both the consumer (`blocks/<block>` + the `runtime_info.input` dot-path) and the producer (`blocks/<src>.output.<key>`) — the validator's message already contains both.
- For `dryrun:*` failures/warnings, include the exact line from dryrun.sh output.
- If a block's `dryrun.sh` printed a Run Configuration Summary, include it verbatim in the report so the user can review the full configuration before confirming.
- **Never proceed to `/root:run` without explicit user confirmation**, even when the verdict is `✅ YES`.

## Step 6 — What this skill must NOT do

- Do not edit any `config.yaml` or any other file.
- Do not run `scripts/start.sh` or any script that has side effects.
- `scripts/dryrun.sh` is the ONLY script this skill may execute (it is side-effect-free by design).
- Do not call chat-completion or embeddings endpoints — only `/models`.
- Do not skip failed checks just because the user said "ignore that" — re-run after they fix it instead.
- Do not invent values to "satisfy" a check (e.g. don't substitute env vars for null inputs). Surface the gap; let the user fill it.
- **Do not proceed to /root:run without explicit user confirmation**, even if all checks pass.
## Shared LegoFlow CLI

The canonical execution command for this skill is `./bin/legoflow check --full`. Claude Code and Codex use this same command; this skill supplies the agent-specific confirmation, reporting, and artifact-analysis workflow around it.
## LegoFlow Command Convention

The canonical command for this skill is `/root:check`. The shared CLI accepts the same command as `./bin/legoflow /root:check` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
