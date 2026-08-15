

**Easy and Interactive Code Data Engineering**

[Docs](https://legoflow-docs.pages.dev/docs)  ·  [HuggingFace](https://huggingface.co/SWE-Lego)  ·  [Blog](https://legox.pages.dev/blog/legoflow/)  ·  [LegoX](https://legox.pages.dev/)  ·  [License](LICENSE)  ·  [ZH](./README_zh.md)

---



## About

LegoFlow is an easy and interactive framework for code data engineering, part of the [LegoX](https://legox.pages.dev/) family. The highlights include:

- **Fully Vibe-coding**: LegoFlow turns the complicated, error-prone code data collection procedures (repo and PR collection, task verification, trajectory rollout, and the training-evaluation loop) into well-prepared plugin skills, where users can simply chat and interact with the coding agent (e.g., Claude Code) for real production.
- **Wide Coverage**: LegoFlow covers over 8+ programming languages and 20+ task tags, and trajectory rollouts across multiple coding scaffolds including Claude Code, OpenCode, OpenHands and Terminus.
- **High Flexibility**: LegoFlow is designed to ground on **block**, the building unit that allows your coding agent to manage repositories, scripts, configurations and the runtime output of a particular stage.
- **Live Dashboards**: LegoFlow monitors the data production process through a series of live dashboards. These dashboards are built around carefully designed rubrics for tracking task difficulty, trajectory quality, and model performance.
- **Self-evolving**: An agent has run the whole loop on its own, diagnosed why its first fine-tune plateaued, and lifted `Qwen3.5-35B-A3B-Base` from **7.6% to 64.4% on SWE-bench Verified**. See [the end-to-end run](https://legox.pages.dev/blog/legoflow/).



## News

🔥 **2026-08-12**: We release LegoFlow v0.1, the initial version of a fully agentic pipeline for software-engineering data.

## Architecture

![LegoFlow block tree](docs/public/figures/my-version-coding-repos-expandable.png)

The pipeline is a tree of **blocks**. The root orchestrates four children:


| Block                                                                | What it does                                                                               | Built on                             |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------ |
| [`curator`](https://legoflow-docs.pages.dev/docs/blocks/curator/getting-started)     | Curates high-quality SWE and coding tasks from GitHub PRs, issues, and online forums       | GitHub API, Docker, Claude Agent SDK |
| [`tracer`](https://legoflow-docs.pages.dev/docs/blocks/tracer/getting-started)       | Collects high-quality trajectories with verified rewards, across multiple coding scaffolds | Harbor, per-job LiteLLM proxy        |
| [`trainer`](https://legoflow-docs.pages.dev/docs/blocks/trainer/getting-started)     | Converts rollout traces into training-ready formats and launches end-to-end training       | LLaMA-Factory, DeepSpeed ZeRO-3      |
| [`evaluator`](https://legoflow-docs.pages.dev/docs/blocks/evaluator/getting-started) | Measures checkpoints on coding benchmarks, with rubric and tag level analysis              | Harbor, vLLM                         |


Because the config is one-shot per run, changing one variable and rerunning is a small, reviewable edit rather than an archaeology exercise across scripts.

#### What is a Block?

> [!NOTE]
> Each block plays a particular role, following the same structure. A block maintains its relevant repositories (`repos/`), configuration (`config.yaml`) and run scripts (`scripts/`), manages its output in `artifacts/`, and communicates with its adjacent blocks. More details can be found at [What is a Block](https://legoflow-docs.pages.dev/docs/block-design).



## Quick Start

### Prerequisites

- **Claude Code** — the recommended coding agent to drive LegoFlow.
- **An OpenAI-compatible LLM endpoint** — required by Curator, Tracer and Evaluator.
- **GitHub token(s)** — supplied through `GITHUB_TOKENS`, for Curator's PR collection.
- **Docker** — every task and every rollout runs in a container.
- **A GPU node** — only if you train, or serve a checkpoint yourself. Validated on one node with 8× H800 80GB; multi-node is not wired up.
- **Docker and Cloudflare credentials** — optional, for authenticated image pulls and for publishing dashboards.

What each one is for is spelled out in [Getting Started](https://legoflow-docs.pages.dev/docs/getting-started). Each block has its own dependent enviroment, which can be setup automatically following the corresponding guides. 

### Enviroment Setup

**1. Clone the tree.**

```bash
git clone --recurse-submodules https://github.com/LegoX/SWE-Lego-Live LegoFlow
cd LegoFlow
```

**2. Install the plugins.** Every block ships one. Register its directory as a Claude Code marketplace, then install from it:

```bash
claude plugin marketplace add ./.claude/plugins
claude plugin install root@root-block
```

The same rules hold for `curator`, `tracer`, `trainer` and `evaluator`, whose plugin directories live at `./blocks/<name>/.claude/plugins`. All five pairs are listed in [Getting Started](https://legoflow-docs.pages.dev/docs/getting-started). Run `/reload-plugins` to ensure the plugins are correctly loaded.

**3. Hand over to a skill.** `/root:setup` to prepare the workspace. From there every block answers the same four skills — `setup`, `check`, `run`, `dashboard` — so there is no script to read before your first run.

### Example Usages

Users can triger individual blocks for a particular purpose, or jointly run mulitple blocks for a more complicated pipeline.

#### Running Individual Blocks

Every block has clear roles and functions. Users can easily trigger these blocks with their pre-defined skills. In general, the steps to trigger a block include:

- `/block:setup` prepares necessary dependencies and fills in `config.yaml`
- `/block:check` validates the configuration and run pre-flight check
- `/block:run` does the work and archives the intermediate output to `./artifacts`
- `/block:dashboard` helps the user to monitor the running progress and status

#### Running the Full Pipeline

Once each block has run on its own, the root drives all four in dependency order. You describe the target, not the steps:

```text
/root:setup
/root:check
/root:run start the data pipeline
```

The chain pauses at every approval gate and archives each stage as it goes. In one such run an agent built 4,166 verified Python tasks, selected 512 trajectories out of 915 solved rollouts, and fine-tuned `Qwen3.5-35B-A3B-Base` from **7.6% to 64.4% on SWE-bench Verified** — reaching that second number only after reading its own first result and changing the selection rule.

Full write-up on the [blog](https://legox.pages.dev/blog/legoflow/); step-by-step guidance in [Running Cascaded Blocks](https://docs-dev.legoflow-docs.pages.dev/docs/running-blocks/cascaded).

## Open-source Data Collection

Datasets produced by LegoFlow and released for reuse. Every row names the teacher model that generated the trajectories, the scaffold they were rolled out in, and the result of training on them.


| ID                  | Teacher Model | Scaffold      | Data Samples | Training Result                                           | HF Link                                                                                                 |
| ------------------- | ------------- | ------------- | ------------ | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `swe-sft-512-glm52` | GLM-5.2       | OpenHands SDK | 512          | `Qwen3.5-35B-A3B-Base` 7.6% → 64.4% on SWE-bench Verified | [samples_for_llama_factory_sft](https://huggingface.co/datasets/SWE-Lego/samples_for_llama_factory_sft) |




## Contributing

We welcome all developers to use, improve and contribute to LegoFlow. Issues and pull requests go on [GitHub](https://github.com/LegoX/SWE-Lego-Live). For more details, please refer to the [Development guide](https://docs-dev.legoflow-docs.pages.dev/docs/development).

## Citation

```bibtex
@misc{legoflow2026,
  title  = {LegoFlow: An Agentic Pipeline for Coding-Agent Data},
  author = {The LegoX Team},
  year   = {2026},
  url    = {https://github.com/LegoX/SWE-Lego-Live}
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