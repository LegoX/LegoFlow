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
  **Mandatory before `:create-tasks`.** Triggers on phrases like "check curator",
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

This check is mandatory before `/curator:create-tasks`; do not skip it just because
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

## Step 4b - Shared credentials (optional, never blocking)

`dryrun.sh` also reports the two tree-wide optional credentials, resolved by
`<repo_root>/scripts/shared_credentials.sh` in the order **env > root
`config.yaml` → `runtime_info.input.{cloudflare,docker}` > this block's legacy
`~/.config/swegen_progress_cloudflare.env`**. The reported source tells the user
which of the three won, so say it in the report rather than just "configured".

- **Cloudflare Pages** — needs an `npx`/node toolchain on `PATH` plus
  `account_id` + `api_token`. Purely for publishing the databoard online (the
  manual `npx wrangler pages deploy` from `dashboard/site/` documented in
  `dashboard/README.md`); nothing in `/curator:create-tasks` depends on it.
- **Container registry** — `username` + `password`. Anonymous Docker Hub pulls
  are capped at 100 per 6h per IP, and a long multi-language create run pulls one
  image per task environment, so hitting the cap mid-run is realistic; it shows
  up as image-pull/manifest errors rather than as an obvious auth error. Fix with
  `bash <repo_root>/scripts/docker_login.sh` or a plain `docker login`.

Both are always a `WARN`, never a reason to block `SAFE TO RUN`. If either is
missing, mention `/root:setup`'s optional extras as the fix, but do not offer to
configure credentials yourself (see that skill's guardrail on secrets).

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

## Step 5 - Run the block dryrun

Run `bash scripts/dryrun.sh` and include its OK/WARN/FAIL lines in the
report. This script verifies the installed package, YAML parsing, key env
vars, the Claude Code proxy endpoint, Docker availability, and (Step 4b)
the shared Cloudflare/registry credentials.

## Step 6 - The report (always the last thing you print)

The report **is** the deliverable. Print it every single time — even
on an abort (then: heading + a `NO` verdict whose reason is the abort
message, nothing else). Fill this template exactly; drop only truly
inapplicable rows.

````
## curator block check — CWD=<relative path>

**SAFE TO RUN: <✅ YES | ❌ NO>** — <R> required · <A> advisory · <W> warnings

| Layer | Check | Status | Detail |
|-------|-------|:------:|--------|
| det  | config · repos/swegen pin · scripts present | ✓ | ok=<N> |
| det  | <each FAIL/WARN det check> | <✗/⚠> | <verbatim detail> |
| det  | github tokens   | <✓/✗>   | <N tokens ok, total remaining K> |
| det  | llm (openai)    | <✓/✗>   | <base> / <model> |
| det  | cc path         | <✓/⚠/✗> | <mode> / <anthropic_base_url> / <ok/down/native> |
| det  | docker          | <✓/✗>   | <server version> at <DOCKER_HOST or unset> |
| det  | dependency wiring | <✓/✗> | <ok: N edges, both ends \| dep:link-mismatch … \| suppressed: smoke overlay> |
| det  | cloudflare      | <✓/⚠>   | <ok (source: env\|root-config\|legacy-file) \| missing npx/credentials (optional, see /root:setup)> |
| det  | docker registry | <✓/⚠>   | <ok (source: …) \| no credentials, pulls capped at 100/6h per IP> |
| det  | dryrun          | <✓/⚠/✗> | <pass/warn/fail> |
| live | smoke           | <✓/·/✗> | <skipped \| NOP=0 Oracle=1 \| fail: <detail>> |

**Run configuration**
```
languages:  <enabled list with timeout/cc_timeout/n_concurrent>
swe tasks:  <per-language generated + verified counts>
pr_ids:     <artifacts/collected_prs/{lang}_pr_ids.txt present? counts>
```

**Next steps**
1. <one per failure, required first; quote the underlying error verbatim>
2. ...
Re-run `/curator:check`.
````

**The three invariants:**

1. **Verdict** — `✅ YES` iff `R == 0`, where `R` = config/GitHub/LLM/Docker/
   dryrun `FAIL` count **+** a down CC proxy when
   `cc_provider_mode=openai_proxy`. A skipped smoke never changes it unless
   the user explicitly requested smoke and it failed.
2. **Glyphs** — `✓` pass · `✗` blocks · `⚠` advisory/warning · `·` skipped.
3. **Collapse** — fold all passing `det` checks into the first row; add
   a row only for each `det` check that is `✗` or `⚠`.

## Guardrails

- Read-only except for the optional Harbor smoke jobs directory.
- Do not edit `config.yaml`, `.env`, token files, or `artifacts/index.yaml`.
- Do not launch `scripts/start.sh` or `swegen create`; that is `/curator:create-tasks`.
- Do not hide credential or provider errors. Quote the provider error
  message, but never print secret values.

---

## Config reference (moved from config.yaml — do not re-add as comments)

- **Silent verification failure**: the Claude Code path (task_model / cc_provider_mode / anthropic_base_url) is what writes `verifiable_tasks.txt`. If the mode is wrong for the provider, verification fails **silently** — task skeletons stay templates, no task is verified, yet batch state still reports success. Always verify the CC path end-to-end, not just the OpenAI path.
- **pr_collection.filters**: a null/absent filter is not an error — it means "use the collector's built-in default". Only flag values that are set but out of range.
