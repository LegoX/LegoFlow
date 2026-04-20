# SWE-gen Agent Workbench

Automated pipeline that converts GitHub PRs into verified SWE-Bench tasks across 8 programming languages.

## Environment Setup

### Required Environment Variables

Set these BEFORE running any command:

| Variable | Purpose | Example |
|----------|---------|---------|
| `GITHUB_TOKENS` | Comma-separated GitHub tokens for API access | `ghp_xxx,ghp_yyy` |
| `OPENAI_API_KEY` | LLM API key (auto-mirrored to ANTHROPIC_API_KEY) | `sk-xxx` |
| `OPENAI_API_BASE_URL` | OpenAI-compatible API endpoint | `https://api.example.com/v1` |
| `OPENAI_MODEL` | Model for PR evaluation + instruction generation | `gpt-4o` |
| `ANTHROPIC_MODEL` | Model for Claude Code SDK (task completion) | `claude-sonnet-4-6` |

Optional: place GitHub tokens in `gh_token.txt` (one per line) at project root or `~/gh_token.txt`.

### Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Verify Docker

```bash
docker run --rm hello-world
```

## Core Workflow

### Step 1: Collect PRs

```bash
python tools/collect_prs_wo_image.py \
  --languages python \
  --repo_num 100 \
  --max_prs_per_repo 50 \
  --output_dir ./artifacts/collected_prs
```

Output: `artifacts/collected_prs/{language}_pr_ids.txt` (format: `owner/repo:pr-NUMBER`)

Supported languages: `python`, `javascript`, `typescript`, `go`, `c`, `cpp`, `java`, `rust`

### Step 2: Create SWE Tasks

```bash
swegen create \
  --input-ids-file ./artifacts/collected_prs/python_pr_ids.txt \
  --n-concurrent 8 \
  --output ./artifacts/swe_tasks/py-cc \
  --timeout 3600 \
  --cc-timeout 2400 \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10
```

Output: task directories under `artifacts/swe_tasks/{lang}-cc/`. Verified task IDs appended to `verifiable_tasks.txt`.

Per-language scripts with tuned parameters: `bash scripts/create_{lang}.sh` where lang = py, js, ts, go, c, cpp, java, rust.

### Step 3: Validate (optional, built into create)

```bash
swegen validate ./artifacts/swe_tasks/py-cc --max-parallel 8
```

### Step 4: Score Tasks

```bash
python tools/score_tasks.py --dir artifacts/swe_tasks/py-cc --update-toml
```

### Step 5: Extract Verified Tasks

```bash
python extract_verified_tasks.py
```

Reads `verifiable_tasks.txt` from each language, copies verified task directories to `outputs/`.

## Downstream Agent Interface

Downstream agents consume verified SWE tasks for trajectory inference:

1. Read `outputs.yaml` for data location schema
2. Run `python extract_verified_tasks.py` to populate `outputs/`
3. Each task in `outputs/{task_id}/` contains:
   - `instruction.md` — problem description (input to the solving agent)
   - `environment/Dockerfile` — Docker build environment
   - `environment/bug.patch` — patch that introduces the bug
   - `solution/fix.patch` — ground truth fix
   - `tests/test.sh` — verification script (writes reward to `/logs/verifier/reward.txt`)

## Directory Layout

```
src/swegen/           # Core Python package (CLI, task generation, validation, scoring)
tools/                # Standalone scripts (PR collection, batch scoring)
scripts/              # Per-language create scripts with tuned parameters
artifacts/
  collected_prs/      # PR ID lists (input to swegen create)
  swe_tasks/          # Generated SWE tasks per language ({lang}-cc/)
outputs/              # Merged verified tasks (populated by extract_verified_tasks.py)
```

## Key Files

| File | Purpose |
|------|---------|
| `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` | List of verified task IDs per language |
| `outputs.yaml` | Schema for downstream agents to find verified tasks |
| `extract_verified_tasks.py` | Merges all verified tasks into `outputs/` |
| `inputs.yaml` | Reserved for upstream agent configuration |

## Coding Standards

- Python 3.12, formatted with `black` + `ruff` (line-length=100)
- Install: `pip install -e .`
- Run tests: `pytest tests/`
- CLI entry point: `swegen` (defined in pyproject.toml)
