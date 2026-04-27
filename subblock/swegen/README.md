# SWE-gen: 多语言 SWE-Bench 数据构建管线

自动化管线，从 GitHub PR 构建经过验证的 SWE-Bench 任务，覆盖 8 种编程语言：Python, JavaScript, TypeScript, Go, C, C++, Java, Rust。

## 功能概述

1. **收集 PR** — 通过两阶段筛选发现合格的 GitHub 仓库和 PR
2. **生成 SWE 任务** — 将 PR 转换为基于 Docker 的任务环境（含测试）
3. **验证** — NOP/Oracle 双重验证确保任务正确性
4. **评分** — 5 维度静态难度评分（Easy/Medium/Hard）
5. **提取** — 合并已验证任务供下游使用

## 快速开始

```bash
# 安装
pip install -e repos/swegen/

# 设置必要的环境变量
export GITHUB_TOKENS="ghp_your_token"
export OPENAI_API_KEY="sk-your-key"
export OPENAI_API_BASE_URL="https://your-api.com/v1"

# 运行管线
python repos/swegen/tools/collect_prs_wo_image.py --languages python --output_dir ./artifacts/collected_prs
swegen create --input-ids-file ./artifacts/collected_prs/python_pr_ids.txt \
  --output ./artifacts/swe_tasks/py-cc --n-concurrent 8
python scripts/extract_verified_tasks.py
```

## 文档

| 文件 | 受众 | 内容 |
|------|------|------|
| `CLAUDE.md` | AI agent | 项目操作手册。CLAUDE.md 是 [Claude Code](https://code.claude.com/docs/zh-CN/memory) 的项目级指令文件，用于告诉 AI agent 如何在本项目上工作：构建/测试命令、目录结构、编码规范、常见工作流。目标 200 行以内，使用 markdown 标题和项目符号组织，指令需具体可验证（如"使用 2 空格缩进"而非"正确格式化代码"） |
| `memory/experiment-log.md` | 开发者 | AI agent (Claude-code) 端到端自动化执行管线的验证记录 |
| `outputs.yaml` | 下游 agent | 定位和提取已验证 SWE 任务的 schema |

## 项目结构

```
artifacts/
  collected_prs/             # 各语言的 PR ID 列表
  swe_tasks/{lang}-cc/       # 各语言生成的 SWE 任务
  logs/                      # 自适应调参日志和 create 日志
memory/                      # 长期记忆（实验日志、调参验证、决策记录）
outputs/                     # 合并后的已验证任务（供下游使用）
repos/swegen/                # 核心 Python 包 + 工具
  src/swegen/                # Python 包（CLI、任务生成、验证、评分）
  tests/                     # 单元测试
  tools/                     # PR 收集和评分脚本
scripts/                     # 各语言的 create 脚本
  extract_verified_tasks.py  # 从 artifacts/ 提取已验证任务到 outputs/
inputs.yaml                  # 上游输入配置（预留）
outputs.yaml                 # 下游 agent 接口：定义如何定位已验证任务
```

## 自适应调参机制

AI agent（Claude Code）可以根据各语言的 SWE 数据产出效率，自动调整管线参数。

### 工作原理

1. **参数配置**：`inputs.yaml` 定义每种语言的 `timeout`、`cc_timeout`、`n_concurrent` 三个可调参数及运行状态
2. **定期监控**：AI agent 每 30 分钟检查各语言的成功率（从 `verifiable_tasks.txt` 和 batch state 统计）
3. **按需调参**：成功率偏低时增加超时，成功率高时提升并发，PR 池不足时自动补充
4. **安全约束**：每次仅调整 1 个参数，间隔 ≥ 60 分钟，参数有上下界限制

### 可调参数

| 参数 | 范围 | 步长 | 作用 |
|------|------|------|------|
| `timeout` | 2400-5400s | 400 | swegen create 单 case 超时 |
| `cc_timeout` | 1800-4200s | 300 | Claude Code session 超时 |
| `n_concurrent` | 4-32 | 4 | 并发数 |

### 使用方式

```bash
# 各语言 create 脚本自动从 inputs.yaml 读取参数
bash scripts/create_py.sh

# 或批量启动
bash scripts/create_all_bg.sh
```

调参决策记录在 `artifacts/logs/adaptive_decisions.jsonl`。详见 `memory/adaptive-tuning-validation.md`。

## License

Apache-2.0
