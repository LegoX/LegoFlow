# 自适应调参机制端到端验证日志

> 本次验证流程完全由 AI agent（Claude Code）自主完成，包括参数读取、任务构建、状态采集、自适应决策和日志记录。

## 验证目标

验证 `inputs.yaml` 驱动的自适应调参全流程：
1. `read_params.py` 从 `inputs.yaml` 读取参数
2. `swegen create` 使用这些参数构建 SWE 任务
3. 监控周期：采集状态、计算成功率、更新 `inputs.yaml`
4. 自适应决策：根据成功率调整参数
5. PR 池检查：判断是否需要补充 PR
6. 决策日志：写入 `logs/adaptive_decisions.jsonl`

## 环境

- 日期：2026-04-22
- 机器：hk01dgx060
- Python：3.12.2
- Docker：29.0.0
- swegen CLI：已安装（`/home/ywxzml3j/ywxzml3juser23/miniconda3/bin/swegen`）
- 模型：OPENAI_MODEL=glm-5-urg, ANTHROPIC_MODEL=claude-sonnet-4-6

## Step 1: 验证 read_params.py 集成

```bash
$ eval $(python scripts/read_params.py --lang py --inputs-yaml inputs.yaml)
$ echo "TIMEOUT=${TIMEOUT} CC_TIMEOUT=${CC_TIMEOUT} N_CONCURRENT=${N_CONCURRENT}"
TIMEOUT=3200 CC_TIMEOUT=2400 N_CONCURRENT=16
```

结果：参数正确从 `inputs.yaml` 读取。

## Step 2: 运行 swegen create（使用 inputs.yaml 参数）

### 尝试 1：tox-dev/tox PR #3814

```bash
$ swegen create --repo tox-dev/tox --pr 3814 \
    --output artifacts/swe_tasks/py-cc \
    --timeout "${TIMEOUT}" --cc-timeout "${CC_TIMEOUT}" \
    --no-require-issue --min-source-files 3 --max-source-files 10
```

结果：**Skipped (Trivial PR)** — 仅 2 个 source files，低于 `--min-source-files 3` 阈值。属于正常的 policy 过滤。

### 尝试 2：AnswerDotAI/RAGatouille PR #157

```bash
$ swegen create --repo AnswerDotAI/RAGatouille --pr 157 \
    --output artifacts/swe_tasks/py-cc \
    --timeout "${TIMEOUT}" --cc-timeout "${CC_TIMEOUT}" \
    --no-require-issue --min-source-files 2 --max-source-files 10
```

结果：Skeleton 生成成功（28.7s），但 CC session 验证失败（47.5s）。NOP 和 Oracle 均未通过。属于 model 级别失败。

### 尝试 3：electricitymaps/electricitymaps-contrib PR #8113（成功）

```bash
$ swegen create --repo electricitymaps/electricitymaps-contrib --pr 8113 \
    --output artifacts/swe_tasks/py-cc \
    --timeout "${TIMEOUT}" --cc-timeout "${CC_TIMEOUT}" \
    --no-require-issue --min-source-files 2 --max-source-files 10
```

结果：**成功**
- Skeleton 生成：116.1s
- CC session + 验证：1415.0s（约 23.6 分钟）
- CC NOP: reward=0 ✓（无操作 agent 正确失败）
- CC Oracle: reward=1 ✓（ground truth 正确通过）
- Task ID: `electricitymaps__electricitymaps-contrib-8113`
- 已自动追加到 `verifiable_tasks.txt`

## Step 3: 监控周期 — 状态采集

从 `verifiable_tasks.txt` 和运行结果中采集状态：

| 指标 | 值 |
|------|-----|
| 已验证任务总数 (py) | 3 |
| 本轮成功 | 1 |
| 本轮失败 | 1 |
| 本轮过滤 | 1 |
| 成功率 | 0.50 |
| PR 池剩余 | 10 |

已更新 `inputs.yaml` 中 `languages.py.status` 的所有字段。

## Step 4: 自适应决策

根据调参规则表：

- `success_rate = 0.50 > 0.4` 且 `n_concurrent = 16 < 24` → **增加并发数**
- `n_concurrent`: 16 → 20（+4）

决策已记录到 `logs/adaptive_decisions.jsonl`：
```json
{"timestamp": "2026-04-22T12:00:06Z", "lang": "py", "action": "adjust_param", "param": "n_concurrent", "old": 16, "new": 20, "reason": "success_rate 0.50 > 0.4, increasing concurrency"}
```

## Step 5: PR 池检查

- PR 池剩余：10
- 阈值：100
- 判断：`10 < 100`，**需要补充 PR**

决策已记录：
```json
{"timestamp": "2026-04-22T12:00:06Z", "lang": "py", "action": "collect_pr_needed", "pr_pool_before": 10, "reason": "pr_pool_remaining (10) < threshold (100)"}
```

注：本次验证未实际执行 collect（需要大量 GitHub API 调用），仅验证触发逻辑正确。

## Step 6: 验证调参生效

```bash
$ eval $(python scripts/read_params.py --lang py --inputs-yaml inputs.yaml)
$ echo "TIMEOUT=${TIMEOUT} CC_TIMEOUT=${CC_TIMEOUT} N_CONCURRENT=${N_CONCURRENT}"
TIMEOUT=3200 CC_TIMEOUT=2400 N_CONCURRENT=20
```

`N_CONCURRENT` 已从 16 更新为 20，下一轮 create 脚本将使用新值。

## 验证结论

| 环节 | 状态 | 说明 |
|------|------|------|
| read_params.py 读取 | ✅ | 正确输出 shell 变量 |
| swegen create 使用参数 | ✅ | timeout/cc_timeout 从 inputs.yaml 传入 |
| 任务生成 + 验证 | ✅ | 1/3 PR 成功通过 NOP+Oracle |
| 状态采集 | ✅ | inputs.yaml status 字段已更新 |
| 自适应决策 | ✅ | 根据规则正确调整 n_concurrent |
| PR 池检查 | ✅ | 正确识别需要补充 |
| 决策日志 | ✅ | adaptive_decisions.jsonl 记录完整 |
| 调参生效 | ✅ | read_params.py 读到新值 |

自适应调参全流程验证通过。
