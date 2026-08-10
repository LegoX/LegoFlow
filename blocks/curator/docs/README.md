# Curator Docs Site

Fumadocs/Next.js documentation site for the Curator block. The generated
Cloudflare Pages project is `swe-swegen-docs`, published at:

```text
https://swe-swegen-docs.pages.dev
```

The live progress databoard is a separate Pages project:

```text
https://swe-databoard-ems.pages.dev/
```

## Develop locally

From this directory:

```bash
cd blocks/curator/docs
npm ci
npm run dev
```

Then open the local Next.js URL printed by the command and visit `/docs`.

## Build locally

```bash
cd blocks/curator/docs
npm ci
npm run build
```

Static export files are written to:

```text
blocks/curator/docs/out/
```

## Deploy to Cloudflare Pages

Set credentials in the shell or in a local env file that is not committed:

```bash
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_API_TOKEN="..."
```

Deploy:

```bash
bash blocks/curator/docs/deploy_cloudflare_pages.sh
```

Optional overrides:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROJECT_NAME` | `swe-swegen-docs` | Cloudflare Pages project name |
| `BRANCH_NAME` | `swegen` | Pages deployment branch |
| `OUT_DIR` | `out` | Directory deployed by wrangler |
| `WRANGLER_PKG` | `wrangler@latest` | Wrangler package used through `npx --yes` |
| `ENV_FILE` | `~/.config/swegen_docs_cloudflare.env` | Optional credential/config file |

The deploy script creates or reuses the Pages project, builds the docs, and
deploys `out/` with `wrangler pages deploy`.
