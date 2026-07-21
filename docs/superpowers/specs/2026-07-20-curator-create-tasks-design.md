# Curator `create-tasks` Command Design

## Context

The current block is named `curator`; the legacy `subblock/swegen` path and
`/swegen:*` namespace were renamed before the latest `dev`. The task-generation
skill is still exposed publicly as `/curator:run`, although its only domain
operation is creating and verifying tasks from existing PR ID files.

The root block protocol expects every child to expose `/<block>:run`, so removing
that command outright would break `/root:run`. The public documentation site at
<https://swe-swegen-docs.pages.dev/> still serves legacy SWEgen content, while
the maintained site source now lives under `subblock/curator/docs`.

## Goals

- Make `/curator:create-tasks` the canonical user-facing task-generation command.
- Keep root orchestration working without adding a Curator special case.
- Replace user-facing `/curator:run` references across plugin, block, root, test,
  and web documentation.
- Publish the maintained Curator documentation to the existing
  `swe-swegen-docs` Cloudflare Pages project.

## Non-goals

- Do not restore `subblock/swegen` or the `/swegen:*` plugin namespace.
- Do not change task-generation scripts, PR collection behavior, or artifacts.
- Do not rename `:run` for other blocks or change the generic root block protocol.

## Command Surface

The full current run workflow moves to:

```text
/curator:create-tasks
```

Its skill directory, frontmatter name, title, trigger phrases, examples, and
cross-references use `create-tasks`.

`/curator:run` remains as a minimal compatibility adapter for `/root:run`. It
must forward the original request and arguments to `/curator:create-tasks`
without duplicating preflight, confirmation, mode selection, or launch logic.
User-facing command tables and tutorials advertise only `create-tasks`; the
compatibility command is documented only where the root protocol is explained.

## Documentation and Website

Update all directly relevant references in:

- the Curator plugin manifest, marketplace metadata, README, and skills;
- Curator `CLAUDE.md`, config comments, dashboard guidance, memory, tests, and
  Fumadocs pages;
- root documentation and examples that currently present `/curator:run` as a
  user command.

The web source remains `subblock/curator/docs`; no files are restored under
`subblock/swegen/docs`. The deployment script and docs README target:

```text
project: swe-swegen-docs
branch:  swegen
URL:     https://swe-swegen-docs.pages.dev/
```

After the follow-up PR merges, deploy the static export to that project and
verify the live site contains `/curator:create-tasks` and no longer recommends
`/curator:run`.

## Validation

- Search tracked text for stale user-facing `/curator:run` references; allow
  only the compatibility skill and explicit root-protocol explanation.
- Validate plugin JSON, YAML, and shell syntax.
- Run the root static contract suite and Curator schema test.
- Build both the root and Curator documentation sites.
- Require the follow-up PR CI to pass before merging.
- Fetch the deployed Pages site and verify the canonical command is visible.

## Delivery

PR #60 is already merged and cannot accept more commits. This change uses the
same remote `swegen` branch in a new follow-up PR targeting `dev`, then deploys
the merged documentation to Cloudflare Pages.
