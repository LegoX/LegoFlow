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
pip install -e .

# 设置必要的环境变量
export GITHUB_TOKENS="ghp_your_token"
export OPENAI_API_KEY="sk-your-key"
export OPENAI_API_BASE_URL="https://your-api.com/v1"

# 运行管线
python tools/collect_prs_wo_image.py --languages python --output_dir ./artifacts/collected_prs
swegen create --input-ids-file ./artifacts/collected_prs/python_pr_ids.txt \
  --output ./artifacts/swe_tasks/py-cc --n-concurrent 8
python extract_verified_tasks.py
```

## 文档

| 文件 | 受众 | 内容 |
|------|------|------|
| `CLAUDE.md` | AI agent | 操作手册：环境配置、工作流命令、下游接口 |
| `docs/Detail_Pipeline_SWE-gen.md` | 开发者 | 管线技术细节：架构、算法、各阶段说明 |
| `docs/experiment-log.md` | 开发者 | 端到端管线验证记录 |
| `outputs.yaml` | 下游 agent | 定位和提取已验证 SWE 任务的 schema |

## 项目结构

```
src/swegen/              # 核心 Python 包
tools/                   # PR 收集和评分脚本
scripts/                 # 各语言的 create 脚本
artifacts/
  collected_prs/         # PR ID 列表
  swe_tasks/{lang}-cc/   # 各语言生成的任务
outputs/                 # 合并后的已验证任务
```

## License

Apache-2.0
