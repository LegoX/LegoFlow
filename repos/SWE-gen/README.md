# SWE-gen: Multilingual SWE-Bench Data Construction Pipeline

Automated pipeline for constructing verified SWE-Bench tasks from GitHub PRs across 8 programming languages: Python, JavaScript, TypeScript, Go, C, C++, Java, Rust.

## What It Does

1. **Collects PRs** — Discovers qualifying GitHub repositories and PRs via two-stage filtering
2. **Generates SWE Tasks** — Converts PRs into Docker-based task environments with tests
3. **Validates** — NOP/Oracle dual verification ensures task correctness
4. **Scores** — 5-dimension static difficulty scoring (Easy/Medium/Hard)
5. **Extracts** — Merges verified tasks for downstream consumption

## Quick Start

```bash
# Install
pip install -e .

# Set required environment variables
export GITHUB_TOKENS="ghp_your_token"
export OPENAI_API_KEY="sk-your-key"
export OPENAI_API_BASE_URL="https://your-api.com/v1"

# Run the pipeline
python tools/collect_prs_wo_image.py --languages python --output_dir ./artifacts/collected_prs
swegen create --input-ids-file ./artifacts/collected_prs/python_pr_ids.txt \
  --output ./artifacts/swe_tasks/py-cc --n-concurrent 8
python extract_verified_tasks.py
```

## Documentation

| File | Audience | Content |
|------|----------|---------|
| `CLAUDE.md` | AI agents | Operations manual: environment setup, workflow commands, downstream interface |
| `docs/README.md` | Developers | Detailed technical documentation: architecture, algorithms, performance data |
| `docs/experiment-log.md` | Developers | End-to-end pipeline validation record |
| `outputs.yaml` | Downstream agents | Schema for locating and extracting verified SWE tasks |

## Project Structure

```
src/swegen/              # Core Python package
tools/                   # PR collection and scoring scripts
scripts/                 # Per-language create scripts
artifacts/
  collected_prs/         # PR ID lists
  swe_tasks/{lang}-cc/   # Generated tasks per language
outputs/                 # Merged verified tasks
```

## License

Apache-2.0
