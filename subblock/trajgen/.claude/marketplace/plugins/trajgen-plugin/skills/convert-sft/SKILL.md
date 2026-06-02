---
name: convert-sft
description: >
  Convert one Harbor job's raw trajectories into SFT training data using the
  swe_data_process converter: writes <out_dir>/<job>/im.jsonl (intermediate
  OpenAI-style messages) and <out_dir>/<job>/lf.json (LLaMA-Factory ShareGPT
  array) plus lf.stats.json. Handles scaffold auto-detection from the agent /
  job name and --skip-unchanged polling. Use when asked to "convert trajectories
  to SFT data", "make the LF dataset", "build sft_data for a job", or to refresh
  SFT stats. Triggers on "/trajgen:convert-sft".
---

# /trajgen:convert-sft

Turn a finished Harbor job into LLaMA-Factory SFT data. Run from the block root
`subblock/trajgen/`. Requires the `swe_data_process` repo and its uv env
(`artifacts/env/swe-data-process-uv`) — provision via `/trajgen:setup` if missing.

## Run

```bash
scripts/convert_trajectories.sh                                   # job=latest, defaults from config
scripts/convert_trajectories.sh --job <name|latest>
scripts/convert_trajectories.sh --job latest --scaffold claude_code
scripts/convert_trajectories.sh --job <name> --out-dir artifacts/sft_data --max-instances 100
scripts/convert_trajectories.sh --job latest --skip-unchanged
```

Defaults are read from `runtime_info.input.sft_conversion` in `config.yaml`
(`enabled`, `scaffold`, `out_dir`, `max_instances`, `exclude_repos_file`).

## Outputs

Written under `<out_dir>/<job>/` (default `artifacts/sft_data/<job>/`):
- `im.jsonl` — intermediate OpenAI-style messages
- `lf.json` — LLaMA-Factory ShareGPT array (consumed by the `sft` block)
- `lf.stats.json` — counts, token lengths, turns, scores (surfaced by the dashboard)
- `.convert_sig.json` — signature of the resolved instance set + inputs (drives `--skip-unchanged`)

## Flags

| Flag | Meaning |
| --- | --- |
| `--job <name\|latest>` | Which job under `artifacts/jobs/`. `latest` = most recently modified dir. |
| `--scaffold <auto\|claude_code\|open_code\|openhands_sdk\|terminus2>` | Trajectory format. `auto` derives from the **job name** (and falls back to `runtime_info.input.agent.name`, e.g. `custom-claude-code → claude_code`). Override when job name and agent disagree. |
| `--out-dir <path>` | Output root (default `artifacts/sft_data`). |
| `--max-instances <N>` | Cap converted instances. |
| `--exclude-repos-file <path>` | Repos to drop; `""` = converter default `artifacts/excluded_repos.txt`. |
| `--skip-unchanged` | Exit early without reconverting when the job's resolved (reward=1.0) instance set and inputs are unchanged since the last run (per `.convert_sig.json`). Used by the dashboard sync loop. |

## When it runs automatically

If `runtime_info.input.sft_conversion.enabled: true`, `scripts/start.sh` calls
`scripts/convert_trajectories.sh --job "$JOB_NAME"` after Harbor exits — no
manual step needed. The `/trajgen:dashboard` Cloudflare loop also calls it
periodically with `--skip-unchanged` to keep online SFT stats fresh.
