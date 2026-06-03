---
name: check
description: >
  Preflight the swegen block: validate config.yaml schema; verify
  GITHUB_TOKENS reach the GitHub API (one `GET /rate_limit` per token);
  probe the LLM endpoint with `GET /models` and confirm the configured
  pr_model / task_model are present; verify the local Docker daemon is up
  (needed for task verification); run `scripts/dryrun.sh` if present.
  Read-only. Reports all failures in one consolidated message. Triggers on
  phrases like "check swegen", "preflight swegen", "is swegen ready",
  "diagnose swegen", "validate swegen config".
---

# /swegen:check

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:check` is the read-only preflight that
answers "is it safe to run?". Reports all failures in one pass.

## Intent

1. **Schema** — `config.yaml` parses; `meta_info.name == 'swegen'`.
2. **Env / tokens** — `GITHUB_TOKENS` resolves (env var or `gh_token.txt`);
   each token answers `GET https://api.github.com/rate_limit` with 200.
3. **LLM endpoint** — `GET <api_base_url>/models` with the configured key;
   `pr_model` and `task_model` appear in `data[].id`.
4. **Docker** — local docker daemon reachable (`docker info`); needed for
   per-language verification containers.
5. **dryrun.sh** — if present, run it; surface its OK / WARN / FAIL lines.

## TODO

- [ ] Wire to `scripts/dryrun.sh` once it's written.
- [ ] Decide whether to probe every per-language Docker image, or just the
      ones declared in `runtime_info.input.languages`.
