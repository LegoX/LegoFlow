<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/public/figures/legoflow-wordmark-dark.svg">
    <img alt="LegoFlow" src="docs/public/figures/legoflow-wordmark-light.svg" width="380">
  </picture>
</p>

<p align="center"><b>Easy and Interactive Code Data Engineering</b></p>

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
  <a href="./README_zh.md"><img src="docs/public/figures/icon-lang.svg" height="15" alt=""> ZH</a>
</p>

---



## About

LegoFlow is an easy and interactive framework for code data engineering, part of the [LegoX](https://legox.net/) family. The highlights include:

- **Agent-native Workflows**: LegoFlow turns the complicated, error-prone steps of code data collection (repo and PR collection, task verification, trajectory rollout, and the training-evaluation loop) into plugin skills, so you can drive real data production by talking to a coding agent.
- **Wide Coverage**: LegoFlow covers 8+ programming languages and 20+ task tags, and rolls out trajectories across multiple coding scaffolds, including Claude Code, OpenCode, OpenHands and Terminus.
- **High Flexibility**: LegoFlow is built on the **block**, the unit that lets your coding agent manage the repositories, scripts, configuration and runtime output of a single stage.
- **Live Dashboards**: LegoFlow tracks data production through live dashboards, with rubrics for task difficulty, trajectory quality, and model performance.
- **Self-evolving**: An agent has run the whole loop on its own, diagnosed why its first fine-tune plateaued, and lifted `Qwen3.5-35B-A3B-Base` from **7.6% to 64.4% on SWE-bench Verified**. See [the end-to-end run](https://legox.net/blog/legoflow/).



## News

🔥 **2026-08-12**: We released LegoFlow v0.1, the first version of a fully agentic pipeline for software-engineering data.

## Architecture

![LegoFlow block tree](docs/public/figures/my-version-coding-repos-expandable.png)

LegoFlow follows the tree structure of **blocks**. The root orchestrates four children:


| Block                                                                                | Role                                                                                 |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| [`curator`](https://legoflow-docs.pages.dev/docs/blocks/curator/getting-started)     | Curates high-quality SWE and coding tasks from GitHub PRs, issues, and online forums |
| [`tracer`](https://legoflow-docs.pages.dev/docs/blocks/tracer/getting-started)       | Collects trajectories with verified rewards across multiple coding scaffolds         |
| [`trainer`](https://legoflow-docs.pages.dev/docs/blocks/trainer/getting-started)     | Converts rollout traces into training-ready formats and launches end-to-end training |
| [`evaluator`](https://legoflow-docs.pages.dev/docs/blocks/evaluator/getting-started) | Measures checkpoints on coding benchmarks, with rubric- and tag-level analysis       |


> [!NOTE]
> A **block** has a strict definition: it plays one role in the workflow and follows the same layout and file structure as every other block. It maintains the relevant repositories (`repos/`), configuration (`config.yaml`) and run scripts (`scripts/`), manages its output in `artifacts/`, and communicates with its adjacent blocks. See [What is a Block](https://legoflow-docs.pages.dev/docs/development/block-design) for more details.

## Released Datasets

We release datasets as LegoFlow produces them:

- <a href="https://huggingface.co/datasets/Lego-X/LegoFlow-SWE"><img src="docs/public/figures/icon-huggingface.svg" height="14" alt=""> <b>LegoFlow-SWE</b></a> — 512 SFT samples, distilled from **GLM-5.2** as the teacher model and rolled out on the OpenHands SDK (`openhands-sdk-1.33`) scaffold. Fine-tuning on it lifted `Qwen3.5-35B-A3B-Base` from **7.6% to 64.4%** on SWE-bench Verified.


## Quick Start

### Prerequisites

- **Claude Code**: the recommended coding agent to drive LegoFlow.
- **An OpenAI-compatible LLM endpoint**: required by Curator, Tracer and Evaluator.
- **GitHub token(s)**: supplied through `GITHUB_TOKENS`, for Curator's PR collection.
- **Docker**: every task and every rollout runs in a container.
- **A GPU node**: only if you train or serve a checkpoint yourself. Validated on one node with 8× H800 80GB; multi-node is not wired up.
- **Docker and Cloudflare credentials**: optional, for authenticated image pulls and for publishing dashboards.

[Getting Started](https://legoflow-docs.pages.dev/docs/getting-started) explains what each one is for. Every block has its own environment, and the corresponding guide sets it up automatically.

### Environment Setup

1. Clone the code repository.

```bash
git clone --recurse-submodules https://github.com/LegoX/SWE-Lego-Live LegoFlow
cd LegoFlow
```

2. Install the plugins. Every block ships one. Register its directory as a Claude Code marketplace, then install from it:

```bash
claude plugin marketplace add ./.claude/plugins
claude plugin install root@root-block
```

   The same holds for `curator`, `tracer`, `trainer` and `evaluator`, whose plugin directories live at `./blocks/<name>/.claude/plugins`. All five pairs are listed in [Getting Started](https://legoflow-docs.pages.dev/docs/getting-started). Run `/reload-plugins` afterwards so the skills load.

### Example Usages

You can trigger individual blocks for a particular purpose, or chain several blocks into a larger pipeline.

#### Running Individual Blocks

Every block has a clear role, and ships the skills that drive it. The steps usually go:

- `/block:setup` prepares necessary dependencies and fills in `config.yaml`
- `/block:check` validates the configuration and run pre-flight check
- `/block:run` does the work and archives the intermediate output to `artifacts/`
- `/block:dashboard` helps the user to monitor the running progress and status

The [docs](https://legoflow-docs.pages.dev/docs/running-blocks/block-by-block) carry a detailed guide for each block.

#### Running Multiple Blocks

You can also customize the workflow by running multiple blocks. [Running Cascaded Blocks](https://legoflow-docs.pages.dev/docs/running-blocks/cascaded) walks through it step by step.

In our own run with all four blocks, Curator built 4,166 verified Python tasks, Tracer solved 915 of them, 512 trajectories went into training, and the resulting fine-tune lifted `Qwen3.5-35B-A3B-Base` from **7.6% to 64.4%** on SWE-bench Verified. The [blog](https://legox.net/blog/legoflow/) records the details.

## Contributing

We welcome all developers to use, improve and contribute to LegoFlow. Issues and pull requests go on [GitHub](https://github.com/LegoX/SWE-Lego-Live). See the [Development guide](https://legoflow-docs.pages.dev/docs/development) for more details.

## Citation

```bibtex
@misc{legoflow2026,
  title  = {LegoFlow: Easy and Interactive Code Data Engineering},
  author = {The LegoX Team},
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
