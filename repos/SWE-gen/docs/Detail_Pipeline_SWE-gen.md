# 多语言 SWE-Bench 数据构建管线 — 技术说明

> **基于**: [SWE-gen](https://github.com/abundant-ai/SWE-gen) by Abundant AI
> **覆盖语言**: Python, JavaScript, TypeScript, Go, C, C++, Java, Rust

---

## 概述

本项目在公开版 SWE-gen 基础上，构建了一套覆盖 8 种编程语言的端到端自动化数据生产管线。核心改进：

1. **Two-Stage PR Collection** — 仓库级 + PR 级两阶段筛选，8 语言差异化阈值
2. **Batch Processing** — 批量输入 + 状态持久化 + 断点续跑
3. **Relaxed Substantiality Filter** — 接受单文件 bug 修复，增加候选任务量
4. **Difficulty Scoring** — 5 维度零 API 调用静态评分

---

## 管线架构

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   PR Collection   │────▶│  Task Generation  │────▶│   Validation     │────▶│  Scoring & Output │
│                  │     │                  │     │                  │     │                  │
│ • GitHub API     │     │ • LLM Eval       │     │ • Docker Build   │     │ • 5-dim Scoring  │
│ • Repo Filter    │     │ • Skeleton Gen   │     │ • NOP Agent      │     │ • task.toml      │
│ • PR Filter      │     │ • CC Completion  │     │ • Oracle Agent   │     │ • Easy/Med/Hard  │
│ • 8-lang Config  │     │ • Batch + Resume │     │ • Pass/Fail      │     │                  │
└──────────────────┘     └──────────────────┘     └──────────────────┘     └──────────────────┘
```

---

## 1. Two-Stage PR Collection

公开版 SWE-gen 不包含 PR 收集模块。本管线实现了完整的两阶段收集（`tools/collect_prs_wo_image.py`）。

### Stage 1: 仓库筛选

从 GitHub Search API 按语言搜索仓库，针对不同语言设计差异化阈值（Star 数、合并 PR 数、主语言占比等）。采用分段 Star 范围搜索（50-99, 100-199, ...5000+）绕过 API 单次 1,000 结果限制。

排除非工程项目（awesome-\*, tutorial\*, demo\*, dotfiles）。要求存在依赖管理文件和 CI/CD 配置。

### Stage 2: PR 筛选

对候选仓库的已合并 PR 进行精细筛选：
- 修改文件数 1–20（C/C++ 放宽到 30）
- 总改动行数 <1,000（C/C++ <1,500）
- 必须包含测试文件和代码文件修改
- 排除依赖更新（dependabot, renovate 等）、非功能性变更（docs, chore, style）、版本发布

测试文件识别覆盖 8 种语言的主流测试框架约定（pytest, jest, go test, cargo test, gtest 等）。

---

## 2. Task Generation

### Substantiality Evaluation（放宽策略）

公开版要求 PR 必须修改多个文件（≥2-3 个源文件）。本管线放宽为：单文件修改只要修复真实 bug（非平凡控制流），即视为 substantial。

动机：C、Rust 等系统级语言中，大量真实 bug 修复集中在单个源文件（空指针、竞态条件、内存泄漏）。

### Task Instruction 生成

`instruction.md` 仅描述问题现象（错误信息、期望 vs 实际行为），不暴露文件路径或修复方案。信息来源优先级：关联 Issue > PR 标题/正文 > 测试文件内容 > LLM 生成。

### Claude Code 集成

任务骨架生成后，由 Claude Code（CC）补全 Dockerfile 和 test.sh。CC 分析仓库结构，自动检测语言、运行时、构建系统和测试框架。

**Reference Task Caching**：同一仓库已有成功任务时，从 `.swegen/task_references.json` 加载缓存的参考任务，CC 仅需调整测试路径而非从零分析。

与公开版的差异：
- 超时差异化：编译型语言预配置更长超时（7200s+ vs 默认 3600s）
- 文件数差异化：不同语言设置不同的 `--max-source-files`
- 第三方模型支持：`_resolve_sdk_model()` 支持非 Claude 模型接入
- JSON 容错：`_extract_and_fix_json()` 处理非标准 JSON 格式

### NOP/Oracle 双重验证

| 验证类型 | 操作 | 期望结果 | 目的 |
|---------|------|---------|------|
| NOP Agent | 不做修改，直接运行测试 | 测试失败 | 确认 bug 存在且可复现 |
| Oracle Agent | 应用 fix.patch，运行测试 | 测试通过 | 确认修复方案有效 |

### Batch Processing

公开版仅支持基础并发（3-5 worker），无状态持久化。本管线实现：
- 文件批量输入，每个输入文件对应状态文件（`.swegen-create-batch/{hash}.json`）
- 自动跳过已完成 PR，对基础设施失败（网络、Docker、timeout）自动重试
- `run_fingerprint` 变化时自动重新处理非成功 case

---

## 3. Difficulty Scoring

公开版不包含难度评分。本管线新增 5 维度零 API 调用的静态评分（`src/swegen/scoring.py`）：

| 维度 | 权重 | 评分依据 |
|------|------|---------|
| Patch Scope | 30% | 修改文件数 + 改动行数 |
| Logic Complexity | 25% | 新增函数/类/方法数 + 净增代码行数 |
| Test Complexity | 20% | 测试文件数 + 测试代码行数 |
| Context Breadth | 15% | 变更涉及的目录数 |
| Instruction Complexity | 10% | 任务描述字符数 |

加权和映射至 1–10 分。难度标签：Easy (1–3), Medium (4–7), Hard (8–10)。

评分已集成到管线的所有任务产出路径，任务生成成功后即时评分。

---

## 4. 任务目录结构

每个成功生成的 SWE 任务包含以下标准化文件：

```
{repo_owner}__{repo_name}-{pr_number}/
├── task.toml              # 元数据：难度评分、类别、超时配置
├── instruction.md         # 自然语言任务描述
├── environment/
│   ├── Dockerfile         # Docker 构建环境定义
│   └── bug.patch          # 引入 bug 的 patch
├── solution/
│   ├── fix.patch          # 修复 bug 的 patch
│   └── solve.sh           # 应用 fix.patch 的脚本
└── tests/
    ├── test.sh            # 测试执行入口
    └── [test files]       # 原始测试文件
```

---

## 5. 项目结构

```
src/swegen/                    # 核心 Python 包
├── create/                    #   任务生成管线
│   ├── create.py              #     主流程
│   ├── orchestrator.py        #     管线编排
│   ├── claude_code_runner.py  #     CC 集成 + Reference Task Caching
│   ├── task_instruction.py    #     实质性评估 + 指令生成
│   ├── pr_fetcher.py          #     GitHub API + Issue 关联
│   └── task_reference.py      #     参考任务缓存
├── scoring.py                 #   难度评分
└── cli.py                     #   CLI 入口 + 批量处理
tools/
├── collect_prs_wo_image.py    #   PR 收集（两阶段筛选）
└── score_tasks.py             #   批量难度评分
scripts/                       #   各语言的 create 脚本（create_py.sh 等）
artifacts/
├── collected_prs/             #   PR ID 列表
└── swe_tasks/{lang}-cc/       #   生成的任务
outputs/                       #   合并后的已验证任务
```

---

## 6. 下游应用

构建的 SWE 任务数据通过 Harbor 平台搭配多种专家 Agent（Claude Code, OpenCode, OpenHands-SDK）和专家模型（GLM-5）推理获取专家轨迹，用于对学生模型进行 SFT，提升多语言 SWE 能力。
