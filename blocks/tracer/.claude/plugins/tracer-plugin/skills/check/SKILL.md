---
name: check
description: >
  Preflight the tracer block. Validates config.yaml schema; verifies
  Harbor / swe_data_process pinned commits match (and accepts tracked
  submodule status, not just gitignore); confirms the three uv/venv
  environments exist with the expected editable installs; live-probes
  the configured LLM endpoint with `GET /models` and (when
  `task_source.provider: huggingface`) probes HF dataset reachability;
  confirms LiteLLM proxy port is free or held by current user; verifies
  the agent runtime_image is present on the local Docker daemon; sanity-
  checks `artifacts/processed_tasks.yaml` and cross-checks every
  done/failed/skipped entry against the resolved exclusion set. Read-only.
  Reports all failures in one pass. Triggers on phrases like "check
  tracer", "preflight tracer", "is tracer ready", "diagnose tracer",
  "validate tracer config".
---

# /tracer:check

Read-only preflight. Wraps `scripts/dryrun.sh` and adds the few
contextual checks that need conversational nuance (e.g. interpreting CF-
gating 401s, flagging stale memories, deciding whether a WARN is worth
escalating).

## How to run

1. Execute `bash scripts/dryrun.sh` from the block root. The script is
   the single source of truth for what "tracer is ready" means — it
   covers schema, repos/commits, all three envs, LLM endpoint live
   probe, HF dataset auth probe, port-4001 ownership, Docker image
   presence, ledger validity, and how many task ids
   `HARBOR_EXCLUDE_TASKS` resolves to.
2. Fold every `dryrun.sh` line into the Step 3 report below — never
   re-run a probe or overrule an `OK`.

## Step 3 — The report (always the last thing you print)

The report **is** the deliverable. Print it every single time — even
on an abort (then: heading + a `NO` verdict whose reason is the abort
message, nothing else). Fill this template exactly; drop only truly
inapplicable rows.

````
## tracer block check — CWD=<relative path>

**SAFE TO RUN: <✅ YES | ❌ NO>** — <R> required · <A> advisory · <W> warnings

| Layer | Check | Status | Detail |
|-------|-------|:------:|--------|
| det  | schema · repos/commits · harbor-uv · litellm-venv · swe-data-process-uv | ✓ | ok=<N> |
| det  | <each FAIL/WARN det check> | <✗/⚠> | <verbatim dryrun line> |
| det  | llm endpoint          | <✓/⚠/✗> | <GET /models 2xx \| CF-gated 401 (WARN) \| unreachable> |
| det  | hf dataset auth       | <✓/⚠/✗/·> | <reachable \| 401/403 \| skipped (not a huggingface source)> |
| det  | litellm port 4001     | <✓/✗>   | <free \| held by pid <P>> |
| det  | agent runtime_image   | <✓/⚠>   | <present \| not pulled locally> |
| det  | processed-tasks ledger    | <✓/✗>   | <parses, <n> entries, <m> resolve to exclusions> |
| det  | dependency wiring | <✓/✗> | <ok: N edges, both ends \| dep:link-mismatch … \| suppressed: smoke overlay> |
| det  | cloudflare (optional) | <✓/⚠>   | <ok (source: env\|root-config\|legacy-file) \| missing npx/credentials, see /root:setup> |
| det  | docker registry (optional) | <✓/⚠> | <ok (source: …) \| no credentials, pulls capped at 100/6h per IP> |

**Run configuration**
```
task_source: <provider> / <dataset_name or local task dir>
llm:         <api_base_url> / <model>
litellm:     port <port>, proxy config <template>
harbor_job:  jobs_dir=<jobs_dir> concurrency=<n> retries=<n> timeout_mult=<x>
agent:       <agent.name>@<agent.version>, image <runtime_image>
sft:         <sft_conversion.enabled> → out_dir=<out_dir>
excluded:    <n> task ids resolved from HARBOR_EXCLUDE_TASKS
```

**Next steps**
1. <one per failure, required first; quote the dryrun line verbatim>
2. ...
Re-run `/tracer:check`.
````

**The three invariants:**

1. **Verdict** — `✅ YES` iff `R == 0`, where `R` = dryrun `FAIL` count
   **+** any unresolved ledger leak **+** a held litellm port. Advisory
   items (agent image not pulled, HF network blip) and warnings *never*
   change it.
2. **Glyphs** — `✓` pass · `✗` blocks · `⚠` advisory/warning · `·` skipped.
3. **Collapse** — fold all passing `det` checks into the first row; add
   a row only for each `det` check that is `✗` or `⚠`.

## Interpreting results

- **LLM endpoint 401 from this shell**: when the configured base URL is
  `<your-production-endpoint>` (or similar CF-gated production endpoint),
  `dummy-key` is the real production key and the 401 is a network
  artifact specific to Claude Code's sandboxed shell — see memory
  `project-legoflow-curator-llm-endpoint`. Dryrun downgrades 401/403 to WARN for
  this reason. If the user is targeting that endpoint, suggest probing
  from a non-sandboxed shell on the same host (`ssh <user>@<host>` then
  `curl …`) to confirm.

- **HF dataset 401/403**: real auth failure. Either the user needs to
  paste an `hf_…` token (write to `~/.cache/huggingface/token`, mode
  600), or the dataset id is wrong, or their token doesn't grant
  access to that gated repo. Resolve before `prepare_tasks.sh` runs.

- **Port 4001 occupied by another user**: real FAIL — `start.sh` will
  fail to bind. There is a long-running root-owned LiteLLM on
  `:4002` on shared hosts; that's normal. Only `:4001` matters for
  tracer.

- **Agent runtime_image not pulled**: WARN, not FAIL. Harbor will
  `docker pull` it at first task. Offer to pre-pull
  (`docker pull <image>`) so launch latency is predictable and any
  registry-auth surprises surface now.

- **ledger status**: every entry needs a valid
  `status: pending|running|done|failed|skipped`. `start.sh` resolves the
  exclude list *from* the ledger, so an entry with a typo'd status is
  invisible to it and that task will re-run.

## Dependency wiring (cross-checked inside dryrun)

`scripts/dryrun.sh` runs `scripts/validate_config.py --block .`, which
cross-checks `meta_info.dependencies` against the real `runtime_info` on **both**
ends of every edge. These findings are easy to lose in the dryrun output, and
they are exactly what breaks a hand-off silently — surface them in the report.

| Finding | Meaning | Verdict |
|---|---|---|
| `dep:bad-key` | a `from` key is not a real dot-path in this block's own `runtime_info.input`, or a `to` key is not a declared `runtime_info.output` key | FAIL |
| `dep:bad-ref` | malformed ref, or the named block / output key / input path does not exist | FAIL |
| `dep:link-mismatch` | the edge is declared by only one end — the other end does not point back | FAIL |
| `dep:unresolved` | a required upstream output has neither `value` nor `path` yet | FAIL (WARN when the edge is `required: false`) |
| `dep:path-mismatch` | this block's configured value resolves outside the producer's declared output path — usually a stale path after a rename | WARN |
| `output:orphan` | an output with no `dependencies.to` entry; normal for a terminal output, suspicious for one that is supposed to feed the next stage | WARN |
| `dep:smoke-overlay` | a root smoke currently holds some block's config, so the tree mixes two config sets; every cross-block finding above is downgraded to a warning for the duration | INFO |

Any `dep:*` FAIL blocks `SAFE TO RUN` — it means this block is wired to something
the other end does not actually provide. The one exception is when
`dep:smoke-overlay` is present: those findings are artifacts of the running
smoke, not real drift, and must not be reported as such.

## When to escalate to FAIL vs WARN

Dryrun's classification is intentional. Do not "promote" warnings to
failures unless the user asks. The four current real-world warnings:

| WARN | Meaning |
|---|---|
| `llm_api.input_cost_per_token / output_cost_per_token empty` | Billing accounting only. |
| `agent runtime_image not pulled locally` | First task pays the pull cost. |
| `HF dataset network error` | Likely transient; retry once. |
| LLM endpoint 401/403 from this shell | Likely CF-gating artifact (see memory). |
| `cloudflare: missing npx/credentials` | Only affects `/tracer:dashboard`'s public sync; local HTML dashboard is unaffected. Credentials resolve env > root `config.yaml` → `runtime_info.input.cloudflare` > `~/.config/trajgen_progress_cloudflare.env`; the reported source says which won. Point the user at `/root:setup`'s optional Cloudflare extra — never blocks `/tracer:run`. A common cause on this host: node installed under `~/.nvm` is not on the PATH of a non-interactive shell, so `npx` looks missing to the script but present to the user. |
| `docker registry: no credentials` | Anonymous Docker Hub pulls are capped at 100 per 6h per IP; a long Harbor run pulls one image per task environment and can hit the cap mid-job, where it surfaces as agent/verifier failures rather than an auth error. Fix with `bash <repo_root>/scripts/docker_login.sh` (uses root `config.yaml` → `runtime_info.input.docker`) or a plain `docker login`. Never blocks `/tracer:run`. |

## Mandatory before `/tracer:run`

Per the root `CLAUDE.md` "check → confirm → run" workflow, this skill
must run AND the user must explicitly confirm before any
`/tracer:run` invocation. Never auto-launch.

## Out of scope

- Long-running side effects: no `docker pull`, no `prepare_tasks.sh`,
  no `git clone`. All checks are < 30 s in aggregate.
- Fixing failures: this skill only diagnoses. Setup fixes belong in
  `/tracer:setup`.

---

## Config reference (moved from config.yaml — do not re-add as comments)

- **LiteLLM proxy port**: 4001 is squatted by an unowned stale LiteLLM and 4002 is reserved for the host-wide root-owned LiteLLM — that is why `litellm_proxy.port` defaults to 4003. Flag a config that moves back onto 4001/4002.
- **Smoke overlays**: test/smoke runs use their own configs — `tests/smoke/config.yaml` (per-block) and `<repo_root>/tests/smoke/tracer/config.yaml` (root chain) — never the production config.yaml.
- **agent.runtime_host_path** must be pre-extracted from `runtime_image` via `docker cp` and user-owned: gpufs root_squash blocks docker-daemon writes to root-owned dirs.
