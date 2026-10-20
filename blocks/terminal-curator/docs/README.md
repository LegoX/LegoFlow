# Terminal Curator documentation

The operator documentation lives in `content/docs/` and is mounted by the root
LegoFlow documentation site at `/docs/blocks/terminal-curator`. There is no
second web app or dependency lockfile to maintain for this block.

From the repository root:

```bash
cd docs
npm ci
npm run build
```

The root site's content loader, sidebar, static routes, and search index all
include these pages. Use `/docs/blocks/terminal-curator/...` links when linking
from root documentation. Within the block pages use relative Markdown links
so they also work when browsing this folder on GitHub.

Start with [getting started](content/docs/getting-started.mdx), then read
[configuration](content/docs/configuration.mdx), [workflow](content/docs/workflow.mdx),
[outputs](content/docs/outputs.mdx), and [dashboard](content/docs/dashboard.mdx).
