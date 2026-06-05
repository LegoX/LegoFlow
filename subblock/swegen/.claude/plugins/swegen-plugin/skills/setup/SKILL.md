---
name: setup
description: >
  Bootstrap the swegen block from a fresh clone: initialise the swegen
  submodule under `repos/swegen/` (`git submodule update --init`),
  create + activate the Python venv, `pip install -e repos/swegen/`, and
  ensure the cross-provider LLM env (`OPENAI_API_KEY`,
  `OPENAI_API_BASE_URL`, `OPENAI_MODEL`, mirrored to `ANTHROPIC_API_KEY` /
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`), `GITHUB_TOKENS`,
  `DOCKER_HOST`, and `CLAUDE_CONFIG_DIR` are set in the user's shell —
  prompting for any that are missing. Stage `gh_token.txt` (one token per
  line) if the user prefers the file channel. Idempotent. Triggers on
  phrases like "set up swegen", "bootstrap swegen", "install swegen",
  "prepare swegen before running".
---

# /swegen:setup

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:setup` brings the block from a fresh
clone to "`:check` passes".

## Intent

1. **Submodule** — ensure `repos/swegen/` is initialised:
   `git submodule update --init subblock/swegen/repos/swegen` (run from
   the repo root). Skip if the submodule is already populated at the
   pinned commit declared under `meta_info.repos.swegen.commit_id`.
2. **venv** — create `./.venv` if missing, then
   `source .venv/bin/activate` and `pip install -e repos/swegen/`.
3. **Cross-provider LLM env** — swegen's runtime uses
   `swegen.llm_env.hydrate_cross_provider_env`, which expects both the
   OpenAI- and Anthropic-side env vars to point at the same provider.
   For each of the variables below: if unset, ask the user once, then
   suggest the `export …` line they can drop into their shell rc. Never
   write secrets into `config.yaml`.

   | Variable | Purpose |
   |---|---|
   | `GITHUB_TOKENS` | Comma-separated GitHub tokens for PR collection |
   | `OPENAI_API_KEY` | Cross-provider LLM API key (also mirrored to `ANTHROPIC_API_KEY`) |
   | `OPENAI_API_BASE_URL` | OpenAI-compatible endpoint |
   | `ANTHROPIC_BASE_URL` | Anthropic-compatible endpoint (same provider, different surface) |
   | `OPENAI_MODEL` | Model for PR evaluation + instruction generation |
   | `ANTHROPIC_MODEL` | Model for Claude Code SDK (task completion) |
   | `DOCKER_HOST` | Must be set to `unix:///var/run/docker.sock` so Harbor doesn't probe `/tmp/podman-fresh.sock` by default |
   | `CLAUDE_CONFIG_DIR` | Per-run Claude Code config dir, e.g. `$PWD/artifacts/claude-config/swegen-clean` — create with `mkdir -p` |

4. **`gh_token.txt`** (optional alternative to `GITHUB_TOKENS`) — if the
   user prefers a file: stage `gh_token.txt` at the project root, one
   token per line; warn if the file already exists with content.
5. **`config.yaml`** — set `runtime_info.input.languages`, `pr_limit`,
   `target_count` only if the user wants to deviate from defaults.

## TODO

- [ ] Decide whether to auto-install `gh` CLI for token testing.
- [ ] Spec the prompt order so re-runs only ask for missing items.
- [ ] Decide whether `:setup` should write a `.env` template the user
      can `source` instead of hand-exporting eight variables.
