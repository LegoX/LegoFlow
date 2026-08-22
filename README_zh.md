<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/public/figures/legoflow-wordmark-dark.svg">
    <img alt="LegoFlow" src="docs/public/figures/legoflow-wordmark-light.svg" width="380">
  </picture>
</p>

<p align="center"><b>简单、可交互的代码数据工程</b></p>

<p align="center">
  <a href="https://legoflow-docs.pages.dev/docs"><img src="docs/public/figures/icon-docs.svg" height="15" alt=""> Docs</a>
  &nbsp;·&nbsp;
  <a href="https://huggingface.co/Lego-X"><img src="docs/public/figures/icon-huggingface.svg" height="15" alt=""> HuggingFace</a>
  &nbsp;·&nbsp;
  <a href="https://legox.net/blog/legoflow/"><img src="docs/public/figures/icon-blog.svg" height="15" alt=""> Blog</a>
  &nbsp;·&nbsp;
  <a href="https://legox.net/"><img src="docs/public/figures/icon-legox.svg" height="15" alt=""> LegoX</a>
  &nbsp;·&nbsp;
  <a href="LICENSE"><img src="docs/public/figures/icon-license.svg" height="15" alt=""> License</a>
  &nbsp;·&nbsp;
  <a href="./README.md"><img src="docs/public/figures/icon-lang.svg" height="15" alt=""> EN</a>
</p>

---



## 关于

LegoFlow 是一个简单、可交互的代码数据工程框架，隶属于 [LegoX](https://legox.net/) 品牌。主要特点包括：

- **智能体原生工作流**：LegoFlow 把繁琐易错的代码数据生产流程（仓库与 PR 采集、任务验证、轨迹 rollout、训练与评测闭环）封装成面向编程智能体的 plugin skills，你可以通过与智能体对话来驱动真实的数据生产。
- **广泛覆盖**：LegoFlow 覆盖 8+ 种编程语言、20+ 类任务标签，并支持在 Claude Code、OpenCode、OpenHands、Terminus 等多种编程 scaffold 上做轨迹 rollout。
- **高度灵活**：LegoFlow 以 **block** 为基础单元，让你的编程智能体统一管理某个阶段的仓库、脚本、配置与运行产物。
- **实时看板**：LegoFlow 通过实时看板追踪数据生产过程，看板围绕任务难度、轨迹质量与模型表现的评分标准设计。
- **自我演进**：一个智能体独立跑完了整条链路，诊断出第一次微调为何停滞，并把 `Qwen3.5-35B-A3B-Base` 在 **SWE-bench Verified 上从 7.6% 提升到 64.4%**。详见[这次端到端运行](https://legox.net/blog/legoflow/)。



## 最新动态

🔥 **2026-08-12**：我们发布了 LegoFlow v0.1，这是面向软件工程数据的全智能体化流水线的首个版本。

## 系统架构

![LegoFlow block tree](docs/public/figures/my-version-coding-repos-expandable.png)

LegoFlow 采用 **block** 的树形结构。根 block 编排四个子 block：


| Block | 职责 |
| ----- | ---- |
| [`curator`](https://legoflow-docs.pages.dev/docs/blocks/curator/getting-started) | 从 GitHub PR、issue 以及在线论坛中构建高质量 SWE 与编程任务 |
| [`tracer`](https://legoflow-docs.pages.dev/docs/blocks/tracer/getting-started) | 在多种编程 scaffold 上采集带可验证奖励的轨迹 |
| [`trainer`](https://legoflow-docs.pages.dev/docs/blocks/trainer/getting-started) | 把 rollout 轨迹转成可训练格式，并启动端到端训练 |
| [`evaluator`](https://legoflow-docs.pages.dev/docs/blocks/evaluator/getting-started) | 在编程 benchmark 上评测 checkpoint，并支持按 rubric 与标签做细粒度分析 |


> [!NOTE]
> **block** 有严格的定义：它在工作流中承担单一职责，并遵循与其他 block 统一的目录与文件结构。一个 block 维护自己相关的仓库（`repos/`）、配置（`config.yaml`）与运行脚本（`scripts/`），在 `artifacts/` 中管理产物，并与相邻的 block 通信。更多细节见 [What is a Block](https://legoflow-docs.pages.dev/docs/development/block-design)。

## 已发布数据集

LegoFlow 产出的数据集，我们会持续发布：

- <a href="https://huggingface.co/datasets/Lego-X/LegoFlow-SWE"><img src="docs/public/figures/icon-huggingface.svg" height="14" alt=""> <b>LegoFlow-SWE</b></a> —— 512 条 SFT 数据，以 **GLM-5.2** 为 teacher 模型，在 OpenHands SDK（`openhands-sdk-1.33`）scaffold 上 rollout 得到。基于它微调把 `Qwen3.5-35B-A3B-Base` 在 SWE-bench Verified 上从 **7.6% 提升到 64.4%**。


## 快速开始

### 前置条件

- **Claude Code**：推荐用来驱动 LegoFlow 的编程智能体。
- **一个 OpenAI 兼容的 LLM 端点**：Curator、Tracer 和 Evaluator 都需要。
- **GitHub token**：通过 `GITHUB_TOKENS` 提供，用于 Curator 采集 PR。
- **Docker**：每个任务、每次 rollout 都在容器中运行。
- **GPU 节点**：仅在你要训练或自行部署 checkpoint 时需要。已验证配置为单节点 8× H800 80GB，多机训练尚未接通。
- **Docker 与 Cloudflare 凭据**：可选，分别用于认证镜像拉取和发布看板。

每一项的具体用途见 [Getting Started](https://legoflow-docs.pages.dev/docs/getting-started)。每个 block 有各自的环境，按对应指引即可自动完成安装。

### 环境准备
1. 克隆代码仓库。
```bash
git clone --recurse-submodules https://github.com/LegoX/SWE-Lego-Live LegoFlow
cd LegoFlow
```

2. 安装插件。每个 block 各自带一个插件。把它的目录注册为 Claude Code marketplace，再从中安装：

```bash
claude plugin marketplace add ./.claude/plugins
claude plugin install root@root-block
```

   `curator`、`tracer`、`trainer`、`evaluator` 同理，它们的插件目录位于 `./blocks/<name>/.claude/plugins`。五组完整命令见 [Getting Started](https://legoflow-docs.pages.dev/docs/getting-started)。装完执行 `/reload-plugins` 以加载这些 skills。

### 使用示例

你既可以为某个特定目的单独触发一个 block，也可以把多个 block 串成更复杂的流水线。

#### 运行单个 Block

每个 block 职责清晰，并自带驱动它的 skills。一般步骤是：

- `/block:setup` 准备必要依赖并填写 `config.yaml`
- `/block:check` 校验配置并执行预检
- `/block:run` 执行任务，并把中间产物归档到 `artifacts/`
- `/block:dashboard` 帮助用户监控运行进度与状态

各个 block 的详细指引见 [docs](https://legoflow-docs.pages.dev/docs/running-blocks/block-by-block)。

#### 运行多个 Block

你也可以通过运行多个 block 来定制工作流，分步指引见 [Running Cascaded Blocks](https://legoflow-docs.pages.dev/docs/running-blocks/cascaded)。

在我们自己跑的一次四 block 全链路运行中，Curator 构建了 4,166 个已验证的 Python 任务，Tracer 成功解决其中 915 个，筛选出 512 条轨迹用于训练，最终微调把 `Qwen3.5-35B-A3B-Base` 在 SWE-bench Verified 上从 **7.6% 提升到 64.4%**。更多细节记录在[博客](https://legox.net/blog/legoflow/)中。

## 参与贡献

我们欢迎所有开发者使用、改进 LegoFlow 并为之贡献。issue 和 pull request 请提交到 [GitHub](https://github.com/LegoX/SWE-Lego-Live)。更多细节见 [Development guide](https://legoflow-docs.pages.dev/docs/development)。

## 引用

```bibtex
@misc{legoflow2026,
  title  = {LegoFlow: Easy and Interactive Code Data Engineering},
  author = {The LegoX Team},
  year   = {2026},
  url    = {https://legox.net/blog/legoflow/}
}
```


## 致谢

LegoFlow 构建在以下项目之上：[Harbor](https://www.harborframework.com/) 提供隔离的任务执行环境，
[LLaMA-Factory](https://github.com/LegoX/LLaMA-Factory) 与 [DeepSpeed](https://github.com/deepspeedai/DeepSpeed) 负责训练，
[LiteLLM](https://github.com/BerriAI/litellm) 负责轨迹采集，
[vLLM](https://github.com/vllm-project/vllm) 用于本地 checkpoint 推理服务，
[Claude Code](https://claude.com/claude-code) 与 Claude Agent SDK 负责智能体操作，
[Fumadocs](https://fumadocs.dev/) 支撑文档站点。

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
