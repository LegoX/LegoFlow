# Curator Memory

Long-form context, decisions, and experiment records for the curator block.

---

## Experiment Log

> Source: memory/experiment-log.md (2026-04-20)

端到端管线验证日志 — 本次验证流程完全由 AI agent（Claude Code）自主完成。

**日期**: 2026-04-20 | **环境**: Linux 5.15.0, Python 3.12, Docker | **模型**: OPENAI_MODEL=glm-5-urg, ANTHROPIC_MODEL=claude-sonnet-4-6

### Pipeline validation steps

| 步骤 | 命令 | 状态 | 耗时 |
|------|------|------|------|
| 安装 | `pip install -e .` | OK | 5s |
| 收集 PR | `collect_prs_wo_image.py` | 跳过（使用样本） | — |
| 创建任务 | `swegen create` | 1 个任务验证通过 | 15m 28s |
| 评分 | `score_tasks.py` | 4 个任务已评分 | <1s |
| 提取 | `python scripts/extract_verified_tasks.py` | 9 个任务已提取 | <1s |

Verified task from this run: `tox-dev__tox-3813`

---

## Adaptive Tuning Validation

> Source: memory/adaptive-tuning-validation.md (2026-04-22)

**机器**: hk01dgx060 | **Python**: 3.12.2 | **Docker**: 29.0.0

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
