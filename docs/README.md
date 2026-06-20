# lego-factory-docs

User-facing documentation for the [LegoFactory](../README.md) root project,
built as a [fumadocs](https://fumadocs.dev/) (Next.js) site and deployed to
Cloudflare Pages as a static export.

Live site: **<https://lego-factory-docs.pages.dev>**

The prose lives in `content/docs/`; everything else is the minimal app shell
needed to render and deploy it. This site documents the *root* block (pipeline,
block system, getting started). Each subblock has its own docs site, e.g.
tracer at <https://swe-tracer-docs.pages.dev>.

## Requirements

Node **>= 20** (Next 16 + fumadocs 16). On hosts whose system Node is older,
install a newer Node via [nvm](https://github.com/nvm-sh/nvm) and activate it
before any `npm` command here:

```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm use 22
```

## Develop

```bash
npm install        # first time (also generates the fumadocs .source/)
npm run dev        # dev server at http://localhost:3000 (redirects / -> /docs)
```

## Build & preview the static export

```bash
npm run build      # static export to out/ (next.config.mjs sets output: 'export')
npx serve out      # preview the exported site
```

## Deploy to Cloudflare Pages

```bash
bash deploy_cloudflare_pages.sh
```

Builds and deploys `out/` to the `lego-factory-docs` Cloudflare Pages project
(published at <https://lego-factory-docs.pages.dev>, separate from any
subblock's docs project). It activates Node 22 via nvm, asserts Node >= 20,
and reuses Cloudflare credentials (`CLOUDFLARE_API_TOKEN` +
`CLOUDFLARE_ACCOUNT_ID` from `.env.cf` or
`~/.config/trajgen_progress_cloudflare.env`). Override the project name with
`PROJECT_NAME=...` or `ROOT_DOCS_PROJECT_NAME=...` in the env file.

## Structure

```text
docs/
├── content/docs/          # the documentation (MDX + meta.json page order)
│   ├── meta.json          #   top-level page order
│   ├── index.mdx          #   motivation / landing
│   ├── getting-started.mdx
│   ├── block-design.mdx
│   ├── sub-block/         #   per-block portals (each links to that block's full docs)
│   │   ├── curator.mdx
│   │   ├── tracer.mdx
│   │   ├── trainer.mdx
│   │   └── rl.mdx
│   └── reference/         #   config-schema, artifacts
├── src/                   # app shell (docs route, layouts, source loader)
├── public/_redirects      # Cloudflare Pages root redirect (/ -> /docs)
├── next.config.mjs        # createMDX() + output: 'export'
├── source.config.ts       # fumadocs content source (frontmatter/meta schema)
└── deploy_cloudflare_pages.sh
```

## Add or edit a page

1. Add an `.mdx` file under `content/docs/` (or a subfolder) with `title` and
   `description` frontmatter.
2. Add its slug to the folder's `meta.json` `pages` array to place it in the
   sidebar order.
3. Link to other pages by their route, e.g. `/docs/pipeline/tracer`.

Build outputs (`node_modules/`, `.next/`, `.source/`, `out/`) are gitignored.
