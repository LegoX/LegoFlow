---
name: dashboard
description: >
  Summarize swegen's progress across languages: PRs collected, tasks
  generated, verifiable_tasks.txt count, NOP/Oracle pass rate, last run
  duration. Reads `artifacts/swe_tasks/<lang>-cc/` and the per-language
  manifests; no live server needed for the textual view, but if a webui
  exists, launch it on a configured port. Read-only. Triggers on phrases
  like "swegen dashboard", "show swegen progress", "how many tasks does
  swegen have", "verifiable rate per language".
---

# /swegen:dashboard

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:dashboard` is the per-block "show me
what's happening" surface.

## Intent

1. **Textual summary** (always): scan `artifacts/swe_tasks/<lang>-cc/`,
   count entries, read `verifiable_tasks.txt`, compute pass rate, print
   one row per language.
2. **Optional webui** (later): if a swegen-specific dashboard exists,
   launch it (background) and print the URL.

## TODO

- [ ] Decide where the per-language progress lives — `artifacts/index.yaml`
      or per-language manifest files.
- [ ] Build a tiny webui (or reuse the root dashboard) for the visual view.
