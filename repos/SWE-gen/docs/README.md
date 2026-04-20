# Multilingual SWE-Bench Data Construction Pipeline

> **Repository**: [github.com/SWE-Lego/SWE-gen](https://github.com/SWE-Lego/SWE-gen)
> **Based on**: [SWE-gen](https://github.com/abundant-ai/SWE-gen) by Abundant AI
> **Version**: 2.1 — April 2026
> **Scope**: 8 languages — Python, JavaScript, TypeScript, Go, C, C++, Java, Rust

---

## TL;DR

本项目构建了一套面向多语言 SWE-Bench 的端到端自动化数据生产管线，从 GitHub PR 收集到难度评分全流程自动化。在公开版 [SWE-gen](https://github.com/abundant-ai/SWE-gen) 基础上新增 4 项核心能力：

| # | 核心贡献 | 效果 |
|---|---------|------|
| 1 | **Two-Stage PR Collection** — 仓库级+PR级两阶段筛选，8 语言差异化阈值 | 系统性收集 135,753 个候选 PR |
| 2 | **Batch Processing & Concurrency** — 批量输入 + 状态持久化 + 断点续跑 | 吞吐量 0.4→15 task/h (**37x**) |
| 3 | **Relaxed Substantiality Filter** — 接受单文件 bug 修复 | 候选任务 **+75%**（通过率 40%→70%） |
| 4 | **Difficulty Scoring** — 5 维度零 API 调用静态评分 | 全量 15,803 个任务统一评分 |

### Data Production Summary

| 语言 | 收集 PR | Selfmade Verified (Feb+March) | 目标 | 状态 |
|------|--------|-------------------------------|------|------|
| Python | 9,831 | 1,768 | 1,500 | 已达标 |
| JavaScript | 7,087 | 1,894 | 1,500 | 已达标 |
| TypeScript | 10,438 | 1,644 | 1,500 | 已达标 |
| Go | 15,455 | 2,397 | 1,500 | 已达标 |
| C | 13,047 | 1,005 | 500 | 已达标 |
| C++ | 19,668 | 800 | 1,000 | 进行中 |
| Java | 35,500 | 712 | 1,000 | 进行中 |
| Rust | 24,727 | 755 | 1,000 | 进行中 |
| **合计** | **135,753** | **10,975** | — | — |

### Dataset Locations

| 数据集 | 路径 | 任务数 | 语言 |
|--------|------|--------|------|
| **Selfmade (March)** | `tasks/March/{lang}-cc/` | 10,182 | 8 种 |
| **Selfmade (Feb)** | `tasks/Feb-verified/Feb-verified-combine/` | 793 | Py, JS, TS, Go |
| **SWE-gen Java** | `SWE-gen-old/harbor/datasets/swe-gen-java/` | 1,000 | Java |
| **SWE-gen Go** | `SWE-gen-old/harbor/datasets/swe-gen-go/` | 1,000 | Go |
| **SWE-gen C++** | `SWE-gen-old/harbor/datasets/swe-gen-cpp/` | 828 | C++ |
| **SWE-gen JS** | `SWE-gen-old/harbor/datasets/swe-gen-js/` | 1,000 | JS |
| **SWE-gen Rust** | `SWE-gen-old/harbor/datasets/swe-gen-rust/` | 1,000 | Rust |

Selfmade 10,975 + SWE-gen 4,828 = **15,803 个可用 SWE 任务**。

### Downstream Application

构建的 SWE 任务数据通过 Harbor 平台搭配多种专家 Agent（Claude Code, OpenCode, OpenHands-SDK）和专家模型（GLM-5）推理获取专家轨迹，用于对 Qwen3-8B 进行 SFT，提升小模型的多语言 SWE 能力。详见 [Section 8](#7-downstream-application-expert-trajectory-distillation)。

<!-- SECTION_1_PLACEHOLDER -->

---

## 1. Introduction

### 1.1 Background

SWE-Bench 是评估大语言模型代码修复能力的核心基准。原始 SWE-Bench 仅覆盖 Python，后续社区扩展了 JavaScript、Go 等语言，但现有公开数据集仍存在显著局限：

- **语言覆盖不足**：缺少 C、C++、Rust、Java 等系统级/企业级语言，无法评估模型在编译型语言上的能力
- **数据规模有限**：单语言通常不超过 1,000 个任务，统计显著性不足
- **质量参差不齐**：缺乏统一的难度评估体系，不同数据集间无法横向对比
- **构建效率低**：公开工具链（如 [SWE-gen](https://github.com/abundant-ai/SWE-gen)）缺少批量处理和自动化调度能力，单机吞吐量仅 ~0.4 task/h

### 1.2 Our Approach

本项目在公开版 SWE-gen 基础上，构建了一套覆盖 8 种编程语言的端到端自动化数据生产管线。核心思路是：

1. **系统性 PR 收集**：设计两阶段筛选体系（仓库级 + PR 级），针对不同语言生态差异化配置阈值，从 GitHub 系统性发现高质量候选 PR
2. **工业级批量处理**：实现状态持久化、断点续跑、24 并发的批量任务生成系统，将吞吐量提升 37 倍
3. **放宽质量门槛**：修改 LLM 实质性评估策略，接受单文件 bug 修复，增加 75% 候选任务而不降低最终质量（通过 NOP/Oracle 双重验证保障）
4. **统一难度评分**：设计 5 维度零 API 调用的静态评分系统，对全量 15,803 个任务进行统一评分

---

## 2. End-to-End Pipeline

### 2.1 Architecture

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   PR Collection   │────▶│  Task Generation  │────▶│   Validation     │────▶│  Scoring & Output │
│                  │     │                  │     │                  │     │                  │
│ • GitHub API     │     │ • LLM Eval       │     │ • Docker Build   │     │ • 5-dim Scoring  │
│ • Repo Filter    │     │ • Skeleton Gen   │     │ • NOP Agent      │     │ • task.toml      │
│ • PR Filter      │     │ • CC Completion  │     │ • Oracle Agent   │     │ • JSONL Export   │
│ • 8-lang Config  │     │ • Batch + Resume │     │ • Pass/Fail      │     │ • Easy/Med/Hard  │
└──────────────────┘     └──────────────────┘     └──────────────────┘     └──────────────────┘
      135,753 PRs              25,122 tasks            10,975 verified          15,803 scored
```

### 2.2 Stage Summary

| 阶段 | 输入 | 输出 | 关键技术 | 转化率 |
|------|------|------|---------|--------|
| PR Collection | GitHub repos | `{lang}_pr_ids.txt` | 两阶段筛选 + 语言差异化阈值 | ~5,000 repos → 135,753 PRs |
| Task Generation | PR ID 列表 | Task 目录 | LLM 评估 + CC 补全 + Batch Processing | 135,753 → 25,122 tasks |
| Validation | Task 目录 | `verifiable_tasks.txt` | NOP/Oracle 双重验证 | 25,122 → 10,975 verified |
| Scoring | Verified tasks | `task.toml` + JSONL | 5 维度静态评分 | 100% coverage |

### 2.3 Task Directory Structure

每个成功生成的 SWE 任务包含以下标准化文件：

```
{repo_owner}__{repo_name}-{pr_number}/
├── task.toml              # 元数据：难度评分、类别、标签、超时配置
├── instruction.md         # 自然语言任务描述（不含文件路径和实现提示）
├── environment/
│   ├── Dockerfile         # 完整的 Docker 构建环境定义
│   └── bug.patch          # 引入 bug 的 patch（base→head）
├── solution/
│   ├── fix.patch          # 修复 bug 的 patch（head→base）
│   └── solve.sh           # 应用 fix.patch 的脚本
└── tests/
    ├── test.sh            # 测试执行入口脚本
    └── [test files]       # 从仓库提取的原始测试文件
```

**设计原则**：`instruction.md` 仅描述问题现象（错误信息、期望行为 vs 实际行为），不暴露文件路径或修复方案，确保评估的公平性。

<!-- SECTION_3_PLACEHOLDER -->

---

## 3. Two-Stage PR Collection

公开版 SWE-gen 不包含 PR 收集模块——它假设用户已有候选 PR 列表。本管线实现了完整的两阶段收集管线（`tools/collect_prs_wo_image.py`），从 GitHub 系统性地发现和筛选高质量 PR。

### 3.1 Stage 1: Repository Filtering

从 GitHub Search API 按语言搜索仓库。不同语言的开源生态规模差异显著（Python/JS 仓库数量远超 C/Rust），因此我们为每种语言设计了差异化的筛选阈值：

| 过滤维度 | Py/JS/TS/Go (默认) | C/C++ | Rust | Java | 设计意图 |
|---------|-------------------|-------|------|------|---------|
| 最低 Star 数 | 200 | 50 | 50 | 80 | 确保社区认可度；小语种放宽 |
| 最低合并 PR 数 | 20 | 5 | 5 | 10 | 确保活跃 PR 流程 |
| 主语言占比 | ≥60% | ≥40% | ≥50% | ≥50% | C/C++ 项目含大量头文件，占比天然偏低 |
| 最近推送时间 | 3 年内 | 3 年内 | 3 年内 | 3 年内 | 排除废弃项目 |
| 依赖管理文件 | requirements.txt / package.json / go.mod | CMakeLists 等 | Cargo.toml | pom.xml / build.gradle | 确保项目可构建 |
| CI/CD 配置 | 必须存在 | 必须存在 | 必须存在 | 必须存在 | 确保有自动化测试基础设施 |
| 排除模式 | awesome-\*, tutorial\*, demo\*, dotfiles | 同左 | 同左 | 同左 | 排除非工程项目 |

**搜索策略**：GitHub Search API 单次查询最多返回 1,000 个结果。我们采用分段 Star 范围搜索（50-99, 100-199, 200-499, 500-999, 1000-4999, 5000+），将每个范围作为独立查询，绕过结果数限制，确保覆盖从小型到大型的各类项目。

### 3.2 Stage 2: PR Filtering

对每个候选仓库的已合并 PR 进行精细筛选。同样针对不同语言调整阈值：

| 过滤维度 | Py/JS/TS/Go (默认) | C/C++ | Rust/Java | 设计意图 |
|---------|-------------------|-------|-----------|---------|
| 修改文件数 | 1–20 | 1–30 | 1–25 | C/C++ 头文件多，允许更多文件 |
| 总改动行数 | <1,000 | <1,500 | <1,200 | 控制任务复杂度上限 |
| 测试文件修改 | 必须包含 | 必须包含 | 必须包含 | 确保有可验证的测试用例 |
| 代码文件修改 | 必须包含 | 必须包含 | 必须包含 | 确保有实际代码变更 |
| 关联 Issue | 恰好 1 个 | 恰好 1 个 | 恰好 1 个 | 确保有明确的问题描述来源 |
| Issue 描述长度 | ≥10 字符 | ≥10 字符 | ≥10 字符 | 排除空 Issue |
| PR 状态 | 已合并 | 已合并 | 已合并 | 确保变更被项目维护者接受 |

**排除规则**：
- 依赖更新：`bump`, `renovate`, `dependabot`, `greenkeeper`, `snyk`
- 非功能性变更：`docs:`, `chore:`, `style:`, `revert`, `merge`
- 版本发布：匹配 `v1.2.3` 模式的标题

**测试文件识别**覆盖 8 种语言的主流测试框架约定：

| 语言 | 测试文件模式 |
|------|------------|
| Python | `test_*.py`, `*_test.py`, `tests/*.py`, `conftest.py` |
| Java | `*Test.java`, `*Tests.java`, `*IT.java`, `src/test/*.java` |
| Go | `*_test.go` |
| Rust | `tests/*.rs`, `*_test.rs` |
| C | `test*.c`, `*_test.c`, `tests/*.c`, `*_check.c` |
| C++ | `test*.cpp`, `*_test.cpp`, `*Test.cpp`, `gtest*.cpp` |
| JS/TS | `*.test.{js,ts}`, `*.spec.{js,ts}`, `__tests__/*` |

### 3.3 Collection Results

| 语言 | 候选仓库 | 收集 PR 总数 | 过滤后 PR | 转化率 |
|------|---------|------------|----------|--------|
| Python | ~5,000 | 19,096 | 9,831 | 51.5% |
| JavaScript | ~5,000 | 13,915 | 7,087 | 50.9% |
| TypeScript | ~5,000 | 20,497 | 10,438 | 50.9% |
| Go | ~5,000 | 29,063 | 15,455 | 53.2% |
| C | ~5,000 | 13,047 | 10,730 | 82.2% |
| C++ | ~5,000 | 19,668 | 13,785 | 70.1% |
| Java | ~5,000 | 35,500 | 5,371 | 15.1% |
| Rust | ~5,000 | 24,727 | 7,238 | 29.3% |

> Java 和 Rust 额外经过 `sort_prs_by_quality.py` 按质量排序后取 top-N，因此过滤后数量较少但质量更高。

### 3.4 SWE Task Generation Success Rate

过滤后的 PR 经过 SWE-gen 管线处理后的验证通过率。Claude Code（CC）是任务生成的核心组件，负责补全 Dockerfile 和 test.sh：

| 语言 | 过滤后 PR | 已处理 | Verified Tasks | 成功率 | CC 模型 | Timeout (task/CC) |
|------|----------|--------|---------------|--------|---------|-------------------|
| Python | 9,831 | 9,421 | 1,652 | 17.5% | claude-sonnet-4-6 | 5400s / 3600s |
| JavaScript | 7,087 | 6,779 | 1,586 | 23.4% | claude-sonnet-4-6 | 5400s / 3600s |
| TypeScript | 10,438 | 9,827 | 1,572 | 16.0% | claude-sonnet-4-6 | 6000s / 4200s |
| Go | 15,455 | 14,061 | 2,100 | 14.9% | claude-sonnet-4-6 | 5400s / 3600s |
| C | 13,047 | 3,711 | 1,005 | 27.1% | claude-opus-4-6 | 5400s / 3600s |
| C++ | 19,668 | 3,472 | 800 | 23.0% | claude-sonnet-4-6 | 7200s / 5400s |
| Java | 35,500 | 2,531 | 712 | 28.1% | claude-sonnet-4-6 | 7200s / 7200s |
| Rust | 24,727 | 2,124 | 755 | 35.5% | claude-sonnet-4-6 | 9000s / 7200s |

> C++/Java/Rust 的超时时间显著高于 Py/JS/Go，因为编译型语言的 Docker 构建和测试执行耗时更长。LLM 实质性评估使用 gpt-5.2（C/C++/Java/Rust）或 claude-sonnet-4-6（Py/JS/TS/Go）。

### Developer Guide: PR Collection

```bash
# 收集 Java PR（默认参数）
python tools/collect_prs_wo_image.py --language java --output java_pr_ids.txt

# 收集 Rust PR（自定义仓库数量）
python tools/collect_prs_wo_image.py --language rust --repo-num 8000 --output rust_pr_ids.txt

# 按质量排序（可选，推荐用于 Java/Rust）
python tools/sort_prs_by_quality.py --input rust_pr_ids.txt --output rust_pr_ids_sorted.txt
```

<!-- SECTION_4_PLACEHOLDER -->

---

## 4. Task Generation Pipeline

### 4.1 Substantiality Evaluation (Relaxed Filter)

PR 收集阶段的静态筛选之后，任务生成阶段通过 LLM 进行语义级的实质性评估。这是本管线与公开版的关键差异之一。

**公开版策略（严格）**：

> "Changes to only a single file (not substantial enough)"
> "The PR MUST modify multiple files (at least 2-3 meaningful source code files)"

**本管线策略（放宽）**：

> "Single-file changes ARE substantial if they fix real bugs with non-trivial control flow. Only skip truly trivial changes (typos, formatting, version bumps)."

这一改变的动机是：在 C、Rust 等系统级语言中，大量真实 bug 修复集中在单个源文件（如修复空指针、竞态条件、内存泄漏），公开版的多文件要求会误杀这些高质量候选。

| 场景 | 公开版判断 | 本管线判断 | 理由 |
|------|----------|----------|------|
| 单文件修复空指针异常 | SKIP | KEEP | 真实 bug，非平凡控制流 |
| 单文件修复竞态条件 | SKIP | KEEP | 涉及并发逻辑 |
| 多文件格式化重构 | KEEP | SKIP | 纯装饰性，无功能变更 |
| 单文件拼写修复 | SKIP | SKIP | 平凡变更 |

**效果**：PR 通过率从 ~40% 提升至 ~70%（+75% 候选任务），且不降低最终质量——所有候选仍需通过 NOP/Oracle 双重验证。

### 4.2 Task Instruction Generation

任务描述（`instruction.md`）的生成遵循严格规范，确保评估公平性：

- **必须包含**：具体的函数/类/方法名、用户可见的错误信息、期望行为 vs 实际行为
- **必须排除**：文件路径、测试文件引用、实现方案提示
- **信息来源优先级**：关联 Issue 描述 > PR 标题/正文 > 测试文件内容 > LLM 生成

### 4.3 Claude Code Integration & Reference Task Caching

任务骨架生成后，由 Claude Code（CC）补全 Dockerfile 和 test.sh。CC 分析仓库结构，自动检测语言、运行时、构建系统和测试框架，生成完整的 Docker 环境定义。

**Reference Task Caching**（沿用自公开版 SWE-gen）：当同一仓库已有成功生成的任务时，系统从 `.swegen/task_references.json` 中加载缓存的参考任务。CC 接收简化的提示，仅需参照参考任务的 Dockerfile/test.sh 模板调整测试路径，而非从零开始分析仓库。参考任务有效期为 180 天。

**效果**：CC 会话时间从平均 ~30 分钟降至 ~15 分钟，成功率从 ~40% 提升至 ~65%（同仓库后续 PR）。

**语言差异化处理**：CC 在处理不同语言时的主要区别：

| 语言 | 运行时安装 | 构建系统 | 测试命令 | 特殊处理 |
|------|----------|---------|---------|---------|
| Python | `apt-get install python3` | pip/poetry/conda | `pytest` / `python -m unittest` | virtualenv 隔离 |
| JS/TS | `apt-get install nodejs npm` | npm/yarn/pnpm | `jest` / `mocha` / `vitest` | node_modules 缓存 |
| Go | `apt-get install golang` | go mod | `go test` | GOPATH 配置 |
| C | `apt-get install gcc cmake` | make/cmake | 自定义 test binary | **需手动指定编译 flags** |
| C++ | `apt-get install g++ cmake` | make/cmake/bazel | gtest/catch2 | **构建时间长，超时需 5400s+** |
| Java | `apt-get install openjdk maven` | maven/gradle | `mvn test` / `gradle test` | **JVM 启动慢，超时需 7200s** |
| Rust | `curl rustup.rs` | cargo | `cargo test` | **编译时间长，超时需 7200s** |

> **与公开版的区别**：公开版采用语言无关（language-agnostic）的方式，由 CC 自动检测一切。本管线在此基础上做了以下差异化改进：
>
> 1. **超时差异化**：针对 C/C++/Java/Rust 预配置了更长的超时时间（7200-12600s vs 公开版默认 3600s），因为编译型语言的 Docker 构建和测试执行耗时显著更长
> 2. **文件数差异化**：在 create 脚本中为不同语言设置差异化的 `--max-source-files`（Java=10, Rust=15, C++=15 vs 公开版统一默认值）
> 3. **第三方模型支持**：本地新增 `_resolve_sdk_model()` 函数，支持非 Claude 模型（如 GLM-5, MiniMax-M2.7）通过 `ANTHROPIC_BASE_URL` + `ANTHROPIC_MODEL` 环境变量接入 Claude Code SDK，公开版硬编码 `model="sonnet"`
> 4. **JSON 容错**：本地新增 `_extract_and_fix_json()` 处理非 Claude 模型返回的格式问题（trailing commas, 单引号, Python 风格 True/None 等），公开版假设模型总是返回标准 JSON
> 5. **任务骨架模板**：两版共享相同的语言无关骨架模板（`task_skeleton.py`），CC 根据仓库内容自动填充——本管线未修改此部分

### 4.4 NOP/Oracle Validation

每个任务必须通过双重验证才被标记为 verified：

| 验证类型 | 操作 | 期望结果 | 目的 |
|---------|------|---------|------|
| **NOP Agent** | 不做任何修改，直接运行测试 | 测试**失败** | 确认 bug 确实存在且可复现 |
| **Oracle Agent** | 应用 fix.patch，运行测试 | 测试**通过** | 确认修复方案有效且完整 |

从 25,122 个生成的任务中，10,975 个通过双重验证（43.7% 验证通过率）。主要失败原因：Docker 构建失败 (~20%)、CC 超时 (~15%)、NOP 验证失败（bug 不可复现, ~10%）、Oracle 验证失败（patch 不完整, ~5%）。

### 4.5 Batch Processing & Concurrency System

这是本管线最重要的工程贡献之一。公开版 SWE-gen 仅支持基础并发（推荐 3-5 个 worker），无状态持久化和断点续跑能力。本管线实现了完整的工业级批量处理系统。

**File-Based Batch Input**：支持从文件批量读取 PR ID，每个输入文件对应一个 `.swegen-create-batch/{hash}.json` 状态文件。

**State Persistence**：状态文件记录每个 PR 的处理状态（success/failed/filtered/pending）、尝试次数、模型指纹、累计耗时：

```json
{
  "cases": {
    "apache/kafka#21001": {
      "status": "success", "task_id": "apache__kafka-21001",
      "attempts": 1, "last_model_fingerprint": "claude-sonnet-4-..."
    },
    "spring-projects/spring-boot#42000": {
      "status": "failed", "failure_kind": "timeout", "attempts": 2
    }
  },
  "total_elapsed_seconds": 86400.0
}
```

**Resume Logic**：重启时自动跳过已完成的 PR；对基础设施失败（网络、Docker、Git、API 限流、timeout）的 PR 会自动重试；对非基础设施失败是否重跑，取决于 `run_fingerprint` 是否变化。

**FAQ: 重新运行相同参数的 `swegen create` 会自动处理什么？**

- `status=success`：直接跳过，不会重跑。
- 批量模式下，如果某个 task 目录已经有完整 CC 输出，也就是 `environment/Dockerfile`、`tests/test.sh`、`solution/fix.patch` 都存在，且 `Dockerfile` / `test.sh` 里不再含 `TODO`，会走 fast-path recovery：只补跑 Harbor `nop` + `oracle` 验证，不再重跑 CC。
- 只有原始 skeleton 时不会自动续跑。也就是说，如果目录里还是带 `TODO` 的 `Dockerfile` / `test.sh`，这不算“可恢复的未验证任务”；重新运行同参数不会从这个 skeleton 继续做完。批量模式里它通常会被当成未成功 case，但现有 task 目录仍可能导致后续 `FileExistsError`。这类情况要么手动清理旧 task 目录后重跑，要么用 `--force` 全量重建。若文件其实已经完整、只是没进 `verifiable_tasks.txt`，更适合直接用 `tools/revalidate_tasks.py`。
- `failed` 且 `failure_kind=infra`：会自动重新处理。包括 timeout、锁冲突、GitHub 403/rate limit、网络/API/Docker/Git 等基础设施错误；普通 infra 错误默认最多重试 3 次，timeout 在“同仓库已有成功样本”时会比普通 infra 多给几次机会。
- `failed` 但这次运行的 `run_fingerprint` 变了：会自动重新处理所有非 `success` case。这里的 fingerprint 包含模型/API base URL，以及 `validate`、`require_issue`、`require_minimum_difficulty`、`min_source_files`、`max_source_files`、`allow_unmerged` 等参数。
- 历史上因为 `missing_issue` 失败的 case，在改用 `--no-require-issue` 后会被自动 reopen，再次进入处理队列。
- `trivial_pr` 不会因为相同参数重跑而自动重新处理；`validation` / `workflow_error` / `file_exists` 这类非 infra 失败，在相同 fingerprint 下也不会自动重跑。

**边界说明**：上面的 fast-path recovery 只存在于批量模式（`--input-ids-file`）。单 PR 模式重新运行时，如果旧 task 目录还在，默认会直接报 `FileExistsError`，除非显式传 `--force`。

| 指标 | 公开版 SWE-gen | 本管线 |
|------|--------------|--------|
| 最大并发 | 3-5 (推荐) | **24** |
| 状态持久化 | 无 | 完整 JSON 状态文件 |
| 断点续跑 | 无 | 自动跳过 + 智能重试 |
| 输入去重 | 无 | 自动检测重复 PR ID |
| 吞吐量 | ~0.4 task/h | **~15 task/h** |
| **提升倍数** | — | **37x** |

### Developer Guide: Task Generation

```bash
# 安装
pip install -e .

# 单语言批量生成（推荐）
swegen create \
  --input-ids-file collected_prs/java_pr_ids.txt \
  --n-concurrent 24 \
  --output tasks/March/java-cc \
  --timeout 7200 \
  --cc-timeout 7200 \
  --no-require-issue \
  --min-source-files 1 \
  --max-source-files 10

# 使用运维脚本（已配置好参数）
bash scripts/create_java.sh    # Java, N=24
bash scripts/create_cpp.sh     # C++, N=20
bash scripts/create_rust.sh    # Rust, N=20
```

<!-- SECTION_5_PLACEHOLDER -->

---

## 5. Difficulty Scoring System

公开版 SWE-gen 不包含难度评分功能。本管线新增了 5 维度零 API 调用的静态评分系统（`src/swegen/scoring.py`），可在毫秒级完成单个任务的评分。

### 5.1 Scoring Methodology

| 维度 | 权重 | 评分依据 | 分值 |
|------|------|---------|------|
| **Patch Scope** | 30% | 修改文件数 + 改动行数 | 1–5 |
| **Logic Complexity** | 25% | 新增函数/类/方法数 + 净增代码行数 | 1–5 |
| **Test Complexity** | 20% | 测试文件数 + 测试代码行数 | 1–5 |
| **Context Breadth** | 15% | 变更涉及的目录数（跨模块程度） | 1–5 |
| **Instruction Complexity** | 10% | 任务描述字符数（问题复杂度代理指标） | 1–5 |

加权和（1.0–5.0）线性映射至 1–10 分。难度标签：**Easy** (1–3), **Medium** (4–7), **Hard** (8–10)。

评分系统支持 8 种语言的代码模式识别，覆盖各语言的函数/类/方法定义语法：`def`(Python), `func`(Go), `fn`/`pub fn`(Rust), `public static`(Java), `function`/`const =>`(JS/TS), `class`, `struct`, `impl`, `interface` 等。

### 5.2 Auto-Scoring Integration

难度评分已集成到管线的所有任务产出路径，任务生成成功后即时评分：

| 代码路径 | 触发时机 |
|---------|---------|
| `cli.py` 批量并发模式 | Worker 返回成功结果后 |
| `cli.py` 顺序批量模式 | `run_reversal()` 成功返回后 |
| `cli.py` 快速恢复路径 | 已有任务通过重新验证后 |
| `revalidate_tasks.py` | NOP/Oracle 验证通过后 |

评分失败不影响任务的成功状态（non-fatal），确保管线稳定性。

### Developer Guide: Difficulty Scoring

```bash
# 评分所有 March 任务并更新 task.toml
python tools/score_tasks.py --march-all --update-toml

# 评分外部数据集
python tools/score_tasks.py --dir /path/to/dataset --update-toml

# 评分单个任务
python tools/score_tasks.py --task tasks/March/java-cc/apache__kafka-21001
```

---

## 6. Dataset Analysis: Selfmade vs SWE-gen Series

### 6.1 Overall Difficulty Distribution

使用统一的 5 维度评分系统对两个系列进行对比（Selfmade 含 Feb 793 + March verified 9,137 = 9,930 个已评分任务）：

| 指标 | SWE-gen 系列 (4,828) | Selfmade 系列 (9,930) | 差异分析 |
|------|---------------------|----------------------|---------|
| 平均难度 | **7.30** | **6.52** | Selfmade 偏中等难度 |
| Easy (1–3) | 3 (0.1%) | 96 (1.0%) | — |
| Medium (4–7) | 1,981 (41.0%) | 5,753 (57.9%) | Selfmade 更均衡 |
| Hard (8–10) | 2,844 (58.9%) | 4,081 (41.1%) | SWE-gen 偏难 |

SWE-gen 系列整体偏难（59% hard），主要原因是公开版对 PR 的文件数和行数限制较宽松，导致更多大范围改动的 PR 被纳入。Selfmade 系列通过更严格的筛选产出了更均衡的难度分布，更适合作为评估基准——过多的 hard 任务会导致大多数模型得分接近零，降低基准的区分能力。

### 6.2 Per-Language Comparison

**SWE-gen 系列**：

| 语言 | 任务数 | 平均分 | Easy | Medium | Hard | Avg Patch Files | Avg Patch Lines |
|------|-------|--------|------|--------|------|----------------|----------------|
| Java | 1,000 | 7.53 | 0.1% | 36.8% | 63.1% | 15.4 | 364.5 |
| Go | 1,000 | 6.89 | 0.0% | 51.7% | 48.3% | 4.1 | 134.5 |
| C++ | 828 | 7.03 | 0.1% | 48.2% | 51.7% | 4.2 | 231.5 |
| JS | 1,000 | 7.59 | 0.0% | 33.1% | 66.9% | 13.0 | 604.9 |
| Rust | 1,000 | 7.43 | 0.1% | 36.6% | 63.3% | 4.7 | 236.4 |

**Selfmade 系列**（Feb + March Verified）：

| 语言 | 任务数 | 平均分 | Easy | Medium | Hard | Avg Patch Files | Avg Patch Lines |
|------|-------|--------|------|--------|------|----------------|----------------|
| Python | 1,768 | 6.76 | 0.3% | 56.8% | 42.9% | 3.6 | 89.4 |
| JavaScript | 1,894 | 6.33 | 0.6% | 66.5% | 32.9% | 3.5 | 75.2 |
| TypeScript | 1,644 | 6.52 | 0.8% | 60.9% | 38.2% | 3.6 | 79.5 |
| Go | 2,397 | 6.86 | 0.5% | 49.3% | 50.2% | 4.0 | 108.8 |
| C | 1,005 | 6.24 | 1.1% | 62.3% | 36.6% | 4.2 | 162.0 |
| C++ | 800 | 6.27 | 1.9% | 57.0% | 41.1% | 3.5 | 133.1 |
| Java | 712 | 6.34 | 2.6% | 54.7% | 42.7% | 3.3 | 89.0 |
| Rust | 755 | 6.59 | 0.9% | 54.5% | 44.6% | 3.7 | 436.8 |

### 6.3 Quality Dimension Comparison

| 维度 | SWE-gen 系列 | Selfmade 系列 | 解读 |
|------|-------------|-------------|------|
| Patch Scope | 4.27 | 3.78 | SWE-gen 改动范围更大 |
| Logic Complexity | 3.42 | 3.00 | SWE-gen 逻辑复杂度略高 |
| Test Complexity | 3.98 | 3.69 | 测试复杂度相近 |
| Context Breadth | 2.44 | 2.13 | SWE-gen 跨目录更多 |
| Instruction Complexity | 5.00 | 5.00 | 两者任务描述均充分（>800 字符） |

**关键发现**：Selfmade 系列的 Patch Scope 更小（平均 3.3–4.2 文件 vs SWE-gen 的 4.1–15.4 文件），产出了更聚焦的 bug 修复任务。两个系列的 Instruction Complexity 均为满分，说明任务描述质量一致。

### 6.4 fix.patch Complexity Analysis

对两个系列的 `solution/fix.patch` 进行详细分析，统计修改的文件数、代码块数（hunk）、改动行数，以及去除测试文件后的纯代码改动。

**Selfmade 系列**（10,831 个 verified tasks，Feb + March）：

| 语言 | 任务数 | Avg Files | Avg Chunks | Avg Lines | NT Files | NT Chunks | NT Lines |
|------|-------|-----------|-----------|-----------|----------|-----------|----------|
| Python | 1,533 | 4.7 | 12.6 | 189.1 | 4.6 | 12.6 | 188.1 |
| JavaScript | 1,701 | 3.5 | 7.9 | 78.1 | 3.5 | 7.9 | 77.2 |
| TypeScript | 1,571 | 3.6 | 7.7 | 79.3 | 3.6 | 7.6 | 77.1 |
| Go | 2,368 | 4.0 | 9.1 | 108.6 | 4.0 | 9.0 | 106.7 |
| C | 986 | 4.2 | 10.4 | 162.0 | 4.0 | 10.1 | 152.6 |
| C++ | 932 | 3.4 | 8.7 | 125.6 | 3.2 | 8.2 | 117.9 |
| Java | 839 | 3.1 | 6.8 | 83.9 | 3.1 | 6.7 | 83.4 |
| Rust | 901 | 3.9 | 11.3 | 357.8 | 3.8 | 11.1 | 353.7 |
| **Overall** | **10,831** | **3.9** | **9.3** | **136.1** | **3.8** | **9.2** | **133.2** |

**SWE-gen 系列**（4,827 个 tasks）：

| 语言 | 任务数 | Avg Files | Avg Chunks | Avg Lines | NT Files | NT Chunks | NT Lines |
|------|-------|-----------|-----------|-----------|----------|-----------|----------|
| JavaScript | 1,000 | 13.0 | 29.3 | 604.9 | 12.9 | 29.2 | 602.6 |
| Go | 1,000 | 4.1 | 9.0 | 134.5 | 4.0 | 8.8 | 132.4 |
| C++ | 827 | 4.2 | 12.9 | 231.7 | 4.2 | 12.8 | 226.2 |
| Java | 1,000 | 15.4 | 29.5 | 364.5 | 14.2 | 26.4 | 307.0 |
| Rust | 1,000 | 4.7 | 14.6 | 236.4 | 4.6 | 14.3 | 231.2 |
| **Overall** | **4,827** | **8.4** | **19.3** | **317.4** | **8.1** | **18.5** | **302.5** |

> NT = Non-Trivial（去除测试文件后的纯代码改动）。

**对比分析**：
- **Selfmade 系列更聚焦**：平均 3.9 文件 / 9.3 chunks / 136 行，vs SWE-gen 的 8.4 文件 / 19.3 chunks / 317 行。Selfmade 的 patch 规模约为 SWE-gen 的 **43%**，更适合评估精准 bug 修复能力。
- **测试文件占比极低**：两个系列去除测试文件后的指标几乎不变（Selfmade: 136→133 行，SWE-gen: 317→303 行），说明 fix.patch 主要是代码修改而非测试修改。
- **SWE-gen JS/Java 异常大**：JS 平均 13 文件 / 605 行，Java 平均 15.4 文件 / 365 行，远超其他语言。这些大 patch 可能包含重构类 PR，降低了作为 bug 修复基准的精准度。
- **Rust patch 行数最多**（Selfmade 358 行）：Rust 的类型系统和所有权模型要求修改更多代码来修复 bug，这是语言特性决定的。

### 6.4 Yield Estimation: min-source-files Sensitivity & Scaling

当前管线使用 `--min-source-files 1`（接受单文件修复）。以下分析不同阈值对产出量的影响，以及扩大 PR 收集后的潜在规模。

**已验证任务中的 patch 文件数分布**：

| 语言 | 1 file | 2 files | 3 files | 4+ files | min=2 保留率 | min=3 保留率 |
|------|--------|---------|---------|----------|-------------|-------------|
| Python | 0% | 28% | 29% | 43% | 100% | 72% |
| JS | 4% | 39% | 19% | 38% | 96% | 58% |
| TS | 2% | 37% | 21% | 40% | 98% | 61% |
| Go | 1% | 25% | 22% | 51% | 99% | 74% |
| C | 21% | 17% | 14% | 47% | 79% | 61% |
| C++ | **30%** | 18% | 12% | 40% | 70% | 52% |
| Java | **31%** | 19% | 15% | 35% | 69% | 50% |
| Rust | **25%** | 21% | 13% | 41% | 75% | 54% |

C++/Java/Rust 有 25–31% 的有效任务是单文件修复——这正是 Relaxed Substantiality Filter 的核心价值所在。

**产出量估算**（基于当前观测的成功率）：

| 语言 | 当前 Verified | 成功率 | 处理完当前 PR 池 (min=1) | min=2 | min=3 | 扩大收集后 (min=1, ~2.5x) |
|------|-------------|--------|------------------------|-------|-------|--------------------------|
| Python | 1,768 | 16.4% | ~1,900 | ~1,900 | ~1,400 | ~4,700 |
| JS | 1,894 | 21.2% | ~2,000 | ~1,900 | ~1,100 | ~4,900 |
| TS | 1,644 | 15.6% | ~1,800 | ~1,800 | ~1,100 | ~4,600 |
| Go | 2,397 | 14.3% | ~2,800 | ~2,700 | ~2,000 | ~6,900 |
| C | 1,005 | 19.8% | ~6,700 | ~5,300 | ~4,100 | ~16,700 |
| C++ | 800 | 5.2% | ~2,200 | ~1,500 | ~1,100 | ~5,500 |
| Java | 712 | 9.0% | ~4,000 | ~2,700 | ~2,000 | ~9,900 |
| Rust | 755 | 7.3% | ~2,800 | ~2,100 | ~1,500 | ~6,900 |
| **合计** | **10,975** | — | **~24,200** | **~19,900** | **~14,300** | **~60,100** |

> - "处理完当前 PR 池"：将已收集的 135,753 个 PR 全部处理完的预估产出
> - "扩大收集后"：假设 GitHub 上还有 ~2.5x 的符合条件 PR 未被收集（保守估计）
> - C 语言成功率高（19.8%）且 PR 池大（13k），潜力最大
> - C++/Java/Rust 成功率较低（5-9%），主要瓶颈是 Docker 构建复杂度和 CC 超时

<!-- SECTION_7_PLACEHOLDER -->

---

## 7. Performance Profile & Throughput Optimization

### 7.1 Pipeline Time Breakdown

对管线各阶段进行 profiling，识别瓶颈：

| 阶段 | 平均耗时 | 占比 | 瓶颈分析 |
|------|---------|------|---------|
| Phase 1: PR fetch + git clone | 10–30s | <1% | 网络 I/O，非瓶颈 |
| Phase 2: LLM eval + skeleton | 25–53s | <1% | 单次 API 调用，非瓶颈 |
| **Phase 3: CC session** | **1,820–7,723s** | **98%+** | **核心瓶颈** |

CC session 内部包含：仓库分析 → Dockerfile 填充 → Docker build（依赖安装/编译）→ NOP 验证 → Oracle 验证。其中 Docker build 是最耗时的子阶段，尤其是编译型语言（C++/Java/Rust）的依赖下载和编译。

### 7.2 Large-Scale Empirical Timing Data (22K CC sessions, 16K harbor runs)

基于 22,031 次 CC session 和 16,755 次 harbor run 的实测数据（截至 2026-04-15）：

**CC Session 总耗时分布**

| 类别 | 样本数 | P25 | 中位数 | 平均 | P75 | 最大 |
|------|--------|-----|--------|------|-----|------|
| 超时 | 13,269 | 3,601s | 3,603s | 4,242s | 4,202s | 9,006s |
| 非超时 | 8,762 | 80s | 1,611s | 71,145s | 40,143s | — |
| **全部** | **22,031** | 2,705s | **3,602s** | 30,850s | 4,328s | — |

**总体 CC timeout rate: 60.2%**（13,269/22,031）

**Harbor Run（Docker build + test）耗时分布**

| 阶段 | 样本数 | 中位数 | 平均 | P75 | 最大 |
|------|--------|--------|------|-----|------|
| NOP (Docker build + test) | 8,679 | **288s** | 566s | 984s | 30,532s |
| Oracle (Docker build + test) | 8,076 | **78s** | 370s | 412s | 15,784s |
| **全部 harbor run** | **16,755** | **171s** | 472s | 680s | 30,532s |

Harbor run 时间分布：

| 时段 | 数量 | 占比 |
|------|------|------|
| <1min | 5,219 | 31.1% |
| 1–5min | 4,931 | 29.4% |
| 5–10min | 2,114 | 12.6% |
| 10–20min | 1,489 | 8.9% |
| 20–30min | 2,590 | 15.5% |
| 30min–2h+ | 412 | 2.4% |

**Harbor runs per task（CC 迭代深度）**

| harbor runs 数 | 任务数 | 占比 | 含义 |
|---------------|--------|------|------|
| 2 (NOP+Oracle 一次通过) | 5,788 | 72% | CC 一次做对 |
| 3–4 (一次 retry) | 899 | 11% | CC 需要修一次 |
| 5+ | 203 | 3% | 多次 retry |
| 1 (只跑了 NOP 就失败) | 877 | 11% | Docker build 失败 |

**关键发现**：72% 的任务 CC 一次做对（2 runs = 1 NOP + 1 Oracle）。Docker build 是每次 run 的主要耗时（NOP 中位 288s，Oracle 中位 78s — Oracle 更快因为复用 NOP 的 image）。

### 7.3 CC Session Time by Language (Historical)

| 语言 | Avg CC Time | Timeout Rate | 成功 CC 占比 | 主要失败原因 |
|------|------------|-------------|-------------|-------------|
| Java | 1,820s (30min) | 0% | 67% | Docker build 失败 |
| C | 3,147s (52min) | 78% | 53% | CC 超时 |
| JS | 3,256s (54min) | 81% | 45% | CC 超时 |
| Py | 3,551s (59min) | 96% | 55% | CC 超时 |
| C++ | 5,901s (98min) | 73% | 15% | Docker build + CC 超时 |
| Rust | 7,723s (129min) | 78% | 30% | 编译时间长 + CC 超时 |

**核心发现**：CC timeout rate 高达 60–96%。每产出 1 个成功任务，平均浪费 3–25 个 CC session 的时间在超时失败上。

### 7.4 Bottleneck Deep Dive: CC Session 内部时间拆解

一个 CC session 内部有两个并列的时间消耗：

```
CC session = CC model 交互 + harbor run（Docker build + 测试执行）× N
                                         └── N 通常 = 2（72%），偶尔 3-6
```

**典型成功任务的时间分解（实测估算）：**

| 子阶段 | 解释型语言 (Py/JS/TS/Go) | 编译型语言 (Rust/C++/Java) |
|--------|--------------------------|---------------------------|
| CC model 分析 repo + 编辑文件 | 2–5min (20–30%) | 2–5min (5–15%) |
| Docker build — NOP（从 scratch） | 2–5min (30–40%) | **10–40min (50–70%)** |
| Docker build — Oracle（可复用 image） | 1–2min (10–15%) | 2–10min (10–20%) |
| 测试执行（NOP + Oracle） | 0.5–1min (<5%) | 0.5–2min (<5%) |
| CC model 修复+重试（如果需要） | 2–5min per retry | 2–5min per retry |

**CC model 交互 vs Docker build 的时间占比：**

| 语言类型 | CC model 交互 | Docker build | 测试执行 |
|----------|-------------|-------------|---------|
| 解释型 | ~40% | ~50% | ~10% |
| 编译型 | ~15% | **~80%** | ~5% |

### 7.5 换高速 Model API 能提速多少？

**结论：提速有限，不是主要瓶颈。**

当前 CC session 通过 Claude Agent SDK 调用 claude-sonnet-4-6（或第三方 API），model 交互在整个 CC session 中的时间占比：

- 解释型语言：~40%（如果 API 速度翻倍，总时间减少 ~20%）
- 编译型语言：~15%（如果 API 速度翻倍，总时间仅减少 ~7%）

更重要的是，**60% 的 CC session 最终超时失败**。对于这些 session，更快的 API 只是让它更快地到达 timeout，并不能让它成功。真正的瓶颈是：

1. **Docker build 时间**（编译型语言的 cargo build / mvn compile 需要 10–40min）
2. **CC timeout 导致的时间浪费**（超时的 session 占用 worker 直到 timeout）

**量化估算：更快 API 的收益 vs 其他优化的收益**

| 优化手段 | 预期吞吐提升 | 需要改代码？ |
|---------|-------------|------------|
| 换 2x 快 API | 7–20% | 否（改环境变量） |
| cc-timeout 砍半 (如 10800→5400) | 30–50% | 否（改脚本参数） |
| 清理僵尸 Docker 容器 | 5–10% | 否（手动清理） |
| Docker layer caching / repo-level base image | 40–60% | 是（中等工作量） |
| Per-harbor-run timeout（单次 build >20min 即 abort） | 20–30% | 是（小改动） |
| 更激进的 early abort（编译失败 1 次即放弃） | 15–25% | 是（小改动） |

### 7.6 更需要高性能 CPU/Docker 还是高速 Model API？

**当前机器资源完全够用，不是瓶颈（2026-04-15 实测）：**

| 资源 | 总量 | 当前使用 | 利用率 |
|------|------|---------|--------|
| CPU | 96 核 | ~36 (load avg ~35) | 37% |
| RAM | 2 TB | 77 GB | 3.7% |
| Disk | 1.8 TB | 29 GB | 1.6% |
| Docker images | 16.56 TB | — | 12% 可回收 |
| Docker build cache | 3.95 TB | — | 84% 可回收 |

128 并发（8语言 × 16）时的预估：CPU ~120 (125%), RAM ~250 GB (12%), Disk I/O 可能成为瓶颈（多个大型 Rust/C++ 项目同时编译）。

**结论**：

| 维度 | 影响程度 | 说明 |
|------|---------|------|
| 高速 Model API | ⭐⭐ 低 | CC model 交互只占 15–40%，且 60% session 超时失败 |
| 高性能 CPU | ⭐⭐⭐ 中 | 主要影响编译型语言的 Docker build（cargo/mvn/g++）|
| Docker 优化 | ⭐⭐⭐⭐⭐ 高 | layer caching、base image 复用可减少 50%+ Docker build 时间 |
| Timeout 策略优化 | ⭐⭐⭐⭐⭐ 高 | 60% timeout rate 是最大浪费源，缩短 timeout 立即见效 |

### 7.7 Zombie Container 问题

实测发现 Docker 容器经常超出 CC session 的 timeout 继续运行。截至 2026-04-15：

```
0xmiden__miden-vm-2218    运行 12 小时（Rust 项目，cargo build 卡死）
0xpolygonzero__plonky2-1418  运行 9 小时
acalanetwork__acala-2262     运行 6 小时
```

这些僵尸容器占用 CPU/内存/磁盘 I/O，且不会产出任何结果。原因：CC session 被 timeout kill 后，其启动的 Docker 容器没有被级联终止。

### 7.8 Optimization Measures (Implemented)

基于 profiling 结果，实施了以下 4 项优化：

**Opt 1: Static Pre-Filter (已内置)**

在调用 LLM 评估之前，`check_multi_file_requirement()` 先用 GitHub API 返回的文件列表检查源文件数 ≥ `min-source-files`。不满足条件的 PR 在 ~5s 内被跳过，避免浪费 25–53s 的 LLM API 调用。

**Opt 2: Reduced CC Timeout for Interpreted Languages**

解释型语言（Py/JS/TS/Go）的 Docker build 不涉及编译，如果 CC 在 40 分钟内未成功，继续等待的边际收益极低。

| 语言 | 旧 CC Timeout | 新 CC Timeout | 旧 Task Timeout | 新 Task Timeout |
|------|-------------|-------------|----------------|----------------|
| Py | 3,600s | **2,400s** | 5,400s | **3,600s** |
| JS | 3,600s | **2,400s** | 5,400s | **3,600s** |
| TS | 4,200s | **2,700s** | 6,000s | **4,000s** |
| Go | 3,600s | **2,400s** | 5,400s | **3,600s** |

预期效果：worker 更快回收，单位时间内尝试更多 PR，吞吐量提升 ~30–40%。

**Opt 3: Early Docker Build Failure Detection**

在 CC session 的流式输出中监控 docker build 失败模式。当检测到 ≥3 次 docker build 失败（`docker.*error`, `docker.*failed`, `exited with code`）时，立即中止 CC session，而非等待完整 timeout。

```python
# claude_code_runner.py - early abort logic
if "docker" in text_lower and ("error" in text_lower or "failed" in text_lower):
    docker_build_failures += 1
    if docker_build_failures >= 3:
        logger.warning("Early abort: %d docker build failures", docker_build_failures)
        break
```

预期效果：对于 Docker build 注定失败的任务（依赖不可用、编译错误），从平均 5,000s 超时缩短到 ~500–1,000s 即可检测并中止。

**Opt 4: Increased Concurrency for Fast Languages**

Go/TS 的 CC session 时间较短但之前并发度偏低。提高并发以匹配 API 额度：

| 语言 | 旧并发 | 新并发 |
|------|--------|--------|
| TS | 16 | **24** |
| Go | 16 | **24** |

总并发从 156 提升到 **172**。

**Opt 5: Reduced CC Timeout for Compiled Languages (2026-04-14)**

编译型语言的旧 timeout 过于宽松（Java 10,800s / Rust 12,600s），导致失败 case 占用 worker 长达 2.5–3 小时。基于实测数据（成功 CC 中位 ~1,600s），将 timeout 大幅缩减：

| 语言 | 旧 CC Timeout | 新 CC Timeout | 旧 Task Timeout | 新 Task Timeout |
|------|-------------|-------------|----------------|----------------|
| Java | 10,800s (3h) | **5,400s (1.5h)** | 10,800s | **7,200s** |
| Rust | 9,000s (2.5h) | **5,400s (1.5h)** | 12,600s | **7,200s** |

实测效果：Rust 从重启到 16 小时内产出 +128 个新任务（~8/h），较旧配置提速显著。

### 7.9 Proposed Optimizations (Not Yet Implemented)

以下优化按预期收益排序，尚未实施：

**Prop 1: Per-Harbor-Run Timeout (预期 +30–40% 吞吐)**

当前 CC session 只有一个总 timeout，但单次 `harbor run` 没有独立超时。一个卡死的 Docker build（如 Rust 大型项目编译 >1h）会静默消耗整个 CC session timeout。建议：

- 解释型语言：单次 harbor run timeout = 600s (10min)
- 编译型语言：单次 harbor run timeout = 1,800s (30min)

实现方式：在 CC_PROMPT 中加入 `timeout` 命令包裹 `harbor run`，或在 harbor runner 中添加 `--timeout` 参数。

**Prop 2: Repo-Level Docker Base Image Caching (预期 +40–60% 对编译型语言)**

同一仓库的不同 PR 共享相同的 base 环境（OS + 语言 runtime + 依赖）。当前每个 CC session 都从 `ubuntu:24.04` 开始完整构建。如果缓存到 `cargo fetch` / `mvn dependency:resolve` 步骤的 image，后续同仓库 PR 只需 apply patch + 增量编译。

Reference Task Caching 已部分解决此问题（CC prompt 复用），但 Docker image 仍然每次从头构建。

**Prop 3: CC Session 后自动清理 Docker 容器 (预期 +5–10%)**

CC session timeout 后，其启动的 Docker 容器仍在运行（见 7.7 Zombie Container 问题）。建议在 `_run_claude_code_session_async` 的 `except TimeoutError` 分支中添加 `docker rm -f` 清理逻辑。

**Prop 4: 更激进的 Early Abort (预期 +15–25%)**

当前 early abort 需要 ≥3 次 docker 错误消息。对于编译型语言的确定性编译错误（如缺少依赖、ABI 不兼容），单次编译失败即可判定该 PR 不可行。建议将编译错误的 abort 阈值降为 1 次。

---

## 8. Downstream Application: Expert Trajectory Distillation

构建 SWE 任务数据集的最终目标是通过专家轨迹蒸馏提升小模型的多语言代码修复能力。

### 8.1 Methodology

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐     ┌──────────────┐
│  SWE Tasks   │────▶│  Harbor Platform  │────▶│   Expert     │────▶│ SFT Training │
│  (15,803)    │     │  + Agent          │     │ Trajectories │     │  Qwen3-8B    │
└──────────────┘     └──────────────────┘     └──────────────┘     └──────────────┘
                           │
                     ┌─────┴──────┐
                     │  Agents:   │
                     │ Claude Code│
                     │ OpenCode   │
                     │ OH-SDK     │
                     │  + GLM-5   │
                     └────────────┘
```

1. 将 Selfmade 系列和 SWE-gen 系列的 SWE 任务部署到 Harbor 平台
2. 使用多种专家 Agent 分别推理每个任务，收集完整交互轨迹（思考过程、工具调用、代码修改）
3. 筛选成功解决任务的轨迹作为正样本
4. 使用筛选后的专家轨迹对 Qwen3-8B 进行 SFT

### 8.2 Expert Trajectory Inference Results

**Table 1: Agent Resolve Rate on Selfmade Series (%)**

| Agent | Python | JS | TS | Go | C | C++ | Java | Rust | Overall |
|-------|--------|----|----|----|----|-----|------|------|---------|
| Claude Code | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| OpenCode | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| OpenHands-SDK + GLM-5 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

**Table 2: Agent Resolve Rate on SWE-gen Series (%)**

| Agent | Java | Go | C++ | JS | Rust | Overall |
|-------|------|----|-----|----|------|---------|
| Claude Code | TBD | TBD | TBD | TBD | TBD | TBD |
| OpenCode | TBD | TBD | TBD | TBD | TBD | TBD |
| OpenHands-SDK + GLM-5 | TBD | TBD | TBD | TBD | TBD | TBD |

**Table 3: Agent Resolve Rate by Difficulty (%)**

| Agent | Easy | Medium | Hard | Overall |
|-------|------|--------|------|---------|
| Claude Code | TBD | TBD | TBD | TBD |
| OpenCode | TBD | TBD | TBD | TBD |
| OpenHands-SDK + GLM-5 | TBD | TBD | TBD | TBD |

### 8.3 SFT Training Results

**Table 4: Selfmade vs SWE-gen Training Data — Qwen3-8B Resolve Rate (%)**

| Training Data | Python | JS | TS | Go | C | C++ | Java | Rust | Avg |
|--------------|--------|----|----|----|----|-----|------|------|-----|
| Baseline (no SFT) | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| + SWE-gen Series | TBD | TBD | TBD | TBD | — | TBD | TBD | TBD | TBD |
| + Selfmade Series | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| + Combined | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

> SWE-gen 系列不含 Python/TypeScript/C 数据，对应列标记为 "—"。

**Table 5: Different Agent Trajectories — Qwen3-8B Resolve Rate (%)**

| Trajectory Source | Avg Resolve Rate | vs Baseline |
|------------------|-----------------|-------------|
| Baseline (no SFT) | TBD | — |
| Claude Code | TBD | TBD |
| OpenCode | TBD | TBD |
| OpenHands-SDK + GLM-5 | TBD | TBD |
| Mixed (all agents) | TBD | TBD |

**Table 6: SFT Effect by Difficulty — Qwen3-8B Resolve Rate (%)**

| Training Data | Easy | Medium | Hard |
|--------------|------|--------|------|
| Baseline | TBD | TBD | TBD |
| + Selfmade SFT | TBD | TBD | TBD |
| + SWE-gen SFT | TBD | TBD | TBD |
| + Combined SFT | TBD | TBD | TBD |

---

## 9. Project Structure & Quick Start

### Project Structure

```
SWE-gen/
├── src/swegen/                    # 核心 Python 包
│   ├── create/                    #   任务生成管线
│   │   ├── create.py              #     主流程
│   │   ├── orchestrator.py        #     管线编排
│   │   ├── claude_code_runner.py  #     CC 集成 + Reference Task Caching
│   │   ├── task_instruction.py    #     实质性评估 + 指令生成
│   │   ├── pr_fetcher.py          #     GitHub API + Issue 关联
│   │   └── task_reference.py      #     参考任务缓存
│   ├── scoring.py                 #   难度评分
│   └── cli.py                     #   CLI 入口 + 批量处理
├── harbor/                        # Harbor 验证集成
├── tools/                         # 工具脚本
│   ├── collect_prs_wo_image.py    #   PR 收集（两阶段筛选）
│   ├── score_tasks.py             #   批量难度评分
│   ├── revalidate_tasks.py        #   批量重验证
│   └── sort_prs_by_quality.py     #   PR 质量排序
├── scripts/                       # 运维脚本
│   ├── create_java.sh             #   Java 任务生成
│   ├── create_rust.sh             #   Rust 任务生成
│   ├── create_cpp.sh              #   C++ 任务生成
│   └── auto_scheduler.sh          #   自动调度
├── pyproject.toml
├── requirements.txt
└── README.md
```

### Quick Start

```bash
# 1. 安装
pip install -e .

# 2. 收集 PR
python tools/collect_prs_wo_image.py --languages java --output_dir collected_prs

# 3. 生成 SWE 任务
swegen create --input-ids-file collected_prs/java_pr_ids.txt --n-concurrent 8 --output tasks/March/java-cc --timeout 7200

# 4. 难度评分
python tools/score_tasks.py --dir tasks/March/java-cc --update-toml

# 5. 批量重验证（可选）
python tools/revalidate_tasks.py --lang java --n-concurrent 4 --tasks-root tasks
```

---

## 10. Conclusions

1. **多语言 SWE 数据管线**：覆盖 8 种编程语言的端到端自动化数据生产管线，从 PR 收集到难度评分全流程自动化
2. **大规模数据产出**：Selfmade 10,975 + SWE-gen 4,828 = **15,803 个可用 SWE 任务**
3. **4 项核心工程贡献**：Two-Stage PR Collection (135,753 PRs)、Batch Processing (37x 吞吐)、Relaxed Substantiality Filter (+75% 候选)、Difficulty Scoring (15,803 tasks)
4. **均衡的难度分布**：Selfmade 系列 58% medium / 41% hard，比 SWE-gen 系列更适合作为评估基准
5. **下游应用**：通过 Harbor + 多种专家 Agent 获取专家轨迹，用于 Qwen3-8B SFT，提升小模型多语言 SWE 能力
