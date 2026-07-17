# Terminal-gen Memory

Long-form context, decisions, and experiment records for the terminalgen block.

---

## Architecture

terminalgen wraps the pinned, read-only [terminal-lego](https://github.com/SWE-Lego/terminal-lego)
pipeline as a `subblock/` sibling of swegen. Three stages, all from terminal-lego:

1. **scrape** — `repos/terminal-lego/scraper/so_scraper.py` pulls StackOverflow
   questions with accepted answers. `scripts/scrape_so_questions.sh` then buckets
   them into 13 domains by `tag_filter` (`scripts/bucket_questions.py`).
2. **generate** — `repos/terminal-lego/generator/task_generator.py` turns each
   Q&A into a Terminal-Bench task (instruction → environment → solution →
   difficulty → tests → dockerfile, ~7 LLM calls). Emits terminal-lego v1.0 `task.toml`.
3. **validate** — `repos/terminal-lego/validator/validate_tasks.py` runs a Docker
   round-trip (build → solve → test → read `/logs/verifier/reward.txt`); only
   reward=1.0 tasks are kept.

`scripts/extract_verified_tasks.py` optionally merges verified tasks (verbatim,
still v1.0) into a flat `artifacts/merged_terminal_tasks/`. No schema conversion
— downstream/harbor reads terminal-lego v1.0 directly.

## Domain bucketing rationale

The 13 domains follow arXiv 2606.03461 Table 11 (Terminal-Lego paper). Each
maps a subset of terminal-lego's 54 scraper tags via `config.yaml`'s per-domain
`tag_filter`. A question can fall in multiple buckets; tasks dedup downstream.
Zero-match questions fall back to `core-terminal-os`.

## Adaptive tuning rules

- Tunables: `gen_workers` [1,16], `val_workers` [1,8], `val_timeout` [120,1800] (keep ≥900 on throttled networks — test.sh bootstraps uv over the network).
- Adjust at most 1 parameter per domain per cycle; wait ≥ 2 cycles (60 min)
  between adjustments for the same domain.
- If `success_rate < 0.15` for 2 consecutive cycles → increase `val_timeout` (+60).
- If `success_rate > 0.4` and `gen_workers < 12` → increase `gen_workers` (+1).
- Do NOT restart running create scripts unless `zero_success_streak >= 3`.
- Log every decision to `artifacts/logs/adaptive_decisions.jsonl`.

## Known failure modes

- **Generator picks internet/credential-dependent topics** (e.g. Let's Encrypt
  live cert issuance, cloud APIs). These fail Docker validation because the
  container has no live network/credentials. Mitigation: keep `tag_filter` toward
  self-contained CLI/file/text tasks; the validator silently drops them (reward=0).
- **StackExchange 429** when `SO_API_KEY` is unset (300/day shared by IP). Set a
  real key for 10000/day.
- **Generator endpoint**: terminal-lego reads the LLM base URL from `--api-base`,
  NOT `OPENAI_API_BASE`. `create_domain.sh` always passes `$OPENAI_API_BASE_URL`.
- **Validator task discovery**: only directories prefixed `task_*` are scanned.

## Proof point

The known-good fixture `tests/smoke/fixtures/https-nginx-cert-setup` (an HTTPS+
Nginx task, terminal-lego v1.0) was generated and validated end-to-end at
reward=1.0. `tests/smoke/verify.sh` replays it deterministically (no LLM/SO).
