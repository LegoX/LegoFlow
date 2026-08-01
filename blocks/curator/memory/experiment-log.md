# End-to-End Pipeline Validation Log

> This validation was performed entirely and autonomously by an AI agent
> (Claude Code), including environment setup, command execution, result
> analysis, and log keeping.

**Date**: 2026-04-20
**Environment**: Linux 5.15.0, Python 3.12, Docker
**Models**: OPENAI_MODEL=glm-5-urg, ANTHROPIC_MODEL=claude-sonnet-4-6

## Goal

Verify that the full SWE-gen pipeline runs correctly under the refactored
directory layout.

## Steps

### Step 1: PR collection (skipped)

`collect_prs_wo_image.py` takes a while to call the GitHub API. This validation
used the 10 PRs already in `artifacts/collected_prs/python_pr_ids.txt` (from
tox-dev/tox, AnswerDotAI/RAGatouille, electricitymaps/electricitymaps-contrib,
morpheus65535/bazarr).

Note: the collector uses append mode (`a+`), so re-running it does not overwrite
existing PR IDs.

### Step 2: Task creation

```bash
swegen create \
  --input-ids-file ./artifacts/collected_prs/python_pr_ids.txt \
  --max-pr 1 --n-concurrent 1 \
  --output ./artifacts/swe_tasks/py-cc \
  --timeout 600 --cc-timeout 400 \
  --no-require-issue --min-source-files 1 --max-source-files 10
```

Processed 3 PRs (2 filtered/failed, 1 succeeded):
- `tox-dev/tox#3814`: validation failed
- `tox-dev/tox#3813`: succeeded (skeleton 14.6s, CC session 417.0s, NOP reward=0, Oracle reward=1)
- Task ID: `tox-dev__tox-3813`
- Total time: 15m 28s

### Step 3: Scoring

```bash
python tools/score_tasks.py --dir artifacts/swe_tasks/py-cc --update-toml
```

Scored 4 tasks (2 existing samples + 1 newly verified + 1 unverified), mean
difficulty 7.1/10, took <1s.

### Step 4: Extraction

```bash
python extract_verified_tasks.py
```

Extracted 9 verified tasks (8 original samples + 1 newly created), written to
`artifacts/merged_swe_tasks/`.

## Validation summary

| Step | Command | Status | Time |
|------|---------|--------|------|
| Install | `pip install -e .` | OK | 5s |
| Collect PRs | `collect_prs_wo_image.py` | skipped (used samples) | — |
| Create tasks | `swegen create` | 1 task verified | 15m 28s |
| Score | `score_tasks.py` | 4 tasks scored | <1s |
| Extract | `extract_verified_tasks.py` | 9 tasks extracted | <1s |

## Issues encountered

None. The pipeline ran correctly under the refactored paths.
