# 端到端管线验证日志

**日期**: 2026-04-20
**环境**: Linux 5.15.0, Python 3.12, Docker
**模型**: OPENAI_MODEL=glm-5-urg, ANTHROPIC_MODEL=claude-sonnet-4-6

## 目标

验证完整的 SWE-gen 管线在重构后的目录结构下能正确运行。

## 执行步骤

### Step 1: PR 收集（跳过）

`collect_prs_wo_image.py` 脚本需要较长时间调用 GitHub API。本次验证使用 `artifacts/collected_prs/python_pr_ids.txt` 中已有的 10 个 PR（来自 tox-dev/tox, AnswerDotAI/RAGatouille, electricitymaps/electricitymaps-contrib, morpheus65535/bazarr）。

注意：收集脚本使用 append 模式（`a+`），重复运行不会覆盖已有的 PR ID。

### Step 2: 任务创建

```bash
swegen create \
  --input-ids-file ./artifacts/collected_prs/python_pr_ids.txt \
  --max-pr 1 --n-concurrent 1 \
  --output ./artifacts/swe_tasks/py-cc \
  --timeout 600 --cc-timeout 400 \
  --no-require-issue --min-source-files 1 --max-source-files 10
```

处理了 3 个 PR（2 个过滤/失败，1 个成功）：
- `tox-dev/tox#3814`：验证失败
- `tox-dev/tox#3813`：成功（skeleton 14.6s，CC session 417.0s，NOP reward=0，Oracle reward=1）
- Task ID: `tox-dev__tox-3813`
- 总耗时: 15m 28s

### Step 3: 评分

```bash
python tools/score_tasks.py --dir artifacts/swe_tasks/py-cc --update-toml
```

评分 4 个任务（2 个已有样本 + 1 个新验证 + 1 个未验证），平均难度 7.1/10，耗时 <1s。

### Step 4: 提取

```bash
python extract_verified_tasks.py
```

提取 9 个已验证任务（8 个原始样本 + 1 个新创建），输出到 `outputs/`。

## 验证总结

| 步骤 | 命令 | 状态 | 耗时 |
|------|------|------|------|
| 安装 | `pip install -e .` | OK | 5s |
| 收集 PR | `collect_prs_wo_image.py` | 跳过（使用样本） | — |
| 创建任务 | `swegen create` | 1 个任务验证通过 | 15m 28s |
| 评分 | `score_tasks.py` | 4 个任务已评分 | <1s |
| 提取 | `extract_verified_tasks.py` | 9 个任务已提取 | <1s |

## 遇到的问题

无。管线在重构后的路径下运行正常。
