# Scaled batch verification — results (2026-06-23)

Cost-controlled pilot run of `scripts/batch_verify.sh` (+ targeted top-ups) to
produce **≥3 verified tasks per domain** across 4 self-contained domains, then
convert to harbor 1.1 via `scripts/extract_verified_tasks.py`.

## Setup

- LLM endpoint: `https://yunwu.ai/v1`, model `claude-haiku-4-5-20251001`
- Questions: 480 unique SO Q&A from a 6-subround deduped scrape
  (`scrape_so_questions.sh div 80 6`), bucketed by `tag_filter`, score-sorted.
- Validator: terminal-lego Docker round-trip, `val_timeout=900`.

## Result: 4/4 domains reached the target (12 verified tasks, all harbor 1.1)

| Domain | Candidates generated | Verified | Yield |
|---|---|---|---|
| python-ecosystem | 6 | 3 | 50% |
| build-editor-tooling | 8 | 3 | 37% |
| file-text-processing | 9 | 3 | 33% |
| core-terminal-os | 21 | 3 | 14% |
| **total** | **44** | **12** | **27%** |

- Total LLM spend: ~7.1M tokens (~$7–14 on a haiku-class endpoint).
- All 12 merged `task.toml` validated as harbor 1.1 (`schema_version="1.1"`,
  `[task]`, `memory_mb`). Merged ids in
  `artifacts/merged_terminal_tasks/verifiable_tasks.txt`.

## Yield is strongly domain-dependent

- **High-yield** (33–50%): python-ecosystem, build-editor-tooling,
  file-text-processing — self-contained CLI/file/build tasks validate cleanly.
- **Low-yield** (~14%): core-terminal-os — the LLM frequently picks
  un-containerizable tasks (clipboard piping needs an X server) or hallucinates
  apt packages (`mongodb`, `mongosh`, `net-tools` that don't install on the base
  image) → `build_failed` / `reward=0`. The validator correctly rejects these;
  the loss is upstream **terminal-lego generator quality** (read-only, not fixable
  here). `batch_verify.sh`'s `CAND_CAP` absorbs this by generating more chunks
  for low-yield domains.

## Improvements this run drove (all committed)

- `scrape_so_questions.sh`: multi-subround deduped scraping (a single round is
  dominated by a few high-weight tags → poor domain coverage). 480 questions, 0
  fallback (was 51% before the networking tag_filter fix).
- `bucket_questions.py`: score-sorted buckets so `--limit` consumes the
  best-specified questions first.
- `networking-services.tag_filter`: added the socket family
  (`network-programming`, `sockets`, `udp`, …) that was previously uncovered.
- `batch_verify.sh` + `create_domain.sh [limit] [start]`: cost-controlled,
  chunked, target-driven generation with per-chunk candidate dirs.

## Reproduce

```bash
cd subblock/terminalgen && source artifacts/envs/terminalgen-env/bin/activate
export OPENAI_API_KEY=... OPENAI_API_BASE_URL=https://yunwu.ai/v1 \
       MODEL_NAME=claude-haiku-4-5-20251001 SO_API_KEY=... \
       DOCKER_HOST=unix:///var/run/docker.sock
bash scripts/scrape_so_questions.sh div 80 6
CHUNK=6 CAND_CAP=24 bash scripts/batch_verify.sh 3 \
  core-terminal-os python-ecosystem build-editor-tooling file-text-processing
python scripts/extract_verified_tasks.py
```
