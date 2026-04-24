#!/usr/bin/env bash
set -euo pipefail

# Minimal block health check: verify the expected docs and index files exist.
test -f "CLAUDE.md"
test -f "dashboard/overview.mdx"
test -f "inputs.yaml"
test -f "outputs.yaml"
test -f "meta-info.yaml"
test -f "status.yaml"

echo "block dryrun ok"
