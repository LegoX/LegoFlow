# SWE-gen Program

## Purpose
SWE-gen converts merged GitHub PRs into reproducible Harbor evaluation tasks for LLM training and benchmarking. It is the data generation entry point for the pipeline: it produces the raw task corpus that data_composer aggregates and filters.

The underlying repo is `SWE-gen-orig`. It uses a reversed-baseline strategy: clones the repo at HEAD (fix applied), applies `bug.patch` to revert to the buggy state, and stores test files separately to prevent agent tampering.

## WorkSpace Definition

```yaml
name: swe_gen
summary: >
  Idle. No active generation run.
  Last run produced N tasks from repo X.
  Next: trigger a farm run or add a new seed repo.

repo:
  path: ../SWE-gen-orig

program: program.md

inputs:
  seed_repos:
    description: GitHub repos to farm PRs from (owner/repo format)
    source: workspace.config.seed_repos
  github_token:
    description: GitHub API token for PR fetching
    source: environment secret
  openai_api_key:
    description: OpenAI API key for PR substantiality evaluation (gpt-5.2)
    source: environment secret
  anthropic_api_key:
    description: Anthropic API key for Claude Code task completion
    source: environment secret

config:
  output_dir:
    description: Directory where generated tasks are written
    editable_by: [human]
  min_source_files:
    description: Minimum number of source files a PR must touch (default 3)
    editable_by: [human, agent]
  max_source_files:
    description: Maximum number of source files to avoid large refactors (default 10)
    editable_by: [human, agent]
  require_issue:
    description: Whether to require a linked GitHub issue (default true)
    editable_by: [human, agent]
  validate:
    description: Whether to run Harbor NOP/Oracle validation after generation (default true)
    editable_by: [human, agent]
  use_cache:
    description: Whether to reuse cached Dockerfile/test.sh patterns per repo (default true)
    editable_by: [human, agent]
  cc_timeout:
    description: Claude Code session timeout in seconds (default 3200)
    editable_by: [human, agent]

status:
  phase:
    description: idle | fetching | evaluating | skeleton | claude_code | validating | completed | failed
  current_repo:
    description: GitHub repo currently being farmed
  tasks_generated:
    description: Total tasks successfully generated in the current run
  tasks_failed:
    description: Total tasks that failed or were skipped in the current run
  last_run_at:
    description: Timestamp of the last completed run

outputs:
  task_corpus:
    description: Directory of generated Harbor tasks ready for data_composer ingestion
  task_count:
    description: Number of valid tasks produced
  failed_prs:
    description: List of PRs that were skipped or failed, with reasons

artifacts:
  - type: task_corpus
    path: tasks/
    producer: swe_gen
    description: Generated Harbor task directories (one per PR)
  - type: generation_log
    path: .swegen/create.jsonl
    producer: swe_gen
    description: Deduplication log of all processed PRs
  - type: task_references
    path: .swegen/task_references.json
    producer: swe_gen
    description: Cached Dockerfile and test.sh patterns per repo

memory:
  path: memory/
  description: Notes on seed repo selection, failure patterns, and generation quality observations

subspace: {}
```

## CLI Reference

```bash
# Generate a single task from a PR
uv run swegen create --repo owner/repo --pr 1234

# Continuously farm PRs from a repo
uv run swegen farm --repo owner/repo

# Validate an existing task
uv run swegen validate tasks/owner__repo-1234/

# Analyze task quality (runs agent trials and classifies outcomes)
uv run swegen analyze tasks/owner__repo-1234/
```

## Notes
- Upstream dependencies: GitHub API access, Docker, Claude Code CLI
- Failure modes: TrivialPRError, MissingIssueError, ValidationError (NOP/Oracle mismatch), API rate limits
- Open questions: which seed repos to prioritize; target task count per training iteration
