# trajgen CI tests

CI gate for the `trajgen` block. Calibrated to **this repo's fixed CI runner**
(specific uv env paths, configured `DOCKER_HOST`, pinned Harbor/swe_data_process
commits, configured LiteLLM port) — these tests are NOT a drop-in replacement
for the portable `/trajgen:check` skill, which tolerates arbitrary user
environments.

## Layout

```
cases/                          cheap deterministic checks (mirror /trajgen:check)
  01_config_schema.sh
  02_repo_pins.sh
  03_uv_envs_editable.sh
  04_llm_endpoint.sh
  05_hf_dataset.sh              (SKIP when provider != huggingface)
  06_litellm_port.sh
  07_runtime_image.sh
  08_consumption_ledger.sh
smoke/                          end-to-end runs that cost real LLM tokens + Docker
  10_hf_task_demo.sh
run.sh                          aggregator
```

## Invocation

```bash
# Cheap path (cases/ only) — safe for cloud CI:
bash subblock/trajgen/tests/run.sh

# Full path (cases/ + smoke/) — ~30 min, real LLM + Docker; self-hosted only:
bash subblock/trajgen/tests/run.sh --with-smoke
# or:  TESTS_WITH_SMOKE=1 bash subblock/trajgen/tests/run.sh
```

Each test exits `0` for pass, `77` for skip, anything else for fail. `run.sh`
returns non-zero iff any test failed.

## Smoke pass condition

`smoke/10_hf_task_demo.sh` swaps `config.yaml` for a smoke variant
(`harbor_job.jobs_dir=artifacts/jobs-smoke`, `n_tasks=10`, `n_concurrent=2`,
`max_retries=0`, `agent.max_turns=40`, `sft_conversion.enabled=false`), then
runs `scripts/start.sh` against the HF dataset already declared in
`runtime_info.input.task_source` (`SWE-Lego/swerebenchv2-200-260429`). Harbor
selects the first 10 tasks deterministically from the dataset snapshot.

It passes iff at least one trial's `result.json` (under
`artifacts/jobs-smoke/<job>/<trial>/result.json`) has a `verifier_result.rewards`
mapping containing a positive value (i.e. ≥1 resolved trajectory) within a
30-minute wall-clock budget. The original `config.yaml` is restored from backup
on any exit path.
