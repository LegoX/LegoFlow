---
name: collect-prs
description: >
  Collect GitHub PRs into the per-language `{lang}_pr_ids.txt` files that
  `legoflow-curator create` consumes. Wraps `repos/legoflow-curator/tools/collect_prs_wo_image.py`
  driven by `config.yaml -> runtime_info.input.pr_collection`
  (languages, repo_num, max_prs_per_repo, output_dir, token_limit, and the
  global filter thresholds), which `scripts/load_runtime_env.sh` exports as
  `LEGOFLOW_CURATOR_COLLECT_*` / `LEGOFLOW_CURATOR_PR_*` / `COLLECT_TOKEN_LIMIT`. The collector
  combines tokens from its token file with `GITHUB_TOKENS` / `GITHUB_TOKEN`;
  tokens never come from `config.yaml`.
  Long-running for a full multi-language pass; supports a small first-run
  sample. This is the PR-collection stage that precedes `/curator:create-tasks`.
  Triggers on phrases like "collect PRs", "gather PRs", "run PR collection",
  "collect github prs for legoflow-curator", "refresh pr_ids".
---

# /curator:collect-prs

Collect qualifying GitHub PRs per language. This is the **first** pipeline
stage; `/curator:create-tasks` (task generation) consumes its output. Run only from
`blocks/curator/`.

## Step 0 - Orient

Validate:

1. Current directory is `blocks/curator/`.
2. `config.yaml` parses and has `meta_info.name == "curator"`.
3. `repos/legoflow-curator/tools/collect_prs_wo_image.py` exists (submodule initialized;
   if missing, tell the user to run `/curator:setup`).
4. `scripts/collect_all_bg.sh` and `scripts/load_runtime_env.sh` exist.

Read `config.yaml -> runtime_info.input.pr_collection` and `CLAUDE.md`
("PR collection configuration") before launching.

## Step 1 - Resolve collection tokens

The collector first reads `repos/legoflow-curator/gh_token.txt` (one token per line)
unless `COLLECT_GITHUB_TOKEN_FILE` overrides that path, then merges
`GITHUB_TOKENS` and `GITHUB_TOKEN`. `scripts/load_runtime_env.sh` preserves the
caller's exported environment and may hydrate `GITHUB_TOKENS` from ignored
block/home token files when the caller did not provide one.

```bash
source scripts/load_runtime_env.sh
load_runtime_env
COLLECT_GITHUB_TOKEN_FILE="${COLLECT_GITHUB_TOKEN_FILE:-repos/legoflow-curator/gh_token.txt}"
test -s "$COLLECT_GITHUB_TOKEN_FILE" || \
  test -n "${GITHUB_TOKENS:-}${GITHUB_TOKEN:-}"
```

If neither the file nor environment channels contain a token, stop and ask the
user to provide one. Never print token values. `COLLECT_TOKEN_LIMIT` (from
`pr_collection.token_limit`) caps the combined, deduplicated token set
(`0` = all).

## Step 2 - Refuse duplicate live collectors

```bash
BLOCK_DIR="$(pwd -P)"
pgrep -af 'collect_prs_wo_image.py|scripts/collect_all_bg.sh' \
  | grep -F "$BLOCK_DIR" || true
```

If a collector is already alive for this block, refuse to start another;
report the PID and the log under `artifacts/logs/collect_all_*.log`.

## Step 3 - Choose scope

| Scope | Use when | How |
| --- | --- | --- |
| `sample` | First run, "quick", "just a few", smoke before a full pass. | One language, small `repo_num` (e.g. 2) and `max_prs_per_repo` (e.g. 10) via env overrides. This creates a small block-local pool; `/curator:create-tasks` smoke uses a different bundled sample file. |
| `single-language` | User names one language. | `LANGUAGES=<lang>` override; other knobs from config. |
| `full` | All languages / no narrower scope. | Everything from `config.yaml -> pr_collection`. |

Any per-run change to `repo_num`, `max_prs_per_repo`, languages, or filter
thresholds should be shown as an explicit env override (below) and confirmed,
**not** written into `config.yaml` silently.

## Step 4 - Hydrate config and show the plan

```bash
source scripts/load_runtime_env.sh
load_runtime_env
```

This exports, from `config.yaml -> runtime_info.input.pr_collection`:

- `LEGOFLOW_CURATOR_COLLECT_LANGUAGES`, `LEGOFLOW_CURATOR_COLLECT_REPO_NUM`,
  `LEGOFLOW_CURATOR_COLLECT_MAX_PRS_PER_REPO`, `LEGOFLOW_CURATOR_COLLECT_OUTPUT_DIR`,
  `COLLECT_TOKEN_LIMIT`
- global filters: `LEGOFLOW_CURATOR_PR_MIN_STARS`, `LEGOFLOW_CURATOR_PR_MIN_MERGED_PRS`,
  `LEGOFLOW_CURATOR_PR_MIN_LANGUAGE_PERCENTAGE`, `LEGOFLOW_CURATOR_PR_MAX_DAYS_SINCE_PUSH`,
  `LEGOFLOW_CURATOR_PR_MIN_ISSUE_BODY_LENGTH`, `LEGOFLOW_CURATOR_PR_MIN_FILES_CHANGED`,
  `LEGOFLOW_CURATOR_PR_MAX_FILES_CHANGED`, `LEGOFLOW_CURATOR_PR_MAX_LINES_CHANGED`

Only vars still unset after the caller environment and block `.env` are filled
from `config.yaml`; the ignored block `.env` intentionally overrides the
caller environment. Per-language threshold overrides live in the collector's
`LANGUAGE_OVERRIDES` and take precedence over the globals.

Print a compact summary and get explicit confirmation before a full or
single-language run:

```text
legoflow-curator collect-prs plan
  scope        : <sample|single-language|full>
  languages    : <LEGOFLOW_CURATOR_COLLECT_LANGUAGES>
  repo_num     : <LEGOFLOW_CURATOR_COLLECT_REPO_NUM>
  max_prs/repo : <LEGOFLOW_CURATOR_COLLECT_MAX_PRS_PER_REPO>
  output_dir   : <LEGOFLOW_CURATOR_COLLECT_OUTPUT_DIR>
  token_limit  : <COLLECT_TOKEN_LIMIT> (of <N> unique file + env tokens)
  filters      : stars>=.. merged>=.. lang%>=.. files<=.. lines<=..
  logs         : artifacts/logs/collect_all_<stamp>.log
```

Full and single-language passes are long-running and consume GitHub API quota;
never launch either without a "yes".

## Step 5 - Launch

**Full / single-language / config-driven** — use the wrapper, which reads the
`LEGOFLOW_CURATOR_COLLECT_*` env (with direct env vars still winning):

```bash
# full: languages from config
bash scripts/collect_all_bg.sh

# single language: override just the language list
LANGUAGES=python bash scripts/collect_all_bg.sh
```

**Sample** — small foreground pass for a first check, overriding only the
knobs that make it small (thresholds still come from config/defaults):

```bash
source scripts/load_runtime_env.sh && load_runtime_env
python3 repos/legoflow-curator/tools/collect_prs_wo_image.py \
  --languages python \
  --repo_num 2 \
  --max_prs_per_repo 10 \
  --output_dir artifacts/collected_prs \
  --disable_progress_bar
```

Output is `<output_dir>/{language}_pr_ids.txt`, one `owner/repo:pr-NUMBER`
per line. `/curator:create-tasks` consumes these files automatically only when
`output_dir` is `artifacts/collected_prs`.

## Step 6 - Report and hand off

After launch print:

- background PID and `artifacts/logs/collect_all_<stamp>.log` (or foreground result)
- output path and, once files appear, `wc -l <output_dir>/*_pr_ids.txt`
- how to stop (`kill <PID>`) or tail the log
- next step after the collector exits successfully: `/curator:create-tasks`

For a background full run, poll once after a short delay to confirm the
process started and is writing to the log. Do not start `/curator:create-tasks`
merely because the output files have appeared: `legoflow-curator create` snapshots its
input file at startup, so PR IDs appended later are not included in that run.

## Guardrails

- Never run two collectors concurrently in the same block.
- Never write GitHub tokens or API keys into `config.yaml` or logs; use shell
  environment variables or ignored local token/env files.
- Do not edit `repos/legoflow-curator/` source while collecting.
- Per-run parameter changes go through env overrides shown to the user, not
  silent `config.yaml` edits.
- Do not delete existing `*_pr_ids.txt` or `*_prs.jsonl` unless the user asks;
  the collector resumes/extends prior state.
## Shared LegoFlow CLI

The canonical execution command for this skill is `./bin/legoflow collect-prs`. Claude Code and Codex use this same command; this skill supplies the agent-specific confirmation, reporting, and artifact-analysis workflow around it.
## LegoFlow Command Convention

The canonical command for this skill is `/curator:collect-prs`. The shared CLI accepts the same command as `./bin/legoflow /curator:collect-prs` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
