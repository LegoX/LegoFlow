---
name: check
description: >
  Preflight the swegen block. Validates config.yaml schema; verifies that
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
  **Mandatory before `:run`.** Triggers on phrases like "check swegen",
  "preflight swegen", "is swegen ready", "diagnose swegen",
  "validate swegen config".
---

# /swegen:check

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:check` is the read-only preflight that
answers "is it safe to run?". Reports **all** failures in one pass — do
not stop at the first.

## Intent

1. **Schema** — `config.yaml` parses; `meta_info.name == 'swegen'`.
2. **Repo pin** — `repos/swegen/` exists and its
   `git rev-parse HEAD` equals `meta_info.repos.swegen.commit_id`. Drift
   is a FAIL, not a WARN.
3. **GitHub tokens** — `GITHUB_TOKENS` resolves (env var or
   `gh_token.txt`). For each token, `GET https://api.github.com/rate_limit`
   returns 200; record the `resources.core.remaining` so the run-config
   summary can show available API budget.
4. **LLM endpoint (cross-provider ping, not just `/models`)** — load
   `swegen.llm_env`, call `hydrate_cross_provider_env()` and
   `get_openai_compatible_config()`, then issue a single
   `chat.completions.create` with `max_tokens=16` and a one-token prompt.
   A `GET /models` probe is not sufficient — it does not catch real
   failures like `401 Invalid token` or
   `403 unsupported_country_region_territory` (wrong-region routing)
   that surface only on actual completion calls. Surface the provider's
   error verbatim on failure.
5. **Docker** — `docker info --format '{{.ServerVersion}}'` succeeds.
   **Also** verify `DOCKER_HOST` is set: an unset `DOCKER_HOST` lets
   Harbor probe `/tmp/podman-fresh.sock` by default and report
   "Docker daemon is not running" even when `docker info` passes.
   Recommend `export DOCKER_HOST=unix:///var/run/docker.sock` on
   failure.
6. **Harbor smoke (optional, off by default)** — only when the user
   passes `--smoke` (or the agent decides on first-time validation): run
   `swegen validate artifacts/swe_tasks/py-cc --task tox-dev__tox-3813 \
   --jobs-dir artifacts/swe_tasks/.swegen/harbor-jobs-quick --env docker`.
   Expected output: `NOP reward=0` and `Oracle reward=1`. Any deviation
   is a FAIL with the verbatim Harbor stderr included. Skip silently if
   the task source directory is missing — that's a `:setup` problem, not
   a `:check` problem.
7. **`scripts/dryrun.sh`** — run if present; surface its OK / WARN /
   FAIL lines.
8. **`scripts/stop.sh`** — file exists and is executable (per
   BLOCK_DEFINITION.md §2.1). WARN if missing — the block can still
   `:run`, but the user can't cleanly stop it.

## Run-configuration summary

After all checks, print one summary block before exiting:

```
swegen run config
  repos/swegen HEAD          : <sha>
  github tokens              : <N> ok, total budget <K> req/h
  llm provider               : <openai_base_url> / <openai_model>
                              <anthropic_base_url> / <anthropic_model>
  docker                     : <server_version> at <DOCKER_HOST>
  languages enabled          : <list>
  per-language pr_limit      : <map>
  per-language target_count  : <map>
```

This is the surface the user inspects before approving `:run`.

## TODO

- [ ] Wire to `scripts/dryrun.sh` once written.
- [ ] Decide whether to probe per-language Docker base images at
      `:check` time or defer to first-task failure.
- [ ] Decide the default for the Harbor smoke — currently off; arguably
      should be on for the very first `:check` after `:setup`.
