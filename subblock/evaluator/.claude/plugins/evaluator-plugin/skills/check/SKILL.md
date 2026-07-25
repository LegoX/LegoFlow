---
name: check
description: >
  Preflight the evaluator block: validate config.yaml schema; verify
  `repos/harbor/` pinned commit matches `meta_info.repositories.harbor`;
  confirm Harbor uv env and LiteLLM venv exist with expected editable
  installs; probe the configured LLM endpoint with
  `bash scripts/probe_llm_completion.sh` (a minimal real chat
  completion); confirm the configured
  `(task_source.dataset_name, version)` resolves in
  `repos/harbor/registry.json`; confirm the LiteLLM proxy port is free
  or held by the current job. Read-only. Reports all failures in one
  consolidated message with a run-configuration summary.
  **Mandatory before `:run`.** Triggers on phrases like "check evaluator",
  "preflight evaluator", "is evaluator ready", "diagnose evaluator", "validate evaluator
  config".
---

# /evaluator:check

Read-only preflight. Wraps `scripts/dryrun.sh` — the single source of
truth for what "evaluator is ready" means — and adds the few live, contextual
checks a static script cannot make: an LLM-endpoint reachability probe,
whether a Harbor job is already running, whether the LiteLLM port is held
by a foreign process, and whether this run would overwrite an existing
job dir. Reports **all** failures in one pass; never edits `config.yaml`,
never launches.

## How to run

1. **Be on the right host first.** Read `meta_info.resources.ip`. For
   `local` / null, run from the current host. For a configured remote
   hostname or IP, connect with your SSH-key configuration and run the
   checks from the block directory there. Never preflight on a host the
   run won't use; env paths, port ownership, and Docker state are all
   host-specific.
2. Execute `bash scripts/dryrun.sh` from the block root. It is the
   authoritative readiness check (sections below).
3. Run the live contextual checks the dryrun can't, including
   `bash scripts/probe_llm_completion.sh` (see "Contextual checks").
4. Fold every `dryrun.sh` line and the contextual checks into the Step 5
   report below — never re-run a probe or overrule an `OK`.

## What `scripts/dryrun.sh` covers

| § | Checks |
|---|---|
| 1. Block files | `CLAUDE.md`, `config.yaml`, `memory/overview.mdx` exist. A missing `artifacts/index.yaml` is **INFO, not FAIL** (auto-created by `archive_run.sh` after the first run). |
| 2. YAML syntax | `config.yaml` and `index.yaml` parse under PyYAML. |
| 3. Harbor repo config | `url` / `commit` (or `ref`/`branch`) / `path` / `readonly` set; path is a tracked submodule (or gitignored for a non-submodule checkout). |
| 4. Local Harbor checkout | `repos/harbor/.git` present, origin URL matches, **HEAD == `commit` pin** (drift is FAIL), worktree clean. |
| 5. Harbor env | `harbor_uv` env exists and is outside `repos/harbor`; Python ≥ 3.12; `import harbor / litellm / datasets`; `harbor` resolves to `repos/harbor` (editable); `harbor --help` works. |
| 6. LiteLLM env | `litellm_uv` venv exists; Python == 3.13; `litellm` CLI present; installed `litellm` version == `1.83.14`. |
| 7. Model API | `llm_api.{api_base_url, model, api_key}` set (costs empty → WARN). **No live probe.** |
| 8. Harbor run config | proxy/task_source/harbor_job/agent fields set; `provider == harbor_registry`; **`(dataset_name, version)` resolves in `registry.json` and reports task count**; LiteLLM template exists; agent `model_name` derivable; `jobs_dir`/`job_dir` under `artifacts/jobs`; `runtime_image` set; **`runtime_host_path` populated with the per-agent marker file**. |
| 9. Run command | `command_override` empty (default Harbor command will be built) or present. |
| 10. Cloudflare Pages (optional) | `npx`/node toolchain present, plus `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` in env or `~/.config/harbor_webui_cloudflare.env`. Only needed for `/evaluator:dashboard`'s public sync (`dashboard/run_cloudflare_pages_sync.sh`) — always WARN, never FAIL. |

## Contextual checks the skill adds

`dryrun.sh` is static. Add these live checks and fold them into the
report:

- **LLM endpoint probe**: dryrun deliberately skips it — §7 only
  confirms the fields are set, because reachability is exercised by each
  job *after* LiteLLM proxy startup. Run
  `bash scripts/probe_llm_completion.sh` from the block root; it reads
  `runtime_info.input.llm_api` and sends a minimal real request to
  `/chat/completions`. Do **not** substitute `GET /models`: gateways can
  serve the model catalog from local configuration while the upstream
  origin returns 5xx for every completion. Interpret exit 0 as PASS,
  exit 1 as FAIL, and exit 77 as WARN; preserve the script's diagnostic
  in the consolidated report.
  When `api_base_url` points at a **local vLLM endpoint** (custom
  checkpoint), this probe doubles as the "is vLLM up?" gate: a
  connection-refused/timeout or 5xx here means
  `scripts/serve_local_model.sh` isn't healthy on the GPU node — FAIL,
  not a configured gateway WARN. Gateway-specific 401/403 handling is
  enabled only when `EVAL_GATEWAY_HOST_SUFFIX` is set. Tell the user to
  start vLLM and re-check.
  Note: the **active** `llm_api` in `config.yaml` is currently the local
  Qwen3.5-35B-A3B vLLM endpoint (MODE B); MODE A is the commented
  remote-GLM-5 alternate.
- **A job already running**: a live `harbor run` (or a stray LiteLLM on
  the configured port) means launching now would double-book the host.
  Check for an existing `evaluator` tmux session and a `litellm`/`harbor`
  process before declaring SAFE-TO-RUN.
- **LiteLLM port owner**: `runtime_info.input.litellm_proxy.port`
  (currently `4101`) must be free or held by *this* evaluator job. A foreign
  process on the port is FAIL — `start.sh` will fail to bind.
- **Overwrite guard**: `start.sh` timestamps the job dir, so collisions
  are unlikely, but if `job_dir` is pinned in config, warn when it
  already contains results.
- **Job already analyzed?**: after a completed run, `start.sh`
  auto-invokes `scripts/analyze_job.sh`, writing `<job_dir>/analysis/`.
  This is post-run and non-fatal — not a preflight gate — but when
  reporting on an existing/interrupted job dir, note whether `analysis/`
  is present so the user knows the dashboard has data to read.

## Interpreting results

- **Harbor commit drift / dirty worktree** (§4): FAIL, not WARN. Harbor
  is a read-only managed dependency. Do not auto-checkout — defer to
  `/evaluator:setup`'s `scripts/update_repos.sh`, which refuses to update a
  dirty worktree.
- **`agent.runtime_host_path` empty / missing marker** (§8): real FAIL.
  When set, `start.sh` bind-mounts that host dir into every task
  container; if it's empty the agent silently falls back to an
  in-container install (`curl …/install.sh` for claude-code, `pip` for
  openhands-sdk) that 403s/times out on isolated networks. dryrun prints
  the exact `docker create … && docker cp …` extraction hint — fix in
  `/evaluator:setup` before running.
- **`registry.json` `not_found`** (§8): the `(dataset_name, version)`
  pair isn't in the registry. Re-check against the curated table in
  `CLAUDE.md`; list everything with
  `python3 -c 'import json;[print(e["name"]+"@"+e["version"]) for e in json.load(open("repos/harbor/registry.json"))]'`.
  If the entry exists but lies **outside** the curated table, emit a WARN
  with the agent-compatibility caveat from `CLAUDE.md` ("Other registry
  entries") — math/MCQ/QA/own-runtime benchmarks generally don't work
  with the configured custom agent.
- **LiteLLM version mismatch** (§6): FAIL. The pin is `1.83.14`; other
  versions change proxy config semantics. Rebuild the venv in setup.
- **LLM endpoint 401/403 from this shell** (contextual probe): when the
  base URL matches the explicitly configured
  `EVAL_GATEWAY_HOST_SUFFIX`, the response may come from a gateway edge
  rather than the origin. Treat it as WARN and suggest re-probing from
  a non-sandboxed shell on the configured evaluator host. Never commit the
  gateway credential. A **local vLLM** base URL (MODE B) gets no such pass:
  connection-refused/5xx means vLLM is not healthy, while 401/403 means
  its API key does not match; both are FAIL.
- **`scripts/stop.sh` missing**: WARN. The evaluator block currently ships no
  `stop.sh`; teardown is handled by `start.sh`'s EXIT trap
  (`cleanup_litellm` + `archive_run.sh`). Note it but do not block.
- **Token costs empty** (§7): WARN — billing accounting only.

## When to escalate FAIL vs WARN

dryrun's classification is intentional. Do not promote warnings to
failures unless the user asks. The expected real-world WARNs:

| WARN | Meaning |
|---|---|
| `input_cost_per_token / output_cost_per_token empty` | Billing accounting only. |
| benchmark outside the curated `CLAUDE.md` table | Agent compatibility is the user's call. |
| CF-gated remote endpoint 401/403 from this shell | Likely sandbox/network artifact; re-probe on the eval node. |
| `scripts/stop.sh` missing | Teardown is handled by `start.sh`'s EXIT trap. |
| `cloudflare: missing npx/credentials` | Only affects `/evaluator:dashboard`'s public sync; local HTML dashboard is unaffected. Point the user at `/root:setup`'s optional Cloudflare extra — never blocks `/evaluator:run`. |

## Step 5 — The report (always the last thing you print)

The report **is** the deliverable. Print it every single time — even
on an abort (then: heading + a `NO` verdict whose reason is the abort
message, nothing else). Fill this template exactly; drop only truly
inapplicable rows.

````
## evaluator block check — CWD=<relative path>

**SAFE TO RUN: <✅ YES | ❌ NO>** — <R> required · <A> advisory · <W> warnings

| Layer | Check | Status | Detail |
|-------|-------|:------:|--------|
| det  | §1-2 files/yaml · §3-4 harbor repo+checkout · §5-6 harbor-uv/litellm envs | ✓ | ok=<N> |
| det  | <each FAIL/WARN det check, §-labeled> | <✗/⚠> | <verbatim dryrun line> |
| det  | §8 registry lookup   | <✓/✗>   | <(dataset_name, version) resolved, N tasks \| not_found> |
| det  | §8 runtime_host_path | <✓/✗>   | <populated with marker \| empty/missing> |
| live | llm completion probe | <✓/⚠/✗> | <probe_llm_completion.sh exit 0/77/1, detail> |
| live | job already running  | <✓/✗>   | <none \| harbor/litellm process pid=<P>> |
| live | litellm port owner   | <✓/✗>   | <free \| held by pid <P>> |
| live | job dir overwrite    | <✓/⚠>   | <clean \| job_dir already has results> |
| det  | cloudflare (optional) | <✓/⚠>  | <ok \| missing npx/credentials, see /root:setup> |

**Run configuration**
```
host:          <ip> (remote → SSH) or 'local'
repos/harbor:  HEAD=<sha> (pin <commit>)
llm provider:  <api_base_url> / <model>
benchmark:     <dataset_name>@<version> (<n_tasks_in_registry> tasks)
agent:         <agent.name>@<agent.version>, image <agent.runtime_image>
harbor_job:    jobs_dir=<jobs_dir> n_concurrent=<N> n_tasks=<null|N>
litellm:       port <port>, config <litellm_proxy.config_template>
excluded:      <HARBOR_EXCLUDE_TASKS or none>
post-eval:     analysis=<job_analysis.enabled> tag_endpoint=<job_analysis.tag_llm.base_url>
```

**Next steps**
1. <one per failure, required first; quote the dryrun/probe line verbatim>
2. ...
Re-run `/evaluator:check`.
````

**The three invariants:**

1. **Verdict** — `✅ YES` iff `R == 0`, where `R` = dryrun `FAIL` count
   **+** a failed completion probe **+** a job already running **+** a
   foreign-held litellm port. Advisory items (job dir already has
   results, `scripts/stop.sh` missing) and warnings *never* change it.
2. **Glyphs** — `✓` pass · `✗` blocks · `⚠` advisory/warning · `·` skipped.
3. **Collapse** — fold all passing `det` checks into the first row; add
   a row only for each `det`/`live` check that is `✗` or `⚠`.

This is the surface the user inspects before approving `:run`.

## Mandatory before `/evaluator:run`

Per the root `CLAUDE.md` "check → confirm → run" workflow, this skill
must run AND the user must explicitly confirm before any `/evaluator:run`
invocation. Never auto-launch.

## Out of scope

- No side effects: no `docker pull`/`docker cp`, no `update_repos.sh`,
  no env builds, no proxy start. All checks are read-only and fast.
- Fixing failures: this skill only diagnoses. Repo/env/runtime fixes
  belong in `/evaluator:setup`.

---

## Config reference (moved from config.yaml — do not re-add as comments)

- **job_analysis.tag_llm** needs a model that returns CLEAN JSON. A reasoning model that emits `<think>` into content (e.g. Qwen3.5-35B-A3B served with thinking on) produces unparseable output and tagging fails. Empty tag_llm values fall back to `llm_api` — flag that combination when llm_api points at a reasoning model. Runtime overrides: `PREP_TAG_BASE_URL` / `PREP_TAG_MODEL` / `PREP_TAG_API_KEY`.
- **agent.runtime_host_path**: if the dir is empty, the agent falls back to an in-container install step (curl claude.ai/install.sh, pip for openhands-sdk) which 403s/times out on isolated networks — verify the extraction exists (see /evaluator:setup reference).
- **env_extra.LITELLM_STICKY_ROUTING_ALIASES: ""** is a workaround for Harbor's serve_litellm.sh dereferencing it under `set -u` when CONFIG_NAME != "litellm_config" — keep the key present even when empty.
