<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/public/figures/legoflow-wordmark-dark.svg">
    <img alt="LegoFlow" src="docs/public/figures/legoflow-wordmark-light.svg" width="380">
  </picture>
</p>

<p align="center"><b>Easy and Interactive Code Data Engineering</b></p>

<p align="center">
  <a href="https://legoflow-docs.legox.net/docs"><img src="docs/public/figures/icon-docs.svg" height="15" alt=""> Docs</a>
  &nbsp;·&nbsp;
  <a href="https://huggingface.co/Lego-X"><img src="docs/public/figures/icon-huggingface.svg" height="15" alt=""> HuggingFace</a>
  &nbsp;·&nbsp;
  <a href="https://legox.pages.dev/blog/legoflow/"><img src="docs/public/figures/icon-blog.svg" height="15" alt=""> Blog</a>
  &nbsp;·&nbsp;
  <a href="https://legox.pages.dev/"><img src="docs/public/figures/icon-legox.svg" height="15" alt=""> LegoX</a>
  &nbsp;·&nbsp;
  <a href="LICENSE"><img src="docs/public/figures/icon-license.svg" height="15" alt=""> License</a>
  &nbsp;·&nbsp;
  <a href="./README_zh.md"><img src="docs/public/figures/icon-lang.svg" height="15" alt=""> ZH</a>
</p>

---



## About

LegoFlow is an easy and interactive framework for code data engineering, part of the [LegoX](https://legox.pages.dev/) family. The highlights include:

- **Agent-native, end-to-end workflows**: LegoFlow packages repository and PR collection, task verification, trajectory rollout, training, and evaluation as plugin skills that users operate through a coding agent.
- **Reproducible block contracts**: Each stage owns its configuration, pinned repositories, scripts, artifacts, dashboard, and handoffs, making runs easier to audit, resume, and extend.
- **Broad coverage**: LegoFlow supports **8+ programming languages**, **20+ task tags**, and four coding-agent scaffolds: Claude Code, OpenCode, OpenHands, and Terminus.
- **Rubric-guided visibility**: Live dashboards apply consistent rubrics to task difficulty, trajectory quality, training progress, and model performance.
- **A proven improvement loop**: In one autonomous run, Curator produced **4,166 verified tasks**, Tracer solved **915**, and **512 trajectories** were selected for training. After diagnosing a plateau and revising data selection, the agent lifted `Qwen3.5-35B-A3B-Base` from **7.6% to 64.4% on SWE-bench Verified**. See [the end-to-end run](https://legox.pages.dev/blog/legoflow/).



## News

🔥 **2026-09-10**: We released LegoFlow v0.1, the initial version of a fully agentic pipeline for software-engineering data.

## Architecture

![LegoFlow block tree](docs/public/figures/my-version-coding-repos-expandable.png)

LegoFlow follows the tree structure of **blocks**. The root orchestrates four children:


| Block | Role |
| ----- | ---- |
| [`curator`](https://legoflow-docs.legox.net/docs/blocks/curator/getting-started) | Curates high-quality SWE and coding tasks from GitHub PRs, issues, and online forums |
| [`tracer`](https://legoflow-docs.legox.net/docs/blocks/tracer/getting-started) | Collects high-quality trajectories with verified rewards, across multiple coding scaffolds |
| [`trainer`](https://legoflow-docs.legox.net/docs/blocks/trainer/getting-started) | Converts rollout traces into training-ready formats and launches end-to-end training |
| [`evaluator`](https://legoflow-docs.legox.net/docs/blocks/evaluator/getting-started) | Measures checkpoints on coding benchmarks, with rubric and tag level analysis |


> [!NOTE]
> We have a clear definition of **block**. It plays a particular role in the workflow, following the same layout and file structure. A block maintains the relevant repositories (`repos/`), configuration (`config.yaml`) and run scripts (`scripts/`), manages its output in `artifacts/`, and communicates with its adjacent blocks. See [What is a Block](https://legoflow-docs.legox.net/docs/development/block-design) for more details.

## Released Datasets

We are actively releasing the latest datasets produced by LegoFlow:


> ### `swe-sft-512-glm52`
>
> **512 samples** · Teacher: **GLM-5.2** · Scaffold: **OpenHands SDK v1.14**
>
> Fine-tuning `Qwen3.5-35B-A3B-Base` with this dataset improved its SWE-bench Verified score from **7.6% to 64.4%**.
>
> <a href="https://huggingface.co/datasets/Lego-X/samples_for_llama_factory_sft"><img src="docs/public/figures/icon-huggingface.svg" height="16" align="absmiddle" alt=""> <b>Download on Hugging Face</b></a>


## Quick Start

### Prerequisites

- **Claude Code or Codex CLI** — coding agents that operate LegoFlow through its plugin skills. Claude Code is recommended.
- **An OpenAI-compatible LLM endpoint** — required by Curator, Tracer and Evaluator.
- **GitHub token(s)** — supplied through `GITHUB_TOKENS`, for Curator's PR collection.
- **Docker** — every task and every rollout runs in a container.
- **A GPU node** — only if you train, or serve a checkpoint yourself. Validated on one node with 8× H800 80GB; multi-node is not wired up.
- **Docker and Cloudflare credentials** — optional, for authenticated image pulls and for publishing dashboards.

What each one is for is spelled out in [Getting Started](https://legoflow-docs.legox.net/docs/getting-started). Each block has its own dependent environment, which can be set up automatically following the corresponding guides.

### Environment Setup

#### 1. Clone the code repository.

```bash
git clone --recurse-submodules https://github.com/LegoX/LegoFlow LegoFlow
cd LegoFlow
```

#### 2. Install the plugins for coding agents.

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

Run `/reload-plugins` afterwards so the skills load. See [Getting Started](https://legoflow-docs.legox.net/docs/getting-started) for the complete usage guide.

##### Codex

LegoFlow also provides Codex plugins under `plugins/`, which wrap the Claude Code skills. Register the repository root as a local Codex marketplace and install the plugins:

```bash
codex plugin marketplace add .
codex plugin add root@legoflow
codex plugin add curator@legoflow
codex plugin add tracer@legoflow
codex plugin add trainer@legoflow
codex plugin add evaluator@legoflow
```

Restart Codex after installation. Use native Codex skills such as `$<block>-<skill>`, for example `$curator-check`.

### Example Usages

Users can trigger individual blocks for a particular purpose, or jointly run multiple blocks for a more complicated pipeline.

#### Running Individual Blocks

Every block has clear roles and functions. Users can easily trigger these blocks with their pre-defined skills. In general, the steps to trigger a block include:

| Workflow | Claude Code | Codex |
| --- | --- | --- |
| Setup | `/<block>:setup` | `$<block>-setup` |
| Check | `/<block>:check` | `$<block>-check` |
| Run | `/<block>:run` | `$<block>-run` |
| Dashboard | `/<block>:dashboard` | `$<block>-dashboard` |

The setup skill prepares dependencies and fills in `config.yaml`; check validates the configuration and performs a preflight; run does the work and archives intermediate output to `artifacts/`; and dashboard helps monitor progress and status.

The detailed guides to each individual block can be found at [docs](https://legoflow-docs.legox.net/docs/running-blocks/block-by-block).

#### Running Multiple Blocks

Users can also customize the workflow by running multiple blocks. Step-by-step guidance is in [Running Cascaded Blocks](https://legoflow-docs.legox.net/docs/running-blocks/cascaded).

For example, our previous run with all four blocks shows that Curator built 4,166 verified Python tasks, Tracer solved 915 of them, 512 trajectories were selected for training, and the resulting fine-tune lifted `Qwen3.5-35B-A3B-Base` from **7.6% to 64.4%** on SWE-bench Verified. More details are recorded in the [blog](https://legox.pages.dev/blog/legoflow/).

## Contributing

We welcome all developers to use, improve and contribute to LegoFlow. Issues and pull requests go on [GitHub](https://github.com/LegoX/LegoFlow). For more details, please refer to the [Development guide](https://legoflow-docs.legox.net/docs/development).

## Citation

```bibtex
@misc{legoflow2026,
  title  = {LegoFlow: Easy and Interactive Code Data Engineering},
  author = {LegoX Team},
  year   = {2026},
  url    = {https://legox.net/blog/legoflow/}
}
```


## Acknowledgements

LegoFlow builds on [Harbor](https://www.harborframework.com/) for isolated task execution,
[LLaMA-Factory](https://github.com/LegoX/LLaMA-Factory) and [DeepSpeed](https://github.com/deepspeedai/DeepSpeed) for training,
[LiteLLM](https://github.com/BerriAI/litellm) for trajectory capture,
[vLLM](https://github.com/vllm-project/vllm) for serving local checkpoints,
[Claude Code](https://claude.com/claude-code) and the Claude Agent SDK for agent operation,
and [Fumadocs](https://fumadocs.dev/) for the documentation site.

## License

Apache License 2.0. See [LICENSE](LICENSE).
