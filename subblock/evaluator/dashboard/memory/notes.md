# evaluator — long-form operational notes

## Block summary

The `evaluator` block is a standalone evaluation harness modeled on `tracer`. It uses
the same Harbor + LiteLLM-proxy + custom-claude-code runtime, but loads its task
set from Harbor's `registry.json` instead of from the curator's local export.

## Supported benchmarks

The current Harbor pin is expected to contain at least:

SWE / terminal (validated end-to-end with custom-claude-code):

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

- Run on `192.168.35.240` in a named tmux session (`evaluator`).
- Never edit files under `repos/harbor`; update via `scripts/update_repos.sh`.
- Keep `config.yaml → status` current as the job moves through idle → running → done.
- Before changing `meta_info.repositories.harbor.commit`, confirm the new commit's `registry.json` still contains the chosen dataset/version pair.
