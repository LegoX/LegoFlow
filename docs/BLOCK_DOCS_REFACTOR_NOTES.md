# Block Docs Refactor Notes

These notes summarize the Curator docs changes so the same pattern can be
reused for Tracer, Trainer, and Evaluator.

## Overall Page Set

For each block, keep the docs organized around a small, predictable set of
pages:

- `Overview`: what the block does, why it exists, and the high-level workflow
  figure.
- `Getting Started`: the shortest path to run the block once.
- `Design`: how the block works internally, following the Overview figure.
- `Output Format`: what lands under `artifacts/`, in directory order.
- `Test Cases`: what checks exist, smoke runs, and pass conditions.
- `Dashboard`: what the dashboard reads and how users should interpret it.

Avoid spreading the same concept across too many pages. If two pages overlap,
merge the concept into the page where the user naturally needs it.

## Getting Started Pattern

The Getting Started page should be practical and short. It should feel like a
person guiding the user through one successful run.

Use this structure:

1. `Prerequisites`: list only things the block cannot create.
2. `Setup`: explain that the key is filling `config.yaml`; then show the key
   YAML snippets before calling the setup skill.
3. `Check`: show the check skill and link to `Test Cases` for detailed pass
   conditions.
4. Main run step(s): one section per major command.
5. `Dashboard Visualization`: explain when to use the dashboard and what it is
   not.

For Curator, the run path is:

```text
fill config.yaml
/curator:setup
/curator:check
/curator:collect-prs
/curator:create-tasks
/curator:dashboard
```

Do not start Getting Started with long implementation details. Keep advanced
manual commands out unless they are needed for a first run.

For Tracer, Trainer, and Evaluator, mirror the Curator rhythm:

- Start with one sentence about what the page gets the user to produce, plus a
  compact command path.
- Treat plugin skills as the main interface. Mention manual scripts only as
  debugging escape hatches or links to deeper pages.
- In `Setup`, explain `config.yaml` before running `/<block>:setup`. Show the
  smallest useful snippets for the first run:
  - Tracer: LLM endpoint, task source, rollout scale, agent scaffold, optional
    SFT conversion.
  - Trainer: data source, model, training output/hyperparameters, GPU count,
    optional credentials.
  - Evaluator: benchmark source, LLM endpoint or served local checkpoint,
    rollout scale, agent runtime, optional analysis.
- After setup, keep the path simple: `/<block>:check`, then `/<block>:run`, then
  `/<block>:dashboard`.
- Link out to `Design`, `Output Format`, `Test Cases`, and deeper workflow
  pages instead of re-explaining every script inline.

## Config Guidance

When introducing `config.yaml`, show real YAML snippets instead of a broad
"field / meaning" table. Use sub-subsections that map directly to workflow
stages.

For Curator:

- `LLM API Config`: used by PR filtering, instruction generation, and task
  creation. Show both native Anthropic-compatible and OpenAI-proxy modes.
- `PR Collection Config`: read by `/curator:collect-prs`; controls languages,
  repo/PR scale, token use, and filters.
- `Create Task Config`: read by `/curator:create-tasks`; controls enabled
  language workers, timeouts, concurrency, and verified task caps.
- `GitHub Token Config`: explain token file/env usage and keep tracked token
  fields empty unless setup requires otherwise.

Make snippets structurally correct, including the parent path such as
`runtime_info.input`. Keep comments short and aligned.

## Design Pattern

Design should follow the Overview diagram, not the file tree. It explains
how the block works internally after the user knows what it does.

For Curator, the mechanism sequence is:

1. `Discover PRs`: repo discovery, PR quality filters, quota rotation, PR pools.
2. `Prepare Task Materials`: evidence bundle, instruction rewrite, patch split.
3. `Assemble Harbor Tasks`: Harbor task directory construction and isolated
   tests.
4. `Verify And Score`: NOP baseline, Oracle pass, difficulty, tags.
5. `Publish Verified Tasks`: verified manifests and merged task export.
6. `Command Gates`: which slash command gates each part.

Keep the writing explanatory, but still concrete: mention command names, output
paths, and the contract downstream blocks rely on.

## Output Format Pattern

Start with a short statement that all runtime output lands under `artifacts/`.
Then show a directory tree with aligned comments. After the tree, explain the
important outputs in the same order as the tree.

For Curator:

- `artifacts/collected_prs/`: PR pools from collection.
- `artifacts/swe_tasks/<lang>-cc/`: per-language Harbor tasks.
- `verifiable_tasks.txt`: the trusted manifest.
- `.swegen-create-batch/`: resume and batch state.
- `artifacts/merged_swe_tasks/`: optional flat verified task root for Tracer.
- `artifacts/logs/swegen-create/`: create-task logs.
- `artifacts/index.yaml`: run history.

Use a callout for downstream handoff. For Curator, emphasize that Tracer should
consume verified manifests or the intentional merged task root, not arbitrary
task directories.

## Q&A Pattern

Use `Q1: ...` and `A: ...` formatting. Keep answers operational.

The first shared Q&A item should cover missing plugin skills:

- restart Claude Code from the repository root;
- load the root plugin and all block plugins with `--plugin-dir`;
- run `/reload-plugins`;
- then search for the slash command again.

This shared Q&A page should live above `Reference` in the root docs sidebar.

## Tone And Style

- Write like a human guide, not a schema manual.
- Prefer short paragraphs.
- Put exact details in YAML snippets, directory trees, and links to Reference.
- Keep Getting Started concise; move deeper mechanics to Design.
- Use `Output Format`, not `Outputs` or `Results & Artifacts`, for block output
  pages.
- Remove leftover `@codex`, `[xxx]`, and TODO comments before building.

## Validation Checklist

Before finishing a block docs refactor:

```bash
rg -n "@codex|\\[xxx\\]|TODO" docs blocks/*/docs -g '*.mdx' -g '*.md' -g '*.json'
cd docs
/Users/haoli/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/next/dist/bin/next build --webpack
```

The docs should build cleanly, and the block sidebar should show the intended
page order.
