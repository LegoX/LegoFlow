# Curator Memory

Long-form context, decisions, and experiment records for the curator block.

---

## Experiment Log

> Source: memory/experiment-log.md (2026-04-20)

End-to-end pipeline validation log — performed entirely and autonomously by an AI agent (Claude Code).

**Date**: 2026-04-20 | **Environment**: Linux 5.15.0, Python 3.12, Docker | **Models**: OPENAI_MODEL=glm-5-urg, ANTHROPIC_MODEL=claude-sonnet-4-6

### Pipeline validation steps

| Step | Command | Status | Time |
|------|---------|--------|------|
| Install | `pip install -e .` | OK | 5s |
| Collect PRs | `collect_prs_wo_image.py` | skipped (used samples) | — |
| Create tasks | `swegen create` | 1 task verified | 15m 28s |
| Score | `score_tasks.py` | 4 tasks scored | <1s |
| Extract | `python scripts/extract_verified_tasks.py` | 9 tasks extracted | <1s |

Verified task from this run: `tox-dev__tox-3813`

---

## Adaptive Tuning Validation

> Source: memory/adaptive-tuning-validation.md (2026-04-22)

**Machine**: hk01dgx060 | **Python**: 3.12.2 | **Docker**: 29.0.0

### Validated cycle

1. `read_params.py` correctly reads params from `inputs.yaml`
2. `swegen create` uses timeout/cc_timeout from inputs.yaml
3. Monitor cycle collects status and updates `inputs.yaml` status fields
4. Adaptive decision: `success_rate 0.50 > 0.4` → `n_concurrent` 16 → 20
5. PR pool check: `10 < 100` threshold → collect_pr_needed triggered
6. Decision logged to `logs/adaptive_decisions.jsonl`

### Parameter bounds (from inputs.yaml)

| Param | Min | Max | Step |
|-------|-----|-----|------|
| timeout | 2400 | 5400 | 400 |
| cc_timeout | 1800 | 4200 | 300 |
| n_concurrent | 4 | 32 | 4 |

### Rules

- Adjust at most 1 parameter per language per cycle
- Wait ≥ 2 cycles (60 min) between adjustments for the same language
- Do NOT restart running create scripts unless `zero_success_streak >= 3`
- If `success_rate < 0.15` for 2 consecutive cycles → increase timeout (+400) or cc_timeout (+300)
- If `success_rate > 0.4` and `n_concurrent < 24` → increase n_concurrent (+4)
