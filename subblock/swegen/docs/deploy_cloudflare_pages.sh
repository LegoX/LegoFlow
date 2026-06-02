#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLIC_DIR="${PUBLIC_DIR:-$SCRIPT_DIR/site}"
PROJECT_NAME="${PROJECT_NAME:-swe-swegen-docs}"
BRANCH_NAME="${BRANCH_NAME:-swegen}"
WRANGLER_PKG="${WRANGLER_PKG:-wrangler@3}"

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  echo "ERROR: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are required" >&2
  exit 1
fi

python3 "$SCRIPT_DIR/build_docs.py"

npx --yes "$WRANGLER_PKG" pages project create "$PROJECT_NAME" \
  --production-branch "$BRANCH_NAME" >/tmp/swe_swegen_docs_pages_project_create.log 2>&1 || true

npx --yes "$WRANGLER_PKG" pages deploy "$PUBLIC_DIR" \
  --project-name "$PROJECT_NAME" \
  --branch "$BRANCH_NAME" \
  --commit-dirty=true \
  --commit-message "Update SWE-gen docs $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
