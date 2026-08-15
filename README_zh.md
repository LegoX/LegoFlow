# LegoFlow

[English](./README.md)

[Documentation](https://legoflow-docs.pages.dev/docs)
[License](LICENSE)

**LegoFlow** 是一条面向编程智能体数据的 agentic 流水线。它从真实的 GitHub 仓库中构建可验证的 SWE 任务，让智能体在这些任务上做 rollout，把轨迹转换成训练数据，再完成微调与评测。每个阶段遵循同一份契约，因此人和智能体可以用同样的方式操作它。

一个智能体独立跑完了整条链路，诊断出第一次微调为什么停滞，并把基座模型在 **SWE-bench Verified 上从 7.6% 提升到 64.4%**。

[文档](https://legoflow-docs.pages.dev/docs) ·
[设计动机](https://legoflow-docs.pages.dev/docs/motivation) ·
[快速开始](https://legoflow-docs.pages.dev/docs/getting-started) ·
[端到端案例](https://legoflow-docs.pages.dev/docs/running-blocks/cascaded)

## 最新动态

- **2026-08-12** — 文档围绕设计原则与读者意图重写，站点已上线：[legoflow-docs.pages.dev](https://legoflow-docs.pages.dev/docs)。



## 核心特性

- **全流程 agentic 数据工作流。** 覆盖 8+ 种编程语言、20+ 类标签的高质量 SWE 任务，并支持在 Claude Code、OpenCode、OpenHands、Terminus 等多种编程 scaffold 上做 rollout。
- **每一次数据交接都是显式声明的。** 消费方声明上游的哪个输出接到自己的哪个输入，生产方再镜像声明一次。`scripts/validate_config.py` 会在预检时双向交叉校验，让断掉的交接在数小时的任务启动之前就暴露出来。
- **每一次运行都会归档。** 退出钩子会把当时的配置和脚本快照下来，无论运行是成功、失败还是被信号中断。`artifacts/index.yaml` 就是时间线，最新一条即当前状态。
- **每个 block 都有实时看板。** 看板直接读取该 block 的 `artifacts/`，不依赖任何数据库，因此永远和磁盘上的文件一样新。



## 系统设计

LegoFlow block tree

整条流水线是一棵 **block** 树。根 block 编排四个子 block：


| Block                                                                       | 职责                                                 | 底层依赖                               |
| --------------------------------------------------------------------------- | -------------------------------------------------- | ---------------------------------- |
| `[blocks/curator](https://legoflow-docs.pages.dev/docs/blocks/curator)`     | 从 GitHub PR、issue 以及在线论坛中构建高质量 SWE 与编程任务           | GitHub API、Docker、Claude Agent SDK |
| `[blocks/tracer](https://legoflow-docs.pages.dev/docs/blocks/tracer)`       | 采集带可验证奖励的高质量轨迹，支持多种编程 scaffold                     | Harbor、逐任务的 LiteLLM 代理             |
| `[blocks/trainer](https://legoflow-docs.pages.dev/docs/blocks/trainer)`     | 把 rollout 轨迹转成可训练格式，并启动端到端训练                       | LLaMA-Factory、DeepSpeed ZeRO-3     |
| `[blocks/evaluator](https://legoflow-docs.pages.dev/docs/blocks/evaluator)` | 在编程 benchmark 上评测 checkpoint，并支持按 rubric 与标签做细粒度分析 | Harbor、vLLM                        |


一个 block 只负责一个阶段，并被封装成人和智能体都能驱动的形态：`config.yaml` 声明它需要什么、产出什么，`scripts/` 负责执行，`artifacts/` 保存一次运行留下的全部内容，`dashboard/` 只读取这些产物而不修改它们。配置是每次运行一次性的，因此改一个变量再跑一遍，只是一次小而可评审的改动。这套形态本身与这四个 block 无关，根 block 也遵守和它下面所有节点相同的契约。

完整契约见 [What is a Block](https://legoflow-docs.pages.dev/docs/block-design)。

## 快速开始



### 前置条件

- Claude Code，推荐用它来操作 LegoFlow。
- 一个 OpenAI 兼容的 LLM 端点，供 Curator、Tracer 和 Evaluator 使用。
- GitHub token，通过 `GITHUB_TOKENS` 提供，Curator 采集 PR 时需要。
- 运行主机上的 Docker。
- GPU 节点，仅在训练或评测自托管 checkpoint 时需要。已验证的配置是单节点 8× H800 80GB，多机训练尚未接通。
- 可选的 Docker 与 Cloudflare 凭据，分别用于认证镜像拉取和发布看板。

凭据一律来自环境变量，不要写进任何被 git 跟踪的文件，也不要放进 `config.yaml`。

### 1. 克隆仓库

```bash
git clone --recurse-submodules https://github.com/LegoX/SWE-Lego-Live LegoFlow
cd LegoFlow
```

如果已经克隆但漏了 `--recurse-submodules`，补跑 `git submodule update --init --recursive`。

### 2. 安装插件

在仓库根目录下，把每个本地插件目录注册为 Claude Code marketplace：

```bash
claude plugin marketplace add ./.claude/plugins
claude plugin marketplace add ./blocks/curator/.claude/plugins
claude plugin marketplace add ./blocks/tracer/.claude/plugins
claude plugin marketplace add ./blocks/trainer/.claude/plugins
claude plugin marketplace add ./blocks/evaluator/.claude/plugins
```

再从每个 marketplace 各装一个插件：

```bash
claude plugin install root@root-block
claude plugin install curator@curator-block
claude plugin install tracer@tracer
claude plugin install trainer@trainer-block
claude plugin install evaluator@evaluator-block
```

在已打开的会话中执行一次 `/reload-plugins`，或者重启会话。marketplace 路径请保持上面的相对写法。

### 3. 准备工作区

```text
/root:setup
```

它会检查共用工具链、校验根 `config.yaml`，并可以逐个走进子 block 的 setup 流程。它不会启动任何任务。

### 4. 逐个 block 运行

这是推荐路径。每个 block 遵循同一套生命周期：`setup` 准备依赖，`check` 做无副作用的校验，`run` 执行并归档，`dashboard` 查看结果。

```text
/curator:setup   → /curator:check   → /curator:collect-prs → /curator:create-tasks → /curator:dashboard
/tracer:setup    → /tracer:check    → /tracer:run          → /tracer:dashboard
/trainer:setup   → /trainer:check   → /trainer:run         → /trainer:dashboard
/evaluator:setup → /evaluator:check → /evaluator:run       → /evaluator:dashboard
```

`check` 在运行前失败是符合预期的设计：它的作用就是在昂贵的采集、rollout、训练或评测任务开始之前把问题拦下来。详见 [Running Block by Block](https://legoflow-docs.pages.dev/docs/running-blocks/block-by-block)。

### 5. 从根 block 跑整条链路

在每个 block 都至少独立跑通一次之后：

```text
/root:setup
/root:check
/root:run start the data pipeline
```

你描述的是目标，而不是步骤。整条链路会在审批点暂停，并逐阶段归档。两点需要注意：根 block 不负责采集 PR，请先跑完 `/curator:collect-prs`；`check → 确认 → run` 是强制流程，任何智能体都不应在你明确同意之前启动数小时的 GPU 任务。详见 [Running Cascaded Blocks](https://legoflow-docs.pages.dev/docs/running-blocks/cascaded)。

## 端到端结果

一次由智能体驱动的全链路运行，仅使用 Python 任务：


| 阶段        | 产出                                                        |
| --------- | --------------------------------------------------------- |
| Curator   | 4,166 个已验证的 Python 任务。                                    |
| Tracer    | 915 条成功解决的 rollout，连同其可验证奖励一并保留。                          |
| Selection | 512 条轨迹，按推理深度而非覆盖度筛选。                                     |
| Trainer   | 对 `Qwen3.5-35B-A3B-Base` 做一次全参数微调，loss 从 0.524 收敛到 0.229。 |
| Evaluator | 在 500 道 SWE-bench Verified 上达到 64.4%，未训练的基座模型为 7.6%。      |




真正值得看的不是最后那个数字。第一次尝试在同一批 rollout 上按推理覆盖度筛选，只达到 56.1%。智能体读完结果，把轨迹筛选规则改成推理深度，只重跑了受这次改动影响的阶段：64.4%。teacher 模型、任务集合、训练配方都没有变。

具体的任务、轨迹和分数不会逐位复现。LegoFlow 保证的可复现性是流程与结构层面的：显式声明的依赖让每一次交接都清清楚楚，运行归档保留了产生某个结果的配置与代码，统一的生命周期让你可以只重跑一个阶段而不惊动其余部分。完整的实验 brief 和各阶段预期见 [Running Cascaded Blocks](https://legoflow-docs.pages.dev/docs/running-blocks/cascaded)。

## 文档导航


| 我想……                   | 去这里                                                                                          |
| ---------------------- | -------------------------------------------------------------------------------------------- |
| 了解这个项目为什么存在            | [Motivation](https://legoflow-docs.pages.dev/docs/motivation)                                |
| 先装上并跑起来                | [Getting Started](https://legoflow-docs.pages.dev/docs/getting-started)                      |
| 一次只跑一个阶段               | [Running Block by Block](https://legoflow-docs.pages.dev/docs/running-blocks/block-by-block) |
| 从根 block 跑完整条链路        | [Running Cascaded Blocks](https://legoflow-docs.pages.dev/docs/running-blocks/cascaded)      |
| 从 GitHub 构建可验证的 SWE 任务 | [Curator](https://legoflow-docs.pages.dev/docs/blocks/curator)                               |
| 在已有任务上采集智能体轨迹          | [Tracer](https://legoflow-docs.pages.dev/docs/blocks/tracer)                                 |
| 用轨迹微调模型                | [Trainer](https://legoflow-docs.pages.dev/docs/blocks/trainer)                               |
| 评测一个模型或 checkpoint     | [Evaluator](https://legoflow-docs.pages.dev/docs/blocks/evaluator)                           |
| 自己加一个 block            | [What is a Block](https://legoflow-docs.pages.dev/docs/block-design)                         |
| 出问题了                   | [Q&A](https://legoflow-docs.pages.dev/docs/qa)                                               |




## 路线图

已知的缺口，如实列出：

- `/root:dashboard`（跨 block 的统一看板）目前仍是占位实现，请使用四个 block 各自的看板。
- 多机训练尚未接通，随仓库提供的 ZeRO-3 配置假设单节点 8 卡。
- 根目录的 `scripts/start.sh` 只自动化数据阶段，即 Curator 和 Tracer；完整四 block 链路由 `/root:run` 以智能体驱动的方式完成。
- 已在 Harbor registry 上验证过 11 个 benchmark，其余条目尚未用本项目的智能体验证。
- 配置参考分散在四个 block 各自的 Configuration Guide 中，目前还没有统一的参考页和 changelog。



## 参与贡献

欢迎在 [GitHub](https://github.com/LegoX/SWE-Lego-Live) 提 issue 和 PR。

新阶段用 `/root:create` 生成脚手架，它会产出符合 block 契约的完整目录树。一个 block 算完成的标准是：在全新克隆上 `check` 能通过，一次运行会自我归档，并且下游 block 无需手工告知路径就能消费它的输出。见 [Adding Your Own Block](https://legoflow-docs.pages.dev/docs/block-design)。

## 引用

```bibtex
@misc{legoflow2026,
  title  = {LegoFlow: An Agentic Pipeline for Coding-Agent Data},
  author = {The LegoFlow Team},
  year   = {2026},
  url    = {https://github.com/LegoX/SWE-Lego-Live}
}
```



## 致谢

LegoFlow 构建在这些项目之上：[Harbor](https://www.harborframework.com/) 提供隔离的任务执行环境，
[LLaMA-Factory](https://github.com/LegoX/LLaMA-Factory) 与 [DeepSpeed](https://github.com/deepspeedai/DeepSpeed) 负责训练，
[LiteLLM](https://github.com/BerriAI/litellm) 负责轨迹采集，
[vLLM](https://github.com/vllm-project/vllm) 用于本地 checkpoint 的推理服务，
[Claude Code](https://claude.com/claude-code) 与 Claude Agent SDK 负责智能体操作，
[Fumadocs](https://fumadocs.dev/) 支撑文档站点。

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。