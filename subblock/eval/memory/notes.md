# eval — long-form operational notes

## Block summary

The `eval` block is a standalone evaluation harness modeled on `trajgen`. It uses
the same Harbor + per-job LiteLLM-proxy architecture, but loads its task set
from Harbor's `registry.json` instead of from swegen's local export. The active
profile uses `custom-openhands-sdk` 1.14.0 against a local
Qwen3.5-35B-A3B checkpoint served by vLLM on the GPU node; the eval CPU node
still launches the LiteLLM logging proxy and Harbor task containers.

## Supported benchmarks

The current Harbor pin is expected to contain at least:

SWE / terminal (curated for the supported custom agents):

- `swebench-verified` @ 1.0 (500 tasks)
- `swebench-verified-100` @ 1.0 (100-task subset)
- `swebench_multilingual` @ 1.0 (300 tasks)
- `swebench_multilingual-100` @ 1.0 (100-task subset, seed=42)
- `swebenchpro` @ 1.0 (731 tasks)
- `swebenchpro-100` @ 1.0 (100-task subset)
- `terminal-bench` @ 2.0 (89 tasks)

Other coding benchmarks (Harbor adapters exist; confirm agent fit first):

- `aider-polyglot` @ 1.0 (225 tasks, polyglot code editing)
- `livecodebench` @ 6.0 (100 tasks, competitive programming)
- `humanevalfix` @ 1.0 (164 tasks, bug fixing)
- `bigcodebench-hard-complete` @ 1.0.0 (145 tasks, function-level completion)

The 100-task subsets share the same registry entry shape as their full-set parents
and are the recommended smoke-run targets — they boot through the same
`scripts/start.sh` flow without any extra setup.

## Operational expectations

- Run on `meta_info.resources.ip` (`local` by default) in a named tmux
  session (`eval`). Keep remote host details in a private run profile.
- `repos/harbor` is a pinned submodule. Never edit it in place; initialize with
  `git submodule update --init subblock/eval/repos/harbor` and update a clean
  checkout via `scripts/update_repos.sh`.
- Run `/eval:check` before every launch: `scripts/dryrun.sh` validates static
  state, while `scripts/probe_llm_completion.sh` sends the mandatory real
  completion probe. A successful `GET /models` is not sufficient.
- Require explicit confirmation after the preflight summary and before
  `scripts/start.sh`.
- Keep `scripts/serve_local_model.sh` alive on the GPU node configured by
  `llm_api.api_base_url` for the full run.
- `agent.runtime_host_path` must contain the runtime extracted from the exact
  configured `runtime_image`.
- Read live progress from processes and `<job>/result.json`; `config.yaml` is
  a run profile, not a mutable status ledger.
- Before changing `meta_info.repositories.harbor.commit`, confirm the new commit's `registry.json` still contains the chosen dataset/version pair.
