# SWE-gen Docs Site

Static documentation site for the SWE-gen block. The generated Cloudflare Pages
project is `swe-swegen-docs`, published at:

```text
https://swe-swegen-docs.pages.dev
```

The live progress databoard is a separate Pages project:

```text
https://swe-databoard.pages.dev/
```

## Build locally

From the repository root:

```bash
python3 subblock/swegen/docs/build_docs.py
```

Generated files are written to:

```text
subblock/swegen/docs/site/
```

Preview locally:

```bash
python3 -m http.server 8788 --directory subblock/swegen/docs/site
```

Then open:

```text
http://127.0.0.1:8788/
```

## Deploy to Cloudflare Pages

Set credentials in the shell or in a local env file that is not committed:

```bash
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_API_TOKEN="..."
```

Deploy:

```bash
bash subblock/swegen/docs/deploy_cloudflare_pages.sh
```

Optional overrides:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROJECT_NAME` | `swe-swegen-docs` | Cloudflare Pages project name |
| `BRANCH_NAME` | `swegen` | Pages deployment branch |
| `PUBLIC_DIR` | `subblock/swegen/docs/site` | Directory deployed by wrangler |
| `WRANGLER_PKG` | `wrangler@3` | Wrangler package used through `npx --yes` |

The deploy script creates or reuses the Pages project, builds the docs, and
deploys `site/` with `wrangler pages deploy`.
