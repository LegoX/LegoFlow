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
  <a href="https://huggingface.co/SWE-Lego"><img src="docs/public/figures/icon-huggingface.svg" height="15" alt=""> HuggingFace</a>
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

- **Fully Vibe-coding**: LegoFlow turns the complicated, error-prone code data collection procedures (repo and PR collection, task verification, trajectory rollout, and the training-evaluation loop) into well-prepared plugin skills, where users can simply chat and interact with the coding agent (e.g., Claude Code) for real production.
- **Wide Coverage**: LegoFlow covers over 8+ programming languages and 20+ task tags, and trajectory rollouts across multiple coding scaffolds including Claude Code, OpenCode, OpenHands and Terminus.
- **High Flexibility**: LegoFlow is designed to ground on **block**, the building unit that allows your coding agent to manage repositories, scripts, configurations and the runtime output of a particular stage.
- **Live Dashboards**: LegoFlow monitors the data production process through a series of live dashboards. These dashboards are built around carefully designed rubrics for tracking task difficulty, trajectory quality, and model performance.
- **Self-evolving**: An agent has run the whole loop on its own, diagnosed why its first fine-tune plateaued, and lifted `Qwen3.5-35B-A3B-Base` from **7.6% to 64.4% on SWE-bench Verified**. See [the end-to-end run](https://legoflow-docs.pages.dev/docs/running-blocks/cascaded).



## News

|                     |                                                                                              |
| ------------------- | -------------------------------------------------------------------------------------------- |
| 🔥 **2026-08-12** | We release LegoFlow v0.1, the initial version of a fully agentic pipeline for software-engineering data. |

## Architecture

![LegoFlow block tree](docs/public/figures/my-version-coding-repos-expandable.png)

The pipeline is a tree of **blocks**. The root orchestrates four children:


| Block                                                                       | What it does                                                                               | Built on                             |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------ |
| [`blocks/curator`](https://legoflow-docs.pages.dev/docs/blocks/curator)     | Curates high-quality SWE and coding tasks from GitHub PRs, issues, and online forums       | GitHub API, Docker, Claude Agent SDK |
| [`blocks/tracer`](https://legoflow-docs.pages.dev/docs/blocks/tracer)       | Collects high-quality trajectories with verified rewards, across multiple coding scaffolds | Harbor, per-job LiteLLM proxy        |
| [`blocks/trainer`](https://legoflow-docs.pages.dev/docs/blocks/trainer)     | Converts rollout traces into training-ready formats and launches end-to-end training       | LLaMA-Factory, DeepSpeed ZeRO-3      |
| [`blocks/evaluator`](https://legoflow-docs.pages.dev/docs/blocks/evaluator) | Measures checkpoints on coding benchmarks, with rubric and tag level analysis              | Harbor, vLLM                         |


Because the config is one-shot per run, changing one variable and rerunning is a small, reviewable edit rather than an archaeology exercise across scripts.

#### What is a Block?

> [!NOTE]
> Each block plays a particular role, following the same structure. A block maintains its relevant repositories (`repos/`), configuration (`config.yaml`) and run scripts (`scripts/`), manages its output in `artifacts/`, and communicates with its adjacent blocks. More details can be found at [What is a Block](https://legoflow-docs.pages.dev/docs/block-design).

## Quick Start

#### Prerequisites

- **Claude Code** — the recommended way to drive LegoFlow.
- **An OpenAI-compatible LLM endpoint** — used by Curator, Tracer and Evaluator.
- **GitHub token(s)** — supplied through `GITHUB_TOKENS`, for Curator's PR collection.
- **Docker** — every task and every rollout runs in a container.
- **A GPU node** — only if you train, or serve a checkpoint yourself. Validated on one node with 8× H800 80GB; multi-node is not wired up.
- **Docker and Cloudflare credentials** — optional, for authenticated image pulls and for publishing dashboards.

Credentials always come from the environment, never from a tracked file. What each one is for is spelled out in [Getting Started](https://legoflow-docs.pages.dev/docs/getting-started).

#### Three steps to a first run

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

Do the same for `curator`, `tracer`, `trainer` and `evaluator`, whose plugin directories live at `./blocks/<name>/.claude/plugins`. Marketplace names follow `<name>@<name>-block`, with one exception: tracer's is `tracer@tracer`. All five pairs are listed in [Getting Started](https://legoflow-docs.pages.dev/docs/getting-started).

**3. Hand over to a skill.** Run `/reload-plugins`, then `/root:setup` to prepare the workspace. From there every block answers the same four skills — `setup`, `check`, `run`, `dashboard` — so there is no script to read before your first run.

## Example Usages

Two ways LegoFlow gets used: one block at a time, and the whole chain in one go.

### Running Individual Blocks

Every block runs on its own, and every block is worth running on its own first. They all answer the same four skills, so learning one is most of the work of learning the next:

`setup` prepares dependencies · `check` validates with no side effects · `run` does the work and archives it · `dashboard` shows what came out.


| Block         | What you can do with it                                                                                                                                     | Start here                                                                                 |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| **Curator**   | Turn GitHub PRs and issues into verified SWE tasks across 8+ languages, each one a pinned container with a bug, a ground-truth fix, and tests that grade it | [Curator setup →](https://legoflow-docs.pages.dev/docs/blocks/curator/getting-started)     |
| **Tracer**    | Roll a coding agent out over tasks you already have, capture every trajectory with its verified reward, and convert the good ones into SFT data             | [Tracer setup →](https://legoflow-docs.pages.dev/docs/blocks/tracer/getting-started)       |
| **Trainer**   | Fine-tune on those trajectories with LLaMA-Factory and DeepSpeed ZeRO-3, starting from a rollout job or a ready-made Hugging Face dataset                   | [Trainer setup →](https://legoflow-docs.pages.dev/docs/blocks/trainer/getting-started)     |
| **Evaluator** | Benchmark any API endpoint or local checkpoint on SWE-bench Verified, Terminal-Bench and a dozen others, then analyse the failures by tag                   | [Evaluator setup →](https://legoflow-docs.pages.dev/docs/blocks/evaluator/getting-started) |


### Running the Full Pipeline

Once each block has run on its own, the root drives all four in dependency order. You describe the target, not the steps:

```text
/root:setup
/root:check
/root:run start the data pipeline
```

The chain pauses at every approval gate and archives each stage as it goes. Below is a real run: an agent was given one brief — build Python SWE data, train a base model on it, measure the result — and took it from there.


| Stage     | Output                                                                                       |
| --------- | -------------------------------------------------------------------------------------------- |
| Curator   | 4,166 verified Python tasks                                                                  |
| Tracer    | 915 solved rollouts, kept with their verified rewards                                        |
| Selection | 512 trajectories, filtered for reasoning depth rather than coverage                          |
| Trainer   | One full-parameter fine-tune of `Qwen3.5-35B-A3B-Base`, loss 0.524 → 0.229                   |
| Evaluator | **64.4%** on the 500 SWE-bench Verified tasks, against **7.6%** for the untrained base model |


The interesting number is not the last one. A first attempt over the same rollout pool, selected for reasoning coverage, reached 56.1%. The agent read that result, changed the trajectory selection rule to reasoning depth, and reran only the stages that change affected: 64.4%. Same teacher model, same tasks, same training recipe.

Exact tasks, trajectories and scores will not repeat bit for bit. What LegoFlow makes reproducible is procedural: declared dependencies make every handoff explicit, run archives preserve the configuration and code that produced a result, and the uniform lifecycle lets you rerun one stage without disturbing the rest. The full brief and the per-stage expectations are in [Running Cascaded Blocks](https://legoflow-docs.pages.dev/docs/running-blocks/cascaded).

## Open-source Data Collection

Datasets produced by LegoFlow and released for reuse. Every row names the teacher model that generated the trajectories, the scaffold they were rolled out in, and the result of training on them.


| ID                  | Teacher Model | Scaffold      | Data Samples | Training Result                                           | HF Link                                                                                                 |
| ------------------- | ------------- | ------------- | ------------ | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `swe-sft-512-glm52` | GLM-5.2       | OpenHands SDK | 512          | `Qwen3.5-35B-A3B-Base` 7.6% → 64.4% on SWE-bench Verified | <a href="https://huggingface.co/datasets/SWE-Lego/samples_for_llama_factory_sft"><img src="docs/public/figures/icon-huggingface.svg" height="14" alt=""> samples_for_llama_factory_sft</a> |




## Contributing

We welcome all developers to use, improve and contribute to LegoFlow. Issues and pull requests go on [GitHub](https://github.com/LegoX/SWE-Lego-Live).

There are two ways in, and they are easier to keep straight once you know which layer you are on.

**Improve the code a block runs.** Most features live in the repositories a block wraps, not in the block itself. A block never edits the code it runs: every repository is vendored and pinned to a commit. So you change the upstream repository, bump one `commit_id` in `config.yaml`, and rerun the checks.

**Add a stage of your own.** If you already have a workflow worth wrapping — a new data source, a different trainer, another benchmark — `/root:create` scaffolds the whole block: config, scripts, plugin skills and the archive wiring. Declare what it takes in and what it hands on, point `scripts/start.sh` at the entry point you already have, and keep `scripts/dryrun.sh` free of side effects so a check never changes anything.

Your block is done when three things are true: `check` passes on a fresh clone, a run archives itself, and the next block can consume its output without anyone pasting a path by hand.

Every block carries its own tests — fast cases that need no tokens and no Docker, plus optional smoke tests that really run:

```bash
bash blocks/<name>/tests/run.sh               # cases only
bash blocks/<name>/tests/run.sh --with-smoke  # plus a real end-to-end run
```

The full contract is in [What is a Block](https://legoflow-docs.pages.dev/docs/block-design).

## Roadmap

LegoFlow starts from SWE data, but the block contract is not specific to it. Where we are taking it:

- **Wider task coverage.** More languages and more task types than bug-fixing PRs, and more sources than GitHub alone.
- **More agent scaffolds.** Claude Code, OpenCode, OpenHands and Terminus are wired today. The scaffold is a config field, so new ones arrive as adapters rather than rewrites.
- **Recursive self-improvement.** Closing the loop, so a trained checkpoint becomes the next round's rollout model and the pipeline improves the data that trains it.
- **Beyond SWE.** The same tasks → rollouts → training → evaluation shape fits other long-horizon agent domains. The blocks are meant to be swapped, not rebuilt.

Known gaps, stated plainly, so nobody finds them the hard way:

- `/root:dashboard`, a single board across all blocks, is still a stub. Use the four per-block dashboards.
- Multi-node training is not wired up. The shipped ZeRO-3 config assumes one 8-GPU node.
- Root `scripts/start.sh` automates the data stage only, Curator and Tracer. The full four-block chain is agent-driven through `/root:run`.
- 11 benchmarks are validated against Harbor's registry. The remaining entries are unverified with this agent.
- Configuration reference is spread across four per-block guides. There is no consolidated reference page and no changelog yet.

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