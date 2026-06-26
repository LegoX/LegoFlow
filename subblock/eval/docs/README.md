# eval-docs

User-facing documentation for the [eval](../README.md) block, built as a
[fumadocs](https://fumadocs.dev/) (Next.js) site and deployed to Cloudflare
Pages as a static export.

Mirrors the [trajgen docs](../../trajgen/docs) shell; the prose lives in
`content/docs/`, everything else is the minimal app shell needed to render and
deploy it.

## Requirements

Node **>= 20** (Next 16 + fumadocs 16). This host's system Node is 18, so a
newer Node is installed via [nvm](https://github.com/nvm-sh/nvm). Activate it
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

Builds and deploys `out/` to the `swe-eval-docs` Cloudflare Pages project
(published at <https://swe-eval-docs.pages.dev>). It activates Node 22 via nvm,
asserts Node >= 20, and reuses the trajgen Cloudflare credentials
(`CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` from `.env.cf` or
`~/.config/trajgen_progress_cloudflare.env`). Override the project with
`PROJECT_NAME=...`.

## Structure

```text
docs/
├── content/docs/          # the documentation (MDX + meta.json page order)
│   ├── meta.json          #   top-level page order
│   ├── index.mdx          #   motivation / landing
│   ├── getting-started.mdx
│   ├── core-concepts.mdx
│   ├── run-jobs/          #   select benchmark, LiteLLM proxy, results & artifacts
│   ├── job-analysis/      #   post-eval analysis, gold dataset prep
│   ├── local-model.mdx    #   benchmark a local checkpoint with vLLM
│   ├── dashboard.mdx
│   └── reference/         #   io, agents, status, config-variants
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
3. Link to other pages by their route, e.g. `/docs/run-jobs/select-benchmark`.

Build outputs (`node_modules/`, `.next/`, `.source/`, `out/`) are gitignored.
