---
name: setup
description: >
  Bootstrap the swegen block from a fresh clone: create + activate the
  Python venv, `pip install -e repos/swegen/`, ensure `GITHUB_TOKENS`,
  `OPENAI_API_KEY`, `OPENAI_API_BASE_URL`, `OPENAI_MODEL`,
  `ANTHROPIC_MODEL` are set in the user's shell (prompt for any missing
  ones), and stage `gh_token.txt` (one token per line) if the user prefers
  that channel. Idempotent. Triggers on phrases like "set up swegen",
  "bootstrap swegen", "install swegen", "prepare swegen before running".
---

# /swegen:setup

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:setup` is the one-shot bootstrap that
brings the block from fresh clone to "`:check` passes".

## Intent

1. **venv** — create `./.venv` if missing, `pip install -e repos/swegen/`.
2. **env vars** — for each of `GITHUB_TOKENS`, `OPENAI_API_KEY`,
   `OPENAI_API_BASE_URL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`: if unset, ask
   the user once, then suggest an `export …` line they can drop into their
   shell rc. Never write secrets into `config.yaml`.
3. **`gh_token.txt`** — if the user prefers a file: stage `gh_token.txt`
   at the project root, one token per line; warn if the file already
   exists with content.
4. **config.yaml** — set `runtime_info.input.languages`, `pr_limit`,
   `target_count` if the user wants to deviate from defaults.

## TODO

- [ ] Decide whether to auto-install `gh` CLI for token testing.
- [ ] Spec the prompt order so re-runs only ask for missing items.
