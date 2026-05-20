---
name: check
description: >
  Recursively sanity-check every block at and beneath the current working directory: config.yaml schema, runtime_info.input completeness, inter-block dependency resolution, repos pin matches, environment (venv_path) existence, remote resource (SSH/directory) reachability, and live availability of every OpenAI-compatible LLM endpoint declared in any block's runtime_info.input. Reports all failures in one consolidated message — does not execute scripts/start.sh, does not flip status.phase, does not write archives. Use before /block:run after a fresh clone or config edit, or to diagnose why a block is failing preflight. Triggers on phrases like "check my blocks", "validate the config", "sanity check everything", "are my API keys working", "is the remote reachable", "run /block:check", "diagnose this block".
---

# /block:check

Recursively walk the block tree rooted at the current working directory and validate every block's config, resources, and external APIs. **Read-only:** no scripts run, no `status.phase` flips, no archives written.

Use this before `/block:run` to surface every missing input, broken dependency, unreachable remote, or dead API key in one pass — instead of failing preflight one block at a time.

## Step 0 — Orient

Read `references/BLOCK_DEFINITION.md` bundled in this plugin (sibling of the `skills/` folder containing this file). It is the contract — pay particular attention to:

- The `meta_info` / `runtime_info` / `status` schema and the **wiring rule** (inter-block values live in `meta_info.subblocks[<child>].dependencies` formatted `<source_block>.output.<key>` or the literal `human`; `runtime_info.input` is exclusively for values originating outside the block tree).
- The **remote-execution rule** (if `meta_info.resources.ip` is set, the block runs on that host — so its environment and repos must exist there, not locally).

## Step 1 — Discover the block tree

Starting at CWD, decide where to begin:

1. If `./config.yaml` exists, this directory is the root of the check. Read it and recurse into every child named under `meta_info.subblocks` by descending into `./subblock/<name>/`.
2. Else if `./subblock/` exists and contains child block directories (each with its own `config.yaml`), treat CWD as a pseudo-root (the SWE-Lego-Live pattern: a coordinator with no config.yaml of its own). Check each child as an independent block; do **not** synthesize a config for the parent.
3. Else, abort: "This directory is not a block (no `config.yaml`) and has no `subblock/` children. Run `/block:check` from inside a block's directory or from a directory whose `subblock/` contains blocks."

Build a flat list `[(block_path, parsed_config_yaml)]` of every reachable block. Record any declared subblock whose directory is missing as a `tree:missing-child` failure on its parent.

## Step 2 — Per-block static checks

For each discovered block, run every check below. **Never abort early** — collect failures across every block, every check; report them all in Step 4.

| # | Check | Failure label |
| - | ----- | ------------- |
| 1 | `config.yaml` parses as YAML. | `schema:parse-error` |
| 2 | Top-level sections present: `meta_info`, `runtime_info`, `status`. (`evolving` is optional.) | `schema:missing-section` |
| 3 | `meta_info.name` is a non-empty string and matches the directory name. | `schema:name-mismatch` |
| 4 | Every key under `runtime_info.input` has a non-null, non-empty value. Recurse into nested objects — every leaf must be filled. Treat obvious placeholders (`YOUR_API_KEY`, `xxx`, `<...>`, `changeme`, `ghp_YOUR_TOKEN_HERE`) as unfilled. | `input:unfilled` / `input:placeholder` |
| 5 | For each `child` in `meta_info.subblocks`, for each `dep_key: dep_value` in `subblocks[child].dependencies`: if `dep_value` is the literal `human`, then this block's `runtime_info.input.<dep_key>` must be non-null. Otherwise `dep_value` parses as `<src>.output.<key>` and `subblock/<src>/config.yaml`'s `runtime_info.output.<key>` must be non-null. | `dep:unresolved` |
| 6 | For each entry under `meta_info.repos`: `./repos/<name>/` exists. If a `commit_id` is pinned, the checked-out HEAD matches it. Treat a submodule gitlink whose recorded SHA matches `commit_id` as a pass even if the working tree isn't materialized. | `repo:missing` / `repo:pin-drift` |
| 7 | If `meta_info.environment.venv_path` is set: that path exists on the host that will run the block — local if no `meta_info.resources.ip`, remote (`ssh <ip> test -d <path>`) if set. | `env:venv-missing` |
| 8 | If `meta_info.resources.ip` is set: `ssh -o BatchMode=yes -o ConnectTimeout=5 <ip> true` succeeds. If `meta_info.resources.directory` is also set, also verify `ssh <ip> test -d <directory>`. | `resource:ssh-unreachable` / `resource:dir-missing` |
| 9 | `scripts/start.sh` exists (warning, not failure — a block may be a coordinator-only parent). | `scripts:no-start` (warning) |

## Step 3 — API availability (across all blocks)

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

## Step 4 — Report

Print one consolidated report. Lead with the tree shape, then per-block status, then a summary, then concrete next steps. Use this layout:

```
Block tree (CWD = <path>):
  <path>            (no config.yaml — pseudo-root)
  └─ subblock/swegen
      ✓ all checks
      ✓ api(llm_api) → https://endpoint/v1   models: pr_model, task_model present
  └─ subblock/trajgen
      ✗ input:unfilled       runtime_info.input.llm_api.api_key
      ✗ api:auth-failed      llm_api → https://endpoint/v1  (401: invalid api key)
      ✗ resource:ssh-unreachable   192.168.35.240  (ssh: connect timeout)

Summary: 2 blocks checked · 1 healthy · 1 with 3 failures.

Next steps:
  1. subblock/trajgen/config.yaml → runtime_info.input.llm_api.api_key — fill it.
  2. Verify 192.168.35.240 is up and your SSH key is loaded (ssh-add -l).
  3. Re-run /block:check.
```

Rules for the report:

- One row per failure; collapse passes into a single ✓ line per block when nothing is wrong.
- For `api:*` failures, include the URL and the HTTP status / error string verbatim so the user can paste it into their endpoint dashboard.
- For `dep:unresolved`, name both the consumer (`subblock/<child>.<dep_key>`) and the producer (`subblock/<src>.output.<key>`).
- Warnings (e.g. `scripts:no-start`, `api:no-models-endpoint`) print as `⚠` and do **not** count toward the failure total.
- If every block is clean, end with: `All blocks healthy — safe to /block:run.`

## Step 5 — What this skill must NOT do

- Do not edit any `config.yaml`, `status.phase`, or any other file.
- Do not run `scripts/start.sh`, `scripts/dryrun.sh`, or any script under a block.
- Do not call chat-completion or embeddings endpoints — only `/models`.
- Do not skip failed checks just because the user said "ignore that" — re-run after they fix it instead.
- Do not invent values to "satisfy" a check (e.g. don't substitute env vars for null inputs). Surface the gap; let the user fill it.
