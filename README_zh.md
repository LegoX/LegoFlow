<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/public/figures/legoflow-wordmark-dark.svg">
    <img alt="LegoFlow" src="docs/public/figures/legoflow-wordmark-light.svg" width="380">
  </picture>
</p>

<p align="center"><b>简单、可交互的代码数据工程</b></p>

<p align="center">
  <a href="https://legoflow-docs.legox.net/docs"><img src="docs/public/figures/icon-docs.svg" height="15" alt=""> Docs</a>
  &nbsp;·&nbsp;
  <a href="https://huggingface.co/Lego-X"><img src="docs/public/figures/icon-huggingface.svg" height="15" alt=""> HuggingFace</a>
  &nbsp;·&nbsp;
  <a href="https://www.legox.net/blog/legoflow/"><img src="docs/public/figures/icon-blog.svg" height="15" alt=""> Blog</a>
  &nbsp;·&nbsp;
  <a href="https://www.legox.net/"><img src="docs/public/figures/icon-legox.svg" height="15" alt=""> LegoX</a>
  &nbsp;·&nbsp;
  <a href="LICENSE"><img src="docs/public/figures/icon-license.svg" height="15" alt=""> License</a>
  &nbsp;·&nbsp;
  <a href="./README.md"><img src="docs/public/figures/icon-lang.svg" height="15" alt=""> EN</a>
</p>

---



## 关于

编程是现代大语言模型的核心能力，但生产高质量的编程数据仍然十分复杂，需要经过仓库发现、任务验证、轨迹生成、训练和评测等一系列环节。LegoFlow 是一个简单、可交互的代码数据工程框架，主要亮点包括：

- **智能体原生工作流**：LegoFlow 将从仓库与 PR 采集到任务验证、轨迹生成、训练和评测的每个环节，都封装为标准插件 skill，Claude Code、Codex 等编程智能体可以直接调用。
- **开源任务与轨迹**：我们发布 [**LegoFlow-SWE**](https://huggingface.co/datasets/Lego-X/LegoFlow-SWE)，其中包含从 1200 万个 PR 中精心筛选的 **5,000 个任务**，覆盖 8 种编程语言和 20 类任务标签；同时发布 **2,780 条成功轨迹**。仅使用 1K 条样本训练 `Qwen3.5-35B-A3B-Base`，即可在 SWE-bench Pro 上达到 **48.8%**。
- **迈向递归自我改进**：只需少量人工干预，LegoFlow 就能运行完整流水线、评测结果并调整策略。经过两轮迭代，`Qwen3.5-35B-A3B-Base` 在 SWE-bench Verified 上的成绩从 **7.6% 提升到 64.4%**。



## 最新动态

🔥 **2026-09-10**：我们发布 LegoFlow v0.1，首个面向软件工程数据的全智能体化流水线版本。

## 系统架构

![LegoFlow block tree](docs/public/figures/my-version-coding-repos-expandable.png)

LegoFlow 采用 **block** 的树形结构。根 block 编排四个子 block：


| Block | 职责 |
| ----- | ---- |
| [`curator`](https://legoflow-docs.legox.net/docs/blocks/curator/getting-started) | 从 GitHub PR、issue 以及在线论坛中构建高质量 SWE 与编程任务 |
| [`tracer`](https://legoflow-docs.legox.net/docs/blocks/tracer/getting-started) | 采集带可验证奖励的高质量轨迹，支持多种编程 scaffold |
| [`trainer`](https://legoflow-docs.legox.net/docs/blocks/trainer/getting-started) | 把 rollout 轨迹转成可训练格式，并启动端到端训练 |
| [`evaluator`](https://legoflow-docs.legox.net/docs/blocks/evaluator/getting-started) | 在编程 benchmark 上评测 checkpoint，并支持按 rubric 与标签做细粒度分析 |


> [!NOTE]
> 我们对 **block** 有明确的定义：它在工作流中承担特定职责，并遵循统一的目录与文件结构。一个 block 维护自己相关的仓库（`repos/`）、配置（`config.yaml`）与运行脚本（`scripts/`），在 `artifacts/` 中管理产物，并与相邻的 block 通信。更多细节见 [What is a Block](https://legoflow-docs.legox.net/docs/development/block-design)。

## 已发布数据集

随着 LegoFlow 发布新的任务和轨迹，我们会持续更新这里的数据集：

- <a href="https://huggingface.co/datasets/Lego-X/LegoFlow-SWE"><img src="docs/public/figures/icon-huggingface.svg" height="16" align="absmiddle" alt=""> <b>LegoFlow-SWE</b></a> —— 从超过 **1200 万个候选 PR** 中构建，包含 **5,000 个已验证任务**和 **9,767 条 rollout**，其中有 **2,780 条成功轨迹**。使用 1K 条训练样本，`Qwen3.5-35B-A3B` 在 SWE-bench Verified、Pro 和 Multilingual 上分别达到 **70.2%**、**48.8%** 和 **57.0%**。


## 快速开始

### 前置条件

- **Claude Code 或 Codex CLI** —— 通过插件 skills 操作 LegoFlow 的编程智能体，推荐使用 Claude Code。
- **一个 OpenAI 兼容的 LLM 端点** —— Curator、Tracer 和 Evaluator 都需要。
- **GitHub token** —— 通过 `GITHUB_TOKENS` 提供，用于 Curator 采集 PR。
- **Docker** —— 每个任务、每次 rollout 都在容器中运行。
- **GPU 节点** —— 仅在你要训练或自行部署 checkpoint 时需要。已验证配置为单节点 8× H800 80GB，多机训练尚未接通。
- **Docker 与 Cloudflare 凭据** —— 可选，分别用于认证镜像拉取和发布看板。

每一项的具体用途见 [Getting Started](https://legoflow-docs.legox.net/docs/getting-started)。每个 block 有各自依赖的环境，可以按对应指引自动完成安装。

### 环境准备

#### 1. 克隆代码仓库

```bash
git clone --recurse-submodules https://github.com/LegoX/LegoFlow LegoFlow
cd LegoFlow
```

#### 2. 为编程智能体安装插件

##### Claude Code

```bash
claude plugin marketplace add ./.claude/plugins
claude plugin marketplace add ./blocks/curator/.claude/plugins
claude plugin marketplace add ./blocks/tracer/.claude/plugins
claude plugin marketplace add ./blocks/trainer/.claude/plugins
claude plugin marketplace add ./blocks/evaluator/.claude/plugins

claude plugin install root@root-block
claude plugin install curator@curator-block
claude plugin install tracer@tracer
claude plugin install trainer@trainer-block
claude plugin install evaluator@evaluator-block
```

安装完成后执行 `/reload-plugins` 以加载这些 skills。完整使用说明见 [Getting Started](https://legoflow-docs.legox.net/docs/getting-started)。

##### Codex

LegoFlow 还在 `plugins/` 下提供了封装 Claude Code skills 的 Codex 插件。将仓库根目录注册为本地 Codex marketplace，然后安装插件：

```bash
codex plugin marketplace add .
codex plugin add root@legoflow
codex plugin add curator@legoflow
codex plugin add tracer@legoflow
codex plugin add trainer@legoflow
codex plugin add evaluator@legoflow
```

安装完成后重启 Codex。Codex 使用 `$<block>-<skill>` 形式的原生 skills，例如 `$curator-check`。

### 使用示例

用户既可以为某个特定目的单独触发一个 block，也可以联合运行多个 block 来完成更复杂的流水线。

#### 运行单个 Block

每个 block 职责与功能都很清晰，用户可以用预定义的 skills 轻松触发。触发一个 block 的一般步骤包括：

| 工作流 | Claude Code | Codex |
| --- | --- | --- |
| 准备 | `/<block>:setup` | `$<block>-setup` |
| 检查 | `/<block>:check` | `$<block>-check` |
| 运行 | `/<block>:run` | `$<block>-run` |
| 看板 | `/<block>:dashboard` | `$<block>-dashboard` |

setup skill 用于准备依赖并填写 `config.yaml`；check 用于校验配置并执行预检；run 用于执行任务并把中间产物归档到 `artifacts/`；dashboard 用于监控运行进度与状态。

各个 block 的详细指引见 [docs](https://legoflow-docs.legox.net/docs/running-blocks/block-by-block)。

#### 运行多个 Block

用户也可以通过运行多个 block 来定制工作流。分步指引见 [Running the Full Pipeline](https://legoflow-docs.legox.net/docs/running-blocks/full-pipeline)。

例如，我们此前一次四个 block 全链路的运行显示：Curator 构建了 4,166 个已验证的 Python 任务，Tracer 成功解决其中 915 个，筛选出 512 条轨迹用于训练，最终微调把 `Qwen3.5-35B-A3B-Base` 在 SWE-bench Verified 上从 **7.6% 提升到 64.4%**。更多细节记录在[博客](https://www.legox.net/blog/legoflow/)中。

## 参与贡献

我们欢迎所有开发者使用、改进 LegoFlow 并为之贡献。issue 和 pull request 请提交到 [GitHub](https://github.com/LegoX/LegoFlow)。更多细节请参阅 [Development guide](https://legoflow-docs.legox.net/docs/development)。

## 引用

```bibtex
@misc{legoflow2026,
  title  = {LegoFlow: Easy and Interactive Code Data Engineering},
  author = {LegoX Team},
  year   = {2026},
  url    = {https://github.com/LegoX/LegoFlow}
}
```


## 致谢

LegoFlow 构建在以下项目之上：[Harbor](https://www.harborframework.com/) 提供隔离的任务执行环境，
[LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) 与 [DeepSpeed](https://github.com/deepspeedai/DeepSpeed) 负责训练，
[LiteLLM](https://github.com/BerriAI/litellm) 负责轨迹采集，
[vLLM](https://github.com/vllm-project/vllm) 用于本地 checkpoint 推理服务，
[Claude Code](https://claude.com/claude-code) 与 Claude Agent SDK 负责智能体操作，
[Fumadocs](https://fumadocs.dev/) 支撑文档站点。

## Roadmap

- [ ] **TerminalBench**：将 [Terminal-Lego](https://www.legox.net/blog/terminal-lego/) 的工作流迁移并升级到 LegoFlow，以适配 [Terminal-Bench 3.0](https://www.tbench.ai/news/terminal-bench-3-0) 和 [Terminal-Bench 4.0](https://www.tbench.ai/news/terminal-bench-4-0)。
- [ ] **ProgramBench 与 NL2Repo**：挖掘更加复杂的长程软件工程任务，这些任务从一个可执行程序或自然语言需求开始。
- [ ] **递归自我改进**：利用 block 的执行反馈，支持更加通用、可长期运行的自我改进系统。

这些扩展都会采用标准的 [Harbor task 格式](https://www.harborframework.com/docs/tasks)和 LegoFlow block 结构，使产物更容易在不同组织和开发者社区之间共享。

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
