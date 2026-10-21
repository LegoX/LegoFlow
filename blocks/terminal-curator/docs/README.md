# Terminal Curator documentation

The operator guide in `content/docs/` is mounted by the root LegoFlow site at
`/docs/blocks/terminal-curator`. It follows the shared block chapters:

- [Overview](content/docs/index.mdx)
- [Getting Started](content/docs/getting-started.mdx)
- Usage: [Configuration](content/docs/usage/configuration.mdx),
  [Validation Checks](content/docs/usage/validation-checks.mdx),
  [Downstream Handoff](content/docs/usage/downstream-handoff.mdx)
- [Design](content/docs/design.mdx)
- [Output Format](content/docs/outputs.mdx)
- [Dashboard](content/docs/dashboard.mdx)

Build from the repository root with `cd docs && npm ci && npm run build`.
Use relative Markdown links inside block pages and mounted
`/docs/blocks/terminal-curator/...` links from root pages.

The dashboard demo is synthetic. `dashboard/build_demo.py` renders its
checked-in fixture; root build/dev commands stage the canonical snapshot from
`public/dashboard_demo/terminal-curator/`. Keep actual run data separate.
